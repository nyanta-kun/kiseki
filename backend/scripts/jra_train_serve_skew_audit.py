"""JRA 総合指数の train/serve 不整合を測る（P0 監査）。

学習は `keiba.calculated_indices` の v26 行 + `race_entries` + `race_results` を読む
（＝**レース確定後の値**）が、配信時に `composite.py` が組み立てる特徴量は
発走前の値である。両者がずれている列があると「学習で頼りにした情報が配信では常に
定数」という状態になり、地方 v13→v14 で約9pt の損失として実測された型と同じになる。

本スクリプトは **モデルを honest に学習し直した上で**、test 期間の予測を
「学習時の入力」と「配信時の入力」の両方で行い、順位精度の差を測る。

シナリオ（`--scenarios` で選択）:
  db          DB の値そのまま（＝学習時の条件・上限の参照点）
  cond_only   馬場状態と going_pedigree だけが欠ける（影響の切り出し用）
  night       前夜 22:00 の算出。馬場状態なし / 馬体重なし / 体重増減なし
  morning     当日 07:30 の算出。night と同じ（馬体重は発走1時間前に届くため）
  t1h_actual  馬体重到着後の再算出＝**現行本番の実体**。馬体重は入るが
              体重増減は `composite._get_weight_change_map` が `race_results` を
              読むため発走前は入らない
  prerace     t1h_actual の体重増減も入る版（読み先を直した場合の上限）
  dm_only     JV-Next DM 2列だけが欠ける（影響の切り出し用）
  nodm        morning に加えて DM 2列も欠損（DM 取得が落ちた日の再現）

⚠️ 本スクリプトは `train_jra_out_rate.FETCH_SQL` 経由で
`keiba.calculated_indices` の **version=26** 行に依存する。本番は既に v27 なので
v26 行は増えない（docs/jra_rebuild_2026_08.md 4.7）。学習ソースを付け替えたら
本スクリプトも同時に直すこと。

使い方:
    cd backend
    .venv/bin/python scripts/jra_train_serve_skew_audit.py \
        --scenarios db,cond_only,morning,t1h_actual,prerace,dm_only,nodm
    # 配信条件で学習し直した場合
    .venv/bin/python scripts/jra_train_serve_skew_audit.py \
        --train-scenario t1h_actual --scenarios db,t1h_actual
    # 死んだ特徴の除去 A/B
    .venv/bin/python scripts/jra_train_serve_skew_audit.py \
        --drop-features paddock_index,going_pedigree_index,rebound_index
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
from src.indices.composite import OUT_PROB_FEATURE_NAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_skew_audit")

OUT_PATH = _root / "models" / "jra_train_serve_skew_audit.json"

# 馬場状態が未確定のとき `_build_v26_features` が作る値（4本とも 0 = 学習に存在しない状態）
COND_COLS = ["is_good", "is_yaya", "is_heavy", "is_bad"]
# 発走前は NaN になる列
WEIGHT_COLS = ["horse_weight", "weight_change"]
DM_COLS = ["jvan_time_dm", "jvan_battle_dm"]

SCENARIOS: dict[str, dict] = {
    "db": {},
    "night": {"cond": True, "weight": True, "going": True},
    "morning": {"cond": True, "weight": True, "going": True},
    # 馬体重到着後（0B11）の再算出。**現行本番の実体**:
    # horse_weight は race_entries から読めるが、weight_change は race_results
    # からしか読まれない（`composite._get_weight_change_map`）ため発走前は入らない。
    "t1h_actual": {"cond": True, "going": True, "wc": True},
    # weight_change も race_entries から読むよう直した場合（未実装）
    "prerace": {"cond": True, "going": True},
    "nodm": {"cond": True, "weight": True, "going": True, "dm": True},
    # DM 欠損だけを切り出す（他は学習時と同じ）
    "dm_only": {"dm": True},
    # 馬場状態だけを切り出す
    "cond_only": {"cond": True, "going": True},
}


def apply_scenario(X: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """配信時の入力を再現する。列の順序・本数は変えない。"""
    x = X.copy()
    def _set(col: str, val: float) -> None:
        if col in x.columns:  # --drop-features で外された列は触らない
            x[col] = val
    if spec.get("cond"):
        for c in COND_COLS:
            _set(c, 0)
    if spec.get("going"):
        # races.condition が無いと GoingPedigreeIndexCalculator は早期 return し全馬 50
        _set("going_pedigree_index", 50.0)
    if spec.get("weight"):
        for c in WEIGHT_COLS:
            _set(c, np.nan)
    if spec.get("wc"):
        _set("weight_change", np.nan)
    if spec.get("dm"):
        for c in DM_COLS:
            _set(c, 50.0)
    return x


def _params(seed: int) -> dict:
    return dict(
        objective="regression", metric="l2",
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )


def race_metrics(te: pd.DataFrame, score: np.ndarray) -> dict:
    """レース単位の順位精度。score は小さいほど上位。"""
    from scipy.stats import spearmanr

    d = te[["race_id", "finish_position"]].copy()
    d["_s"] = score
    rhos, top1_win, top1_place, ndcg = [], [], [], []
    for _, g in d.groupby("race_id"):
        if len(g) < 3:
            continue
        rho = spearmanr(g["_s"], g["finish_position"]).correlation
        if not np.isnan(rho):
            rhos.append(rho)
        order = g.sort_values("_s")
        fin = order["finish_position"].values
        top1_win.append(1.0 if fin[0] == 1 else 0.0)
        top1_place.append(1.0 if fin[0] <= 3 else 0.0)
        rel = (fin[:3] <= 3).astype(float)
        disc = 1.0 / np.log2(np.arange(2, len(rel) + 2))
        ideal = min(3, int((fin <= 3).sum()))
        idcg = float((np.ones(ideal) * (1.0 / np.log2(np.arange(2, ideal + 2)))).sum()) if ideal else 0.0
        ndcg.append(float((rel * disc).sum() / idcg) if idcg else np.nan)
    return {
        "n_races": len(top1_win),
        "spearman": round(float(np.mean(rhos)), 5),
        "top1_win_rate": round(float(np.mean(top1_win)), 5),
        "top1_place_rate": round(float(np.mean(top1_place)), 5),
        "ndcg3": round(float(np.nanmean(ndcg)), 5),
    }


def top1_agreement(te: pd.DataFrame, base: np.ndarray, other: np.ndarray) -> float:
    """基準シナリオと指数1位馬が一致したレースの割合。"""
    d = te[["race_id"]].copy()
    d["_b"] = base
    d["_o"] = other
    d["_idx"] = np.arange(len(d))
    same = []
    for _, g in d.groupby("race_id"):
        same.append(1.0 if g.loc[g["_b"].idxmin(), "_idx"] == g.loc[g["_o"].idxmin(), "_idx"] else 0.0)
    return round(float(np.mean(same)), 5)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--scenarios", default="db,morning,prerace,nodm")
    p.add_argument("--train-scenario", default="db",
                   help="学習側に適用するシナリオ。serve 条件で学習し直す実験に使う")
    p.add_argument("--drop-features", default="",
                   help="学習・予測の両方から外す列（カンマ区切り）。死んだ特徴の除去 A/B 用")
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    scen_names = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    drop = {c.strip() for c in args.drop_features.split(",") if c.strip()}

    df = featurize(load_df(args.start, args.end))
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    df["y"] = normalized_rank(df)
    logger.info(f"データ: {len(df):,}行 / {df['race_id'].nunique():,}R "
                f"({df['date'].min()}〜{df['date'].max()})")

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end].reset_index(drop=True)
    logger.info(f"train={len(tr):,} valid={len(va):,} test={len(te):,} "
                f"(test {te['date'].min()}〜{te['date'].max()})")
    if te.empty:
        raise SystemExit("test 期間が空。--valid-end を見直すこと")

    F = [c for c in OUT_PROB_FEATURE_NAMES if c not in drop]
    if drop:
        logger.info(f"除外列: {sorted(drop)} → 特徴量 {len(F)}本")
    tr_X = apply_scenario(tr[F], SCENARIOS[args.train_scenario])
    va_X = apply_scenario(va[F], SCENARIOS[args.train_scenario])

    models = []
    for seed in seeds:
        d = lgb.Dataset(tr_X.values, label=tr["y"].values, feature_name=F)
        dv = lgb.Dataset(va_X.values, label=va["y"].values, reference=d)
        m = lgb.train(_params(seed), d, num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        models.append(m)
        logger.info(f"  seed={seed} best_iter={m.best_iteration}")

    def predict(X: pd.DataFrame) -> np.ndarray:
        return np.mean([m.predict(X.values, num_iteration=m.best_iteration) for m in models], axis=0)

    report: dict = {
        "train_scenario": args.train_scenario,
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "test_period": [te["date"].min(), te["date"].max()],
        "n_test_rows": int(len(te)), "seeds": seeds, "dropped_features": sorted(drop),
        "scenarios": {},
    }
    base_pred: np.ndarray | None = None
    for name in scen_names:
        X = apply_scenario(te[F], SCENARIOS[name])
        pred = predict(X)
        met = race_metrics(te, pred)
        if base_pred is None:
            base_pred = pred
        else:
            met["top1_agreement_with_first"] = top1_agreement(te, base_pred, pred)
        report["scenarios"][name] = met
        logger.info(f"[{name:8s}] spearman={met['spearman']:.4f} "
                    f"top1勝率={met['top1_win_rate']:.4f} "
                    f"top1複勝率={met['top1_place_rate']:.4f} ndcg3={met['ndcg3']:.4f}")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
