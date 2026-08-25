#!/usr/bin/env python3
"""9F(RANK_9F) — **看板穴埋めで売っている9車**の成績記録（2026-08-25 新設）。

## なぜ要るのか

`marquee.marquee_race_nos()` は **開催グレードが GIII 以上ならその開催の全レース**を
穴埋め対象にする。9車はほぼ全てが GIII 以上なので、**9車は開催まるごと入稿される**。
ところが picks_history を書いているのは `backfill_9c_rank_wt.py` だけで、そちらは
`rank_9c_daily_select`（ゲート通過）しか回さない。結果:

    9車の入稿（netkeirin_submissions・全期間）
      rank（ゲート通過）  31件 → picks_history 31件
      marquee_fill（穴埋め）55件 → picks_history **1件**

🔴 **売った86件のうち成績が残るのは32件（37%）。しかも残るのはゲートを通った
   易しい側**なので、記録は実態より良く見える（vintage 4,593R 実測）:

    全網羅          的中 39.2% / ROI 75.9%
    ゲート通過       的中 50.3% / ROI 81.0%   ← 記録に出ている側
    ゲート不通過      的中 29.9% / ROI 71.7%   ← 記録に無い側

本スクリプトは**ゲートを通らなかった側**を `#9F` として記録する。
`#9C` と `#9F` は母集団が排他なので、**union が「9車を全網羅したときの姿」**になる。

## 買い方は穴埋め経路の実挙動に合わせる（9C とは別物）

| | `#9C`（ゲート通過） | **`#9F`（穴埋め）** |
|---|---|---|
| 軸 | `rank_7c_select_axis` ＝ p3 上位2車 | **`submit_marquee_wt._axes()` ＝ ライン組み替え** |
| 相手 | `rank_7c_select_legs`（足切り） | 同じ足切り。ただし**下限を割ったら上位から戻す** |
| 点数不足 | **そのレースを落とす** | **落とせない**（必ず出す方針） |

🔴 **軸の正本は `submit_marquee_wt._axes()` を import して使う。** ここへ写すと
   片方だけ直る。実測でも p3上位2車と組み替えでは外れ群の ROI が 63.5% ↔ 69.1% と
   別物になる（`keirin/scripts/exp_9axis/axis_three_way.py`）。

⚠️ 欠車は `void_by_dns` で処理する（`_manual_partners` は出走表の全車から組むが、
   採点では欠車を買えないため）。ここだけ本番より厳しい。

⚠️ **`CURRENT_PAPER_RANKS` には登録しない。** 登録すると kiseki Web の
   `_PAPER_RANK_LABELS` との機械照合（`backend/tests/test_keirin_rank_consistency.py`）
   が落ち、Web の集計にも混ざる。これは**分析用の記録**なので rank 名だけで持つ。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_9f_rank_wt.py \
        --start 2024-01-01 --end 2026-08-24 --model lgbm_wt_eval_m2608 [--wipe] [--dry-run]
    # 全期間を honest に作るときは scripts/rebuild_9f_walkforward_pg.py を使う
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7c_rank_wt import (  # noqa: E402
    _load_board_frames_wt, _load_trio_boards,
)
from scripts.submit_marquee_wt import _axes  # noqa: E402  🔴 軸の正本
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.evaluation.void_rules import void_by_dns  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.rebuild_stakes import (  # noqa: E402
    load_morning_boards, load_submitted_stakes, stakes_for_combos,
)
from src.result_top3 import hit_trio, representative, winning_trios  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN, RANK_9C_P3_SUM_MIN,
    rank_7c_select_axis, rank_7c_select_legs,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402

N_CAR = 9
RANK_NAME = "RANK_9F"
SUFFIX = "#9F"


def _fill_axes(top3_probs: dict[int, float], lines: dict[int, dict]) -> tuple[int, int] | None:
    """穴埋め経路の軸2車。**判定は `submit_marquee_wt._axes()` に委ねる**。

    `_axes` は「`ai_rank` 昇順に並んだ riders」を期待するので、
    3着内率の降順（同値は車番昇順＝`rank_7c_select_axis` と同じ規則）で詰め直す。
    """
    order = sorted(top3_probs, key=lambda f: (-top3_probs[f], f))
    entry = {"riders": [{"frame_no": f, "ai_rank": i} for i, f in enumerate(order)]}
    return _axes(entry, lines)


def _fill_legs(others: list[int], top3_probs: dict[int, float]) -> list[int]:
    """穴埋め経路の相手。足切りは同じだが、**下限を割ったら上位から戻す**。

    🔴 ゲート通過側（`rank_9c_daily_select`）は逆にレースを落とす。
       看板は必ず出す方針なので役割が違う（`_manual_partners` の docstring）。
    """
    kept = rank_7c_select_legs(others, top3_probs, p3_min=RANK_9C_LEG_P3_MIN)
    if len(kept) < RANK_9C_LEGS_MIN:
        kept = sorted(others, key=lambda c: (-top3_probs.get(c, 0.0), c))[:RANK_9C_LEGS_MIN]
    return kept


def build_rows(model_name: str, date_from: str, date_to: str,
               win_model_name: str | None = None,
               bad_model_name: str | None = None) -> list[dict]:
    """`#9F` 行（採点済み）を構築する。母集団は **9車 ∧ 9C のゲートを通らない**もの。

    win_model_name / bad_model_name: 使わない（rebuild 側と signature を揃えるだけ）。
    """
    model = load_model(model_name)
    df = build_features_wt(load_raw_data_wt(min_date=date_from, max_date=date_to))
    if df.empty:
        return []
    with get_connection() as c:
        ne_map = dict(c.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        date_map = dict(c.execute(
            "SELECT race_key, race_date FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        cup_grade_map = dict(c.execute(
            "SELECT race_key, cup_grade FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        race_type_map = dict(c.execute(
            "SELECT race_key, race_type FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        rksN = [rk for rk, ne in ne_map.items() if ne and int(ne) == N_CAR]
        fins: dict[str, list[tuple[int, int]]] = {}
        lines: dict[str, dict[int, dict]] = {}
        for i in range(0, len(rksN), 900):
            chunk = rksN[i:i + 900]
            ph = ",".join("?" * len(chunk))
            for rk, fno, fo in c.execute(
                    f"SELECT race_key, frame_no, finish_order FROM wt_entries "
                    f"WHERE race_key IN ({ph})", chunk):
                if fo is not None and fo >= 1:
                    fins.setdefault(rk, []).append((fo, int(fno)))
            for r in c.execute(
                    f"SELECT race_key, frame_no, line_group, is_line_leader, line_size "
                    f"FROM wt_entries WHERE race_key IN ({ph})", chunk):
                lines.setdefault(r["race_key"], {})[int(r["frame_no"])] = {
                    "line_group": r["line_group"],
                    "is_line_leader": r["is_line_leader"],
                    "line_size": r["line_size"],
                }
    df = df[df["race_key"].isin(set(rksN))].copy()
    if df.empty:
        return []
    X = prepare_X(df)
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    keys = df["race_key"].unique().tolist()
    trio_bd = _load_trio_boards(keys)
    board_map = _load_board_frames_wt(keys)
    pm = _load_payouts_wt(keys)
    morning_boards = load_morning_boards(keys)
    # 実際に入稿した賭け金があればそれを正本にする。穴埋めも rank_key は "9C"。
    submitted_stakes = load_submitted_stakes(keys, "9C")

    rows: list[dict] = []
    for rk, g in df.groupby("race_key"):
        if ne_map.get(rk) != N_CAR or len(g) != N_CAR:
            continue
        trio = trio_bd.get(rk)
        board = board_map.get(rk)
        fin = sorted(fins.get(rk, []))
        if not trio or not board or len(fin) < 3:
            continue
        top3_probs = {int(r.frame_no): float(r.pred_prob) for r in g.itertuples(index=False)}

        # --- 母集団: 9C のゲートを通らないレースだけ（#9C と排他にする）---
        sel = rank_7c_select_axis(top3_probs)
        if sel is None:
            continue
        c_axis1, c_axis2, _ = sel
        gate = calibrated_p3_sum_top2(top3_probs, race_type_map.get(rk), cup_grade_map.get(rk))
        gate_v = float(gate) if gate is not None else 0.0
        c_thirds = sorted(set(top3_probs) - {c_axis1, c_axis2})
        c_skip, c_others = void_by_dns(c_axis1, c_axis2, c_thirds, board)
        c_legs = [] if c_skip else rank_7c_select_legs(
            c_others, top3_probs, p3_min=RANK_9C_LEG_P3_MIN)
        passes_gate = (gate_v >= RANK_9C_P3_SUM_MIN) and (len(c_legs) >= RANK_9C_LEGS_MIN)
        if passes_gate:
            continue                      # そちらは #9C が記録する

        # --- 穴埋め経路の買い目 ---
        ax = _fill_axes(top3_probs, lines.get(rk, {}))
        if ax is None:
            continue
        axis1, axis2 = ax
        thirds_full = sorted(set(top3_probs) - {axis1, axis2})
        skip_race, others = void_by_dns(axis1, axis2, thirds_full, board)
        if skip_race:                     # 軸が欠車＝買えない
            continue
        legs = _fill_legs(others, top3_probs)

        combos, bought = [], []
        for x in legs:
            key = frozenset({axis1, axis2, x})
            if key in trio:
                combos.append(key)
                bought.append(x)
        if not combos:
            continue

        wins = winning_trios(fin)
        win_key = hit_trio(combos, wins)
        hit = win_key is not None
        trio_pay = pm.get(rk, {}).get(("trio", win_key or representative(wins)), 0)
        stakes = stakes_for_combos(axis1, axis2, combos, top3_probs,
                                   morning_boards.get(rk),
                                   submitted=submitted_stakes.get(rk))
        pay = trio_pay * stakes[win_key] // 100 if hit else 0
        rows.append({
            "race_date": date_map.get(rk, ""),
            "race_key": f"{rk}{SUFFIX}", "rank": RANK_NAME,
            "pred_combo": f"{axis1}={axis2}-" + ",".join(str(x) for x in bought),
            "n_combos": len(combos), "hit": int(hit), "payout": pay,
            "trio_payout": trio_pay, "bet_amount": sum(stakes.values()),
            "gate_label": None,
        })
    return rows


def wipe_rows(date_from: str, date_to: str, dry_run: bool) -> None:
    cond = f"rank='{RANK_NAME}' AND race_key LIKE '%{SUFFIX}' AND race_date BETWEEN ? AND ?"
    with get_connection() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM picks_history WHERE {cond}",
                         (date_from, date_to)).fetchone()[0]
        print(f"[backfill-9f] 既存 {SUFFIX} 行（{date_from}〜{date_to}）: {n}件 → 削除"
              f"{'（dry-run）' if dry_run else ''}")
        if not dry_run and n:
            conn.execute(f"DELETE FROM picks_history WHERE {cond}", (date_from, date_to))
            conn.commit()

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        return
    import psycopg2
    cond_pg = f"rank='{RANK_NAME}' AND race_key LIKE %s AND race_date BETWEEN %s AND %s"
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM keirin.picks_history WHERE {cond_pg}",
                        (f"%{SUFFIX}", date_from, date_to))
            n = cur.fetchone()[0]
            print(f"[backfill-9f] VPS PG 既存 {SUFFIX} 行: {n}件 → 削除"
                  f"{'（dry-run）' if dry_run else ''}")
            if not dry_run and n:
                cur.execute(f"DELETE FROM keirin.picks_history WHERE {cond_pg}",
                            (f"%{SUFFIX}", date_from, date_to))


def insert_rows(rows: list[dict], dry_run: bool) -> None:
    if dry_run or not rows:
        return
    rows_ins = [{**r, "miwokuri": False} for r in rows]
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO picks_history "
            "(race_date,race_key,rank,pred_combo,n_combos,hit,payout,"
            " trio_payout,bet_amount,route,miwokuri,gate_label) "
            "VALUES (:race_date,:race_key,:rank,:pred_combo,:n_combos,:hit,"
            " :payout,:trio_payout,:bet_amount,'wt',:miwokuri,:gate_label)",
            rows_ins)
        conn.commit()
    print(f"[backfill-9f] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-9f] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
        return
    import psycopg2
    from psycopg2.extras import execute_batch
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            execute_batch(cur, """
                INSERT INTO keirin.picks_history
                  (race_date,race_key,rank,pred_combo,n_combos,hit,payout,
                   trio_payout,bet_amount,route,miwokuri,gate_label)
                VALUES (%(race_date)s,%(race_key)s,%(rank)s,%(pred_combo)s,
                        %(n_combos)s,%(hit)s,%(payout)s,%(trio_payout)s,
                        %(bet_amount)s,'wt',FALSE,%(gate_label)s)
                ON CONFLICT (race_key) DO UPDATE SET
                  race_date=EXCLUDED.race_date, rank=EXCLUDED.rank,
                  pred_combo=EXCLUDED.pred_combo, n_combos=EXCLUDED.n_combos,
                  hit=EXCLUDED.hit, payout=EXCLUDED.payout,
                  trio_payout=EXCLUDED.trio_payout,
                  bet_amount=EXCLUDED.bet_amount, miwokuri=FALSE,
                  gate_label=EXCLUDED.gate_label
            """, rows, page_size=200)
    print(f"[backfill-9f] VPS PG {len(rows)}件 書き込み完了")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", required=False)
    ap.add_argument("--model", default="lgbm_wt_eval")
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.end:
        assert_vintage_for_past(args.end, {"eval": args.model})

    from datetime import date
    end = args.end or date.today().strftime("%Y-%m-%d")
    print(f"[backfill-9f] model={args.model} {args.start}〜{end}", flush=True)

    if args.wipe:
        wipe_rows(args.start, end, args.dry_run)

    rows = build_rows(args.model, args.start, end)
    n_hit = sum(r["hit"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    pay = sum(r["payout"] for r in rows)
    print(f"[backfill-9f] {len(rows)}R 的中{n_hit} 投資{bet:,} 回収{pay:,} "
          f"ROI {100 * pay / bet if bet else 0:.1f}%", flush=True)
    insert_rows(rows, args.dry_run)


if __name__ == "__main__":
    main()
