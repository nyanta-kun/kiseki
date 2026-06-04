"""地方競馬 モデル公正比較ハーネス（Phase1 比較基盤）

本番に効いているのは v9 線形 softmax（version=10 ラベルだが実体は線形）。
LGB を本番へ組み込む価値があるかを、同一 OOS 期間・複数 seed・drop1・
ブートストラップ CI で公正に比較する。

比較対象:
  - linear : 本番線形モデル（chihou.calculated_indices に格納済みの composite_index）
  - lgb    : LightGBM binary(3着以内) 単体（複数 seed のスコア平均）
  - ens    : 0.3*lgb_index + 0.7*linear_composite アンサンブル（inference と同方式）
  - market : 市場（単勝人気）ベンチマーク

評価指標（OOS test 期間）:
  - top1 勝率 / 複勝率
  - rank-IC（レース内 spearman(score, -finish) の平均）
  - 購入戦略 単勝ROI: 指数1位 ∧ 単勝≥10 / 10-30 / 場フィルタ（drop1 + 95%CI）

使い方:
  cd backend
  .venv/bin/python scripts/chihou_model_compare.py --seeds 5
  .venv/bin/python scripts/chihou_model_compare.py --train-start 20230101 \\
      --train-end 20250630 --test-start 20250701 --test-end 20260605 --seeds 5
"""
from __future__ import annotations

import argparse
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

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
from scipy.stats import spearmanr

# 既存の特徴量エンジニアリングを再利用（v11 と同一の特徴量で公正比較）
from scripts.train_chihou_v11_lightgbm import (  # noqa: E402
    ALL_FEATURES as V11_FEATURES,
    NEW_FEATURES,
    add_historical_features,
    featurize,
    fetch_hist,
)

# 本番取込パスで履歴系4特徴(NEW_FEATURES)をライブ計算するコストを避けるため、
# 17特徴量(v10相当)と21特徴量(v11)を切替可能にする。
V10_FEATURES = [f for f in V11_FEATURES if f not in NEW_FEATURES]
ALL_FEATURES = V11_FEATURES  # main() で --no-hist に応じて差し替え

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_compare")

CHIHOU_V9_VERSION = 9
FAVORABLE_COURSES = {"浦和", "金沢", "高知", "笠松", "盛岡"}

# linear_composite と人気/オッズも取得する点だけ v11 BASE_QUERY と異なる
BASE_QUERY = """
SELECT
    ci.race_id,
    r.date,
    r.course_name,
    r.prize_1st     AS curr_prize,
    r.surface,
    r.condition,
    r.distance,
    r.head_count,
    re.horse_id,
    re.frame_number,
    re.horse_age,
    re.weight_carried,
    COALESCE(rr.horse_weight, 500) AS horse_weight,
    COALESCE(rr.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    ci.composite_index AS linear_composite,
    rr.finish_position,
    rr.win_odds,
    rr.win_popularity
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %(ver)s
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL
  AND rr.win_popularity IS NOT NULL
ORDER BY r.date, ci.race_id, re.horse_id
"""


def fetch_base(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    logger.info("base取得: %d行 %dレース (%s〜%s)", len(df), df["race_id"].nunique(), start, end)
    return df


def scale_to_index(scores: np.ndarray, race_ids: pd.Series) -> np.ndarray:
    """レース内 min-max → 15-85（inference_chihou_v10 と同方式）。"""
    out = np.zeros(len(scores), dtype=float)
    tmp = pd.DataFrame({"race_id": np.asarray(race_ids), "score": scores})
    for _, idx in tmp.groupby("race_id").indices.items():
        s = tmp["score"].values[idx]
        if len(s) <= 1:
            out[idx] = 50.0
            continue
        lo, hi = s.min(), s.max()
        out[idx] = 50.0 if hi - lo < 1e-9 else 15.0 + (s - lo) / (hi - lo) * 70.0
    return out


def train_lgb_seeds(df_train: pd.DataFrame, df_test: pd.DataFrame, seeds: list[int]) -> tuple[np.ndarray, list[np.ndarray]]:
    """複数 seed で binary(3着以内) を学習。(平均スコア, seed別スコアリスト) を返す。"""
    X_train = df_train[ALL_FEATURES].values.astype(float)
    y_train = (pd.to_numeric(df_train["finish_position"], errors="coerce") <= 3).astype(int).values
    X_test = df_test[ALL_FEATURES].values.astype(float)

    preds = []
    for sd in seeds:
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "num_leaves": 31,
            "max_depth": 5,
            "min_data_in_leaf": 50,
            "lambda_l1": 0.1,
            "lambda_l2": 1.0,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "seed": sd,
            "verbose": -1,
        }
        ds = lgb.Dataset(X_train, y_train, feature_name=ALL_FEATURES)
        model = lgb.train(params, ds, num_boost_round=400)
        preds.append(model.predict(X_test))
        logger.info("  seed=%d 学習完了", sd)
    return np.mean(preds, axis=0), preds


def rank_ic(df: pd.DataFrame, score_col: str) -> float:
    """レース内 spearman(score, -finish) の平均 = rank-IC。"""
    vals = []
    for _, idx in df.groupby("race_id").indices.items():
        s = df[score_col].values[idx]
        f = pd.to_numeric(df["finish_position"].values[idx], errors="coerce").astype(float)
        if len(s) < 3 or np.all(s == s[0]):
            continue
        rho, _ = spearmanr(s, -f)
        if not np.isnan(rho):
            vals.append(rho)
    return float(np.mean(vals)) if vals else float("nan")


def _roi_with_ci(picks: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    """単勝ROI・的中率・drop1・ブートストラップ95%CI を返す。picks=1レース1行の選択馬。"""
    n = len(picks)
    if n == 0:
        return {"n": 0, "hit": 0.0, "roi": 0.0, "roi_drop1": 0.0, "ci_lo": 0.0, "ci_hi": 0.0}
    wins = (pd.to_numeric(picks["finish_position"], errors="coerce") == 1)
    odds = pd.to_numeric(picks["win_odds"], errors="coerce").values
    payout = np.where(wins.values, odds, 0.0)
    roi = payout.sum() / n
    # drop1: 最高配当の的中を1つ除外
    if payout.max() > 0:
        roi_drop1 = (payout.sum() - payout.max()) / max(n - 1, 1)
    else:
        roi_drop1 = roi
    # bootstrap
    boot = []
    for _ in range(n_boot):
        samp = rng.choice(payout, size=n, replace=True)
        boot.append(samp.mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {
        "n": n,
        "hit": float(wins.mean()),
        "roi": float(roi),
        "roi_drop1": float(roi_drop1),
        "ci_lo": float(lo),
        "ci_hi": float(hi),
    }


def evaluate_model(df: pd.DataFrame, score_col: str, label: str, rng: np.random.Generator) -> dict:
    """1モデルのランキング品質 + 購入戦略ROIを評価する。"""
    df = df.copy()
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["mrank"] = df.groupby("race_id")[score_col].rank(ascending=False, method="first")

    top1 = df[df["mrank"] == 1]
    win_pct = (top1["finish_position"] == 1).mean() * 100
    place_pct = (top1["finish_position"] <= 3).mean() * 100
    ic = rank_ic(df, score_col)

    # 購入戦略
    s_hi = _roi_with_ci(top1[top1["win_odds"] >= 10], rng)
    s_1030 = _roi_with_ci(top1[(top1["win_odds"] >= 10) & (top1["win_odds"] < 30)], rng)
    s_fav = _roi_with_ci(
        top1[(top1["win_odds"] >= 10) & (top1["win_odds"] < 30) & (top1["course_name"].isin(FAVORABLE_COURSES))],
        rng,
    )
    return {
        "label": label,
        "top1_win_pct": round(win_pct, 1),
        "top1_place_pct": round(place_pct, 1),
        "rank_ic": round(ic, 3),
        "hi": s_hi,
        "odds_10_30": s_1030,
        "fav_course": s_fav,
    }


def compute_prior_runs(hist: pd.DataFrame) -> pd.DataFrame:
    """各 (horse_id, race_id) の出走前累積走数を返す。"""
    h = hist.sort_values(["horse_id", "date", "race_id"]).copy()
    h["prior_runs"] = h.groupby("horse_id").cumcount()
    return h[["horse_id", "race_id", "prior_runs"]].drop_duplicates(subset=["horse_id", "race_id"])


def _top1(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    r = df.groupby("race_id")[score_col].rank(ascending=False, method="first")
    return df[r == 1]


def diagnostics(df_test: pd.DataFrame, per_seed_idx: list[np.ndarray], rng: np.random.Generator) -> None:
    """頑健性診断: 複数seedばらつき / 月次安定性 / 場別 / 症状A・B 改善。"""
    df = df_test.copy()
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["month"] = df["date"].astype(str).str[:6]
    df["year"] = df["date"].astype(str).str[:4]

    # ── ① 複数 seed ばらつき ──
    print("\n--- ① 複数seedばらつき (LGB単体: top1勝率 / 割安場10-30倍 単勝ROI) ---")
    win_list, roi_list = [], []
    for k, idx_scores in enumerate(per_seed_idx):
        df["_s"] = idx_scores
        t1 = _top1(df, "_s")
        win = (t1["finish_position"] == 1).mean() * 100
        fav = t1[(t1["win_odds"] >= 10) & (t1["win_odds"] < 30) & (t1["course_name"].isin(FAVORABLE_COURSES))]
        roi = (fav.loc[fav["finish_position"] == 1, "win_odds"].sum() / len(fav)) if len(fav) else 0.0
        win_list.append(win)
        roi_list.append(roi)
        print(f"  seed{k}: top1勝率 {win:.1f}%  割安場ROI {roi:.3f} (n={len(fav)})")
    print(f"  → top1勝率 平均{np.mean(win_list):.1f}% ±{np.std(win_list):.2f}  /  割安場ROI 平均{np.mean(roi_list):.3f} ±{np.std(roi_list):.3f}")

    # ── ② 月次安定性 (top1勝率) ──
    print("\n--- ② 月次 top1勝率 (linear / LGB平均 / market) ---")
    for col, lab in [("linear_composite", "linear"), ("lgb_index", "LGB"), ("market_score", "market")]:
        df["_r"] = df.groupby("race_id")[col].rank(ascending=False, method="first")
    print(f"  {'月':<8}{'linear':>8}{'LGB':>8}{'market':>8}{'n_race':>8}")
    for m, g in df.groupby("month"):
        row = {}
        for col in ["linear_composite", "lgb_index", "market_score"]:
            t1 = _top1(g, col)
            row[col] = (t1["finish_position"] == 1).mean() * 100
        print(f"  {m:<8}{row['linear_composite']:>7.1f}%{row['lgb_index']:>7.1f}%{row['market_score']:>7.1f}%{g['race_id'].nunique():>8}")

    # ── ③ 場別 top1勝率 + 割安場ROI ──
    print("\n--- ③ 場別 top1勝率 (linear→LGB) + 単勝ROI(LGB 1位×10-30倍) ---")
    print(f"  {'場':<8}{'linear':>8}{'LGB':>8}{'ROI(LGB)':>10}{'n_pick':>8}")
    for c, g in df.groupby("course_name"):
        if g["race_id"].nunique() < 100:
            continue
        lin = (_top1(g, "linear_composite")["finish_position"] == 1).mean() * 100
        t1l = _top1(g, "lgb_index")
        lgbw = (t1l["finish_position"] == 1).mean() * 100
        pick = t1l[(t1l["win_odds"] >= 10) & (t1l["win_odds"] < 30)]
        roi = (pick.loc[pick["finish_position"] == 1, "win_odds"].sum() / len(pick)) if len(pick) else 0.0
        star = " ★" if roi > 1.0 else ""
        print(f"  {c:<8}{lin:>7.1f}%{lgbw:>7.1f}%{roi:>10.3f}{len(pick):>8}{star}")

    # ── ④ 割安場ROI 年次 ──
    print("\n--- ④ 割安場(浦和/金沢/高知/笠松/盛岡) LGB1位×10-30倍 年次ROI ---")
    t1 = _top1(df, "lgb_index")
    fav = t1[(t1["win_odds"] >= 10) & (t1["win_odds"] < 30) & (t1["course_name"].isin(FAVORABLE_COURSES))]
    for y, g in fav.groupby("year"):
        roi = g.loc[g["finish_position"] == 1, "win_odds"].sum() / len(g)
        print(f"  {y}: ROI {roi:.3f}  的中{(g['finish_position']==1).mean()*100:.1f}%  n={len(g)}")

    # ── ⑤ 症状A/B 改善 (prior_runs帯別) ──
    df["rank_lin"] = df.groupby("race_id")["linear_composite"].rank(ascending=False, method="first")
    df["rank_lgb"] = df.groupby("race_id")["lgb_index"].rank(ascending=False, method="first")
    print("\n--- ⑤ 症状A: 勝ち馬をrank4+に降格する率 (linear→LGB, 低いほど良) ---")
    print(f"  {'prior_runs':<12}{'linear':>9}{'LGB':>9}{'n_win':>8}")
    for lo, hi, lab in [(0, 0, "debut"), (1, 2, "1-2走"), (3, 10, "3-10走"), (11, 20, "11-20走"), (21, 999, "21+走")]:
        wmask = (df["finish_position"] == 1) & (df["prior_runs"] >= lo) & (df["prior_runs"] <= hi)
        nwin = int(wmask.sum())
        if nwin == 0:
            continue
        dl = (df.loc[wmask, "rank_lin"] >= 4).mean() * 100
        dg = (df.loc[wmask, "rank_lgb"] >= 4).mean() * 100
        print(f"  {lab:<12}{dl:>8.1f}%{dg:>8.1f}%{nwin:>8}")

    print("\n--- ⑥ 症状B: 指数top3の凡走(fp>=6)率 (linear→LGB, 低いほど良) ---")
    for col, lab in [("linear_composite", "linear"), ("lgb_index", "LGB")]:
        rr = df.groupby("race_id")[col].rank(ascending=False, method="first")
        top3 = df[rr <= 3]
        bust = (top3["finish_position"] >= 6).mean() * 100
        print(f"  {lab}: top3 bust率 {bust:.1f}%")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--train-start", default="20230101")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--test-end", default="20260605")
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--no-hist", action="store_true", help="履歴系4特徴を除外(17特徴=v10相当)")
    args = p.parse_args()

    global ALL_FEATURES
    ALL_FEATURES = V10_FEATURES if args.no_hist else V11_FEATURES
    logger.info("特徴量セット: %s (%d個)", "v10(17)" if args.no_hist else "v11(21)", len(ALL_FEATURES))

    seeds = list(range(args.seeds))
    rng = np.random.default_rng(12345)

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    df_train_raw = fetch_base(conn, args.train_start, args.train_end)
    df_test_raw = fetch_base(conn, args.test_start, args.test_end)
    df_hist = fetch_hist(conn)
    conn.close()

    # 特徴量エンジニアリング（leakage 回避のため全件まとめて hist を適用）
    df_all = pd.concat([df_train_raw, df_test_raw], ignore_index=True)
    df_all = featurize(df_all)
    df_all = add_historical_features(df_all, df_hist)
    for col in ALL_FEATURES:
        df_all[col] = pd.to_numeric(df_all[col], errors="coerce").fillna(-1.0)

    cut = df_train_raw["date"].max()
    df_train = df_all[df_all["date"] <= cut].copy()
    df_test = df_all[df_all["date"] > cut].copy().reset_index(drop=True)
    logger.info("分割: train=%d test=%d", len(df_train), len(df_test))

    # prior_runs（出走前累積走数）を test に付与（症状A/B 層別用）
    pr = compute_prior_runs(df_hist)
    df_test = df_test.merge(pr, on=["horse_id", "race_id"], how="left")
    df_test["prior_runs"] = df_test["prior_runs"].fillna(0).astype(int)

    # LGB 学習（複数 seed 平均）
    logger.info("LGB 学習 (%d seeds)...", len(seeds))
    lgb_raw, per_seed_raw = train_lgb_seeds(df_train, df_test, seeds)
    df_test["lgb_index"] = scale_to_index(lgb_raw, df_test["race_id"])
    per_seed_idx = [scale_to_index(p, df_test["race_id"]) for p in per_seed_raw]
    df_test["linear_composite"] = pd.to_numeric(df_test["linear_composite"], errors="coerce")
    df_test["ens_score"] = 0.3 * df_test["lgb_index"] + 0.7 * df_test["linear_composite"]
    df_test["market_score"] = -pd.to_numeric(df_test["win_popularity"], errors="coerce")  # 人気=小ほど良

    results = [
        evaluate_model(df_test, "linear_composite", "linear(本番)", rng),
        evaluate_model(df_test, "lgb_index", f"lgb({len(seeds)}seed平均)", rng),
        evaluate_model(df_test, "ens_score", "ensemble 0.3/0.7", rng),
        evaluate_model(df_test, "market_score", "market(人気)", rng),
    ]

    # ── 出力 ──
    print("\n" + "=" * 78)
    print(f"OOS比較  train {args.train_start}-{args.train_end} / test {args.test_start}-{args.test_end}")
    print(f"test: {df_test['race_id'].nunique()}レース {len(df_test)}馬 / LGB {len(seeds)}seed平均")
    print("=" * 78)
    print(f"{'model':<18}{'top1勝率':>9}{'top1複勝':>9}{'rankIC':>8}")
    for r in results:
        print(f"{r['label']:<18}{r['top1_win_pct']:>8.1f}%{r['top1_place_pct']:>8.1f}%{r['rank_ic']:>8.3f}")

    print("\n--- 購入戦略 単勝ROI: 指数1位 ∧ 単勝≥10 ---")
    print(f"{'model':<18}{'n':>5}{'的中率':>8}{'ROI':>7}{'drop1':>7}{'95%CI':>16}")
    for key, title in [("hi", "≥10"), ("odds_10_30", "10-30倍"), ("fav_course", "10-30×割安場")]:
        print(f"[{title}]")
        for r in results[:3]:  # market は購入戦略対象外
            s = r[key]
            print(f"  {r['label']:<16}{s['n']:>5}{s['hit']*100:>7.1f}%{s['roi']:>7.3f}{s['roi_drop1']:>7.3f}"
                  f"   [{s['ci_lo']:.2f},{s['ci_hi']:.2f}]")
    print("=" * 78)

    # ── 頑健性診断 ──
    diagnostics(df_test, per_seed_idx, rng)
    print("=" * 78)


if __name__ == "__main__":
    main()
