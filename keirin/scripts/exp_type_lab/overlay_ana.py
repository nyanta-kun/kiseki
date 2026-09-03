#!/usr/bin/env python3
"""「現行の帯の上に、1日数レースだけ多穴狙いを**重ねる**」を測る（2026-09-04・ユーザー提案）。

## 発端

> 100倍を超える配当の的中が無い。100倍を超えるようなレースは、現在の指数上位を軸に
> 取る買い目だと外れている。これらの発生する可能性があるレースに対し、現在の帯の上に
> 1日数レース多穴狙いと下帯を重ねる戦略が取れないか。

`axis_bust_conditional_2026_09_03.md` は**差し替え**（現行を捨てて軸外しへ替える）だけを
測った。ここで測るのは**重ね**——現行の商品を残したまま、上の帯を足す:

  重ね(別商品) … 選んだレースに**2つ目の商品**を出す。投資は 1R 20,000円になる
  バーベル     … 1商品 10,000円の**中で**下帯 w 円 + 上帯 (10,000-w) 円に割る（投資据え置き）
  差し替え     … 参考（既知の結論）

🔴 対照を必ず置く（同数を無作為に選ぶ 20 seed）。件数を増やす操作でも
   「選び方に情報があるか」は対照でしか分からない。
🔴 確認窓(2026)が本番相当（`odds_tf_n7` train_end 2025-12-31）。
🔴 ROI 単独で決めない。見るのは 件/日・投資/日・表示的中・10万+/日。
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import (ctx, _run_named, _plan_for, AXIS_GATE_MIN,  # noqa: E402
                            MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import (PLANS, Plan, SIGNBOARD_RACE_TYPES,  # noqa: E402
                          SIGNBOARD_MAX_ODDS, SIGNBOARD_TARGET,
                          allocate, build_legs, mean_expected_payout)  # noqa: F401

BUDGET = 10_000


# ───────────────────────── 上帯（多穴）の買い目 ─────────────────────────

def sign_legs(x, budget: int, target: int, max_odds: float = SIGNBOARD_MAX_ODDS,
              min_odds: float = 0.0):
    """看板枠と同じ組み方。**予算を引数に取る**（本番 `build_legs` は BUDGET 固定）。

    Σ(1/予測オッズ) <= 予算/計画払戻 の枠へ確率降順に詰める。
    """
    cap = float(budget) / float(target)
    cand = [k for k, v in x.po_tf.items()
            if v and float(v) > 0 and len(set(k)) == 3
            and float(v) >= min_odds and (not max_odds or float(v) <= max_odds)]
    cand.sort(key=lambda k: -float(x.pr_tf.get(k, 0.0)))
    out, s = [], 0.0
    for k in cand:
        o = float(x.po_tf[k])
        if s + 1.0 / o > cap:
            continue
        out.append(tuple(k))
        s += 1.0 / o
    return out or None


def bust_legs(x, k: int):
    """軸1（p3 1位）を外した6車から確率上位 k 点（`A_ana` と同じ組み方）。"""
    return build_legs(x.shape, Plan("x", "?", "trifecta", "bust_top", 0,
                                    max_legs=k, alloc="dutch"), x.po_tf, x.pr_tf)


def band_legs2(x, lo: float, hi: float, k: int):
    """予測オッズ [lo, hi] から確率上位 k 点。上限は帯ROIが崩れる超高配当を買わないため。"""
    cand = [tuple(c) for c, v in x.po_tf.items()
            if v and lo <= float(v) <= hi and len(set(c)) == 3]
    cand.sort(key=lambda c: -float(x.pr_tf.get(c, 0.0)))
    out = cand[:k]
    return out or None


def band_legs(x, lo: float, k: int):
    """予測オッズ lo 以上から確率上位 k 点。"""
    return build_legs(x.shape, Plan("x", "?", "trifecta", "prob_top", 0,
                                    min_odds=lo, max_legs=k, alloc="dutch"),
                      x.po_tf, x.pr_tf)


#: 上帯の候補。budget を受けて legs を返す。
UPSET = {
    "看板15万(上限600)": lambda x, b: sign_legs(x, b, 150_000),
    "帯100-600倍8点": lambda x, b: band_legs2(x, 100.0, 600.0, 8),
    "帯60-600倍8点":  lambda x, b: band_legs2(x, 60.0, 600.0, 8),
    "帯100-600倍4点": lambda x, b: band_legs2(x, 100.0, 600.0, 4),
    "帯100-600倍12点": lambda x, b: band_legs2(x, 100.0, 600.0, 12),
    "看板15万":   lambda x, b: sign_legs(x, b, 150_000),
    "看板30万":   lambda x, b: sign_legs(x, b, 300_000),
    "看板8万":    lambda x, b: sign_legs(x, b, 80_000),
    "軸外し5点":  lambda x, b: bust_legs(x, 5),
    "軸外し12点": lambda x, b: bust_legs(x, 12),
    "帯50倍8点":  lambda x, b: band_legs(x, 50.0, 8),
    "帯100倍8点": lambda x, b: band_legs(x, 100.0, 8),
}

_DUTCH = Plan("x", "?", "trifecta", "prob_top", 0, alloc="dutch")


def _rec(x, stakes: dict) -> dict:
    inv = float(sum(stakes.values()))
    pay = float(stakes[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in stakes else 0.0
    return dict(date=x.date, inv=inv, pay=pay, k=len(stakes),
                mean=mean_expected_payout(stakes, x.po_tf),
                odds=x.pay_tf)          # 三連単の確定オッズ（PAY/100 は ctx で済）


def upset_product(x, name: str, budget: int = BUDGET, gate: bool = True):
    """上帯を単体の商品として組む（入稿ゲートを通す）。"""
    legs = UPSET[name](x, budget)
    if not legs:
        return None
    st = allocate(legs, x.po_tf, x.pr_tf, _DUTCH, budget=budget)
    if not st:
        return None
    if gate:
        if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
            return None
        if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
            return None
    return _rec(x, st)


def barbell(x, cur_key: str, name: str, w_low: int):
    """1商品の中で 下帯 w_low 円 + 上帯 (10,000-w_low) 円。

    下帯は**現行プランの買い目そのまま**（予算だけ縮める）。重複した目は賭け金を足す。
    """
    plan = PLANS[cur_key]
    if plan.bet_type != "trifecta":
        return None                      # 三連複プラン（D_hit/A_trio）は帯を重ねられない
    low_legs = build_legs(x.shape, plan, x.po_tf, x.pr_tf)
    if not low_legs:
        return None
    lo = allocate(low_legs, x.po_tf, x.pr_tf, plan, budget=w_low)
    if not lo:
        return None
    hi_legs = UPSET[name](x, BUDGET - w_low)
    if not hi_legs:
        return None
    hi = allocate(hi_legs, x.po_tf, x.pr_tf, _DUTCH, budget=BUDGET - w_low)
    if not hi:
        return None
    st: dict = dict(lo)
    for c, v in hi.items():
        st[c] = st.get(c, 0) + v
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    return _rec(x, st)


# ───────────────────────── 集計 ─────────────────────────

def agg(recs: list[dict], nd: int) -> dict:
    s = C.summarize(recs, nd)
    if not s.get("n"):
        return s
    pays = [r["pay"] for r in recs if r["pay"] > 0]
    s["inv_per_day"] = sum(r["inv"] for r in recs) / nd
    s["big30"] = sum(1 for p in pays if p >= 300_000) / nd
    s["o100"] = sum(1 for r in recs if r["pay"] > 0 and r.get("odds", 0) >= 100) / nd
    return s


HDR = (f"  {'腕':30s} {'件/日':>6s} {'投資/日':>8s} {'表示的中%':>9s} {'ROI%':>7s} "
       f"{'払戻中央':>9s} {'10万+/日':>8s} {'30万+/日':>8s} {'100倍的中/日':>11s}")


def show(name: str, s: dict) -> None:
    if not s.get("n"):
        print(f"  {name:30s}  (該当なし)")
        return
    print(f"  {name:30s} {s['perday']:6.2f} {s['inv_per_day']:8,.0f} {s['shown']:8.2f}% "
          f"{s['roi']:7.1f} {s['med_pay']:9,.0f} {s['big_per_day']:8.3f} "
          f"{s['big30']:8.3f} {s['o100']:11.3f}")


def build(win: str, sign_rt=None):
    """レースごとに 現行 / 上帯単体 / バーベル を1回だけ作る。

    sign_rt: 看板枠を置く種別。None なら本番の `SIGNBOARD_RACE_TYPES`、
             () なら看板枠なし（型F は全部 `F_hit`／決勝は `F_pay`）。
    """
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = z["AXIS_SUM"].astype(float)
    if sign_rt is None:
        sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    for i in [int(v) for v in C.select(None, win) if tp[int(v)] in "ABCDEF"]:
        x = ctx(i)
        if x is None:
            continue
        t = tp[i]
        trio_ok = (_run_named(x, "A_trio") is not None) if t == "A" else False
        key = _plan_for(t, rt[i], x.shape.pw_ent, trio_ok, sign_rt)
        cur = None if axs[i] < AXIS_GATE_MIN.get(key, 0.0) else _run_named(x, key)
        if cur is not None:
            # 的中した目のオッズ（三連単は PAY/100・三連複は TRIO_PAY）。
            cur["odds"] = (x.odds_t3 if PLANS[key].bet_type == "trio"
                           else x.pay_tf)
        rows.append(dict(i=i, date=x.date, pw=float(x.shape.pw_ent), type=t,
                         cur=cur, cur_key=key, x=x))
    return rows


def pick_topn(rows, n_per_day: int, exclude_upset: bool = True):
    """1日あたり pw_ent 上位 n レース（現行が既に穴狙いのレースは除く）。"""
    by = defaultdict(list)
    for j, r in enumerate(rows):
        if r["cur"] is None:
            continue
        if exclude_upset and r["cur_key"] in ("F_sign", "A_ana"):
            continue
        by[r["date"]].append(j)
    sel = set()
    for d, js in by.items():
        js.sort(key=lambda j: -rows[j]["pw"])
        sel.update(js[:n_per_day])
    return sel


def pick_random(rows, size: int, seed: int, exclude_upset: bool = True):
    cand = [j for j, r in enumerate(rows)
            if r["cur"] is not None and
            (not exclude_upset or r["cur_key"] not in ("F_sign", "A_ana"))]
    rng = random.Random(seed * 977 + 13)
    return set(rng.sample(cand, min(size, len(cand))))


# ═══════════════════════════ Phase 1 — 上帯そのものの素性 ═══════════════════════════

def phase1() -> None:
    """上帯候補を**単体商品**として（10,000円・入稿ゲート後）比べる。

    ここで見るのは「1日数レースだけ足すなら、どの形が 10万+ を作れるか」。
    """
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        print("\n" + "=" * 128)
        print(f"███ Phase1 上帯単体  {label}   n={len(rows):,}R / {nd}日")
        print(HDR)
        show("（参考）現行ラインナップ",
             agg([r["cur"] for r in rows if r["cur"]], nd))
        for name in UPSET:
            recs = [upset_product(r["x"], name) for r in rows]
            show(f"{name}（全レース）", agg([v for v in recs if v], nd))
        # pw_ent 上位10%だけに絞ったとき
        pw = np.array([r["pw"] for r in rows])
        thr = float(np.quantile(pw, 0.90))
        print(f"  ── pw_ent 上位10%（thr={thr:.4f}）だけに置いた場合 ──")
        for name in UPSET:
            recs = [upset_product(r["x"], name) for r in rows if r["pw"] >= thr]
            show(f"{name}", agg([v for v in recs if v], nd))


# ═══════════════════════════ Phase 2 — 重ね vs バーベル vs 差し替え ═══════════════

def phase2(upset_name: str = "看板15万", ns=(2, 3, 5)) -> None:
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        up = {j: upset_product(r["x"], upset_name) for j, r in enumerate(rows)}
        bar = {}
        for w in (7000, 5000):
            bar[w] = {j: barbell(r["x"], r["cur_key"], upset_name, w)
                      for j, r in enumerate(rows)}
        base = [r["cur"] for r in rows if r["cur"]]
        print("\n" + "=" * 128)
        print(f"███ Phase2 上帯={upset_name}  {label}   n={len(rows):,}R / {nd}日")
        print(HDR)
        show("現行", agg(base, nd))
        for n in ns:
            sel = pick_topn(rows, n)
            got = [j for j in sel if up[j]]
            print(f"  ── pw_ent 上位{n}R/日（{len(sel)}R・上帯が組めたのは {len(got)}R）──")
            # ① 重ね（別商品・投資+）
            recs = list(base) + [up[j] for j in got]
            show(f"① 重ね 別商品 {n}R/日", agg(recs, nd))
            show("    └ 足したぶんだけ", agg([up[j] for j in got], nd))
            # ② バーベル（投資据え置き）
            for w in (7000, 5000):
                recs = []
                nb = 0
                for j, r in enumerate(rows):
                    if j in sel and bar[w][j]:
                        recs.append(bar[w][j]); nb += 1
                    elif r["cur"]:
                        recs.append(r["cur"])
                show(f"② バーベル 下{w//1000}:上{(10000-w)//1000} {n}R/日 ({nb}R)",
                     agg(recs, nd))
            # ③ 差し替え（参考）
            recs = [(up[j] if j in sel and up[j] else r["cur"])
                    for j, r in enumerate(rows) if (r["cur"] or (j in sel and up[j]))]
            show(f"③ 差し替え {n}R/日", agg(recs, nd))
            # 対照: 同数を無作為に選んで重ねる
            ms, mr, mb, mo = [], [], [], []
            for seed in range(20):
                s2 = pick_random(rows, len(sel), seed)
                g2 = [j for j in s2 if up[j]]
                a = agg(list(base) + [up[j] for j in g2], nd)
                ms.append(a["shown"]); mr.append(a["roi"])
                mb.append(a["big_per_day"]); mo.append(a["o100"])
            a1 = agg(list(base) + [up[j] for j in got], nd)
            print(f"      └ 無作為に同数重ねる20本 中央 表示的中 {np.median(ms):5.2f}% /"
                  f" ROI {np.median(mr):5.1f} / 10万+ {np.median(mb):.3f} /"
                  f" 100倍的中 {np.median(mo):.3f}"
                  f"  →  検出が勝ち 表示的中 {sum(a1['shown']>v for v in ms)}/20・"
                  f"10万+ {sum(a1['big_per_day']>v for v in mb)}/20・"
                  f"100倍 {sum(a1['o100']>v for v in mo)}/20")




# ═══════════════════════════ Phase 3 — バーベルの掃引 ═══════════════════════════
#
# Phase2 で **バーベル（1商品の中で下帯＋上帯に割る）** が
#   ・投資据え置き ・件数据え置き ・ROI 据え置き
# のまま 10万+ を増やし、表示的中の傷が「重ね」「差し替え」より浅いと分かった。
# ここで ①どのレースに何レース置くか ②割合 ③上帯の形 を掃く。
#
# 🔴 **無作為対照をバーベルにも置く**（Phase2 の対照は「重ね」にしか掛けていない）。

SPLITS = (8000, 7000, 6000, 5000, 4000)


def phase3(upsets=("看板15万", "看板30万", "帯100倍8点"),
           ns=(3, 5, 10, 20, 0)) -> None:
    """ns の 0 は「全レース（上帯を組めた全部）」。"""
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        base = [r["cur"] for r in rows if r["cur"]]
        b0 = agg(base, nd)
        print("\n" + "=" * 128)
        print(f"███ Phase3 バーベル掃引  {label}   n={len(rows):,}R / {nd}日")
        print(HDR)
        show("現行", b0)
        for uname in upsets:
            bar = {w: {j: barbell(r["x"], r["cur_key"], uname, w)
                       for j, r in enumerate(rows) if r["cur"]}
                   for w in SPLITS}
            for n in ns:
                sel = (set(j for j, r in enumerate(rows) if r["cur"])
                       if n == 0 else pick_topn(rows, n))
                tag = "全レース" if n == 0 else f"上位{n}R/日"
                print(f"  ── 上帯={uname}  {tag}（{len(sel)}R）──")
                for w in SPLITS:
                    recs, sw, a_p, b_p = [], 0, [], []
                    for j, r in enumerate(rows):
                        if not r["cur"]:
                            continue
                        b = bar[w].get(j)
                        if j in sel and b:
                            recs.append(b); sw += 1
                            a_p.append(r["cur"]); b_p.append(b)
                        else:
                            recs.append(r["cur"])
                    s = agg(recs, nd)
                    show(f"  下{w//1000}:上{(10000-w)//1000}（{sw}R 適用）", s)
                    if n and sw:
                        ms, mb = [], []
                        for seed in range(20):
                            s2 = pick_random(rows, len(sel), seed)
                            rc = [(bar[w][j] if (j in s2 and bar[w].get(j)) else r["cur"])
                                  for j, r in enumerate(rows) if r["cur"]]
                            a = agg(rc, nd)
                            ms.append(a["shown"]); mb.append(a["big_per_day"])
                        print(f"        └ 無作為同数20本 中央 表示的中 {np.median(ms):5.2f}%"
                              f" / 10万+ {np.median(mb):.3f}  → 検出が勝ち "
                              f"表示的中 {sum(s['shown']>v for v in ms)}/20・"
                              f"10万+ {sum(s['big_per_day']>v for v in mb)}/20")


# ═══════════════════════ Phase 4 — 全商品に薄く重ねる（本命） ═══════════════════════
#
# Phase3 の掃引で、**上帯を 1日数レースではなく全商品に薄く重ねる**のが最も効率が
# 良いと分かった（表示的中の傷 1pt あたりの 10万+ が、差し替え・重ね・看板枠の
# 種別拡大の 4〜10倍）。ここで形と厚みを詰め、対応のあるブートストラップで検定する。

def boot(a: list, b: list, nb: int = 1000, seed: int = 7):
    """同一レースの対応のあるブートストラップ。(Δ表示的中CI, ΔROI CI)。

    🔴 numpy で回す。素の python で 2万件 × 2000本は 10分以上かかる。
    """
    inv_a = np.array([r["inv"] for r in a], float)
    pay_a = np.array([r["pay"] for r in a], float)
    inv_b = np.array([r["inv"] for r in b], float)
    pay_b = np.array([r["pay"] for r in b], float)
    sh_a = (pay_a > inv_a).astype(float)
    sh_b = (pay_b > inv_b).astype(float)
    rng = np.random.default_rng(seed)
    n = len(a)
    ds, dr = np.empty(nb), np.empty(nb)
    for t in range(nb):
        j = rng.integers(0, n, size=n)
        ds[t] = sh_b[j].mean() * 100 - sh_a[j].mean() * 100
        dr[t] = (pay_b[j].sum() / inv_b[j].sum() - pay_a[j].sum() / inv_a[j].sum()) * 100
    qf = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return qf(ds), qf(dr)


HDR4 = (f"  {'腕':26s} {'点数':>5s} {'的中%':>6s} {'ガミ%':>6s} {'表示的中%':>9s} {'ROI%':>7s} "
        f"{'払戻中央':>9s} {'10万+/日':>8s} {'30万+/日':>8s} {'100倍的中/日':>11s}")


def show4(name: str, s: dict) -> None:
    if not s.get("n"):
        print(f"  {name:26s}  (該当なし)")
        return
    print(f"  {name:26s} {s['k']:5.1f} {s['hit']:6.2f} {s['gami']:6.2f} {s['shown']:8.2f}% "
          f"{s['roi']:7.1f} {s['med_pay']:9,.0f} {s['big_per_day']:8.3f} "
          f"{s['big30']:8.3f} {s['o100']:11.3f}")


FORMS = ("看板15万", "帯100-600倍8点", "帯60-600倍8点", "帯100-600倍4点", "帯100-600倍12点")


def phase4(slices=(1000, 2000, 3000)) -> None:
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        cur = [r["cur"] for r in rows]
        print("\n" + "=" * 132)
        print(f"███ Phase4 全商品に薄く重ねる  {label}   n={len(rows):,}R / {nd}日")
        print(HDR4)
        show4("現行", agg([v for v in cur if v], nd))
        for name in FORMS:
            for up in slices:
                w = BUDGET - up
                arm = [(barbell(r["x"], r["cur_key"], name, w) if r["cur"] else None)
                       for r in rows]
                arm = [(b if b else c) for b, c in zip(arm, cur)]
                s = agg([v for v in arm if v], nd)
                show4(f"{name} 上{up//1000}割", s)
                a_p = [c for c, b in zip(cur, arm) if c and b]
                b_p = [b for c, b in zip(cur, arm) if c and b]
                ci_s, ci_r = boot(a_p, b_p)
                print(f"      └ Δ表示的中 [{ci_s[0]:+.2f},{ci_s[1]:+.2f}]pt  "
                      f"ΔROI [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]pt  "
                      f"（上帯が組めた {sum(1 for c,b in zip(cur,arm) if c and b is not c)}R）")


def phase5(name: str = "帯100-600倍8点", up: int = 2000) -> None:
    """採用候補をプラン別に割る（下帯の払戻余裕が薄いプランで傷が深くないか）。"""
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        print("\n" + "=" * 132)
        print(f"███ Phase5 プラン別  上帯={name} 上{up//1000}割  {label}")
        by = defaultdict(lambda: ([], []))
        for r in rows:
            if not r["cur"]:
                continue
            b = barbell(r["x"], r["cur_key"], name, BUDGET - up)
            by[r["cur_key"]][0].append(r["cur"])
            by[r["cur_key"]][1].append(b or r["cur"])
        print(f"  {'プラン':8s} {'n':>5s} {'現行 表示的中':>12s} {'重ね 表示的中':>12s} "
              f"{'Δpt':>7s} {'現行ROI':>8s} {'重ねROI':>8s} {'現行10万+':>9s} {'重ね10万+':>9s}")
        for k, (a, b) in sorted(by.items()):
            sa, sb = agg(a, nd), agg(b, nd)
            print(f"  {k:8s} {sa['n']:5d} {sa['shown']:11.2f}% {sb['shown']:11.2f}% "
                  f"{sb['shown']-sa['shown']:+7.2f} {sa['roi']:8.1f} {sb['roi']:8.1f} "
                  f"{sa['big_per_day']:9.3f} {sb['big_per_day']:9.3f}")


# ═══════════════ Phase 6 — 帯が効いているのか、点を足しただけなのかを分ける ═══════════════
#
# 🔴 **対照が要る。** 「上帯を重ねると表示的中が上がる」は、単に買い目を増やした
#    だけかもしれない。薄いスライスで払戻 > 投資 を作れるのは**高オッズの目だけ**
#    （1,000円を8点へダッチで置くと払戻 = 1000/Σ(1/o)。平均40倍なら 5,000円＝ガミ、
#    平均200倍なら 25,000円＝表示的中）というのが仮説なので、
#    **帯なしの次点8点**と**中位帯(30-100倍)**を並べて確かめる。

def next_prob_legs(x, base_legs, k: int):
    """買っていない目のうち確率上位 k 点（帯を切らない対照）。"""
    have = set(base_legs)
    cand = [tuple(c) for c in x.po_tf if tuple(c) not in have and len(set(c)) == 3]
    cand.sort(key=lambda c: -float(x.pr_tf.get(c, 0.0)))
    return cand[:k] or None


def combo_product(x, cur_key: str, parts: list[tuple[str, int]], base_budget: int):
    """下帯 base_budget 円 ＋ parts=[(上帯名, 円), ...] を1商品にまとめる。"""
    plan = PLANS[cur_key]
    if plan.bet_type != "trifecta":
        return None
    low_legs = build_legs(x.shape, plan, x.po_tf, x.pr_tf)
    if not low_legs:
        return None
    st = allocate(low_legs, x.po_tf, x.pr_tf, plan, budget=base_budget)
    if not st:
        return None
    st = dict(st)
    for name, b in parts:
        legs = (next_prob_legs(x, low_legs, 8) if name == "次点8点(帯なし)"
                else UPSET[name](x, b))
        if not legs:
            return None
        hi = allocate(legs, x.po_tf, x.pr_tf, _DUTCH, budget=b)
        if not hi:
            return None
        for c, v in hi.items():
            st[c] = st.get(c, 0) + v
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    return _rec(x, st)


ARMS6: dict[str, tuple[int, list[tuple[str, int]]]] = {
    "看板15万 1割":                 (9000, [("看板15万", 1000)]),
    "帯100-600倍8点 1割":           (9000, [("帯100-600倍8点", 1000)]),
    "帯30-100倍8点 1割 (対照)":      (9000, [("帯30-100倍8点", 1000)]),
    "次点8点(帯なし) 1割 (対照)":     (9000, [("次点8点(帯なし)", 1000)]),
    "帯100-600倍8点1割+看板15万1割":  (8000, [("帯100-600倍8点", 1000), ("看板15万", 1000)]),
    "帯100-600倍8点2割+看板15万1割":  (7000, [("帯100-600倍8点", 2000), ("看板15万", 1000)]),
}


def phase6() -> None:
    UPSET["帯30-100倍8点"] = lambda x, b: band_legs2(x, 30.0, 100.0, 8)
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        rows = build(win)
        nd = C.days_of(C.select(None, win))
        cur = [r["cur"] for r in rows]
        print("\n" + "=" * 132)
        print(f"███ Phase6 帯の効き（対照つき）  {label}   n={len(rows):,}R / {nd}日")
        print(HDR4)
        show4("現行", agg([v for v in cur if v], nd))
        for name, (bb, parts) in ARMS6.items():
            arm = [(combo_product(r["x"], r["cur_key"], parts, bb) if r["cur"] else None)
                   for r in rows]
            arm = [(b if b else c) for b, c in zip(arm, cur)]
            s = agg([v for v in arm if v], nd)
            show4(name, s)
            a_p = [c for c, b in zip(cur, arm) if c and b]
            b_p = [b for c, b in zip(cur, arm) if c and b]
            ci_s, ci_r = boot(a_p, b_p)
            print(f"      └ Δ表示的中 [{ci_s[0]:+.2f},{ci_s[1]:+.2f}]pt  "
                  f"ΔROI [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]pt  "
                  f"（組めた {sum(1 for c, b in zip(cur, arm) if c and b is not c)}R）")



# ═══════════ Phase 7 — 上帯を重ねたうえで、看板枠 `F_sign` を畳めるか ═══════════
#
# `F_sign`（表示的中 5%・10万+ を作る枠）は「表示的中を売って看板を買う」交換だった。
# 上帯を全商品へ薄く重ねると**同じ看板が表示的中を上げながら**手に入るので、
# 枠そのものが不要になる可能性がある。4腕で直接比べる。

def phase7() -> None:
    parts = [("帯100-600倍8点", 1000), ("看板15万", 1000)]
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        print("\n" + "=" * 132)
        print(f"███ Phase7 看板枠 × 上帯  {label}")
        print(HDR4)
        for tag, srt in (("現行(看板枠 決勝系+準決勝系)", None), ("看板枠なし", ())):
            rows = build(win, sign_rt=srt)
            nd = C.days_of(C.select(None, win))
            cur = [r["cur"] for r in rows]
            show4(f"{tag}", agg([v for v in cur if v], nd))
            arm = [(combo_product(r["x"], r["cur_key"], parts, 8000) if r["cur"] else None)
                   for r in rows]
            arm = [(b if b else c) for b, c in zip(arm, cur)]
            show4(f"{tag} + 上帯2割", agg([v for v in arm if v], nd))
            a_p = [c for c, b in zip(cur, arm) if c and b]
            b_p = [b for c, b in zip(cur, arm) if c and b]
            ci_s, ci_r = boot(a_p, b_p)
            print(f"      └ Δ表示的中 [{ci_s[0]:+.2f},{ci_s[1]:+.2f}]pt  "
                  f"ΔROI [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]pt")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    if cmd == "phase1":
        phase1()
    elif cmd == "phase3":
        phase3()
    elif cmd == "phase7":
        phase7()
    elif cmd == "phase6":
        phase6()
    elif cmd == "phase4":
        phase4()
    elif cmd == "phase5":
        phase5(*(sys.argv[2:3] or []), **({"up": int(sys.argv[3])} if len(sys.argv) > 3 else {}))
    else:
        phase2(sys.argv[2] if len(sys.argv) > 2 else "看板15万")
