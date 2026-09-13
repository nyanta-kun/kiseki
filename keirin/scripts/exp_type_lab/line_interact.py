#!/usr/bin/env python3
"""ライン「同士」の相互関係と単騎で、ライン優先の効かせ方を変えられるか（2026-09-14）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/line_interact.py <section>

section: comp | inter | product | ctrl

先に読んだ本番（CLAUDE.md「測る前に本番コードを読む」）:
  - 売る商品は `sell_plans_for`、買い目は `build_legs`(+`allocate`)。
    `E_hit`/`F_hit` は `structure="prob_top"`＝**選抜確率の降順**で帯の中から k 点。
    ＝確率へボーナスを掛けると買い目そのものが動く。
  - 確率は `rank_7t3_blend_probs`（PL × 同ライン隣接 λ/μ）。
    ライン列は `FEATURE_COLS_WT` にも入っている＝**二重計上を疑うこと**。
  - 前段（`rate_branch.py`）で一律 β は両窓で符号反転して不採用。本スクリプトは
    「編成で効かせ方を変える」＝条件つき β を測る。

## 結論（2026-09-14・**採用候補なし**）

1. **編成は「3着目がどこから出るか」を大きく分ける**（両窓で再現・§1）。
   軸と同ラインから出る率は 4-3 で 74.4/68.1% ↔ 2-2-2-1 で 27.0/29.9%。
   単騎が指数上位(1-2位)に居ると 34.3/35.6% まで落ちる。逃げ型4人以上でも落ちる。
2. **だが「ライン優先の優位」はほぼ全編成で一定**（§2・§5）。車ごとの
   同ラインボーナス β を掃引すると**最良 β はどのセルでも 2.0〜3.0**。
   例外は `逃げ型3人以上`（最良 β 1.2〜1.5・優位ほぼ 0）と `ライン2本`（窓で反転・n小）。
   ＝ 編成は「3着がどこから出るか」を変えるが、**モデル p3 が既にその分を織り込んでいる**
   ので「p3 に対してラインをどれだけ足すか」は編成に依らない。
3. 🔴 **商品では条件つき β も効かない**（§3）。10通りの条件 × β=1.5/2.5 の**20腕すべて**が
   確認窓で 0 以下か、窓で符号反転。しかも **`逃げ型2人以下` に限る条件つき版は一律版より悪い**
   （確認 −0.34 ↔ −0.22 / 探索 +0.73 ↔ +0.89）。
4. 🔴🔴 **機序＝ライン情報は本物だが現行モデルに勝てない**（§4 の対照実験）。
   一律 β=1.5 は**無作為対照に探索 20/20 で勝つ**（22.35% → 23.06%）のに、
   **現行（22.71%）を確認窓では下回る**（23.20% → 23.01%）。
   ＝「対照に勝つ」は採用理由にならない実例。ラインは `rank_7t3_blend_probs` の λ/μ と
   `FEATURE_COLS_WT` の line 列に既に二重に入っている。
5. 🔴 **そもそも `E_hit`/`F_hit` では相手選定の余地が小さい**（§6）。外れの内訳は
   順序違い 26.2/26.0% ・軸崩壊 44.6/44.9% に対し **相手外しは 5.6/6.0% だけ**
   （12〜14点買うので相手はたいてい既に入っている）。相手を**完全に当てた**オラクルでも
   Δ表示的中 +9.85/+9.46pt が上限で、現実の β はそこから 0 しか取れない。
6. 🟢 **副産物**: 3着目の被覆率だけなら**軟らかい β の方が硬い「同ライン優先」より良い**
   （両窓一致・§7）。p3 42.30/42.45% → 硬 44.68/44.90% → **β=2.0 で 45.62/45.81%**。
   ただし 3〜5 で商品には乗らない。
"""
from __future__ import annotations

import pickle
import random
import sys
from collections import Counter, defaultdict

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


# ───────────────────────── ライン編成の量 ─────────────────────────

def lines_of(r):
    """ライン記号 -> 車番リスト（隊列順）。"""
    g = defaultdict(list)
    for c in range(1, 8):
        g[str(r["lg"][c - 1])].append(c)
    for k in g:
        g[k].sort(key=lambda c: (float(r["lpos"][c - 1]), c))
    return dict(g)


def comp_label(r) -> str:
    """編成パターン '3-3-1' など（人数の降順）。"""
    sz = sorted((len(v) for v in lines_of(r).values()), reverse=True)
    return "-".join(str(x) for x in sz)


def n_tanki(r) -> int:
    return int(sum(1 for v in lines_of(r).values() if len(v) == 1))


def tanki_label(r) -> str:
    n = n_tanki(r)
    return "単騎0" if n == 0 else ("単騎1" if n == 1 else "単騎2+")


def tanki_strength(r) -> str:
    """単騎の最強車が、そのレースの p3 順位で何番手か。"""
    L = lines_of(r)
    solo = [v[0] for v in L.values() if len(v) == 1]
    if not solo:
        return "単騎なし"
    rank = {c: j for j, c in enumerate(np.argsort(-r["p3vec"]) + 1, 1)}
    best = min(rank[c] for c in solo)
    return "単騎 上位(1-2)" if best <= 2 else ("単騎 中位(3-4)" if best <= 4 else "単騎 下位(5+)")


def line_rp_stats(r):
    """(ライン平均得点の降順リスト, ライン先頭得点の降順リスト)。単騎も1本と数える。"""
    L = lines_of(r)
    rp = r["rp"]
    means = sorted((float(np.mean([rp[c - 1] for c in v])) for v in L.values()), reverse=True)
    leads = sorted((float(rp[v[0] - 1]) for v in L.values()), reverse=True)
    return means, leads


def rp_gap12(r) -> float:
    m, _ = line_rp_stats(r)
    return m[0] - m[1] if len(m) >= 2 else 0.0


def lead_gap12(r) -> float:
    _, l = line_rp_stats(r)
    return l[0] - l[1] if len(l) >= 2 else 0.0


def n_nige(r) -> int:
    return int(sum(1 for c in range(7) if str(r["style"][c]) == "逃"))


def axis_same_line(r) -> bool:
    return str(r["lg"][r["a1"] - 1]) == str(r["lg"][r["a2"] - 1])


def axis_line_role(r) -> str:
    """軸1のラインが、規模と平均得点でどの位置か。"""
    L = lines_of(r)
    g = str(r["lg"][r["a1"] - 1])
    mine = L[g]
    biggest = max(len(v) for v in L.values())
    rp = r["rp"]
    mean = {k: float(np.mean([rp[c - 1] for c in v])) for k, v in L.items()}
    top = max(mean.values())
    big = len(mine) == biggest
    strong = mean[g] >= top - 1e-9
    if big and strong:
        return "最大かつ最強"
    if strong:
        return "最強だが最大でない"
    if big:
        return "最大だが最強でない"
    return "劣勢ライン"


def third_source(r) -> str:
    """3着目がどこから出たか。"""
    c = r["third_car"]
    if not c:
        return "—"
    lg = r["lg"]
    tgt = {str(lg[r["a1"] - 1]), str(lg[r["a2"] - 1])}
    if str(lg[c - 1]) in tgt:
        return "軸と同ライン"
    if float(r["lsize"][c - 1]) == 1.0:
        return "単騎"
    if float(r["llead"][c - 1]) == 1.0:
        return "別ラインの先頭"
    return "別ラインの番手"


# ───────────────────────── 選び方 ─────────────────────────

def p3_score(r):
    return r["p3vec"]


def line_score(r):
    """軸2車と同ラインなら1（同点は p3 で割る）。`rate_branch._line_score` と同一。"""
    lg = r["lg"]
    tgt = {str(lg[r["a1"] - 1]), str(lg[r["a2"] - 1])}
    return np.array([(1.0 if str(lg[c]) in tgt else 0.0) + 1e-6 * r["p3vec"][c]
                     for c in range(7)])


def top1(fn, rows) -> float:
    ok = 0
    for r in rows:
        s = fn(r)
        cand = [c for c in np.argsort(-s) + 1 if c not in (r["a1"], r["a2"])]
        ok += r["third_car"] == cand[0]
    return ok / len(rows) * 100 if rows else float("nan")


# ───────────────────────── §1 記述 ─────────────────────────

SRC = ("軸と同ライン", "別ラインの先頭", "別ラインの番手", "単騎")


def comp():
    W = wins(load())
    print("§1 軸2車がそろった回の「3着目の出どころ」を編成パターン別に\n")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        buck = defaultdict(list)
        for r in both:
            buck[comp_label(r)].append(r)
        lab = "確認 2026" if w == "confirm" else "探索 2024-07〜2025-12"
        print(f"--- {lab}  n={len(both):,} ---")
        print(f"{'編成':12s}{'n':>7s}" + "".join(f"{s:>14s}" for s in SRC))
        for k in sorted(buck, key=lambda x: -len(buck[x])):
            rs = buck[k]
            if len(rs) < 50:
                continue
            cnt = Counter(third_source(r) for r in rs)
            print(f"{k:12s}{len(rs):7d}" +
                  "".join(f"{cnt.get(s, 0) / len(rs) * 100:13.1f}%" for s in SRC))
        cnt = Counter(third_source(r) for r in both)
        print(f"{'全体':12s}{len(both):7d}" +
              "".join(f"{cnt.get(s, 0) / len(both) * 100:13.1f}%" for s in SRC))
        print()

    print("\n§1b 単騎の有無・強さ別\n")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        for keyfn, name in ((tanki_strength, "単騎の強さ"), (lambda r: f"逃げ型{n_nige(r)}人", "逃げ型の数")):
            buck = defaultdict(list)
            for r in both:
                buck[keyfn(r)].append(r)
            lab = "確認" if w == "confirm" else "探索"
            print(f"── {name}（{lab}）")
            print(f"{'':18s}{'n':>7s}" + "".join(f"{s:>14s}" for s in SRC))
            for k in sorted(buck):
                rs = buck[k]
                if len(rs) < 50:
                    continue
                cnt = Counter(third_source(r) for r in rs)
                print(f"{k:18s}{len(rs):7d}" +
                      "".join(f"{cnt.get(s, 0) / len(rs) * 100:13.1f}%" for s in SRC))
            print()


# ───────────────────────── §2 相互作用 ─────────────────────────

def _terciles(vals):
    v = sorted(vals)
    return v[len(v) // 3], v[2 * len(v) // 3]


CELLS = {}


def _init_cells(all_both):
    """三分位の境界は両窓をまとめた母集団から1回だけ引く（窓ごとに引くと比較できない）。"""
    g1 = _terciles([rp_gap12(r) for r in all_both])
    g2 = _terciles([lead_gap12(r) for r in all_both])

    def _q(v, b):
        return "小" if v < b[0] else ("中" if v < b[1] else "大")

    CELLS.update({
        "編成パターン": comp_label,
        "単騎の数": tanki_label,
        "単騎の強さ": tanki_strength,
        "ライン平均得点 1位-2位差": lambda r: _q(rp_gap12(r), g1),
        "ライン先頭の得点 1位-2位差": lambda r: _q(lead_gap12(r), g2),
        "逃げ型の数": lambda r: f"逃げ型{min(n_nige(r), 3)}人" + ("+" if n_nige(r) >= 3 else ""),
        "軸2車が同ライン": lambda r: "同ライン" if axis_same_line(r) else "別ライン",
        "軸のラインの位置": axis_line_role,
        "ライン本数": lambda r: f"{int(r['nlines'])}本",
    })


def inter():
    W = wins(load())
    both = {w: [r for r in W[w] if r["both_in3"] and r["third_car"]] for w in W}
    _init_cells(both["confirm"] + both["explore"])
    print("§2 「同ライン優先 − モデル p3」の優位が編成でどう変わるか\n"
          "   値は『3着目を相手1車目で当てる率』。Δ>0 ＝ 同ライン優先が勝つ\n")
    keep = []
    for name, fn in CELLS.items():
        buck = {w: defaultdict(list) for w in both}
        for w, rs in both.items():
            for r in rs:
                buck[w][fn(r)].append(r)
        vals = sorted(set(buck["confirm"]) | set(buck["explore"]))
        print(f"── {name}")
        print(f"{'セル':22s}{'n確認':>7s}{'p3':>8s}{'ライン':>8s}{'Δ':>8s}"
              f"{'n探索':>8s}{'p3':>8s}{'ライン':>8s}{'Δ':>8s}  判定")
        for v in vals:
            rc, re = buck["confirm"][v], buck["explore"][v]
            if len(rc) < 200 or len(re) < 200:
                continue
            pc, lc = top1(p3_score, rc), top1(line_score, rc)
            pe, le = top1(p3_score, re), top1(line_score, re)
            dc, de = lc - pc, le - pe
            ok = "両窓+" if (dc > 0 and de > 0) else ("両窓−" if (dc < 0 and de < 0) else "反転")
            if ok == "両窓+":
                keep.append((name, v, dc, de))
            print(f"{str(v):22s}{len(rc):7d}{pc:7.1f}%{lc:7.1f}%{dc:+7.2f}pt"
                  f"{len(re):8d}{pe:7.1f}%{le:7.1f}%{de:+7.2f}pt  {ok}")
        print()
    print("── 両窓でライン優先が勝つセル（n>=200 のみ）")
    for name, v, dc, de in sorted(keep, key=lambda x: -min(x[2], x[3])):
        print(f"  {name:24s}{str(v):18s} 確認{dc:+6.2f}pt 探索{de:+6.2f}pt")


# ───────────────────────── §3 商品化 ─────────────────────────

def _shape_of(r):
    from src.type_lab import RaceShape
    order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
    return RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                     False, order, float(r["pw_ent"]))


def _targets(r, rng=None):
    """軸2車と同ラインの相手（軸自身は除く）。rng を渡すと同数を無作為に選ぶ。"""
    lg = r["lg"]
    tgt = {str(lg[r["a1"] - 1]), str(lg[r["a2"] - 1])}
    real = [c for c in range(1, 8) if c not in (r["a1"], r["a2"]) and str(lg[c - 1]) in tgt]
    if rng is None:
        return set(real)
    pool = [c for c in range(1, 8) if c not in (r["a1"], r["a2"])]
    return set(rng.sample(pool, min(len(real), len(pool))))


def _run(rows, beta, cellfn=None, rng=None, oracle=False):
    """cellfn(r)=True のレースだけ β を掛ける。返り値は行ごとの (bet, pay)。

    oracle=True は**実際の3着目**へ β を掛ける（相手選定を完全に当てた上限の測定用）。
    """
    from src.type_lab import PLANS, allocate, build_legs, mean_expected_payout
    MINM, MINP = 20_000, 2.0
    out = []
    for r in rows:
        plan = PLANS[r["plan"]]
        sh = _shape_of(r)
        pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
        if beta != 1.0 and (cellfn is None or cellfn(r)):
            T = {r["third_car"]} if oracle else _targets(r, rng)
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
        out.append((r["race_key"], b, p))
    return out


def _stats(recs):
    n = len(recs)
    if not n:
        return (0, 0.0, 0.0)
    shown = sum(1 for _, b, p in recs if p >= b)
    inv = sum(b for _, b, _ in recs)
    pay = sum(p for _, _, p in recs)
    return (n, shown / n * 100, pay / inv * 100 if inv else 0.0)


def _paired_ci(base, arm, n_boot=2000, seed=7):
    """同一レース対比較の Δ表示的中 95%CI（レース単位 bootstrap）。"""
    A = {k: (p >= b) for k, b, p in base}
    B = {k: (p >= b) for k, b, p in arm}
    keys = [k for k in A if k in B]
    if not keys:
        return (float("nan"),) * 3
    a = np.array([A[k] for k in keys], float)
    b = np.array([B[k] for k in keys], float)
    d = (b - a) * 100
    rng = np.random.default_rng(seed)
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)]
    return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


#: §2 で「両窓でライン優先が勝つ」と出たセル。`product` はここだけ β を掛ける。
ARMS = {}


def _build_arms():
    _init_cells([r for r in load() if r["both_in3"] and r["third_car"]])
    ARMS.update({
        "全件（一律・前段の再掲）": lambda r: True,
        "軸2車が別ライン": lambda r: not axis_same_line(r),
        "単騎あり": lambda r: n_tanki(r) >= 1,
        "単騎なし": lambda r: n_tanki(r) == 0,
        "ライン3本": lambda r: int(r["nlines"]) == 3,
        "軸のラインが劣勢": lambda r: axis_line_role(r) == "劣勢ライン",
        "軸のラインが最大かつ最強": lambda r: axis_line_role(r) == "最大かつ最強",
        "逃げ型2人以下": lambda r: n_nige(r) <= 2,
        "編成 3-2-2": lambda r: comp_label(r) == "3-2-2",
        "編成 3-3-1": lambda r: comp_label(r) == "3-3-1",
    })


def product():
    W = wins(load())
    _build_arms()
    print("§3 `E_hit`/`F_hit` の選抜確率に、条件つきの同ラインボーナス β を掛ける\n"
          "   点数・帯・配分・ゲートは現行のまま。Δ は同一レース対比較の 95%CI\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        base = _run(rows, 1.0)
        bs = _stats(base)
        lab = "確認 2026" if w == "confirm" else "探索 2024-07〜2025-12"
        print(f"--- {lab}  対象 {len(rows):,}商品 ---")
        print(f"{'腕':26s}{'掛かる%':>8s}{'件数':>7s}{'表示的中':>10s}{'ROI':>8s}"
              f"{'Δ表示的中 [95%CI]':>26s}")
        print(f"{'現行':26s}{'':>8s}{bs[0]:7d}{bs[1]:9.2f}%{bs[2]:7.1f}%")
        for name, fn in ARMS.items():
            frac = sum(1 for r in rows if fn(r)) / len(rows) * 100
            for beta in (1.5, 2.5):
                arm = _run(rows, beta, fn)
                st = _stats(arm)
                d, lo, hi = _paired_ci(base, arm)
                print(f"{name + f' β={beta}':26s}{frac:7.1f}%{st[0]:7d}{st[1]:9.2f}%"
                      f"{st[2]:7.1f}%{d:+12.2f}pt [{lo:+.2f},{hi:+.2f}]")
        print()


def ctrl():
    """効いた腕にだけ無作為対照（同じレース・同じ数の車へ β）を置く。"""
    W = wins(load())
    _build_arms()
    target = sys.argv[2] if len(sys.argv) > 2 else "軸2車が別ライン"
    beta = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5
    fn = ARMS[target]
    print(f"§4 無作為対照 20seed  腕『{target}』 β={beta}\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        base = _stats(_run(rows, 1.0))
        arm = _stats(_run(rows, beta, fn))
        ctl = [_stats(_run(rows, beta, fn, random.Random(1000 + s)))[1] for s in range(20)]
        winr = sum(1 for c in ctl if arm[1] > c)
        print(f"{w}: 現行 {base[1]:.2f}%  腕 {arm[1]:.2f}%  "
              f"対照 中央 {np.median(ctl):.2f}% [{min(ctl):.2f},{max(ctl):.2f}]  "
              f"勝ち {winr}/20")


# ───────────────────────── §5 効かせ方の強さは編成で変わるか ─────────────────────────

def _beta_score(beta):
    """車ごとの素点 p3 に、軸と同ラインなら β を掛ける（β=1 が現行・∞ が同ライン優先）。"""
    def fn(r):
        lg = r["lg"]
        tgt = {str(lg[r["a1"] - 1]), str(lg[r["a2"] - 1])}
        return np.array([r["p3vec"][c] * (beta if str(lg[c]) in tgt else 1.0)
                         for c in range(7)])
    return fn


BETAS = (1.0, 1.2, 1.5, 2.0, 3.0, 5.0)


def beta():
    """セルごとの『最良 β』がばらつくなら条件分岐の余地がある。揃うなら無い。"""
    W = wins(load())
    both = {w: [r for r in W[w] if r["both_in3"] and r["third_car"]] for w in W}
    _init_cells(both["confirm"] + both["explore"])
    print("§5 車ごとの同ラインボーナス β を掃引したときの『3着目 top1 率』\n"
          "   β=1 が現行（モデル p3 そのもの）。セルごとの最良 β が揃うなら条件分岐の余地は無い\n")
    for name in ("編成パターン", "逃げ型の数", "単騎の数", "軸2車が同ライン", "ライン本数"):
        fn = CELLS[name]
        buck = {w: defaultdict(list) for w in both}
        for w, rs in both.items():
            for r in rs:
                buck[w][fn(r)].append(r)
        vals = sorted(set(buck["confirm"]) | set(buck["explore"]))
        print(f"── {name}")
        print(f"{'セル':16s}{'窓':>6s}{'n':>7s}" +
              "".join(f"{f'β={b}':>9s}" for b in BETAS) + "   最良β  同ライン優先")
        for v in vals:
            for w in ("confirm", "explore"):
                rs = buck[w][v]
                if len(rs) < 200:
                    continue
                sc = [top1(_beta_score(b), rs) for b in BETAS]
                hard = top1(line_score, rs)
                best = BETAS[int(np.argmax(sc))]
                print(f"{str(v):16s}{('確認' if w == 'confirm' else '探索'):>6s}{len(rs):7d}" +
                      "".join(f"{x:8.1f}%" for x in sc) + f"{best:8.1f}{hard:12.1f}%")
        print()


# ───────────────────────── §6 この梃子の天井 ─────────────────────────

def ceiling():
    """相手選定を**完全に当てた**ときの `E_hit`/`F_hit`（＝この梃子の絶対上限）。"""
    W = wins(load())
    print("§6 相手選定の天井（3着目を必ず1車目に置けたとしたら）\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        n = len(rows)
        a = [sum(1 for r in rows if r["hit"]),
             sum(1 for r in rows if not r["hit"] and r["set_hit"]),
             sum(1 for r in rows if not r["hit"] and not r["set_hit"] and r["both_in3"]),
             sum(1 for r in rows if not r["hit"] and not r["set_hit"] and not r["both_in3"])]
        lab = "確認 2026" if w == "confirm" else "探索"
        print(f"--- {lab}  n={n:,} ---")
        for l, x in zip(("① 的中", "② 順序違い", "③ 相手外し", "④ 軸崩壊"), a):
            print(f"  {l:12s}{x:6d}{x / n * 100:8.2f}%")
        base = _stats(_run(rows, 1.0))
        orc = _stats(_run(rows, 1e6, cellfn=lambda r: bool(r["both_in3"] and r["third_car"]),
                          rng=None, oracle=True))
        d, lo, hi = _paired_ci(_run(rows, 1.0),
                               _run(rows, 1e6,
                                    cellfn=lambda r: bool(r["both_in3"] and r["third_car"]),
                                    oracle=True))
        print(f"  現行        件数{base[0]:6d}  表示的中 {base[1]:6.2f}%  ROI {base[2]:6.1f}%")
        print(f"  相手オラクル  件数{orc[0]:6d}  表示的中 {orc[1]:6.2f}%  ROI {orc[2]:6.1f}%"
              f"   Δ {d:+.2f}pt [{lo:+.2f},{hi:+.2f}]\n")


def overall_beta():
    """全体（セル分けなし）の top1 率。前段 doc の 44.68/42.30 と並べる。"""
    W = wins(load())
    print("§7 全体の『3着目 top1 率』（前段 doc の一律ライン優先と比べる）\n")
    print(f"{'選び方':24s}{'確認':>9s}{'探索':>9s}")
    both = {w: [r for r in W[w] if r["both_in3"] and r["third_car"]] for w in W}
    arms = [("モデル p3（現行）", p3_score), ("同ライン優先（硬）", line_score)]
    arms += [(f"p3 × β={b}（軟）", _beta_score(b)) for b in BETAS[1:]]
    for name, fn in arms:
        print(f"{name:24s}{top1(fn, both['confirm']):8.2f}%{top1(fn, both['explore']):8.2f}%")
    print(f"{'n':24s}{len(both['confirm']):8,d} {len(both['explore']):8,d}")


if __name__ == "__main__":
    {"comp": comp, "inter": inter, "product": product, "ctrl": ctrl,
     "beta": beta, "ceiling": ceiling, "overall": overall_beta}[sys.argv[1]]()
