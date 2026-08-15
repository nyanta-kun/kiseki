#!/usr/bin/env python3
"""7C(RANK_7C) の過去分バックフィル（ベースモデル・終日の二軸）。

backfill_7c_rank_wt.py の 7C 版。7SS/7S/7A と違う点は3つ:

  1. **軸が3ヘッドではない** — モデル3着内率(pred_prob)の上位2車。
     bad モデルは使わない（win モデルは券種の切替判定にだけ使う・下記）。
  2. **総流しではない** — 相手は3着内率 >= RANK_7C_LEG_P3_MIN に足切り。
     足切り後が RANK_7C_LEGS_MIN 点未満のレースは**買わない**
     （「相手が絞れる＝実力差が大きい＝配当が付かない」ため）。
  3. **賭け金が可変** — 1レース RACE_BUDGET 円を点数で均等割り。

🔴 **買い方は `strategy_wt.rank_7c_buy_plan` を通すこと**（2026-08-15 是正）。
   2026-08-09 に本番へ入った3仕様——(a) 三連単への切替
   （`rank_7c_use_trifecta`）・(b) 三連複側の追加ゲート
   （`RANK_7C_TRIO_P3_SUM_MIN`）・(c) 落差カット（`rank_7c_cut_legs_by_gap`）——が
   **本スクリプトにだけ入っておらず**、`reconcile_walkforward_tail.sh` が毎朝
   当月を作り直すたびに picks_history が 08-09 以前の買い方へ巻き戻っていた。

   実害（2026-08-07〜08-14 実測）: picks_history の 7C 購入行 161件は
   **160件が4〜5点・三連単0件**。同期間の入稿は 1点8/2点8/3点2/4点45/5点29・
   三連単5件。入稿と突き合わせると **84件中17件（20%）で点数が食い違い**、
   うち16件は「1〜2点で売ったのに4〜5点として記録」だった。
   ＝ **Web に出ている 7C の実績が、実際に売っている商品を説明していなかった。**

⚠️ 7C は他ランクと**論理的に排他ではない**（wt_overlap_n を見ない）。
   picks_history の race_key は `{レースキー}#7C` なので他ランク行とは共存する。
   1レース1商品の制約は netkeirin 入稿側だけで解決している。

⚠️ 本番モデル `lgbm_wt_eval` は full_refit でホールドアウト無しのため、
   過去へ遡って使うと in-sample になる。walk-forward 再構築では
   月次vintage（`lgbm_wt_eval_mYYMM`）を渡すこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_7c_rank_wt.py \
        --start 2024-01-01 --end 2026-08-07 [--model lgbm_wt_eval] \
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

from src.wt_vintage_config import assert_vintage_for_past
from src.database import get_connection
from src.evaluation.backtest_wt import _load_payouts_wt
from src.rebuild_stakes import load_morning_boards, stakes_for_combos
from src.evaluation.void_rules import void_by_dns
from src.models.trainer import load_model
from src.preprocessing.feature_wt import build_features_wt, load_raw_data_wt, prepare_X
from src.strategy_wt import (
    RANK_7C_LEGS_MIN, unit_stake, rank_7c_buy_plan, rank_7c_daily_select,
    rank_7c_is_lowpay_pattern, rank_7c_select_axis, rank_7c_select_legs,
    rank_7s_field_entropy,
)

N_CAR = 7


def _load_trio_boards(race_keys: list[str]) -> dict:
    """具体的コンボの購入可否判定用（odds_value 有効値のみ）。

    欠車判定（void_by_dns）には使わない。欠車判定用の盤面掲載車集合は
    `_load_board_frames_wt()`（odds_value フィルタなし）を使うこと。
    """
    trio = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(race_keys), 900):
            chunk = race_keys[i:i + 900]
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 "WHERE bet_type = 'trio' AND race_key IN (%s)"
                 % ",".join("?" * len(chunk)))
            for rk, comb, od in c.execute(q, chunk):
                try:
                    fv = float(od) if od is not None else None
                except (TypeError, ValueError):
                    continue
                if fv is None or fv <= 0:
                    continue
                try:
                    parts = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
                except ValueError:
                    continue
                if len(parts) == 3:
                    trio[rk][parts] = fv
    return trio


def _load_board_frames_wt(race_keys: list[str]) -> dict[str, set[int]]:
    """欠車判定用の盤面掲載車集合を返す（notify_results_wt._board_frames /
    src.evaluation.backtest_wt._load_board_frames_wt と同一の構築方法）。

    bet_type='trio' の combination に現れる車番の和集合。odds_value による
    フィルタは行わない（未確定・異常値でも盤面に車番として存在していれば
    「実際に購入できた車」とみなす本番の判定基準に合わせるため）。
    """
    board_map: dict[str, set[int]] = defaultdict(set)
    if not race_keys:
        return board_map
    with get_connection() as c:
        for i in range(0, len(race_keys), 900):
            chunk = race_keys[i:i + 900]
            q = ("SELECT race_key, combination FROM wt_odds "
                 "WHERE bet_type = 'trio' AND race_key IN (%s)"
                 % ",".join("?" * len(chunk)))
            for rk, comb in c.execute(q, chunk):
                for part in re.split(r"[-=]", str(comb)):
                    try:
                        board_map[rk].add(int(part))
                    except ValueError:
                        pass
    return board_map


def build_rows(model_name: str, date_from: str, date_to: str,
                win_model_name: str | None = None,
                bad_model_name: str | None = None) -> list[dict]:
    """バックフィル対象の 7C(#7C) 行（採点済み）を構築する。

    win_model_name: **券種の切替判定にだけ使う**（`rank_7c_use_trifecta`＝
      単勝率が RANK_7C_TRIFECTA_PW_MIN 以上の1車がいるレースは三連単へ）。
      🔴 **None を渡すと三連単へ切り替わらず、本番と別の商品を再構築する**。
      2026-08-15 まで実際にそうなっていた（本 docstring 冒頭の実害を参照）。
    bad_model_name: 7C では使わない（軸が3ヘッドではないため）。rebuild 側の
      共通ヘルパと signature を揃えるために受け取り、渡されても無視する。
    """
    model = load_model(model_name)
    # 券種の切替（三連単）判定用。無ければ全レース三連複になる＝本番と食い違う。
    win_model = load_model(win_model_name) if win_model_name else None
    if win_model is None:
        print("[backfill-7c] ⚠️ win モデル未指定のため三連単への切替を行いません"
              "（本番と別の商品になります）", flush=True)
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
        rksN = [rk for rk, ne in ne_map.items() if ne and int(ne) == N_CAR]
        fins: dict[str, list[tuple[int, int]]] = {}
        marks: dict[str, dict[int, int]] = {}
        for i in range(0, len(rksN), 900):
            chunk = rksN[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order, prediction_mark FROM wt_entries "
                 "WHERE race_key IN (%s)" % ",".join("?" * len(chunk)))
            for rk, fno, fo, pmv in c.execute(q, chunk):
                if fo is not None and fo >= 1:
                    fins.setdefault(rk, []).append((fo, int(fno)))
                if pmv is not None:
                    marks.setdefault(rk, {})[int(fno)] = int(pmv)
    df = df[df["race_key"].isin(set(rksN))].copy()
    if df.empty:
        return []
    X = prepare_X(df)
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    df["pred_win"] = (win_model.predict_proba(X)[:, 1] if win_model is not None
                      else 0.0)
    trio_bd = _load_trio_boards(df["race_key"].unique().tolist())
    board_map = _load_board_frames_wt(df["race_key"].unique().tolist())
    pm = _load_payouts_wt(df["race_key"].unique().tolist())

    candidates: list[dict] = []
    for rk, g in df.groupby("race_key"):
        if ne_map.get(rk) != N_CAR or len(g) != N_CAR:
            continue
        trio = trio_bd.get(rk)
        if not trio:
            continue
        board = board_map.get(rk)
        if not board:
            continue
        fin = sorted(fins.get(rk, []))
        if len(fin) < 3:
            continue

        top3_probs = {int(r.frame_no): float(r.pred_prob) for r in g.itertuples(index=False)}
        win_probs = {int(r.frame_no): float(r.pred_win) for r in g.itertuples(index=False)}
        # 7C の軸は **pred_prob 上位2車**（3ヘッド軸ではない）。live（src/cli/main.py）と同一。
        sel = rank_7c_select_axis(top3_probs)
        if sel is None:
            continue
        axis1, axis2, p3_sum = sel

        # 欠車判定は本番と同一の void_by_dns。軸欠車=レース無効／相手欠車=その目のみ除外。
        thirds_full = sorted(set(top3_probs.keys()) - {axis1, axis2})
        skip_race, others = void_by_dns(axis1, axis2, thirds_full, board)
        if skip_race:
            continue

        # 相手は盤面に残った車から足切りする（朝の候補をそのまま使わない）。
        # 足切り後が RANK_7C_LEGS_MIN 点未満なら **買わない**（低配当回避の本体）。
        legs = rank_7c_select_legs(others, top3_probs)

        order3 = tuple(fno for _, fno in fin[:3])
        actual_top3 = frozenset(order3)

        # 低配当パターン（上位3車が抜けている ∧ その3車が同一ライン）は見送る。
        # live（src/cli/main.py）と同じく特徴量DFの line_group をそのまま渡す。
        line_groups = {int(r.frame_no): getattr(r, "line_group", None)
                       for r in g.itertuples(index=False)}

        # 🔴 WT△（prediction_mark==3）は案E（総流し帯から△を外す）の発動条件。
        #    live（src/cli/main.py）は候補JSONの `wt_ana` を渡すので、再構築でも
        #    同じ値を作る。**ここが欠けると再構築だけ旧挙動に戻り**、Web の実績が
        #    実際に売った商品を説明しなくなる。
        _marks = marks.get(rk, {})
        wt_ana = next((f for f, v in _marks.items() if v == 3), None)

        candidates.append({
            "race_key": rk, "race_date": date_map.get(rk, ""),
            "wt_ana": wt_ana,
            "axis1": axis1, "axis2": axis2,
            "p3_sum_top2": p3_sum, "legs_7c": legs, "win_probs": win_probs,
            "order3": order3,
            "lowpay_pattern": rank_7c_is_lowpay_pattern(top3_probs, line_groups),
            "entropy": rank_7s_field_entropy(top3_probs),
            "trio": trio, "actual_top3": actual_top3,
            "top3_probs": top3_probs,
        })

    # 朝オッズ盤面は 2026-06-08 以降にしか無い。無い期間は p3 単独へ落ちる。
    morning_boards = load_morning_boards([c["race_key"] for c in candidates])
    rows: list[dict] = []
    for c_ in rank_7c_daily_select(candidates):
        axis1, axis2 = c_["axis1"], c_["axis2"]
        trio = c_["trio"]
        rk = c_["race_key"]
        # 🔴 **買い方は本番と同じ単一正本を通す**（券種の切替・三連複側の追加
        #    ゲート・落差カットがここで一括して決まる）。2026-08-15 まで本
        #    スクリプトだけがこれを通さず `legs_7c` を総流ししていた。
        plan = rank_7c_buy_plan(c_.get("top3_probs") or {}, c_.get("win_probs") or {},
                                axis1, c_["legs_7c"], wt_ana=c_.get("wt_ana"))
        if plan is None:
            continue                       # 三連複側のゲートを下回る＝見送り
        bet_kind, buy_legs = plan

        if bet_kind == "trifecta":
            # 三連単「1着=軸1 / 2着=軸2 / 3着=相手」。**着順まで一致**で的中。
            # ⚠️ 点数ゲートは**三連複の板**で行う（live の judge_rank_7c と同一）。
            #    三連単の板は薄く、そちらで足切りすると 7C の 16.9% が
            #    「オッズ取得できた目がN点」で黙って見送りになる。
            playable = [x for x in buy_legs if frozenset({axis1, axis2, x}) in trio]
            if len(playable) < RANK_7C_LEGS_MIN:
                continue
            order3 = c_["order3"]
            hit = tuple(order3) in {(axis1, axis2, x) for x in playable}
            tri_pay = pm.get(rk, {}).get(("trifecta", tuple(order3)), 0)
            # 三連単は**均等割り**（live の `rank_7c_unit_stake` と同じ）。
            # 傾斜配分は三連複の目に対して組んであるので流用しない。
            unit = unit_stake(len(playable))
            bet = unit * len(playable)
            pay = tri_pay * unit // 100 if hit else 0
            rows.append({
                "race_date": c_["race_date"],
                "race_key": f"{rk}#7C", "rank": "RANK_7C",
                # 🔴 `三単:` と着順の `-` 表記は**券種を伝える唯一の手段**
                #    （notify_prerace_wt の pred_combo と完全に同じ形式にすること）。
                "pred_combo": (f"三単:{axis1}-{axis2}-"
                               + ",".join(str(x) for x in playable)),
                "n_combos": len(playable), "hit": int(hit), "payout": pay,
                "trio_payout": 0, "trifecta_payout": tri_pay,
                "bet_amount": bet, "gate_label": None,
            })
            continue

        # combos/bought_thirds を同期して構築（pred_combo は実際に買った目のみ）。
        combos, bought_thirds = [], []
        for x in buy_legs:
            key = frozenset({axis1, axis2, x})
            if key in trio:
                combos.append(key)
                bought_thirds.append(x)
        # 🔴 必要点数は**買う点数**で判定する。`RANK_7C_LEGS_MIN`(=4) は選別の
        #    閾値で、落差カット後（1〜5点）へ流用すると常に見送りになる
        #    （live の judge_rank_7c が同じ理由で `len(legs)` を使っている）。
        if len(combos) < len(buy_legs):
            continue
        hit = c_["actual_top3"] in combos
        trio_pay = pm.get(rk, {}).get(("trio", c_["actual_top3"]), 0)
        # 賭け金は1レース RACE_BUDGET 円を**入稿と同じ傾斜配分**で割り振る
        # （2026-08-07・均等割りから変更）。最終オッズで配分すると先読みになり
        # 本番より 14.5pt 高く出るので、必ず「朝オッズ×p3、無ければ p3 単独」の
        # 本番と同じ規則を使う（src/rebuild_stakes.py の docstring 参照）。
        stakes = stakes_for_combos(axis1, axis2, combos, c_.get("top3_probs") or {},
                                   morning_boards.get(rk))
        pay = trio_pay * stakes[c_["actual_top3"]] // 100 if hit else 0
        bet = sum(stakes.values())
        rows.append({
            "race_date": c_["race_date"],
            "race_key": f"{rk}#7C", "rank": "RANK_7C",
            "pred_combo": f"{axis1}={axis2}-" + ",".join(str(x) for x in bought_thirds),
            "n_combos": len(combos), "hit": int(hit), "payout": pay,
            "trio_payout": trio_pay, "bet_amount": bet, "gate_label": None,
        })
    return rows


def wipe_rows(date_from: str, date_to: str, dry_run: bool) -> None:
    cond = "rank='RANK_7C' AND race_key LIKE '%#7C' AND race_date BETWEEN ? AND ?"
    with get_connection() as conn:
        n = conn.execute(
            f"SELECT COUNT(*) FROM picks_history WHERE {cond}",
            (date_from, date_to)).fetchone()[0]
        print(f"[backfill-7c] 既存 #7C 行（{date_from}〜{date_to}）: {n}件 → 削除"
              f"{'（dry-run）' if dry_run else ''}")
        if not dry_run and n:
            conn.execute(f"DELETE FROM picks_history WHERE {cond}", (date_from, date_to))
            conn.commit()

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        return
    import psycopg2
    cond_pg = "rank='RANK_7C' AND race_key LIKE %s AND race_date BETWEEN %s AND %s"
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM keirin.picks_history WHERE {cond_pg}",
                        ("%#7C", date_from, date_to))
            n = cur.fetchone()[0]
            print(f"[backfill-7c] VPS PG 既存 #7C 行: {n}件 → 削除{'（dry-run）' if dry_run else ''}")
            if not dry_run and n:
                cur.execute(f"DELETE FROM keirin.picks_history WHERE {cond_pg}",
                            ("%#7C", date_from, date_to))


def insert_rows(rows: list[dict], dry_run: bool) -> None:
    if dry_run or not rows:
        return
    # ⚠️ 三連単へ切り替えたレースだけ `trifecta_payout` を持つ。列は全行に
    #    必要なので、持たない行は 0 で補う（欠けると executemany が落ちる）。
    rows = [{"trifecta_payout": 0, **r} for r in rows]
    rows_ins = [{**r, "miwokuri": False} for r in rows]
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO picks_history "
            "(race_date,race_key,rank,pred_combo,n_combos,hit,payout,"
            " trio_payout,trifecta_payout,bet_amount,route,miwokuri,gate_label) "
            "VALUES (:race_date,:race_key,:rank,:pred_combo,:n_combos,:hit,"
            " :payout,:trio_payout,:trifecta_payout,:bet_amount,'wt',:miwokuri,"
            " :gate_label)",
            rows_ins)
        conn.commit()
    print(f"[backfill-7c] get_connection先 {len(rows)}件 書き込み完了")

    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        print("[backfill-7c] KEIRIN_DB_URL 未設定 → VPS PG ミラーはスキップ")
        return
    import psycopg2
    from psycopg2.extras import execute_batch
    with psycopg2.connect(db_url) as pg:
        with pg.cursor() as cur:
            execute_batch(cur, """
                INSERT INTO keirin.picks_history
                  (race_date,race_key,rank,pred_combo,n_combos,hit,payout,
                   trio_payout,trifecta_payout,bet_amount,route,miwokuri,gate_label)
                VALUES (%(race_date)s,%(race_key)s,%(rank)s,%(pred_combo)s,
                        %(n_combos)s,%(hit)s,%(payout)s,%(trio_payout)s,
                        %(trifecta_payout)s,%(bet_amount)s,'wt',FALSE,%(gate_label)s)
                ON CONFLICT (race_key) DO UPDATE SET
                  race_date=EXCLUDED.race_date, rank=EXCLUDED.rank,
                  pred_combo=EXCLUDED.pred_combo, n_combos=EXCLUDED.n_combos,
                  hit=EXCLUDED.hit, payout=EXCLUDED.payout,
                  trio_payout=EXCLUDED.trio_payout,
                  trifecta_payout=EXCLUDED.trifecta_payout,
                  bet_amount=EXCLUDED.bet_amount, miwokuri=FALSE,
                  gate_label=EXCLUDED.gate_label
            """, rows, page_size=200)
    print(f"[backfill-7c] VPS PG {len(rows)}件 書き込み完了")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", required=False)
    ap.add_argument("--model", default="lgbm_wt_eval")
    ap.add_argument("--win-model", default="lgbm_wt_win")
    ap.add_argument("--wipe", action="store_true",
                    help="書き込み前に対象期間の既存 #7C 行を削除")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 過去日に本番モデルを当てると in-sample になるので落とす（2026-08-08）。
    # 既定値が本番モデル名なので、指定を忘れると**無言で**そうなっていた。
    _end = args.end
    if _end:
        assert_vintage_for_past(_end, {"eval": args.model, "win": args.win_model})

    from datetime import date
    end = args.end or date.today().strftime("%Y-%m-%d")
    print(f"[backfill-7c] model={args.model} {args.start}〜{end}", flush=True)

    if args.wipe:
        wipe_rows(args.start, end, args.dry_run)

    rows = build_rows(args.model, args.start, end, args.win_model)
    n = len(rows)
    hits = sum(r["hit"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    ret = sum(r["payout"] for r in rows)
    roi = ret / bet * 100 if bet else 0
    print(f"[backfill-7c] 7C(ベースモデル・終日の二軸): {n}R 的中{hits} ({hits/n*100 if n else 0:.1f}%) "
          f"投資{bet:,} → 回収{ret:,} ROI {roi:.1f}%", flush=True)

    insert_rows(rows, args.dry_run)
    if args.dry_run:
        print("[backfill-7c] DRY RUN（書き込みなし）")


if __name__ == "__main__":
    main()
