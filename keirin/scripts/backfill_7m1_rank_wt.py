#!/usr/bin/env python3
"""7M1(RANK_7M1) の過去分バックフィル（中間層・2026-08-17 新設）。

`backfill_9c_rank_wt.py` の中間層版。買い目の形（三連複・軸2車流し）は同じで、
違うのは**母集団と相手の取り方**:

  - 母集団 … 上位2車の3着内率合計が `RANK_7M1_P3_SUM_MAX`(=7C の下限) **未満**
             ∧ その2車が WT公式印の ◎○ と**一致しない**（`wt_overlap_7c_n < 2`）
             **＋ 堅い帯のうち「◎あり・○なし ∧ 7C が見送る」レース**
             （2026-08-19・`RANK_7M1_FIRM_BAND`）
  - 相手  … **軸を除く5車のうち下位3車**（全体では指数5〜7番手）から、
             3着内率 < `RANK_7M1_LEG_P3_MIN` を削ったもの（最低2点・
             `rank_7m1_select_legs`）

🔴 **7C の相手足切り（`rank_7c_select_legs`）を流用してはいけない。**
   7M1 も同じ 0.15 で足切りするが、掛ける**場所が違う**（下位3車を採った"後"で
   削るだけ）。7C のように5車全体からの選抜に使うと、狙う帯そのものが消える。

🔴 **公式印が取れないレースは買わない**（fail-closed）。他ランクは情報欠損を
   fail-open にしているが、7M1 は「印と割れていること」自体がエッジの本体なので、
   確認できない以上は降りる。fail-open にすると印の取得が壊れた日だけ
   母集団が約3倍に膨らみ、別の商品になる。

⚠️ 7C/9C と同じく他ランクと**論理的に排他ではない**。picks_history の race_key は
   `{レースキー}#7M1` なので他ランク行と共存する。入稿だけが優先順位で1本に絞る。

⚠️ 本番モデル `lgbm_wt_eval` は full_refit でホールドアウト無しのため、
   過去へ遡って使うと in-sample になる。walk-forward 再構築では
   月次vintage（`lgbm_wt_eval_mYYMM`）を渡すこと。

⚠️ 2026-08-19 から **`--win-model` が要る**（堅い帯の取り込み・`RANK_7M1_FIRM_BAND`）。
   7M1 が拾うのは「7C が見送るレース」で、7C の受理判定は券種切替
   （`rank_7c_use_trifecta`）を通るため win モデルが要る。省くと堅い帯は
   fail-closed で0件になる（黙って母集団が狭くなるので必ず渡すこと）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_7m1_rank_wt.py \
        --start 2025-01-01 --end 2026-08-16 \
        --model lgbm_wt_eval_m2608 --win-model lgbm_wt_win_m2608 [--wipe] [--dry-run]
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
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.evaluation.void_rules import void_by_dns  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.odds_prediction import (  # noqa: E402
    model_train_end as odds_model_train_end, trio_ev_for_legs,
)
from src.rebuild_stakes import load_morning_boards, stakes_for_combos  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7M1_LEGS_MIN, rank_7c_buy_plan, rank_7c_is_lowpay_pattern,
    rank_7c_select_axis, rank_7c_select_legs, rank_7m1_daily_select,
    rank_7m1_select_legs, rank_7s_wt_overlap_n,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402
from src.result_top3 import hit_trio, representative, winning_trios

N_CAR = 7


_EV_WARNED = False


def _ev_for(race_key: str, axis1: int, axis2: int, others: list[int],
            race_date: str) -> dict[int, float] | None:
    """EV（予測オッズ × 3着内確率）。**honest な日付のときだけ**返す。

    🔴 三連複オッズモデルを**学習終端以前の日付へ当てると in-sample**になる。
       過去分の再構築でそれをやると picks_history の 7M1 が「未来を知っていた
       買い方」で埋まり、以後の評価が全部そこに引きずられる
       （[[keirin_n7_gami_cut_predicted_odds_2026_08_21]] で実際に踏んだ型）。
    ⚠️ honest でない日付は **None を返して従来規則（下位3車）へ落とす**。
       止めない理由は、止めると tail 再構築ごと失敗して当日の行が消えるため。
       代わりに一度だけ警告を出す。
    """
    global _EV_WARNED
    # 🔴 `model_train_end()` は**メタが無いと例外を投げる**（`load_meta`）。
    #    keirin/data はリポジトリ管理外なので、モデル未配備の環境では必ずここを通る。
    #    ここで素通しにすると **tail 再構築ごと落ちて当日の行が消える**
    #    （CI で実際に落ちて発覚・2026-08-21）。読めなければ EV を使わないだけにする。
    try:
        end = odds_model_train_end()
    except Exception:
        end = None
        if not _EV_WARNED:
            print("[backfill_7m1] オッズモデルのメタを読めないため EV を使わず"
                  "従来規則（下位3車）で再構築します", flush=True)
            _EV_WARNED = True
        return None
    if not race_date or (end and race_date <= str(end)):
        if not _EV_WARNED:
            print(f"[backfill_7m1] {race_date} はオッズモデルの学習終端 {end} 以前"
                  "のため EV を使わず従来規則（下位3車）で再構築します", flush=True)
            _EV_WARNED = True
        return None
    return trio_ev_for_legs(race_key, axis1, axis2, others)


def build_rows(model_name: str, date_from: str, date_to: str,
               win_model_name: str | None = None,
               bad_model_name: str | None = None) -> list[dict]:
    """バックフィル対象の 7M1(#7M1) 行（採点済み）を構築する。

    win_model_name: **堅い帯の取り込み（`RANK_7M1_FIRM_BAND`）に必要**。
      7M1 が拾うのは「7C が見送るレース」なので、7C の受理判定
      （`rank_7c_accepts` → `rank_7c_buy_plan` → `rank_7c_use_trifecta`）を
      同じ入力で再現する必要がある。🔴 **渡さないと母集団が約19%膨らむ**
      （7C が実際に買うレースまで拾ってしまう）。渡さない場合は堅い帯を
      諦める（`_FIRM_BAND_REQUIRED_KEYS` の fail-closed で0件になる）。
    bad_model_name: **7M1 では使わない**。rebuild 側の共通ヘルパと signature を
      揃えるためだけに受け取る。
    """
    model = load_model(model_name)
    win_model = load_model(win_model_name) if win_model_name else None
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
        marks: dict[str, dict[int, int]] = {}
        for i in range(0, len(rksN), 900):
            chunk = rksN[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order, prediction_mark "
                 "FROM wt_entries WHERE race_key IN (%s)" % ",".join("?" * len(chunk)))
            for rk, fno, fo, pm_ in c.execute(q, chunk):
                if fo is not None and fo >= 1:
                    fins.setdefault(rk, []).append((fo, int(fno)))
                if pm_ is not None:
                    marks.setdefault(rk, {})[int(pm_)] = int(fno)
    df = df[df["race_key"].isin(set(rksN))].copy()
    if df.empty:
        return []
    X = prepare_X(df)
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    if win_model is not None:
        df["pred_win"] = win_model.predict_proba(X)[:, 1]
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
        sel = rank_7c_select_axis(top3_probs)
        if sel is None:
            continue
        axis1, axis2, p3_sum = sel

        thirds_full = sorted(set(top3_probs) - {axis1, axis2})
        skip_race, others = void_by_dns(axis1, axis2, thirds_full, board)
        if skip_race:
            continue

        mk = marks.get(rk, {})
        # --- 7C の受理判定を live（src/cli/main.py）と同じ入力で再現する ---
        #     堅い帯を拾うかどうかは「7C が見送るか」で決まるので、ここは
        #     7C のバックフィルと同じ組み立てにする（片方だけ直すと母集団がずれる）。
        others_7c = sorted(set(top3_probs) - {axis1, axis2})
        legs_7c = rank_7c_select_legs(others_7c, top3_probs)
        line_groups = {int(r.frame_no): getattr(r, "line_group", None)
                       for r in g.itertuples(index=False)}
        win_probs = ({int(r.frame_no): float(r.pred_win)
                      for r in g.itertuples(index=False)}
                     if win_model is not None else None)
        plan_7c = rank_7c_buy_plan(top3_probs, win_probs, axis1, legs_7c,
                                   wt_ana=mk.get(4))
        candidates.append({
            "race_key": rk, "race_date": date_map.get(rk, ""),
            "n_entries": N_CAR,
            "axis1": axis1, "axis2": axis2,
            "p3_sum_top2": p3_sum,
            "p3_sum_top2_cal": calibrated_p3_sum_top2(
                top3_probs, race_type_map.get(rk), cup_grade_map.get(rk)),
            # 🔴 印の重なりは **7C の軸**（pred_top3 上位2車）で測る。
            #    3ヘッド軸の `wt_overlap_n` を使うと母集団が約2割ずれる。
            "wt_overlap_7c_n": rank_7s_wt_overlap_n(
                axis1, axis2, mk.get(1), mk.get(2)),
            # 堅い帯の取り込み（RANK_7M1_FIRM_BAND）に必要。◎が軸に居るか。
            "wt_honmei_in_axis_7c": (
                (mk.get(1) in (axis1, axis2)) if mk.get(1) is not None else None),
            # 🔴 以下4つは `rank_7c_accepts`（7C が買うか）の入力。
            #    `_FIRM_BAND_REQUIRED_KEYS` と対応しており、欠けると堅い帯を
            #    拾わなくなる（fail-closed）。win_model 未指定なら
            #    `legs_7c_buy` は三連単切替なしの判定になる点に注意。
            "legs_7c": legs_7c,
            "legs_7c_buy": (plan_7c[1] if plan_7c else None),
            "bet_kind_7c": (plan_7c[0] if plan_7c else None),
            "lowpay_pattern": rank_7c_is_lowpay_pattern(top3_probs, line_groups),
            "axis1_p3": top3_probs.get(axis1),
            # 相手は盤面に残った車から採る（朝の候補をそのまま使わない）。
            # 🔴 **live（`cli/main.py`）と同じ EV 順を通すこと**（2026-08-21）。
            #    片方だけ EV にすると、毎朝の tail 再構築で picks_history が
            #    旧規則（下位3車）へ**巻き戻る**。7C が 2026-08-15 に実際に踏んだ型で、
            #    そのときは「入稿と記録が84件中17件で食い違う」実害になった
            #    （本ファイル冒頭ではなく `backfill_7c_rank_wt.py` の冒頭コメント参照）。
            # ⚠️ 予測オッズモデルは学習終端より前の日付に当てると in-sample。
            #    `_ev_for` が honest な日付のときだけ EV を返す。
            "legs_7m1": rank_7m1_select_legs(
                others, top3_probs, ev=_ev_for(rk, axis1, axis2, others, date_map.get(rk, ""))),
            "cup_grade": cup_grade_map.get(rk),
            "trio": trio,
            # 🔴 同着では当たり目が2通りになる（`src/result_top3` が正本）。
            "actual_top3": representative(winning_trios(fin)),
            "wins": winning_trios(fin),
            "top3_probs": top3_probs,
        })

    morning_boards = load_morning_boards([c["race_key"] for c in candidates])
    rows: list[dict] = []
    for c_ in rank_7m1_daily_select(candidates):
        axis1, axis2 = c_["axis1"], c_["axis2"]
        trio = c_["trio"]
        combos, bought = [], []
        for x in c_["legs_7m1"]:
            key = frozenset({axis1, axis2, x})
            if key in trio:
                combos.append(key)
                bought.append(x)
        # オッズ欠けで点数を割ったら買わない（live の judge_rank_7m1 と同一規約）。
        if len(combos) < RANK_7M1_LEGS_MIN:
            continue
        rk = c_["race_key"]
        # 同着では当たり目が複数ある。**買った目**で払戻を引く。
        win_key = hit_trio(combos, c_["wins"])
        hit = win_key is not None
        trio_pay = pm.get(rk, {}).get(("trio", win_key or c_["actual_top3"]), 0)
        stakes = stakes_for_combos(axis1, axis2, combos, c_.get("top3_probs") or {},
                                   morning_boards.get(rk))
        pay = trio_pay * stakes[win_key] // 100 if hit else 0
        rows.append({
            "race_date": c_["race_date"],
            "race_key": f"{rk}#7M1", "rank": "RANK_7M1",
            "pred_combo": f"{axis1}={axis2}-" + ",".join(str(x) for x in bought),
            "n_combos": len(combos), "hit": int(hit), "payout": pay,
            "trio_payout": trio_pay, "bet_amount": sum(stakes.values()),
            "gate_label": None,
        })
    return rows


def wipe_rows(date_from: str, date_to: str, dry_run: bool) -> None:
    cond = "rank='RANK_7M1' AND race_key LIKE '%#7M1' AND race_date BETWEEN ? AND ?"
    with get_connection() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM picks_history WHERE {cond}",
                         (date_from, date_to)).fetchone()[0]
        print(f"[backfill-7m1] 既存 #7M1 行（{date_from}〜{date_to}）: {n}件 → 削除"
              f"{'（dry-run）' if dry_run else ''}")
        if not dry_run and n:
            conn.execute(f"DELETE FROM picks_history WHERE {cond}", (date_from, date_to))
            conn.commit()

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        return
    import psycopg2
    cond_pg = "rank='RANK_7M1' AND race_key LIKE %s AND race_date BETWEEN %s AND %s"
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM keirin.picks_history WHERE {cond_pg}",
                        ("%#7M1", date_from, date_to))
            n = cur.fetchone()[0]
            print(f"[backfill-7m1] VPS PG 既存 #7M1 行: {n}件 → 削除"
                  f"{'（dry-run）' if dry_run else ''}")
            if not dry_run and n:
                cur.execute(f"DELETE FROM keirin.picks_history WHERE {cond_pg}",
                            ("%#7M1", date_from, date_to))


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
    print(f"[backfill-7m1] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-7m1] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
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
    print(f"[backfill-7m1] VPS PG {len(rows)}件 書き込み完了")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", required=False)
    ap.add_argument("--model", default="lgbm_wt_eval")
    # 🔴 堅い帯（RANK_7M1_FIRM_BAND）の再現に必要。省くと母集団が狭くなる。
    ap.add_argument("--win-model", default="lgbm_wt_win")
    ap.add_argument("--wipe", action="store_true",
                    help="書き込み前に対象期間の既存 #7M1 行を削除")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.end:
        # 🔴 win モデルも版の対象。堅い帯の母集団（7C が見送るか）が
        #    これで決まるので、eval だけ vintage にしても片肺になる。
        assert_vintage_for_past(args.end, {"eval": args.model,
                                           "win": args.win_model})

    from datetime import date
    end = args.end or date.today().strftime("%Y-%m-%d")
    print(f"[backfill-7m1] model={args.model} {args.start}〜{end}", flush=True)

    if args.wipe:
        wipe_rows(args.start, end, args.dry_run)

    rows = build_rows(args.model, args.start, end, args.win_model)
    n_hit = sum(r["hit"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    pay = sum(r["payout"] for r in rows)
    print(f"[backfill-7m1] {len(rows)}R 的中{n_hit} 投資{bet:,} 回収{pay:,} "
          f"ROI {100 * pay / bet if bet else 0:.1f}%", flush=True)
    insert_rows(rows, args.dry_run)


if __name__ == "__main__":
    main()
