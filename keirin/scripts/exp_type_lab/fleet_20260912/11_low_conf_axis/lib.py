"""集計・paired bootstrap・無作為対照の共通部品。"""
from __future__ import annotations
import pickle
from statistics import median
import numpy as np

ROWS = None
THR = None


def load():
    global ROWS, THR
    if ROWS is None:
        ROWS = pickle.loads(open("/tmp/11_rows.pkl", "rb").read())
        THR = pickle.loads(open("/tmp/11_thr.pkl", "rb").read())
    return ROWS, THR


def ndays(win: str) -> int:
    rows, _ = load()
    return len({r["date"] for r in rows if r["win"] == win})


def inlay(r, nm: str) -> bool:
    _, thr = load()
    if nm in ("(全体)", "all", None):
        return True
    if nm == "dis":
        return r["dis"]
    if nm.endswith("+dis"):
        return inlay(r, nm[:-4]) and r["dis"]
    q, side, t = thr[nm]
    return (r[q] <= t) if side == "lo" else (r[q] >= t)


def summ(recs: list[dict], nd: int) -> dict:
    if not recs:
        return dict(n=0)
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    ratios = sorted(r["pay"] / r["inv"] for r in hits)
    means = sorted(r["mean"] for r in recs if r.get("mean"))
    return dict(
        n=len(recs), perday=len(recs) / nd, k=sum(r["k"] for r in recs) / len(recs),
        hit=len(hits) / len(recs) * 100,
        gami=len(gami) / len(hits) * 100 if hits else 0.0,
        shown=(len(hits) - len(gami)) / len(recs) * 100,
        med_pay=median(pays) if pays else 0.0,
        med_mean=median(means) if means else 0.0,
        two=sum(1 for x in ratios if x >= 2) / nd,
        big=sum(1 for p in pays if p >= 100_000) / nd,
        big_rate=sum(1 for p in pays if p >= 100_000) / len(recs) * 100,
        b30=sum(1 for p in pays if p >= 300_000) / nd,
        b30_rate=sum(1 for p in pays if p >= 300_000) / len(recs) * 100,
        inv_day=inv / nd, roi=pay / inv * 100 if inv else 0.0)


HEAD = ("  {:20s} {:>6s} {:>5s} {:>6s} {:>6s} {:>8s} {:>9s} {:>9s} {:>6s} {:>7s} {:>7s} {:>6s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中", "払戻中央", "計画中央",
                "2倍+", "10万+率", "30万+率", "ROI"))


def line(nm: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {nm:20s}  (該当なし)"
    return (f"  {nm:20s} {s['perday']:6.2f} {s['k']:5.2f} {s['hit']:6.2f} {s['gami']:6.2f}"
            f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {s['med_mean']:9,.0f}"
            f" {s['two']:6.2f} {s['big_rate']:7.2f} {s['b30_rate']:7.2f} {s['roi']:6.1f}")


def paired(base: list[float], arm: list[float], seeds: int = 2000,
           agg=lambda v: float(np.mean(v))) -> tuple[float, float, float]:
    """レース単位 paired bootstrap の (点推定Δ, lo, hi)。base/arm は同じレース順。"""
    b = np.asarray(base, dtype=float)
    a = np.asarray(arm, dtype=float)
    d = agg(a) - agg(b)
    n = len(b)
    rng = np.random.default_rng(12345)
    ds = np.empty(seeds)
    for s in range(seeds):
        j = rng.integers(0, n, n)
        ds[s] = agg(a[j]) - agg(b[j])
    return d, float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))


def shown_vec(recs: list[dict]) -> list[float]:
    return [100.0 if (r["pay"] > 0 and r["pay"] >= r["inv"]) else 0.0 for r in recs]


def big_vec(recs: list[dict], thr: float = 100_000) -> list[float]:
    return [100.0 if r["pay"] >= thr else 0.0 for r in recs]


def roi_pair(base: list[dict], arm: list[dict], seeds: int = 2000):
    bi = np.array([r["inv"] for r in base]); bp = np.array([r["pay"] for r in base])
    ai = np.array([r["inv"] for r in arm]); ap = np.array([r["pay"] for r in arm])
    d = ap.sum() / ai.sum() * 100 - bp.sum() / bi.sum() * 100
    rng = np.random.default_rng(12345)
    n = len(bi); ds = np.empty(seeds)
    for s in range(seeds):
        j = rng.integers(0, n, n)
        ds[s] = ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100
    return d, float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))
