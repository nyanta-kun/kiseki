"""能力分布モデルによる着順の同時分布（予想フロー再設計 Phase 2）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Phase 2 / §5.4、
設計: `docs/prediction_flow_review_2026_11_06/architect.md` §3.3。

## モデル

各馬の走りを ``X_i = μ_i + σ_i · G_i``（G_i は独立な標準 Gumbel・大きいほど先着）とする。
σ_i を全馬 1 にそろえると Plackett-Luce ＝ Harville に一致する
（`finish_dist_scoring.harville_top3_tensor` と同じ分布）。
σ_i を馬ごとに動かすことが Harville からの唯一の拡張で、

- μ_i・σ_i はレースごとに、**勝率（Σ=1）と上位 k 着以内率（Σ=k）の両方に一致するよう**解く
  （2n 本の制約に対し未知数 2n、平行移動・尺度の不定性と Σ 制約が相殺する）。
  **学習するパラメータは無い。**
- 勝率は**厳密に**保つ（最後に σ を固定して μ だけを解き直す）。複勝側は当てはめの残差を返す。

## 計算はシミュレーションではなく数値積分

独立な X_i の順位の確率は、どれも1次元の積分で書ける（F_i = CDF, f_i = 密度）:

    P(i が1着)          = ∫ f_i(t) Π_{j≠i} F_j(t) dt
    P(i が上位 k 着以内) = ∫ f_i(t) P(他馬のうち t を超える頭数 ≤ k-1) dt
    P(a 1着, b 2着, c 3着) = ∫ f_c(t) Π_{r∉{a,b,c}} F_r(t) · H_ab(t) dt,
        H_ab(t) = P(X_a > X_b > t) = ∫_t^∞ f_b(u) (1 - F_a(u)) du

台形則で一様格子の上で計算する。三連複の実現組（確率 0.1% 前後）を 10 万回の
シミュレーションで数えると誤差が検出したい差より大きくなる（計画書 §3 Wave 0c）ので、
シミュレーション（`sample_top3`）は自己検査と「事前試行」の収束確認にだけ使う。

DB にも FastAPI にも依存しない純関数（numpy / scipy のみ）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

METHOD_VERSION = "ru1"

FIT_OK = "ok"  # 勝率は厳密・複勝の残差 ≤ PLACE_TOL
FIT_RESIDUAL = "residual"  # 勝率は厳密・複勝に残差が残った（目標が実現できない等）
FIT_HARVILLE = "harville"  # 当てはめを使わず σ=1（k=0 のフィールド等）

PLACE_TOL = 1e-4
WIN_TOL = 1e-9
LOG_SIGMA_BOUND = 3.0  # σ ∈ [e^-3, e^3]（幾何平均 1 に正規化した上で）
_LOGF_FLOOR = -800.0  # exp(-800) は倍精度で 0。これより小さい log F を丸めても値は変わらない
_Z_FLOOR = -690.0  # exp(690) が倍精度に収まる範囲
_EPS = 1e-12


# ---------------------------------------------------------------------------
# 格子と基本量
# ---------------------------------------------------------------------------


def _grid(
    mu: np.ndarray, sigma: np.ndarray, pts_per_sigma: int, max_pts: int, refine: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """全馬の密度が無視できる範囲を覆う一様格子と台形則の重み。

    区間数は ``m · refine``（m は pts_per_sigma から決まる）。refine=2 の格子は
    refine=1 の格子幅のちょうど半分になる（Richardson 外挿の前提）。
    """
    lo = float(np.min(mu - 5.0 * sigma))
    hi = float(np.max(mu + 34.0 * sigma))  # e^-34 ≈ 1.7e-15
    h = float(np.min(sigma)) / pts_per_sigma
    m = max(min(int(np.ceil((hi - lo) / h)), max_pts), 64)
    t = np.linspace(lo, hi, m * refine + 1)
    n = t.size
    w = np.full(n, t[1] - t[0])
    w[0] *= 0.5
    w[-1] *= 0.5
    return t, w


@dataclass(frozen=True)
class _Terms:
    t: np.ndarray  # (G,)
    w: np.ndarray  # (G,) 台形則の重み
    z: np.ndarray  # (n, G)
    log_f: np.ndarray  # (n, G) log 密度
    log_cdf: np.ndarray  # (n, G) log F（_LOGF_FLOOR で下を丸める）
    cdf: np.ndarray  # (n, G)
    sf: np.ndarray  # (n, G) 1 - F


def _terms(mu: np.ndarray, sigma: np.ndarray, pts_per_sigma: int, max_pts: int, refine: int = 1) -> _Terms:
    t, w = _grid(mu, sigma, pts_per_sigma, max_pts, refine)
    z = np.maximum((t[None, :] - mu[:, None]) / sigma[:, None], _Z_FLOOR)
    ez = np.exp(-z)
    log_cdf = np.maximum(-ez, _LOGF_FLOOR)
    log_f = -z + log_cdf - np.log(sigma)[:, None]
    cdf = np.exp(log_cdf)
    sf = -np.expm1(-ez)
    return _Terms(t=t, w=w, z=z, log_f=log_f, log_cdf=log_cdf, cdf=cdf, sf=sf)


def _count_excluding(cdf: np.ndarray, sf: np.ndarray, k: int) -> np.ndarray:
    """``P(i 以外で t を超える頭数 = c)`` を c = 0..k-1 について返す。形は (n, k, G)。

    前から・後ろから の部分積（ポアソン二項の DP）を作り、i を抜いた分布は両者の畳み込みで得る
    （割り算で i を取り除くと F が 0 に近い所で不安定になるため）。
    """
    n, g = cdf.shape
    pre = np.zeros((n + 1, k, g))
    pre[0, 0] = 1.0
    for j in range(n):
        pre[j + 1] = pre[j] * cdf[j]
        if k > 1:
            pre[j + 1, 1:] += pre[j, :-1] * sf[j]
    suf = np.zeros((n + 1, k, g))
    suf[n, 0] = 1.0
    for j in range(n - 1, -1, -1):
        suf[j] = suf[j + 1] * cdf[j]
        if k > 1:
            suf[j, 1:] += suf[j + 1, :-1] * sf[j]
    out = np.zeros((n, k, g))
    for c in range(k):
        for a in range(c + 1):
            out[:, c] += pre[:n, a] * suf[1:, c - a]
    return out


def win_topk_probs(
    mu: Sequence[float] | np.ndarray,
    sigma: Sequence[float] | np.ndarray,
    k: int,
    *,
    pts_per_sigma: int = 40,
    max_pts: int = 20000,
) -> tuple[np.ndarray, np.ndarray]:
    """(勝率, 上位 k 着以内率) を数値積分で返す。k=1 のとき2つは同じもの。"""
    mu = np.asarray(mu, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    if k < 1:
        raise ValueError(f"k は 1 以上（k={k}）")
    tm = _terms(mu, sigma, pts_per_sigma, max_pts)
    cnt = _count_excluding(tm.cdf, tm.sf, k)
    f = np.exp(tm.log_f)
    win = (f * cnt[:, 0] * tm.w).sum(axis=1)
    topk = (f * cnt.sum(axis=1) * tm.w).sum(axis=1)
    return win, topk


def top3_tensor(
    mu: Sequence[float] | np.ndarray,
    sigma: Sequence[float] | np.ndarray,
    *,
    pts_per_sigma: int = 40,
    max_pts: int = 20000,
) -> np.ndarray:
    """``T3[a, b, c] = P(a 1着, b 2着, c 3着)`` を返す（n×n×n・対角は 0）。

    台形則の誤差は格子幅の2乗で縮むので、格子幅 h と h/2 の2回の結果から
    Richardson 外挿 ``(4·T(h/2) − T(h)) / 3`` をとる（三連複の実現組の相対誤差を
    1e-6 程度まで下げるため。h だけだと 1e-4 残る）。
    """
    t_h = _top3_tensor_once(mu, sigma, pts_per_sigma=pts_per_sigma, max_pts=max_pts, refine=1)
    t_h2 = _top3_tensor_once(mu, sigma, pts_per_sigma=pts_per_sigma, max_pts=max_pts, refine=2)
    return np.clip((4.0 * t_h2 - t_h) / 3.0, 0.0, None)


def _top3_tensor_once(
    mu: Sequence[float] | np.ndarray,
    sigma: Sequence[float] | np.ndarray,
    *,
    pts_per_sigma: int,
    max_pts: int,
    refine: int,
) -> np.ndarray:
    """格子1本での T3。

    ``f_c Π_{r∉{a,b,c}} F_r = (e^{-z_c}/σ_c) Π_{r∉{a,b}} F_r`` を使い、
    ``M_ab(t) = Π_{r∉{a,b}} F_r(t) · H_ab(t)`` を一度作ってから c について行列積で積分する。
    """
    mu = np.asarray(mu, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    n = mu.size
    if n < 3:
        raise ValueError(f"3頭未満のフィールドでは T3 を作れない（n={n}）")
    tm = _terms(mu, sigma, pts_per_sigma, max_pts, refine)
    f = np.exp(tm.log_f)  # (n, G)
    # H_ab(t) = ∫_t^∞ f_b(u) sf_a(u) du（後ろからの累積台形則）
    sf_f = tm.sf[:, None, :] * f[None, :, :]  # (a, b, G)
    dt = tm.t[1] - tm.t[0]
    seg = 0.5 * (sf_f[:, :, 1:] + sf_f[:, :, :-1]) * dt
    h = np.zeros_like(sf_f)
    h[:, :, :-1] = np.cumsum(seg[:, :, ::-1], axis=2)[:, :, ::-1]
    s_all = tm.log_cdf.sum(axis=0)  # (G,)
    log_rest = s_all[None, None, :] - tm.log_cdf[:, None, :] - tm.log_cdf[None, :, :]
    idx = np.arange(n)
    log_rest[idx, idx, :] = -np.inf  # a=b は起きない（そのままだと log F_a を2回引いて溢れる）
    m = np.exp(log_rest) * h
    g = np.exp(-tm.z - np.log(sigma)[:, None])  # (c, G) = f_c / F_c
    t3 = np.einsum("abg,cg,g->abc", m, g, tm.w, optimize=True)
    t3[idx, :, idx] = 0.0
    t3[:, idx, idx] = 0.0
    return np.clip(t3, 0.0, None)


# ---------------------------------------------------------------------------
# 当てはめ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitResult:
    """`fit_random_utility` の結果。"""

    mu: np.ndarray
    sigma: np.ndarray
    k: int
    win_target: np.ndarray
    place_target: np.ndarray  # 実行可能な範囲へクリップした後の目標
    win_model: np.ndarray
    place_model: np.ndarray
    status: str
    n_iter: int
    n_clipped: int  # 目標をクリップした馬の数
    max_win_resid: float
    max_place_resid: float


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p) - np.log1p(-p)


def _normalize(mu: np.ndarray, log_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """平行移動・尺度の不定性を消す（σ の幾何平均 1・μ の最大 0）。"""
    c = log_s.mean()
    log_s = log_s - c
    mu = mu / np.exp(c)
    return mu - mu.max(), log_s


def _solve_mu(
    win: np.ndarray, mu: np.ndarray, sigma: np.ndarray, k: int, pts: int, max_pts: int, max_iter: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """σ を固定して勝率に厳密に合わせる。Gumbel の性質から Δμ = σ log(目標/現在) が良い近似。"""
    it = 0
    w_m, p_m = win_topk_probs(mu, sigma, k, pts_per_sigma=pts, max_pts=max_pts)
    for it in range(1, max_iter + 1):
        if np.max(np.abs(w_m - win)) <= WIN_TOL:
            break
        mu = mu + sigma * (np.log(win) - np.log(np.maximum(w_m, 1e-300)))
        mu = mu - mu.max()
        w_m, p_m = win_topk_probs(mu, sigma, k, pts_per_sigma=pts, max_pts=max_pts)
    return mu, w_m, p_m, it


def fit_random_utility(
    win: Sequence[float] | np.ndarray,
    place: Sequence[float] | np.ndarray,
    k: int,
    *,
    max_iter: int = 300,
    step: float = 0.5,
    pts_per_sigma: int = 40,
    max_pts: int = 20000,
) -> FitResult:
    """勝率（Σ=1）と上位 k 着以内率（Σ=k）に一致する μ・σ を解く。

    Args:
        win: 馬ごとの勝率。レース内で Σ=1 に正規化してから使う。
        place: 馬ごとの上位 k 着以内率（Σ=k を想定）。k=0 なら使わない。
        k: 複勝の払戻対象の着数（`composite.place_slots_for_field`）。0 なら σ=1（Harville）。

    目標は ``win_i ≤ place_i ≤ 1`` へクリップする（破れた馬の数を `n_clipped` に残す）。
    σ を動かす更新は「σ を大きくすると、勝率を保ったまま上位 k 着以内率が下がる」
    （一発か大敗かに振れる）性質を使った固定点反復。σ を動かすたびに μ を解き直して
    勝率を `WIN_TOL` まで合わせるので、勝率は常に厳密に保たれる。
    複勝の残差が増える歩は採らず、歩幅を半分にする。
    """
    w = np.clip(np.asarray(win, dtype=float), _EPS, None)
    w = w / w.sum()
    n = w.size
    mu = np.log(w)
    mu = mu - mu.max()
    log_s = np.zeros(n)
    if k <= 0:
        sigma = np.ones(n)
        mu, w_m, p_m, it = _solve_mu(w, mu, sigma, 1, pts_per_sigma, max_pts, 50)
        return FitResult(
            mu=mu,
            sigma=sigma,
            k=0,
            win_target=w,
            place_target=w.copy(),
            win_model=w_m,
            place_model=w_m,
            status=FIT_HARVILLE,
            n_iter=it,
            n_clipped=0,
            max_win_resid=float(np.max(np.abs(w_m - w))),
            max_place_resid=0.0,
        )

    p_raw = np.asarray(place, dtype=float)
    lo = w * (1.0 + 1e-6)
    hi = np.full(n, 1.0 - 1e-6)
    p = np.clip(p_raw, lo, hi)
    n_clipped = int(np.sum((p_raw < lo) | (p_raw > hi)))
    lp = _logit(p)

    mu, w_m, p_m, _ = _solve_mu(w, mu, np.exp(log_s), k, pts_per_sigma, max_pts, 100)
    resid = float(np.max(np.abs(p_m - p)))
    it = 0
    for it in range(1, max_iter + 1):
        if resid <= PLACE_TOL * 0.01 or step < 1e-3:
            break
        # σ を半歩動かし（σ↑ で上位 k 着以内率↓）、勝率に合わせて μ を解き直す
        cand = np.clip(log_s + step * (_logit(p_m) - lp), -LOG_SIGMA_BOUND, LOG_SIGMA_BOUND)
        c = cand.mean()
        cand = cand - c
        mu_c, w_c, p_c, _ = _solve_mu(w, mu / np.exp(c), np.exp(cand), k, pts_per_sigma, max_pts, 100)
        r_c = float(np.max(np.abs(p_c - p)))
        if r_c < resid:
            log_s, mu, w_m, p_m, resid = cand, mu_c, w_c, p_c, r_c
        else:
            step *= 0.5  # 悪化したら歩幅を半分にして同じ点からやり直す

    sigma = np.exp(log_s)
    max_w = float(np.max(np.abs(w_m - w)))
    status = FIT_OK if resid <= PLACE_TOL and max_w <= WIN_TOL else FIT_RESIDUAL
    return FitResult(
        mu=mu,
        sigma=sigma,
        k=k,
        win_target=w,
        place_target=p,
        win_model=w_m,
        place_model=p_m,
        status=status,
        n_iter=it,
        n_clipped=n_clipped,
        max_win_resid=max_w,
        max_place_resid=resid,
    )


# ---------------------------------------------------------------------------
# 比較腕 B1 と自己検査
# ---------------------------------------------------------------------------


def discounted_harville_top3_tensor(win: Sequence[float] | np.ndarray, gamma2: float, gamma3: float) -> np.ndarray:
    """2着・3着だけ勝率を γ 乗して割り引く Harville（Benter 型・比較腕 B1）。

    ``T3[a,b,c] = p_a · p_b^γ2 / Σ_{j≠a} p_j^γ2 · p_c^γ3 / Σ_{j≠a,b} p_j^γ3``。
    1着の周辺確率は勝率のまま変わらない。γ2 = γ3 = 1 で Harville に一致する。
    """
    p = np.clip(np.asarray(win, dtype=float), _EPS, None)
    p = p / p.sum()
    n = p.size
    if n < 3:
        raise ValueError(f"3頭未満のフィールドでは T3 を作れない（n={n}）")
    q2 = p**gamma2
    q3 = p**gamma3
    c2 = q2[None, :] / (q2.sum() - q2)[:, None]  # (a, b)
    rem3 = q3.sum() - q3[:, None] - q3[None, :]  # (a, b)
    c3 = q3[None, None, :] / np.maximum(rem3[:, :, None], 1e-300)
    t3 = p[:, None, None] * c2[:, :, None] * c3
    idx = np.arange(n)
    t3[idx, idx, :] = 0.0
    t3[idx, :, idx] = 0.0
    t3[:, idx, idx] = 0.0
    return t3


def sample_top3(
    mu: Sequence[float] | np.ndarray, sigma: Sequence[float] | np.ndarray, n_sims: int, rng: np.random.Generator
) -> np.ndarray:
    """``X = μ + σ·Gumbel`` を n_sims 回試行し、上位3頭の馬番号（0始まり）を返す（n_sims×3）。

    数値積分の自己検査用（「事前試行」の収束確認）。判定には使わない。
    """
    mu = np.asarray(mu, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    x = mu[None, :] + sigma[None, :] * rng.gumbel(size=(n_sims, mu.size))
    top = np.argpartition(-x, 3, axis=1)[:, :3]
    order = np.argsort(-np.take_along_axis(x, top, axis=1), axis=1)
    return np.take_along_axis(top, order, axis=1)
