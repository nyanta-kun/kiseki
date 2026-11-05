"""着順分布の採点（予想フロー再設計 Wave 0c）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Wave 0c。
DB にも FastAPI にも依存しない純関数だけを置く。

## 考え方

モデルは「上位3着の着順の同時確率」を 1 レースにつき 1 枚の表 ``T3[a, b, c]``
（a が1着・b が2着・c が3着になる確率、n×n×n）として渡す。
単勝・連対・複勝・馬連・ワイド・三連複（と馬単・三連単）の確率は
**すべて T3 から厳密に導ける**ので、採点側はモデルの中身を知らなくてよい。

- Harville（= Plackett-Luce = 能力が同じ幅の Gumbel）: :func:`harville_top3_tensor` で厳密に作る
- シミュレーション: :func:`top3_tensor_from_orders` で着順サンプルを数えて作る

🔴 **シミュレーションは採点に使わず、自己検査に回す。**
三連複の実現組の確率は 0.1% 前後で、10万回のシミュレーションでは当たりが約100回しか
出ない。相対誤差は約10%＝1レースあたり約0.1 nat で、検出したい差（0.005 nat）の約20倍になる。
採点は厳密な T3 で行い、シミュレーションは「厳密値へ収束するか」の確認にだけ使う
（計画 §3 Wave 0c の合格基準・2026-11-06 改訂）。

## 指標（いずれも低いほど良い・proper scoring rule）

| 指標 | 種類 | 対象 |
|---|---|---|
| ``win_ll`` | 多項 | 1着馬の確率 |
| ``exacta_ll`` | 多項 | 1・2着の順序つき組（馬単） |
| ``quinella_ll`` | 多項 | 1・2着の組（馬連） |
| ``trio_ll`` | 多項 | 1〜3着の組（三連複） |
| ``trifecta_ll`` | 多項 | 1〜3着の順序つき組（三連単） |
| ``top2_ll`` | 二値・馬ごとの平均 | 2着以内か（連対） |
| ``place_ll`` | 二値・馬ごとの平均 | ``place_slots`` 着以内か（複勝） |
| ``wide_ll`` | 二値・組ごとの平均 | 2頭とも3着以内か（ワイド） |

``place_ll`` の定義は `scripts/jra_place_head_ab._place_ll_per_race` と同一
（ラベル ``finish_position <= place_slots``・``EPS`` でクリップ・レース内の馬の平均）。

## 母集団の規則（測る前に決めてある）

- 渡された行がそのレースのフィールド全体。取消・除外（``abnormality_code`` 1, 2）を
  除くのは呼び出し側の責任（`jra_place_residual_diag` と同じ）。
- 着順が欠けている馬（中止・失格で NULL / 0）はフィールドに残る。確率の分母に入り、
  どの「着以内」ラベルも 0 になる。
- 1〜3着の組を使う指標（馬連・馬単・三連複・三連単・ワイド・連対）は、
  **1着・2着・3着がそれぞれちょうど1頭いるレースだけ**で採点する。
  同着や3頭未満の完走は ``status`` に理由を残し、``NaN`` にする（黙って落とさない）。
- 単勝は「1着がちょうど1頭」、複勝は ``place_slots > 0`` が条件。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

EPS = 1e-9

# 採点できなかった理由（``status``）
STATUS_OK = "ok"
STATUS_DEAD_HEAT = "dead_heat"  # 1〜3着のどこかに同着がある
STATUS_SHORT_FIELD = "short_finishers"  # 1〜3着のどこかが居ない
STATUS_NO_WINNER = "no_single_winner"  # 1着がちょうど1頭ではない


# ---------------------------------------------------------------------------
# T3（上位3着の着順の同時確率）を作る
# ---------------------------------------------------------------------------


def harville_top3_tensor(p_win: Sequence[float] | np.ndarray) -> np.ndarray:
    """Harville で ``T3[a, b, c] = P(a 1着, b 2着, c 3着)`` を厳密に作る。

    `betting/finish_order._harville_joint` と同じく「残った馬の確率の合計」で割る形
    （単勝確率の合計が 1 からずれていても同じ値になる）。

    Args:
        p_win: 馬ごとの単勝確率（長さ n・非負）。

    Returns:
        n×n×n の配列。対角（同じ馬が2回出る組）は 0。
    """
    p = np.clip(np.asarray(p_win, dtype=float), 0.0, None)
    n = p.size
    if n < 3:
        raise ValueError(f"3頭未満のフィールドでは T3 を作れない（n={n}）")
    total = p.sum()
    if total <= 0.0:
        raise ValueError("単勝確率の合計が 0")
    # 1着 a
    p1 = p / total
    # 2着 b | 1着 a
    rem1 = total - p  # (a,)
    p2 = np.where(rem1[:, None] > 0, p[None, :] / np.maximum(rem1[:, None], 1e-300), 0.0)
    np.fill_diagonal(p2, 0.0)
    # 3着 c | 1着 a, 2着 b
    rem2 = total - p[:, None] - p[None, :]  # (a, b)
    p3 = np.where(rem2[:, :, None] > 0, p[None, None, :] / np.maximum(rem2[:, :, None], 1e-300), 0.0)
    t3 = p1[:, None, None] * p2[:, :, None] * p3
    idx = np.arange(n)
    t3[idx, idx, :] = 0.0
    t3[idx, :, idx] = 0.0
    t3[:, idx, idx] = 0.0
    return t3


def sample_plackett_luce_top3(p_win: Sequence[float] | np.ndarray, n_sims: int, rng: np.random.Generator) -> np.ndarray:
    """Harville と同じ分布から上位3着の着順を ``n_sims`` 回引く（自己検査用）。

    Plackett-Luce は「log p_i に標準 Gumbel を足した値の降順」と同じ分布になる。

    Returns:
        ``(n_sims, 3)`` の整数配列（各行が 1着・2着・3着の馬の添字）。
    """
    p = np.clip(np.asarray(p_win, dtype=float), 1e-300, None)
    g = rng.gumbel(size=(n_sims, p.size)) + np.log(p)[None, :]
    top = np.argpartition(-g, 2, axis=1)[:, :3]
    order = np.take_along_axis(g, top, axis=1).argsort(axis=1)[:, ::-1]
    return np.take_along_axis(top, order, axis=1)


def top3_tensor_from_orders(orders: np.ndarray, n: int) -> np.ndarray:
    """着順サンプル ``(S, 3)`` を数えて経験的な T3 を作る。"""
    orders = np.asarray(orders, dtype=np.int64)
    flat = (orders[:, 0] * n + orders[:, 1]) * n + orders[:, 2]
    counts = np.bincount(flat, minlength=n * n * n).astype(float)
    return (counts / max(len(orders), 1)).reshape(n, n, n)


# ---------------------------------------------------------------------------
# T3 から各券種の確率を導く
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinishMarginals:
    """T3 から導いた券種ごとの確率（いずれも馬の添字で引く）。"""

    win: np.ndarray  # (n,)   1着
    top2: np.ndarray  # (n,)   2着以内
    top3: np.ndarray  # (n,)   3着以内
    exacta: np.ndarray  # (n,n)  [a,b] = a 1着・b 2着
    quinella: np.ndarray  # (n,n)  対称。{a,b} が1・2着
    wide: np.ndarray  # (n,n)  対称。a と b がともに3着以内
    trio: np.ndarray  # (n,n,n) 対称。{a,b,c} が1〜3着
    t3: np.ndarray  # (n,n,n) 三連単


def marginals_from_t3(t3: np.ndarray) -> FinishMarginals:
    """T3 から全券種の確率を導く。"""
    exacta = t3.sum(axis=2)
    win = exacta.sum(axis=1)
    top2 = win + exacta.sum(axis=0)
    top3 = top2 + t3.sum(axis=(0, 1))
    quinella = exacta + exacta.T
    trio = (
        t3
        + t3.transpose(0, 2, 1)
        + t3.transpose(1, 0, 2)
        + t3.transpose(1, 2, 0)
        + t3.transpose(2, 0, 1)
        + t3.transpose(2, 1, 0)
    )
    wide = trio.sum(axis=2)
    return FinishMarginals(win=win, top2=top2, top3=top3, exacta=exacta, quinella=quinella, wide=wide, trio=trio, t3=t3)


# ---------------------------------------------------------------------------
# 1 レースの採点
# ---------------------------------------------------------------------------


def _single(fp: np.ndarray, pos: int) -> int | None:
    hit = np.flatnonzero(fp == pos)
    return int(hit[0]) if hit.size == 1 else None


def realized_top3(finish_positions: Sequence[float] | np.ndarray) -> tuple[str, tuple[int, int, int] | None]:
    """1〜3着の馬の添字を返す。採点できないときは理由を返す。"""
    fp = np.asarray(finish_positions, dtype=float)
    counts = [int(np.sum(fp == k)) for k in (1, 2, 3)]
    if any(c > 1 for c in counts):
        return STATUS_DEAD_HEAT, None
    if any(c == 0 for c in counts):
        return STATUS_SHORT_FIELD, None
    a, b, c = (_single(fp, k) for k in (1, 2, 3))
    assert a is not None and b is not None and c is not None
    return STATUS_OK, (a, b, c)


def _binary_ll(p: np.ndarray, y: np.ndarray) -> float:
    q = np.clip(p, EPS, 1.0 - EPS)
    return float(np.mean(-(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))))


def _neg_log(p: float) -> float:
    return float(-np.log(max(p, EPS)))


def score_race(
    t3: np.ndarray,
    finish_positions: Sequence[float] | np.ndarray,
    place_slots: int,
    place_probs: Sequence[float] | np.ndarray | None = None,
) -> dict:
    """1 レースを採点する。

    Args:
        t3: そのレースの T3（n×n×n）。
        finish_positions: 馬ごとの確定着順（NaN / 0 は着外扱い）。
        place_slots: 複勝の払戻対象着順の数（3 / 2 / 0）。
        place_probs: 複勝確率を別のヘッドから渡すとき（独立複勝ヘッド）。
            省略すると T3 から作る（``place_slots`` 着以内の確率）。

    Returns:
        指標ごとの対数損失と ``status``。採点できない指標は ``NaN``。
    """
    fp = np.nan_to_num(np.asarray(finish_positions, dtype=float), nan=0.0)
    n = fp.size
    if t3.shape != (n, n, n):
        raise ValueError(f"T3 の形 {t3.shape} が出走頭数 {n} と合わない")
    m = marginals_from_t3(t3)
    nan = float("nan")
    out: dict = {"n": n, "status": STATUS_OK}

    winner = _single(fp, 1)
    out["win_ll"] = _neg_log(m.win[winner]) if winner is not None else nan

    if place_slots > 0:
        y = ((fp >= 1) & (fp <= place_slots)).astype(float)
        if place_probs is not None:
            pp = np.asarray(place_probs, dtype=float)
        else:
            pp = m.top3 if place_slots >= 3 else m.top2
        out["place_ll"] = _binary_ll(pp, y)
    else:
        out["place_ll"] = nan

    status, top = realized_top3(fp)
    if status != STATUS_OK or top is None:
        out["status"] = status if winner is not None else STATUS_NO_WINNER
        for k in ("exacta_ll", "quinella_ll", "trio_ll", "trifecta_ll", "top2_ll", "wide_ll"):
            out[k] = nan
        return out

    a, b, c = top
    out["exacta_ll"] = _neg_log(m.exacta[a, b])
    out["quinella_ll"] = _neg_log(m.quinella[a, b])
    out["trio_ll"] = _neg_log(m.trio[a, b, c])
    out["trifecta_ll"] = _neg_log(t3[a, b, c])
    out["top2_ll"] = _binary_ll(m.top2, ((fp >= 1) & (fp <= 2)).astype(float))

    iu = np.triu_indices(n, k=1)
    in3 = (fp >= 1) & (fp <= 3)
    y_pair = (in3[:, None] & in3[None, :])[iu].astype(float)
    out["wide_ll"] = _binary_ll(m.wide[iu], y_pair)
    return out


METRICS = ("win_ll", "place_ll", "top2_ll", "exacta_ll", "quinella_ll", "wide_ll", "trio_ll", "trifecta_ll")


# ---------------------------------------------------------------------------
# 対応差の信頼区間（レース単位のクラスタ bootstrap）
# ---------------------------------------------------------------------------


def paired_bootstrap_ci(
    delta_per_race: Sequence[float] | np.ndarray, n_boot: int = 2000, seed: int = 20261106, alpha: float = 0.05
) -> dict:
    """レースごとの差（腕 − 基準）の平均と、レースを単位に引き直した信頼区間。

    1 レース 1 値なので、レースを復元抽出することがそのままクラスタ bootstrap になる。
    ``NaN``（採点できなかったレース）は除いてから引く。
    """
    d = np.asarray(delta_per_race, dtype=float)
    d = d[~np.isnan(d)]
    if d.size == 0:
        return {"n_races": 0, "mean": None, "ci": None, "alpha": alpha}
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        boots[i] = d[rng.integers(0, d.size, d.size)].mean()
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "n_races": int(d.size),
        "mean": float(d.mean()),
        "ci": [float(lo), float(hi)],
        "alpha": alpha,
    }
