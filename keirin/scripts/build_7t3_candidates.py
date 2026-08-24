#!/usr/bin/env python3
"""RANK_7T3（三連単・決勝の中配当枠）の候補を生成する。

## 位置づけ

ユーザー要望「1日3〜5レース狙い、**週2〜3ヒット**」に対応する枠。
🔴 **万車券（100倍以上）では算数的に不可能**——週2.5ヒット ÷（3.5R/日×7日）＝
必要な的中率は約10%だが、100倍以上を5点買ったときの的中は構造的に 2.2% しかない。
対応するのは **30倍以上の帯**（`RANK_7T3_MIN_ODDS`）。

母集団は **決勝（完全一致）× 予測オッズ30倍以上の目がある** 7車立て。
🔴 **ライン条件は持たない**。入稿の優先順位で 7T1（決勝×別ライン）の直後に置く
ことで、結果として同ラインだけを拾う（判定の正本を2箇所に持たないため）。
設計と実測は `docs/rank_7t3_design.md` / `src/strategy_wt.py` の RANK_7T3 セクション。

## 7T1 との違い（同じ三連単だが中身は別物）

| | 7T1 | 7T3 |
|---|---|---|
| 買い方 | 軸2車を1着・2着に**固定**し3着を流す（1〜5点・可変） | 帯の中から**確率上位5点**（軸を置かない） |
| 点数の決め方 | 「払戻15万円に届くか」から自己整合で導く | 固定5点 |
| ライン条件 | 上位2車が別ライン（必須） | 無し |
| ◎○ | 買い目の軸そのもの | `rank_7t3_axes`（1着最多 / ◎除く1-2着最多）＝**軸ではない** |

## 3着内率・1着率に加えて「予測オッズ板」が要る

帯（30倍以上）を切るために全210通りの**予測オッズ**が必要。
`src/odds_prediction_tf.predict_board()` を使う。これは `data/models/odds_tf_n7.txt`
（+ `odds_tf_meta.json`）を読むので、**モデルが配備されていないと候補が0件になる**。
日次バッチで欠品したら気づけるよう `--require-model`（既定ON）で**明示的に落とす**。

## 処理

1. 対象日の7車レースと出走表を DB から取得（ライン・競走得点・級班・脚質も）
2. 本番モデル（`lgbm_wt_eval` / `_win`）で選手単位の3着内率・1着率を作る
3. `odds_prediction_tf.predict_board()` で全210点の予測オッズ
4. `strategy_wt.rank_7t3_select()` で帯の中から確率上位5点
5. `strategy_wt.rank_7t3_stakes()` で**均等**配分の賭け金
6. `strategy_wt.rank_7t3_daily_select()` で決勝に絞る

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/build_7t3_candidates.py \\
        --date 2026-08-24 [--out data/picks/wave_picks_wt_2026-08-24_s7t3_candidates.json]
    # 過去日を honest に作る場合は月次vintageを明示する
    #   --eval-model lgbm_wt_eval_m2608 --win-model lgbm_wt_win_m2608

⚠️ 過去分の再構築で本番モデル（全期間学習）を使うと in-sample になる。
   必ず vintage を指定すること（`assert_vintage_for_past` が落とす）。
   ⚠️ **オッズ予測モデルには vintage が無い**（学習 <= 2025-12 の1本）。
      2026年より前の日を再構築する用途には使えない（7T1 と同じ制約）。

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

from src import odds_prediction_tf as odds_tf  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7T3_MIN_ODDS, RANK_7T3_NE, rank_7t3_axes, rank_7t3_blend_probs,
    rank_7t3_daily_select, rank_7t3_select, rank_7t3_stakes,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402

# `predict_board` の meta に渡す列。予測値ではなく**出走表の構造情報**なので、
# 過去日の再構築でも look-ahead にならない。
_META_COLS = ("race_point", "line_group", "line_size", "line_pos", "is_line_leader",
              "player_class", "style", "first_rate", "second_rate", "third_rate")


def _load_range(date_from: str, date_to: str) -> dict[str, list[dict]]:
    """対象期間の7車レースの出走表を race_key ごとに返す。"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT e.race_key, r.race_date, r.venue_id, r.race_no, "
            "       r.n_entries, r.grade, r.race_type, r.day_index, r.distance, "
            "       r.start_at, e.frame_no, e.name, e.line_group, e.line_pos, "
            "       e.line_size, e.is_line_leader, e.race_point, e.player_class, "
            "       e.style, e.first_rate, e.second_rate, e.third_rate, "
            "       e.prediction_mark, v.name AS venue_name "
            "FROM wt_entries e "
            "JOIN wt_races r USING(race_key) "
            "LEFT JOIN venue_info v ON v.venue_code = r.venue_id "
            "WHERE r.cancel=0 AND r.n_entries=? AND r.race_date BETWEEN ? AND ?",
            (RANK_7T3_NE, date_from, date_to))
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
    p3v = load_model(eval_model).predict_proba(X)[:, 1]
    pwv = load_model(win_model).predict_proba(X)[:, 1]
    for rk, fn, a, b in zip(feats["race_key"], feats["frame_no"], p3v, pwv):
        p3.setdefault(rk, {})[int(fn)] = float(a)
        pw.setdefault(rk, {})[int(fn)] = float(b)
    return p3, pw


def _meta_of(ents: list[dict]) -> dict[int, dict]:
    """`predict_board` に渡す車番ごとの構造情報。"""
    out: dict[int, dict] = {}
    for e in ents:
        m = {c: e.get(c) for c in _META_COLS}
        m["mark"] = e.get("prediction_mark")
        out[int(e["frame_no"])] = m
    return out


def build(date_from: str, date_to: str, eval_model: str, win_model: str,
          require_model: bool = True) -> list[dict]:
    """対象期間の 7T3 候補（選別後）を返す。当日生成もバックフィルもこれを使う。"""
    by_race = _load_range(date_from, date_to)
    if not by_race:
        print(f"{date_from}〜{date_to}: 7車立てのレースがありません")
        return []
    print(f"{date_from}〜{date_to}: 7車立て {len(by_race)}R")

    # 🔴 モデル未配備を**黙って0件**にしない。既存の三連複ランクに無い依存なので、
    #    ここで落ちないと「今日はたまたま該当なし」と区別がつかない。
    try:
        odds_tf.load_model(RANK_7T3_NE)
    except Exception as e:                                   # noqa: BLE001
        msg = (f"三連単オッズ予測モデルを読めません（{e}）。"
               f"data/models/odds_tf_n7.txt と odds_tf_meta.json を配備すること")
        if require_model:
            raise SystemExit(f"[build_7t3] {msg}") from e
        print(f"[build_7t3] 警告: {msg} → 候補0件で続行")
        return []

    p3_all, pw_all = _predict(date_from, date_to, eval_model, win_model)

    cands = []
    n_no_board = n_no_band = 0
    for rk, ents in by_race.items():
        probs = p3_all.get(rk)
        pw = pw_all.get(rk)
        if not probs or len(probs) != RANK_7T3_NE or not pw:
            print(f"  {rk}: 3着内率が {len(probs or {})}/{RANK_7T3_NE} 件しか無く skip")
            continue
        meta = _meta_of(ents)
        try:
            pred_odds = odds_tf.predict_board(sorted(probs), probs, pw, meta)
        except odds_tf.OddsPredictionUnavailable as e:
            n_no_board += 1
            print(f"  {rk}: 予測オッズを作れず skip（{e}）")
            continue
        legs = rank_7t3_select(probs, pw, pred_odds)
        if not legs:
            n_no_band += 1          # 帯（30倍以上）に届く目が1点も無い
            continue
        stakes = rank_7t3_stakes(legs)
        # 🔴 ◎○ は**買い目の軸ではない**（`rank_7t3_axes` の docstring）。
        #    見解本文でも「二軸」と書かない（`_body_no_axis`）。
        axis1, axis2 = rank_7t3_axes(legs)
        name_of = {int(e["frame_no"]): e.get("name") for e in ents}
        order = [f for f, _ in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))]
        cars_in_legs = sorted({int(x) for leg in legs for x in leg.split("-")})
        partners = [c for c in cars_in_legs if c not in (axis1, axis2)]
        blend = rank_7t3_blend_probs(sorted(probs), pw, probs)
        cands.append({
            "race_key": rk, "race_date": ents[0]["race_date"],
            "venue_name": ents[0].get("venue_name"), "race_no": ents[0].get("race_no"),
            "start_time": ents[0].get("start_at"),
            "race_type": ents[0].get("race_type"),
            "n_entries": RANK_7T3_NE,
            "min_odds": RANK_7T3_MIN_ODDS,
            "order": order,                      # 3着内率の降順（表示に使う）
            # ⚠️ **買い目の軸ではない**。表示（◎○）と `netkeirin_submissions` の
            #    axis1/axis2 のためだけに持つ。入稿側は候補に無ければ
            #    `rank_7t3_axes` で導き直すので、ここは記録の意味合いが強い。
            "axis1": axis1, "axis2": axis2,
            "axis1_name": name_of.get(axis1), "axis2_name": name_of.get(axis2),
            "partners": partners,                # △（買い目に出るが ◎○ でない車）
            # ⚠️ `formation_*` は持たない。5点の1着が1車に揃うのは 7.0% しかなく、
            #    1着1車固定のフォーメーションでは表現できない（1点=1行で送る）。
            # 帯に入っている根拠。入稿はしないが検証・調査で必ず要る。
            "pred_odds": {leg: round(float(pred_odds[tuple(int(x) for x in leg.split("-"))]), 1)
                          for leg in legs},
            # 位置別合成 PL の確率（買い目の並び順の根拠）。
            "leg_probs": {leg: round(float(blend.get(tuple(int(x) for x in leg.split("-")), 0.0)), 6)
                          for leg in legs},
            # 展開済みの買い目と賭け金（picks_history と入稿の両方がこれを正とする）。
            "legs": legs,
            "stakes": stakes,
            "bet_amount": sum(stakes.values()),
        })

    if n_no_board:
        print(f"  予測オッズを作れなかったレース: {n_no_board}件")
    if n_no_band:
        print(f"  {RANK_7T3_MIN_ODDS:.0f}倍以上の目が無かったレース: {n_no_band}件")
    picked_all = rank_7t3_daily_select(cands)
    print(f"決勝で {len(picked_all)}/{len(cands)}R を採用")
    for c in picked_all:
        print(f"  {c['race_key']} {c.get('venue_name')}{c.get('race_no')}R "
              f"[{c.get('race_type')}] ◎{c['axis1']}○{c['axis2']} "
              f"{len(c['legs'])}点 計{c['bet_amount']:,}円 "
              f"想定={min(c['pred_odds'].values()):.0f}〜{max(c['pred_odds'].values()):.0f}倍")
    return picked_all


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    ap.add_argument("--out", default=None)
    ap.add_argument("--eval-model", default="lgbm_wt_eval", help="3着内率モデル")
    ap.add_argument("--win-model", default="lgbm_wt_win", help="1着率モデル（PL合成に使う）")
    ap.add_argument("--allow-missing-model", action="store_true",
                    help="オッズ予測モデルが無くても落とさず0件で続行する")
    args = ap.parse_args()

    # 🔴 過去日を本番モデル（全期間学習）でスコアすると model-vintage look-ahead に
    #    なる。既定値が本番モデル名なので、指定を忘れると**無言で**そうなる。
    assert_vintage_for_past(
        args.date, {"eval": args.eval_model, "win": args.win_model})

    cands = build(args.date, args.date, args.eval_model, args.win_model,
                  require_model=not args.allow_missing_model)
    # 既存ランクと同じ data/picks/ 配下へ出す（notify_prerace_wt.py が読む場所）
    path = Path(args.out) if args.out else (
        REPO / "data" / "picks" / f"wave_picks_wt_{args.date}_s7t3_candidates.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")   # 書き込み途中を読まれないよう原子的に置く
    tmp.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"[保存先] {path}  (7T3候補 {len(cands)}件)")


if __name__ == "__main__":
    main()
