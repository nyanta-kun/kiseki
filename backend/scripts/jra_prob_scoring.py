"""JRA 確率スコアリング — 単勝の proper scoring rule と市場ベースラインの並置

事前登録 `docs/jra_winplace_structure_plan_2026_09_04.md` §5 Phase B の実装。

## なぜ作るか

現行 `scripts/jra_rank_quality_review.py` の指標は top1勝率 / NDCG@3 / Spearman、
`models/v26_iswin_calib_metrics.json` の指標は ECE / Brier しかない。

  - **単勝率の精度を測る proper scoring rule（レース単位の多項対数損失）が無い。**
    top1勝率は n=2,046 で SE ≈ 1.0pt あり、0.5pt の改善を判定できない。
  - **同一窓の市場ベースラインを並置した表がリポジトリに存在しない。**
    ユーザーの「単勝率が市場を超えていない」を数字で固定できていない。
  - 複勝側は `HEAD_top1_place`（指数1位馬の複勝率）しかなく、**指数1位の馬しか
    見ないため「単勝順では下位だが複勝順では上位」という順位の交差を原理的に
    検出できない**（計画 §2.1）。

## 指標の定義

### 単勝側

| キー | 定義 |
|---|---|
| `mnl_logloss` | レース単位の多項対数損失 `-mean_race log p(実際の勝ち馬)`。1レース1事象の proper scoring rule。単位は nat。低いほど良い |
| `uniform_logloss` | 同じレース集合で一様分布 `1/n` を置いたときの多項対数損失 `mean_race log n` |
| `info_gain_nats` | `uniform_logloss - mnl_logloss`。頭数差を吸収するので窓をまたいで比較できる。正なら一様分布より情報がある |
| `info_gain_pct` | `info_gain_nats / uniform_logloss`（一様分布からの相対改善） |
| `top1_win_rate` | 予測1位馬の勝率（既存指標との接続用） |
| `reliability` | 予測確率の10分位 × 実測勝率・n（ECE は p<0.05 帯に質量が偏って上位帯の崩れを隠すため必ず併記する） |

### 複勝側（`place_slots` ごとに分けて出す）

`place_slots = 3 (n>=8) / 2 (5<=n<=7) / 0 (n<5)`。n はレースの**出走頭数**
（`abnormality_code ∈ {1,2}` を除いた行数）。`place_slots = 0` のレースは除外。

| キー | 定義 |
|---|---|
| `coverage_at_k` | 複勝確率の上位 k=place_slots 頭に、実際の3着内（= 着順 <= place_slots）馬が何頭入るかの割合 |
| `place_logloss` | `is_top3` に対する二値対数損失（馬単位の平均） |
| `spearman_in_race` | レース内 Spearman（複勝確率の順位 vs `is_top3` フラグ） |
| `cross_races` / `cross_pairs` | **単勝順位と複勝順位の交差**。現行の複勝は `_harville_place_probs(win_probs)` で単勝確率の単調変換なので理論上ゼロ。**ゼロであることを実測で確認するのが目的**（ゼロなら「現行は交差を出せない」の実証になる）。⚠️ 判定は `rank()` ではなく生の値の差を `CROSS_TOL` 付きで比べる（同値ペアの tie-break が偽の交差になるため・実測13件） |
| `tied_pairs` | 単勝確率か複勝確率が `CROSS_TOL` 以内で同値だったペア数（交差判定から除いた分） |

### 市場ベースライン（🔴 評価にのみ使う。特徴量には絶対に入れない）

市場含意確率 `p_i = (1/odds_i) / Σ_j (1/odds_j)`（控除率を Σ=1 で吸収）。

- 🔴 **発走前オッズから作る。** 確定人気 `race_results.win_popularity` も確定オッズも
  使わない（発走10分前の1番人気が確定と一致するのは 80.7%・`CLAUDE.md:1327`。
  代用すると別物の比較になる）。
  `keiba.odds_history` の `bet_type='win'`・発走時刻より前の**最後の**スナップショット
  （既定で発走 60 分前以内）を使う。
- ⚠️ `odds_history.fetched_at` は **naive UTC**、`races.post_time` は **JST**。
  本スクリプトは `SET TIME ZONE 'Asia/Tokyo'` 下で `to_timestamp()` してから
  `AT TIME ZONE 'UTC'` で naive UTC に落として比較する。`now()` とは比較しない。
- 🔴 **`odds_history` は 2026-03-28 より前が存在しない。**
  それ以前の日付に付いている win 行は後から取り込んだ確定オッズであって発走前ではない。
  よって **2025（探索窓）には市場ベースラインが作れない**（JSON では `null` ＋理由）。
  確認窓の市場比較は **2026-04-01〜2026-08-01** に限定する（`MARKET_WINDOWS`）。
- 市場比較は必ず「その窓」「発走前オッズが取れたレース数と母数に対する割合」
  「取れたレースだけの部分集合であること」を JSON と標準出力の両方に書く。
- `--max-lead-min` の感度（2026-09-04 実測・確認窓）: 既定 60分 → 961/1188R (80.9%)・
  Δ(モデル−市場)=+0.164 [+0.125, +0.202]。720分 → 1116/1188R (93.9%)・
  Δ=+0.151 [+0.118, +0.185]。**結論は動かない。**
  60分に落ちるのは 2026-04 だけ（84.9% → 40.2%）で、当時は直前の巡回が無く
  午前の板しか残っていないため。既定は「発走直前の板」を優先して 60分にしてある。

## モデル確率の作り方

🔴 **DB の `calculated_indices.win_probability` は使わない。** 出荷モデルは
`jra_protocol.TRAIN_DATA_END` までで学習されており過去分は in-sample
（`composite.py:271-278` に実害の記録: 訓練内 0.43 / 訓練外 0.26）。

`jra_rank_quality_review.py` と同じ walk-forward で is_win ヘッドを組み直す:

| 窓 | train | valid | test | 市場比較 |
|---|---|---|---|---|
| 2025（探索） | 〜20240630 | 20240701〜20241231 | 20250101〜20251231 | **不可（発走前オッズ 0件）** |
| 2026（確認） | 〜20250630 | 20250701〜20251231 | 20260104〜20260801 | 20260401〜20260801 |

- 特徴量 34列 = `composite.OUT_PROB_FEATURE_NAMES`（オッズ・人気は含まない）
- ハイパラは `scripts/train_jra_iswin_head.py` の `PARAMS` を import して共有
- seed 平均（既定 42,123,456）・valid で early stopping
- 生出力をレース内で L1 正規化（`composite.py:737-741` の本番と同じ）
- 複勝確率は本番と同じ `CompositeIndexCalculator._harville_place_probs` を import して算出
  （🔴 独立実装をしない）

## 使い方

    cd backend
    .venv/bin/python scripts/jra_prob_scoring.py --window 2025
    .venv/bin/python scripts/jra_prob_scoring.py --window 2026

出力: `docs/model_verification/jra_prob_scoring_<window>.json`（冪等・上書き）
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

from src.indices.composite import (  # noqa: E402
    OUT_PROB_FEATURE_NAMES,
    SUBINDEX_SOURCE_SQL,
    CompositeIndexCalculator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prob_scoring")

FEATURES = OUT_PROB_FEATURE_NAMES
OUT_DIR = _root.parent / "docs" / "model_verification"

# 窓定義: (train_end, valid_end, test_start, test_end)
WINDOWS = {
    "2025": ("20240630", "20241231", "20250101", "20251231"),
    "2026": ("20250630", "20251231", "20260104", "20260801"),
}

# 🔴 市場ベースラインを出せる窓（2026-09-04 実測・事前登録 §3 への訂正）
#
# `keiba.odds_history` は **2026-03-28 01:38:42 より前の行が存在しない**。
# それ以前の日付に付いている win 行は 2026 年に取り込んだ**発走後（確定）オッズ**であり、
# 発走前スナップショットではない（実測: 2025 年の任意のレースで
# `odds_history.odds` が `race_results.win_odds` と完全一致する）。
#
# 発走前スナップショットのあるレースの割合（JRA・実測）:
#   2025年 0.00% / 2026-01 0% / 02 0% / 03 22.0% / 04 84.9% / 05 100% / 06 89.0%
#   / 07 100% / 08 99.2%
#
# したがって:
#   - **探索窓 2025 では市場ベースラインを一切作れない**（`None`）。
#     確定人気 `race_results.win_popularity` や確定オッズでの代用は **しない**
#     （発走10分前の1番人気が確定と一致するのは 80.7%・`CLAUDE.md:1327`。別物の比較になる）
#   - 確認窓の市場比較は実用水準に達する **2026-04-01〜2026-08-01** に限定する
MARKET_WINDOWS: dict[str, tuple[str, str] | None] = {
    "2025": None,
    "2026": ("20260401", "20260801"),
}

JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# 交差判定の許容誤差。これ未満の差は「同値」とみなす（浮動小数の偽陽性よけ・下の注記参照）
CROSS_TOL = 1e-9

FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, r.post_time, ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.horse_number, re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position, rr.win_odds
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.course IN %(courses)s
"""

# 発走前オッズ: 発走時刻より前で **最後の** スナップショットを1レース1枚だけ取る。
#
#   - `post_time` は JST。セッション TZ を `Asia/Tokyo` に明示 SET したうえで
#     `to_timestamp()` し、`AT TIME ZONE 'UTC'` で **naive UTC** に落とす。
#     `odds_history.fetched_at` も naive UTC なので、そのまま素の比較ができる。
#   - 🔴 `now()` とは比較しない（CLAUDE.md「タイムゾーンが列ごとに違う」）。
#   - ⚠️ `fetched_at` 側に式を掛けると `ix_odds_history_race_bet_type_fetched_at` が
#     効かず 8,300 万行を全走査して10分以上かかる。変換は必ず races 側に寄せ、
#     LATERAL + LIMIT 1 で1レース1枚だけ引く。
PRERACE_ODDS_SQL = """
WITH tgt AS (
    SELECT id,
           to_timestamp(date || post_time, 'YYYYMMDDHH24MI') AT TIME ZONE 'UTC' AS post_utc
    FROM keiba.races
    WHERE course IN %(courses)s
      AND date BETWEEN %(start)s AND %(end)s
      AND post_time IS NOT NULL AND post_time <> ''
), sel AS (
    SELECT t.id AS race_id, s.fetched_at,
           EXTRACT(EPOCH FROM (t.post_utc - s.fetched_at)) / 60.0 AS lead_min
    FROM tgt t
    CROSS JOIN LATERAL (
        SELECT oh.fetched_at
        FROM keiba.odds_history oh
        WHERE oh.race_id = t.id
          AND oh.bet_type = 'win'
          AND oh.fetched_at < t.post_utc
          AND oh.fetched_at >= t.post_utc - (%(max_lead)s * INTERVAL '1 minute')
        ORDER BY oh.fetched_at DESC
        LIMIT 1
    ) s
)
SELECT sel.race_id, sel.fetched_at, sel.lead_min, oh.combination, oh.odds
FROM sel
JOIN keiba.odds_history oh
     ON oh.race_id = sel.race_id AND oh.bet_type = 'win' AND oh.fetched_at = sel.fetched_at
"""


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def _connect():
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    # to_timestamp() が JST 解釈になることを明示的に保証する
    cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    cur.close()
    return conn


def _query(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    """`jra_rank_quality_review.featurize` / `jra_calibration_ab.featurize` と同一の変換。"""
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
    num = FEATURES + ["finish_position", "abnormality_code", "win_odds", "horse_number"]
    for c in num:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df[FEATURES[:17]] = df[FEATURES[:17]].fillna(50.0)
    df["jvan_time_dm"] = df["jvan_time_dm"].fillna(50.0)
    df["jvan_battle_dm"] = df["jvan_battle_dm"].fillna(50.0)
    return df


# ---------------------------------------------------------------------------
# モデル（walk-forward で is_win ヘッドを組み直す）
# ---------------------------------------------------------------------------

def train_iswin(tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame,
                seeds: list[int]) -> tuple[np.ndarray, list[int]]:
    """is_win ヘッドを seed 平均で学習し te の生出力を返す。

    ハイパラは `scripts/train_jra_iswin_head.py` の PARAMS を import して共有する
    （そちらを直せば本スクリプトも追随する）。
    """
    from scripts.train_jra_iswin_head import MAX_ROUND, PARAMS

    Xtr, ytr = tr[FEATURES].values.astype(float), (tr["finish_position"] == 1).astype(int).values
    Xva, yva = va[FEATURES].values.astype(float), (va["finish_position"] == 1).astype(int).values
    Xte = te[FEATURES].values.astype(float)
    preds, iters = [], []
    for s in seeds:
        d = lgb.Dataset(Xtr, ytr, feature_name=FEATURES)
        dv = lgb.Dataset(Xva, yva, reference=d)
        m = lgb.train(dict(PARAMS, seed=s), d, num_boost_round=MAX_ROUND,
                      valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
        iters.append(int(m.best_iteration))
        preds.append(m.predict(Xte, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0), iters


def race_normalize(raw: np.ndarray, race_id: pd.Series) -> np.ndarray:
    """本番 `composite.py:737-741` と同じレース内 L1 正規化（Σ=1）。"""
    s = pd.Series(np.clip(raw, 1e-9, 1.0), index=race_id.index)
    return (s / s.groupby(race_id).transform("sum")).values


def harville_place(df: pd.DataFrame, win_col: str) -> np.ndarray:
    """本番 `CompositeIndexCalculator._harville_place_probs` をそのまま使う。

    🔴 独立実装をしない。レース単位で本番関数へ渡し、元の行順へ戻す。
    """
    out = np.empty(len(df), dtype=float)
    for _, idx in df.groupby("race_id", sort=False).indices.items():
        wp = df[win_col].values[idx].tolist()
        pp = CompositeIndexCalculator._harville_place_probs(wp)
        out[idx] = pp
    return out


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------

def reliability_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    """予測確率の10分位 × 実測勝率・n。"""
    d = pd.DataFrame({"p": p, "y": y}).dropna().sort_values("p").reset_index(drop=True)
    if not len(d):
        return []
    d["bin"] = (np.arange(len(d)) * n_bins // len(d)).clip(0, n_bins - 1)
    rows = []
    for b, g in d.groupby("bin"):
        rows.append({
            "decile": int(b) + 1, "n": int(len(g)),
            "pred_pct": round(float(g["p"].mean() * 100), 3),
            "actual_pct": round(float(g["y"].mean() * 100), 3),
            "gap_pct": round(float((g["y"].mean() - g["p"].mean()) * 100), 3),
        })
    return rows


def win_scores(df: pd.DataFrame, prob_col: str) -> dict:
    """レース単位の多項対数損失と一様分布への情報利得。

    勝ち馬がちょうど1頭のレースのみを使う（同着1着はレース単位の1事象にならない）。
    """
    per_race, uniform, skipped = [], [], 0
    for rid, g in df.groupby("race_id", sort=False):
        w = g[g["finish_position"] == 1]
        if len(w) != 1:
            skipped += 1
            continue
        p = float(w[prob_col].iloc[0])
        per_race.append(-np.log(max(p, 1e-12)))
        uniform.append(np.log(len(g)))
    per_race = np.asarray(per_race)
    uniform = np.asarray(uniform)
    if not len(per_race):
        return {"n_races": 0, "skipped_races": skipped}

    top1 = df.sort_values(prob_col, ascending=False).groupby("race_id", sort=False).head(1)
    y = (df["finish_position"] == 1).astype(int).values
    return {
        "n_races": int(len(per_race)),
        "skipped_races": int(skipped),
        "mnl_logloss": round(float(per_race.mean()), 5),
        "mnl_logloss_se": round(float(per_race.std(ddof=1) / np.sqrt(len(per_race))), 5),
        "uniform_logloss": round(float(uniform.mean()), 5),
        "info_gain_nats": round(float((uniform - per_race).mean()), 5),
        "info_gain_nats_se": round(float((uniform - per_race).std(ddof=1) / np.sqrt(len(per_race))), 5),
        "info_gain_pct": round(float((uniform - per_race).mean() / uniform.mean() * 100), 3),
        "top1_win_rate": round(float((top1["finish_position"] == 1).mean()), 4),
        "top1_place_rate": round(float((top1["finish_position"] <= top1["place_slots"]).mean()), 4),
        "top1_mean_pred": round(float(top1[prob_col].mean()), 4),
        "reliability": reliability_table(df[prob_col].values, y),
        "_per_race": per_race,      # bootstrap 用（JSON には出さない）
        "_race_ids": [rid for rid, g in df.groupby("race_id", sort=False)
                      if (g["finish_position"] == 1).sum() == 1],
    }


def paired_logloss_ci(a: dict, b: dict, n_boot: int = 2000, seed: int = 0) -> dict:
    """a - b の多項対数損失差を、レースクラスタ bootstrap で 95%CI 付きに。"""
    ra, rb = a.get("_race_ids"), b.get("_race_ids")
    if not ra or not rb:
        return {}
    sa = pd.Series(a["_per_race"], index=ra)
    sb = pd.Series(b["_per_race"], index=rb)
    common = sa.index.intersection(sb.index)
    d = (sa.loc[common] - sb.loc[common]).values
    if not len(d):
        return {}
    rng = np.random.default_rng(seed)
    boots = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)])
    return {
        "n_races": int(len(d)),
        "delta_logloss": round(float(d.mean()), 5),
        "ci95": [round(float(np.percentile(boots, 2.5)), 5),
                 round(float(np.percentile(boots, 97.5)), 5)],
    }


def place_scores(df: pd.DataFrame, place_col: str, win_col: str) -> dict:
    """複勝側の指標。呼び出し側で place_slots ごとに分けて渡すこと。"""
    if not len(df):
        return {"n_races": 0}
    k = int(df["place_slots"].iloc[0])
    y = (df["finish_position"] <= k).astype(int).values
    p = np.clip(df[place_col].values.astype(float), 1e-9, 1 - 1e-9)
    place_ll = float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())

    covs, sps, cross_pairs, cross_races, tied_pairs = [], [], 0, 0, 0
    for _, g in df.groupby("race_id", sort=False):
        gg = g.sort_values(place_col, ascending=False)
        hit = int((gg.head(k)["finish_position"] <= k).sum())
        covs.append(hit / k)
        yy = (g["finish_position"] <= k).astype(int).values
        if len(g) >= 3 and 0 < yy.sum() < len(yy):
            rho = spearmanr(g[place_col].values, yy).correlation
            if not np.isnan(rho):
                sps.append(float(rho))

        # 単勝順位 vs 複勝順位の交差（不一致ペア数）
        #
        # ⚠️ `rank(method="first")` で順位を作って比べてはいけない。
        # 単勝確率が完全同値（オッズが同値）の馬同士は、`_harville_place_probs` の
        # O(n^3) 加算の順序差で複勝確率の**最下位ビットだけ**がずれることがあり、
        # 同着の tie-break が単勝側と複勝側で逆転して「交差」に見える。
        # 実測（2026 窓の市場含意確率）でこの偽陽性が 13 レース出た。
        # よって生の値を **許容誤差つき** で比較する。
        w = g[win_col].values.astype(float)
        pv = g[place_col].values.astype(float)
        dw = w[:, None] - w[None, :]
        dp = pv[:, None] - pv[None, :]
        iu = np.triu_indices(len(g), 1)
        dw, dp = dw[iu], dp[iu]
        sig = (np.abs(dw) > CROSS_TOL) & (np.abs(dp) > CROSS_TOL)
        c = int(((dw * dp < 0) & sig).sum())
        tied_pairs += int((~sig).sum())
        cross_pairs += c
        cross_races += 1 if c else 0

    return {
        "place_slots": k,
        "n_races": int(df["race_id"].nunique()),
        "n_horses": int(len(df)),
        "coverage_at_k": round(float(np.mean(covs)), 4),
        "place_logloss": round(place_ll, 5),
        "spearman_in_race": round(float(np.mean(sps)), 4) if sps else None,
        "spearman_n_races": int(len(sps)),
        "cross_races": int(cross_races),
        "cross_pairs": int(cross_pairs),
        "tied_pairs": int(tied_pairs),
        "cross_tol": CROSS_TOL,
    }


# ---------------------------------------------------------------------------
# 市場
# ---------------------------------------------------------------------------

def attach_market(df: pd.DataFrame, odds_col: str, out_col: str) -> pd.DataFrame:
    """市場含意確率 `(1/odds)/Σ(1/odds)` をレース内で作る。

    レース内に odds 欠損 / <=0 の馬が1頭でもあればそのレースは市場側から落とす
    （部分集合で正規化すると Σ=1 が保証できないため）。
    """
    d = df.copy()
    o = pd.to_numeric(d[odds_col], errors="coerce")
    bad = o.isna() | (o <= 0)
    ok_race = ~d["race_id"].isin(d.loc[bad, "race_id"].unique())
    inv = np.where(ok_race, 1.0 / o.where(~bad, np.nan), np.nan)
    d["_inv"] = inv
    tot = d.groupby("race_id")["_inv"].transform("sum")
    d[out_col] = d["_inv"] / tot
    return d.drop(columns=["_inv"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_population(df: pd.DataFrame) -> pd.DataFrame:
    """計画 §3 の母集団を作る。"""
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    n = df.groupby("race_id")["race_id"].transform("size")
    df["n_runners"] = n
    df["place_slots"] = np.where(n >= 8, 3, np.where(n >= 5, 2, 0))
    return df


def show_one_race(df: pd.DataFrame, cols: dict, title: str) -> list[str]:
    """🔴 実データを1レース分そのまま表示して目視確認する。"""
    lines = [f"\n{'=' * 104}", title, "=" * 104]
    g = df
    hdr = f"{'馬番':>4}{'着順':>5}{'発走前O':>10}{'確定O':>9}"
    for label in cols.values():
        hdr += f"{label:>16}"
    lines.append(hdr)

    def _n(v, w, d=1):
        return f"{'-':>{w}}" if pd.isna(v) else f"{float(v):>{w}.{d}f}"

    for _, r in g.iterrows():
        row = (f"{int(r['horse_number']):>4}{int(r['finish_position']):>5}"
               f"{_n(r.get('pre_odds'), 10)}{_n(r.get('win_odds'), 9)}")
        for c in cols:
            row += _n(r[c], 16, 4)
        lines.append(row)
    tot = f"{'合計':>3}{'':>5}{'':>10}{'':>9}"
    for c in cols:
        tot += f"{float(g[c].sum()):>16.4f}"
    lines.append(tot)
    lines.append("（合計行: p_win と p_mkt はレース内 Σ=1 になっていること）")
    return lines


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--window", default="2026", choices=sorted(WINDOWS))
    p.add_argument("--test-start", default=None, help="窓の既定を上書きする (YYYYMMDD)")
    p.add_argument("--test-end", default=None, help="窓の既定を上書きする (YYYYMMDD)")
    p.add_argument("--market-start", default=None,
                   help="市場ベースラインの窓の開始 (YYYYMMDD)。既定は MARKET_WINDOWS")
    p.add_argument("--market-end", default=None,
                   help="市場ベースラインの窓の終了 (YYYYMMDD)。既定は MARKET_WINDOWS")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--max-lead-min", type=float, default=60.0,
                   help="発走前オッズとして採用する最大リードタイム（分）")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out-dir", default=str(OUT_DIR))
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    train_end, valid_end, test_start, test_end = WINDOWS[args.window]
    test_start = args.test_start or test_start
    test_end = args.test_end or test_end

    conn = _connect()
    logger.info("データ取得中 ...")
    raw = _query(conn, FETCH_SQL, {"courses": JRA_COURSES})
    raw["date"] = raw["date"].astype(str)
    df = build_population(featurize(raw))

    tr = df[df["date"] <= train_end].copy()
    va = df[(df["date"] > train_end) & (df["date"] <= valid_end)].copy()
    te = df[(df["date"] >= test_start) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info("train=%d/%dR valid=%d test=%d/%dR (%s〜%s)",
                len(tr), tr.race_id.nunique(), len(va), len(te), te.race_id.nunique(),
                te["date"].min(), te["date"].max())
    if not len(tr) or not len(va) or not len(te):
        raise SystemExit("train / valid / test のいずれかが空。窓の指定を確認すること")

    # --- モデル確率（walk-forward・DB の win_probability は使わない） ---
    logger.info("is_win ヘッドを walk-forward で学習 (seeds=%s) ...", seeds)
    raw_w, best_iters = train_iswin(tr, va, te, seeds)
    te["p_win"] = race_normalize(raw_w, te["race_id"])
    te["p_place"] = harville_place(te, "p_win")

    # --- 市場（発走前オッズのみ。確定オッズ・確定人気での代用はしない） ---
    mkt_win = MARKET_WINDOWS[args.window]
    if args.market_start or args.market_end:
        mkt_win = (args.market_start or test_start, args.market_end or test_end)
    if mkt_win is not None:
        m_start = max(mkt_win[0], test_start)
        m_end = min(mkt_win[1], test_end)
        logger.info("発走前オッズ取得 %s〜%s (max_lead=%.0f分) ...",
                    m_start, m_end, args.max_lead_min)
        od = _query(conn, PRERACE_ODDS_SQL, {"courses": JRA_COURSES, "start": m_start,
                                             "end": m_end, "max_lead": args.max_lead_min})
    else:
        m_start = m_end = None
        od = pd.DataFrame()
        logger.warning("窓 %s には発走前オッズが存在しない（odds_history は 2026-03-28 以降）。"
                       "市場ベースラインは出さない", args.window)
    conn.close()

    if len(od):
        od["horse_number"] = pd.to_numeric(od["combination"], errors="coerce")
        od["pre_odds"] = pd.to_numeric(od["odds"], errors="coerce")
        od = od.dropna(subset=["horse_number"])
        lead = od.groupby("race_id")["lead_min"].first().astype(float)
        te = te.merge(od[["race_id", "horse_number", "pre_odds"]],
                      on=["race_id", "horse_number"], how="left")
    else:
        te["pre_odds"] = np.nan
        lead = pd.Series(dtype=float)

    te = attach_market(te, "pre_odds", "p_mkt_pre")
    te["p_mkt_pre_place"] = np.nan
    ok = te[te["p_mkt_pre"].notna()]
    if len(ok):
        te.loc[ok.index, "p_mkt_pre_place"] = harville_place(ok.reset_index(drop=True), "p_mkt_pre")

    pre_races = set(te.loc[te["p_mkt_pre"].notna(), "race_id"].unique())
    all_races = set(te["race_id"].unique())
    # 市場窓の母数（この分母に対する割合を報告する）
    in_mkt = (te[(te["date"] >= m_start) & (te["date"] <= m_end)]
              if m_start else te.iloc[0:0])
    mkt_denom = set(in_mkt["race_id"].unique())
    logger.info("発走前オッズ取得可能: %d / %d レース（市場窓の母数比 %.1f%%）",
                len(pre_races), len(mkt_denom), 100 * len(pre_races) / max(1, len(mkt_denom)))

    # 月別の取得可能率（(b) の内訳）
    te["_ym"] = te["date"].str[:6]
    monthly = []
    for ym, g in te.groupby("_ym"):
        tot = g["race_id"].nunique()
        got = g.loc[g["p_mkt_pre"].notna(), "race_id"].nunique()
        monthly.append({"ym": ym, "n_races": int(tot), "n_prerace_odds": int(got),
                        "coverage_pct": round(100 * got / max(1, tot), 2)})

    # --- 🔴 目視確認: 実データを1レース分そのまま出す ---
    vis_lines: list[str] = []
    blocks = [("発走前オッズあり", pre_races,
               {"p_win": "p_win(モデル)", "p_mkt_pre": "p_mkt(発走前)", "p_place": "p_place"})]
    if len(pre_races) < len(all_races):
        blocks.append(("発走前オッズなし（市場列は作らない）", all_races - pre_races,
                       {"p_win": "p_win(モデル)", "p_place": "p_place"}))
    for label, rset, cols in blocks:
        cand = te[te["race_id"].isin(rset) & (te["place_slots"] == 3)]
        if not len(cand):
            continue
        rid = int(cand["race_id"].iloc[0])
        g = te[te["race_id"] == rid].sort_values("horse_number")
        head = (f"目視確認 [{label}] race_id={rid} date={g['date'].iloc[0]} "
                f"post_time={g['post_time'].iloc[0]} n={len(g)} "
                f"place_slots={int(g['place_slots'].iloc[0])}")
        vis_lines += show_one_race(g, cols, head)
    print("\n".join(vis_lines))

    # --- スコアリング ---
    results: dict = {}

    # (a) モデル単独（評価窓の全母集団）
    results["model_overall"] = {
        "population": f"{test_start}〜{test_end} の全レース（市場に依存しない）",
        "win": win_scores(te, "p_win"),
    }

    # (b) 市場比較（発走前オッズが取れたレースだけの部分集合）
    if not len(pre_races):
        results["market_comparison"] = {
            "available": False,
            "reason": ("keiba.odds_history は 2026-03-28 01:38:42 より前の行が存在せず、"
                       "この窓には発走前オッズが1件も無い。確定オッズ・確定人気での代用はしない"
                       "（発走10分前の1番人気が確定と一致するのは 80.7%）"),
            "market_window": None,
            "n_races_with_prerace_odds": 0,
        }
    else:
        sub_pre = te[te["race_id"].isin(pre_races)]
        blk = {"model": win_scores(sub_pre, "p_win"),
               "market_prerace": win_scores(sub_pre, "p_mkt_pre")}
        results["market_comparison"] = {
            "available": True,
            "market_window": [m_start, m_end],
            "n_races_in_market_window": int(len(mkt_denom)),
            "n_races_with_prerace_odds": int(len(pre_races)),
            "coverage_pct": round(100 * len(pre_races) / max(1, len(mkt_denom)), 2),
            "coverage_pct_of_full_eval_window": round(100 * len(pre_races) / max(1, len(all_races)), 2),
            "subset_note": ("🔴 発走前オッズが全馬分そろったレースだけの部分集合。"
                            "評価窓全体の数字ではない"),
            "win": blk,
            "model_minus_market_logloss": paired_logloss_ci(
                blk["model"], blk["market_prerace"], args.bootstrap),
        }

    # (c) 複勝側（place_slots ごと）
    place_out: dict = {}
    for slots in (3, 2):
        s = te[te["place_slots"] == slots]
        if len(s):
            place_out[f"slots_{slots}"] = {"model": place_scores(s, "p_place", "p_win")}
            sp = s[s["p_mkt_pre"].notna()]
            if len(sp):
                place_out[f"slots_{slots}"]["market_prerace"] = place_scores(
                    sp, "p_mkt_pre_place", "p_mkt_pre")
    results["place"] = place_out

    # (d) 障害の内訳
    strat = {}
    for label, mask in (("flat", te["is_jump"] == 0), ("jump", te["is_jump"] == 1)):
        s = te[mask]
        if len(s):
            strat[label] = {"n_races": int(s.race_id.nunique()), "n_horses": int(len(s)),
                            "model": win_scores(s, "p_win")}
    results["by_surface"] = strat

    # --- 表示 ---
    def _fmt(rows: list[tuple[str, dict]]) -> None:
        hdr = (f"{'系列':<20}{'nR':>7}{'MNL logloss':>14}{'(SE)':>9}{'uniform':>10}"
               f"{'info gain':>12}{'gain%':>9}{'top1勝率':>11}")
        print(hdr)
        print("-" * 96)
        for k, m in rows:
            print(f"{k:<20}{m['n_races']:>7}{m['mnl_logloss']:>14.5f}{m['mnl_logloss_se']:>9.5f}"
                  f"{m['uniform_logloss']:>10.5f}{m['info_gain_nats']:>12.5f}"
                  f"{m['info_gain_pct']:>9.2f}{m['top1_win_rate']:>11.4f}")

    print(f"\n### 窓 {args.window}: {te['date'].min()}〜{te['date'].max()} "
          f"({te.race_id.nunique():,}レース / {len(te):,}頭) ###")
    print("\n【単勝・全母集団】モデルのみ（市場に依存しない）")
    _fmt([("model", results["model_overall"]["win"])])

    mc = results["market_comparison"]
    if not mc["available"]:
        print(f"\n【市場ベースライン】❌ 出せない — {mc['reason']}")
    else:
        print(f"\n【市場比較】対象窓 {mc['market_window'][0]}〜{mc['market_window'][1]} / "
              f"発走前オッズが取れたレース {mc['n_races_with_prerace_odds']} / "
              f"{mc['n_races_in_market_window']}R ({mc['coverage_pct']}%)")
        print(f"  {mc['subset_note']}")
        _fmt([("model", mc["win"]["model"]), ("market_prerace", mc["win"]["market_prerace"])])
        v = mc["model_minus_market_logloss"]
        if v:
            print(f"\n  モデル − 市場 の多項対数損失差 Δ={v['delta_logloss']:+.5f} "
                  f"95%CI=[{v['ci95'][0]:+.5f}, {v['ci95'][1]:+.5f}] n={v['n_races']}R "
                  f"（負ならモデルが優位）")

    print("\n【発走前オッズの取得可能率（月別）】")
    print(f"{'年月':<8}{'レース':>8}{'発走前オッズ':>14}{'率%':>9}")
    for r in monthly:
        print(f"{r['ym']:<8}{r['n_races']:>8}{r['n_prerace_odds']:>14}{r['coverage_pct']:>9.2f}")

    print("\n【複勝側】")
    hdr = (f"{'place_slots':<12}{'系列':<16}{'nR':>7}{'coverage@k':>13}"
           f"{'place_ll':>11}{'spearman':>11}{'交差R':>9}{'交差ペア':>10}{'同値ペア':>10}")
    print(hdr)
    print("-" * 100)
    for key, d in place_out.items():
        for series, m in d.items():
            print(f"{key:<12}{series:<16}{m['n_races']:>7}{m['coverage_at_k']:>13.4f}"
                  f"{m['place_logloss']:>11.5f}"
                  f"{(m['spearman_in_race'] if m['spearman_in_race'] is not None else float('nan')):>11.4f}"
                  f"{m['cross_races']:>9}{m['cross_pairs']:>10}{m['tied_pairs']:>10}")

    # --- JSON ---
    def _strip(o):
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if not k.startswith("_")}
        if isinstance(o, list):
            return [_strip(v) for v in o]
        return o

    meta = {
        "window": args.window,
        "test_period": [te["date"].min(), te["date"].max()],
        "requested_period": [test_start, test_end],
        "train_end": train_end, "valid_end": valid_end,
        "seeds": seeds, "best_iters": best_iters,
        "features": FEATURES,
        "n_races": int(te.race_id.nunique()), "n_horses": int(len(te)),
        "population": ("JRA のみ (races.course IN 01..10) / abnormality_code in (1,2) 除外 / "
                       "finish_position NULL・0 除外 / 障害含む / "
                       "place_slots = 3(n>=8), 2(5<=n<=7), 0(n<5)。"
                       "⚠️ keiba.race_results には地方・海外の行も混ざるため course 条件は必須"),
        "model_note": ("DB の calculated_indices.win_probability は使っていない。"
                       "jra_rank_quality_review.py と同じ walk-forward で is_win ヘッドを"
                       "組み直し、本番と同じレース内 L1 正規化 + "
                       "CompositeIndexCalculator._harville_place_probs を適用した"),
        "market_note": ("市場は評価にのみ使用。特徴量には含めない。"
                        "発走前オッズは keiba.odds_history(bet_type='win') の"
                        f"発走 {args.max_lead_min:.0f} 分前以内で最後のスナップショット。"
                        "fetched_at は naive UTC なので post_time(JST) を "
                        "to_timestamp(...) AT TIME ZONE 'UTC' で naive UTC に落として比較する。"
                        "now() は使っていない。"
                        "🔴 odds_history は 2026-03-28 01:38:42 より前の行が存在しないため、"
                        "それ以前の窓では市場ベースラインを作れない。確定オッズ・"
                        "確定人気(win_popularity)での代用はしていない"),
        "market_coverage": {
            "market_window": [m_start, m_end],
            "n_races_eval_window": int(len(all_races)),
            "n_races_in_market_window": int(len(mkt_denom)),
            "n_races_with_prerace_odds": int(len(pre_races)),
            "coverage_pct_of_market_window": round(
                100 * len(pre_races) / max(1, len(mkt_denom)), 2),
            "coverage_pct_of_eval_window": round(
                100 * len(pre_races) / max(1, len(all_races)), 2),
            "max_lead_min": args.max_lead_min,
            "by_month": monthly,
            "lead_min_stats": ({} if not len(lead) else {
                "median": round(float(lead.median()), 2),
                "mean": round(float(lead.mean()), 2),
                "p90": round(float(lead.quantile(0.9)), 2),
                "max": round(float(lead.max()), 2),
            }),
        },
        "visual_check": vis_lines,
    }
    out = {**meta, "results": _strip(results)}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"jra_prob_scoring_{args.window}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {path}")


if __name__ == "__main__":
    main()
