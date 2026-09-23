"""JRA ラップ由来の過去走特徴（前半 par 比 / 上がり par 比 / ペース / ラップ形状）。

事前登録: `docs/jra_lap_feature_plan_2026_09_16.md`。**定義を変えるときは先にそちらを直す。**

## 形（`past_form.py` と同じ）

特徴の計算は **DB に触らない純関数**に閉じ込める。学習（一括）と配信（レース単位）で
別実装にすると train/serve skew が入るため、DB アクセスは呼び出し側の薄いアダプタに置く。

## 入力の事実（2026-09-16 実測）

- `races.lap_times` は 25区間×3バイト（0.1秒）で**スタートから**並ぶ。距離が 200 で
  割り切れないときは先頭区間が短い（1700m → 100m + 200m×8）。末尾は `000` 埋め
- `Σラップ` は1着馬の `finish_time` と一致し、末尾3区間の和は `last_3f_race` と一致する
- ラップは**先頭馬の区間**なので、各馬は「前半 = finish_time − last_3f」と
  「上がり3F = last_3f」の2区間にしか分けられない（`last_4f` は全行 NULL）

## 🔴 point-in-time

- 馬の特徴は対象レース日より**厳密に前**の走だけを使う（同日の先行レースも使わない）
- par（標準タイム）は、その過去走の日付より**厳密に前**のレースだけで作る
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "LAP_FORM_FEATURE_NAMES",
    "LAP_LOOKBACK",
    "PAR_MIN_COUNT",
    "LapRun",
    "LapRunStore",
    "ParTable",
    "RaceLapSummary",
    "compute_lap_form_features",
    "lap_form_feature_row",
    "parse_lap_times",
    "run_lap_metrics",
    "summarize_race_laps",
]

# LightGBM へ渡す列の正準の順序（学習も配信もこの順）
LAP_FORM_FEATURE_NAMES: list[str] = [
    "lap_front_par_l1",
    "lap_l3_par_l1",
    "lap_race_front_par_l1",
    "lap_race_shape_l1",
    "lap_front_par_m3",
    "lap_l3_par_m3",
    "lap_l3_par_best3",
]

LAP_LOOKBACK = 3          # 馬の特徴に使う有効走の数（新しい順）
PAR_MIN_COUNT = 10        # par を出すのに要るレース数
SEGMENT_M = 200           # ラップ1区間の長さ
LAST_SEGMENTS = 3         # 上がり3F = 末尾3区間
SHAPE_PRE_SEGMENTS = 2    # race_shape で比べる「上がりの手前」の区間数
_LAP_WIDTH = 3            # lap_times の1区間のバイト数

# 各馬の上がり3F として受け付ける範囲（秒）。範囲外はデータ異常として走ごと捨てる
_LAST3F_MIN, _LAST3F_MAX = 30.0, 50.0


# ---------------------------------------------------------------------------
# レースのラップ
# ---------------------------------------------------------------------------


def parse_lap_times(raw: str | None) -> list[float] | None:
    """`lap_times` 生文字列を秒のリストへ。末尾の `000` 埋めは落とす。

    数字以外が混じる・区間が1つも無いときは `None`。
    """
    if not raw:
        return None
    s = raw.strip()
    laps: list[float] = []
    for i in range(0, len(s) - len(s) % _LAP_WIDTH, _LAP_WIDTH):
        chunk = s[i:i + _LAP_WIDTH]
        if not chunk.isdigit():
            return None
        laps.append(int(chunk) / 10.0)
    while laps and laps[-1] == 0.0:
        laps.pop()
    if not laps or any(x <= 0.0 for x in laps):
        return None
    return laps


@dataclass(frozen=True, slots=True)
class RaceLapSummary:
    """1レースのラップの要約（すべて秒）。"""

    total: float    # Σラップ（1着馬の走破タイム）
    last3: float    # 末尾3区間の和
    front: float    # total − last3
    shape: float    # mean(末尾3区間) − mean(その前の2区間)。負 = 加速 / 正 = 失速


def summarize_race_laps(laps: Sequence[float] | None, distance: int | None) -> RaceLapSummary | None:
    """ラップを要約する。区間数が距離と合わない・短すぎるときは `None`。"""
    if not laps or not distance or distance <= 0:
        return None
    n_expected = math.ceil(distance / SEGMENT_M)
    if len(laps) != n_expected or len(laps) < LAST_SEGMENTS + SHAPE_PRE_SEGMENTS:
        return None
    total = float(sum(laps))
    last = laps[-LAST_SEGMENTS:]
    pre = laps[-(LAST_SEGMENTS + SHAPE_PRE_SEGMENTS):-LAST_SEGMENTS]
    last3 = float(sum(last))
    shape = float(sum(last) / len(last) - sum(pre) / len(pre))
    return RaceLapSummary(total=total, last3=last3, front=total - last3, shape=shape)


# ---------------------------------------------------------------------------
# 標準タイム（par）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ParSeries:
    dates: list[str]
    cum_front: list[float]   # 先頭に 0.0 を置いた累積和（len = len(dates) + 1）
    cum_last3: list[float]


class ParTable:
    """`(course, surface, distance[, condition])` ごとの par を日付で切り出す。

    🔴 `par(..., date)` は **`date` より厳密に前**のレースだけの平均を返す。
    """

    def __init__(self, races: Iterable[tuple[str, str, str | None, int, str | None, float, float]]):
        """races: `(date, course, surface, distance, condition, front, last3)` の列。"""
        buckets: dict[tuple, list[tuple[str, float, float]]] = {}
        for date, course, surface, distance, condition, front, last3 in races:
            key3 = (str(course), surface, int(distance))
            buckets.setdefault(key3, []).append((str(date), front, last3))
            if condition:
                buckets.setdefault(key3 + (condition,), []).append((str(date), front, last3))
        self._series: dict[tuple, _ParSeries] = {}
        for key, rows in buckets.items():
            rows.sort(key=lambda r: r[0])
            cf, cl = [0.0], [0.0]
            for _, f, l3 in rows:
                cf.append(cf[-1] + f)
                cl.append(cl[-1] + l3)
            self._series[key] = _ParSeries([r[0] for r in rows], cf, cl)

    def _mean_before(self, key: tuple, date: str) -> tuple[float, float] | None:
        s = self._series.get(key)
        if s is None:
            return None
        k = bisect.bisect_left(s.dates, date)
        if k < PAR_MIN_COUNT:
            return None
        return s.cum_front[k] / k, s.cum_last3[k] / k

    def par(self, course: str, surface: str | None, distance: int,
            condition: str | None, date: str) -> tuple[float, float] | None:
        """`(par_front, par_last3)`。馬場状態別 → 馬場状態なしの順に落とす。"""
        key3 = (str(course), surface, int(distance))
        if condition:
            got = self._mean_before(key3 + (condition,), str(date))
            if got is not None:
                return got
        return self._mean_before(key3, str(date))


# ---------------------------------------------------------------------------
# 過去走
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LapRun:
    """ラップ特徴に使える1走（JRA 平地・ラップ有効・完走）。"""

    date: str
    race_id: int
    course: str
    surface: str | None
    distance: int
    condition: str | None
    finish_time: float
    last_3f: float
    race: RaceLapSummary


def run_lap_metrics(run: LapRun, par_table: ParTable) -> dict[str, float] | None:
    """1走ぶんの指標。par が出ない・値が異常なら `None`（その走は使わない）。"""
    if not (_LAST3F_MIN <= run.last_3f <= _LAST3F_MAX):
        return None
    horse_front = run.finish_time - run.last_3f
    n_front = (run.distance - LAST_SEGMENTS * SEGMENT_M) / SEGMENT_M
    if horse_front <= 0.0 or n_front <= 0.0:
        return None
    par = par_table.par(run.course, run.surface, run.distance, run.condition, run.date)
    if par is None:
        return None
    par_front, par_last3 = par
    return {
        "front_par": (horse_front - par_front) / n_front,
        "l3_par": run.last_3f - par_last3,
        "race_front_par": (run.race.front - par_front) / n_front,
        "race_shape": run.race.shape,
    }


class LapRunStore:
    """馬ごとの有効走を `(date, race_id)` 昇順で持ち、日付より前だけを切り出す。"""

    def __init__(self, runs: Iterable[tuple[int, LapRun]]):
        by_horse: dict[int, list[LapRun]] = {}
        for horse_id, run in runs:
            by_horse.setdefault(int(horse_id), []).append(run)
        self._runs: dict[int, list[LapRun]] = {}
        self._dates: dict[int, list[str]] = {}
        for hid, rs in by_horse.items():
            rs.sort(key=lambda r: (r.date, r.race_id))
            self._runs[hid] = rs
            self._dates[hid] = [r.date for r in rs]

    def before(self, horse_id: int, date: str) -> list[LapRun]:
        """`date` より**厳密に前**の走を**新しい順**で返す。"""
        hid = int(horse_id)
        rs = self._runs.get(hid)
        if not rs:
            return []
        cut = bisect.bisect_left(self._dates[hid], str(date))
        return rs[:cut][::-1]


# ---------------------------------------------------------------------------
# 馬の特徴
# ---------------------------------------------------------------------------


def compute_lap_form_features(runs_new_first: Sequence[LapRun], par_table: ParTable,
                              target_date: str) -> dict[str, float | None]:
    """対象レース日より前の有効走から 7 特徴を作る。欠損は `None`。

    `runs_new_first` に対象日以降の走が混ざっていても捨てる（防御的に PIT を守る）。
    """
    metrics: list[dict[str, float]] = []
    for run in runs_new_first:
        if run.date >= str(target_date):
            continue
        m = run_lap_metrics(run, par_table)
        if m is None:
            continue
        metrics.append(m)
        if len(metrics) >= LAP_LOOKBACK:
            break

    out: dict[str, float | None] = dict.fromkeys(LAP_FORM_FEATURE_NAMES)
    if not metrics:
        return out
    last = metrics[0]
    out["lap_front_par_l1"] = last["front_par"]
    out["lap_l3_par_l1"] = last["l3_par"]
    out["lap_race_front_par_l1"] = last["race_front_par"]
    out["lap_race_shape_l1"] = last["race_shape"]
    out["lap_front_par_m3"] = sum(m["front_par"] for m in metrics) / len(metrics)
    out["lap_l3_par_m3"] = sum(m["l3_par"] for m in metrics) / len(metrics)
    out["lap_l3_par_best3"] = min(m["l3_par"] for m in metrics)
    return out


def lap_form_feature_row(feats: Mapping[str, float | None]) -> list[float]:
    """特徴 dict を `LAP_FORM_FEATURE_NAMES` 順の1行へ。`None` は NaN。"""
    return [
        float("nan") if feats.get(name) is None else float(feats[name])  # type: ignore[arg-type]
        for name in LAP_FORM_FEATURE_NAMES
    ]
