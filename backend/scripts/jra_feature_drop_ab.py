"""死んだ特徴を外すべきかを **レース単位 paired bootstrap** で判定する。

台帳 `docs/jra_rebuild_2026_08.md` 6章・課題#6。
`paddock_index` / `going_pedigree_index` / `rebound_index` は配信時に定数になっている。
外すと honest test で +0.53pt だったが、n=2,082R では 1レース = 0.048pt であり
**その差が偶然の範囲かどうかが判定できていなかった**。

## 2 つの実験を分けて回す（`--experiment`）

**`honest`**: train ≤2025-06-30 / test 2026-07〜（プロトコル準拠）
  → 測れるのは実質 `going_pedigree_index` と `rebound_index` だけ。
    ⚠️ **`paddock_index` は TRAIN 期間で完全な定数なので、この分割では
    モデルが一度も分岐せず、外しても数値が 1 ミリも動かない**（実測で小数点以下5桁まで一致）。

**`paddock`**: train ≤2026-04-30（paddock が生きている期間を含む）/ test 2026-05-01〜
  （paddock が死んだ期間）
  → **本番モデルと同じ状況を再現する**。本番は全期間 refit なので
    paddock が生きていた 2025-07〜2026-04 の分岐を持ち、配信では必ず定数 50 側へ落ちる。
    「今は死んでいる特徴を学習に残しておくと害があるか」を直接測る唯一の形。

## paired bootstrap

同じレース集合に対する 2 モデルの予測を比べるので、**レースを単位に**リサンプルして
差の分布を作る（レース間の難易度差が相殺され、検出力が上がる）。

使い方:
    cd backend
    .venv/bin/python scripts/jra_feature_drop_ab.py --experiment honest
    .venv/bin/python scripts/jra_feature_drop_ab.py --experiment paddock
"""

from __future__ import annotations

import argparse
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
import pandas as pd  # noqa: E402

from scripts.train_jra_out_rate import featurize, load_df  # noqa: E402
from scripts.train_jra_reg_rank import normalized_rank  # noqa: E402
from src import jra_protocol  # noqa: E402
from src.indices.composite import OUT_PROB_FEATURE_NAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_feature_drop_ab")

OUT_PATH = _root / "models" / "jra_feature_drop_ab.json"

DEAD_FEATURES = ["paddock_index", "going_pedigree_index", "rebound_index"]

EXPERIMENTS = {
    # 既定。**評価は VAL（2025-07〜2026-06）で行い TEST は開けない**。
    # 特徴量の採否は「条件探索」なのでプロトコル上 VAL でやるのが正しく、
    # TEST を使うと一度きりの窓を潰す（src/jra_protocol.py）。
    "val": {
        "train_end": "20241231",
        "valid_end": jra_protocol.TRAIN_END,   # early stopping 用
        "test_end": jra_protocol.VAL_END,      # 評価は VAL の終わりまで
        "drop": DEAD_FEATURES,
    },
    # 本番と同じ「生きている期間で学習し、死んだ期間へ配信する」状況を再現。
    # paddock は 2025-07〜2026-04 だけ生きて 2026-05 に死んだので、その境界を跨がせる。
    "paddock": {
        "train_end": "20260229",
        "valid_end": "20260430",
        "test_end": jra_protocol.VAL_END,
        "drop": ["paddock_index"],
    },
    # プロトコル準拠の一度きり評価。**採否判断に使ってはいけない**（TEST が焼ける）
    "honest": {
        "train_end": jra_protocol.TRAIN_END,
        "valid_end": jra_protocol.VAL_END,
        "test_end": "20991231",
        "drop": DEAD_FEATURES,
    },
}


def _params(seed: int) -> dict:
    return dict(
        objective="regression", metric="l2",
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )


def _fit_predict(
    tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame, feats: list[str], seeds: list[int]
) -> np.ndarray:
    preds = []
    for seed in seeds:
        d = lgb.Dataset(tr[feats].values, label=tr["y"].values, feature_name=feats)
        dv = lgb.Dataset(va[feats].values, label=va["y"].values, reference=d)
        m = lgb.train(_params(seed), d, num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[feats].values, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0)


def per_race_metrics(te: pd.DataFrame, score: np.ndarray) -> pd.DataFrame:
    """レースごとに 1 行の指標を返す（bootstrap の単位）。"""
    from scipy.stats import spearmanr

    d = te[["race_id", "finish_position"]].copy()
    d["_s"] = score
    rows = []
    for rid, g in d.groupby("race_id"):
        if len(g) < 3:
            continue
        order = g.sort_values("_s")
        fin = order["finish_position"].values
        rho = spearmanr(g["_s"], g["finish_position"]).correlation
        rows.append({
            "race_id": rid,
            "top1_win": 1.0 if fin[0] == 1 else 0.0,
            "top1_place": 1.0 if fin[0] <= 3 else 0.0,
            "spearman": rho if not np.isnan(rho) else np.nan,
        })
    return pd.DataFrame(rows).set_index("race_id")


def paired_bootstrap(
    base: pd.DataFrame, alt: pd.DataFrame, metric: str, n_boot: int, seed: int
) -> dict:
    """レース単位の paired bootstrap。alt - base の差の分布を返す。"""
    common = base.index.intersection(alt.index)
    b = base.loc[common, metric].values
    a = alt.loc[common, metric].values
    mask = ~(np.isnan(b) | np.isnan(a))
    b, a = b[mask], a[mask]
    diff = a - b
    rng = np.random.default_rng(seed)
    n = len(diff)
    boots = np.array([diff[rng.integers(0, n, n)].mean() for _ in range(n_boot)])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "n_races": int(n),
        "base": round(float(b.mean()), 5),
        "alt": round(float(a.mean()), 5),
        "diff": round(float(diff.mean()), 5),
        "ci95": [round(float(lo), 5), round(float(hi), 5)],
        # 差が 0 をまたがなければ有意
        "significant": bool(lo > 0 or hi < 0),
        "p_alt_better": round(float((boots > 0).mean()), 4),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--experiment", choices=list(EXPERIMENTS), default="val")
    p.add_argument("--start", default="20230506")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--n-boot", type=int, default=5000)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    spec = EXPERIMENTS[args.experiment]

    df = featurize(load_df(args.start, "20991231"))
    df = df[~df["abnormality_code"].fillna(0).isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    df["y"] = normalized_rank(df)

    tr = df[df["date"] <= spec["train_end"]]
    va = df[(df["date"] > spec["train_end"]) & (df["date"] <= spec["valid_end"])]
    te = df[(df["date"] > spec["valid_end"]) & (df["date"] <= spec["test_end"])]
    te = te.reset_index(drop=True)
    logger.info(
        f"[{args.experiment}] train={len(tr):,} (〜{spec['train_end']}) "
        f"valid={len(va):,} test={len(te):,} "
        f"({te['date'].min()}〜{te['date'].max()} / {te['race_id'].nunique():,}R)"
    )
    if te.empty or va.empty:
        raise SystemExit("valid/test が空。期間指定を見直すこと")

    # 除去対象が学習期間で本当に動いているかを先に見る（動いていなければ判定不能）
    variance = {
        c: round(float(tr[c].std()), 4) for c in spec["drop"] if c in tr.columns
    }
    logger.info(f"学習期間での除去対象の sd: {variance}")
    inert = [c for c, v in variance.items() if v == 0.0]
    if inert:
        logger.warning(
            f"⚠️ {inert} は学習期間で定数。この分割では外しても結果は変わらない"
            "（モデルが一度も分岐しないため）。--experiment paddock を使うこと"
        )

    base_feats = OUT_PROB_FEATURE_NAMES
    alt_feats = [c for c in OUT_PROB_FEATURE_NAMES if c not in spec["drop"]]

    base_pred = _fit_predict(tr, va, te, base_feats, seeds)
    alt_pred = _fit_predict(tr, va, te, alt_feats, seeds)
    base_m = per_race_metrics(te, base_pred)
    alt_m = per_race_metrics(te, alt_pred)

    report = {
        "experiment": args.experiment,
        "train_end": spec["train_end"],
        "valid_end": spec["valid_end"],
        "test_period": [te["date"].min(), te["date"].max()],
        "test_is_val": spec["test_end"] == jra_protocol.VAL_END,
        "dropped": spec["drop"],
        "train_sd_of_dropped": variance,
        "inert_in_train": inert,
        "n_features": {"base": len(base_feats), "alt": len(alt_feats)},
        "seeds": seeds,
        "n_boot": args.n_boot,
        "metrics": {},
    }
    for metric in ("top1_win", "top1_place", "spearman"):
        r = paired_bootstrap(base_m, alt_m, metric, args.n_boot, seed=seeds[0])
        report["metrics"][metric] = r
        mark = "✅ 有意" if r["significant"] else "－ 有意差なし"
        logger.info(
            f"{metric:<11} 現行 {r['base']:.4f} → 除去 {r['alt']:.4f} "
            f"(差 {r['diff']:+.4f}, 95%CI [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}]) {mark}"
        )

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
