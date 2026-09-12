#!/usr/bin/env python3
"""◎○△ の並び違いで落としている回を、当たっている回を壊さずに拾えるか（2026-09-10）。

腕（すべて本番の `allocate` を通し、入稿ゲートも本番と同じ）:
  ⓪ 現行
  ① 追加 : 現行の買い目に「◎○△ の確率最上位の並び」を1点足す（点数+1・予算は同じ）
  ② 入替 : 現行の**確率最下位の1点**を上と入れ替える（点数据え置き）
  ③ 入替2: 確率最下位の2点を ◎○△ の上位2並びと入れ替える
  ④ ②を「◎○△で決まる確率 q_m3 が探索窓 p50 以上」のレースだけに当てる

🔴 **当たっている回を壊していないか**を必ず内訳で出す（維持 / 破壊 / 救済）。
🔴 三連複プラン（D_hit / A_trio）は集合を1つ足す形で同じことをする。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
CACHE = Path("/tmp/mark_order_fix_rows2.pkl")
#: 設計上「当てにいかない」商品（穴狙い・看板枠）。触らない。
BY_DESIGN = {"A_ana", "F_sign"}


def score(legs, plan, pod, prb, x):
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=float(mean), gate=gate)


def build() -> list[dict]:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    MK = z["A_prediction_mark"]
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            mk = {c: int(MK[i][c - 1]) for c in range(1, 8)}
            m3 = tuple(c for c in range(1, 8) if mk[c] in (1, 2, 3))
            if len(m3) != 3:
                continue
            trio_ok = False
            if tp[i] == "A":
                p = PLANS["A_trio"]
                lg = build_legs(x.shape, p, x.po_t3, x.pr_t3)
                trio_ok = bool(lg) and (score(lg, p, x.po_t3, x.pr_t3, x) or {}).get("gate")
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok,
                            tuple(SIGNBOARD_RACE_TYPES))
            plan = PLANS[key]
            trio = plan.bet_type == "trio"
            pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
            legs = build_legs(x.shape, plan, pod, prb)
            if not legs:
                continue
            cur = score(legs, plan, pod, prb, x)
            if cur is None:
                continue

            # ◎○△ の候補（三連単は6並びを確率降順・三連複は集合1つ）
            if trio:
                cand = [frozenset(m3)]
            else:
                cand = sorted(itertools.permutations(m3),
                              key=lambda c: -prb.get(c, 0.0))
            cand = [c for c in cand if c in pod and float(pod[c]) > 0]
            q_m3 = sum(prb.get(c, 0.0) for c in
                       ([frozenset(m3)] if trio else
                        list(itertools.permutations(m3))))
            arms = {"cur": cur}
            base = list(legs)
            low = sorted(base, key=lambda c: prb.get(c, 0.0))     # 確率の低い順
            miss = [c for c in cand if c not in base]
            for m in (1, 2, 3):
                if len(miss) >= m:
                    arms[f"add{m}"] = score(base + miss[:m], plan, pod, prb, x)
            if miss:
                drop = {low[0]} if len(base) > 1 else set()
                arms["swap1"] = score([c for c in base if c not in drop] + miss[:1],
                                      plan, pod, prb, x)
            # 🔴 本線＝**三連複 {◎○△} の予測オッズ**（ユーザーの言う「1番人気で2〜3倍」）。
            hs_trio = float(x.po_t3.get(frozenset(m3), float("nan")))
            hs_tf = float(x.po_tf.get(tuple(sorted(m3, key=lambda c: mk[c])),
                                      float("nan")))
            pop = int(sum(1 for v in x.po_t3.values()
                          if np.isfinite(v) and 0 < v < hs_trio)) + 1
            rows.append(dict(win=win, date=x.date, type=tp[i], plan=key, trio=trio,
                             q_m3=float(q_m3), n_legs=len(base),
                             hs_trio=hs_trio, hs_tf=hs_tf, hs_pop=pop,
                             m3_hit=(frozenset(x.win_tf) == frozenset(m3)),
                             cur_hit=bool(cur["pay"] >= cur["inv"]),
                             arms=arms))
    return rows


if CACHE.exists():
    ROWS = pickle.load(CACHE.open("rb"))
else:
    ROWS = build()
    pickle.dump(ROWS, CACHE.open("wb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}
print(f"台 {len(ROWS):,}行  探索 {len(W['explore']):,} / 確認 {len(W['confirm']):,}")


def agg(sel, nd):
    if not sel:
        return None
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    pays = sorted(a["pay"] for a in sel if a["pay"] > 0)
    return dict(perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                shown=np.mean([a["pay"] >= a["inv"] for a in sel]) * 100,
                roi=pay / inv * 100, med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd)


def boot(a, b, iters=1000, seed=0):
    rng = np.random.default_rng(seed)
    A = np.array([x["pay"] >= x["inv"] for x in a], float)
    B = np.array([x["pay"] >= x["inv"] for x in b], float)
    ap = np.array([x["pay"] for x in a]); ai = np.array([x["inv"] for x in a])
    bp = np.array([x["pay"] for x in b]); bi = np.array([x["inv"] for x in b])
    n = len(A); ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((A[j].mean() - B[j].mean()) * 100)
        dr.append(ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100)
    f = lambda v: (np.mean(v), np.percentile(v, 2.5), np.percentile(v, 97.5))
    return f(ds), f(dr)


def pick(r, arm, scope, thr=None):
    """その腕を当てる条件に合えば腕、合わなければ現行。ゲート落ちは現行へ落とす。"""
    ok = (r["plan"] not in BY_DESIGN and (scope is None or r["plan"] in scope)
          and (thr is None or r["q_m3"] >= thr))
    if ok and r["arms"].get(arm) and r["arms"][arm]["gate"]:
        return r["arms"][arm]
    c = r["arms"]["cur"]
    return c if c["gate"] else None


def main() -> None:
    thr = float(np.percentile([r["q_m3"] for r in W["explore"]], 50))
    print(f"q_m3（◎○△で決まる予測確率）の探索窓 p50 = {thr:.4f}")
    scopes = [("全プラン（設計上の穴狙い除く）", None, None),
              ("B_hit/E_hit/D_hit だけ", {"B_hit", "E_hit", "D_hit"}, None),
              ("同上 × q_m3 上位50%", {"B_hit", "E_hit", "D_hit"}, thr),
              ("B/E/D + A_hit/F_hit", {"B_hit", "E_hit", "D_hit", "A_hit", "F_hit"}, None)]
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        base = [r["arms"]["cur"] for r in rows if r["arms"]["cur"]["gate"]]
        B = agg(base, nd)
        print("\n" + "=" * 122)
        print(f"=== {lab}  {nd}日")
        print("=" * 122)
        print(f"    {'腕':38s} {'件/日':>6s} {'点数':>5s} {'表示的中%':>9s} {'ROI%':>7s} "
              f"{'払戻中央':>9s} {'10万+/日':>8s} {'維持':>5s} {'破壊':>5s} {'救済':>5s}"
              f"   {'現行との差 95%CI':>30s}")
        print(f"    {'⓪ 現行':38s} {B['perday']:6.2f} {B['k']:5.1f} {B['shown']:9.2f}"
              f" {B['roi']:7.1f} {B['med']:9,.0f} {B['big']:8.3f}")
        for arm, an in (("add1", "① 追加(+1点)"), ("swap1", "② 入替(1点)"),
                        ("swap2", "③ 入替(2点)")):
            for sn, scope, t in scopes:
                sel, pa, pb, keep, brk, sav = [], [], [], 0, 0, 0
                for r in rows:
                    x = pick(r, arm, scope, t)
                    y = r["arms"]["cur"] if r["arms"]["cur"]["gate"] else None
                    if x:
                        sel.append(x)
                    if x and y:
                        pa.append(x); pb.append(y)
                        hx, hy = x["pay"] >= x["inv"], y["pay"] >= y["inv"]
                        keep += hx and hy; brk += hy and not hx; sav += hx and not hy
                S = agg(sel, nd)
                (ds, dl, dh), (rs, rl, rh) = boot(pa, pb)
                print(f"    {an+' / '+sn:38s} {S['perday']:6.2f} {S['k']:5.1f}"
                      f" {S['shown']:9.2f} {S['roi']:7.1f} {S['med']:9,.0f} {S['big']:8.3f}"
                      f" {keep:5,} {brk:5,} {sav:5,}"
                      f"   {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}] {rs:+5.1f} [{rl:+5.1f},{rh:+5.1f}]")


if __name__ == "__main__":
    main()
