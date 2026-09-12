"""市場（三連複の板）から「読み」の量を作る共通関数。"""
from __future__ import annotations
import itertools
import numpy as np

CANON = list(itertools.permutations(range(1, 8), 3))
CANON3 = list(itertools.combinations(range(1, 8), 3))
C3IDX = {frozenset(c): i for i, c in enumerate(CANON3)}
PERM2TRIO = np.array([C3IDX[frozenset(c)] for c in CANON])       # 210 -> 35
CAR_IN = np.array([[c in trio for trio in CANON3] for c in range(1, 8)], bool)  # (7,35)


def model_trio_prob(prob210: np.ndarray) -> np.ndarray:
    """三連単の確率(…,210) → 三連複(…,35)。"""
    out = np.zeros(prob210.shape[:-1] + (35,), np.float64)
    for t, j in enumerate(PERM2TRIO):
        out[..., j] += prob210[..., t]
    return out


def market_features(odds35: np.ndarray, p3: np.ndarray, pm35: np.ndarray,
                    trio_po35: np.ndarray, min_fill: int = 1) -> dict:
    """1レース分。odds35: 板(nan=未確定) / p3: モデル3着内率(7) / pm35: モデル三連複確率 /
    trio_po35: 予測三連複オッズ。戻り値は dict（充足点数が足りなければ全部 nan）。"""
    f = np.isfinite(odds35) & (odds35 > 0)
    nf = int(f.sum())
    order = np.argsort(-p3)
    a1, a2 = int(order[0]), int(order[1])          # 0-indexed 車番
    out = dict(n_fill=nf)
    nan = float("nan")
    keys = ("mk_axis", "mk_top2", "agree2", "mk_ent", "mk_pair", "md_pair", "resid_pair",
            "ratio_top", "log_fav", "kl", "mk_rank_a1", "mk_rank_a2", "pair_ratio", "mk_a1")
    if nf < min_fill:
        out.update({k: nan for k in keys}); return out
    inv = np.where(f, 1.0 / np.where(f, odds35, 1.0), 0.0)
    q = inv / inv.sum()
    m = CAR_IN @ q                                  # 車ごとの市場3着内率(和=3)
    mk_order = np.argsort(-m)
    pair_mask = CAR_IN[a1] & CAR_IN[a2]
    mk_pair = float(q[pair_mask].sum())
    pmn = pm35 / max(pm35.sum(), 1e-12)
    md_pair = float(pmn[pair_mask].sum())
    top = int(np.argmax(pm35))
    ratio_top = float(trio_po35[top] / odds35[top]) if f[top] and np.isfinite(trio_po35[top]) else nan
    # 軸ペアの 5点の 予測/板 の比（幾何平均・埋まっている点だけ）
    pr = []
    for j in np.flatnonzero(pair_mask):
        if f[j] and np.isfinite(trio_po35[j]) and trio_po35[j] > 0:
            pr.append(np.log(trio_po35[j] / odds35[j]))
    pair_ratio = float(np.exp(np.mean(pr))) if pr else nan
    with np.errstate(divide="ignore", invalid="ignore"):
        kl = float(np.nansum(np.where(q > 0, q * np.log(q / np.maximum(pmn, 1e-9)), 0.0)))
        ent = float(-np.nansum(np.where(q > 0, q * np.log(q), 0.0)) / np.log(35))
    out.update(mk_axis=float(m[a1] + m[a2]), mk_top2=float(m[mk_order[0]] + m[mk_order[1]]),
               agree2=float({a1, a2} == {int(mk_order[0]), int(mk_order[1])}),
               mk_ent=ent, mk_pair=mk_pair, md_pair=md_pair, resid_pair=md_pair - mk_pair,
               ratio_top=ratio_top, log_fav=float(np.log(odds35[f].min())), kl=kl,
               mk_rank_a1=float(np.flatnonzero(mk_order == a1)[0] + 1),
               mk_rank_a2=float(np.flatnonzero(mk_order == a2)[0] + 1),
               pair_ratio=pair_ratio, mk_a1=float(m[a1]))
    return out


def win_entropy(pw: np.ndarray) -> float:
    v = pw[np.isfinite(pw) & (pw > 0)]
    if v.sum() <= 0:
        return 0.0
    v = v / v.sum()
    return float(-(v * np.log(v)).sum())
