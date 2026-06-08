"""地方モデルに 馬場コンディション関連特徴を統合する A/B (5seed × 2cutoff 真OOS)。

確立した検証規律(ab_chihou_external_features.py と同型):
  cutoff再学習→held-out真OOS・複数seed平均(単一seedは当たりくじ)・両cutoffで頑健か。

現状診断: 本番26特徴では is_good/is_heavy/is_bad の gain 合計 ≈ 0.1%（ほぼ無効）。
原因 = (1)離散3フラグでグラデーション無視 (2)距離/脚質/馬個体適性との交互作用なし
       (3)コンディションの時計効果は speed_index の条件別par_timeで既に吸収済み。

追加特徴(全て発走前算出・リークなし point-in-time):
  track_wetness        : 良=0/稍=1/重=2/不=3 の順序値（グラデーション）
  wet_x_dist           : track_wetness ×(距離/1600 − 1)            馬場×距離
  pace_x_wet           : 先行度(prev_pace_ratio)×track_wetness     馬場×脚質
  horse_wet_apt        : 過去道悪走スコア − 過去全走スコア(≥2道悪走) 馬の道悪得手不得手
  horse_wet_apt_active : horse_wet_apt ×(現在 重/不 か)            適性×現在の馬場(明示交互)
  horse_wet_runs       : 過去道悪経験数 min(n,20)/20               確信度

スコア = 1 − (着順−1)/(頭数−1)（field正規化, 1=勝ち/0=最下位）。

評価(test=cutoff翌日〜): top1勝率(is_winヘッド rank1) / top1複勝率(is_top3 rank1) /
単勝ROI(rank1 by is_win) / ECE(is_win)。5seed 平均±std で baseline と比較。

使い方:
  cd backend
  PYTHONPATH=. .venv/bin/python scripts/ab_chihou_track_condition.py
  AB_CUTOFF=20250331 AB_TEST_START=20250401 PYTHONPATH=. .venv/bin/python scripts/ab_chihou_track_condition.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.ab_chihou_external_features import (  # noqa: E402
    EXT_FEATURES,
    MY_QUERY,
    _add_ext,
    _ece,
    _eval,
)
from scripts.train_chihou_prod_lgb import (  # noqa: E402
    FEATURES,
    NUM_ROUNDS,
    PARAMS,
    fetch_hist,
    prep,
)

CUTOFF = os.getenv("AB_CUTOFF", "20250630")
TEST_START = os.getenv("AB_TEST_START", "20250701")
TRAIN_START = os.getenv("AB_TRAIN_START", "20240101")
TEST_END = os.getenv("AB_TEST_END", "20260607")
SEEDS = [0, 1, 2, 3, 4]

# 本番26特徴(FEATURES) には外部指数5本(EXT_FEATURES)が含まれる。FEATURES は
# prep() で算出される 21本 + add_external_features で算出される 5本。
# baseline は本番と同一の FEATURES、augmented はそこに馬場特徴を加える。
# フル6特徴。AB_LEAN=1 で「効いている4本」に絞る(track_wetness/wet_x_dist は gain≈0 のため除外)。
TRACK_FEATURES_FULL = [
    "track_wetness", "wet_x_dist", "pace_x_wet",
    "horse_wet_apt", "horse_wet_apt_active", "horse_wet_runs",
]
TRACK_FEATURES_LEAN = [
    "pace_x_wet", "horse_wet_apt", "horse_wet_apt_active", "horse_wet_runs",
]
TRACK_FEATURES = TRACK_FEATURES_LEAN if os.getenv("AB_LEAN") == "1" else TRACK_FEATURES_FULL

WET_CONDS = ("重", "不")
WETNESS_MAP = {"良": 0.0, "稍": 1.0, "重": 2.0, "不": 3.0}

# 道悪適性の point-in-time 算出用履歴（condition 込み）
HIST_COND_QUERY = """
SELECT rr.horse_id, r.id AS race_id, r.date, r.condition,
       rr.finish_position, r.head_count
FROM chihou.race_results rr
JOIN chihou.races r ON r.id = rr.race_id
WHERE r.course != '83'
  AND r.date >= '20220101'
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.horse_id, r.date, r.id
"""


def _conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))


def fetch_hist_cond(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(HIST_COND_QUERY)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def _compute_wet_apt_table(hist: pd.DataFrame) -> pd.DataFrame:
    """履歴から馬の道悪適性を point-in-time(現走前累積)で算出し
    (horse_id, race_id) -> [horse_wet_apt, horse_wet_runs] を返す。"""
    h = hist.copy()
    h["fp"] = pd.to_numeric(h["finish_position"], errors="coerce")
    h["hc"] = pd.to_numeric(h["head_count"], errors="coerce")
    h["score"] = (1.0 - (h["fp"] - 1.0) / (h["hc"] - 1.0)).clip(0.0, 1.0)
    h.loc[h["hc"] < 2, "score"] = np.nan
    h["wet"] = h["condition"].isin(WET_CONDS).astype(float)
    h = h.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    g = h.groupby("horse_id")
    # 現走前累積（現走分を引く）。score 欠損行は集計から除外するため有効フラグで処理。
    h["valid"] = h["score"].notna().astype(float)
    h["s"] = h["score"].fillna(0.0)
    h["all_cnt"] = g["valid"].cumsum() - h["valid"]
    h["all_sum"] = g["s"].cumsum() - h["s"]
    h["wscore"] = h["s"] * h["wet"]
    h["wet_valid"] = h["valid"] * h["wet"]
    h["wet_cnt"] = g["wet_valid"].cumsum() - h["wet_valid"]
    h["wet_sum"] = g["wscore"].cumsum() - h["wscore"]
    base = h["all_sum"] / h["all_cnt"].clip(lower=1)
    wetperf = h["wet_sum"] / h["wet_cnt"].clip(lower=1)
    h["horse_wet_apt"] = np.where(h["wet_cnt"] >= 2, (wetperf - base).clip(-1.0, 1.0), 0.0)
    h["horse_wet_runs"] = (h["wet_cnt"].clip(upper=20) / 20.0)
    return h.set_index(["horse_id", "race_id"])[["horse_wet_apt", "horse_wet_runs"]]


def add_track_features(df: pd.DataFrame, apt_tbl: pd.DataFrame) -> pd.DataFrame:
    """df(prep 済・prev_pace_ratio 含む) に馬場関連特徴を付与する。"""
    df = df.copy()
    cond = df["condition"].fillna("").astype(str)
    df["track_wetness"] = cond.map(WETNESS_MAP).fillna(1.0)  # 不明→稍(中立)
    dist = pd.to_numeric(df["distance"], errors="coerce").fillna(1600.0)
    df["wet_x_dist"] = df["track_wetness"] * (dist / 1600.0 - 1.0)
    # prev_pace_ratio: 0〜1(小=先行), 不明=-1 → 中立 0.5
    pace = pd.to_numeric(df["prev_pace_ratio"], errors="coerce")
    pace = pace.where(pace >= 0, 0.5)
    df["pace_x_wet"] = pace * df["track_wetness"]
    # 馬の道悪適性（point-in-time）
    df = df.join(apt_tbl, on=["horse_id", "race_id"], how="left")
    df["horse_wet_apt"] = df["horse_wet_apt"].fillna(0.0)
    df["horse_wet_runs"] = df["horse_wet_runs"].fillna(0.0)
    df["horse_wet_apt_active"] = df["horse_wet_apt"] * (df["track_wetness"] >= 2).astype(float)
    return df


def _my_fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(MY_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def main() -> None:
    conn = _conn()
    df_hist = fetch_hist(conn)
    apt_tbl = _compute_wet_apt_table(fetch_hist_cond(conn))
    print(f"データ取得中... cutoff={CUTOFF} test={TEST_START}〜{TEST_END}")
    tr = add_track_features(_add_ext(prep(conn, _my_fetch(conn, TRAIN_START, CUTOFF), df_hist)), apt_tbl)
    te = add_track_features(_add_ext(prep(conn, _my_fetch(conn, TEST_START, TEST_END), df_hist)), apt_tbl)
    conn.close()

    # 道悪割合・適性カバレッジを表示
    wet_tr = (tr["track_wetness"] >= 2).mean()
    apt_cov = (te["horse_wet_apt"] != 0).mean()
    print(f"train {tr['race_id'].nunique()}R / test {te['race_id'].nunique()}R "
          f"| train道悪率={wet_tr:.1%} test道悪適性カバレッジ={apt_cov:.1%}")

    fp_tr = pd.to_numeric(tr["finish_position"], errors="coerce")
    y_win = (fp_tr == 1).astype(int).values
    y_top3 = (fp_tr <= 3).astype(int).values

    # 道悪レース(重/不)限定の test サブセット（ユーザー関心の本丸）
    wet_race_ids = set(te.loc[te["track_wetness"] >= 2, "race_id"].unique())
    te_wet = te[te["race_id"].isin(wet_race_ids)].copy()

    feat_sets = {"baseline": FEATURES, "augmented": FEATURES + TRACK_FEATURES}
    results = {k: [] for k in feat_sets}
    results_wet = {k: [] for k in feat_sets}
    gains = {f: [] for f in TRACK_FEATURES}

    # LightGBM のマルチスレッド非決定性(ラン間で top1 が ±0.25pt 揺れる)を排除し、
    # seed-std が真の分散を表すようにする。これをしないと small-Δ の判定が信用できない。
    det = {"deterministic": True, "force_row_wise": True, "num_threads": 1}
    for seed in SEEDS:
        params = dict(PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed, **det)
        for name, feats in feat_sets.items():
            Xtr = tr[feats].values.astype(float)
            Xte = te[feats].values.astype(float)
            Xte_w = te_wet[feats].values.astype(float)
            mw = lgb.train(params, lgb.Dataset(Xtr, y_win, feature_name=feats), num_boost_round=NUM_ROUNDS)
            m3 = lgb.train(params, lgb.Dataset(Xtr, y_top3, feature_name=feats), num_boost_round=NUM_ROUNDS)
            results[name].append(_eval(te, mw.predict(Xte), m3.predict(Xte)))
            results_wet[name].append(_eval(te_wet, mw.predict(Xte_w), m3.predict(Xte_w)))
            if name == "augmented":
                imp = dict(zip(feats, mw.feature_importance(importance_type="gain")))
                tot = sum(imp.values()) or 1
                for f in TRACK_FEATURES:
                    gains[f].append(100 * imp[f] / tot)
        print(f"  seed {seed} 完了")

    print(f"\n{'='*72}\n5seed平均 (test OOS {TEST_START}〜) baseline vs augmented(+馬場特徴)\n{'='*72}")
    metrics = [("top1勝率%", "win_pct", "+"), ("top1複勝率%", "place_pct", "+"),
               ("単勝ROI", "win_roi", "+"), ("ECE(is_win)", "ece", "-")]
    for label, key, good in metrics:
        b = np.array([r[key] for r in results["baseline"]])
        a = np.array([r[key] for r in results["augmented"]])
        delta = a.mean() - b.mean()
        improved = (delta > 0) if good == "+" else (delta < 0)
        per_seed = [(a[i] - b[i]) if good == "+" else (b[i] - a[i]) for i in range(len(SEEDS))]
        n_better = sum(1 for x in per_seed if x > 0)
        sig = "★std超" if abs(delta) > max(b.std(), a.std()) and improved else ("◯" if improved else "✗")
        print(f"  {label:13} base {b.mean():.3f}±{b.std():.3f} → aug {a.mean():.3f}±{a.std():.3f} "
              f"| Δ={delta:+.3f} ({n_better}/{len(SEEDS)}seed改善) {sig}")
    print(f"\n{'-'*72}\n道悪レース限定(重/不, test {te_wet['race_id'].nunique()}R) baseline vs augmented\n{'-'*72}")
    for label, key, good in metrics:
        b = np.array([r[key] for r in results_wet["baseline"]])
        a = np.array([r[key] for r in results_wet["augmented"]])
        delta = a.mean() - b.mean()
        improved = (delta > 0) if good == "+" else (delta < 0)
        per_seed = [(a[i] - b[i]) if good == "+" else (b[i] - a[i]) for i in range(len(SEEDS))]
        n_better = sum(1 for x in per_seed if x > 0)
        sig = "★std超" if abs(delta) > max(b.std(), a.std()) and improved else ("◯" if improved else "✗")
        print(f"  {label:13} base {b.mean():.3f}±{b.std():.3f} → aug {a.mean():.3f}±{a.std():.3f} "
              f"| Δ={delta:+.3f} ({n_better}/{len(SEEDS)}seed改善) {sig}")

    print("\n馬場特徴 gain%(is_winヘッド, 5seed平均):")
    for f in TRACK_FEATURES:
        print(f"  {f:22} {np.mean(gains[f]):.2f}%")
    print("\n判定基準: 主目標=top1勝率/複勝率が 全seed改善(≥4/5)かつΔ>std で本採用候補。")


if __name__ == "__main__":
    main()
