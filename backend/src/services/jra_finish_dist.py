"""中央の着順の同時分布（能力分布モデル R1）を配信用にまとめる（予想フロー再設計 Phase 3）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Phase 3・§5.5。

🔴 **配信（`/api/races/{id}/indices` ・ `/finish-dist`）と前向き記録の採点
（`scripts/jra_finish_dist_forward.py`）は、必ずこのモジュールの `build_race_finish_dist`
を通す。** R1 は (勝率, 複勝率, 払戻対象着順) だけで決まり学習パラメータを持たないので、
発走前スナップショット（`keiba.hit_tier_picks`）の保存値から同じ分布を作り直せる。
入口を2つ書いた瞬間に train/serve skew が入る（地方 v13→v14 で 9pt 落とした型）。

## 入力の約束

- フィールドは `calculated_indices` の**そのレースの全馬**（v28 が複勝率を Σ=place_slots に
  正規化したのと同じ母集団）。取消馬もここでは外さない。外すと place_slots と Σ が
  v28 の正規化とずれる（`composite.place_slots_for_field` の docstring）
- 保存値は `Numeric(6,4)` で丸められているので、読み出し側で勝率は Σ=1、
  複勝率は Σ=place_slots に正規化し直してから当てはめる
- place_slots=0（5頭未満）・当てはめが ok でないレースは Harville（B0）へ落とす

DB にも FastAPI にも依存しない純関数。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

import numpy as np

from ..indices.composite import place_slots_for_field
from ..indices.finish_dist import FIT_OK, METHOD_VERSION, fit_random_utility, top3_tensor
from ..indices.finish_dist_scoring import harville_top3_tensor, marginals_from_t3

PROB_FLOOR = 1e-6  # 保存値 0.0000（丸めで 0 になった大穴）を当てはめに渡すための下限
SOURCE_R1 = "r1"
SOURCE_HARVILLE = "harville"


@dataclass(frozen=True)
class RaceFinishDist:
    """1 レースの着順分布。配列はすべて `horse_numbers` の並び。"""

    horse_numbers: tuple[int, ...]
    place_slots: int
    source: str  # "r1" / "harville"（B0 へ落とした）
    fit_status: str  # finish_dist の status（k=0 のときは "harville"）
    method_version: str
    win: np.ndarray
    top2: np.ndarray
    top3: np.ndarray
    t3: np.ndarray  # T3[a, b, c] = P(a 1着, b 2着, c 3着)


def _normalized(values: np.ndarray, total: float) -> np.ndarray:
    v = np.clip(np.nan_to_num(values.astype(float), nan=0.0), PROB_FLOOR, None)
    return v * (total / v.sum())


def build_race_finish_dist(
    horse_numbers: list[int] | tuple[int, ...],
    win_probs: list[float] | np.ndarray,
    place_probs: list[float] | np.ndarray,
) -> RaceFinishDist | None:
    """勝率と複勝率（v28 の保存値）から、上位3着の同時分布を作る。

    Args:
        horse_numbers: フィールド全馬の馬番。
        win_probs: 勝率（`calculated_indices.win_probability`）。
        place_probs: 複勝率（同 `place_probability`・払戻対象着順以内の確率）。

    Returns:
        3頭未満なら None。
    """
    n = len(horse_numbers)
    if n < 3 or len(win_probs) != n or len(place_probs) != n:
        return None
    k = place_slots_for_field(n)
    win = _normalized(np.asarray(win_probs, dtype=float), 1.0)
    t3: np.ndarray | None = None
    status = "harville"
    if k > 0:
        place = _normalized(np.asarray(place_probs, dtype=float), float(k))
        fr = fit_random_utility(win, place, k)
        status = fr.status
        if fr.status == FIT_OK:
            t3 = top3_tensor(fr.mu, fr.sigma)
    source = SOURCE_R1 if t3 is not None else SOURCE_HARVILLE
    if t3 is None:
        t3 = harville_top3_tensor(win)
    m = marginals_from_t3(t3)
    return RaceFinishDist(
        horse_numbers=tuple(int(h) for h in horse_numbers),
        place_slots=k,
        source=source,
        fit_status=status,
        method_version=METHOD_VERSION,
        win=m.win,
        top2=m.top2,
        top3=m.top3,
        t3=t3,
    )


def top_combinations(fd: RaceFinishDist, kind: str, limit: int) -> list[tuple[tuple[int, ...], float]]:
    """確率の高い順に組合せを返す。kind = quinella（馬連）/ wide（ワイド）/ trio（三連複）。

    馬番は昇順に並べる（券面と同じ）。
    """
    m = marginals_from_t3(fd.t3)
    hn = fd.horse_numbers
    n = len(hn)
    out: list[tuple[tuple[int, ...], float]] = []
    if kind in ("quinella", "wide"):
        mat = m.quinella if kind == "quinella" else m.wide
        for i in range(n):
            for j in range(i + 1, n):
                out.append((tuple(sorted((hn[i], hn[j]))), float(mat[i, j])))
    elif kind == "trio":
        for i in range(n):
            for j in range(i + 1, n):
                for c in range(j + 1, n):
                    out.append((tuple(sorted((hn[i], hn[j], hn[c]))), float(m.trio[i, j, c])))
    else:
        raise ValueError(f"未知の券種: {kind}")
    out.sort(key=lambda x: -x[1])
    return out[:limit]


# ---------------------------------------------------------------------------
# プロセス内キャッシュ（入力そのものをキーにするので古い値は返さない）
# ---------------------------------------------------------------------------

_CACHE_SIZE = 256
_cache: OrderedDict[tuple, RaceFinishDist | None] = OrderedDict()
_cache_lock = Lock()


def cached_race_finish_dist(
    race_id: int,
    horse_numbers: list[int] | tuple[int, ...],
    win_probs: list[float],
    place_probs: list[float],
) -> RaceFinishDist | None:
    """`build_race_finish_dist` の結果を (race_id, 入力) でキャッシュする。

    1 レース 0.1〜0.3 秒かかるので、同じ入力で何度も当てはめない。
    入力（勝率・複勝率）が変われば別のキーになるので、再算出後に古い分布は返らない。
    """
    key = (
        race_id,
        METHOD_VERSION,
        tuple(int(h) for h in horse_numbers),
        tuple(round(float(x), 6) for x in win_probs),
        tuple(round(float(x), 6) for x in place_probs),
    )
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    fd = build_race_finish_dist(horse_numbers, win_probs, place_probs)
    with _cache_lock:
        _cache[key] = fd
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return fd
