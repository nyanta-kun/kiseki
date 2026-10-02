#!/usr/bin/env python3
"""混戦の逃げ先頭ライン（`L_flat`・紙上の検証）の判定集計（2026-10-02）。

事前登録 `docs/type_lab/flat_lead_2026_10_02.md` §4 の指標をそのまま出す:

- 主: 発生倍率 = 的中数 ÷ Σ(0.75 / 確定オッズ)（買った全目・`wt_odds` の確定三連単）
- 副1: 同じレースで実際に売った現行商品（`netkeirin_submissions`・決済済み）との回収率
- 副2: 上位3本を除いた回収率
- 参考: 回収率・表示的中（払戻 > 投資）・10万円以上の的中数

    KEIRIN_DB_URL=... python scripts/flat_lead_report.py --from 2026-10-03 --to 2026-12-02
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 発生倍率に掛ける払戻率（`L_lead` の判定と同じ）。
PAYBACK = 0.75
#: 事前登録の閾値（§4）。**判定の前に変えないこと。**
OCC_ADOPT = 1.10
OCC_REJECT = 1.00
ROI_X3_MIN = 75.0


def _legs(raw) -> list[dict]:
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def summarize(rows: list[dict], final_odds: dict[str, dict[str, float]],
              sold: dict[str, tuple[int, int]]) -> dict:
    """行を事前登録の指標へまとめる（DB に依存しない純関数）。

    rows: `type_lab_picks` の `L_flat` 決済済み行（`race_key` / `legs` / `payout` / `void_refund`）
    final_odds: race_key → {"5-6-1": 確定オッズ}
    sold: race_key → (実際に売った現行商品の投資, 払戻)。`L_flat` 自身と `L_lead` は含めない

    >>> rows = [{"race_key": "a", "legs": [{"combo": "5-6-1", "stake": 5000},
    ...                                     {"combo": "5-6-2", "stake": 5000}],
    ...          "payout": 150000, "void_refund": 0},
    ...         {"race_key": "b", "legs": [{"combo": "1-2-3", "stake": 10000}],
    ...          "payout": 0, "void_refund": 0}]
    >>> odds = {"a": {"5-6-1": 30.0, "5-6-2": 15.0}, "b": {"1-2-3": 7.5}}
    >>> s = summarize(rows, odds, {"a": (10000, 0)})
    >>> s["n"], s["hits"], round(s["occ"], 3), s["roi"]
    (2, 1, 5.714, 750.0)
    >>> s["paired_n"], s["paired_cur_roi"], s["paired_flat_roi"]
    (1, 0.0, 1500.0)
    """
    inv = pay = 0.0
    hits = shown = big = 0
    expected = 0.0
    missing_odds = 0
    pays: list[float] = []
    per_race: list[tuple[float, float]] = []
    p_inv = p_pay = c_inv = c_pay = 0.0
    paired: list[tuple[float, float, float, float]] = []
    for r in rows:
        legs = _legs(r["legs"])
        stake = float(sum(int(x["stake"]) for x in legs))
        ret = float(r.get("payout") or 0) + float(r.get("void_refund") or 0)
        inv += stake
        pay += ret
        pays.append(ret)
        per_race.append((stake, ret))
        if ret > 0 and r.get("payout"):
            hits += 1
        if ret > stake:
            shown += 1
        if ret >= 100_000:
            big += 1
        fo = final_odds.get(str(r["race_key"]), {})
        for x in legs:
            o = fo.get(str(x["combo"]))
            if o and o > 0:
                expected += PAYBACK / float(o)
            else:
                missing_odds += 1
        cur = sold.get(str(r["race_key"]))
        if cur:
            p_inv += stake
            p_pay += ret
            c_inv += cur[0]
            c_pay += cur[1]
            paired.append((stake, ret, float(cur[0]), float(cur[1])))
    pays.sort(reverse=True)
    roi = pay / inv * 100 if inv else float("nan")
    return dict(
        n=len(rows), hits=hits, shown=shown, big=big, inv=inv, pay=pay, roi=roi,
        roi_x3=(pay - sum(pays[:3])) / inv * 100 if inv else float("nan"),
        expected=expected, occ=hits / expected if expected else float("nan"),
        missing_odds=missing_odds, per_race=per_race, paired=paired,
        paired_n=len(paired),
        paired_flat_roi=p_pay / p_inv * 100 if p_inv else float("nan"),
        paired_cur_roi=c_pay / c_inv * 100 if c_inv else float("nan"),
    )


def occ_ci(rows: list[dict], final_odds: dict, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """発生倍率の 95% 範囲（レース単位のブートストラップ）。"""
    unit = []
    for r in rows:
        fo = final_odds.get(str(r["race_key"]), {})
        e = sum(PAYBACK / float(fo[str(x["combo"])]) for x in _legs(r["legs"])
                if fo.get(str(x["combo"])))
        unit.append((1 if (r.get("payout") or 0) > 0 else 0, e))
    if not unit:
        return float("nan"), float("nan")
    rnd = random.Random(seed)
    m = len(unit)
    v = []
    for _ in range(n):
        s = [unit[rnd.randrange(m)] for _ in range(m)]
        e = sum(x[1] for x in s)
        v.append(sum(x[0] for x in s) / e if e else float("nan"))
    v.sort()
    return v[int(0.025 * n)], v[int(0.975 * n)]


def verdict(s: dict) -> str:
    """事前登録 §4 の判定。"""
    if s["occ"] != s["occ"]:
        return "判定不能（行が無い）"
    if s["occ"] < OCC_REJECT:
        return "不採用（発生倍率 < 1.00）"
    if s["occ"] >= OCC_ADOPT and s["roi_x3"] >= ROI_X3_MIN:
        return "置き換えの提案へ（発生倍率 ≥ 1.10 ∧ 上位3本除く ≥ 75%）"
    return "判定保留（期間の延長をユーザーと決める）"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", required=True)
    ap.add_argument("--to", dest="d2", required=True)
    # 集計の動作確認用（`L_lead` の行で回せる）。判定は既定の `L_flat` で出す。
    ap.add_argument("--plan", default="L_flat")
    a = ap.parse_args()
    from src.database import get_connection
    from src.type_lab import FLAT_LEAD_PLAN_KEYS, LINE_LEAD_PLAN_KEYS
    # 🔴 副1 の「現行」は**置き換えの対象（型ラボの商品）だけ**。混戦では L_flat ⊇ L_lead なので、
    #    L_lead の実売を入れるとほぼ同じ買い目同士を比べることになる（事前登録 §4）。
    not_current = {a.plan} | set(FLAT_LEAD_PLAN_KEYS) | set(LINE_LEAD_PLAN_KEYS)
    with get_connection() as c:
        rows = [dict(zip(("race_key", "legs", "payout", "void_refund", "race_date"), r))
                for r in c.execute(
                    "SELECT race_key, legs, payout, void_refund, race_date FROM type_lab_picks"
                    " WHERE plan_key = ? AND mode = 'live' AND settled_at IS NOT NULL"
                    "   AND race_date BETWEEN ? AND ? ORDER BY race_key",
                    (a.plan, a.d1, a.d2)).fetchall()]
        keys = sorted({r["race_key"] for r in rows})
        final_odds: dict[str, dict[str, float]] = defaultdict(dict)
        sold: dict[str, tuple[int, int]] = {}
        for i in range(0, len(keys), 500):
            ch = keys[i:i + 500]
            ph = ",".join("?" * len(ch))
            for rk, comb, o in c.execute(
                    f"SELECT race_key, combination, odds_value FROM wt_odds"
                    f" WHERE bet_type = 'trifecta' AND race_key IN ({ph})", ch).fetchall():
                if o is not None:
                    final_odds[rk][str(comb)] = float(o)
            # 🔴 `netkeirin_submissions.race_key` は `#ランク` の接尾辞を持つことがある
            for rk, rank, bet, pay in c.execute(
                    "SELECT race_key, rank_key, settled_bet, settled_payout"
                    "  FROM netkeirin_submissions WHERE settled_at IS NOT NULL AND deleted_at IS NULL"
                    f"   AND split_part(race_key, '#', 1) IN ({ph})", ch).fetchall():
                if str(rank) in not_current:
                    continue
                base = str(rk).split("#")[0]
                b0, p0 = sold.get(base, (0, 0))
                sold[base] = (b0 + int(bet or 0), p0 + int(pay or 0))
    s = summarize(rows, final_odds, sold)
    lo, hi = occ_ci(rows, final_odds)
    days = len({str(r["race_date"]) for r in rows})
    print(f"{a.plan}（紙上）{a.d1}〜{a.d2}  {s['n']}R / {days}日")
    print(f"  主  発生倍率 {s['occ']:.3f} [{lo:.2f}, {hi:.2f}]  的中 {s['hits']} ÷ 見込み {s['expected']:.1f}"
          f"（確定オッズ欠け {s['missing_odds']} 目）")
    print(f"  副1 同じレースで売った現行: {s['paired_n']}R"
          f"  現行 {s['paired_cur_roi']:.1f}% ↔ {a.plan} {s['paired_flat_roi']:.1f}%")
    print(f"  副2 上位3本を除いた回収率 {s['roi_x3']:.1f}%")
    print(f"  参考 回収率 {s['roi']:.1f}%  表示的中 {s['shown']}/{s['n']}  10万+ {s['big']}本")
    print(f"  判定: {verdict(s)}")


if __name__ == "__main__":
    main()
