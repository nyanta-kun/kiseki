"""JRA 指数のランキング品質レビュー（上位・下位を分離して測定）

問題意識（ユーザー要件, 2026-08-02）:
  「全順位を正しく並べる」のが理想だが、まずは **上位と下位を正しく並べる** 指数に
  改修できないかを調査する。

本スクリプトは同一の honest 分割上で複数の候補スコアを並べ、
  - HEAD（上位）: 1位馬の勝率・複勝率、勝ち馬をtop3に含む率、NDCG@3
  - TAIL（下位）: 下位3頭の実着外率、勝ち馬/3着内馬を下位に沈めてしまう率
  - 全体      : レース内 Spearman ρ、平均絶対順位誤差
を比較する。

候補スコア:
  1. prod_composite   本番 v26（LGB LambdaRank 0.3 + v24線形和 0.7）
  2. p_win            1着二値ヘッド
  3. p_top3           3着内二値ヘッド
  4. neg_p_out        着外率の逆順（本番の足切りに使っているヘッド）
  5. blend_equal      z(p_win)+z(p_top3)-z(p_out) 等重み
  6. blend_valid      上記3ヘッドの重みを valid で最適化
  7. lambdarank_grad  着順を段階的relevanceにした LambdaRank（新規学習）
  8. reg_rank         レース内正規化着順の回帰（新規学習）
  9. head_tail_stack  上位はp_top3・下位はp_outを効かせる合成（提案本命）

honest 設計:
  - 特徴量にオッズ・人気・composite_index・win_probability を使わない（新規学習分）
  - train ≤2025-06 / valid 2025-07〜12 / test 2026-01〜（既定）
  - `--test-year 2025` で train ≤2024-06 / valid 2024-07〜12 / test 2025年 の独立窓に切替

使い方:
    cd backend
    .venv/bin/python scripts/jra_rank_quality_review.py
    .venv/bin/python scripts/jra_rank_quality_review.py --test-year 2025
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
import psycopg2  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from src.indices.composite import OUT_PROB_FEATURE_NAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rank_quality")

MODELS_DIR = _root / "models"
FEATURES = OUT_PROB_FEATURE_NAMES

FETCH_SQL = """
SELECT
    r.date, ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position,
    rr.win_odds, rr.win_popularity,
    ci.composite_index
FROM keiba.calculated_indices ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = 26
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def load() -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    conn.close()
    return df


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    surface = df["surface"].fillna("").astype(str)
    cond = df["condition"].fillna("").astype(str)
    grade = df["grade"].fillna("").astype(str)
    df["is_turf"] = surface.str.startswith("芝").astype(int)
    df["is_dirt"] = surface.str.startswith("ダ").astype(int)
    df["is_jump"] = surface.str.startswith("障").astype(int)
    df["is_good"] = (cond == "良").astype(int)
    df["is_yaya"] = (cond == "稍").astype(int)
    df["is_heavy"] = (cond == "重").astype(int)
    df["is_bad"] = (cond == "不").astype(int)
    df["is_g1g2g3"] = grade.isin(["G1", "G2", "G3"]).astype(int)
    num = FEATURES + ["finish_position", "abnormality_code", "composite_index",
                      "win_odds", "win_popularity"]
    for c in num:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df[FEATURES[:17]] = df[FEATURES[:17]].fillna(50.0)
    df["jvan_time_dm"] = df["jvan_time_dm"].fillna(50.0)
    df["jvan_battle_dm"] = df["jvan_battle_dm"].fillna(50.0)
    return df


def _params(seed: int, **kw) -> dict:
    p = dict(
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )
    p.update(kw)
    return p


def _groups(df: pd.DataFrame) -> np.ndarray:
    """LightGBM の group（レースごとの行数）。df は race_id でソート済みであること。"""
    return df.groupby("race_id", sort=False).size().values


def train_binary(tr, va, te, label_tr, label_va, seeds) -> np.ndarray:
    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[FEATURES].values, label=label_tr, feature_name=FEATURES)
        dv = lgb.Dataset(va[FEATURES].values, label=label_va, reference=d)
        m = lgb.train(_params(s, objective="binary", metric="binary_logloss"), d,
                      num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[FEATURES].values, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0)


def train_lambdarank(tr, va, te, seeds) -> np.ndarray:
    """着順を段階的 relevance にした LambdaRank。

    relevance: 1着=4 / 2着=3 / 3着=2 / 4-5着=1 / 6着以下=0
    → NDCG が上位の並びを、gain 0 の広い底が下位の押し下げを担う。
    """
    def rel(fp: pd.Series) -> np.ndarray:
        r = np.zeros(len(fp), dtype=int)
        v = fp.values
        r[v == 1] = 4
        r[v == 2] = 3
        r[v == 3] = 2
        r[(v >= 4) & (v <= 5)] = 1
        return r

    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[FEATURES].values, label=rel(tr["finish_position"]),
                        group=_groups(tr), feature_name=FEATURES)
        dv = lgb.Dataset(va[FEATURES].values, label=rel(va["finish_position"]),
                         group=_groups(va), reference=d)
        m = lgb.train(
            _params(s, objective="lambdarank", metric="ndcg",
                    ndcg_eval_at=[1, 3, 5], label_gain=[0, 1, 3, 7, 15]),
            d, num_boost_round=2000, valid_sets=[dv],
            callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[FEATURES].values, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0)


def train_reg_rank(tr, va, te, seeds) -> np.ndarray:
    """レース内正規化着順（0=1着, 1=最下位）の回帰。小さいほど上位。"""
    def y(d: pd.DataFrame) -> np.ndarray:
        r = d.groupby("race_id")["finish_position"].rank(method="min")
        n = d.groupby("race_id")["finish_position"].transform("size")
        return ((r - 1) / (n - 1).clip(lower=1)).values

    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[FEATURES].values, label=y(tr), feature_name=FEATURES)
        dv = lgb.Dataset(va[FEATURES].values, label=y(va), reference=d)
        m = lgb.train(_params(s, objective="regression", metric="l2"), d,
                      num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[FEATURES].values, num_iteration=m.best_iteration))
    return -np.mean(preds, axis=0)  # 大きいほど上位に統一


def zscore_in_race(df: pd.DataFrame, col: str) -> np.ndarray:
    g = df.groupby("race_id")[col]
    return ((df[col] - g.transform("mean")) / g.transform("std").replace(0, np.nan)).fillna(0).values


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, score_col: str) -> dict:
    """score_col（大きいほど上位）のランキング品質を測る。"""
    d = df.copy()
    d["rank"] = d.groupby("race_id")[score_col].rank(ascending=False, method="first")
    d["n"] = d.groupby("race_id")["race_id"].transform("size")
    d["rank_from_bottom"] = d["n"] - d["rank"] + 1
    fp = d["finish_position"]

    top1 = d[d["rank"] == 1]
    top3 = d[d["rank"] <= 3]
    bot3 = d[d["rank_from_bottom"] <= 3]
    bot30 = d[d["rank"] > d["n"] * 0.7]      # 下位30%

    # NDCG@3（relevance: 1着=3, 2着=2, 3着=1）
    rel = np.where(fp == 1, 3.0, np.where(fp == 2, 2.0, np.where(fp == 3, 1.0, 0.0)))
    d["_rel"] = rel
    def _ndcg3(g: pd.DataFrame) -> float:
        gs = g.sort_values("rank")["_rel"].values[:3]
        disc = 1.0 / np.log2(np.arange(2, len(gs) + 2))
        dcg = float((gs * disc).sum())
        ideal = np.sort(g["_rel"].values)[::-1][:3]
        idcg = float((ideal * 1.0 / np.log2(np.arange(2, len(ideal) + 2))).sum())
        return dcg / idcg if idcg > 0 else np.nan
    ndcg = d.groupby("race_id")[["rank", "_rel"]].apply(_ndcg3, include_groups=False)

    # レース内 Spearman
    def _sp(g: pd.DataFrame) -> float:
        if len(g) < 3:
            return np.nan
        return spearmanr(-g["rank"], -g["finish_position"]).correlation
    sp = d.groupby("race_id")[["rank", "finish_position"]].apply(_sp, include_groups=False)

    # 勝ち馬 / 3着内馬をモデルが何位に置いたか（正規化位置 0=最上位, 1=最下位）
    d["pos_norm"] = (d["rank"] - 1) / (d["n"] - 1).clip(lower=1)
    winners = d[fp == 1]
    placers = d[fp <= 3]

    return {
        "HEAD_top1_win": round(float((top1["finish_position"] == 1).mean()), 4),
        "HEAD_top1_place": round(float((top1["finish_position"] <= 3).mean()), 4),
        "HEAD_winner_in_top3": round(float(
            top3.groupby("race_id")["finish_position"].apply(lambda s: (s == 1).any()).mean()), 4),
        "HEAD_ndcg3": round(float(ndcg.mean()), 4),
        "TAIL_bot3_out_rate": round(float((bot3["finish_position"] >= 6).mean()), 4),
        "TAIL_winner_in_bot3": round(float(
            bot3.groupby("race_id")["finish_position"].apply(lambda s: (s == 1).any()).mean()), 4),
        "TAIL_placer_in_bot30pct": round(float((bot30["finish_position"] <= 3).sum()
                                               / max(1, (fp <= 3).sum())), 4),
        "TAIL_bot30pct_out_rate": round(float((bot30["finish_position"] >= 6).mean()), 4),
        "ALL_spearman": round(float(sp.mean()), 4),
        "ALL_winner_pos": round(float(winners["pos_norm"].mean()), 4),
        "ALL_placer_pos": round(float(placers["pos_norm"].mean()), 4),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--test-year", default="2026", choices=["2025", "2026"])
    p.add_argument("--seeds", default="42,123,456")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    if args.test_year == "2026":
        train_end, valid_end, test_end = "20250630", "20251231", "20991231"
    else:
        train_end, valid_end, test_end = "20240630", "20241231", "20251231"

    df = featurize(load())
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df[df["composite_index"].notna()]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    tr = df[df["date"] <= train_end].copy()
    va = df[(df["date"] > train_end) & (df["date"] <= valid_end)].copy()
    te = df[(df["date"] > valid_end) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info(f"train={len(tr):,}/{tr.race_id.nunique():,}R  valid={len(va):,}  "
                f"test={len(te):,}/{te.race_id.nunique():,}R "
                f"({te['date'].min()}〜{te['date'].max()})")

    # --- 3ヘッド + 新規2モデル ---
    logger.info("学習: p_win / p_top3 / p_out / lambdarank_grad / reg_rank")
    te["p_win"] = train_binary(tr, va, te, (tr.finish_position == 1).astype(int),
                               (va.finish_position == 1).astype(int), seeds)
    te["p_top3"] = train_binary(tr, va, te, (tr.finish_position <= 3).astype(int),
                                (va.finish_position <= 3).astype(int), seeds)
    te["p_out"] = train_binary(tr, va, te, (tr.finish_position >= 6).astype(int),
                               (va.finish_position >= 6).astype(int), seeds)
    te["lambdarank_grad"] = train_lambdarank(tr, va, te, seeds)
    te["reg_rank"] = train_reg_rank(tr, va, te, seeds)

    # valid 上でブレンド重みを決めるため valid の予測も作る
    va2 = va.copy().reset_index(drop=True)
    va2["p_win"] = train_binary(tr, va, va2, (tr.finish_position == 1).astype(int),
                                (va.finish_position == 1).astype(int), seeds)
    va2["p_top3"] = train_binary(tr, va, va2, (tr.finish_position <= 3).astype(int),
                                 (va.finish_position <= 3).astype(int), seeds)
    va2["p_out"] = train_binary(tr, va, va2, (tr.finish_position >= 6).astype(int),
                                (va.finish_position >= 6).astype(int), seeds)

    for d in (te, va2):
        d["z_win"] = zscore_in_race(d, "p_win")
        d["z_top3"] = zscore_in_race(d, "p_top3")
        d["z_out"] = zscore_in_race(d, "p_out")
    te["z_lr"] = zscore_in_race(te, "lambdarank_grad")

    te["blend_equal"] = te["z_win"] + te["z_top3"] - te["z_out"]

    # valid で重みを最適化（目的: HEAD と TAIL の合成スコア）
    best, best_w = -1e9, (1.0, 1.0, 1.0)
    for w1 in np.arange(0, 1.01, 0.25):
        for w2 in np.arange(0, 1.01, 0.25):
            for w3 in np.arange(0, 1.01, 0.25):
                if w1 + w2 + w3 == 0:
                    continue
                va2["_s"] = w1 * va2.z_win + w2 * va2.z_top3 - w3 * va2.z_out
                m = evaluate(va2, "_s")
                obj = m["HEAD_ndcg3"] + m["TAIL_bot30pct_out_rate"]
                if obj > best:
                    best, best_w = obj, (w1, w2, w3)
    logger.info(f"blend_valid 最適重み (win, top3, out) = {best_w} (valid obj={best:.4f})")
    te["blend_valid"] = (best_w[0] * te.z_win + best_w[1] * te.z_top3 - best_w[2] * te.z_out)

    # 提案: 上位は LambdaRank、下位は着外率で押し下げる head-tail stack
    te["head_tail_stack"] = te["z_lr"] - 0.5 * te["z_out"]

    te["neg_p_out"] = -te["p_out"]
    te["prod_composite"] = te["composite_index"]

    candidates = ["prod_composite", "p_win", "p_top3", "neg_p_out", "blend_equal",
                  "blend_valid", "lambdarank_grad", "reg_rank", "head_tail_stack"]

    # 本番アンサンブルの LGB 部分だけを取り出して比較する
    # （本番 composite = 0.3*LGB + 0.7*v24線形和。線形和が足を引っ張っていないかの切り分け）
    # v26_lightgbm_rank.txt の学習期間は 2023-05〜2025-06 なので 2026 窓でのみ honest。
    v26_path = MODELS_DIR / "v26_lightgbm_rank.txt"
    if args.test_year == "2026" and v26_path.exists():
        try:
            booster = lgb.Booster(model_file=str(v26_path))
            te["v26_lgb_only"] = booster.predict(te[FEATURES].values)
            candidates.insert(1, "v26_lgb_only")
            logger.info("v26_lgb_only を候補に追加（本番アンサンブルの LGB 部分のみ）")
        except Exception as e:
            logger.warning(f"v26_lgb_only の推論に失敗: {e}")
    rows = {c: evaluate(te, c) for c in candidates}

    keys_head = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3"]
    keys_tail = ["TAIL_bot3_out_rate", "TAIL_winner_in_bot3",
                 "TAIL_placer_in_bot30pct", "TAIL_bot30pct_out_rate"]
    keys_all = ["ALL_spearman", "ALL_winner_pos", "ALL_placer_pos"]

    def table(title: str, keys: list[str], note: str) -> None:
        print("\n" + "=" * 108)
        print(f"{title}   {note}")
        print("=" * 108)
        print(f"{'候補':<20}" + "".join(f"{k.split('_', 1)[1]:>21}" for k in keys))
        for c in candidates:
            print(f"{c:<20}" + "".join(f"{rows[c][k]:>21.4f}" for k in keys))

    print(f"\n### test = {te['date'].min()}〜{te['date'].max()} "
          f"({te.race_id.nunique():,}レース / {len(te):,}頭) ###")
    table("【HEAD】上位の並び", keys_head, "（高いほど良い）")
    table("【TAIL】下位の並び", keys_tail,
          "（bot3_out/bot30_out は高いほど良い・winner_in_bot3/placer_in_bot30 は低いほど良い）")
    table("【ALL】全体", keys_all,
          "（spearman は高い / winner_pos・placer_pos は低いほど良い）")

    out = MODELS_DIR / f"jra_rank_quality_review_{args.test_year}.json"
    out.write_text(json.dumps(
        {"test_year": args.test_year, "blend_valid_weights": list(best_w),
         "test_period": [te["date"].min(), te["date"].max()],
         "n_races": int(te.race_id.nunique()), "results": rows},
        ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
