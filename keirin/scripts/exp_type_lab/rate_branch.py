#!/usr/bin/env python3
"""二軸そろい×外れ を ライン／連対率／3着内率 で条件分岐できるか（2026-09-13）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/rate_branch.py <section>

section: anatomy | partner | resid | cond
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

ROWS = None


def load():
    global ROWS
    if ROWS is None:
        with open("/tmp/ratebranch/rows.pkl", "rb") as f:
            ROWS = pickle.load(f)
    return ROWS


def wins(rows):
    return {w: [r for r in rows if r["win"] == w] for w in ("explore", "confirm")}


# ───────────────────── §1 台の妥当性（miss_anatomy の再現） ─────────────────────

def anatomy():
    W = wins(load())
    print("§1 外れの排他分解（miss_anatomy_2026_09_10 §1 と同じ定義）\n")
    print(f"{'':22s}{'確認 2026':>12s}{'探索':>10s}")
    lab = ["① 的中", "② 順序違い(集合は買えた)", "③ 相手外し(軸2車そろい)", "④ 軸崩壊"]
    acc = {}
    for w in ("confirm", "explore"):
        rs = W[w]
        n = len(rs)
        a = [sum(1 for r in rs if r["hit"]),
             sum(1 for r in rs if not r["hit"] and r["set_hit"]),
             sum(1 for r in rs if not r["hit"] and not r["set_hit"] and r["both_in3"]),
             sum(1 for r in rs if not r["hit"] and not r["set_hit"] and not r["both_in3"])]
        acc[w] = [x / n * 100 for x in a]
        acc[w + "_n"] = n
    for j, l in enumerate(lab):
        print(f"{l:22s}{acc['confirm'][j]:11.2f}%{acc['explore'][j]:9.2f}%")
    print(f"{'件数':22s}{acc['confirm_n']:12,d}{acc['explore_n']:10,d}")
    for w in ("confirm", "explore"):
        rs = W[w]
        both = [r for r in rs if r["both_in3"]]
        print(f"\n{w}: 軸2車そろい {len(both)/len(rs)*100:.1f}%  "
              f"うち集合まで買えた {sum(1 for r in both if r['set_hit'])/len(both)*100:.1f}%")


# ───────────────────── §2 3着目の相手はどこから来るか ─────────────────────

SEL = {
    "モデル p3（現行の軸順）": lambda r: r["p3vec"],
    "モデル pw（1着確率）": lambda r: r["pwvec"],
    "3着内率（選手成績）": lambda r: r["rate"][:, 2],
    "連対率（選手成績）": lambda r: r["rate"][:, 1],
    "1着率（選手成績）": lambda r: r["rate"][:, 0],
    "競走得点": lambda r: r["rp"],
}


def _mkt_score(r):
    """市場: その車を含む三連単の予測オッズの最小値の逆数（安いほど上位）。"""
    s = np.zeros(7)
    for c, o in r["po_tf"].items():
        if o > 0:
            for car in c:
                s[car - 1] = max(s[car - 1], 1.0 / o)
    return s


def _line_score(r):
    """軸2車と同ラインなら 1、そうでなければ 0（同点は p3 で割る）。"""
    lg = r["lg"]
    tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
    return np.array([(1.0 if lg[c] in tgt else 0.0) + 1e-6 * r["p3vec"][c]
                     for c in range(7)])


def partner():
    W = wins(load())
    sels = dict(SEL)
    sels["市場（予測オッズ）"] = _mkt_score
    sels["同ライン優先"] = _line_score
    print("§2 軸2車がそろった回の「3着目の車」を、相手上位 m 車で覆える率\n")
    print(f"{'選び方':26s}" + "".join(f"{f'm={m}':>9s}" for m in (1, 2, 3, 4, 5)) + "   窓")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        for name, fn in sels.items():
            cov = []
            for m in (1, 2, 3, 4, 5):
                ok = 0
                for r in both:
                    s = fn(r)
                    cand = [c for c in np.argsort(-s) + 1 if c not in (r["a1"], r["a2"])]
                    ok += r["third_car"] in cand[:m]
                cov.append(ok / len(both) * 100)
            print(f"{name:26s}" + "".join(f"{v:8.2f}%" for v in cov) +
                  f"   {w} n={len(both):,}")
        print()


# ───────────────────── §3 残差テスト ─────────────────────

def resid():
    """モデル p3 の相手順位で層別し、率が層の中で3着目を分離するか。"""
    W = wins(load())
    print("§3 モデル p3 の相手順位で層別したときの、率の追加情報\n")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        print(f"--- {w} (n={len(both):,}) ---")
        print(f"{'p3相手順位':>10s}{'n':>7s}{'実3着率':>9s}"
              f"{'3着内率上位半':>14s}{'連対率上位半':>13s}{'同ライン':>10s}")
        # 各レースの「相手候補5車」を p3 順位 1..5 に割り当て、その車が3着目だったか
        buckets = defaultdict(lambda: [0, 0, [0, 0], [0, 0], [0, 0]])
        for r in both:
            cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
            t3 = r["rate"][:, 2]
            t2 = r["rate"][:, 1]
            med3 = np.median([t3[c - 1] for c in cand])
            med2 = np.median([t2[c - 1] for c in cand])
            lg = r["lg"]
            tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
            for j, c in enumerate(cand[:5], 1):
                b = buckets[j]
                won = c == r["third_car"]
                b[0] += 1
                b[1] += won
                b[2][0] += (t3[c - 1] >= med3)
                b[2][1] += won and (t3[c - 1] >= med3)
                b[3][0] += (t2[c - 1] >= med2)
                b[3][1] += won and (t2[c - 1] >= med2)
                b[4][0] += (lg[c - 1] in tgt)
                b[4][1] += won and (lg[c - 1] in tgt)
        for j in sorted(buckets):
            n, won, a3, a2, al = buckets[j]
            f = lambda p: (p[1] / p[0] * 100) if p[0] else 0.0
            print(f"{j:>10d}{n:7d}{won/n*100:8.2f}%{f(a3):13.2f}%{f(a2):12.2f}%{f(al):9.2f}%")
        print()




# ───────────────────── §4 条件分岐（どの条件で選び方が変わるか） ─────────────────────

def _cands(r):
    return [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]


def _top1(fn, rows):
    ok = 0
    for r in rows:
        s = fn(r)
        cand = [c for c in np.argsort(-s) + 1 if c not in (r["a1"], r["a2"])]
        ok += r["third_car"] == cand[0]
    return ok / len(rows) * 100 if rows else 0.0


def _conds(r):
    lg = r["lg"]
    tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
    same = lg[r["a1"] - 1] == lg[r["a2"] - 1]
    t2 = r["rate"][:, 1]
    cand = _cands(r)
    sp2 = float(np.max([t2[c - 1] for c in cand[:5]]) - np.min([t2[c - 1] for c in cand[:5]]))
    return {
        "型": r["type"],
        "軸2車が同ライン": "同ライン" if same else "別ライン",
        "ライン本数": f"{int(r['nlines'])}本",
        "軸の堅さ axis_sum": "堅い" if r["axis"] >= 1.44 else "混戦",
        "相手の連対率のひらき": "大" if sp2 >= 20 else "小",
        "相手に軸と同ラインが居るか": "居る" if any(lg[c - 1] in tgt for c in cand[:3]) else "居ない",
    }


def cond():
    W = wins(load())
    sels = dict(SEL)
    sels["市場（予測オッズ）"] = _mkt_score
    sels["同ライン優先"] = _line_score
    data = {w: [r for r in W[w] if r["both_in3"] and r["third_car"]] for w in W}
    keys = list(_conds(data["confirm"][0]).keys())
    print("§4 条件ごとの「3着目を1車目で当てる率」（両窓）\n")
    for k in keys:
        buck = {w: defaultdict(list) for w in data}
        for w, rs in data.items():
            for r in rs:
                buck[w][_conds(r)[k]].append(r)
        vals = sorted(set(buck["confirm"]) | set(buck["explore"]))
        print(f"── {k}")
        hdr = "".join(f"{str(v)[:10]:>12s}" for v in vals)
        print(f"{'選び方':24s}{hdr}")
        for name, fn in sels.items():
            line_c = "".join(f"{_top1(fn, buck['confirm'][v]):11.1f}%" for v in vals)
            line_e = "".join(f"{_top1(fn, buck['explore'][v]):11.1f}%" for v in vals)
            print(f"{name:24s}{line_c}   確認")
            print(f"{'':24s}{line_e}   探索")
        print(f"{'n(確認/探索)':24s}" +
              "".join(f"{len(buck['confirm'][v]):5d}/{len(buck['explore'][v]):<6d}" for v in vals))
        print()



# ───────────────────── §5 商品化（ライン優先を型E/F へ条件分岐） ─────────────────────

def product():
    """`E_hit`/`F_hit` の選抜確率に「軸と同ラインの相手」ボーナス β を掛ける。
    点数・帯・配分・ゲートは現行のまま。対照は同数の車を無作為に選んで同じ β を掛ける。"""
    import random
    from src.type_lab import PLANS, allocate, build_legs, mean_expected_payout
    from src.type_lab import RaceShape
    MINM, MINP = 20_000, 2.0
    W = wins(load())

    def shape_of(r):
        order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
        return RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                         False, order, float(r["pw_ent"]))

    def targets(r, rng=None):
        lg = r["lg"]
        tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
        real = [c for c in range(1, 8) if c not in (r["a1"], r["a2"]) and lg[c - 1] in tgt]
        if rng is None:
            return set(real)
        pool = [c for c in range(1, 8) if c not in (r["a1"], r["a2"])]
        return set(rng.sample(pool, min(len(real), len(pool))))

    def run(rows, beta, rng=None):
        n = shown = 0
        inv = pay = 0.0
        for r in rows:
            plan = PLANS[r["plan"]]
            sh = shape_of(r)
            pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
            if beta != 1.0:
                T = targets(r, rng)
                prb = {c: v * (beta ** len(set(c if not r["trio"] else tuple(c)) & T))
                       for c, v in prb.items()}
            legs = build_legs(sh, plan, pod, prb)
            if not legs:
                continue
            st = allocate(legs, pod, prb, plan)
            if not st:
                continue
            m = mean_expected_payout(st, pod)
            if m <= MINM or min(float(pod[c]) for c in st) < MINP:
                continue
            b = float(sum(st.values()))
            if r["trio"]:
                p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
            else:
                p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
            n += 1
            inv += b
            pay += p
            shown += (p >= b)
        return n, shown / n * 100, pay / inv * 100

    print("§5 型E/F の相手に「軸と同ライン」ボーナス β（点数・帯・配分・ゲートは現行のまま）\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        base = run(rows, 1.0)
        print(f"--- {w}  対象 {len(rows):,}商品 ---")
        print(f"{'腕':18s}{'件数':>8}{'表示的中':>10}{'ROI':>9}{'Δ表示的中':>11}{'対照に勝ち':>10}")
        print(f"{'現行':18s}{base[0]:8d}{base[1]:9.2f}%{base[2]:8.1f}%")
        for beta in (1.5, 2.5, 4.0):
            a = run(rows, beta)
            ctrl = [run(rows, beta, random.Random(1000 + s))[1] for s in range(10)]
            winr = sum(1 for c in ctrl if a[1] > c)
            print(f"{'同ライン β=' + str(beta):18s}{a[0]:8d}{a[1]:9.2f}%{a[2]:8.1f}%"
                  f"{a[1] - base[1]:+10.2f}pt{winr:8d}/10")
        print()




# ───────────────────── §6 事前に見分けられるか（AUC） ─────────────────────

def auc():
    """『軸2車はそろうが現行の買い目は外す』を発走前に見分けられるか。
    見分けられないなら、どの買い方へ切り替えても条件分岐は成立しない。"""
    W = wins(load())

    def _auc(x, y):
        x = np.asarray(x, float); y = np.asarray(y, bool)
        if y.all() or not y.any():
            return float("nan")
        r = np.argsort(np.argsort(x)) + 1
        n1, n0 = y.sum(), (~y).sum()
        return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

    def feats(r):
        lg = r["lg"]
        cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
        t3, t2, t1 = r["rate"][:, 2], r["rate"][:, 1], r["rate"][:, 0]
        tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
        return {
            "3着内率 軸2車の平均": (t3[r["a1"] - 1] + t3[r["a2"] - 1]) / 2,
            "3着内率 相手上位3車の平均": float(np.mean([t3[c - 1] for c in cand[:3]])),
            "3着内率 軸−相手の差": (t3[r["a1"] - 1] + t3[r["a2"] - 1]) / 2
                                 - float(np.mean([t3[c - 1] for c in cand[:3]])),
            "連対率 軸2車の平均": (t2[r["a1"] - 1] + t2[r["a2"] - 1]) / 2,
            "連対率 相手のひらき": float(np.max([t2[c - 1] for c in cand[:5]])
                                      - np.min([t2[c - 1] for c in cand[:5]])),
            "1着率 軸1−軸2": t1[r["a1"] - 1] - t1[r["a2"] - 1],
            "ライン本数": r["nlines"],
            "軸2車が同ライン": float(lg[r["a1"] - 1] == lg[r["a2"] - 1]),
            "相手上位3車の同ライン数": float(sum(lg[c - 1] in tgt for c in cand[:3])),
            "軸のライン規模": float(r["lsize"][r["a1"] - 1]),
            "（参考）axis_sum": r["axis"],
            "（参考）gap": r["gap"],
        }

    print("§6 『軸2車そろい ∧ 現行の買い目は外れ』を発走前に見分けられるか（AUC）\n")
    print(f"{'量':30s}{'確認':>9}{'探索':>9}   （0.5＝情報なし）")
    names = list(feats(W["confirm"][0]).keys())
    out = {}
    for w in ("confirm", "explore"):
        rs = [r for r in W[w] if r["both_in3"]]
        y = [not r["hit"] for r in rs]
        F = [feats(r) for r in rs]
        out[w] = ({n: _auc([f[n] for f in F], y) for n in names}, len(rs), sum(y) / len(y) * 100)
    for n in names:
        print(f"{n:30s}{out['confirm'][0][n]:9.3f}{out['explore'][0][n]:9.3f}")
    for w in ("confirm", "explore"):
        print(f"  {w}: n={out[w][1]:,}  うち外れ {out[w][2]:.1f}%")


if __name__ == "__main__":
    {"anatomy": anatomy, "partner": partner, "resid": resid, "cond": cond, "product": product, "auc": auc}[sys.argv[1]]()
