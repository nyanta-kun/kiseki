#!/usr/bin/env python3
"""軸差し替え検証（10）: 食い違い頻度 → 低信頼層の実態 → 差し替え規則の直接対決。"""
from __future__ import annotations
import pickle, sys
import numpy as np

D = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/10_axis_swap"
ROWS = pickle.load(open(f"{D}/table.pkl", "rb"))
WINS = [("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")]
rng = np.random.default_rng(0)

def top3(r): return set(r["fin"])
def mk_top(r): return int(np.argmax(r["mk_win"])) + 1
def mk_rank(r, c): return int(np.argsort(-r["mk_win"]).tolist().index(c - 1)) + 1

# ───────── 軸の規則 ─────────
def base_TL(r):            # 型ラボ現行: 軸1 = p3 1位 / 軸2 = p3 2位
    return r["p3_o"][0], r["p3_o"][1]
def base_7S(r):            # 旧ランク 7S 近似: 軸1 = pw 1位 / 軸2 = p3 最上位（軸1除く）
    a1 = r["pw_o"][0]
    a2 = next(c for c in r["p3_o"] if c != a1)
    return a1, a2
def _a2_p3(r, a1): return next(c for c in r["p3_o"] if c != a1)

def rule_a1(kind):
    """軸1 を kind で置き換え、軸2 = p3 最上位（軸1除く）。"""
    def f(r, base):
        a1b, a2b = base(r)
        if kind == "p3": a1 = r["p3_o"][0]
        elif kind == "pw": a1 = r["pw_o"][0]
        elif kind == "hon": a1 = r["hon"] or a1b
        elif kind == "mk": a1 = mk_top(r)
        elif kind == "hon_if_2nd":           # 食い違いのときだけ市場へ寄せる（◎がモデル2位以下）
            a1 = r["hon"] if (r["hon"] and r["hon"] != a1b) else a1b
        elif kind == "mk_if_dis":            # 食い違いのときだけ予測オッズ最安へ
            a1 = mk_top(r) if mk_top(r) != a1b else a1b
        elif kind == "toward":               # 食い違い時: モデル上位3のうち市場順位が最良
            if mk_top(r) != a1b:
                cand = r["p3_o"][:3] if base is base_TL else r["pw_o"][:3]
                a1 = min(cand, key=lambda c: mk_rank(r, c))
            else: a1 = a1b
        elif kind == "away":                 # 食い違い時: モデル上位3のうち市場順位が最悪
            if mk_top(r) != a1b:
                cand = r["p3_o"][:3] if base is base_TL else r["pw_o"][:3]
                a1 = max(cand, key=lambda c: mk_rank(r, c))
            else: a1 = a1b
        elif kind == "away_agree":           # 一致時に嫌われ側へ（対照: 市場と逆張り）
            cand = r["p3_o"][:3] if base is base_TL else r["pw_o"][:3]
            a1 = max(cand, key=lambda c: mk_rank(r, c)) if mk_top(r) == a1b else a1b
        else: raise ValueError(kind)
        if a1 == a1b:
            return a1b, a2b
        return a1, _a2_p3(r, a1)
    return f

def rule_a2(kind):
    """軸1 は動かさず軸2 だけ。"""
    def f(r, base):
        a1, a2b = base(r)
        others = [c for c in range(1, 8) if c != a1]
        if kind == "mark":                   # ○（○=軸1 なら ◎、どちらも無ければ現行）
            m = [c for c in (r["tai"], r["hon"], r["san"]) if c and c != a1]
            a2 = m[0] if m else a2b
        elif kind == "mk_p3":                # 予測三連複オッズから導いた3着内シェア最上位
            if np.isfinite(r["mk_p3"]).all():
                a2 = max(others, key=lambda c: r["mk_p3"][c - 1])
            else: a2 = a2b
        elif kind == "mk_win":               # 予測三連単オッズの1着シェア最上位
            a2 = max(others, key=lambda c: r["mk_win"][c - 1])
        elif kind == "pw":                   # pw 最上位
            a2 = max(others, key=lambda c: r["pw"][c - 1])
        else: raise ValueError(kind)
        return a1, a2
    return f

def outcome(r, ax):
    a1, a2 = ax
    t = top3(r)
    return (a1 in t and a2 in t, r["fin"][0] == a1, a1 in t)

def boot_ci(d, B=2000):
    d = np.asarray(d, float); n = len(d)
    if n == 0: return (np.nan, np.nan, np.nan)
    bs = np.sort([d[rng.integers(0, n, n)].mean() for _ in range(B)]) * 100
    return d.mean() * 100, bs[int(B * .025)], bs[int(B * .975)]

# ───────── 層（閾値は探索窓の分位） ─────────
EX = [r for r in ROWS if r["win"] == "explore"]
_Q = {}
def q(k, p):
    if (k, p) not in _Q:
        _Q[(k, p)] = float(np.percentile([r[k] for r in EX if np.isfinite(r[k])], p))
    return _Q[(k, p)]
LAYERS = [
    ("全レース", lambda r: True),
    ("pw_gap12 下位25%（2番手と差がない）", lambda r: r["pw_gap12"] <= q("pw_gap12", 25)),
    ("pw_max 下位25%（軸1の占有が少ない）", lambda r: r["pw_max"] <= q("pw_max", 25)),
    ("pw_ent 上位25%", lambda r: r["pw_ent"] >= q("pw_ent", 75)),
    ("axis_sum 下位25%（§5.4 の層）", lambda r: r["axis_sum"] <= q("axis_sum", 25)),
    ("p3_gap12 下位25%", lambda r: r["p3_gap12"] <= q("p3_gap12", 25)),
    ("rp_sd 下位25%（実力伯仲）", lambda r: r["rp_sd"] <= q("rp_sd", 25)),
    ("軸1(p3)≠◎", lambda r: r["hon"] and r["p3_o"][0] != r["hon"]),
    ("軸1(p3)≠予測オッズ最安", lambda r: mk_top(r) != r["p3_o"][0]),
    ("pw_gap12下位25% ∧ 軸1(p3)≠◎", lambda r: r["pw_gap12"] <= q("pw_gap12", 25) and r["hon"] and r["p3_o"][0] != r["hon"]),
]

def section0():
    print("=" * 100)
    print("§0 「人気」と「モデル軸1」が食い違う頻度（余地）")
    print("=" * 100)
    for lbl, w in WINS:
        rs = [r for r in ROWS if r["win"] == w]
        n = len(rs)
        has = [r for r in rs if r["hon"]]
        f = lambda cond: sum(1 for r in rs if cond(r)) / n * 100
        print(f"\n[{lbl}] n={n:,}R  ◎あり {len(has)/n*100:.1f}%")
        print(f"  ◎ ≠ pw 1位                 {f(lambda r: r['hon'] and r['hon']!=r['pw_o'][0]):5.1f}%")
        print(f"  ◎ ≠ p3 1位（型ラボの軸1）   {f(lambda r: r['hon'] and r['hon']!=r['p3_o'][0]):5.1f}%")
        print(f"  ◎ = p3 2位                  {f(lambda r: r['hon'] and r['hon']==r['p3_o'][1]):5.1f}%")
        print(f"  ◎ = p3 3位以下              {f(lambda r: r['hon'] and r['hon'] in r['p3_o'][2:]):5.1f}%")
        print(f"  {{◎,○}} ≠ {{p3 1位,2位}}      {f(lambda r: r['hon'] and r['tai'] and {r['hon'],r['tai']}!=set(r['p3_o'][:2])):5.1f}%")
        print(f"  予測オッズ最安 ≠ pw 1位     {f(lambda r: mk_top(r)!=r['pw_o'][0]):5.1f}%")
        print(f"  予測オッズ最安 ≠ p3 1位     {f(lambda r: mk_top(r)!=r['p3_o'][0]):5.1f}%")
        print(f"  予測オッズ最安 ≠ ◎          {f(lambda r: r['hon'] and mk_top(r)!=r['hon']):5.1f}%")
        print(f"  pw 1位 ≠ p3 1位（モデル内） {f(lambda r: r['pw_o'][0]!=r['p3_o'][0]):5.1f}%")
        # 市場が2番手を上に見ている: ◎ = モデル2位
        print(f"  ◎ = pw 2位                  {f(lambda r: r['hon'] and r['hon']==r['pw_o'][1]):5.1f}%")
        # 食い違い層での軸1の質
        for nm, cond, ax in [("◎≠p3 1位 のレース", lambda r: r['hon'] and r['hon']!=r['p3_o'][0], None)]:
            sub = [r for r in rs if cond(r)]
            if not sub: continue
            t = lambda g: np.mean([outcome(r, g(r))[2] for r in sub]) * 100
            w1 = lambda g: np.mean([outcome(r, g(r))[1] for r in sub]) * 100
            print(f"  └ {nm} n={len(sub):,}: p3 1位の 1着/3着内 {w1(lambda r:(r['p3_o'][0],0)):.1f}/{t(lambda r:(r['p3_o'][0],0)):.1f}%"
                  f"  ◎の 1着/3着内 {w1(lambda r:(r['hon'],0)):.1f}/{t(lambda r:(r['hon'],0)):.1f}%")
        sub = [r for r in rs if mk_top(r) != r['p3_o'][0]]
        w1 = lambda g: np.mean([outcome(r, g(r))[1] for r in sub]) * 100
        t = lambda g: np.mean([outcome(r, g(r))[2] for r in sub]) * 100
        print(f"  └ 予測オッズ最安≠p3 1位 n={len(sub):,}: p3 1位の 1着/3着内 {w1(lambda r:(r['p3_o'][0],0)):.1f}/{t(lambda r:(r['p3_o'][0],0)):.1f}%"
              f"  最安の 1着/3着内 {w1(lambda r:(mk_top(r),0)):.1f}/{t(lambda r:(mk_top(r),0)):.1f}%")

def section1():
    print("\n" + "=" * 100)
    print("§1 「軸1の信頼が薄い」層の実態（型ラボ現行 軸1=p3 1位・軸2=p3 2位）")
    print("=" * 100)
    for lbl, w in WINS:
        rs = [r for r in ROWS if r["win"] == w]
        print(f"\n[{lbl}]")
        print(f"  {'層':38s} {'n':>7s} {'割合':>6s} {'二軸そろい':>9s} {'軸1 1着':>8s} {'軸1 3着内':>9s} {'軸1崩壊':>8s}  {'7S軸そろい':>9s}")
        for nm, cond in LAYERS:
            sub = [r for r in rs if cond(r)]
            if not sub: continue
            o = np.array([outcome(r, base_TL(r)) for r in sub], float)
            o7 = np.array([outcome(r, base_7S(r))[0] for r in sub], float)
            print(f"  {nm:38s} {len(sub):7,d} {len(sub)/len(rs)*100:5.1f}% {o[:,0].mean()*100:8.2f}% {o[:,1].mean()*100:7.2f}% {o[:,2].mean()*100:8.2f}% {100-o[:,2].mean()*100:7.2f}%  {o7.mean()*100:8.2f}%")

ARMS = [
    ("(a) 軸1=pw 1位（7S型）", rule_a1("pw")),
    ("(b) 軸1=◎", rule_a1("hon")),
    ("(c) 軸1=予測オッズ最安", rule_a1("mk")),
    ("(d1) 食い違い時のみ ◎へ", rule_a1("hon_if_2nd")),
    ("(d2) 食い違い時のみ 最安へ", rule_a1("mk_if_dis")),
    ("(d3) 食い違い時 上位3の市場最良", rule_a1("toward")),
    ("(d4) 食い違い時 上位3の市場最悪", rule_a1("away")),
    ("(d5) 一致時 上位3の市場最悪(逆張り対照)", rule_a1("away_agree")),
    ("(e1) 軸2=印(○/◎/▲)", rule_a2("mark")),
    ("(e2) 軸2=予測三連複の3着内シェア", rule_a2("mk_p3")),
    ("(e3) 軸2=予測オッズ1着シェア", rule_a2("mk_win")),
    ("(e4) 軸2=pw 最上位", rule_a2("pw")),
]

def section2(base, base_name, layers=LAYERS, arms=ARMS, metric=0, mname="二軸そろい"):
    print("\n" + "=" * 100)
    print(f"§2 差し替え規則の直接対決  基準={base_name}  指標={mname}  Δpt [95%CI]（レース単位 paired bootstrap）")
    print("=" * 100)
    cells = []
    for nm, cond in layers:
        print(f"\n[{nm}]")
        print(f"  {'腕':40s}" + "".join(f"{'  '+lbl[:2]+' n':>9s}{'基準%':>8s}{'新%':>8s}{'Δ [CI]':>24s}{'入替%':>7s}" for lbl, _ in WINS) + "   両窓")
        for anm, f in arms:
            line = f"  {anm:40s}"; signs = []
            for lbl, w in WINS:
                sub = [r for r in rs_cache[w] if cond(r)]
                if not sub:
                    line += " " * 56; signs.append(None); continue
                b = np.array([outcome(r, base(r))[metric] for r in sub], float)
                a = np.array([outcome(r, f(r, base))[metric] for r in sub], float)
                ch = np.mean([f(r, base) != base(r) for r in sub]) * 100
                m, lo, hi = boot_ci(a - b)
                sig = "+" if lo > 0 else ("-" if hi < 0 else "0")
                signs.append(sig)
                line += f"{len(sub):9,d}{b.mean()*100:8.2f}{a.mean()*100:8.2f}  {m:+6.2f} [{lo:+5.2f},{hi:+5.2f}]{ch:7.1f}"
            both = "✅" if signs == ["+", "+"] else ("❌❌" if signs == ["-", "-"] else "")
            line += f"   {both}"
            cells.append((nm, anm, signs))
            print(line)
    n_cells = len(cells)
    wins = [c for c in cells if c[2] == ["+", "+"]]
    print(f"\n  見たセル数 {n_cells}・両窓 CI が 0 を跨がず正: {len(wins)}  → " + ("; ".join(f'{a}×{b}' for a,b,_ in wins) or "なし"))

rs_cache = {w: [r for r in ROWS if r["win"] == w] for _, w in WINS}

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("0", "all"): section0()
    if what in ("1", "all"): section1()
    if what in ("2", "all"):
        section2(base_TL, "型ラボ現行(軸1=p3 1位/軸2=p3 2位)")
        section2(base_7S, "7S型(軸1=pw 1位/軸2=p3)", arms=[a for a in ARMS if not a[0].startswith("(a)")] + [("(a') 軸1=p3 1位", rule_a1("p3"))])
    if what in ("3", "all"):
        section2(base_TL, "型ラボ現行", metric=1, mname="軸1の1着率")
        section2(base_TL, "型ラボ現行", metric=2, mname="軸1の3着内率")
