"""地方の着順の同時分布（能力分布モデル R1）を作る（予想フロー再設計・地方 Phase 2）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §5.6（事前登録）。

🔴 **中央の `services/jra_finish_dist.py` を流用してはいけない。** 中央は複勝ヘッドが
「払戻対象着順以内」（5〜7頭立ては2着以内）なので k = place_slots で当てはめる。
地方のヘッドは**頭数に関係なく「3着以内」**（`chihou_prob_normalize` も Σ=3 に揃える）なので、
ここでは **k = min(3, 頭数 − 1)** で当てはめる。中央の入口に通すと、5〜7頭立てを全部
「2着以内」の目標で当てはめることになり、静かに間違う。

## 入力の約束

- 勝率・3着内率は保存値（生の値）をそのまま渡してよい。中で勝率 Σ=1、3着内率 Σ=k に
  **比例で揃える**（`chihou_prob_normalize` の「揃えて 1 で止める」とは違い、止めない。
  当てはめは目標の合計がちょうど k であることを前提にするため。1 を超えた目標は
  `fit_random_utility` が内部で丸め、`n_clipped` に数える）
- 取消・除外の馬は呼び出し側で除いてから渡す（発走前は分からないので全馬になる）
- 4頭未満は Harville。当てはめが ok でなければ Harville（B0）

DB にも FastAPI にも依存しない純関数。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

import numpy as np

from ..indices.finish_dist import FIT_OK, METHOD_VERSION, fit_random_utility, top3_tensor
from ..indices.finish_dist_scoring import harville_top3_tensor, marginals_from_t3

PROB_FLOOR = 1e-6
TOP_K = 3  # 地方のヘッドは「3着以内」
SOURCE_R1 = "r1"
SOURCE_HARVILLE = "harville"


@dataclass(frozen=True)
class ChihouFinishDist:
    """1 レースの着順分布。配列はすべて `horse_numbers` の並び。"""

    horse_numbers: tuple[int, ...]
    k: int  # 当てはめに使った上位 k 着（0 は当てはめなし）
    source: str
    fit_status: str
    n_clipped: int  # 1 を超えて丸めた目標の数
    method_version: str
    win: np.ndarray
    top2: np.ndarray
    top3: np.ndarray
    t3: np.ndarray
    sigma: np.ndarray | None


def fit_k(n: int) -> int:
    """当てはめに使う k。4頭以上なら min(3, n−1)、それ未満は 0（Harville）。"""
    if n < 4:
        return 0
    return min(TOP_K, n - 1)


def _normalized(values: np.ndarray, total: float) -> np.ndarray:
    v = np.clip(np.nan_to_num(values.astype(float), nan=0.0), PROB_FLOOR, None)
    return v * (total / v.sum())


def build_race_finish_dist(
    horse_numbers: list[int] | tuple[int, ...],
    win_probs: list[float] | np.ndarray,
    top3_probs: list[float] | np.ndarray,
) -> ChihouFinishDist | None:
    """勝率と3着内率（地方 v14 の保存値）から上位3着の同時分布を作る。3頭未満は None。"""
    n = len(horse_numbers)
    if n < 3 or len(win_probs) != n or len(top3_probs) != n:
        return None
    k = fit_k(n)
    win = _normalized(np.asarray(win_probs, dtype=float), 1.0)
    t3: np.ndarray | None = None
    status = "harville"
    n_clipped = 0
    sigma: np.ndarray | None = None
    if k > 0:
        target = _normalized(np.asarray(top3_probs, dtype=float), float(k))
        fr = fit_random_utility(win, target, k)
        status, n_clipped = fr.status, fr.n_clipped
        if fr.status == FIT_OK:
            t3 = top3_tensor(fr.mu, fr.sigma)
            sigma = fr.sigma
    source = SOURCE_R1 if t3 is not None else SOURCE_HARVILLE
    if t3 is None:
        t3 = harville_top3_tensor(win)
    m = marginals_from_t3(t3)
    return ChihouFinishDist(
        horse_numbers=tuple(int(h) for h in horse_numbers),
        k=k,
        source=source,
        fit_status=status,
        n_clipped=n_clipped,
        method_version=METHOD_VERSION,
        win=m.win,
        top2=m.top2,
        top3=m.top3,
        t3=t3,
        sigma=sigma,
    )


def top_combinations(fd: ChihouFinishDist, kind: str, limit: int) -> list[tuple[tuple[int, ...], float]]:
    """確率の高い順に組合せを返す。kind = quinella（馬連）/ wide（ワイド）/ trio（三連複）。馬番は昇順。"""
    m = marginals_from_t3(fd.t3)
    hn = fd.horse_numbers
    n = len(hn)
    out: list[tuple[tuple[int, ...], float]] = []
    if kind in ("quinella", "wide"):
        mat = m.quinella if kind == "quinella" else m.wide
        out = [(tuple(sorted((hn[i], hn[j]))), float(mat[i, j])) for i in range(n) for j in range(i + 1, n)]
    elif kind == "trio":
        out = [
            (tuple(sorted((hn[i], hn[j], hn[c]))), float(m.trio[i, j, c]))
            for i in range(n)
            for j in range(i + 1, n)
            for c in range(j + 1, n)
        ]
    else:
        raise ValueError(f"未知の券種: {kind}")
    out.sort(key=lambda x: -x[1])
    return out[:limit]


# ---------------------------------------------------------------------------
# プロセス内キャッシュ（入力そのものをキーにするので古い値は返さない）
# ---------------------------------------------------------------------------

_CACHE_SIZE = 512
_cache: OrderedDict[tuple, ChihouFinishDist | None] = OrderedDict()
_cache_lock = Lock()


def cached_race_finish_dist(
    race_id: int,
    horse_numbers: list[int] | tuple[int, ...],
    win_probs: list[float],
    top3_probs: list[float],
) -> ChihouFinishDist | None:
    """`build_race_finish_dist` を (race_id, 入力) でキャッシュする（1 レース 0.1〜0.3 秒かかるため）。"""
    key = (
        race_id,
        METHOD_VERSION,
        tuple(int(h) for h in horse_numbers),
        tuple(round(float(x), 8) for x in win_probs),
        tuple(round(float(x), 8) for x in top3_probs),
    )
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    fd = build_race_finish_dist(horse_numbers, win_probs, top3_probs)
    with _cache_lock:
        _cache[key] = fd
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return fd
