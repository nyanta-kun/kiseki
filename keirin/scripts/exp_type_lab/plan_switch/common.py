import os, sys, pickle, importlib.util, random
from pathlib import Path
import numpy as np
K = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(K))
from src.type_lab import sell_plans_for, type_label_of
_s = importlib.util.spec_from_file_location("gate", K.parent / "backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
HERE = Path(__file__).parent
EXP_END = "2025-12-31"


def load():
    rows = pickle.load(open(os.environ.get("PLAN_SWITCH_MATRIX", "/tmp/plan_switch_matrix.pkl"), "rb"))  # build_matrix.py の出力
    for r in rows:
        r["win"] = "exp" if r["date"] <= EXP_END else "conf"
        r["s"] = 1 if r["arare"] >= 1 else (-1 if r["arare"] <= -1 else 0)
    return rows


def ok(r, k, axis_gate=True):
    """そのプランが組めて入稿ゲートと軸信頼ゲートを通るか。"""
    v = r["plans"].get(k)
    if not v or not v["gate"]:
        return False
    return G.passes_axis_gate(k, r["axis_sum"], 7) if axis_gate else True


def sell_key(r, firm=None):
    t = r["type"] if firm is None else type_label_of(r["axis_sum"] + (1.44 - firm), r["arare"])
    a = r["plans"].get("A_trio")
    trio_ok = bool(a and a["gate"]) if t == "A" else None
    s = sell_plans_for(t, 7, r["rtype"], pw_ent=r["pw_ent"], trio_ok=trio_ok)
    return s[0].key if s else None


def baseline(r):
    k = r["sell"]
    return k if k and ok(r, k) else None


def recs(rows, pol):
    out = []
    for r in rows:
        k = pol(r)
        if k:
            v = r["plans"][k]
            out.append((r["date"], k, v["inv"], v["pay"], r))
    return out


def summ(rs, ndays):
    if not rs:
        return dict(n=0)
    inv = np.array([x[2] for x in rs]); pay = np.array([x[3] for x in rs])
    hit = pay > inv
    ps = np.sort(pay)[::-1]
    def ex(k):
        return (pay.sum() - ps[:k].sum()) / inv.sum() * 100
    return dict(n=len(rs), perday=len(rs) / ndays, shown=hit.mean() * 100,
                roi=pay.sum() / inv.sum() * 100, roi3=ex(3), roi10=ex(10),
                med=float(np.median(pay[hit])) if hit.any() else 0,
                b4=(pay >= 40000).sum() / ndays, b10=(pay >= 100000).sum() / ndays)


def fmt(name, s):
    if not s.get("n"):
        return f"{name:<34} (なし)"
    return (f"{name:<34}{s['perday']:>6.1f}{s['shown']:>7.2f}{s['roi']:>7.1f}{s['roi3']:>7.1f}"
            f"{s['roi10']:>7.1f}{s['med']:>8,.0f}{s['b4']:>6.2f}{s['b10']:>6.3f}")


HEAD = f"{'腕':<34}{'件/日':>6}{'表示%':>7}{'ROI':>7}{'除3':>7}{'除10':>7}{'中央':>8}{'4万+':>6}{'10万+':>6}"


def daily(rs):
    d = {}
    for day, k, inv, pay, r in rs:
        a = d.setdefault(day, [0, 0, 0, 0])
        a[0] += inv; a[1] += pay; a[2] += int(pay > inv); a[3] += 1
    return d


def paired(rsA, rsB, days, B=2000, seed=0):
    """日ブートストラップ: (B − A) の 表示的中pt・ROIpt の 95%CI。"""
    da, db = daily(rsA), daily(rsB)
    days = sorted(days)
    M = np.array([[*da.get(x, [0, 0, 0, 0]), *db.get(x, [0, 0, 0, 0])] for x in days], float)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(B):
        s = M[rng.integers(0, len(days), len(days))].sum(0)
        out.append(((s[6] / s[7] - s[2] / s[3]) * 100, (s[5] / s[4] - s[1] / s[0]) * 100))
    o = np.array(out)
    return np.percentile(o[:, 0], [2.5, 97.5]), np.percentile(o[:, 1], [2.5, 97.5])
