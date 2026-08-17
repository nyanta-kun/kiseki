#!/usr/bin/env python3
"""7H2（7車・荒れ読み・RANK_7H2）の過去分バックフィル。

**候補生成そのものは `build_7h2_candidates.build()` を呼ぶ**。本スクリプトは
その候補に対して、live と同じ盤面フィルタ・採点・記録だけを行う。

## 🔴 2026-08-18 の三連複一本化を前提にしている

7H2 は当初「三連単F 10点 + 三連複BOX 10点」の2券種だったが、三連単側が
**0/44 的中**・三連複の枠が 10,000円中 3,000円しか無く**33.3倍取らないとガミ**
という設計欠陥があり、三連複のみへ一本化した（`RANK_7H2_TRIFECTA_ENABLED=False`）。
本スクリプトは一本化後の買い方（三連複BOX に 10,000円を均等割り）で採点する。

⚠️ したがって `picks_history` に残っている 2026-08-10〜14 の旧 44件とは
**買い方が違う**。比較するときは必ず本スクリプトで作り直した行どうしで比べること。

## 採点式

  - 買い目は◎を除いたプール上位5車の三連複BOX（最大10点）
  - 賭け金は**均等**（`rank_7h2_stakes`）。的中した点の賭け金で按分する
  - `trio_payout` は **100円あたりの確定配当**（賭け金非依存の生値）。
    実払戻額は `payout` に入る。**ここを取り違えると Web が実額と配当を混ぜる**
  - 返還処理なし（実精算方式）

## 欠車の扱い

  - 盤面（wt_odds の trio）に無い目は**その目だけ落として購入継続**
  - 残りが0点になったら skip
  - 残った点数で `rank_7h2_stakes()` を**引き直す**（1レース1万円の枠は動かさない）

## ⚠️ 必ず vintage モデルで流すこと

本番モデル（`lgbm_wt_eval` 等）は全期間学習なので、過去へ遡って適用すると
in-sample になる（model-vintage look-ahead）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_7h2_rank_wt.py \\
        --start 2026-07-01 --end 2026-07-31 \\
        --eval-model lgbm_wt_eval_m2607 --win-model lgbm_wt_win_m2607 \\
        [--wipe] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 盤面の読み込みは 7C 版と**同じ関数を使う**（写すと片方だけ直る）。
from scripts.backfill_7c_rank_wt import _load_trio_boards  # noqa: E402
from scripts.build_7h2_candidates import build as build_candidates  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.strategy_wt import RANK_7H2_NE, rank_7h2_stakes  # noqa: E402
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402

RANK = "RANK_7H2"
SUFFIX = "#7H2"


def _trio_key(combo: str) -> frozenset | None:
    """'1=2=3' / '1-2-3' を車番の集合へ。

    🔴 **区切り文字を決め打ちしないこと。** 三連複の表記は 2026-06 の途中で
       `1=2=3` から `1-2-3` へ変わっている（`keirin_wt_odds_combination_format`）。
    """
    try:
        nums = [int(p) for p in re.split(r"[-=→]", str(combo)) if p != ""]
    except ValueError:
        return None
    return frozenset(nums) if len(nums) == 3 else None


def _load_finishes(race_keys: list[str]) -> dict[str, frozenset]:
    """{race_key: {1〜3着の車番}}。3着まで揃わないレースは含めない。"""
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    if not race_keys:
        return {}
    with get_connection() as c:
        for i in range(0, len(race_keys), 900):
            chunk = race_keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 "WHERE finish_order BETWEEN 1 AND 3 AND race_key IN (%s)"
                 % ",".join("?" * len(chunk)))
            for rk, fno, fo in c.execute(q, chunk):
                out[rk].append((int(fo), int(fno)))
    return {rk: frozenset(f for _, f in sorted(v))
            for rk, v in out.items() if len(v) >= 3}


def build_rows(date_from: str, date_to: str, *, eval_model: str,
               win_model: str) -> list[dict]:
    """バックフィル対象の 7H2（#7H2）行（採点済み）を構築する。"""
    cands = build_candidates(date_from, date_to, eval_model, win_model)
    if not cands:
        return []

    race_keys = [c["race_key"] for c in cands]
    trio_bd = _load_trio_boards(race_keys)
    fins = _load_finishes(race_keys)
    pm = _load_payouts_wt(race_keys)

    rows: list[dict] = []
    for c in cands:
        rk = c["race_key"]
        board = trio_bd.get(rk)
        if not board:
            continue                        # 盤面が取れていない
        actual = fins.get(rk)
        if not actual:
            continue                        # 3着まで確定していない

        combos = []
        for t in (c.get("legs_trio") or []):
            key = _trio_key(t)
            if key is not None and key in board:
                combos.append(key)
        if not combos:
            continue                        # 欠車で買い目が全部消えた

        # 🔴 賭け金は**残った点数で均等に引き直す**。候補JSONの stake_trio を
        #    そのまま使うと落ちた目のぶん予算を下回る。
        unit, _u_tf, bet = rank_7h2_stakes(len(combos), 0)
        if not unit:
            continue

        hit = actual in combos
        trio_pay = pm.get(rk, {}).get(("trio", actual), 0)
        payout = trio_pay * unit // 100 if hit else 0

        rows.append({
            "race_date": c["race_date"],
            "race_key": rk + SUFFIX,
            "rank": RANK,
            "pred_combo": "三複:" + ",".join(
                "=".join(str(x) for x in sorted(k)) for k in combos),
            "n_combos": len(combos),
            "hit": int(hit),
            "payout": int(payout),
            # ⚠️ 全ランク共通で「100円あたりの確定配当」（賭け金非依存の生値）。
            "trio_payout": int(trio_pay),
            "trifecta_payout": 0,
            "bet_amount": int(bet),
        })
    return rows


def wipe(date_from: str, date_to: str, dry_run: bool) -> None:
    if dry_run:
        return
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM picks_history WHERE rank=? AND race_key LIKE ? "
            "AND race_date BETWEEN ? AND ?",
            (RANK, "%" + SUFFIX, date_from, date_to))
        conn.commit()


def insert_rows(rows: list[dict], dry_run: bool) -> None:
    if dry_run or not rows:
        return
    rows_ins = [{**r, "miwokuri": False} for r in rows]
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO picks_history "
            "(race_date,race_key,rank,pred_combo,n_combos,hit,payout,"
            " trio_payout,trifecta_payout,bet_amount,route,miwokuri) "
            "VALUES (:race_date,:race_key,:rank,:pred_combo,:n_combos,:hit,"
            " :payout,:trio_payout,:trifecta_payout,:bet_amount,'wt',:miwokuri)",
            rows_ins)
        conn.commit()
    print(f"[backfill-7h2] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-7h2] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
        return
    import psycopg2
    from psycopg2.extras import execute_batch
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            execute_batch(cur, """
                INSERT INTO keirin.picks_history
                  (race_date,race_key,rank,pred_combo,n_combos,hit,payout,
                   trio_payout,trifecta_payout,bet_amount,route,miwokuri)
                VALUES (%(race_date)s,%(race_key)s,%(rank)s,%(pred_combo)s,
                        %(n_combos)s,%(hit)s,%(payout)s,%(trio_payout)s,
                        %(trifecta_payout)s,%(bet_amount)s,'wt',FALSE)
                ON CONFLICT (race_key) DO UPDATE SET
                  race_date=EXCLUDED.race_date, rank=EXCLUDED.rank,
                  pred_combo=EXCLUDED.pred_combo, n_combos=EXCLUDED.n_combos,
                  hit=EXCLUDED.hit, payout=EXCLUDED.payout,
                  trio_payout=EXCLUDED.trio_payout,
                  trifecta_payout=EXCLUDED.trifecta_payout,
                  bet_amount=EXCLUDED.bet_amount, miwokuri=FALSE
            """, rows, page_size=200)
    print(f"[backfill-7h2] VPS PG {len(rows)}件 書き込み完了")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--eval-model", default="lgbm_wt_eval")
    ap.add_argument("--win-model", default="lgbm_wt_win")
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 過去日に本番モデルを当てると in-sample になるので落とす。
    assert_vintage_for_past(
        args.end, {"eval": args.eval_model, "win": args.win_model})

    rows = build_rows(args.start, args.end, eval_model=args.eval_model,
                      win_model=args.win_model)
    n_hit = sum(r["hit"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    pay = sum(r["payout"] for r in rows)
    # 表示的中（netkeirin の表示指標）は**ガミを除いた**的中。
    n_disp = sum(1 for r in rows if r["hit"] and r["payout"] > r["bet_amount"])
    print(f"[backfill-7h2] {args.start}〜{args.end}: {len(rows)}R "
          f"({RANK_7H2_NE}車) 的中{n_hit} "
          f"({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
          f"表示的中{n_disp} ({n_disp / len(rows) * 100 if rows else 0:.1f}%) "
          f"投資{bet:,} → 回収{pay:,} "
          f"ROI {pay / bet * 100 if bet else 0:.1f}%")
    if args.wipe:
        wipe(args.start, args.end, args.dry_run)
    insert_rows(rows, args.dry_run)


if __name__ == "__main__":
    main()
