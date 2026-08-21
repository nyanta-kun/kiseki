"""JRA is_win 較正ヘッド 学習スクリプト（本番 win_probability 較正用）

jra_calibration_ab.py の検証で、softmax(composite) は未較正(OOS ECE 0.033・最上位
decile +16pt 過信)、is_win binary LGB の生出力＋レース内正規化が OOS ECE 0.0027 と
ほぼ完璧に較正されると判明。本スクリプトはその is_win ヘッドを本番モデルとして学習・
保存する。composite.py が推論時にレース内正規化して win_probability に使う。

- 特徴量: composite._build_v26_features と同一(v24サブ17 + レースメタ10 + 馬メタ7 = 34)
- 目的: binary is_win (1着=1)
- 境界: `src/jra_protocol.py`（TRAIN ≤ TRAIN_END / VAL / TEST）に従う

## ⚠️ 2026-08-22 修正: 本番モデルを全期間 refit していた

旧実装は OOS sanity 用に別モデルを1本作った上で、**本番モデルだけ `--start`〜`--end`
の全期間で refit** していた（`train_model(df, seed=0)`）。`train_jra_reg_rank.py` /
`train_jra_out_rate.py` は `jra_protocol.TRAIN_DATA_END` で切る修正が入っていたが、
本スクリプトだけ取り残されていた。

実害（実測 / v27 index・keiba.race_results）:
    win_probability 最上位馬の勝率
      2023〜2026H1・2026-05-01〜06-05（= 旧モデルの訓練内）  0.42〜0.44
      2026-06-06〜08-22（= 旧モデルの訓練外）                0.258
    composite_index 1位馬（reg_rank ヘッド・境界修正済み）は全期間 0.29〜0.32 で安定。
つまり差分は丸ごと暗記であり、**2026-06-05 以前で信頼度指標を検証すると本命の勝率が
26% ではなく 43% に見える**。`models/v26_iswin_calib_metrics.json` の
`oos_sanity.iswin_norm_ece` は訓練内窓を含まない別モデルの数字なので嘘ではないが、
**出荷モデルの数字ではない**点に注意（ECE 自体も p<0.05 帯に質量の 56% が偏るため
上位帯の崩れを隠す。信頼性テーブルを併記する）。

出力:
  models/v26_iswin_calib.txt    - 較正ヘッド（TRAIN_DATA_END までで学習）
  models/v26_iswin_calib_metrics.json

使い方:
  cd backend
  .venv/bin/python scripts/train_jra_iswin_head.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

from scripts.jra_calibration_ab import (  # noqa: E402
    ALL_FEATURES,
    QUERY,
    calib_metrics,
    featurize,
    race_normalize,
)
from src import jra_protocol  # noqa: E402
from src.indices.composite import SUBINDEX_SOURCE_SQL  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_iswin")

MODELS_DIR = _root / "models"
MODEL_PATH = MODELS_DIR / "v26_iswin_calib.txt"
METRICS_PATH = MODELS_DIR / "v26_iswin_calib_metrics.json"

PARAMS = dict(
    objective="binary", metric="binary_logloss", num_leaves=31, max_depth=6,
    min_data_in_leaf=100, lambda_l1=0.1, lambda_l2=0.1, learning_rate=0.05,
    feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=5, verbose=-1,
)
# early stopping の上限。旧実装は 500 固定だったが、VAL で best_iteration を選ぶ。
MAX_ROUND = 2000

# サブ指数は v26 以降不変（composite.SUBINDEX_MIN_VERSION 参照）。共有 QUERY は
# `version = 26` 固定だが v26 行は 2026-08-02 で止まっており、以降は v27 行しかない。
# 固定のままだと **honest test 窓が途中で切れて本命勝率の実測が痩せる**ため、
# composite と同じ「(race_id, horse_id) ごとに最大版」に差し替えて引く。
SUBINDEX_QUERY = QUERY.replace(
    "FROM keiba.calculated_indices ci",
    f"FROM ({SUBINDEX_SOURCE_SQL}) ci",
).replace("WHERE ci.version = %(ver)s", "WHERE TRUE")


def fetch(conn, start, end):
    import pandas as pd
    cur = conn.cursor()
    cur.execute(SUBINDEX_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    df["date"] = df["date"].astype(str)
    return featurize(df)


def _xy(df):
    return (df[ALL_FEATURES].values.astype(float),
            (df["finish_position"] == 1).astype(int).values)


def train_model(df, seed=0, num_round=None, valid_df=None):
    """is_win ヘッドを学習する。

    valid_df を渡すと early stopping で best_iteration を選ぶ。
    渡さない場合は num_round 固定ラウンドで学習する（本番 refit 用）。
    """
    X, y = _xy(df)
    ds = lgb.Dataset(X, y, feature_name=ALL_FEATURES)
    if valid_df is None:
        return lgb.train(dict(PARAMS, seed=seed), ds, num_boost_round=num_round or MAX_ROUND)
    Xv, yv = _xy(valid_df)
    dv = lgb.Dataset(Xv, yv, reference=ds)
    return lgb.train(dict(PARAMS, seed=seed), ds, num_boost_round=MAX_ROUND,
                     valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20991231")
    p.add_argument("--train-end", default=jra_protocol.TRAIN_END)
    p.add_argument("--valid-end", default=jra_protocol.VAL_END)
    p.add_argument("--refit-end", default=jra_protocol.TRAIN_DATA_END,
                   help="本番モデルを refit する終端。既定は TEST_START の前日。"
                        "TEST を学習に含めると一度きり評価が in-sample になる")
    p.add_argument("--seeds", default="42,123,456")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    logger.info("protocol: %s", jra_protocol.describe())

    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch(conn, args.start, args.end)
    conn.close()
    df = df[df["finish_position"].notna()].reset_index(drop=True)
    logger.info("全データ: %d行 %dレース (%s〜%s)",
                len(df), df["race_id"].nunique(), df["date"].min(), df["date"].max())

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end].reset_index(drop=True)
    logger.info("train=%d valid=%d test=%d", len(tr), len(va), len(te))
    if not len(tr) or not len(va):
        raise SystemExit("train / valid が空。--start / --train-end / --valid-end を確認すること")

    # ── ラウンド数を VAL で選ぶ（seed 平均の best_iteration 中央値） ──
    best_iters, te_preds = [], []
    for seed in seeds:
        m = train_model(tr, seed=seed, valid_df=va)
        best_iters.append(int(m.best_iteration))
        if len(te):
            Xte, _ = _xy(te)
            te_preds.append(m.predict(Xte, num_iteration=m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info("best_iter=%s → refit rounds=%d", best_iters, n_rounds)

    metrics: dict = {
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "seeds": seeds,
        "best_iters": best_iters,
        "refit_rounds": n_rounds,
        "features": ALL_FEATURES,
        "params": PARAMS,
    }

    # ── honest test: TRAIN のみで学習したモデルを TEST に一度だけ当てる ──
    # 本番 refit モデル（VAL を含む）で TEST を測ると VAL 分だけ有利になるため、
    # 較正の数字は必ずこちらの train-only モデルで出す。
    if len(te):
        raw_te = np.mean(te_preds, axis=0)
        norm_te = race_normalize(raw_te, te["race_id"])
        y_te = (te["finish_position"] == 1).astype(int).values
        cm_raw = calib_metrics(raw_te, y_te)
        cm_norm = calib_metrics(norm_te, y_te)
        cm_softmax = calib_metrics(te["softmax_win"].values.astype(float), y_te)
        # レース内 win_probability 最上位馬の勝率（= 本命の堅さの実測値）
        import pandas as pd
        top = (pd.DataFrame({"r": te["race_id"].values, "p": norm_te, "y": y_te})
               .sort_values("p", ascending=False).groupby("r").head(1))
        metrics["test"] = {
            "period": [te["date"].min(), te["date"].max()],
            "n": int(len(te)), "n_races": int(te["race_id"].nunique()),
            "softmax_ece": round(cm_softmax["ece"], 4),
            "iswin_raw_ece": round(cm_raw["ece"], 4),
            "iswin_norm_ece": round(cm_norm["ece"], 4),
            "iswin_norm_mce": round(cm_norm["mce"], 4),
            "softmax_brier": round(cm_softmax["brier"], 4),
            "iswin_norm_brier": round(cm_norm["brier"], 4),
            "top1_mean_pred": round(float(top["p"].mean()), 4),
            "top1_actual_win_rate": round(float(top["y"].mean()), 4),
            # ECE は最下位 decile に質量が偏って上位帯の崩れを隠すので decile 表も残す
            "iswin_norm_reliability": [
                {"decile": int(b), "n": int(n), "pred_pct": round(float(pr), 2),
                 "actual_pct": round(float(ac), 2), "gap_pct": round(float(gp), 2)}
                for b, n, pr, ac, gp in cm_norm["table"]
            ],
        }
        logger.info("honest test (%s〜%s, %dR): softmax ECE=%.4f / iswin raw ECE=%.4f "
                    "/ iswin norm ECE=%.4f",
                    te["date"].min(), te["date"].max(), te["race_id"].nunique(),
                    cm_softmax["ece"], cm_raw["ece"], cm_norm["ece"])
        # ECE は p<0.05 帯に質量が偏って上位帯の崩れを隠すため、本命の実測を併記する。
        logger.info("honest test 本命(win_probability最上位)の予測=%.3f 実測勝率=%.3f",
                    top["p"].mean(), top["y"].mean())
    else:
        logger.warning("test 期間にデータなし（TEST_START=%s）", jra_protocol.TEST_START)

    # ── 本番モデル: **TEST_START の前日まで**で refit ──
    # （seed 平均は取れないため先頭 seed で固定ラウンド学習）。
    # TEST を学習に含めるとその四半期の一度きり評価が in-sample になる。
    fit = df[df["date"] <= args.refit_end]
    logger.info("refit: %d行 (%s〜%s)", len(fit), fit["date"].min(), fit["date"].max())
    final = train_model(fit, seed=seeds[0], num_round=n_rounds)
    MODELS_DIR.mkdir(exist_ok=True)
    final.save_model(str(MODEL_PATH))
    metrics["model_path"] = str(MODEL_PATH)
    metrics["refit_period"] = [fit["date"].min(), fit["date"].max()]
    metrics["n_rows"] = int(len(fit))
    metrics["n_races"] = int(fit["race_id"].nunique())
    metrics["protocol"] = jra_protocol.describe()
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    logger.info("保存: %s / %s", MODEL_PATH, METRICS_PATH)


if __name__ == "__main__":
    main()
