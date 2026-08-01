"""JRA: 1着率 / 3着内率 / 着外率(6着以下) の3ヘッド化と「着外率フィルタ × 不人気馬」ROI検証

目的（ユーザー要件, 2026-08-02）:
  1. 各レースの条件・各馬のパラメータ（オッズを使わない）から
     P(1着) / P(3着内) / P(着外=6着以下) を算出する
  2. 着外率が一定閾値を超える馬を除外する
  3. 残った「3着内に来そうな馬」のうち **人気薄** を拾って ROI が確保できるか検証する

honest 設計:
  - 特徴量にオッズ・人気・composite_index・win_probability を **一切使わない**
    （composite_index / win_probability は v26 LGB の出力＝2023-05〜2025-06 で学習した
      モデルを過去に遡って適用しており model-vintage look-ahead を含むため）
  - sub-indices は各 calculator が Race.date < 当該レース日 で過去走のみ参照するルールベース
    （pedigree の種牡馬統計・frame_bias のコースバイアスのみ全期間集計＝弱い集計リーク）
  - train ≤ 2025-06-30 / valid 2025-07-01〜2025-12-31 / test 2026-01-01〜 の時系列分割
  - test はさらに前半・後半に割って再現性を確認する

使い方:
    cd backend
    .venv/bin/python scripts/jra_out_rate_filter_backtest.py
    .venv/bin/python scripts/jra_out_rate_filter_backtest.py --seeds 42,123,456
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("out_rate_filter")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

SUBINDEX_FEATURES = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]
RACE_FEATURES = ["distance", "head_count", "is_turf", "is_dirt", "is_jump",
                 "is_good", "is_yaya", "is_heavy", "is_bad", "is_g1g2g3"]
HORSE_FEATURES = ["frame_number", "horse_age", "weight_carried", "horse_weight",
                  "weight_change", "jvan_time_dm", "jvan_battle_dm"]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES

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
    rr.weight_change, rr.abnormality_code,
    rr.finish_position, rr.win_odds, rr.place_odds, rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r        ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = 26
  AND r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def load_df(start: str, end: str) -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"取得: {len(df):,}行 / {df['race_id'].nunique():,}レース")
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
    for c in ALL_FEATURES + ["finish_position", "win_odds", "place_odds",
                             "win_popularity", "abnormality_code"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def train_heads(
    tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame, seeds: list[int]
) -> dict[str, np.ndarray]:
    """3ヘッド（win / top3 / out）を学習し test の予測確率を返す（seed平均）。"""
    labels = {
        "p_win": (tr["finish_position"] == 1, va["finish_position"] == 1),
        "p_top3": (tr["finish_position"] <= 3, va["finish_position"] <= 3),
        "p_out": (tr["finish_position"] >= 6, va["finish_position"] >= 6),
    }
    Xtr, Xva, Xte = tr[ALL_FEATURES].values, va[ALL_FEATURES].values, te[ALL_FEATURES].values
    out: dict[str, np.ndarray] = {}
    for name, (ytr, yva) in labels.items():
        preds = []
        best_iters = []
        for seed in seeds:
            params = dict(
                objective="binary", metric="binary_logloss",
                learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
                feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
                num_threads=os.cpu_count() or 4,
            )
            dtr = lgb.Dataset(Xtr, label=ytr.astype(int), feature_name=ALL_FEATURES)
            dva = lgb.Dataset(Xva, label=yva.astype(int), reference=dtr)
            m = lgb.train(
                params, dtr, num_boost_round=2000, valid_sets=[dva],
                callbacks=[lgb.early_stopping(100, verbose=False)],
            )
            best_iters.append(m.best_iteration)
            preds.append(m.predict(Xte, num_iteration=m.best_iteration))
        out[name] = np.mean(preds, axis=0)
        logger.info(f"{name}: best_iter={best_iters} 学習完了 "
                    f"(base_rate train={ytr.mean():.3f})")
    return out


def roi_block(sel: pd.DataFrame) -> dict:
    """選択馬群の単勝・複勝 ROI と的中率を返す。"""
    n = len(sel)
    if n == 0:
        return {"n": 0}
    win_hit = int((sel["finish_position"] == 1).sum())
    top3_hit = int((sel["finish_position"] <= 3).sum())
    win_ret = float(sel.loc[sel["finish_position"] == 1, "win_odds"].fillna(0).sum())
    # 複勝: place_odds は 3着以内のみ格納。7頭以下は2着まで払戻
    placed = sel[(sel["finish_position"] <= 3) & (sel["place_odds"].notna())]
    place_ret = float(placed["place_odds"].sum())
    n_place_known = int(sel["place_odds"].notna().sum())
    return {
        "n": n,
        "win_hit": win_hit,
        "win_rate": round(win_hit / n, 4),
        "win_roi": round(win_ret / n, 4),
        "top3_hit": top3_hit,
        "top3_rate": round(top3_hit / n, 4),
        "place_roi": round(place_ret / n, 4),
        "n_place_paid": n_place_known,
        "avg_pop": round(float(sel["win_popularity"].mean()), 2),
        "avg_odds": round(float(sel["win_odds"].median()), 1),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20261231")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--use-cached-preds", action="store_true",
                   help="models/jra_out_rate_test_preds.parquet を再利用して再学習を省く")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    df = load_df(args.start, args.end)
    df = featurize(df)

    # 取消・除外・結果なしを除去
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df[df["win_odds"].notna()]
    df = df.reset_index(drop=True)
    logger.info(f"クリーニング後: {len(df):,}行 / {df['race_id'].nunique():,}レース")

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end].reset_index(drop=True)
    logger.info(
        f"train={len(tr):,}行/{tr['race_id'].nunique():,}R  "
        f"valid={len(va):,}行/{va['race_id'].nunique():,}R  "
        f"test={len(te):,}行/{te['race_id'].nunique():,}R "
        f"({te['date'].min()}〜{te['date'].max()})"
    )

    dump_path = MODELS_DIR / "jra_out_rate_test_preds.pkl"
    if args.use_cached_preds and dump_path.exists():
        logger.info(f"キャッシュ済み予測を使用: {dump_path}")
        te = pd.read_pickle(dump_path)
    else:
        preds = train_heads(tr, va, te, seeds)
        for k, v in preds.items():
            te[k] = v
        te.to_pickle(dump_path)
        logger.info(f"test予測を保存: {dump_path}")

    # レース内順位・正規化
    te["r_top3"] = te.groupby("race_id")["p_top3"].rank(ascending=False, method="min")
    te["r_out"] = te.groupby("race_id")["p_out"].rank(ascending=True, method="min")
    te["r_win"] = te.groupby("race_id")["p_win"].rank(ascending=False, method="min")

    report: dict = {"test_period": [te["date"].min(), te["date"].max()],
                    "n_rows": len(te), "n_races": int(te["race_id"].nunique())}

    print("\n" + "=" * 92)
    print("【0】ベースライン: test 全馬 / 人気別")
    print("=" * 92)
    print(f"{'区分':<28}{'n':>7}{'勝率':>8}{'単ROI':>8}{'複率':>8}{'複ROI':>8}{'平均人気':>9}")
    base_rows = [("全馬", te)]
    for lo, hi in [(1, 3), (4, 6), (7, 9), (10, 99)]:
        base_rows.append((f"人気{lo}-{hi if hi < 99 else '~'}",
                          te[te["win_popularity"].between(lo, hi)]))
    for label, sub in base_rows:
        s = roi_block(sub)
        if s["n"]:
            print(f"{label:<28}{s['n']:>7}{s['win_rate']:>8.3f}{s['win_roi']:>8.3f}"
                  f"{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}{s['avg_pop']:>9.2f}")

    print("\n" + "=" * 92)
    print("【1】着外率(p_out)フィルタ単体の効き: p_out 十分位ごと")
    print("=" * 92)
    te["out_decile"] = pd.qcut(te["p_out"], 10, labels=False, duplicates="drop") + 1
    print(f"{'decile':<10}{'p_out範囲':<20}{'n':>7}{'勝率':>8}{'単ROI':>8}{'複率':>8}{'複ROI':>8}{'平均人気':>9}")
    for d in sorted(te["out_decile"].dropna().unique()):
        sub = te[te["out_decile"] == d]
        s = roi_block(sub)
        rng = f"{sub['p_out'].min():.3f}-{sub['p_out'].max():.3f}"
        print(f"{int(d):<10}{rng:<20}{s['n']:>7}{s['win_rate']:>8.3f}{s['win_roi']:>8.3f}"
              f"{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}{s['avg_pop']:>9.2f}")

    print("\n" + "=" * 92)
    print("【2】本命題: 着外率フィルタ × 不人気馬 (test 全期間)")
    print("   条件: p_out <= θ  かつ  人気 >= P  （オッズ非使用モデル）")
    print("=" * 92)
    grid = []
    header = (f"{'p_out<=':>8}{'人気>=':>7}{'n':>7}{'勝率':>8}{'単ROI':>8}"
              f"{'複率':>8}{'複ROI':>8}{'中央オッズ':>10}")
    print(header)
    for th in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        for pop in [4, 5, 6, 7, 8]:
            sub = te[(te["p_out"] <= th) & (te["win_popularity"] >= pop)]
            s = roi_block(sub)
            if s["n"] < 30:
                continue
            grid.append({"th_out": th, "min_pop": pop, **s})
            print(f"{th:>8.2f}{pop:>7}{s['n']:>7}{s['win_rate']:>8.3f}{s['win_roi']:>8.3f}"
                  f"{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}{s['avg_odds']:>10.1f}")
    report["grid_out_pop"] = grid

    print("\n" + "=" * 92)
    print("【3】着外率フィルタ × 3着内率順位 × 不人気 (混戦回避を強めた版)")
    print("   条件: p_out <= θ かつ r_top3 <= k かつ 人気 >= P")
    print("=" * 92)
    grid2 = []
    print(f"{'p_out<=':>8}{'r_top3<=':>9}{'人気>=':>7}{'n':>7}{'勝率':>8}{'単ROI':>8}"
          f"{'複率':>8}{'複ROI':>8}{'中央オッズ':>10}")
    for th in [0.55, 0.60, 0.65, 0.70]:
        for k in [3, 4, 5]:
            for pop in [4, 5, 6]:
                sub = te[(te["p_out"] <= th) & (te["r_top3"] <= k)
                         & (te["win_popularity"] >= pop)]
                s = roi_block(sub)
                if s["n"] < 30:
                    continue
                grid2.append({"th_out": th, "k_top3": k, "min_pop": pop, **s})
                print(f"{th:>8.2f}{k:>9}{pop:>7}{s['n']:>7}{s['win_rate']:>8.3f}"
                      f"{s['win_roi']:>8.3f}{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}"
                      f"{s['avg_odds']:>10.1f}")
    report["grid_out_rank_pop"] = grid2

    print("\n" + "=" * 92)
    print("【4】オッズ帯で切った版 (人気順位でなく実オッズ)")
    print("   条件: p_out <= θ かつ 単勝オッズ ∈ [lo, hi)")
    print("=" * 92)
    grid3 = []
    print(f"{'p_out<=':>8}{'odds帯':>12}{'n':>7}{'勝率':>8}{'単ROI':>8}{'複率':>8}{'複ROI':>8}")
    for th in [0.55, 0.60, 0.65, 0.70]:
        for lo, hi in [(5, 10), (10, 20), (20, 50), (10, 50), (7, 30)]:
            sub = te[(te["p_out"] <= th) & te["win_odds"].between(lo, hi, inclusive="left")]
            s = roi_block(sub)
            if s["n"] < 30:
                continue
            grid3.append({"th_out": th, "odds_lo": lo, "odds_hi": hi, **s})
            print(f"{th:>8.2f}{f'[{lo},{hi})':>12}{s['n']:>7}{s['win_rate']:>8.3f}"
                  f"{s['win_roi']:>8.3f}{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}")
    report["grid_out_oddsband"] = grid3

    # ------------------------------------------------------------------
    # 【5】再現性: test を前半/後半に分割して上位候補を再評価
    # ------------------------------------------------------------------
    mid = te["date"].sort_values().iloc[len(te) // 2]
    te_a, te_b = te[te["date"] <= mid], te[te["date"] > mid]
    print("\n" + "=" * 92)
    print(f"【5】再現性チェック: test前半({te_a['date'].min()}〜{mid}) vs "
          f"後半({te_b['date'].min()}〜{te_b['date'].max()})")
    print("=" * 92)
    cands = sorted(grid + grid2, key=lambda x: -x["win_roi"])[:8]
    print(f"{'条件':<40}{'期':>4}{'n':>6}{'勝率':>8}{'単ROI':>8}{'複ROI':>8}")
    repro = []
    for c in cands:
        def _apply(d: pd.DataFrame) -> pd.DataFrame:
            m = (d["p_out"] <= c["th_out"]) & (d["win_popularity"] >= c["min_pop"])
            if "k_top3" in c:
                m &= d["r_top3"] <= c["k_top3"]
            return d[m]
        label = (f"p_out<={c['th_out']:.2f} pop>={c['min_pop']}"
                 + (f" r_top3<={c['k_top3']}" if "k_top3" in c else ""))
        row = {"cond": label, "full": {k: c[k] for k in ("n", "win_roi", "place_roi")}}
        for tag, d in (("前", te_a), ("後", te_b)):
            s = roi_block(_apply(d))
            if s["n"] == 0:
                continue
            row[tag] = s
            print(f"{label:<40}{tag:>4}{s['n']:>6}{s['win_rate']:>8.3f}"
                  f"{s['win_roi']:>8.3f}{s['place_roi']:>8.3f}")
        repro.append(row)
    report["repro"] = repro

    out_path = MODELS_DIR / "jra_out_rate_filter_backtest.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {out_path}")


if __name__ == "__main__":
    main()
