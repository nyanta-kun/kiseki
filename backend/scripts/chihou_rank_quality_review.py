"""地方競馬 指数のランキング品質レビュー（上位・下位を分離して測定）

問題意識:
  地方の評価はこれまで「勝率 / 複勝率 / ROI」だけだった。JRA では同じ状態のときに
  「本番 composite が比較候補中で最下位クラス」「アンサンブルの線形和が足枷」という
  事実を発見できておらず、HEAD/TAIL/ALL を分離して初めて判明した
  （memory: jra_rank_quality_redesign_2026_08_02）。地方でも同じ測定基盤を用意する。

測定する指標:
  HEAD（上位）: 1位馬の勝率・複勝率、勝ち馬をtop3に含む率、NDCG@3
  TAIL（下位）: 下位3頭の実着外率、勝ち馬を下位3頭に沈める率、3着内馬を下位30%に沈める率
  ALL（全体） : レース内 Spearman ρ、勝ち馬/3着内馬の平均正規化位置

JRA からの移植で変えた点（重要）:
  - **「着外」の定義**。JRA は16頭立て前提で「6着以下」だが、地方は 7〜12頭立てなので
    そのまま移植すると意味が変わる。既定を `finish_position >= 5`（複勝圏 top3 の
    1つ上にバッファを置く）とし、`--out-threshold` で変更可能にした。
  - **特徴量に市場（オッズ）系を含める**。JRA はオッズ非依存で測ったが、地方の本番
    モデル v11/v12 は設計上 market 5特徴を含む。除くと本番との比較が成立しないため、
    既定は 44 特徴すべて（`--no-market` で除外可）。発走前オッズなのでリークではない。

候補スコア（すべて honest 再学習・大きいほど上位に統一）:
  1. prod_db      DB の composite_index（**in-sample・参考値**。model-vintage look-ahead を含む）
  2. bin_top3     is_top3 二値（現行本番と同じ目的関数）= 真のベースライン
  3. bin_win      is_win 二値
  4. reg_rank     レース内正規化着順の回帰（JRA v27 の採用形）
  5. lambdarank   着順を段階 relevance にした LambdaRank
  6. neg_p_out    着外率ヘッドの逆順
  7. blend_*      z(reg_rank) − w·z(p_out) の合成（w を数点）

honest 分割（`src/chihou_protocol.py` 準拠）:
  train ≤ TRAIN_END(20250630) / valid 20250701〜20251231 / test 20260101〜20260630
  TEST_START(20260701) 以降は既定で使わない。`--use-test` 指定時のみ使い、
  `record_test_usage()` で台帳に記録する。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_rank_quality_review.py
    .venv/bin/python scripts/chihou_rank_quality_review.py --out-threshold 4 --seeds 42,123
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

from scripts.train_chihou_market_lgb import ALL_FEATURES, MARKET_FEATURES, fetch, prep  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TEST_START, TRAIN_END, record_test_usage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_rank_quality")

MODELS_DIR = _root / "models"
VALID_END = "20251231"
DATA_START = "20230101"


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_prod_composite(conn, start: str, end: str) -> pd.DataFrame:
    """DB の本番 composite_index（参考値）。version は race ごとに最新を採る。"""
    sql = """
    SELECT DISTINCT ON (ci.race_id, ci.horse_id)
           ci.race_id, ci.horse_id, ci.composite_index
    FROM chihou.calculated_indices ci
    JOIN chihou.races r ON r.id = ci.race_id
    WHERE r.date BETWEEN %(s)s AND %(e)s
    ORDER BY ci.race_id, ci.horse_id, ci.version DESC
    """
    cur = conn.cursor()
    cur.execute(sql, {"s": start, "e": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    df["composite_index"] = pd.to_numeric(df["composite_index"], errors="coerce")
    return df


def _params(seed: int, **kw) -> dict:
    p = dict(
        learning_rate=0.05, num_leaves=31, max_depth=5, min_data_in_leaf=50,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=5,
        lambda_l1=0.1, lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )
    p.update(kw)
    return p


def _groups(df: pd.DataFrame) -> np.ndarray:
    return df.groupby("race_id", sort=False).size().values


def train_binary(tr, va, te, y_tr, y_va, feats, seeds) -> np.ndarray:
    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[feats].values, label=y_tr, feature_name=feats)
        dv = lgb.Dataset(va[feats].values, label=y_va, reference=d)
        m = lgb.train(_params(s, objective="binary", metric="binary_logloss"), d,
                      num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[feats].values, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0)


def train_reg_rank(tr, va, te, feats, seeds) -> np.ndarray:
    """レース内正規化着順（0=1着, 1=最下位）の回帰。返り値は大きいほど上位。"""
    def y(d: pd.DataFrame) -> np.ndarray:
        r = d.groupby("race_id")["finish_position"].rank(method="min")
        n = d.groupby("race_id")["finish_position"].transform("size")
        return ((r - 1) / (n - 1).clip(lower=1)).values

    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[feats].values, label=y(tr), feature_name=feats)
        dv = lgb.Dataset(va[feats].values, label=y(va), reference=d)
        m = lgb.train(_params(s, objective="regression", metric="l2"), d,
                      num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[feats].values, num_iteration=m.best_iteration))
    return -np.mean(preds, axis=0)


def train_lambdarank(tr, va, te, feats, seeds) -> np.ndarray:
    """着順の段階 relevance（1着=4/2着=3/3着=2/4-5着=1/以下=0）で LambdaRank。"""
    def rel(fp: pd.Series) -> np.ndarray:
        v = fp.values
        r = np.zeros(len(v), dtype=int)
        r[v == 1] = 4
        r[v == 2] = 3
        r[v == 3] = 2
        r[(v >= 4) & (v <= 5)] = 1
        return r

    preds = []
    for s in seeds:
        d = lgb.Dataset(tr[feats].values, label=rel(tr["finish_position"]),
                        group=_groups(tr), feature_name=feats)
        dv = lgb.Dataset(va[feats].values, label=rel(va["finish_position"]),
                         group=_groups(va), reference=d)
        m = lgb.train(_params(s, objective="lambdarank", metric="ndcg",
                              ndcg_eval_at=[1, 3], label_gain=[0, 1, 3, 7, 15]),
                      d, num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(m.predict(te[feats].values, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0)


def z_in_race(df: pd.DataFrame, values: np.ndarray) -> np.ndarray:
    s = pd.Series(values, index=df.index)
    g = s.groupby(df["race_id"])
    return ((s - g.transform("mean")) / g.transform("std").replace(0, np.nan)).fillna(0.0).values


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, score: np.ndarray, out_threshold: int) -> dict:
    """score（大きいほど上位）のランキング品質を測る。"""
    d = df.copy()
    d["_score"] = score
    d["rank"] = d.groupby("race_id")["_score"].rank(ascending=False, method="first")
    d["n"] = d.groupby("race_id")["race_id"].transform("size")
    d["rank_from_bottom"] = d["n"] - d["rank"] + 1
    fp = d["finish_position"]

    top1 = d[d["rank"] == 1]
    top3 = d[d["rank"] <= 3]
    bot3 = d[d["rank_from_bottom"] <= 3]
    bot30 = d[d["rank"] > d["n"] * 0.7]

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

    def _sp(g: pd.DataFrame) -> float:
        if len(g) < 3:
            return np.nan
        return spearmanr(-g["rank"], -g["finish_position"]).correlation

    sp = d.groupby("race_id")[["rank", "finish_position"]].apply(_sp, include_groups=False)

    d["pos_norm"] = (d["rank"] - 1) / (d["n"] - 1).clip(lower=1)

    return {
        "HEAD_top1_win": round(float((top1["finish_position"] == 1).mean()), 4),
        "HEAD_top1_place": round(float((top1["finish_position"] <= 3).mean()), 4),
        "HEAD_winner_in_top3": round(float(
            top3.groupby("race_id")["finish_position"].apply(lambda s: (s == 1).any()).mean()), 4),
        "HEAD_ndcg3": round(float(ndcg.mean()), 4),
        "TAIL_bot3_out_rate": round(float((bot3["finish_position"] >= out_threshold).mean()), 4),
        "TAIL_winner_in_bot3": round(float(
            bot3.groupby("race_id")["finish_position"].apply(lambda s: (s == 1).any()).mean()), 4),
        "TAIL_placer_in_bot30pct": round(float((bot30["finish_position"] <= 3).sum()
                                               / max(1, int((fp <= 3).sum()))), 4),
        "ALL_spearman": round(float(sp.mean()), 4),
        "ALL_winner_pos": round(float(d[fp == 1]["pos_norm"].mean()), 4),
        "ALL_placer_pos": round(float(d[fp <= 3]["pos_norm"].mean()), 4),
    }


def per_race_metrics(df: pd.DataFrame, score: np.ndarray) -> pd.DataFrame:
    """ペア比較（ブートストラップ）用にレース単位の指標を返す。

    候補間の差は小さく、レース集合を共有しているため、平均値の単純比較では
    有意性を判断できない。同一レース集合でリサンプルする paired bootstrap を使う。
    """
    d = df[["race_id", "finish_position"]].copy()
    d["_score"] = score
    d["rank"] = d.groupby("race_id")["_score"].rank(ascending=False, method="first")

    def _agg(g: pd.DataFrame) -> pd.Series:
        fp = g["finish_position"].values
        rk = g["rank"].values
        top1_fp = fp[rk == 1]
        sp = spearmanr(-rk, -fp).correlation if len(g) >= 3 else np.nan
        return pd.Series({
            "top1_win": float(top1_fp[0] == 1) if len(top1_fp) else np.nan,
            "top1_place": float(top1_fp[0] <= 3) if len(top1_fp) else np.nan,
            "spearman": sp,
        })

    return d.groupby("race_id")[["rank", "finish_position"]].apply(_agg, include_groups=False)


def paired_bootstrap(base: pd.DataFrame, cand: pd.DataFrame, metric: str,
                     n_boot: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """レース単位 paired bootstrap で (差の平均, CI下限, CI上限) を返す。"""
    b = base[metric].to_numpy()
    c = cand[metric].to_numpy()
    ok = ~(np.isnan(b) | np.isnan(c))
    diff = c[ok] - b[ok]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n_boot, len(diff)))
    boots = diff[idx].mean(axis=1)
    return float(diff.mean()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--out-threshold", type=int, default=5,
                   help="「着外」とみなす着順の下限（既定5=5着以下。地方は7-12頭立て）")
    p.add_argument("--no-market", action="store_true",
                   help="市場(オッズ)由来の5特徴を除いて学習する")
    p.add_argument("--use-test", action="store_true",
                   help=f"TEST_START({TEST_START})以降を test に使う（台帳に記録される）")
    p.add_argument("--json-out", default=str(MODELS_DIR / "chihou_rank_quality_review.json"))
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    feats = [f for f in ALL_FEATURES if not (args.no_market and f in MARKET_FEATURES)]

    if args.use_test:
        test_start, test_end = TEST_START, "20991231"
        record_test_usage("ランキング品質レビュー（候補スコア比較）",
                          "chihou_rank_quality_review.py",
                          f"out_threshold={args.out_threshold} seeds={seeds}")
    else:
        test_start, test_end = "20260101", "20260630"

    conn = connect()
    try:
        logger.info(f"データ取得 {DATA_START}〜{test_end}")
        df_raw = fetch(conn, DATA_START, test_end)
        logger.info(f"  {len(df_raw):,} 行 / {df_raw['race_id'].nunique():,} レース")
        df_hist = fetch_hist(conn)
        logger.info("前処理（prep）")
        df = prep(conn, df_raw, df_hist)
        prod = load_prod_composite(conn, test_start, test_end)
    finally:
        conn.close()

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    tr = df[df["date"] <= TRAIN_END].copy()
    va = df[(df["date"] > TRAIN_END) & (df["date"] <= VALID_END)].copy()
    te = df[(df["date"] >= test_start) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info(f"train {len(tr):,} / valid {len(va):,} / test {len(te):,} 行"
                f"（test {te['race_id'].nunique():,} レース）")
    if te.empty:
        logger.error("test が空です")
        sys.exit(1)

    y_top3_tr = (tr["finish_position"] <= 3).astype(int).values
    y_top3_va = (va["finish_position"] <= 3).astype(int).values
    y_win_tr = (tr["finish_position"] == 1).astype(int).values
    y_win_va = (va["finish_position"] == 1).astype(int).values
    y_out_tr = (tr["finish_position"] >= args.out_threshold).astype(int).values
    y_out_va = (va["finish_position"] >= args.out_threshold).astype(int).values

    logger.info("bin_top3 学習")
    p_top3 = train_binary(tr, va, te, y_top3_tr, y_top3_va, feats, seeds)
    logger.info("bin_win 学習")
    p_win = train_binary(tr, va, te, y_win_tr, y_win_va, feats, seeds)
    logger.info(f"p_out 学習（着外={args.out_threshold}着以下）")
    p_out = train_binary(tr, va, te, y_out_tr, y_out_va, feats, seeds)
    logger.info("reg_rank 学習")
    s_reg = train_reg_rank(tr, va, te, feats, seeds)
    logger.info("lambdarank 学習")
    s_lmr = train_lambdarank(tr, va, te, feats, seeds)

    z_reg = z_in_race(te, s_reg)
    z_out = z_in_race(te, p_out)
    z_t3 = z_in_race(te, p_top3)

    candidates: dict[str, np.ndarray] = {
        "bin_top3(現行と同目的)": p_top3,
        "bin_win": p_win,
        "neg_p_out": -p_out,
        "reg_rank": s_reg,
        "lambdarank": s_lmr,
        "blend reg-0.3out": z_reg - 0.3 * z_out,
        "blend reg-0.5out": z_reg - 0.5 * z_out,
        "blend reg-0.8out": z_reg - 0.8 * z_out,
        "blend reg+0.3t3-0.3out": z_reg + 0.3 * z_t3 - 0.3 * z_out,
    }

    # DB 本番 composite（in-sample 参考値）
    te_m = te.merge(prod, on=["race_id", "horse_id"], how="left")
    if te_m["composite_index"].notna().mean() > 0.9:
        candidates["prod_db(in-sample参考)"] = te_m["composite_index"].fillna(
            te_m["composite_index"].mean()).values
    else:
        logger.warning(f"DB composite の照合率が低い "
                       f"({te_m['composite_index'].notna().mean():.1%}) ため prod_db を除外")

    results = {name: evaluate(te, sc, args.out_threshold) for name, sc in candidates.items()}

    metrics = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3",
               "TAIL_bot3_out_rate", "TAIL_winner_in_bot3", "TAIL_placer_in_bot30pct",
               "ALL_spearman", "ALL_winner_pos", "ALL_placer_pos"]
    print("\n" + "=" * 130)
    print(f"地方 ランキング品質レビュー  test {test_start}〜{test_end} "
          f"({te['race_id'].nunique():,}R / {len(te):,}頭) 着外={args.out_threshold}着以下 "
          f"特徴{len(feats)}本")
    print("=" * 130)
    hdr = f"{'candidate':<26}" + "".join(f"{m.replace('HEAD_','').replace('TAIL_','').replace('ALL_',''):>14}"
                                         for m in metrics)
    print(hdr)
    print("-" * len(hdr))
    for name, r in results.items():
        print(f"{name:<26}" + "".join(f"{r[m]:>14.4f}" for m in metrics))
    print("\n凡例: 上位が良い = top1_win/top1_place/winner_in_top3/ndcg3/bot3_out_rate/spearman")
    print("      下位が良い = winner_in_bot3/placer_in_bot30pct/winner_pos/placer_pos")
    print("※ prod_db は model-vintage look-ahead を含む in-sample 値。honest 比較の対象外。")

    # ── paired bootstrap（bin_top3 = 現行と同じ目的関数 を基準にした差の有意性）──
    base_name = "bin_top3(現行と同目的)"
    pr = {name: per_race_metrics(te, sc) for name, sc in candidates.items()}
    boot: dict[str, dict[str, list[float]]] = {}
    print("\n" + "=" * 130)
    print(f"paired bootstrap（基準 = {base_name} / レース単位2000回リサンプル / 95%CI）")
    print("=" * 130)
    print(f"{'candidate':<26}" + "".join(f"{m:>34}" for m in ["top1_win", "top1_place", "spearman"]))
    for name in candidates:
        if name == base_name:
            continue
        cells, rec = "", {}
        for m in ["top1_win", "top1_place", "spearman"]:
            d, lo, hi = paired_bootstrap(pr[base_name], pr[name], m)
            sig = "*" if (lo > 0 or hi < 0) else " "
            cells += f"{d:>+11.4f} [{lo:>+7.4f},{hi:>+7.4f}]{sig}"
            rec[m] = [round(d, 5), round(lo, 5), round(hi, 5)]
        boot[name] = rec
        print(f"{name:<26}{cells}")
    print("* = 95%CI が 0 を跨がない（基準と有意差あり）")

    out = {
        "test_start": test_start, "test_end": test_end,
        "out_threshold": args.out_threshold, "n_features": len(feats),
        "n_races": int(te["race_id"].nunique()), "n_rows": int(len(te)),
        "seeds": seeds, "results": results,
        "paired_bootstrap_vs_bin_top3": boot,
    }
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    logger.info(f"保存: {args.json_out}")


if __name__ == "__main__":
    main()
