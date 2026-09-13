#!/usr/bin/env python3
"""ライン「内部」の実力構造で、ライン優先の効かせ方を変えられるか（2026-09-14）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/line_inner.py <section>

section: desc | resid | auc | cover | prod | byplan | byplan_ci | tier_sweep | adopt

前提と既知の結果は `/tmp/ratebranch/BRIEF.md` /
`keirin/docs/type_lab/rate_line_branch_2026_09_13.md`。
一律のライン優先ボーナス β は既に商品で負けている（確認 −0.19 / 探索 +0.36pt）ので、
ここでは **ライン内部の実力構造で場合分けした β** が現行を両窓で上回るかを測る。

🔴 `lpos` は float。`str(x)=="1"` 比較は `"1.0"` で必ず外れる（既出の罠）。
"""
from __future__ import annotations

import pickle
import random
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


# ───────────────────── ライン内部の量（すべて発走前に確定する列だけ） ─────────────────────

def line_members(r, g):
    """ライン g の車番を隊列順（lpos 昇順・同値は車番）で返す。"""
    lg = r["lg"]
    lpos = r["lpos"]
    ms = [c for c in range(1, 8) if lg[c - 1] == g]
    return sorted(ms, key=lambda c: (float(lpos[c - 1]), c))


def axis_lines(r):
    return {r["lg"][r["a1"] - 1], r["lg"][r["a2"] - 1]}


def feats(r):
    """1レースぶんのライン内部構造。軸1のラインを主対象にする。"""
    rp = np.asarray(r["rp"], float)
    lg = r["lg"]
    a1, a2 = r["a1"], r["a2"]
    g1 = lg[a1 - 1]
    mem = line_members(r, g1)
    size = len(mem)
    lead = mem[0]
    vals = [rp[c - 1] for c in mem]
    # 先頭のレース内順位（1 = 競走得点が最も高い）
    lead_rank = int(1 + sum(1 for v in rp if v > rp[lead - 1]))
    f = {
        "size": size,
        "lead": lead,
        "lead_rp": rp[lead - 1],
        "lead_rank": lead_rank,
        "lead_first_rate": float(r["rate"][lead - 1, 0]),
        "rp_range": float(max(vals) - min(vals)) if size > 1 else 0.0,
        "rp_sd": float(np.std(vals)) if size > 1 else 0.0,
        "lead_minus_2nd": float(rp[lead - 1] - rp[mem[1] - 1]) if size > 1 else float("nan"),
        "lead_is_strongest": bool(rp[lead - 1] >= max(vals) - 1e-9) if size > 1 else True,
        "pos3_minus_lead": float(rp[mem[2] - 1] - rp[lead - 1]) if size > 2 else float("nan"),
        "a1_pos": float(r["lpos"][a1 - 1]),
        "a2_pos": float(r["lpos"][a2 - 1]),
        "same_line": g1 == lg[a2 - 1],
    }
    # ライン得点の 平均 vs 総和 で順位が変わるか（2車以上のラインだけで比べる）
    groups = {}
    for g in set(lg):
        ms = line_members(r, g)
        if len(ms) >= 2:
            s = sum(rp[c - 1] for c in ms)
            groups[g] = (s, s / len(ms))
    if len(groups) >= 2:
        by_sum = sorted(groups, key=lambda g: -groups[g][0])
        by_mean = sorted(groups, key=lambda g: -groups[g][1])
        f["sum_mean_swap"] = by_sum[0] != by_mean[0]
        f["a1_line_sum_rank"] = by_sum.index(g1) + 1 if g1 in groups else 0
        f["a1_line_mean_rank"] = by_mean.index(g1) + 1 if g1 in groups else 0
    else:
        f["sum_mean_swap"] = False
        f["a1_line_sum_rank"] = f["a1_line_mean_rank"] = 0
    return f


def _b(v, edges, labels):
    if v != v:                       # NaN
        return "—"
    for e, l in zip(edges, labels):
        if v < e:
            return l
    return labels[-1]


def bins(r):
    f = feats(r)
    return {
        "軸1ラインの人数": {1: "単騎", 2: "2車", 3: "3車"}.get(f["size"], "4車+"),
        "先頭のレース内順位(得点)": {1: "1位", 2: "2位", 3: "3位"}.get(f["lead_rank"], "4位以下"),
        "先頭の1着率": _b(f["lead_first_rate"], [5, 10, 18, 1e9], ["<5%", "5-10", "10-18", "18%+"]),
        "ライン内の得点レンジ": ("単騎" if f["size"] == 1 else
                        _b(f["rp_range"], [1.5, 3.5, 6.5, 1e9], ["<1.5", "1.5-3.5", "3.5-6.5", "6.5+"])),
        "先頭−番手の得点差": _b(f["lead_minus_2nd"], [-1.5, 0.0, 1.5, 1e9],
                        ["番手が強い(-1.5-)", "番手がやや強", "先頭やや強", "先頭が強い(1.5+)"]),
        "先頭がライン最強か": ("単騎" if f["size"] == 1 else
                       ("最強" if f["lead_is_strongest"] else "最強でない")),
        "3番手の見劣り(3番手−先頭)": _b(f["pos3_minus_lead"], [-6.5, -3.5, -1.5, 1e9],
                             ["-6.5以下", "-6.5〜-3.5", "-3.5〜-1.5", "-1.5以上"]),
        "軸1のライン内位置": {1.0: "先頭", 2.0: "番手", 3.0: "3番手"}.get(f["a1_pos"], "4番手"),
        "軸2のライン内位置": {1.0: "先頭", 2.0: "番手", 3.0: "3番手"}.get(f["a2_pos"], "4番手"),
        "軸2車が同ライン": "同ライン" if f["same_line"] else "別ライン",
        "総和順位と平均順位の入替": "入れ替わる" if f["sum_mean_swap"] else "同じ",
    }


# ───────────────────── §1 記述 ─────────────────────

def desc():
    """軸2車そろい時に「3着目が軸と同ラインから出る率」が各量でどう動くか。"""
    W = wins(load())
    data = {w: [r for r in W[w] if r["both_in3"] and r["third_car"]] for w in W}
    print("§1 軸2車そろい時、3着目が『軸と同ライン』から出る率（両窓・n つき）\n")
    for w in ("confirm", "explore"):
        rs = data[w]
        hit = sum(1 for r in rs if r["lg"][r["third_car"] - 1] in axis_lines(r))
        # 同ラインの相手が存在するレースだけの条件付き率も出す
        avail = [r for r in rs
                 if any(r["lg"][c - 1] in axis_lines(r)
                        for c in range(1, 8) if c not in (r["a1"], r["a2"]))]
        ah = sum(1 for r in avail if r["lg"][r["third_car"] - 1] in axis_lines(r))
        print(f"  {w}: 全体 {hit/len(rs)*100:.2f}%  (n={len(rs):,})   "
              f"同ライン相手が居るレースだけ {ah/len(avail)*100:.2f}% (n={len(avail):,})")
    print()
    keys = list(bins(data["confirm"][0]).keys())
    for k in keys:
        buck = {w: defaultdict(lambda: [0, 0]) for w in data}
        for w, rs in data.items():
            for r in rs:
                b = buck[w][bins(r)[k]]
                b[0] += 1
                b[1] += r["lg"][r["third_car"] - 1] in axis_lines(r)
        vals = sorted(set(buck["confirm"]) | set(buck["explore"]))
        print(f"── {k}")
        print(f"{'':22s}" + "".join(f"{str(v)[:14]:>16s}" for v in vals))
        for w in ("confirm", "explore"):
            cells = []
            for v in vals:
                n, h = buck[w][v]
                cells.append(f"{h/n*100:6.2f}% n={n:<6d}" if n else f"{'—':>15s}")
            print(f"{('確認' if w=='confirm' else '探索'):22s}" + "".join(f"{c:>16s}" for c in cells))
        print()


# ───────────────────── §2 残差（モデル p3 の相手順位で層別） ─────────────────────

def cand_groups(r, c):
    """候補車 c の、軸ラインとの関係（同ラインの中を更に割る）。"""
    lg = r["lg"]
    tgt = axis_lines(r)
    if lg[c - 1] not in tgt:
        return "別ライン"
    # c が属する軸ラインの中の、軸車との前後関係
    g = lg[c - 1]
    mem = line_members(r, g)
    ax = [x for x in mem if x in (r["a1"], r["a2"])]
    if not ax:
        return "別ライン"
    pc = mem.index(c)
    pa = min(mem.index(x) for x in ax)
    if pc < pa:
        return "同ライン・軸より前"
    return "同ライン・軸の直後" if pc == pa + 1 else "同ライン・さらに後ろ"


def cand_groups2(r, c):
    """位置関係を『そのラインに軸が何車居るか』込みで割る（2026-09-14 追加）。"""
    lg = r["lg"]
    tgt = axis_lines(r)
    if lg[c - 1] not in tgt:
        return "別ライン"
    g = lg[c - 1]
    mem = line_members(r, g)
    ax = [x for x in mem if x in (r["a1"], r["a2"])]
    if not ax:
        return "別ライン"
    pc = mem.index(c)
    pos = sorted(mem.index(x) for x in ax)
    if len(ax) == 2:
        return "軸2車と同ライン・後ろ" if pc > pos[-1] else "軸2車と同ライン・間/前"
    if pc < pos[0]:
        return "軸1車と同ライン・軸より前"
    return "軸1車と同ライン・軸の直後" if pc == pos[0] + 1 else "軸1車と同ライン・さらに後ろ"


def cand_rpdiff(r, c):
    """候補車と、同じラインに居る軸車との競走得点差。"""
    rp = np.asarray(r["rp"], float)
    lg = r["lg"]
    g = lg[c - 1]
    ax = [x for x in (r["a1"], r["a2"]) if lg[x - 1] == g]
    if not ax:
        return float("nan")
    return float(rp[c - 1] - max(rp[x - 1] for x in ax))


def resid():
    W = wins(load())
    print("§2 モデル p3 の相手順位で層別したうえで、ライン内部の関係が更に分けるか\n")
    for w in ("confirm", "explore"):
        rs = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        print(f"--- {w} (n={len(rs):,}レース) ---")
        # (a) 同ラインを「軸との前後関係」で割る
        b = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in rs:
            cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
            for j, c in enumerate(cand[:5], 1):
                g = cand_groups(r, c)
                cell = b[j][g]
                cell[0] += 1
                cell[1] += (c == r["third_car"])
                cell2 = b[j]["__all__"]
                cell2[0] += 1
                cell2[1] += (c == r["third_car"])
        b4 = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in rs:
            cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
            for j, c in enumerate(cand[:5], 1):
                cell = b4[j][cand_groups2(r, c)]
                cell[0] += 1
                cell[1] += (c == r["third_car"])
        cols4 = ["別ライン", "軸2車と同ライン・後ろ", "軸2車と同ライン・間/前",
                 "軸1車と同ライン・軸より前", "軸1車と同ライン・軸の直後", "軸1車と同ライン・さらに後ろ"]
        print("  (a2) 位置関係を『そのラインに軸が何車居るか』込みで割る")
        print(f"{'p3相手順位':>10s}" + "".join(f"{c[:16]:>19s}" for c in cols4))
        for j in sorted(b4):
            cells = []
            for c in cols4:
                n, h = b4[j][c]
                cells.append(f"{h/n*100:5.1f}% n={n:<5d}" if n >= 30 else (f"(n={n})" if n else "—"))
            print(f"{j:>10d}" + "".join(f"{c:>19s}" for c in cells))
        print()
        cols = ["__all__", "別ライン", "同ライン・軸より前", "同ライン・軸の直後", "同ライン・さらに後ろ"]
        print(f"{'p3相手順位':>10s}" + "".join(f"{c.replace('__all__','母数(全体)')[:14]:>18s}" for c in cols))
        for j in sorted(b):
            cells = []
            for c in cols:
                n, h = b[j][c]
                cells.append(f"{h/n*100:5.1f}% n={n:<5d}" if n >= 30 else
                             (f"(n={n})" if n else "—"))
            print(f"{j:>10d}" + "".join(f"{c:>18s}" for c in cells))
        print()
        # (b) 同ライン候補を「軸との得点差」で割る
        b2 = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in rs:
            cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
            for j, c in enumerate(cand[:5], 1):
                if cand_groups(r, c) == "別ライン":
                    continue
                d = cand_rpdiff(r, c)
                k = _b(d, [-4.0, -2.0, 0.0, 1e9], ["-4以下", "-4〜-2", "-2〜0", "0以上"])
                cell = b2[j][k]
                cell[0] += 1
                cell[1] += (c == r["third_car"])
        cols2 = ["-4以下", "-4〜-2", "-2〜0", "0以上"]
        print(f"  同ライン候補を『軸との競走得点差』で割る（{w}）")
        print(f"{'p3相手順位':>10s}" + "".join(f"{c:>16s}" for c in cols2))
        for j in sorted(b2):
            cells = []
            for c in cols2:
                n, h = b2[j][c]
                cells.append(f"{h/n*100:5.1f}% n={n:<5d}" if n >= 30 else (f"(n={n})" if n else "—"))
            print(f"{j:>10d}" + "".join(f"{c:>16s}" for c in cells))
        print()
        # (c) レース側の条件（先頭がライン最強か 等）で、同ライン候補の当たり率が動くか
        for key in ("先頭がライン最強か", "先頭のレース内順位(得点)", "ライン内の得点レンジ",
                    "先頭−番手の得点差"):
            b3 = defaultdict(lambda: [0, 0, 0, 0])   # n_same, h_same, n_other, h_other
            for r in rs:
                cand = [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]
                v = bins(r)[key]
                for c in cand[:5]:
                    same = cand_groups(r, c) != "別ライン"
                    cell = b3[v]
                    cell[0 if same else 2] += 1
                    cell[1 if same else 3] += (c == r["third_car"])
            print(f"  {key}（{w}・相手上位5車の当たり率／同ライン vs 別ライン）")
            for v in sorted(b3):
                ns, hs, no, ho = b3[v]
                if ns < 50 or no < 50:
                    continue
                print(f"    {v:>20s}  同ライン {hs/ns*100:5.2f}% (n={ns:5d})   "
                      f"別ライン {ho/no*100:5.2f}% (n={no:5d})   比 {(hs/ns)/(ho/no):.2f}")
            print()


# ───────────────────── §3 商品化（条件つき β） ─────────────────────

def _mk_arms():
    """条件つき重み。`w(r)` は **非軸車 → 係数** の dict を返す（1.0 は現行と同じ）。

    🔴 一律 β は既に負けている。ここで測るのは「ライン内部の位置・実力構造で
       係数を変える」腕だけ。対照は同じ係数の多重集合を非軸車へ**無作為に配る**。
    """
    def flat(b):
        def f(r):
            tgt = axis_lines(r)
            return {c: b for c in range(1, 8)
                    if c not in (r["a1"], r["a2"]) and r["lg"][c - 1] in tgt}
        return f

    def bypos(table):
        def f(r):
            out = {}
            for c in range(1, 8):
                if c in (r["a1"], r["a2"]):
                    continue
                v = table.get(cand_groups2(r, c), 1.0)
                if v != 1.0:
                    out[c] = v
            return out
        return f

    def race_cond(pred, hi, lo):
        """レース側の条件で同ライン相手への係数を切り替える。"""
        def f(r):
            b = hi if pred(r) else lo
            tgt = axis_lines(r)
            return {c: b for c in range(1, 8)
                    if c not in (r["a1"], r["a2"]) and r["lg"][c - 1] in tgt}
        return f

    A2B = "軸2車と同ライン・後ろ"
    A2F = "軸2車と同ライン・間/前"
    A1F = "軸1車と同ライン・軸より前"
    A1N = "軸1車と同ライン・軸の直後"
    A1B = "軸1車と同ライン・さらに後ろ"

    def _lead2(r):
        v = feats(r)["lead_minus_2nd"]
        return v == v and abs(v) < 1.5          # 先頭と番手が拮抗

    return {
        "[参考] 一律 β=2.0": flat(2.0),
        "[参考] 一律 β=1.5": flat(1.5),
        "位置① 軸2車ライン残りのみ 2.0": bypos({A2B: 2.0, A2F: 2.0}),
        "位置② 同上 1.5": bypos({A2B: 1.5, A2F: 1.5}),
        "位置③ 軸1車ラインの遠い車 0.5": bypos({A1B: 0.5}),
        "位置④ 同上 0.25": bypos({A1B: 0.25}),
        "位置⑤ ①+③": bypos({A2B: 2.0, A2F: 2.0, A1B: 0.5}),
        "位置⑥ 段階 2.0/1.3/0.5": bypos({A2B: 2.0, A2F: 2.0, A1F: 1.3, A1N: 1.3, A1B: 0.5}),
        "位置⑦ 段階 1.5/1.0/0.3": bypos({A2B: 1.5, A2F: 1.5, A1B: 0.3}),
        "構造① 先頭番手が拮抗 2.0 / 他 1.0": race_cond(_lead2, 2.0, 1.0),
        "構造② 先頭番手が拮抗 1.5 / 他 0.7": race_cond(_lead2, 1.5, 0.7),
        "構造③ 得点レンジ<6.5 1.5 / 他 0.7":
            race_cond(lambda r: feats(r)["rp_range"] < 6.5, 1.5, 0.7),
        "構造④ 先頭がライン最強でない 1.5 / 他 0.7":
            race_cond(lambda r: not feats(r)["lead_is_strongest"], 1.5, 0.7),
    }


def _run(rows, wf, rng=None, want_per_race=False):
    from src.type_lab import PLANS, RaceShape, allocate, build_legs, mean_expected_payout
    MINM, MINP = 20_000, 2.0
    n = shown = 0
    inv = pay = 0.0
    per = {}
    for r in rows:
        plan = PLANS[r["plan"]]
        order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
        sh = RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                       False, order, float(r["pw_ent"]))
        pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
        w = wf(r) if wf else {}
        if w:
            if rng is not None:
                pool = [c for c in range(1, 8) if c not in (r["a1"], r["a2"])]
                vals = list(w.values())
                rng.shuffle(pool)
                w = dict(zip(pool[:len(vals)], vals))
            def mult(comb):
                m = 1.0
                for car in comb:
                    m *= w.get(car, 1.0)
                return m
            prb = {c: v * mult(tuple(c)) for c, v in prb.items()}
        legs = build_legs(sh, plan, pod, prb)
        if not legs:
            continue
        st = allocate(legs, pod, prb, plan)
        if not st:
            continue
        m = mean_expected_payout(st, pod)
        if m <= MINM or min(float(pod[c]) for c in st) < MINP:
            continue
        bet = float(sum(st.values()))
        if r["trio"]:
            p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
        else:
            p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
        n += 1
        inv += bet
        pay += p
        shown += (p >= bet)
        if want_per_race:
            per[r["race_key"] + "|" + r["plan"]] = (1, 1 if p >= bet else 0)
    return n, (shown / n * 100 if n else 0.0), (pay / inv * 100 if inv else 0.0), per


def _ci(d, iters=2000, seed=7):
    """レース単位 bootstrap で Δ表示的中の95%CI。d = list[(base_hit, arm_hit)]（両方在るレースのみ）。"""
    if not d:
        return (float("nan"), float("nan"))
    a = np.array([x[0] for x in d], float)
    b = np.array([x[1] for x in d], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(iters, len(d)))
    diff = (b[idx].mean(axis=1) - a[idx].mean(axis=1)) * 100
    return float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))


def prod():
    W = wins(load())
    arms = _mk_arms()
    print("§3 条件つき β を E_hit/F_hit の選抜確率へ（点数・帯・配分・ゲートは現行のまま）\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        bn, bs, br, bper = _run(rows, None, want_per_race=True)
        print(f"--- {w}  対象 {len(rows):,}商品 ---")
        print(f"{'腕':34s}{'件数':>7}{'表示的中':>10}{'ROI':>8}{'Δ表示的中':>11}"
              f"{'95%CI':>20}{'対照勝ち':>9}")
        print(f"{'現行':34s}{bn:7d}{bs:9.2f}%{br:7.1f}%")
        for name, fn in arms.items():
            an, asn, ar, aper = _run(rows, fn, want_per_race=True)
            common = [k for k in bper if k in aper]
            d = [(bper[k][1], aper[k][1]) for k in common]
            lo, hi = _ci(d)
            ctrl = [_run(rows, fn, random.Random(2000 + s))[1] for s in range(10)]
            winr = sum(1 for c in ctrl if asn > c)
            print(f"{name:34s}{an:7d}{asn:9.2f}%{ar:7.1f}%{asn-bs:+10.2f}pt"
                  f"{f'[{lo:+.2f},{hi:+.2f}]':>20}{winr:6d}/10")
        print()


# ───────────────────── §4 事前に見分けられるか（AUC） ─────────────────────

def auc():
    W = wins(load())

    def _auc(x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, bool)
        ok = ~np.isnan(x)
        x, y = x[ok], y[ok]
        if y.all() or not y.any():
            return float("nan")
        rk = np.argsort(np.argsort(x)) + 1
        n1, n0 = y.sum(), (~y).sum()
        return float((rk[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

    names = ["lead_rank", "lead_first_rate", "rp_range", "rp_sd", "lead_minus_2nd",
             "pos3_minus_lead", "a1_pos", "a2_pos"]
    print("§4 『軸2車そろい ∧ 現行の買い目は外れ』を ライン内部構造で見分けられるか（AUC）\n")
    print(f"{'量':24s}{'確認':>9}{'探索':>9}   （0.5＝情報なし）")
    out = {}
    for w in ("confirm", "explore"):
        rs = [r for r in W[w] if r["both_in3"]]
        y = [not r["hit"] for r in rs]
        F = [feats(r) for r in rs]
        out[w] = ({n: _auc([f[n] for f in F], y) for n in names},
                  {n: _auc([1.0 if f[n] else 0.0 for f in F], y)
                   for n in ("lead_is_strongest", "same_line", "sum_mean_swap")},
                  len(rs))
    for n in names:
        print(f"{n:24s}{out['confirm'][0][n]:9.3f}{out['explore'][0][n]:9.3f}")
    for n in ("lead_is_strongest", "same_line", "sum_mean_swap"):
        print(f"{n:24s}{out['confirm'][1][n]:9.3f}{out['explore'][1][n]:9.3f}")
    for w in ("confirm", "explore"):
        print(f"  {w}: n={out[w][2]:,}")


#: 位置の段（`cand_groups2` → 係数）。`別ライン` = 1.0 を基準にする。
TIER = {"軸2車と同ライン・後ろ": 2.0, "軸2車と同ライン・間/前": 2.0,
        "軸1車と同ライン・軸の直後": 1.2, "軸1車と同ライン・軸より前": 1.2,
        "別ライン": 1.0, "軸1車と同ライン・さらに後ろ": 0.4}


def tier_map(r, rng=None):
    """非軸車 → 位置の段の係数。rng を渡すと同じ係数を無作為に配り直す（対照）。"""
    w = {c: TIER[cand_groups2(r, c)] for c in range(1, 8) if c not in (r["a1"], r["a2"])}
    if rng is None:
        return w
    pool = list(w)
    vals = list(w.values())
    rng.shuffle(vals)
    return dict(zip(pool, vals))


def _run_full(rows, mode, rng=None):
    """位置の段を **確率へ / 相手の並び順へ / 両方へ** 効かせる。

    mode: None（現行） / 'p'（probs のみ） / 'o'（order のみ） / 'po'（両方）

    🔴 `probs` を触っても動かないプランがある（`axis2_flow` / `axis1_second2` /
       `fixed12` は `shape.order` で相手を採る）。逆に `prob_top` は order を見ない。
       だから2経路を分けて測る。
    """
    from src.type_lab import PLANS, RaceShape, allocate, build_legs, mean_expected_payout
    MINM, MINP = 20_000, 2.0
    inv = pay = 0.0
    pays, days, per = [], set(), {}
    for r in rows:
        plan = PLANS[r["plan"]]
        p3 = r["p3vec"]
        mode_r = mode.get(r["plan"], "") if isinstance(mode, dict) else (mode or "")
        order = tuple(sorted(range(1, 8), key=lambda c: (-p3[c - 1], c)))
        w = tier_map(r, rng) if mode_r else {}
        if "o" in mode_r:
            head = list(order[:2])
            tail = sorted(order[2:], key=lambda c: (-p3[c - 1] * w.get(c, 1.0), c))
            order = tuple(head + tail)
        sh = RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                       False, order, float(r["pw_ent"]))
        pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
        if "p" in mode_r:
            prb = {c: v * math_prod(w, tuple(c)) for c, v in prb.items()}
        legs = build_legs(sh, plan, pod, prb)
        if not legs:
            continue
        st = allocate(legs, pod, prb, plan)
        if not st:
            continue
        if mean_expected_payout(st, pod) <= MINM or min(float(pod[c]) for c in st) < MINP:
            continue
        bet = float(sum(st.values()))
        if r["trio"]:
            p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
        else:
            p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
        inv += bet
        pay += p
        pays.append(p)
        days.add(r["date"])
        per[r["race_key"] + "|" + r["plan"]] = 1 if p >= bet else 0
    n = len(pays)
    hits = [p for p in pays if p > 0]
    return {"n": n, "per_day": n / max(len(days), 1),
            "shown": sum(per.values()) / n * 100 if n else 0.0,
            "roi": pay / inv * 100 if inv else 0.0,
            "med_pay": float(np.median(hits)) if hits else 0.0,
            "big": sum(1 for p in pays if p >= 100_000) / max(len(days), 1),
            "per": per}


PLAN_ORDER = ["A_hit", "A_trio", "A_ana", "B_hit", "C_hit", "D_hit",
              "E_hit", "F_hit", "F_sign"]


def byplan():
    """プラン別に『位置の段』を 確率 / 並び順 / 両方 へ効かせたときの表示的中。"""
    W = wins(load())
    print("§6 位置の段をプラン別に当てる（点数・帯・配分・ゲートは現行のまま）\n")
    for w in ("confirm", "explore"):
        rows = W[w]
        print(f"--- {w} ---")
        print(f"{'plan':9s}{'件数':>7}{'現行':>9}{'確率へ':>9}{'並び順へ':>9}{'両方':>9}"
              f"{'   ROI 現行→両方':>18}")
        for p in PLAN_ORDER + ["(全体)"]:
            rs = rows if p == "(全体)" else [r for r in rows if r["plan"] == p]
            b = _run_full(rs, None)
            res = {m: _run_full(rs, m) for m in ("p", "o", "po")}
            print(f"{p:9s}{b['n']:7d}{b['shown']:8.2f}%"
                  + "".join(f"{res[m]['shown']:8.2f}%" for m in ("p", "o", "po"))
                  + f"{b['roi']:11.1f}→{res['po']['roi']:.1f}%")
        print()


def byplan_ci():
    """`byplan` で見込みのあるプランだけ、Δ の 95%CI と無作為対照20seed を付ける。"""
    W = wins(load())
    COMBO = {"A_hit": "p", "A_trio": "o"}
    COMBO2 = {"A_hit": "p", "A_trio": "o", "F_sign": "p"}
    targets = [("A_hit", "p"), ("A_trio", "o"), ("F_sign", "p"),
               ("(全体)", COMBO), ("(全体)", COMBO2)]
    for w in ("confirm", "explore"):
        rows = W[w]
        print(f"--- {w} ---")
        print(f"{'plan/mode':24s}{'件数':>7}{'表示的中':>10}{'Δ':>9}{'95%CI':>18}"
              f"{'ROI':>8}{'払戻中央':>10}{'10万+/日':>9}{'対照':>7}")
        for p, m in targets:
            rs = rows if p == "(全体)" else [r for r in rows if r["plan"] == p]
            b = _run_full(rs, None)
            a = _run_full(rs, m)
            common = [k for k in b["per"] if k in a["per"]]
            lo, hi = _ci([(b["per"][k], a["per"][k]) for k in common])
            ctrl = [_run_full(rs, m, random.Random(4000 + s))["shown"] for s in range(20)]
            winr = sum(1 for c in ctrl if a["shown"] > c)
            label = p + "/" + (m if isinstance(m, str) else "A_hit+A_trio" + ("+F_sign" if len(m) > 2 else ""))
            print(f"{label:24s}{a['n']:7d}{a['shown']:9.2f}%"
                  f"{a['shown']-b['shown']:+8.2f}pt{f'[{lo:+.2f},{hi:+.2f}]':>18}"
                  f"{a['roi']:7.1f}%{a['med_pay']:10,.0f}{a['big']:9.3f}{winr:5d}/20")
        print()


def _run_kpi(rows, wf, rng=None):
    """`_run` と同じ買い方で、件/日・払戻中央・10万+/日 まで返す。"""
    from src.type_lab import PLANS, RaceShape, allocate, build_legs, mean_expected_payout
    MINM, MINP = 20_000, 2.0
    inv = pay = 0.0
    pays, days, per = [], set(), {}
    for r in rows:
        plan = PLANS[r["plan"]]
        order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
        sh = RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                       False, order, float(r["pw_ent"]))
        pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
        w = wf(r) if wf else {}
        if w:
            if rng is not None:
                pool = [c for c in range(1, 8) if c not in (r["a1"], r["a2"])]
                vals = list(w.values())
                rng.shuffle(pool)
                w = dict(zip(pool[:len(vals)], vals))
            prb = {c: v * math_prod(w, tuple(c)) for c, v in prb.items()}
        legs = build_legs(sh, plan, pod, prb)
        if not legs:
            continue
        st = allocate(legs, pod, prb, plan)
        if not st:
            continue
        if mean_expected_payout(st, pod) <= MINM or min(float(pod[c]) for c in st) < MINP:
            continue
        bet = float(sum(st.values()))
        if r["trio"]:
            p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
        else:
            p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
        inv += bet
        pay += p
        pays.append(p)
        days.add(r["date"])
        per[r["race_key"] + "|" + r["plan"]] = 1 if p >= bet else 0
    n = len(pays)
    hits = [p for p in pays if p > 0]
    return {
        "n": n, "per_day": n / max(len(days), 1),
        "shown": sum(1 for k, v in per.items() if v) / n * 100 if n else 0.0,
        "roi": pay / inv * 100 if inv else 0.0,
        "med_pay": float(np.median(hits)) if hits else 0.0,
        "big": sum(1 for p in pays if p >= 100_000) / max(len(days), 1),
        "per": per,
    }


def math_prod(w, comb):
    m = 1.0
    for car in comb:
        m *= w.get(car, 1.0)
    return m


def final():
    """有望な腕だけ、KPI を揃えて（件/日・払戻中央・10万+/日）・対照20seed で確認する。"""
    W = wins(load())
    arms = _mk_arms()
    pick = ["[参考] 一律 β=2.0", "位置① 軸2車ライン残りのみ 2.0", "位置③ 軸1車ラインの遠い車 0.5",
            "位置⑤ ①+③", "位置⑥ 段階 2.0/1.3/0.5", "位置⑦ 段階 1.5/1.0/0.3"]
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        base = _run_kpi(rows, None)
        print(f"--- {w}  対象 {len(rows):,}商品 ---")
        print(f"{'腕':30s}{'件/日':>7}{'表示的中':>9}{'ROI':>7}{'払戻中央':>9}"
              f"{'10万+/日':>9}{'Δ表示的中':>10}{'95%CI':>18}{'対照':>7}")
        print(f"{'現行':30s}{base['per_day']:7.2f}{base['shown']:8.2f}%{base['roi']:6.1f}%"
              f"{base['med_pay']:9,.0f}{base['big']:9.3f}")
        for name in pick:
            a = _run_kpi(rows, arms[name])
            common = [k for k in base["per"] if k in a["per"]]
            d = [(base["per"][k], a["per"][k]) for k in common]
            lo, hi = _ci(d)
            ctrl = [_run_kpi(rows, arms[name], random.Random(3000 + s))["shown"] for s in range(20)]
            winr = sum(1 for c in ctrl if a["shown"] > c)
            print(f"{name:30s}{a['per_day']:7.2f}{a['shown']:8.2f}%{a['roi']:6.1f}%"
                  f"{a['med_pay']:9,.0f}{a['big']:9.3f}{a['shown']-base['shown']:+9.2f}pt"
                  f"{f'[{lo:+.2f},{hi:+.2f}]':>18}{winr:5d}/20")
        print()


def cover():
    """3着目を相手 m 車で覆える率（BRIEF §2 と同じ形式・位置優先を追加）。"""
    W = wins(load())
    TIER = {"軸2車と同ライン・後ろ": 3.0, "軸2車と同ライン・間/前": 3.0,
            "軸1車と同ライン・軸の直後": 2.0, "軸1車と同ライン・軸より前": 2.0,
            "別ライン": 1.0, "軸1車と同ライン・さらに後ろ": 0.0}

    def s_p3(r):
        return r["p3vec"]

    def s_line(r):
        lg = r["lg"]
        tgt = axis_lines(r)
        return np.array([(1.0 if lg[c] in tgt else 0.0) + 1e-6 * r["p3vec"][c] for c in range(7)])

    def s_pos(r):
        return np.array([TIER[cand_groups2(r, c)] + 1e-6 * r["p3vec"][c - 1]
                         for c in range(1, 8)])

    def s_pos_p3(r):
        """位置の段をモデル p3 に掛ける（順位を潰さず重みだけ変える）。"""
        return np.array([r["p3vec"][c - 1] * {3.0: 2.0, 2.0: 1.2, 1.0: 1.0, 0.0: 0.4}[
            TIER[cand_groups2(r, c)]] for c in range(1, 8)])

    sels = {"モデル p3（現行）": s_p3, "同ライン優先（既出）": s_line,
            "位置の段のみ": s_pos, "位置の段 × p3": s_pos_p3}
    print("§5 軸2車がそろった回の「3着目の車」を、相手上位 m 車で覆える率\n")
    print(f"{'選び方':22s}" + "".join(f"{f'm={m}':>9s}" for m in (1, 2, 3)) + "   窓")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        for name, fn in sels.items():
            cov = []
            for m in (1, 2, 3):
                ok = 0
                for r in both:
                    s = fn(r)
                    cand = [c for c in np.argsort(-s) + 1 if c not in (r["a1"], r["a2"])]
                    ok += r["third_car"] in cand[:m]
                cov.append(ok / len(both) * 100)
            print(f"{name:22s}" + "".join(f"{v:8.2f}%" for v in cov) + f"   {w} n={len(both):,}")
        print()


# ───────────────────── §7 位置の段の強さを掃く / 採用候補の確認 ─────────────────────

TIER_SETS = {
    "強 2.0/1.2/1.0/0.4": (2.0, 1.2, 1.0, 0.4),
    "中 1.5/1.1/1.0/0.7": (1.5, 1.1, 1.0, 0.7),
    "弱 1.25/1.05/1.0/0.85": (1.25, 1.05, 1.0, 0.85),
    "罰のみ 1/1/1/0.4": (1.0, 1.0, 1.0, 0.4),
    "賞のみ 2/1/1/1": (2.0, 1.0, 1.0, 1.0),
}


def set_tier(t):
    a2, a1n, other, far = t
    TIER.clear()
    TIER.update({"軸2車と同ライン・後ろ": a2, "軸2車と同ライン・間/前": a2,
                 "軸1車と同ライン・軸の直後": a1n, "軸1車と同ライン・軸より前": a1n,
                 "別ライン": other, "軸1車と同ライン・さらに後ろ": far})


def _paired(rs, mode, ctrl_seeds=0):
    b = _run_full(rs, None)
    a = _run_full(rs, mode)
    common = [k for k in b["per"] if k in a["per"]]
    bb = np.mean([b["per"][k] for k in common]) * 100
    aa = np.mean([a["per"][k] for k in common]) * 100
    lo, hi = _ci([(b["per"][k], a["per"][k]) for k in common])
    cd = []
    for s in range(ctrl_seeds):
        c = _run_full(rs, mode, random.Random(5000 + s))
        cc = [k for k in b["per"] if k in c["per"]]
        cd.append(np.mean([c["per"][k] for k in cc]) * 100
                  - np.mean([b["per"][k] for k in cc]) * 100)
    return b, a, aa - bb, lo, hi, cd


def tier_sweep():
    """位置の段の強さ × プラン（A_hit は確率経路・A_trio は並び順経路）。"""
    W = wins(load())
    for p, m in (("A_hit", "p"), ("A_trio", "o")):
        print(f"== {p} / {m} ==")
        print(f"{'段':24s}{'窓':>8}{'件数':>14}{'Δ(対)':>9}{'95%CI':>18}{'ROI':>8}")
        for name, t in TIER_SETS.items():
            for w in ("confirm", "explore"):
                set_tier(t)
                rs = [r for r in W[w] if r["plan"] == p]
                b, a, d, lo, hi, _ = _paired(rs, m)
                print(f"{name:24s}{w:>8}{b['n']:6d}→{a['n']:<7d}{d:+8.2f}pt"
                      f"{f'[{lo:+.2f},{hi:+.2f}]':>18}{a['roi']:7.1f}%")
        print()


def adopt():
    """採用候補（A_hit にだけ 位置の段「中」）を、対照20seed 付きで確認する。"""
    W = wins(load())
    set_tier(TIER_SETS["中 1.5/1.1/1.0/0.7"])
    for scope, rows_of in (("A_hit 単体", lambda w: [r for r in W[w] if r["plan"] == "A_hit"]),
                           ("ラインナップ全体", lambda w: W[w])):
        print(f"── {scope}")
        for w in ("confirm", "explore"):
            rs = rows_of(w)
            b, a, d, lo, hi, cd = _paired(rs, {"A_hit": "p"}, ctrl_seeds=20)
            winr = sum(1 for x in cd if d > x)
            print(f"  {w}: 件/日 {b['per_day']:.2f}→{a['per_day']:.2f}  "
                  f"表示的中 {b['shown']:.2f}→{a['shown']:.2f}%  "
                  f"Δ(対) {d:+.2f}pt [{lo:+.2f},{hi:+.2f}]  "
                  f"ROI {b['roi']:.1f}→{a['roi']:.1f}%  "
                  f"払戻中央 {b['med_pay']:,.0f}→{a['med_pay']:,.0f}  "
                  f"10万+/日 {b['big']:.3f}→{a['big']:.3f}  "
                  f"対照 {winr}/20（対照中央Δ {np.median(cd):+.2f}pt）")
        print()


if __name__ == "__main__":
    {"desc": desc, "resid": resid, "prod": prod, "auc": auc, "cover": cover, "final": final, "byplan": byplan, "byplan_ci": byplan_ci,
     "tier_sweep": tier_sweep, "adopt": adopt}[sys.argv[1]]()
