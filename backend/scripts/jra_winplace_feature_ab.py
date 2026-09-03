"""JRA 単勝確率モデル 4腕 walk-forward A/B（事前登録 §10 Phase C の実装）

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §10.2〜10.4。
**本スクリプトは事前登録の仕様を実装するだけで、判定基準を動かさない。**

## 目的

§9.1（Phase A 残差診断）が「脚質 / 過去5走の着順分散 / 過去5走の勝率複勝率比」に
複勝側固有の構造（excess）を見つけた。§9.2（Phase B）は単勝の多項対数損失を
proper scoring rule として固定した。Phase C は **その情報を単勝ヘッドの特徴量に
入れると多項対数損失が下がるか** を、同一 walk-forward の対応比較で測る。

## 腕（§10.2・4腕とも同一データセット・同一 seed 群・同一 early stopping）

| 腕 | 内容 |
|---|---|
| `base` | 現行34特徴・`fillna(50.0)`（＝本番 `train_jra_out_rate.featurize` の再現） |
| `nan`  | サブ指数17列と DM 2列の `fillna(50.0)` を廃止し **NaN のまま LightGBM に渡す** ＋ `dm_missing` フラグ1列 |
| `feat` | 34 + 脚質（本番 `pace_handicap._determine_runner_type` を import して PIT 導出）+ 過去5走の着順分散 + 過去5走の勝率/複勝率比 + `pace_handicap_pit` |
| `both` | `nan` + `feat` |

🔴 `feat` の全特徴は point-in-time 厳守。`race_results.running_style` は**その
レースの結果列**なので対象レースの値は使わない（§9.1 罠1）。過去走は
`passing_3` / `passing_4` のみ（`passing_1` 44% / `passing_2` 50% / `margin` 0%）。
PIT 特徴の生成は `scripts/jra_place_residual_diag.py` の
`PastRuns` / `build_pit_features` を **import** して共有する（独立実装をしない）。

### ⚠️ `pace_handicap_index` を丸ごとは使えない（実装上の逸脱・理由を明記する）

事前登録 §10.2 は `feat` に `pace_handicap_index` を挙げているが、

1. **DB に列が無い**（`keiba.calculated_indices` に `pace_handicap_index` は存在しない。
   §訂正3 のとおり `_compute_composite` は返すが upsert の kwargs に無い）。
   よって過去分を引いてくることはできず、`PaceHandicapCalculator.calculate_batch()` を
   レースごとに呼び直すしかない。
2. その本番実装は **point-in-time ではない**。実見:
   - `pace_handicap.py:305-352 _get_frame_stats` … 枠別勝率統計に **日付条件が無い**
     （評価対象レース以降の結果も混ざる）
   - `pace_handicap.py:284-303 _ensure_first3f_medians` … 全期間の中央値。日付条件なし
   - `pace_handicap.py:400-425 _get_jockey_style` … `jockey_running_style_stats` の
     **現在のスナップショット**（window_months=24 の集計済みテーブル・日付条件なし）
   - `pace_handicap.py:488-505 _apply_last3f_bonus` → `_get_avg_last3f` … 日付条件なし

   これらを 2024Q1 の評価に使うと未来の情報が入る。🔴 PIT 厳守は本 Phase の
   絶対条件なので、**PIT 違反の部分を落とした部分再構成**を `pace_handicap_pit`
   として入れる。含めたのは日付に依存しない部分だけ:

   - `PACE_SCORE_TABLE[runner_type][pace_type]`（本番定数を import）
   - `pace_type` = `PaceHandicapCalculator._predict_pace(runner_types)`（本番メソッドを import）。
     ただし runner_types は **馬の脚質のみ**で作る（本番は `_predict_actual_runner_type`
     で騎手戦法を 0.4 混ぜるが、その統計が PIT でないため使わない）
   - `_apply_course_adjustment` / `_apply_field_size_adjustment`（本番メソッドを import。
     `keiba.racecourse_features` は直線長・コーナー・スタート〜コーナー距離の静的表）

   落としたのは: 枠別勝率補正 / 上がり3Fボーナス / 開催バイアス補正（PIT 安全だが
   レースごとに DB を叩くため 1万レース超では現実的でない）/ 前走ハイペース
   リバウンド（`passing_1` 44% でそもそも半分発火しない・§訂正5）。

   **この逸脱は結果を見る前に決めた。** 報告にも明記する。

## 窓（§10.3）

四半期 **2024Q1〜2026Q2 の10四半期**。各四半期の**前日まで**で学習。
🔴 **2026Q3（`20260701` 以降）は使わない**（採否に直結するため一度きり評価を温存）。
`--eval-end` に 20260701 以降を渡すとスクリプトが落ちる。

## 主指標と判定基準（§10.4・事前に固定・動かさない）

**レース単位の多項対数損失**（`scripts/jra_prob_scoring.py` の `win_scores` /
`paired_logloss_ci` を import。🔴 再実装しない）。`base` との**対応差**を
レースクラスタ bootstrap で評価。

| 判定 | 条件 |
|---|---|
| **採用候補** | 10四半期の対応差の平均の 95%CI が 0 を跨がず改善側（＝負）、かつ四半期別で改善が 6/10 以上 |
| 不採用 | 上記を満たさない |

「10四半期の対応差の平均」は**四半期を等重みで平均**した値を主とする
（`quarter_equal_weight`）。bootstrap は四半期ごとにレースを復元抽出して
四半期平均を作り、その10本を等重み平均する。参考として全レースを1つの母集団と
した重み付き平均（`race_pooled`）も併記する。両者が食い違ったら報告する。

**副指標（報告必須・採否には使わない）**: top1勝率 / 複勝側 place_ll・coverage@k /
交差件数 / §9.1 の層（脚質・着順分散・勝率複勝率比）ごとの残差と excess。

🔴 `feat` が効いた場合、「単勝側の較正が直った」のか「複勝側の構造を捉えた」のかを
§9.1 の `excess = residual - (p_place/p_win) * win_residual` で分離して報告する。

## 使い方

    cd backend
    .venv/bin/python scripts/jra_winplace_feature_ab.py \
        --quarters 2024Q1..2026Q2 --seeds 42,123,456 --bootstrap 2000 \
        --out ../docs/model_verification/jra_winplace_feature_ab.json

冪等（同じ引数なら同じ JSON を上書き）。`--cache` にデータセット pickle を
指定すると2回目以降の取得・PIT 特徴生成を省略する。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
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

# --- 本番コード／既存の測定基盤をそのまま import する（独立実装をしない） -------
from scripts.jra_place_residual_diag import (  # noqa: E402
    CUTS,
    PAST_SQL,
    PastRuns,
    _bootstrap_indices,
    _dsn,
    _query,
    assign_levels,
    build_pit_features,
)
from scripts.jra_place_residual_diag import FETCH_SQL as _DIAG_FETCH_SQL  # noqa: E402
from scripts.jra_prob_scoring import (  # noqa: E402
    build_population,
    harville_place,
    paired_logloss_ci,
    place_scores,
    race_normalize,
    win_scores,
)
from scripts.train_jra_iswin_head import MAX_ROUND, PARAMS  # noqa: E402
from scripts.train_jra_out_rate import featurize as prod_featurize  # noqa: E402
from src.indices.composite import OUT_PROB_FEATURE_NAMES  # noqa: E402
from src.indices.pace_handicap import (  # noqa: E402
    INDEX_MAX,
    INDEX_MIN,
    PACE_SCORE_TABLE,
    PaceHandicapCalculator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("winplace_ab")

FEATURES: list[str] = list(OUT_PROB_FEATURE_NAMES)
OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_winplace_feature_ab.json"

# 🔴 TEST 窓の先頭。ここ以降は本 Phase では絶対に使わない（§10.3）
TEST_START = "20260701"

# `fillna(50.0)` の対象＝サブ指数17列 + DM 2列（`train_jra_out_rate.featurize`）
FILL50_COLS: list[str] = FEATURES[:17] + ["jvan_time_dm", "jvan_battle_dm"]
RAW_SUFFIX = "__raw"          # fillna 前の生値を保持する列の接尾辞
NAN_MAP = {c: c + RAW_SUFFIX for c in FILL50_COLS}

# `feat` 腕で足す列
FEAT_EXTRA: list[str] = [
    "runner_type_ord",     # 脚質 escape=0 / leader=1 / mid=2 / closer=3 / unknown=NaN
    "finish_var5",         # 直近5走の着順の標本分散（5走未満は NaN）
    "win_place_ratio5",    # 直近5走の 勝ち数/3着内数（3着内0走は -1.0・5走未満は NaN）
    "pace_handicap_pit",   # PIT 安全な展開ハンデ部分再構成（上の docstring 参照）
]
RUNNER_TYPE_ORD = {"escape": 0.0, "leader": 1.0, "mid": 2.0, "closer": 3.0}

ARMS: dict[str, dict[str, list[str]]] = {
    # names: LightGBM に渡す特徴名 / cols: 値を取ってくる DataFrame の列名
    "base": {"names": FEATURES, "cols": FEATURES},
    "nan": {
        "names": FEATURES + ["dm_missing"],
        "cols": [NAN_MAP.get(c, c) for c in FEATURES] + ["dm_missing"],
    },
    "feat": {"names": FEATURES + FEAT_EXTRA, "cols": FEATURES + FEAT_EXTRA},
    "both": {
        "names": FEATURES + ["dm_missing"] + FEAT_EXTRA,
        "cols": [NAN_MAP.get(c, c) for c in FEATURES] + ["dm_missing"] + FEAT_EXTRA,
    },
}

# `r.course` を足すだけ。母集団条件・JOIN は jra_place_residual_diag と完全に同じものを使う
FETCH_SQL = _DIAG_FETCH_SQL.replace(
    "r.race_number, r.race_name, r.course_name,",
    "r.race_number, r.race_name, r.course_name, r.course,",
)
assert "r.course," in FETCH_SQL, "FETCH_SQL への r.course 追加に失敗した"

COURSE_FEAT_SQL = """
SELECT course_code, straight_distance, corner_tightness, start_to_corner_m
FROM keiba.racecourse_features
"""


# ---------------------------------------------------------------------------
# 四半期
# ---------------------------------------------------------------------------

def parse_quarters(spec: str) -> list[tuple[str, str, str]]:
    """`2024Q1..2026Q2` → [(ラベル, 開始YYYYMMDD, 終了YYYYMMDD), ...]。"""
    if ".." not in spec:
        raise SystemExit(f"--quarters は '2024Q1..2026Q2' 形式で指定する: {spec!r}")
    a, b = spec.split("..", 1)

    def _p(s: str) -> tuple[int, int]:
        s = s.strip().upper()
        if len(s) != 6 or s[4] != "Q" or not s[5].isdigit():
            raise SystemExit(f"四半期の指定が不正: {s!r}")
        return int(s[:4]), int(s[5])

    (y0, q0), (y1, q1) = _p(a), _p(b)
    out: list[tuple[str, str, str]] = []
    y, q = y0, q0
    while (y, q) <= (y1, q1):
        qs = pd.Timestamp(year=y, month=(q - 1) * 3 + 1, day=1)
        qe = qs + pd.offsets.QuarterEnd(0)
        out.append((f"{y}Q{q}", qs.strftime("%Y%m%d"), qe.strftime("%Y%m%d")))
        q += 1
        if q == 5:
            q, y = 1, y + 1
    return out


# ---------------------------------------------------------------------------
# データセット
# ---------------------------------------------------------------------------

def _pace_handicap_pit(df: pd.DataFrame, course_feat: dict) -> np.ndarray:
    """PIT 安全な展開ハンデの部分再構成。

    🔴 本番 `PaceHandicapCalculator.calculate_batch` そのものではない。
    PIT でない補正（枠別勝率・上がり3F・騎手戦法）を落としてある。
    落とした理由と実装の出典は本モジュールの docstring を参照。

    使うのは本番の定数・メソッドだけ（閾値のコピーをしない）:
      PACE_SCORE_TABLE / _predict_pace / _apply_course_adjustment
      / _apply_field_size_adjustment
    """
    calc = PaceHandicapCalculator.__new__(PaceHandicapCalculator)  # DB を持たせない
    out = np.full(len(df), np.nan, dtype=float)
    rt_all = df["runner_type"].to_numpy()
    course_all = df["course"].astype(str).to_numpy()
    hc_all = pd.to_numeric(df["head_count"], errors="coerce").to_numpy()
    for _, idx in df.groupby("race_id", sort=False).indices.items():
        types = {i: rt_all[i] for i in idx}
        pace = PaceHandicapCalculator._predict_pace(calc, types)
        feat = course_feat.get(course_all[idx[0]])
        hc = hc_all[idx[0]]
        head = int(hc) if not np.isnan(hc) else len(idx)
        for i in idx:
            rt = rt_all[i]
            score = PACE_SCORE_TABLE.get(rt, PACE_SCORE_TABLE["unknown"])[pace]
            score = PaceHandicapCalculator._apply_course_adjustment(calc, score, rt, feat)
            score = PaceHandicapCalculator._apply_field_size_adjustment(
                calc, score, rt, head, feat)
            out[i] = min(INDEX_MAX, max(INDEX_MIN, score))
    return out


def build_dataset(start: str, end: str) -> pd.DataFrame:
    """34特徴 + fillna 前の生値 + PIT 特徴 を持つ1枚の DataFrame を作る。

    🔴 4腕はこの**同一の DataFrame**から特徴列を選ぶだけ。腕ごとに母集団が
    変わってはいけないので、行のフィルタはここで1回だけ行う。
    """
    conn = psycopg2.connect(_dsn())
    t0 = time.time()
    logger.info("対象レース取得 %s〜%s ...", start, end)
    raw = _query(conn, FETCH_SQL, {"start": start, "end": end})
    raw["date"] = raw["date"].astype(str)
    logger.info("  %d行 / %dレース (%.1fs)", len(raw), raw["race_id"].nunique(),
                time.time() - t0)

    t0 = time.time()
    logger.info("過去走取得（PIT 用・全 course・abnormality_code=0）...")
    past_df = _query(conn, PAST_SQL, {"end": end})
    past_df["date"] = past_df["date"].astype(str)
    logger.info("  %d行 / %d頭 (%.1fs)", len(past_df), past_df["horse_id"].nunique(),
                time.time() - t0)

    cf_rows = _query(conn, COURSE_FEAT_SQL, {})
    conn.close()
    from types import SimpleNamespace
    course_feat = {
        str(r.course_code): SimpleNamespace(
            straight_distance=r.straight_distance,
            corner_tightness=r.corner_tightness,
            start_to_corner_m=r.start_to_corner_m,
        )
        for r in cf_rows.itertuples()
    }

    # --- fillna 前の生値を退避してから本番 featurize を当てる ---
    for c in FILL50_COLS:
        raw[c + RAW_SUFFIX] = pd.to_numeric(raw[c], errors="coerce")
    raw["dm_missing"] = (
        raw["jvan_time_dm" + RAW_SUFFIX].isna() | raw["jvan_battle_dm" + RAW_SUFFIX].isna()
    ).astype(int)
    df = prod_featurize(raw)          # 🔴 本番と同一の変換（fillna(50.0) を含む）
    df = build_population(df)         # 🔴 jra_prob_scoring と同一の母集団

    t0 = time.time()
    logger.info("PIT 特徴を生成（脚質・着順分散・勝率複勝率比）...")
    past = PastRuns(past_df)
    df = build_pit_features(df, past)  # 🔴 jra_place_residual_diag の実装を共有
    logger.info("  完了 (%.1fs)", time.time() - t0)

    df["runner_type_ord"] = df["runner_type"].map(RUNNER_TYPE_ORD).astype(float)
    t0 = time.time()
    df["pace_handicap_pit"] = _pace_handicap_pit(df, course_feat)
    logger.info("pace_handicap_pit 生成 完了 (%.1fs)", time.time() - t0)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 学習（腕ごとに特徴列だけ差し替える。seed / early stopping は共通）
# ---------------------------------------------------------------------------

def fit_predict(tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame,
                names: list[str], cols: list[str],
                seeds: list[int]) -> tuple[np.ndarray, list[int]]:
    """`jra_prob_scoring.train_iswin` と同じ手順を、特徴列を差し替えられる形にしたもの。

    ハイパラ `PARAMS` と `MAX_ROUND` は `train_jra_iswin_head` から import して共有。
    """
    Xtr = tr[cols].to_numpy(dtype=float)
    Xva = va[cols].to_numpy(dtype=float)
    Xte = te[cols].to_numpy(dtype=float)
    ytr = (tr["finish_position"] == 1).astype(int).to_numpy()
    yva = (va["finish_position"] == 1).astype(int).to_numpy()
    preds, iters = [], []
    for s in seeds:
        d = lgb.Dataset(Xtr, ytr, feature_name=names)
        dv = lgb.Dataset(Xva, yva, reference=d)
        m = lgb.train(dict(PARAMS, seed=s), d, num_boost_round=MAX_ROUND,
                      valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
        iters.append(int(m.best_iteration))
        preds.append(m.predict(Xte, num_iteration=m.best_iteration))
    return np.mean(preds, axis=0), iters


def run_arm(df: pd.DataFrame, arm: str, qs: list[tuple[str, str, str]],
            seeds: list[int], valid_days: int) -> pd.DataFrame:
    """1腕を10四半期の walk-forward で回し、評価行に p_win / p_place を付けて返す。"""
    names, cols = ARMS[arm]["names"], ARMS[arm]["cols"]
    out = []
    for label, qstart, qend in qs:
        train = df[df["date"] < qstart]
        te = df[(df["date"] >= qstart) & (df["date"] <= qend)].copy()
        if train.empty or te.empty:
            raise SystemExit(f"{label}: train か test が空（train={len(train)} test={len(te)}）")
        cut = (pd.to_datetime(qstart) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
        tr, va = train[train["date"] <= cut], train[train["date"] > cut]
        if len(va) < 2000:
            i = int(len(train) * 0.8)
            tr, va = train.iloc[:i], train.iloc[i:]
        t0 = time.time()
        raw_w, iters = fit_predict(tr, va, te, names, cols, seeds)
        te = te.reset_index(drop=True)
        te["p_win"] = race_normalize(raw_w, te["race_id"])
        te["p_place"] = harville_place(te, "p_win")
        te["quarter"] = label
        te["best_iters"] = str(iters)
        out.append(te)
        logger.info("  [%s] %s tr=%d va=%d te=%d/%dR iters=%s (%.1fs)",
                    arm, label, len(tr), len(va), len(te), te["race_id"].nunique(),
                    iters, time.time() - t0)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# 判定（主指標）
# ---------------------------------------------------------------------------

def quarter_paired(arm_ev: pd.DataFrame, base_ev: pd.DataFrame,
                   n_boot: int, seed: int = 20260904) -> dict:
    """四半期別の対応差と、その等重み平均 / レース重み平均の 95%CI。

    レース単位の per-race 対数損失は `jra_prob_scoring.win_scores` が返す `_per_race`
    をそのまま使う（🔴 再実装しない）。
    """
    quarters = sorted(base_ev["quarter"].unique())
    per_q: dict[str, np.ndarray] = {}
    rows = []
    for q in quarters:
        a = win_scores(arm_ev[arm_ev["quarter"] == q], "p_win")
        b = win_scores(base_ev[base_ev["quarter"] == q], "p_win")
        sa = pd.Series(a["_per_race"], index=a["_race_ids"])
        sb = pd.Series(b["_per_race"], index=b["_race_ids"])
        common = sa.index.intersection(sb.index)
        d = (sa.loc[common] - sb.loc[common]).to_numpy()
        per_q[q] = d
        rows.append({
            "quarter": q, "n_races": int(len(d)),
            "arm_logloss": a["mnl_logloss"], "base_logloss": b["mnl_logloss"],
            "delta": round(float(d.mean()), 5),
            "improved": bool(d.mean() < 0),
            "arm_top1_win_rate": a["top1_win_rate"], "base_top1_win_rate": b["top1_win_rate"],
        })

    rng = np.random.default_rng(seed)
    qkeys = list(per_q)
    eq_boot = np.empty(n_boot, dtype=float)
    pooled_boot = np.empty(n_boot, dtype=float)
    all_d = np.concatenate([per_q[q] for q in qkeys])
    for b in range(n_boot):
        means = []
        for q in qkeys:
            d = per_q[q]
            means.append(d[rng.integers(0, len(d), len(d))].mean())
        eq_boot[b] = float(np.mean(means))
        pooled_boot[b] = all_d[rng.integers(0, len(all_d), len(all_d))].mean()

    eq_point = float(np.mean([per_q[q].mean() for q in qkeys]))
    pooled_point = float(all_d.mean())
    n_improved = int(sum(1 for q in qkeys if per_q[q].mean() < 0))

    def _ci(b):
        return [round(float(np.percentile(b, 2.5)), 5), round(float(np.percentile(b, 97.5)), 5)]

    eq_ci, pooled_ci = _ci(eq_boot), _ci(pooled_boot)
    ci_ok = eq_ci[1] < 0            # 95%CI が 0 を跨がず改善側（logloss は低いほど良い）
    maj_ok = n_improved >= 6
    return {
        "by_quarter": rows,
        "n_quarters": len(qkeys),
        "n_improved_quarters": n_improved,
        "quarter_equal_weight": {"delta": round(eq_point, 5), "ci95": eq_ci},
        "race_pooled": {"delta": round(pooled_point, 5), "ci95": pooled_ci,
                        "n_races": int(len(all_d))},
        "criterion_ci_excludes_zero_improving": bool(ci_ok),
        "criterion_majority_quarters": bool(maj_ok),
        "verdict": "採用候補" if (ci_ok and maj_ok) else "不採用",
    }


# ---------------------------------------------------------------------------
# 副指標: §9.1 の層ごとの残差と excess
# ---------------------------------------------------------------------------

def excess_by_level(ev: pd.DataFrame, cutoffs: dict, n_boot: int,
                    seed: int = 20260904) -> dict:
    """§9.1 と同じ `excess = residual - (p_place/p_win) * win_residual` を層別に出す。

    - 母集団は `place_slots == 3` に限定（§9.1 と同じ。`=2` は Harville が
      「2着以内」を返すので混ぜると別の量になる）
    - `p_win` 10分位で中心化してから水準平均を取る（§9.1 罠6）
    - CI はレースクラスタ bootstrap（`jra_place_residual_diag._bootstrap_indices` を共有）

    🔴 これは採否には使わない。`feat` が効いた理由を「単勝側の較正」と
    「複勝側の構造」に分けるための解釈用。
    """
    d = ev[ev["place_slots"] == 3].copy()
    if not len(d):
        return {}
    d = assign_levels(d, cutoffs)
    y_place = (d["finish_position"] <= 3).astype(float).to_numpy()
    y_win = (d["finish_position"] == 1).astype(float).to_numpy()
    pw = d["p_win"].to_numpy(dtype=float)
    pp = d["p_place"].to_numpy(dtype=float)
    res = y_place - pp
    win_res = y_win - pw
    excess = res - (pp / np.clip(pw, 1e-9, None)) * win_res
    dec = d["pwin_decile"].to_numpy(dtype=int)
    race_codes = pd.factorize(d["race_id"].to_numpy())[0]
    n_dec = int(dec.max()) + 1
    rng = np.random.default_rng(seed)

    def _adj(vals: np.ndarray, idx: np.ndarray, codes: np.ndarray, k: int) -> np.ndarray:
        vv, dd = vals[idx], dec[idx]
        ds = np.bincount(dd, weights=vv, minlength=n_dec)
        dc = np.bincount(dd, minlength=n_dec).astype(float)
        dm = np.divide(ds, dc, out=np.zeros_like(ds), where=dc > 0)
        vadj = vv - dm[dd]
        cc = codes[idx]
        m = cc >= 0
        ls = np.bincount(cc[m], weights=vadj[m], minlength=k)
        lc = np.bincount(cc[m], minlength=k).astype(float)
        return np.divide(ls, lc, out=np.full(k, np.nan), where=lc > 0)

    out: dict = {"n_horses": int(len(d)), "n_races": int(d["race_id"].nunique()),
                 "overall": {
                     "place_residual_pt": round(float(res.mean() * 100), 4),
                     "win_residual_pt": round(float(win_res.mean() * 100), 4),
                 },
                 "cuts": {}}
    all_idx = np.arange(len(d))
    cut_names = ("runner_type", "finish_var_tertile", "win_place_ratio_tertile")
    spec = {}
    for cut in cut_names:
        levels = list(CUTS[cut]) + ["unknown"] + (
            ["no_place"] if cut == "win_place_ratio_tertile" else [])
        code_map = {lv: i for i, lv in enumerate(levels)}
        codes = d[f"cut_{cut}"].map(lambda v: code_map.get(v, -1)).to_numpy(dtype=int)
        spec[cut] = (code_map, codes, len(levels),
                     np.empty((n_boot, len(levels)), dtype=float))

    # bootstrap の行インデックスは1回だけ作って3つの切り口で使い回す（生成が O(n) で重い）
    for b, idx in enumerate(_bootstrap_indices(race_codes, n_boot, rng)):
        for cut in cut_names:
            _, codes, k, bx = spec[cut]
            bx[b] = _adj(excess, idx, codes, k)

    for cut in cut_names:
        code_map, codes, k, bx = spec[cut]
        pt_r = _adj(res, all_idx, codes, k)
        pt_w = _adj(win_res, all_idx, codes, k)
        pt_x = _adj(excess, all_idx, codes, k)
        lo = np.nanpercentile(bx, 2.5, axis=0)
        hi = np.nanpercentile(bx, 97.5, axis=0)
        out["cuts"][cut] = {
            lv: {
                "n": int((codes == i).sum()),
                "place_residual_pt": round(float(pt_r[i] * 100), 4),
                "win_residual_pt": round(float(pt_w[i] * 100), 4),
                "excess_pt": round(float(pt_x[i] * 100), 4),
                "excess_ci95_pt": [round(float(lo[i] * 100), 4), round(float(hi[i] * 100), 4)],
            }
            for lv, i in code_map.items() if (codes == i).sum() > 0
        }
    return out


def freeze_cutoffs(ev: pd.DataFrame) -> dict:
    """3分位・10分位のカット点を `base` 腕の評価行で1回だけ決めて全腕へ流用する。

    §9.1 罠4「窓ごとに切り直すと同符号で再現が別の量の比較になる」。腕ごとに
    切り直すと『同じ層』の比較でなくなるので、**base で凍結して4腕に使う**。
    """
    d = ev[ev["place_slots"] == 3]
    fv = d["finish_var5"].dropna().to_numpy(dtype=float)
    wr = d["win_place_ratio5"]
    wr = wr[wr >= 0].dropna().to_numpy(dtype=float)
    return {
        "finish_var_tertile": [float(np.quantile(fv, 1 / 3)), float(np.quantile(fv, 2 / 3))],
        "win_place_ratio_tertile": [float(np.quantile(wr, 1 / 3)), float(np.quantile(wr, 2 / 3))],
        "pwin_decile": [float(x) for x in
                        np.quantile(d["p_win"].to_numpy(dtype=float), np.arange(1, 10) / 10)],
    }


# ---------------------------------------------------------------------------
# 🔴 目視確認: 実データを1レース表示する
# ---------------------------------------------------------------------------

def visual_check(df: pd.DataFrame, evs: dict[str, pd.DataFrame]) -> list[str]:
    """全体を回す前に必ず目で見る。

    確認するのは3点（CLAUDE.md「baseline は実データを1件表示して目視確認する」）:
      1. `Σp_win = 1.0`（4腕とも）
      2. `nan` 腕で欠損馬が実際に NaN で渡っていること（`__raw` 列と `dm_missing`）
      3. `feat` 腕の脚質・着順分散・勝率複勝率比が妥当なこと
    """
    base = evs["base"]
    # 欠損馬とそうでない馬が**同じレースに混在**しているレースを選ぶ（差が目で見えるように）
    g = base[base["place_slots"] == 3].groupby("race_id")["dm_missing"].agg(["min", "max"])
    mixed = g[(g["min"] == 0) & (g["max"] == 1)]
    rid = int(mixed.index[0]) if len(mixed) else int(base["race_id"].iloc[0])
    src = df[df["race_id"] == rid].sort_values("horse_number")
    L = ["", "=" * 132]
    g0 = src.iloc[0]
    L.append(f"目視確認 race_id={rid} {g0['date']} {g0['course_name']}{int(g0['race_number'])}R "
             f"{g0['race_name']} n={len(src)} place_slots={int(g0['place_slots'])}")
    L.append("=" * 132)
    hdr = (f"{'馬番':>4}{'着':>4}{'馬名':<20}"
           f"{'time_dm(base)':>14}{'time_dm(nan)':>14}"
           f"{'battle(base)':>14}{'battle(nan)':>14}{'dmMiss':>7}"
           f"{'脚質':>9}{'ord':>5}{'着順分散':>10}{'勝/複':>8}{'paceH':>8}")
    for arm in ARMS:
        hdr += f"{'p_win:' + arm:>14}"
    L.append(hdr)

    def _n(v, w, d=2):
        return f"{'NaN':>{w}}" if v is None or (isinstance(v, float) and np.isnan(v)) \
            else f"{float(v):>{w}.{d}f}"

    pw = {arm: evs[arm].set_index(["race_id", "horse_number"])["p_win"] for arm in ARMS}
    for _, r in src.iterrows():
        hn = int(r["horse_number"])
        row = (f"{hn:>4}{int(r['finish_position']):>4}{str(r['horse_name'])[:19]:<20}"
               f"{_n(r['jvan_time_dm'], 14, 1)}{_n(r['jvan_time_dm' + RAW_SUFFIX], 14, 1)}"
               f"{_n(r['jvan_battle_dm'], 14, 1)}{_n(r['jvan_battle_dm' + RAW_SUFFIX], 14, 1)}"
               f"{int(r['dm_missing']):>7}{str(r['runner_type']):>9}"
               f"{_n(r['runner_type_ord'], 5, 0)}{_n(r['finish_var5'], 10, 2)}"
               f"{_n(r['win_place_ratio5'], 8, 2)}{_n(r['pace_handicap_pit'], 8, 1)}")
        for arm in ARMS:
            row += f"{_n(pw[arm].get((rid, hn)), 14, 5)}"
        L.append(row)
    tot = (f"{'Σ':>4}{'':>4}{'':<20}{'':>14}{'':>14}{'':>14}{'':>14}"
           f"{'':>7}{'':>9}{'':>5}{'':>10}{'':>8}{'':>8}")
    for arm in ARMS:
        s = evs[arm][evs[arm]["race_id"] == rid]["p_win"].sum()
        tot += f"{s:>14.5f}"
    L.append(tot)
    L.append("（Σ行が 4腕とも 1.00000 であること）")
    L.append("（(base) 列は本番 featurize の fillna(50.0) 後・(nan) 列は fillna 前の生値。"
             "dmMiss=1 の馬は (base) が 50.0 に埋まり (nan) が NaN のまま LightGBM へ渡る）")
    L.append("（脚質は過去10走の passing_4/head_count 平均から本番 _determine_runner_type で導出。"
             "対象レースの結果列は使っていない＝point-in-time）")
    return L


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--quarters", default="2024Q1..2026Q2",
                   help="評価する四半期の範囲（既定 = 事前登録 §10.3 の10四半期）")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--data-start", default="20230101", help="学習データの開始日")
    p.add_argument("--valid-days", type=int, default=90,
                   help="early stopping 用に train の末尾から取る日数")
    p.add_argument("--cache", default=None, help="データセット pickle（冪等・再利用可）")
    p.add_argument("--pred-cache", default=None, help="腕ごとの評価行 pickle")
    p.add_argument("--arms", default=",".join(ARMS), help="回す腕（既定は4腕すべて）")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            raise SystemExit(f"未知の腕: {a}")
    if "base" not in arms:
        raise SystemExit("base は対応比較の基準なので必ず含める")

    qs = parse_quarters(args.quarters)
    eval_end = qs[-1][2]
    # 🔴 §10.3: TEST 窓（2026Q3 以降）を絶対に使わない
    if eval_end >= TEST_START:
        raise SystemExit(
            f"🔴 評価終端 {eval_end} が TEST_START={TEST_START} 以降。事前登録 §10.3 により "
            f"2026Q3 は Phase C では使わない（一度きり評価の温存）")
    logger.info("四半期 %d本: %s", len(qs), " / ".join(q[0] for q in qs))

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = build_dataset(args.data_start, eval_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
            logger.info("データセットを保存: %s", cache)

    # 🔴 TEST 窓の行を DataFrame からも落とす（誤って学習・評価に混ざらないように）
    n_before = len(df)
    df = df[df["date"] < TEST_START].reset_index(drop=True)
    if len(df) != n_before:
        logger.info("TEST 窓(%s 以降) を %d行 除外", TEST_START, n_before - len(df))
    logger.info("母集団: %d行 / %dレース (%s〜%s)", len(df), df["race_id"].nunique(),
                df["date"].min(), df["date"].max())

    miss = {c: round(float(df[c + RAW_SUFFIX].isna().mean() * 100), 3) for c in FILL50_COLS}
    logger.info("fillna(50) 対象列の欠損率(%%): %s",
                {k: v for k, v in sorted(miss.items(), key=lambda x: -x[1]) if v > 0})
    logger.info("dm_missing=1 の割合: %.3f%%", df["dm_missing"].mean() * 100)
    rt_dist = df["runner_type"].value_counts(normalize=True).round(4).to_dict()
    logger.info("脚質(PIT)の分布: %s / 直近5走が揃う割合 %.1f%%",
                rt_dist, (df["n_past5"] >= 5).mean() * 100)

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        evs = {}
        for arm in arms:
            logger.info("=== arm=%s (%d特徴) ===", arm, len(ARMS[arm]["names"]))
            evs[arm] = run_arm(df, arm, qs, seeds, args.valid_days)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    # --- 🔴 目視確認（全体の集計より先に出す） ---
    vis = visual_check(df, evs)
    print("\n".join(vis))

    base_ev = evs["base"]
    print(f"\n評価対象: {base_ev['race_id'].nunique():,}レース / {len(base_ev):,}頭 "
          f"({base_ev['date'].min()}〜{base_ev['date'].max()}) / 四半期 {len(qs)}本")
    print("腕: " + " / ".join(f"{a}({len(ARMS[a]['names'])}特徴)" for a in arms))

    results: dict = {}

    # --- 主指標 ---
    print("\n" + "=" * 118)
    print("  【主指標】レース単位 多項対数損失の base との対応差（負＝改善）")
    print("=" * 118)
    hdr = f"{'四半期':<9}{'nR':>7}" + "".join(f"{a:>26}" for a in arms if a != "base")
    print(hdr)
    print(f"{'':<9}{'':>7}" + "".join(f"{'base_ll → arm_ll (Δ)':>26}" for a in arms if a != "base"))
    print("-" * 118)
    paired = {}
    for a in arms:
        if a == "base":
            continue
        paired[a] = quarter_paired(evs[a], base_ev, args.bootstrap)
    qlabels = [q[0] for q in qs]
    for i, q in enumerate(qlabels):
        line = f"{q:<9}{paired[arms[1]]['by_quarter'][i]['n_races']:>7}"
        for a in arms:
            if a == "base":
                continue
            r = paired[a]["by_quarter"][i]
            line += f"{r['base_logloss']:>9.4f} →{r['arm_logloss']:>8.4f}({r['delta']:+.4f})"
        print(line)
    print("-" * 118)
    for a in arms:
        if a == "base":
            continue
        v = paired[a]
        eq, po = v["quarter_equal_weight"], v["race_pooled"]
        print(f"\n  [{a}] 四半期等重み平均 Δ={eq['delta']:+.5f} "
              f"95%CI=[{eq['ci95'][0]:+.5f}, {eq['ci95'][1]:+.5f}]")
        print(f"       レース重み平均   Δ={po['delta']:+.5f} "
              f"95%CI=[{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}] (n={po['n_races']}R)")
        print(f"       改善四半期 {v['n_improved_quarters']}/{v['n_quarters']} "
              f"｜CI条件 {'○' if v['criterion_ci_excludes_zero_improving'] else '×'}"
              f" / 過半条件 {'○' if v['criterion_majority_quarters'] else '×'}"
              f" → **{v['verdict']}**")
    results["primary"] = paired

    # --- 副指標: 全体の単勝スコア・複勝側・交差 ---
    print("\n" + "=" * 118)
    print("  【副指標】全期間プール（採否には使わない）")
    print("=" * 118)
    sec: dict = {}
    print(f"{'腕':<8}{'nR':>7}{'MNL logloss':>14}{'(SE)':>9}{'info gain':>12}{'gain%':>8}"
          f"{'top1勝率':>10}{'top1複勝率':>12}")
    for a in arms:
        w = win_scores(evs[a], "p_win")
        sec.setdefault(a, {})["win"] = {k: v for k, v in w.items() if not k.startswith("_")}
        print(f"{a:<8}{w['n_races']:>7}{w['mnl_logloss']:>14.5f}{w['mnl_logloss_se']:>9.5f}"
              f"{w['info_gain_nats']:>12.5f}{w['info_gain_pct']:>8.2f}"
              f"{w['top1_win_rate']:>10.4f}{w['top1_place_rate']:>12.4f}")

    print(f"\n{'腕':<8}{'slots':>6}{'nR':>7}{'coverage@k':>13}{'place_ll':>11}"
          f"{'spearman':>11}{'交差R':>8}{'交差ペア':>10}{'同値ペア':>10}")
    for a in arms:
        for slots in (3, 2):
            s = evs[a][evs[a]["place_slots"] == slots]
            if not len(s):
                continue
            m = place_scores(s, "p_place", "p_win")
            sec.setdefault(a, {}).setdefault("place", {})[f"slots_{slots}"] = m
            sp = m["spearman_in_race"] if m["spearman_in_race"] is not None else float("nan")
            print(f"{a:<8}{slots:>6}{m['n_races']:>7}{m['coverage_at_k']:>13.4f}"
                  f"{m['place_logloss']:>11.5f}{sp:>11.4f}{m['cross_races']:>8}"
                  f"{m['cross_pairs']:>10}{m['tied_pairs']:>10}")
    print("（交差は Harville 経路のままなので全腕 0 のはず。0 でなければ実装バグを疑う・§10.4）")
    results["secondary"] = sec

    # --- 副指標: §9.1 の層ごとの残差と excess（解釈の分離） ---
    print("\n" + "=" * 118)
    print("  【excess による分離】単勝側の較正が直ったのか / 複勝側の構造を捉えたのか")
    print("  excess = 複勝残差 − (p_place/p_win)×単勝残差  ＝ p_win 較正ずれの影を除いた複勝側固有のズレ")
    print("=" * 118)
    cutoffs = freeze_cutoffs(base_ev)     # 🔴 base で凍結して全腕に流用（§9.1 罠4）
    exc = {a: excess_by_level(evs[a], cutoffs, args.bootstrap) for a in arms}
    for cut in ("runner_type", "finish_var_tertile", "win_place_ratio_tertile"):
        print(f"\n  --- {cut} ---")
        print(f"{'水準':<12}{'腕':<8}{'n':>8}{'複勝残差pt':>12}{'単勝残差pt':>12}"
              f"{'excess pt':>11}{'95%CI':>22}")
        for lv in list(CUTS[cut]) + (["no_place"] if cut == "win_place_ratio_tertile" else []):
            for a in arms:
                r = exc[a].get("cuts", {}).get(cut, {}).get(lv)
                if not r:
                    continue
                print(f"{lv:<12}{a:<8}{r['n']:>8}{r['place_residual_pt']:>12.2f}"
                      f"{r['win_residual_pt']:>12.2f}{r['excess_pt']:>11.2f}"
                      f"  [{r['excess_ci95_pt'][0]:+7.2f},{r['excess_ci95_pt'][1]:+7.2f}]")
    results["excess"] = exc

    # --- JSON ---
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "preregistration": "docs/jra_winplace_structure_plan_2026_09_04.md §10.2-10.4",
        "quarters": [{"label": q[0], "start": q[1], "end": q[2]} for q in qs],
        "test_window_excluded": {"test_start": TEST_START,
                                 "note": "🔴 2026Q3 以降は Phase C では使わない（§10.3）"},
        "arms": {a: {"n_features": len(ARMS[a]["names"]), "features": ARMS[a]["names"]}
                 for a in arms},
        "seeds": seeds,
        "bootstrap": args.bootstrap,
        "valid_days": args.valid_days,
        "n_rows": int(len(df)), "n_races": int(df["race_id"].nunique()),
        "data_period": [df["date"].min(), df["date"].max()],
        "eval_period": [base_ev["date"].min(), base_ev["date"].max()],
        "population": ("JRA のみ (races.course IN 01..10) / abnormality_code in (1,2) 除外 / "
                       "finish_position NULL・0 除外 / 障害含む / "
                       "place_slots = 3(n>=8), 2(5<=n<=7), 0(n<5)。"
                       "4腕は完全に同一の行集合（build_population で1回だけフィルタ）"),
        "criteria": ("採用候補 = 10四半期の対応差の平均（四半期等重み）の 95%CI が 0 を跨がず "
                     "改善側（負）、かつ改善四半期が 6/10 以上。副指標は採否に使わない"),
        "missing_rate_pct_of_fill50_cols": miss,
        "dm_missing_pct": round(float(df["dm_missing"].mean() * 100), 4),
        "runner_type_distribution": rt_dist,
        "n_past5_full_pct": round(float((df["n_past5"] >= 5).mean() * 100), 3),
        "pace_handicap_note": (
            "🔴 事前登録 §10.2 の `pace_handicap_index` は DB に列が無く、本番 "
            "PaceHandicapCalculator は _get_frame_stats / _ensure_first3f_medians / "
            "_get_jockey_style / _get_avg_last3f に日付条件が無く point-in-time でない。"
            "PIT 厳守を優先し、日付に依存しない部分（PACE_SCORE_TABLE × _predict_pace + "
            "_apply_course_adjustment + _apply_field_size_adjustment）だけを "
            "`pace_handicap_pit` として再構成した。落としたのは枠別勝率補正・上がり3F・"
            "開催バイアス・前走ハイペースリバウンド。結果を見る前に決めた逸脱"),
        "cutoffs_frozen_on_base": cutoffs,
        "visual_check": vis,
        "results": results,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {outp}")


if __name__ == "__main__":
    main()
