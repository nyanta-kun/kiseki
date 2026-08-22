#!/usr/bin/env python3
"""7T1（三連単・高配当枠・RANK_7T1）の過去分バックフィル。

**候補生成そのものは `build_7t1_candidates.build()` を呼ぶ**。本スクリプトは
その候補に対して、live と同じ盤面フィルタ・採点・記録だけを行う。

## 🔴 2026-01-01 以降しか honest に作れない

7T1 は三連単オッズ予測モデル（`data/models/odds_tf_n7.txt`）で点数と足切りを
決める。このモデルの学習終端は **2025-12-31**（`odds_tf_meta.json` の
`train_end`）で、**月次 vintage が無い**。したがって 2025 年以前へ遡って適用すると
model-vintage look-ahead になる。他ランクの rebuild が 2024-01 から回るのに対し、
7T1 だけ **2026-01 が下限**。`ODDS_TF_TRAIN_END` で機械的に弾く。

## 採点式

  - 買い目は「軸1→軸2→相手」の順序つき三連単（**点数はレースごとに1〜5点**）
  - 賭け金は**均等**。的中した点の賭け金で按分する
  - `trifecta_payout` は **100円あたりの確定配当**（賭け金非依存の生値）。
    実払戻額は `payout` に入る。**ここを取り違えると Web が実額と配当を混ぜる**
    （2026-08-08 に 7H1 で実際に起きた）
  - 返還処理なし（実精算方式）

## 欠車の扱い

  - 盤面（wt_odds）に無い目は**その目だけ落として購入継続**（点数が減る）
  - 残りが0点になったら skip
  - 残った点数で `rank_7t1_stakes()` を**引き直す**（1レース1万円の枠は動かさない）
    ⚠️ 7T1 は均等配分なので、点が落ちると1点あたりの賭け金は**増える**。
       目標払戻(`RANK_7T1_TARGET_PAYOUT`)への到達性はむしろ緩くなる方向なので、
       落として続行してよい。

## ⚠️ 必ず vintage モデルで流すこと

本番モデル（`lgbm_wt_eval` 等）は全期間学習なので、過去へ遡って適用すると
in-sample になる（model-vintage look-ahead）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_7t1_rank_wt.py \\
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

from scripts.build_7t1_candidates import build as build_candidates  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7T1_NE, RANK_7T1_TARGET_PAYOUT, rank_7t1_stakes,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402
from src.result_top3 import hit_trifecta, winning_trifectas

RANK = "RANK_7T1"
SUFFIX = "#7T1"
# 三連単オッズ予測モデルの学習終端（`data/models/odds_tf_meta.json` の train_end）。
# 🔴 これ以前へ遡ると look-ahead。**月次 vintage が無いので回避策も無い。**
ODDS_TF_TRAIN_END = "2025-12-31"


def _tf_key(combo: str) -> tuple[int, ...] | None:
    """'1-2-3' を着順つきタプルへ。"""
    try:
        nums = [int(p) for p in re.split(r"[-=]", str(combo)) if p != ""]
    except ValueError:
        return None
    return tuple(nums) if len(nums) == 3 else None


def _load_tf_boards(race_keys: list[str]) -> dict[str, set]:
    """wt_odds から三連単の盤面（有効なオッズがある目）を返す。

    live と同じく **odds_value が有効な組み合わせのみ**を掲載とみなす
    （欠車を含む目はオッズが出ない）。
    """
    tf: dict[str, set] = defaultdict(set)
    if not race_keys:
        return {}
    with get_connection() as c:
        for i in range(0, len(race_keys), 900):
            chunk = race_keys[i:i + 900]
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 "WHERE bet_type='trifecta' AND race_key IN (%s)"
                 % ",".join("?" * len(chunk)))
            for rk, combo, odds in c.execute(q, chunk):
                if odds is None:
                    continue
                key = _tf_key(combo)
                if key is not None:
                    tf[rk].add(key)
    return dict(tf)


def _load_finishes(race_keys: list[str]) -> dict[str, list[tuple[int, int]]]:
    """{race_key: [(着順, 車番), ...]}。3着まで揃わないレースは含めない。

    🔴 **車番だけに畳まないこと。** 同着では三連単の当たりが複数になるので、
       着順そのものが要る（`src/result_top3` が正本）。
    """
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
    return {rk: sorted(v) for rk, v in out.items() if len(v) >= 3}


def assert_odds_model_is_honest(date_from: str) -> None:
    """三連単オッズモデルの学習終端より前を対象にしていないか検査する。

    🔴 **月次 vintage が無いので「古いモデルを使う」回避策が取れない。**
       静かに通すと 7T1 だけ in-sample な数字になり、他ランクと並べたときに
       強く見える（このリポジトリが繰り返し踏んでいる型）。
    """
    if date_from <= ODDS_TF_TRAIN_END:
        raise SystemExit(
            f"[backfill_7t1] {date_from} は三連単オッズモデルの学習終端"
            f"（{ODDS_TF_TRAIN_END}）以前です。7T1 の honest 再構築は "
            f"2026-01-01 以降のみ可能です（vintage が無いため回避策はありません）。")


def build_rows(date_from: str, date_to: str, *, eval_model: str,
               win_model: str, candidates_fn=None,
               rank: str = RANK, suffix: str = SUFFIX) -> list[dict]:
    """バックフィル対象の行（採点済み）を構築する。

    🔴 **RANK_7T2 もこの関数を通す**（`candidates_fn` に 7T2 の候補生成を渡す）。
       採点式・欠車の扱い・賭け金の引き直しは 7T1 と完全に同じなので、
       複製すると片方だけ直したときに無言で食い違う。
    """
    assert_odds_model_is_honest(date_from)
    cands = (candidates_fn or build_candidates)(
        date_from, date_to, eval_model, win_model)
    if not cands:
        return []

    race_keys = [c["race_key"] for c in cands]
    tf_bd = _load_tf_boards(race_keys)
    fins = _load_finishes(race_keys)
    pm = _load_payouts_wt(race_keys)

    rows: list[dict] = []
    for c in cands:
        rk = c["race_key"]
        board = tf_bd.get(rk)
        if not board:
            continue                        # 盤面が取れていない
        order = fins.get(rk)
        if not order:
            continue                        # 3着まで確定していない

        legs_all = [str(x) for x in (c.get("legs") or [])]
        legs = [t for t in legs_all if _tf_key(t) in board]
        if not legs:
            continue                        # 欠車で買い目が全部消えた
            # 🔴 **1点でも成立する**（7T1 は1点買いが約半数）。7H3 の "< 2" を
            #    そのまま持ってくると母集団の半分が消える。

        # 🔴 賭け金は**残った点数で均等に引き直す**。候補JSONの stakes をそのまま
        #    使うと落ちた目のぶん予算を下回る。
        #    ⚠️ 7H3 は PL 比率を引き継いでいたが 7T1 は**均等が仕様**なので
        #       比率の引き継ぎをしてはいけない（`rank_7t1_stakes` が正本）。
        stakes = rank_7t1_stakes(legs)
        bet = sum(stakes.values())

        # 🔴 同着では当たり目が複数ある（`src/result_top3` が正本）。
        wins_tf = winning_trifectas(order)
        tf_win = hit_trifecta([tuple(int(x) for x in t.split("-")) for t in legs
                               if t.count("-") == 2], wins_tf)
        hit = tf_win is not None
        hit_combo = "-".join(map(str, tf_win or wins_tf[0]))
        # pm のオッズは「100円あたりの払戻」なので賭け金で按分する
        tf_odds = pm.get(rk, {}).get(("trifecta", tf_win or wins_tf[0]), 0)
        payout = tf_odds * stakes[hit_combo] // 100 if hit else 0

        rows.append({
            "race_date": c["race_date"],
            "race_key": rk + suffix,
            "rank": rank,
            "pred_combo": "三単:" + ",".join(legs),
            "n_combos": len(legs),
            "hit": int(hit),
            "payout": int(payout),
            # ⚠️ 全ランク共通で「100円あたりの確定配当」（賭け金非依存の生値）。
            "trio_payout": 0,
            "trifecta_payout": int(tf_odds),
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
    print(f"[backfill-7t1] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-7t1] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
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
    print(f"[backfill-7t1] VPS PG {len(rows)}件 書き込み完了")


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
    # 🔴 集計の基準は**このランクの目標額**（`RANK_7T1_TARGET_PAYOUT`）。
    #    ここに固定値を書くと、目標額を変えたときに別の物差しで見ることになる
    #    （7H3 由来の 10万円固定が残っていて実際にそうなっていた）。
    big = sum(1 for r in rows if r["payout"] >= RANK_7T1_TARGET_PAYOUT)
    print(f"[backfill-7t1] {args.start}〜{args.end}: {len(rows)}R "
          f"({RANK_7T1_NE}車) 的中{n_hit} "
          f"({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
          f"投資{bet:,} → 回収{pay:,} "
          f"ROI {pay / bet * 100 if bet else 0:.1f}% / {RANK_7T1_TARGET_PAYOUT//10000}万円超 {big}件")
    if args.wipe:
        wipe(args.start, args.end, args.dry_run)
    insert_rows(rows, args.dry_run)


if __name__ == "__main__":
    main()
