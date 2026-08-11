#!/usr/bin/env python3
"""RANK_7H3（穴推奨・本命連対どまり型／三連単の高配当）の候補を生成する。

## 位置づけ

7H3 は「指数上位2車は3着以内に来るが、勝ち切るのは別の車」という決着だけを買う。
軸2車を **2着・3着**に置き、1着は相手に任せる三連単フォーメーション。

既存の的中率商品（三連複・軸2車流し／看板+準決勝）とは **母集団が排他**で、
7H3 は **看板でも準決勝でもないレース**（予選・一般・チャレンジ・ガールズ等）
だけを対象にする。市場の歪みがそこにしか無いため（設計と実測は
`src/strategy_wt.py` の RANK_7H3 セクション / `docs/rank_7h3_design.md`）。

7H1/7H2 のようなレース単位の学習モデルは**使わない**。既存の3着内率
（`lgbm_wt_eval`）と1着率（`lgbm_wt_win`）だけで決まる。

## 処理

1. 対象日の7車レースと出走表を DB から取得
2. 本番モデル（`lgbm_wt_eval` / `_win`）で選手単位の3着内率・1着率を作る
3. `strategy_wt.rank_7h3_formation()` / `rank_7h3_build_legs()` で買い目
4. `strategy_wt.rank_7h3_stakes()` で Plackett-Luce 配分の賭け金
5. `strategy_wt.rank_7h3_daily_select()` で絶対閾値（軸積 >= 0.70）により選別

出力する候補JSONの形式と保存先は既存ランクに合わせてあるので、
`notify_prerace_wt.py` / `netkeirin_submit_wt.py` からは同じように読める。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/build_7h3_candidates.py \\
        --date 2026-08-12 [--out data/picks/wave_picks_wt_2026-08-12_s7h3_candidates.json]
    # 過去日を honest に作る場合は月次vintageを明示する
    #   --eval-model lgbm_wt_eval_m2608 --win-model lgbm_wt_win_m2608

⚠️ 過去分の再構築で本番モデル（全期間学習）を使うと in-sample になる。
   必ず vintage を指定すること（`assert_vintage_for_past` が落とす）。

⚠️ `RANK_7H3_AXIS_PRODUCT_MIN` は本番モデルの較正に合わせた絶対閾値。
   vintage モデルで過去分を作ると該当率がずれうる。

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7H3_AXIS_PRODUCT_MIN, RANK_7H3_NE, rank_7h3_axis, rank_7h3_axis_product,
    rank_7h3_build_legs, rank_7h3_daily_select, rank_7h3_formation, rank_7h3_legs,
    rank_7h3_stakes,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402


def _load_range(date_from: str, date_to: str) -> dict[str, list[dict]]:
    """対象期間の7車レースの出走表を race_key ごとに返す。"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT e.race_key, r.race_date, r.venue_id, r.race_no, "
            "       r.n_entries, r.grade, r.race_type, r.day_index, r.distance, "
            "       r.start_at, e.frame_no, e.name, e.line_group, e.line_pos, "
            "       v.name AS venue_name "
            "FROM wt_entries e "
            "JOIN wt_races r USING(race_key) "
            "LEFT JOIN venue_info v ON v.venue_code = r.venue_id "
            "WHERE r.cancel=0 AND r.n_entries=? AND r.race_date BETWEEN ? AND ?",
            (RANK_7H3_NE, date_from, date_to))
        by_race: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            by_race[e["race_key"]].append(dict(e))
    return by_race


def _predict(date_from: str, date_to: str, eval_model: str, win_model: str
             ) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, float]]]:
    """(3着内率, 1着率) を race_key → {車番: 確率} で返す。"""
    feats = build_features_wt(load_raw_data_wt(min_date=date_from, max_date=date_to))
    p3: dict[str, dict[int, float]] = {}
    pw: dict[str, dict[int, float]] = {}
    if feats is None or not len(feats):
        return p3, pw
    X = prepare_X(feats)
    # ⚠️ どちらも sklearn API（`predict_proba`）。9H1 の波乱スコアだけが
    #    LightGBM Booster（`predict`）なので、あちらのコードを写さないこと。
    p3v = load_model(eval_model).predict_proba(X)[:, 1]
    pwv = load_model(win_model).predict_proba(X)[:, 1]
    for rk, fn, a, b in zip(feats["race_key"], feats["frame_no"], p3v, pwv):
        p3.setdefault(rk, {})[int(fn)] = float(a)
        pw.setdefault(rk, {})[int(fn)] = float(b)
    return p3, pw


def build(date_from: str, date_to: str, eval_model: str, win_model: str) -> list[dict]:
    """対象期間の 7H3 候補（選別後）を返す。当日生成もバックフィルもこれを使う。"""
    by_race = _load_range(date_from, date_to)
    if not by_race:
        print(f"{date_from}〜{date_to}: 7車立てのレースがありません")
        return []
    print(f"{date_from}〜{date_to}: 7車立て {len(by_race)}R")
    p3_all, pw_all = _predict(date_from, date_to, eval_model, win_model)

    cands = []
    for rk, ents in by_race.items():
        probs = p3_all.get(rk)
        if not probs or len(probs) != RANK_7H3_NE:
            print(f"  {rk}: 3着内率が {len(probs or {})}/{RANK_7H3_NE} 件しか無く skip")
            continue
        axis = rank_7h3_axis(probs)
        legs_str = rank_7h3_build_legs(probs)
        if axis is None or not legs_str:
            continue
        first, second, third = rank_7h3_formation(probs)
        stakes = rank_7h3_stakes(legs_str, pw_all.get(rk))
        name_of = {int(e["frame_no"]): e.get("name") for e in ents}
        order = [f for f, _ in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))]
        cands.append({
            "race_key": rk, "race_date": ents[0]["race_date"],
            "venue_name": ents[0].get("venue_name"), "race_no": ents[0].get("race_no"),
            "start_time": ents[0].get("start_at"),
            "race_type": ents[0].get("race_type"),
            "n_entries": RANK_7H3_NE,
            "axis_product": round(rank_7h3_axis_product(probs) or 0.0, 6),
            "order": order,                      # 3着内率の降順（印の割当に使う）
            "axis1": axis[0], "axis2": axis[1],
            "axis1_name": name_of.get(axis[0]), "axis2_name": name_of.get(axis[1]),
            "partners": rank_7h3_legs(probs),    # 1着に置く相手
            # 三連単フォーメーションの3列。**netkeirin へはこれをそのまま送る**。
            "formation_first": first, "formation_second": second,
            "formation_third": third,
            # 展開済みの買い目と賭け金（picks_history と入稿の両方がこれを正とする）。
            "legs": legs_str,
            "stakes": stakes,
            "bet_amount": sum(stakes.values()),
        })

    picked = rank_7h3_daily_select(cands)
    print(f"軸積 >= {RANK_7H3_AXIS_PRODUCT_MIN:.2f} かつ看板外で "
          f"{len(picked)}/{len(cands)}R を採用")
    for c in picked:
        print(f"  {c['race_key']} {c.get('venue_name')}{c.get('race_no')}R "
              f"[{c.get('race_type')}] 軸積={c['axis_product']:.3f} "
              f"軸={c['axis1']}-{c['axis2']} 相手={c['partners']} "
              f"{len(c['legs'])}点 計{c['bet_amount']:,}円")
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    ap.add_argument("--out", default=None)
    ap.add_argument("--eval-model", default="lgbm_wt_eval", help="3着内率モデル")
    ap.add_argument("--win-model", default="lgbm_wt_win", help="1着率モデル（配分に使う）")
    args = ap.parse_args()

    # 🔴 過去日を本番モデル（全期間学習）でスコアすると model-vintage look-ahead に
    #    なる。既定値が本番モデル名なので、指定を忘れると**無言で**そうなる。
    assert_vintage_for_past(
        args.date, {"eval": args.eval_model, "win": args.win_model})

    cands = build(args.date, args.date, args.eval_model, args.win_model)
    # 既存ランクと同じ data/picks/ 配下へ出す（notify_prerace_wt.py が読む場所）
    path = Path(args.out) if args.out else (
        REPO / "data" / "picks" / f"wave_picks_wt_{args.date}_s7h3_candidates.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")   # 書き込み途中を読まれないよう原子的に置く
    tmp.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"[保存先] {path}  (7H3候補 {len(cands)}件)")


if __name__ == "__main__":
    main()
