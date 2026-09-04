"""JRA v28 単勝ヘッド（is_win 較正ヘッド・38特徴）の学習スクリプト。

本番 `composite.py` が `win_probability` の較正に使うモデルを学習・保存する。
推論時にレース内正規化（Σ=1）されて `win_probability` になる。

## v28 で変わったこと（計画 §16.1）

特徴を **34列 → 38列**にした。足したのは `src/indices/past_form.py` の
`PAST_FORM_FEATURE_NAMES`:

    runner_type_ord / finish_var5 / win_place_ratio5 / pace_handicap_pit

🔴 **新特徴は `past_form` モジュールからのみ取得する。別実装をしない。**
本スクリプトの入口は `build_past_form_features_bulk`、配信側（`composite.py`）の
入口は `build_past_form_features_for_race`。**どちらも同じ純関数**
`compute_race_past_form_features` を通るので train/serve skew が入らない
（地方 v13→v14 は「市場込みで学習・市場なしで配信」の skew で指数1位馬の勝率を
9pt 落とした）。列の順序は `composite.V28_FEATURE_NAMES` が正本。

🔴 **新特徴の欠損は NaN のまま LightGBM に渡す**（`50.0` で埋めない）。
過去5走が揃うのは 58.5% のみで、`finish_var5` / `win_place_ratio5` の 41.5% と
`runner_type_ord` の 10.1% が NaN になる。検証時から NaN 意味論で測っている（§16.2-5）。
34列側の `fillna(50.0)` は v27 から変更なし（`nan` 腕は §11.1 で不採用）。

## 🔴 データセットは検証実装と同一の作り方にする

Phase C（§11.1 Δ=−0.00750）も 2026Q3 の確認成功（§18.1）も
`scripts/jra_winplace_feature_ab.build_dataset` で出した数字なので、
本番の学習が違う母集団・違う変換を使うと**検証結果が本番に対して無効になる**。
そこで本スクリプトは検証と同じ部品を使う:

| 部位 | 使うもの | 検証で使っていたもの |
|---|---|---|
| 34列の変換 | `train_jra_out_rate.featurize`（`fillna(50.0)` 込み） | 同じ（`prod_featurize`） |
| 母集団 | `jra_prob_scoring.build_population` | 同じ |
| 新特徴4列 | `src.indices.past_form`（本番モジュール） | `jra_place_residual_diag.build_pit_features`。
`tests/test_past_form.py::test_parity_with_winplace_feature_ab_*` が値の一致を固定している |

⚠️ v27 までの本スクリプトは `jra_calibration_ab.QUERY` / `featurize` を使っており
`fillna(50.0)` が無く、かつ `head_count >= 8` で 5〜7頭立てを落としていた。
v28 では**検証実装（＝本番 `_build_v26_features` の再現）に揃えた**。
5〜7頭立ては複勝の独立ヘッド（`train_jra_placed_head.py`）の学習に必要でもある。

## 境界（`src/jra_protocol.py`）

- honest test 用のモデルは TRAIN（≤`TRAIN_END`）のみで学習し、TEST に一度だけ当てる
- **本番モデルは `TRAIN_DATA_END`（= `TEST_START` の前日）まで**で refit する。
  🔴 全期間 refit にすると DB の過去分が全て in-sample になり一度きり評価が成立しない
  （2026-08-22 に本スクリプトだけ取り残されていて実害が出た。`composite.py` の
  `_V26_ISWIN_MODEL_PATH` 上のコメントを参照）

出力（🔴 **既存の v27 モデルは上書きしない**。ロールバック可能にするため新規名）:
  models/v28_iswin_calib.txt
  models/v28_iswin_calib_metrics.json

使い方:
  cd backend
  .venv/bin/python scripts/train_jra_iswin_head.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
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

from scripts.jra_calibration_ab import calib_metrics, race_normalize  # noqa: E402
from scripts.jra_prob_scoring import JRA_COURSES, build_population  # noqa: E402
from scripts.train_jra_out_rate import featurize as prod_featurize  # noqa: E402
from src import jra_protocol  # noqa: E402
from src.indices.composite import (  # noqa: E402
    OUT_PROB_FEATURE_NAMES,
    SUBINDEX_SOURCE_SQL,
    V28_FEATURE_NAMES,
    place_slots_for_field,
)
from src.indices.past_form import (  # noqa: E402
    CourseFeature,
    PastRunStore,
    RaceContext,
    build_past_form_features_bulk,
    load_course_features,
    load_past_run_store,
    past_form_feature_row,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_iswin")

MODELS_DIR = _root / "models"
MODEL_PATH = MODELS_DIR / "v28_iswin_calib.txt"
METRICS_PATH = MODELS_DIR / "v28_iswin_calib_metrics.json"

# v28 の新特徴（34列の後ろに付く4列）。順序の正本は `composite.V28_FEATURE_NAMES`
NEW_FEATURE_NAMES: list[str] = list(V28_FEATURE_NAMES[len(OUT_PROB_FEATURE_NAMES):])

PARAMS = dict(
    objective="binary", metric="binary_logloss", num_leaves=31, max_depth=6,
    min_data_in_leaf=100, lambda_l1=0.1, lambda_l2=0.1, learning_rate=0.05,
    feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=5, verbose=-1,
)
# early stopping の上限。VAL で best_iteration を選ぶ。
# ⚠️ `scripts/jra_winplace_feature_ab.py` が PARAMS / MAX_ROUND を import して
#    4腕の学習に使っている（検証と本番でハイパラを共有するため）。名前を変えないこと。
MAX_ROUND = 2000

# 🔴 検証 `jra_place_residual_diag.FETCH_SQL`（= `jra_winplace_feature_ab` が使う SQL）と
#    同じ母集団条件。表示用の列（race_name / horse_name 等）だけ落とし、
#    `r.course`（`racecourse_features` を引くキー）を足してある。
#
#    サブ指数は `SUBINDEX_SOURCE_SQL`（version >= SUBINDEX_MIN_VERSION の最大版）で引く。
#    🔴 特定の版に固定すると本番が版を上げた瞬間に学習データが静かに凍結する。
V28_FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, ci.race_id, ci.horse_id, rr.horse_number,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade, r.course,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN {JRA_COURSES}
"""


def dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


def _query(conn: object, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()  # type: ignore[attr-defined]
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def attach_serving_field(df: pd.DataFrame) -> pd.DataFrame:
    """🔴 レース単位の「フィールド」を**配信条件（エントリー全馬）**で定義する。

    ## なぜエントリー全馬なのか（2026-09-04・PR #462 のレビュー指摘1+2）

    v28 の最初の実装は、レース単位の量（`place_slots` と `pace_handicap_pit` の
    `pace_type`）を **`build_population` 後の行**＝ `abnormality_code ∈ {1,2}`
    （取消・除外）と `finish_position` NULL/≤0 を落とした**完走馬だけ**で決めていた。
    ところが配信側 `composite.calculate_and_save` は `race_entries` の**全馬**で決める。

    🔴 **配信は取消を物理的に知り得ない。** `race_entries` に取消を示す列は無く、
    `abnormality_code` は `race_results`＝レース確定後にしか存在しない。
    したがって「配信を学習に合わせる」ことはできない。**学習を配信に合わせる。**

    実測（JRA 11,592レース）:

    | 事象 | 件数 | 割合 |
    |---|---|---|
    | エントリー数 ≠ 完走数 | 1,293 | **11.15%**（差 +1 が 1,112 / +2 が 107 / +3 が 16 / +5 が 1） |
    | そのうち `place_slots` が変わる | 100 | **0.863%** |

    エントリー数で決めると 11.15% のレースで `pace_type` の skew が消える
    （`pace_type` はレース単位なので、ずれると `PACE_SCORE_TABLE` 経由で
    **そのレース全馬**の `pace_handicap_pit` が動く）。
    代償は 0.863% のレースでラベルが払戻規則（出走頭数≥8 で3着まで）とずれること。
    **11.15% を消すほうが桁で大きい**という判断（ユーザー決定・2026-09-04）。

    ## 🔴 文脈とラベルを分離する

    - **文脈（レース単位の量）= エントリー全馬**: `place_slots` / `pace_type` /
      `field_head_count`。この関数が付ける `n_entries` / `place_slots` がそれ
    - **ラベルに使う行 = 完走馬のみ**: `jra_prob_scoring.build_population` が
      従来どおり `abnormality_code ∈ {1,2}` と `finish_position` NULL/≤0 を落とす

    つまり `attach_serving_field` → `attach_past_form` → `build_population` の順に
    通す。`build_population` は `place_slots` を完走数から作り直すので、
    `load_v28_dataset` がそのあと `place_slots`（配信条件）で上書きし、
    完走数側は `place_slots_finishers` として残して監視に使う。

    Args:
        df: `build_population` を**掛ける前**の DataFrame（`race_id` を持つこと）。
            1行 = 1エントリー（`race_entries` × `calculated_indices` の JOIN 結果）。

    Returns:
        `n_entries` / `place_slots` を足した DataFrame。
    """
    out = df.copy()
    n_entries = out.groupby("race_id")["race_id"].transform("size")
    out["n_entries"] = n_entries.astype(int)
    out["place_slots"] = [place_slots_for_field(int(v)) for v in out["n_entries"]]
    return out


def attach_past_form(
    df: pd.DataFrame,
    conn: object,
    *,
    end: str,
    store: PastRunStore | None = None,
    course_features: dict[str, CourseFeature] | None = None,
) -> pd.DataFrame:
    """🔴 v28 の新特徴4列を `past_form` モジュール経由で付ける（**別実装をしない**）。

    🔴 **フィールド（= `pace_handicap_pit` の `pace_type` を決める馬の集合）は
    「そのレースのエントリー全馬」にする。** つまり `build_population` を掛ける
    **前**の df をこの関数に渡すこと（`attach_serving_field` の docstring を参照）。
    配信側は `composite.calculate_and_save` の `results` ＝ `race_entries` の全馬を
    渡すので、これで両者が同じ集合になる。

    ⚠️ 掛ける順序を `build_population` → `attach_past_form` に戻すと、
    11.15% のレースで `pace_type` が配信とずれる（レビュー指摘1+2 の再発）。

    `RaceContext.head_count` には `races.head_count` を渡す（検証実装
    `jra_winplace_feature_ab._pace_handicap_pit` と同一）。これは
    `_apply_field_size_adjustment` の頭数にだけ使われ、NULL ならフィールドの
    馬数へフォールバックする。
    🔴 **`place_slots` はここから作らない**（`head_count` は発走前 NULL）。

    Args:
        df: `build_population` を**掛ける前**の DataFrame（`race_id` / `horse_id` /
            `date` / `course` / `head_count` を持つこと）。
        conn: psycopg2 の接続。`store` と `course_features` を両方渡すなら未使用。
        end: 過去走の取得範囲の上限 `YYYYMMDD`。**PIT の保証ではない**
            （PIT は `PastRunStore.before` の `date <` が担う）。
        store: 事前に作った過去走の索引（A/B 比較で使い回す用。既定は毎回ロード）。
        course_features: 事前に引いたコース特性（同上）。

    Returns:
        `PAST_FORM_FEATURE_NAMES` の4列（欠損は NaN）と `runner_type` を足した DataFrame。
    """
    if store is None:
        store = load_past_run_store(conn, end_date=end)
    if course_features is None:
        course_features = load_course_features(conn)

    race_fields = []
    for race_id, g in df.groupby("race_id", sort=False):
        r0 = g.iloc[0]
        course = None if r0["course"] is None else str(r0["course"])
        race_fields.append((
            race_id,
            RaceContext(
                date=str(r0["date"]),
                course=course,
                head_count=r0["head_count"],
                course_feature=course_features.get(course or ""),
            ),
            [int(h) for h in g["horse_id"]],
        ))

    feats = build_past_form_features_bulk(race_fields, store=store)

    rows = [
        past_form_feature_row(feats.get((rid, int(hid)), {}))
        for rid, hid in zip(df["race_id"], df["horse_id"])
    ]
    out = df.copy()
    extra = pd.DataFrame(rows, columns=NEW_FEATURE_NAMES, index=out.index)
    for c in NEW_FEATURE_NAMES:
        out[c] = extra[c]
    out["runner_type"] = [
        feats.get((rid, int(hid)), {}).get("runner_type", "unknown")
        for rid, hid in zip(df["race_id"], df["horse_id"])
    ]
    return out


def load_v28_dataset(start: str, end: str) -> pd.DataFrame:
    """🔴 v28 の学習データセットを作る（単勝ヘッドと複勝の独立ヘッドで共有する）。

    `train_jra_placed_head.py` もこの関数を呼ぶ。2本のヘッドが別々にデータを
    組み立てると、そこだけ挙動がずれうるため。

    ## 🔴 文脈はエントリー全馬・ラベルは完走馬のみ（レビュー指摘1+2・2026-09-04）

    順序に意味がある。**変えないこと。**

        prod_featurize        … 34列の変換（本番 `_build_v26_features` と同一）
        attach_serving_field  … n_entries / place_slots を**エントリー全馬**から
        attach_past_form      … pace_type も**エントリー全馬**から（この前に絞らない）
        build_population      … ここではじめてラベル行（完走馬）に絞る

    `build_population` は `place_slots` を完走数から作り直すので、そのあと
    配信条件（エントリー数）の値で上書きし、完走数側は `place_slots_finishers`
    に退避する（`train_jra_placed_head.sanity_check_place_slots` が両者の差を記録する）。

    Returns:
        38特徴（`V28_FEATURE_NAMES`）+ `place_slots`（**エントリー数から**）/
        `place_slots_finishers` / `n_entries` / `n_runners` / `finish_position`
        / `date` / `race_id` / `horse_id` を持つ DataFrame。
    """
    conn = psycopg2.connect(dsn())
    logger.info("対象レース取得 %s〜%s ...", start, end)
    raw = _query(conn, V28_FETCH_SQL, {"start": start, "end": end})
    raw["date"] = raw["date"].astype(str)
    logger.info("  %d行 / %dレース", len(raw), raw["race_id"].nunique())
    df = build_v28_frame(raw, conn, end=end)
    conn.close()

    miss = {c: round(float(df[c].isna().mean() * 100), 2) for c in NEW_FEATURE_NAMES}
    logger.info("新特徴の欠損率(%%): %s （🔴 NaN のまま学習に渡す）", miss)
    return df


def build_v28_frame(
    raw: pd.DataFrame,
    conn: object,
    *,
    end: str,
    store: PastRunStore | None = None,
    course_features: dict[str, CourseFeature] | None = None,
) -> pd.DataFrame:
    """🔴 生の DB 行 → 学習用の DataFrame。**順序が正本**（`load_v28_dataset` の中身）。

    DB 接続から切り離してあるのは、`tests/test_v28_winplace.py` が擬似接続で
    **この関数そのもの**を通せるようにするため。テストが順序を写経すると、
    そこだけ本番とずれても誰も気付かない。

    Args:
        raw: `V28_FETCH_SQL` の結果（1行 = 1エントリー・`date` は str）。
        conn: psycopg2 互換の接続（`store` / `course_features` を渡すなら未使用）。
        end: 過去走の取得範囲の上限。
        store / course_features: 事前構築ぶんの使い回し（任意）。
    """
    df = prod_featurize(raw)         # 🔴 本番 `_build_v26_features` と同一の変換（fillna(50.0)）
    df = attach_serving_field(df)    # 🔴 レース単位の量は**エントリー全馬**から（配信条件）
    df = attach_past_form(df, conn, end=end, store=store, course_features=course_features)

    # 🔴 ここではじめてラベル行に絞る（`abnormality_code ∈ {1,2}` / finish_position NULL・≤0）
    n_before = len(df)
    df["_place_slots_entries"] = df["place_slots"]        # build_population が上書きするので退避
    df = build_population(df)        # n_runners と「完走数ベースの place_slots」を付ける
    df["place_slots_finishers"] = df["place_slots"]       # 払戻規則そのもの（監視用）
    df["place_slots"] = df.pop("_place_slots_entries")    # 🔴 学習が使うのは配信条件のほう
    logger.info("母集団: %d行 → %d行（ラベル行のみ。文脈はエントリー全馬のまま）",
                n_before, len(df))
    return df.reset_index(drop=True)


def is_win_label(df: pd.DataFrame) -> np.ndarray:
    """単勝ヘッドのラベル（1着 = 1）。"""
    return (
        (pd.to_numeric(df["finish_position"], errors="coerce") == 1).astype(int).to_numpy()
    )


def _xy(df: pd.DataFrame, label_fn: Callable[[pd.DataFrame], np.ndarray]
        ) -> tuple[np.ndarray, np.ndarray]:
    return df[V28_FEATURE_NAMES].to_numpy(dtype=float), label_fn(df)


def train_model(df: pd.DataFrame, label_fn: Callable[[pd.DataFrame], np.ndarray],
                seed: int = 0, num_round: int | None = None,
                valid_df: pd.DataFrame | None = None) -> lgb.Booster:
    """38特徴の binary ヘッドを学習する（単勝ヘッド・複勝の独立ヘッドで共有）。

    `valid_df` を渡すと early stopping で best_iteration を選ぶ。
    渡さない場合は `num_round` 固定ラウンドで学習する（本番 refit 用）。
    """
    X, y = _xy(df, label_fn)
    ds = lgb.Dataset(X, y, feature_name=list(V28_FEATURE_NAMES))
    if valid_df is None:
        return lgb.train(dict(PARAMS, seed=seed), ds, num_boost_round=num_round or MAX_ROUND)
    Xv, yv = _xy(valid_df, label_fn)
    dv = lgb.Dataset(Xv, yv, reference=ds)
    return lgb.train(dict(PARAMS, seed=seed), ds, num_boost_round=MAX_ROUND,
                     valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])


def _fmt(v: object, w: int) -> str:
    fv = float(v)  # type: ignore[arg-type]
    return f"{'NaN':>{w}}" if np.isnan(fv) else f"{fv:>{w}.3f}"


def visual_check(df: pd.DataFrame, preds: np.ndarray, title: str) -> None:
    """🔴 実データを1レース表示して目視確認する（`CLAUDE.md` 検証の作法）。

    見るのは2点: `Σp_win = 1.0`（レース内正規化後）/ 新特徴が **NaN のまま**渡っていること。
    """
    d = df.copy()
    d["_p"] = preds
    rid = int(d["race_id"].iloc[0])
    g = d[d["race_id"] == rid].sort_values("horse_number")
    tot = float(g["_p"].sum())
    print("\n" + "=" * 104)
    print(f"🔴 目視確認 [{title}] race_id={rid} {g.iloc[0]['date']} "
          f"n={len(g)} place_slots={int(g.iloc[0]['place_slots'])}")
    print("=" * 104)
    print(f"{'馬番':>4}{'着':>4}{'脚質ord':>9}{'着順分散5':>11}{'勝複比5':>10}"
          f"{'pace_pit':>10}{'raw':>12}{'p_win':>10}")
    for _, r in g.iterrows():
        print(f"{int(r['horse_number']):>4}{int(r['finish_position']):>4}"
              f"{_fmt(r['runner_type_ord'], 9)}{_fmt(r['finish_var5'], 11)}"
              f"{_fmt(r['win_place_ratio5'], 10)}{_fmt(r['pace_handicap_pit'], 10)}"
              f"{float(r['_p']):>12.5f}{float(r['_p']) / tot:>10.5f}")
    print(f"{'Σ':>4}{'':>4}{'':>9}{'':>11}{'':>10}{'':>10}{tot:>12.5f}"
          f"{float((g['_p'] / tot).sum()):>10.5f}   ← 期待 Σp_win=1.00000")


def main() -> None:
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
    p.add_argument("--skip-honest-test", action="store_true",
                   help="🔴 TEST 窓での honest test を省く。TEST 窓を既に消費した後に "
                        "実装の修正でモデルだけ作り直すときに使う（refit 境界は不変）")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    logger.info("protocol: %s", jra_protocol.describe())

    df = load_v28_dataset(args.start, args.end)
    logger.info("全データ: %d行 %dレース (%s〜%s) 特徴=%d列",
                len(df), df["race_id"].nunique(), df["date"].min(), df["date"].max(),
                len(V28_FEATURE_NAMES))

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end].reset_index(drop=True)
    if args.skip_honest_test:
        # 🔴 TEST 窓（`jra_protocol.TEST_START` 以降）に一切触らない。
        #    ラウンド選択は VAL の early stopping だけで行うので影響しない
        logger.warning("--skip-honest-test: TEST 窓 %d行を評価に使わない", len(te))
        te = te.iloc[0:0]
    logger.info("train=%d valid=%d test=%d", len(tr), len(va), len(te))
    if not len(tr) or not len(va):
        raise SystemExit("train / valid が空。--start / --train-end / --valid-end を確認すること")

    # ── ラウンド数を VAL で選ぶ（seed 平均の best_iteration 中央値） ──
    best_iters, te_preds = [], []
    for seed in seeds:
        m = train_model(tr, is_win_label, seed=seed, valid_df=va)
        best_iters.append(int(m.best_iteration))
        if len(te):
            te_preds.append(m.predict(te[V28_FEATURE_NAMES].to_numpy(dtype=float),
                                      num_iteration=m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info("best_iter=%s → refit rounds=%d", best_iters, n_rounds)

    metrics: dict = {
        "head": "is_win (v28 / 38特徴)",
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "seeds": seeds,
        "best_iters": best_iters,
        "refit_rounds": n_rounds,
        "features": list(V28_FEATURE_NAMES),
        "n_features": len(V28_FEATURE_NAMES),
        "params": PARAMS,
        "new_feature_missing_pct": {
            c: round(float(df[c].isna().mean() * 100), 2) for c in NEW_FEATURE_NAMES
        },
    }

    # ── honest test: TRAIN のみで学習したモデルを TEST に一度だけ当てる ──
    # ⚠️ 本番 refit モデル（VAL を含む）で TEST を測ると VAL 分だけ有利になるため、
    #    較正の数字は必ずこちらの train-only モデルで出す。
    if len(te):
        raw_te = np.mean(te_preds, axis=0)
        norm_te = race_normalize(raw_te, te["race_id"])
        y_te = is_win_label(te)
        cm_raw = calib_metrics(raw_te, y_te)
        cm_norm = calib_metrics(norm_te, y_te)
        top = (pd.DataFrame({"r": te["race_id"].values, "p": norm_te, "y": y_te})
               .sort_values("p", ascending=False).groupby("r").head(1))
        metrics["test"] = {
            "period": [te["date"].min(), te["date"].max()],
            "n": int(len(te)), "n_races": int(te["race_id"].nunique()),
            "iswin_raw_ece": round(cm_raw["ece"], 4),
            "iswin_norm_ece": round(cm_norm["ece"], 4),
            "iswin_norm_mce": round(cm_norm["mce"], 4),
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
        logger.info("honest test (%s〜%s, %dR): iswin norm ECE=%.4f / "
                    "本命 予測=%.3f 実測勝率=%.3f",
                    te["date"].min(), te["date"].max(), te["race_id"].nunique(),
                    cm_norm["ece"], top["p"].mean(), top["y"].mean())
        visual_check(te, raw_te, "honest test / is_win v28")
    elif args.skip_honest_test:
        metrics["honest_test"] = "🔴 --skip-honest-test で省略。TEST 窓（2026Q3）は docs/jra_winplace_structure_plan_2026_09_04.md §18 の一度きり評価で消費済みで、PR #462 レビュー指摘1+2 の修正後に再測定してはいけない。このモデルの honest な数字は 2026Q4 のローリングで出す。修正前後の比較は VAL で行った: docs/model_verification/jra_v28_field_definition_ab.json"
    else:
        logger.warning("test 期間にデータなし（TEST_START=%s）", jra_protocol.TEST_START)

    # ── 本番モデル: **TEST_START の前日まで**で refit ──
    # （seed 平均は取れないため先頭 seed で固定ラウンド学習）。
    fit = df[df["date"] <= args.refit_end]
    logger.info("refit: %d行 (%s〜%s)", len(fit), fit["date"].min(), fit["date"].max())
    final = train_model(fit, is_win_label, seed=seeds[0], num_round=n_rounds)
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
