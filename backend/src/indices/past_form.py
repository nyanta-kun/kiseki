"""v28 の過去走由来特徴（脚質 / 着順分散5 / 勝率複勝率比5 / pace_handicap_pit）。

## このモジュールが存在する理由（🔴 train/serve skew の予防）

v28 で単勝ヘッドに足す4特徴は **過去走から算出する新種の特徴**である。学習は DB を
一括で読み、配信は `composite.py` の live 経路でレース単位に読む。
**この2経路が別実装になった瞬間に train/serve skew が入る。**

前例: 地方 v13→v14 は「市場込みで学習・市場なしで配信」という skew で
**指数1位馬の勝率を 9pt 落とした**（全9四半期で +6.6〜+13.6pt の差）。

したがって本モジュールは

- **特徴の計算は DB に触らない純関数**（`compute_past_form_features` /
  `compute_race_past_form_features`）に閉じ込める
- DB アクセスは**薄いアダプタ**（学習用の一括 / 配信用のレース単位）に分離し、
  **どちらも同じ純関数へ渡す**

という形にしてある。特徴の定義を変えるときは純関数だけを触ること。

## 🔴 仕様の正本

`backend/scripts/jra_winplace_feature_ab.py`（Phase C）と、そこが import している
`backend/scripts/jra_place_residual_diag.py` の `PastRuns` / `build_pit_features`。
**検証で出した数字（§11.1 の Δ=−0.00750 / §18.1 の 2026Q3 確認成功）はこの実装で
出したものなので、1ビットでも定義が違えば検証結果は本番に対して無効になる。**
`backend/tests/test_past_form.py::test_parity_with_winplace_feature_ab_*` が
同一入力に対する値の一致を機械的に固定している。

## 特徴の定義（`jra_winplace_feature_ab.FEAT_EXTRA` と同名・同定義）

| key | 定義 | 欠損 |
|---|---|---|
| `runner_type_ord` | 脚質 escape=0 / leader=1 / mid=2 / closer=3 | `unknown` → `None` |
| `finish_var5` | 直近5走の `finish_position` の標本分散（ddof=1） | 5走揃わない → `None` |
| `win_place_ratio5` | 直近5走の 勝ち数 / 3着内数 | 5走揃わない → `None` / 3着内0走 → `-1.0` |
| `pace_handicap_pit` | PIT 安全な展開ハンデの部分再構成（下記） | 常に値が出る |

🔴 **欠損は `None`（呼び出し側で NaN）であって `50.0` ではない。**
検証時から NaN 意味論で測っている（§16.2-5）。34列側の `fillna(50.0)` は維持する
（`nan` 腕は §11.1 で不採用）。

## 🔴 point-in-time の規律

- 対象レースの `date` より **厳密に前**（`date <`）の走だけを使う。**同日の先行レースも
  除外**する（本番 `PaceHandicapCalculator._get_past_results_batch` と同じ挙動）
- 🔴 `race_results.running_style` は**そのレースの結果列**なので使わない。脚質は
  過去走の `passing_4 / head_count` から `_determine_runner_type` で導出する
- 🔴 `passing_1` は充足 44% / `passing_2` は 50% / `margin` は全行 NULL なので使わない。
  使うのは `passing_3` / `passing_4`（99%）のうち `passing_4` だけ

## 🔴 `pace_handicap_pit` は本番 `PaceHandicapCalculator` ではない

本番 `PaceHandicapCalculator` は **point-in-time ではない**（§11.3・新規発見）:

- `_get_frame_stats`（枠別勝率統計・**日付条件なし**）
- `_ensure_first3f_medians`（全期間の中央値・日付条件なし）
- `_get_jockey_style`（`jockey_running_style_stats` の**現在のスナップショット**）
- `_get_avg_last3f`（日付条件なし）

Phase C はここから**日付に依存しない部分だけ**を取り出して `pace_handicap_pit` とした。
本モジュールも同じ範囲に限定する（§16.2-3）。含めたのは:

- `PACE_SCORE_TABLE[runner_type][pace_type]`（本番定数を import）
- `pace_type = PaceHandicapCalculator._predict_pace(runner_types)`（本番メソッドを import）。
  ただし `runner_types` は**馬の脚質のみ**で作る（本番は `_predict_actual_runner_type` で
  騎手戦法を 0.4 混ぜるが、その統計が PIT でない）
- `_apply_course_adjustment` / `_apply_field_size_adjustment`（本番メソッドを import。
  `keiba.racecourse_features` は静的表なので PIT 安全）
- 最後に `[INDEX_MIN, INDEX_MAX]` へクリップ。**本番と違い `round(_, 1)` はしない**
  （検証実装が丸めていないため）

落としたのは: 枠別勝率補正 / 上がり3Fボーナス / 開催バイアス補正 / 前走ハイペース
リバウンド。理由は上の PIT 違反、および前走ハイペースリバウンドは `passing_1` の
充足が 44% でそもそも半分発火しないこと（§訂正5）。

**閾値・スコア表は一切コピーしていない。** すべて `pace_handicap` から import している。

## 使い方

学習（一括）::

    store = load_past_run_store(conn, end_date="20260628")
    course_features = load_course_features(conn)
    feats = build_past_form_features_bulk(
        (
            (race_id,
             RaceContext(date=date, course=course, head_count=head_count,
                         course_feature=course_features.get(course)),
             horse_ids)
            for race_id, date, course, head_count, horse_ids in race_fields
        ),
        store=store,
    )
    row = past_form_feature_row(feats[(race_id, horse_id)])   # 欠損は NaN

配信（レース単位・`composite.py` から呼ぶ）::

    feats = await build_past_form_features_for_race(db, race=race, horse_ids=horse_ids)
    row = past_form_feature_row(feats[horse_id])   # 欠損は NaN
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RacecourseFeatures, RaceResult
from .pace_handicap import (
    INDEX_MAX,
    INDEX_MIN,
    LOOKBACK_RACES,
    PACE_SCORE_TABLE,
    PaceHandicapCalculator,
)

__all__ = [
    "PAST_FORM_FEATURE_NAMES",
    "PAST_N",
    "RUNNER_TYPE_ORD",
    "CourseFeature",
    "PastRun",
    "PastRunStore",
    "RaceContext",
    "build_past_form_features_bulk",
    "build_past_form_features_for_race",
    "compute_past_form_features",
    "compute_race_past_form_features",
    "compute_runner_type",
    "fetch_past_runs_for_race",
    "get_course_feature",
    "load_course_features",
    "load_past_run_store",
    "past_form_feature_row",
    "predict_pace_type",
    "select_past_runs",
]

# 着順分散・勝率複勝率比のルックバック（計画 §4.1「過去5走」）。
# 脚質のルックバックは本番と同じ `pace_handicap.LOOKBACK_RACES`（=10）を使う。
PAST_N = 5

# 脚質の順序符号化。`jra_winplace_feature_ab.RUNNER_TYPE_ORD` と同一。
# `unknown` はこの表に無い ＝ 欠損（None → NaN）になる。
RUNNER_TYPE_ORD: dict[str, float] = {
    "escape": 0.0,
    "leader": 1.0,
    "mid": 2.0,
    "closer": 3.0,
}

# LightGBM へ渡す列の**正準の順序**。`jra_winplace_feature_ab.FEAT_EXTRA` と同一・同順。
# 🔴 学習も配信もこの順序で 34列の後ろに連結する（順序がずれると静かに壊れる）。
PAST_FORM_FEATURE_NAMES: list[str] = [
    "runner_type_ord",
    "finish_var5",
    "win_place_ratio5",
    "pace_handicap_pit",
]

# `_predict_pace` / `_apply_course_adjustment` / `_apply_field_size_adjustment` は
# self を参照しないが、本番のメソッドをそのまま呼ぶために DB を持たないインスタンスを作る
# （`jra_winplace_feature_ab._pace_handicap_pit` と同じ手口）。
_PACE_CALC: PaceHandicapCalculator = PaceHandicapCalculator.__new__(PaceHandicapCalculator)


# ---------------------------------------------------------------------------
# 入出力の型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PastRun:
    """対象レースより前の1走。

    Attributes:
        date: 開催日 `YYYYMMDD`。**文字列で持つ**（DB がそう持っており、比較も辞書順で
            日付順と一致するため）。
        race_id: そのレースの id。同日に複数走がある場合の決定的な並び順にだけ使う。
        finish_position: 着順。`None` / 0 は「着順が付かなかった」扱いで
            着順分散・勝率複勝率比から除外される。
        passing_4: 4コーナー通過順位。脚質判定に使う唯一の通過順位
            （`passing_1` 44% / `passing_2` 50% しか充足していないため）。
        head_count: そのレースの出走頭数。`passing_4 / head_count` が脚質の相対位置。
    """

    date: str
    race_id: int | None = None
    finish_position: float | None = None
    passing_4: float | None = None
    head_count: float | None = None


@dataclass(frozen=True, slots=True)
class CourseFeature:
    """`keiba.racecourse_features` の静的なコース特性（日付に依存しない＝PIT 安全）。"""

    straight_distance: float | None = None
    corner_tightness: float | None = None
    start_to_corner_m: float | None = None


@dataclass(frozen=True, slots=True)
class RaceContext:
    """対象レースの文脈。

    Attributes:
        date: 対象レースの開催日 `YYYYMMDD`。🔴 過去走はこの日付より **厳密に前**
            （`date <`）のものだけが使われる。同日の先行レースも除外される。
        course: 競馬場コード（`races.course`・2桁文字列）。`course_feature` を
            引くためのキー。
        head_count: `races.head_count`。`None` ならフィールドの頭数で代替する。
        course_feature: `course` に対応するコース特性。`None` ならコース補正・
            多頭数補正が丸ごと no-op になる（本番と同じ挙動）。
    """

    date: str
    course: str | None = None
    head_count: int | float | None = None
    course_feature: CourseFeature | None = None


# ---------------------------------------------------------------------------
# 純関数（🔴 DB に触らない。学習も配信もここを通る）
# ---------------------------------------------------------------------------


def _to_float_or_none(value: Any) -> float | None:
    """`None` / NaN を `None` に、それ以外を `float` にする。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def select_past_runs(
    past_runs: Iterable[PastRun], *, before_date: str, limit: int
) -> list[PastRun]:
    """🔴 point-in-time の関門。`before_date` より**厳密に前**の直近 `limit` 走を新しい順で返す。

    Args:
        past_runs: 対象馬の過去走。順序は任意（本関数が並べ替える）。
        before_date: 対象レースの開催日 `YYYYMMDD`。**同日は含めない**。
        limit: 取り出す最大走数（新しい方から）。

    Returns:
        新しい順（date 降順）の `PastRun` のリスト。1走も無ければ空リスト。

    Note:
        同日に複数走がある場合は `race_id` を第2キーにして決定的に並べる。
        検証スクリプト（`jra_place_residual_diag.PastRuns`）は SQL の返却順に
        依存していてこの場合だけ非決定的だった。JRA で同一馬が同日に2走することは
        事実上無いので値は変わらないが、本モジュールでは決定的にしてある。
    """
    if limit <= 0:
        return []
    eligible = [r for r in past_runs if str(r.date) < str(before_date)]
    eligible.sort(key=lambda r: (str(r.date), r.race_id if r.race_id is not None else 0))
    if not eligible:
        return []
    return eligible[-limit:][::-1]


def compute_runner_type(past_runs: Sequence[PastRun]) -> str:
    """過去走（新しい順・PIT 済み）から脚質を判定する。

    🔴 閾値（`RUNNER_TYPE_THRESHOLDS`）をコピーせず、本番
    `PaceHandicapCalculator._determine_runner_type` をそのまま呼ぶ。
    行の形（`row.RaceResult.passing_4` / `row.Race.head_count`）だけ合わせて渡す。

    Returns:
        `"escape"` / `"leader"` / `"mid"` / `"closer"` / `"unknown"`。
        過去走が無い、または `passing_4` / `head_count` が全て欠損なら `"unknown"`。
    """
    if not past_runs:
        return "unknown"
    rows = [
        SimpleNamespace(
            RaceResult=SimpleNamespace(passing_4=_to_float_or_none(r.passing_4)),
            Race=SimpleNamespace(head_count=_to_float_or_none(r.head_count)),
        )
        for r in past_runs
    ]
    return PaceHandicapCalculator._determine_runner_type(_PACE_CALC, rows)


def predict_pace_type(runner_types: Iterable[str]) -> str:
    """フィールド全馬の脚質から予想ペース（`fast` / `normal` / `slow`）を決める。

    🔴 本番 `PaceHandicapCalculator._predict_pace` をそのまま呼ぶ（閾値をコピーしない）。
    ただし渡すのは**馬の脚質のみ**で、本番の `_predict_actual_runner_type`（騎手戦法を
    0.4 で混ぜる）は通さない。騎手戦法統計 `jockey_running_style_stats` が
    現在のスナップショットで point-in-time でないため。
    """
    return PaceHandicapCalculator._predict_pace(
        _PACE_CALC, {i: rt for i, rt in enumerate(runner_types)}
    )


def compute_pace_handicap_pit(
    runner_type: str,
    *,
    pace_type: str,
    field_head_count: int,
    course_feature: CourseFeature | None,
) -> float:
    """PIT 安全な展開ハンデの部分再構成（1頭ぶん）。

    🔴 本番 `PaceHandicapCalculator.calculate_batch` **ではない**。落とした補正と
    その理由はモジュール docstring を参照。本番と違い `round(_, 1)` もしない。
    """
    score = PACE_SCORE_TABLE.get(runner_type, PACE_SCORE_TABLE["unknown"])[pace_type]
    feat = (
        SimpleNamespace(
            straight_distance=course_feature.straight_distance,
            corner_tightness=course_feature.corner_tightness,
            start_to_corner_m=course_feature.start_to_corner_m,
        )
        if course_feature is not None
        else None
    )
    score = PaceHandicapCalculator._apply_course_adjustment(
        _PACE_CALC, score, runner_type, feat
    )
    score = PaceHandicapCalculator._apply_field_size_adjustment(
        _PACE_CALC, score, runner_type, field_head_count, feat
    )
    return min(INDEX_MAX, max(INDEX_MIN, score))


def compute_past_form_features(
    past_runs: Iterable[PastRun],
    *,
    race_context: RaceContext,
    pace_type: str | None = None,
    field_head_count: int | None = None,
) -> dict[str, Any]:
    """🔴 本モジュールの中核。過去走のリストから1頭ぶんの特徴 dict を作る純関数。

    **DB に触らない。** 学習側も配信側もこの関数（またはこれを呼ぶ
    `compute_race_past_form_features`）を通ること。

    Args:
        past_runs: 対象馬の過去走。**順序は任意**（`select_past_runs` が
            `race_context.date` より厳密に前のものだけを新しい順に選び直す）。
            対象レースより後の走が混ざっていても結果は変わらない。
        race_context: 対象レースの文脈（`date` / `course` / `head_count` /
            `course_feature`）。
        pace_type: フィールド全馬の脚質から決めた予想ペース。`None` なら
            `pace_handicap_pit` は `None` になる。**レース単位で1つ決まる量なので、
            1頭だけ見ても計算できない。** 通常は
            `compute_race_past_form_features` から渡される。
        field_head_count: `_apply_field_size_adjustment` に渡す頭数。`None` なら
            `race_context.head_count` を使う。

    Returns:
        次の key を持つ dict。

        - `runner_type` (str): `escape` / `leader` / `mid` / `closer` / `unknown`
        - `runner_type_ord` (float | None): 0/1/2/3。`unknown` は `None`
        - `finish_var5` (float | None): 直近5走の着順の標本分散（ddof=1）。
          5走揃わなければ `None`
        - `win_place_ratio5` (float | None): 直近5走の 勝ち数/3着内数。
          5走揃わなければ `None`、3着内が0走なら `-1.0`
        - `pace_handicap_pit` (float | None): `pace_type` を渡さなければ `None`
        - `n_past5` (int): 直近5走のうち着順が有効だった走数（診断用）

        🔴 欠損は `None`（呼び出し側で NaN）。**`50.0` で埋めてはいけない。**
    """
    runs = list(past_runs)

    # --- 脚質: 本番と同じ直近 LOOKBACK_RACES 走 ---
    rt_runs = select_past_runs(
        runs, before_date=race_context.date, limit=LOOKBACK_RACES
    )
    runner_type = compute_runner_type(rt_runs)

    # --- 着順分散 / 勝率複勝率比: 直近 PAST_N 走 ---
    # 🔴 「直近5走を取ってから着順が有効なものを残す」順序であること。
    #     「着順が有効な走を5走集める」ではない（検証実装がそうなっている）。
    fp_runs = select_past_runs(runs, before_date=race_context.date, limit=PAST_N)
    finishes = [
        f for f in (_to_float_or_none(r.finish_position) for r in fp_runs)
        if f is not None and f > 0
    ]
    n_past5 = len(finishes)
    if n_past5 < PAST_N:
        finish_var5: float | None = None
        win_place_ratio5: float | None = None
    else:
        arr = np.asarray(finishes, dtype=float)
        finish_var5 = float(np.var(arr, ddof=1))
        n_win = float((arr == 1).sum())
        n_place = float((arr <= 3).sum())
        win_place_ratio5 = n_win / n_place if n_place > 0 else -1.0

    # --- pace_handicap_pit（レース単位の pace_type が要る） ---
    pace_handicap_pit: float | None = None
    if pace_type is not None:
        head = field_head_count
        if head is None:
            head = _to_float_or_none(race_context.head_count)  # type: ignore[assignment]
        pace_handicap_pit = compute_pace_handicap_pit(
            runner_type,
            pace_type=pace_type,
            field_head_count=int(head) if head is not None else 0,
            course_feature=race_context.course_feature,
        )

    return {
        "runner_type": runner_type,
        "runner_type_ord": RUNNER_TYPE_ORD.get(runner_type),
        "finish_var5": finish_var5,
        "win_place_ratio5": win_place_ratio5,
        "pace_handicap_pit": pace_handicap_pit,
        "n_past5": n_past5,
    }


def compute_race_past_form_features(
    past_runs_by_horse: Mapping[int, Iterable[PastRun]],
    *,
    race_context: RaceContext,
) -> dict[int, dict[str, Any]]:
    """レース1本ぶんの特徴を作る純関数。**学習も配信もここを呼ぶ。**

    `pace_handicap_pit` はフィールド全馬の脚質分布から決まる `pace_type` に依存するので、
    1頭ずつでは計算できない。そのため

      1. 全馬の脚質を PIT に導出
      2. その分布から `pace_type` を決定
      3. 各馬の特徴を確定

    の3段で回す。`past_runs_by_horse` に渡す馬の集合（＝フィールド）は、
    **`p_win` をレース内正規化するときの集合と一致させること**
    （検証では `jra_prob_scoring.build_population` 後の行、配信では
    `composite.calculate_and_save` の `results`）。

    Args:
        past_runs_by_horse: `{horse_id: 過去走のリスト}`。対象レースより後の走が
            混ざっていても構わない（PIT フィルタは中で掛かる）。
        race_context: 対象レースの文脈。

    Returns:
        `{horse_id: 特徴 dict}`。dict の key は `compute_past_form_features` と同じ。
    """
    horse_ids = list(past_runs_by_horse)
    runs_by_horse = {hid: list(past_runs_by_horse[hid]) for hid in horse_ids}

    runner_types: dict[int, str] = {}
    for hid in horse_ids:
        rt_runs = select_past_runs(
            runs_by_horse[hid], before_date=race_context.date, limit=LOOKBACK_RACES
        )
        runner_types[hid] = compute_runner_type(rt_runs)

    pace_type = predict_pace_type(runner_types[hid] for hid in horse_ids)

    head = _to_float_or_none(race_context.head_count)
    field_head_count = int(head) if head is not None else len(horse_ids)

    return {
        hid: compute_past_form_features(
            runs_by_horse[hid],
            race_context=race_context,
            pace_type=pace_type,
            field_head_count=field_head_count,
        )
        for hid in horse_ids
    }


def past_form_feature_row(features: Mapping[str, Any]) -> list[float]:
    """特徴 dict を LightGBM に渡す1行（`PAST_FORM_FEATURE_NAMES` 順）に変換する。

    🔴 欠損 `None` は **NaN** にする（`50.0` にしてはいけない）。検証時から
    新特徴だけは NaN 意味論で測っている（§16.2-5）。34列側の `fillna(50.0)` は維持する。
    """
    return [
        float("nan") if features.get(name) is None else float(features[name])
        for name in PAST_FORM_FEATURE_NAMES
    ]


# ---------------------------------------------------------------------------
# 学習用アダプタ（一括・DB-API / psycopg2 経路）
# ---------------------------------------------------------------------------

# 本番 `PaceHandicapCalculator._get_past_results_batch` と同じ条件で過去走を引く。
#   - `abnormality_code = 0`
#   - **course で絞らない**（本番も絞っておらず、地方・海外の走も脚質判定に使っている）
#   - 🔴 `r.date <= %(end)s` は取得範囲の上限にすぎない。point-in-time は
#     `select_past_runs` の `date <`（厳密不等号）で保証される。
PAST_RUNS_SQL = """
SELECT rr.horse_id, rr.race_id, r.date, rr.finish_position, rr.passing_4, r.head_count
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
WHERE rr.abnormality_code = 0
  AND r.date <= %(end)s
ORDER BY rr.horse_id, r.date, rr.race_id
"""

COURSE_FEATURES_SQL = """
SELECT course_code, straight_distance, corner_tightness, start_to_corner_m
FROM keiba.racecourse_features
"""


class PastRunStore:
    """馬ごとの過去走を date 昇順で保持し、任意の日付より前だけを切り出す（学習用）。

    🔴 取り出しは常に `date < 対象レース日` の厳密不等号（`before()` → `select_past_runs`）。
    全期間ぶんを1回だけ読み、レースごとの DB 往復を無くすためのインデックスであって、
    特徴の定義は一切持たない。
    """

    def __init__(self, runs_by_horse: Mapping[int, Sequence[PastRun]]) -> None:
        self._by_horse: dict[int, tuple[list[str], list[PastRun]]] = {}
        for hid, runs in runs_by_horse.items():
            ordered = sorted(
                runs,
                key=lambda r: (str(r.date), r.race_id if r.race_id is not None else 0),
            )
            self._by_horse[int(hid)] = ([str(r.date) for r in ordered], list(ordered))

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any] | Sequence[Any]]) -> PastRunStore:
        """`PAST_RUNS_SQL` の行（dict もしくは列順のタプル）から作る。"""
        runs_by_horse: dict[int, list[PastRun]] = {}
        for row in rows:
            if isinstance(row, Mapping):
                hid = int(row["horse_id"])
                run = PastRun(
                    date=str(row["date"]),
                    race_id=_int_or_none(row.get("race_id")),
                    finish_position=_to_float_or_none(row.get("finish_position")),
                    passing_4=_to_float_or_none(row.get("passing_4")),
                    head_count=_to_float_or_none(row.get("head_count")),
                )
            else:
                hid = int(row[0])
                run = PastRun(
                    date=str(row[2]),
                    race_id=_int_or_none(row[1]),
                    finish_position=_to_float_or_none(row[3]),
                    passing_4=_to_float_or_none(row[4]),
                    head_count=_to_float_or_none(row[5]),
                )
            runs_by_horse.setdefault(hid, []).append(run)
        return cls(runs_by_horse)

    def before(self, horse_id: int, date: str, limit: int = LOOKBACK_RACES) -> list[PastRun]:
        """`date` より**厳密に前**の直近 `limit` 走を新しい順で返す。"""
        rec = self._by_horse.get(int(horse_id))
        if rec is None or limit <= 0:
            return []
        dates, runs = rec
        cut = bisect.bisect_left(dates, str(date))  # runs[:cut] は全て date 未満
        if cut <= 0:
            return []
        return runs[max(0, cut - limit):cut][::-1]

    def horse_ids(self) -> list[int]:
        return list(self._by_horse)


def _int_or_none(value: Any) -> int | None:
    f = _to_float_or_none(value)
    return int(f) if f is not None else None


def load_past_run_store(conn: Any, *, end_date: str) -> PastRunStore:
    """学習用: 全馬・全期間（`end_date` まで）の過去走を1回で読んで索引化する。

    Args:
        conn: DB-API 2.0 の接続（psycopg2 を想定）。
        end_date: 取得範囲の上限 `YYYYMMDD`。**PIT の保証ではない**
            （PIT は `PastRunStore.before` の `date <` が担う）。
    """
    cur = conn.cursor()
    cur.execute(PAST_RUNS_SQL, {"end": end_date})
    rows = cur.fetchall()
    cur.close()
    return PastRunStore.from_rows(rows)


def load_course_features(conn: Any) -> dict[str, CourseFeature]:
    """学習用: `keiba.racecourse_features`（静的表）を全件読む。"""
    cur = conn.cursor()
    cur.execute(COURSE_FEATURES_SQL)
    rows = cur.fetchall()
    cur.close()
    return {
        str(r[0]): CourseFeature(
            straight_distance=_to_float_or_none(r[1]),
            corner_tightness=_to_float_or_none(r[2]),
            start_to_corner_m=_to_float_or_none(r[3]),
        )
        for r in rows
    }


def build_past_form_features_bulk(
    race_fields: Iterable[tuple[Any, RaceContext, Sequence[int]]],
    *,
    store: PastRunStore,
) -> dict[tuple[Any, int], dict[str, Any]]:
    """🔴 学習側の入口。`build_past_form_features_for_race` と対になる一括版。

    学習スクリプトがレース分割のループを自前で書くと、そこだけ配信と挙動が
    ずれうる（例: フィールドの定義、`head_count` のフォールバック）。
    両方をこの層で揃えるために用意してある。**中身は配信側と同じ純関数を呼ぶだけ。**

    Args:
        race_fields: `(race_key, RaceContext, そのレースのフィールドの horse_id)` の列。
            `race_key` は返り値のキーに使うだけで中身は問わない（`race_id` を想定）。
        store: `load_past_run_store` で作った過去走の索引。

    Returns:
        `{(race_key, horse_id): 特徴 dict}`。
    """
    out: dict[tuple[Any, int], dict[str, Any]] = {}
    for race_key, ctx, horse_ids in race_fields:
        feats = compute_race_past_form_features(
            {int(hid): store.before(int(hid), ctx.date) for hid in horse_ids},
            race_context=ctx,
        )
        for hid, f in feats.items():
            out[(race_key, hid)] = f
    return out


# ---------------------------------------------------------------------------
# 配信用アダプタ（レース単位・SQLAlchemy async 経路）
# ---------------------------------------------------------------------------


async def fetch_past_runs_for_race(
    db: AsyncSession,
    horse_ids: Sequence[int],
    *,
    before_date: str,
    exclude_race_id: int | None = None,
) -> dict[int, list[PastRun]]:
    """配信用: レース1本ぶんの過去走を1クエリで引く。

    🔴 本番 `PaceHandicapCalculator._get_past_results_batch` と**同じ条件**:
    `Race.date < before_date` / `abnormality_code = 0` / 対象レース自身を除外 /
    course で絞らない / 各馬 `LOOKBACK_RACES` 走まで。
    """
    if not horse_ids:
        return {}
    conditions = [
        RaceResult.horse_id.in_(list(horse_ids)),
        Race.date < before_date,
        RaceResult.abnormality_code == 0,
    ]
    if exclude_race_id is not None:
        conditions.append(RaceResult.race_id != exclude_race_id)
    stmt = (
        select(RaceResult, Race)
        .join(Race, RaceResult.race_id == Race.id)
        .where(*conditions)
        .order_by(RaceResult.horse_id, Race.date.desc(), RaceResult.race_id.desc())
    )
    rows = (await db.execute(stmt)).all()

    out: dict[int, list[PastRun]] = {int(hid): [] for hid in horse_ids}
    for row in rows:
        rr: RaceResult = row.RaceResult
        ra: Race = row.Race
        bucket = out.setdefault(int(rr.horse_id), [])
        if len(bucket) >= LOOKBACK_RACES:
            continue
        bucket.append(
            PastRun(
                date=str(ra.date),
                race_id=_int_or_none(rr.race_id),
                finish_position=_to_float_or_none(rr.finish_position),
                passing_4=_to_float_or_none(rr.passing_4),
                head_count=_to_float_or_none(ra.head_count),
            )
        )
    return out


async def get_course_feature(db: AsyncSession, course_code: str | None) -> CourseFeature | None:
    """配信用: `keiba.racecourse_features` を1件引く（静的表なので PIT 安全）。"""
    if not course_code:
        return None
    row = (
        await db.execute(
            select(RacecourseFeatures).where(
                RacecourseFeatures.course_code == course_code
            )
        )
    ).scalars().first()
    if row is None:
        return None
    return CourseFeature(
        straight_distance=_to_float_or_none(row.straight_distance),
        corner_tightness=_to_float_or_none(row.corner_tightness),
        start_to_corner_m=_to_float_or_none(row.start_to_corner_m),
    )


async def build_past_form_features_for_race(
    db: AsyncSession,
    *,
    race: Race,
    horse_ids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    """🔴 `composite.py` の live 経路から呼ぶ入口。

    DB から過去走とコース特性を引き、**学習側と同一の純関数**
    `compute_race_past_form_features` に渡すだけ。ここに特徴の定義を書かないこと。

    Args:
        db: 非同期セッション。
        race: 対象レース（`date` / `course` / `head_count` / `id` を使う）。
        horse_ids: フィールド（＝`p_win` をレース内正規化する集合）の馬 id。
            `composite.calculate_and_save` の `results` の `horse_id` を渡す。

    Returns:
        `{horse_id: 特徴 dict}`。key は `compute_past_form_features` の Returns を参照。
        LightGBM の行にするときは `past_form_feature_row` を使う（欠損は NaN）。
    """
    past = await fetch_past_runs_for_race(
        db,
        horse_ids,
        before_date=str(race.date),
        exclude_race_id=race.id,
    )
    course_feature = await get_course_feature(db, race.course)
    ctx = RaceContext(
        date=str(race.date),
        course=race.course,
        head_count=race.head_count,
        course_feature=course_feature,
    )
    return compute_race_past_form_features(
        {int(hid): past.get(int(hid), []) for hid in horse_ids}, race_context=ctx
    )
