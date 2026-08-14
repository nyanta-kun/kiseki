"""JRA 推奨（hit_tier）前向き記録の集計。

`keiba.hit_tier_races` / `keiba.hit_tier_picks` に貯まった**発走前に撮った**記録を
集計する。DB の `calculated_indices` を後から読む集計とは別物で、
**こちらだけが「ユーザーに提示された内容」の答え合わせになる**
（理由は `src/services/jra_hit_tier_log.py` の docstring）。

出力:
  1. tier 別の的中率（推奨を出したレース）
  2. 棄権（tier=C）側で実際に何が起きたか
  3. **発走前 tier と確定 tier のずれ** — オッズがどれだけ動いたか
  4. 反実仮想: 指数1位馬の成績を tier に関係なく並べたもの

使い方:
    cd backend
    .venv/bin/python scripts/jra_pick_log_report.py --start 20260815 --end 20260930
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

RACE_SQL = """
SELECT date, race_id, course_name, race_number, tier, bet_type, is_recommended,
       skip_reason, market_agree, final_market_agree, final_tier,
       confidence_score, entropy_norm, top1_win_odds,
       top1_finish_position, hit, n_finishers, settled_at, lead_minutes, rule_version
FROM keiba.hit_tier_races
WHERE date >= %(start)s AND date <= %(end)s
ORDER BY date, race_id
"""

PICK_SQL = """
SELECT p.race_id, p.horse_number, p.index_rank, p.pop_rank, p.is_top1,
       p.pre_win_odds, p.finish_position, p.abnormality_code,
       p.final_win_odds, p.place_payout_odds
FROM keiba.hit_tier_picks p
JOIN keiba.hit_tier_races r ON r.id = p.pick_race_id
WHERE r.date >= %(start)s AND r.date <= %(end)s
"""

TIER_ORDER = ["S", "A", "B", "C+", "C"]


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _rows(cur, sql: str, params: dict) -> list[dict]:
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:5.1f}%" if den else "    -"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()

    conn = connect()
    cur = conn.cursor()
    races = _rows(cur, RACE_SQL, {"start": args.start, "end": args.end})
    picks = _rows(cur, PICK_SQL, {"start": args.start, "end": args.end})
    cur.close()
    conn.close()

    if not races:
        print(f"記録なし（{args.start}〜{args.end}）")
        print("cron が動いているか確認: logs/jra_pick_snapshot_trigger.log")
        return

    settled = [r for r in races if r["settled_at"] is not None]
    versions = sorted({r["rule_version"] for r in races})
    leads = [r["lead_minutes"] for r in races if r["lead_minutes"] is not None]

    print(f"=== JRA hit_tier 前向き記録 {args.start}〜{args.end} ===")
    print(f"記録レース {len(races)} / 確定済み {len(settled)} / "
          f"日数 {len({r['date'] for r in races})}")
    print(f"rule_version: {', '.join(versions)}")
    if leads:
        print(f"撮影リード: 中央値 {sorted(leads)[len(leads) // 2]} 分前 "
              f"(min {min(leads)} / max {max(leads)})")
    if len(versions) > 1:
        print("⚠️ rule_version が複数ある。閾値を変えた前後が混ざっているので分けて見ること")

    # --- 1. tier 別 -------------------------------------------------------
    print("\n--- tier 別（確定済みのみ）---")
    print("tier   n   推奨   的中   的中率   券種")
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for r in settled:
        by_tier[r["tier"] or "?"].append(r)
    for tier in TIER_ORDER + sorted(set(by_tier) - set(TIER_ORDER)):
        rs = by_tier.get(tier)
        if not rs:
            continue
        reco = [r for r in rs if r["is_recommended"]]
        hits = [r for r in reco if r["hit"]]
        bet = {r["bet_type"] for r in reco if r["bet_type"]}
        print(f"{tier:<5} {len(rs):>3} {len(reco):>5} {len(hits):>6}  "
              f"{_pct(len(hits), len(reco))}   {'/'.join(sorted(bet)) or '-'}")

    # --- 2. 棄権側 --------------------------------------------------------
    print("\n--- 棄権（推奨を出さなかったレース）で実際に起きたこと ---")
    skipped = [r for r in settled if not r["is_recommended"]]
    if skipped:
        reasons = Counter(r["skip_reason"] or "?" for r in skipped)
        print(f"棄権 {len(skipped)} レース  内訳: "
              + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))
        top1_won = [r for r in skipped if r["top1_finish_position"] == 1]
        top1_placed = [r for r in skipped
                       if r["top1_finish_position"] is not None
                       and r["top1_finish_position"] <= 3]
        print(f"  棄権レースの指数1位馬: 勝率 {_pct(len(top1_won), len(skipped))} / "
              f"複勝率 {_pct(len(top1_placed), len(skipped))}")
        print("  ※ 推奨したレースの的中率と比べて初めて「見送って正解か」が言える")
    else:
        print("なし")

    # --- 3. 発走前 tier と確定 tier のずれ --------------------------------
    print("\n--- 発走前 tier vs 確定オッズ tier（オッズがどれだけ動いたか）---")
    cmp_rows = [r for r in settled if r["final_tier"] is not None]
    if cmp_rows:
        same = [r for r in cmp_rows if r["tier"] == r["final_tier"]]
        agree_same = [r for r in cmp_rows if r["market_agree"] == r["final_market_agree"]]
        print(f"tier 一致 {_pct(len(same), len(cmp_rows))} ({len(same)}/{len(cmp_rows)})")
        print(f"market_agree 一致 {_pct(len(agree_same), len(cmp_rows))}")
        moved = Counter(
            (r["tier"], r["final_tier"]) for r in cmp_rows if r["tier"] != r["final_tier"]
        )
        for (a, b), n in moved.most_common(8):
            print(f"  {a} → {b}: {n}")
        print("  ⚠️ ずれが大きいほど「確定オッズを使った過去分析」は本番の再現にならない")
    else:
        print("確定オッズ tier が未算出（settle がまだ）")

    # --- 4. 反実仮想: 指数1位馬を全レースで買っていたら -------------------
    print("\n--- 反実仮想: tier を問わず指数1位馬を買った場合 ---")
    top1_by_race = {p["race_id"]: p for p in picks if p["is_top1"]}
    fin = [p for p in top1_by_race.values() if p["finish_position"] is not None]
    if fin:
        won = [p for p in fin if p["finish_position"] == 1]
        placed = [p for p in fin if p["finish_position"] <= 3]
        payout = sum(p["final_win_odds"] or 0.0 for p in won)
        print(f"n={len(fin)}  勝率 {_pct(len(won), len(fin))}  "
              f"複勝率 {_pct(len(placed), len(fin))}  "
              f"単勝ROI {payout / len(fin):.3f}")
        print("  ※ ROI は参考値。hit_tier は的中重視の設計で、収支の設計ではない")
    else:
        print("確定済みの指数1位馬がまだ無い")


if __name__ == "__main__":
    main()
