#!/usr/bin/env python3
"""9C(RANK_9C) の過去分バックフィル（9車のベースモデル・2026-08-14 新設）。

`backfill_7c_rank_wt.py` の9車版。**軸と相手の選び方は 7C と同じ関数**
（`rank_7c_select_*` は車数に依存しない）で、違うのは閾値と、7C が持つ
飾りを持ち込まないこと:

  - 三連単への切替（`trifecta_7c`）は**入れない**（9車で未検証）
  - 低配当パターンの見送り・3着内率の落差カットも**入れない**（同上）
  - 相手の足切りは `RANK_9C_LEG_P3_MIN`(0.15)、点数の下限は `RANK_9C_LEGS_MIN`(3)
  - 選別は `rank_9c_daily_select`（上位2車の3着内率合計 >= 1.30。
    **GII以上の開催だけ 1.40**・2026-08-16。`rank_9c_p3_sum_min` が正本）

🔴 **7C の定数を持ち込んではいけない。** `pred_top3_pct` はレース内合計が3.0に
   正規化されるため、車数が増えると上位2車の合計が構造的に下がる。
   7C の 1.44 は9車では 21.2% しか通らない（7車は53.7%）。

⚠️ 7C と同じく他ランクと**論理的に排他ではない**（wt_overlap_n を見ない）。
   picks_history の race_key は `{レースキー}#9C` なので他ランク行と共存する。

⚠️ 本番モデル `lgbm_wt_eval` は full_refit でホールドアウト無しのため、
   過去へ遡って使うと in-sample になる。walk-forward 再構築では
   月次vintage（`lgbm_wt_eval_mYYMM`）を渡すこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_9c_rank_wt.py \
        --start 2024-01-01 --end 2026-08-13 [--wipe] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 盤面・三連複板の読み込みは 7C 版と**同じ関数を使う**（写すと片方だけ直る）。
from scripts.backfill_7c_rank_wt import (  # noqa: E402
    _load_board_frames_wt, _load_trio_boards,
)
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.evaluation.void_rules import void_by_dns  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.rebuild_stakes import load_morning_boards, stakes_for_combos  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN, rank_7c_select_axis,
    rank_7c_select_legs, rank_9c_daily_select,
)
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402
from src.result_top3 import hit_trio, representative, winning_trios

N_CAR = 9


def build_rows(model_name: str, date_from: str, date_to: str,
               win_model_name: str | None = None,
               bad_model_name: str | None = None) -> list[dict]:
    """バックフィル対象の 9C(#9C) 行（採点済み）を構築する。

    win_model_name / bad_model_name: **9C では使わない**（軸も相手も pred_prob
      だけで決まる）。rebuild 側の共通ヘルパと signature を揃えるためだけに受け取る。
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
        # ゲートは較正後の p3合計 で見る（2026-08-17）。開催グレードとレース種別が
        # 較正のセグメント。⚠️ `cup_grade` は 2026-08-14 保存開始で以前は NULL、
        # `grade_group(None)` が「F級」（ほぼ恒等）へ倒すので静かに減らない。
        cup_grade_map = dict(c.execute(
            "SELECT race_key, cup_grade FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        race_type_map = dict(c.execute(
            "SELECT race_key, race_type FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        rksN = [rk for rk, ne in ne_map.items() if ne and int(ne) == N_CAR]
        fins: dict[str, list[tuple[int, int]]] = {}
        for i in range(0, len(rksN), 900):
            chunk = rksN[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 "WHERE race_key IN (%s)" % ",".join("?" * len(chunk)))
            for rk, fno, fo in c.execute(q, chunk):
                if fo is not None and fo >= 1:
                    fins.setdefault(rk, []).append((fo, int(fno)))
    df = df[df["race_key"].isin(set(rksN))].copy()
    if df.empty:
        return []
    X = prepare_X(df)
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    keys = df["race_key"].unique().tolist()
    trio_bd = _load_trio_boards(keys)
    board_map = _load_board_frames_wt(keys)
    pm = _load_payouts_wt(keys)

    candidates: list[dict] = []
    for rk, g in df.groupby("race_key"):
        if ne_map.get(rk) != N_CAR or len(g) != N_CAR:
            continue
        trio = trio_bd.get(rk)
        board = board_map.get(rk)
        fin = sorted(fins.get(rk, []))
        if not trio or not board or len(fin) < 3:
            continue

        top3_probs = {int(r.frame_no): float(r.pred_prob) for r in g.itertuples(index=False)}
        sel = rank_7c_select_axis(top3_probs)      # 車数非依存
        if sel is None:
            continue
        axis1, axis2, p3_sum = sel

        # 欠車判定は本番と同一の void_by_dns。軸欠車=レース無効／相手欠車=その目のみ除外。
        thirds_full = sorted(set(top3_probs) - {axis1, axis2})
        skip_race, others = void_by_dns(axis1, axis2, thirds_full, board)
        if skip_race:
            continue

        # 相手は盤面に残った車から足切りする（朝の候補をそのまま使わない）。
        legs = rank_7c_select_legs(others, top3_probs, p3_min=RANK_9C_LEG_P3_MIN)

        candidates.append({
            "race_key": rk, "race_date": date_map.get(rk, ""),
            "n_entries": N_CAR,
            "axis1": axis1, "axis2": axis2,
            "p3_sum_top2": p3_sum, "legs_9c": legs,
            "p3_sum_top2_cal": calibrated_p3_sum_top2(
                top3_probs, race_type_map.get(rk), cup_grade_map.get(rk)),
            "cup_grade": cup_grade_map.get(rk),
            "trio": trio,
            # 🔴 同着では当たり目が2通りになる（`src/result_top3` が正本）。
            "actual_top3": representative(winning_trios(fin)),
            "wins": winning_trios(fin),
            "top3_probs": top3_probs,
        })

    # 朝オッズ盤面は 2026-06-08 以降にしか無い。無い期間は p3 単独へ落ちる。
    morning_boards = load_morning_boards([c["race_key"] for c in candidates])
    rows: list[dict] = []
    for c_ in rank_9c_daily_select(candidates):
        axis1, axis2 = c_["axis1"], c_["axis2"]
        trio = c_["trio"]
        combos, bought = [], []
        for x in c_["legs_9c"]:
            key = frozenset({axis1, axis2, x})
            if key in trio:
                combos.append(key)
                bought.append(x)
        # オッズ欠けで点数ゲートを割ったら買わない（live の judge_rank_9c と同一）。
        if len(combos) < RANK_9C_LEGS_MIN:
            continue
        rk = c_["race_key"]
        # 同着では当たり目が複数ある。**買った目**で払戻を引く。
        win_key = hit_trio(combos, c_["wins"])
        hit = win_key is not None
        trio_pay = pm.get(rk, {}).get(("trio", win_key or c_["actual_top3"]), 0)
        # 賭け金は入稿と同じ傾斜配分（最終オッズで配分すると先読みになる）。
        stakes = stakes_for_combos(axis1, axis2, combos, c_.get("top3_probs") or {},
                                   morning_boards.get(rk))
        pay = trio_pay * stakes[win_key] // 100 if hit else 0
        rows.append({
            "race_date": c_["race_date"],
            "race_key": f"{rk}#9C", "rank": "RANK_9C",
            "pred_combo": f"{axis1}={axis2}-" + ",".join(str(x) for x in bought),
            "n_combos": len(combos), "hit": int(hit), "payout": pay,
            "trio_payout": trio_pay, "bet_amount": sum(stakes.values()),
            "gate_label": None,
        })
    return rows


def wipe_rows(date_from: str, date_to: str, dry_run: bool) -> None:
    cond = "rank='RANK_9C' AND race_key LIKE '%#9C' AND race_date BETWEEN ? AND ?"
    with get_connection() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM picks_history WHERE {cond}",
                         (date_from, date_to)).fetchone()[0]
        print(f"[backfill-9c] 既存 #9C 行（{date_from}〜{date_to}）: {n}件 → 削除"
              f"{'（dry-run）' if dry_run else ''}")
        if not dry_run and n:
            conn.execute(f"DELETE FROM picks_history WHERE {cond}", (date_from, date_to))
            conn.commit()

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        return
    import psycopg2
    cond_pg = "rank='RANK_9C' AND race_key LIKE %s AND race_date BETWEEN %s AND %s"
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM keirin.picks_history WHERE {cond_pg}",
                        ("%#9C", date_from, date_to))
            n = cur.fetchone()[0]
            print(f"[backfill-9c] VPS PG 既存 #9C 行: {n}件 → 削除"
                  f"{'（dry-run）' if dry_run else ''}")
            if not dry_run and n:
                cur.execute(f"DELETE FROM keirin.picks_history WHERE {cond_pg}",
                            ("%#9C", date_from, date_to))


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
    print(f"[backfill-9c] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-9c] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
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
    print(f"[backfill-9c] VPS PG {len(rows)}件 書き込み完了")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", required=False)
    ap.add_argument("--model", default="lgbm_wt_eval")
    ap.add_argument("--wipe", action="store_true",
                    help="書き込み前に対象期間の既存 #9C 行を削除")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 過去日に本番モデルを当てると in-sample になるので落とす。
    if args.end:
        assert_vintage_for_past(args.end, {"eval": args.model})

    from datetime import date
    end = args.end or date.today().strftime("%Y-%m-%d")
    print(f"[backfill-9c] model={args.model} {args.start}〜{end}", flush=True)

    if args.wipe:
        wipe_rows(args.start, end, args.dry_run)

    rows = build_rows(args.model, args.start, end)
    n_hit = sum(r["hit"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    pay = sum(r["payout"] for r in rows)
    print(f"[backfill-9c] {len(rows)}R 的中{n_hit} 投資{bet:,} 回収{pay:,} "
          f"ROI {100 * pay / bet if bet else 0:.1f}%", flush=True)
    insert_rows(rows, args.dry_run)


if __name__ == "__main__":
    main()
