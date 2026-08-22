#!/usr/bin/env python3
"""三連単の「買い目の形」を後からいくらでも比較するためのレース単位キャッシュ。

## なぜキャッシュを作るのか

買い目の形（軸の置き方・点数・目標額・母集団・選別順）を1組ずつ検証するたびに
モデル推論と予測オッズ盤面の生成をやり直すと、1組あたり十数分かかって比較にならない。
**推論結果と確定結果を1度だけ落として JSONL に固め、以後の比較は全部オフライン**にする。

1レースぶんに入るもの:

  - `p3` / `pw`  : 3着内率・1着率（月次凍結 vintage モデル）
  - `bad` / `top2`: 大敗率（6着以下）・2着内率。**同じ vintage 世代**を使う。
    🔴 `bad` は 7H1 が使う「本命が飛ぶか」の唯一の市場直交シグナル
    （AUC 0.6848）で、レース単位の選別に要る
  - `odds`       : **全210点**の予測三連単オッズ（`odds_prediction_tf.predict_board`）
  - `board`      : `wt_odds` に実在した点（欠車で消えた目を落とすため）
  - `win`        : 確定した当たり目（**同着があるので複数**）と 100円あたり配当
  - `line` / `mark` / `race_type` : 母集団の切り方に使う属性

🔴 **必ず月次 vintage モデルで回す。** 本番モデル（全期間 full-refit）を過去へ当てると
model-vintage look-ahead になる。三連単オッズ予測モデル（`odds_tf_n7.txt`・
学習終端 2025-12-31）には vintage が無いので **2026-01 より前は作れない**。

🔴 **予測オッズは「板」として再スケールされた値**（`predict_board` の docstring）。
生の点推定ではないので、ここを自前で作り直さないこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_tf_shape_cache.py \
        --out data/exp/tf_shape_cache.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.build_7t1_candidates as b7t1  # noqa: E402
from scripts.backfill_7t1_rank_wt import (  # noqa: E402
    ODDS_TF_TRAIN_END, _load_finishes, _load_tf_boards,
)
from src import odds_prediction_tf as odds_tf  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import RANK_7T1_NE  # noqa: E402
from src.wt_vintage_config import (  # noqa: E402
    bad_model_name, monthly_windows, top2_model_name,
)


def _window_rows(date_from: str, date_to: str, eval_model: str, win_model: str):
    by_race = b7t1._load_range(date_from, date_to)
    if not by_race:
        return
    odds_tf.load_model(RANK_7T1_NE)
    p3_all, pw_all = b7t1._predict(date_from, date_to, eval_model, win_model)
    # 🔴 bad / top2 は **eval と同じ vintage 世代**を使う（世代がずれると
    #    「未来を知っているヘッド」が混ざる）。`_predict` は2ヘッド用なので
    #    同じ関数へ別の組で渡して流用する。
    bad_all, top2_all = b7t1._predict(
        date_from, date_to, bad_model_name(eval_model), top2_model_name(eval_model))

    keys = list(by_race)
    boards = _load_tf_boards(keys)
    fins = _load_finishes(keys)
    pm = _load_payouts_wt(keys)

    for rk, ents in by_race.items():
        probs = p3_all.get(rk)
        pw = pw_all.get(rk)
        if not probs or len(probs) != RANK_7T1_NE or not pw:
            continue
        order = fins.get(rk)
        board = boards.get(rk)
        if not order or not board:
            continue
        meta = b7t1._meta_of(ents)
        try:
            pred = odds_tf.predict_board(sorted(probs), probs, pw, meta)
        except odds_tf.OddsPredictionUnavailable:
            continue
        wins = winning_trifectas(order)
        e0 = ents[0]
        yield {
            "race_key": rk, "race_date": e0["race_date"],
            "venue_name": e0.get("venue_name"), "race_no": e0.get("race_no"),
            "race_type": e0.get("race_type"), "grade": e0.get("grade"),
            "p3": {str(k): round(v, 6) for k, v in probs.items()},
            "pw": {str(k): round(v, 6) for k, v in pw.items()},
            "bad": {str(k): round(v, 6)
                    for k, v in (bad_all.get(rk) or {}).items()},
            "top2": {str(k): round(v, 6)
                     for k, v in (top2_all.get(rk) or {}).items()},
            # 全210点。キーは "1-2-3"（着順つき）。
            "odds": {"-".join(map(str, c)): round(o, 2) for c, o in pred.items()},
            "board": ["-".join(map(str, c)) for c in sorted(board)],
            "line_group": {str(int(e["frame_no"])): e.get("line_group") for e in ents},
            "line_pos": {str(int(e["frame_no"])): e.get("line_pos") for e in ents},
            "mark": {str(int(e["frame_no"])): e.get("prediction_mark") for e in ents},
            # 🔴 同着があるので当たり目は複数になりうる。配当も目ごとに違う。
            "win": {"-".join(map(str, w)): int(pm.get(rk, {}).get(("trifecta", w), 0))
                    for w in wins},
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--upto", default=None)
    args = ap.parse_args()

    upto = date.fromisoformat(args.upto) if args.upto else None
    windows = [w for w in monthly_windows(upto=upto) if w[1] > ODDS_TF_TRAIN_END]
    if not windows:
        print("対象窓なし（三連単オッズモデルの学習終端より後の月が無い）")
        return 1
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w") as f:
        for df, dt, em, wm in windows:
            c = 0
            for row in _window_rows(df, dt, em, wm):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                c += 1
            n += c
            print(f"  {df}〜{dt} [{em}]: {c}R", flush=True)
    print(f"→ {out} に {n}R")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
