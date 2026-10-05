#!/usr/bin/env python3
"""H19 台側の組み立て: 「何車が堅いか」(k) で買い方を変える（事前登録どおり・README「事前登録 — H19」）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h19_build.py [--eyeball-only]

- 台・母集団・①（現行）は h15_run と同じ（lineup_arms.run と全行一致を assert）。①には L_lead を含めない。
- k = 3着内率 p3 >= TH(=0.60) の車数（3 以上は 3。k>=4 は p3 上位3車を「堅い3車」とする）。
- ②③④ の k>=1 の買い目は S.build / build_with_gate_fallback を通さず手で組む
  （line_swap/order_swap/osae/add_perm/cheap_target を掛けると事前登録の形から外れるため）。
  配分は TL.allocate（dutch・本番と同じ）。結果は data/exp_bet_review/h19/h19_arms_{TH}.pkl。
- 🔴 解釈（README に無く、結果を見る前にここで固定したもの）:
    * 母集団 = ① が売るレース（main + 高額枠）。① が売らない k>=1 のレースは件数だけ別掲。
    * k=3 の捨て: 先頭1点でも 1/o > 1/2.2 なら「捨てる」。丸めで本番ゲートを割る場合も捨てる。
    * k=2: 相手1車につき (a,b,c)(b,a,c) の2点を組で足す。最小1相手（2点）に上丸め。
    * k=1: tier_axis と同じコード形（候補→確率順→cap で k_fit→max(3,min(8,k_fit))→2.0 未満を除いて先頭 k 点）。
      1着は「堅い1車」。gap 条件なし。
    * k=1/2 で組めない（2.0 以上の点が最小点数に届かない・本番ゲートを割る）は ① に戻す（件数を記録）。
    * ③ の穴目: 型A は A_ana、他は T_upset を S.build で組み S.gate_ok を掛ける。落ちたら見送り。
    * ④: ②（③）と同じ点数を、210目から確率降順・2.0 未満を除いて先頭 n 点・dutch。
"""
from __future__ import annotations
import itertools, pickle, sys, time
from collections import defaultdict
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL
from src.type_lab import PLANS, BUDGET
import h15_run as H

CAP = float(BUDGET) / float(TL.TIER_TARGET_PAYOUT)          # 10000/22000 = 0.4545（合成 2.2 倍）
DUTCH = PLANS["T_firm"]                                    # alloc='dutch'
H19_DIR = D / "h19"
THS = (0.60, 0.55, 0.65)
_G = S._G


def alloc(x, legs):
    """dutch 配分（本番 allocate）→ (stakes, gate_ok) or None。"""
    if not legs:
        return None
    stakes = TL.allocate(legs, x.po_tf, x.pr_tf, DUTCH)
    if not stakes:
        return None
    mean = TL.mean_expected_payout(stakes, x.po_tf)
    return stakes, bool(S.gate_ok(stakes, x.po_tf, mean))


def _row(x, stakes):
    inv, pay = S.settle(x, stakes, False)
    comp = 1.0 / sum(1.0 / float(x.po_tf[c]) for c in stakes)      # 合成オッズ（予測）
    return dict(inv=inv, pay=pay, n=len(stakes), comp=float(comp), stakes=dict(stakes))


def build_k3(x, firm3):
    """堅い3車の6並びを確率降順に・合成 2.2 倍を割る直前（break）。最低1点。→ legs or None(捨て)"""
    cand = [q for q in itertools.permutations(firm3) if q in x.po_tf]
    cand.sort(key=lambda q: -float(x.pr_tf.get(q, 0.0)))
    out, s = [], 0.0
    for q in cand:
        if s + 1.0 / float(x.po_tf[q]) > CAP:
            break
        out.append(q); s += 1.0 / float(x.po_tf[q])
    return out or None


def build_k2(x, firm2, order):
    """2車を1・2着（両方の並び）に固定・相手は p3 降順で1車ずつ2点組。最小1相手に上丸め。"""
    a, b = firm2
    partners = [c for c in order if c not in (a, b)]
    out, s = [], 0.0
    for c in partners:
        pair = [(a, b, c), (b, a, c)]
        if any(q not in x.po_tf for q in pair):
            continue
        add = sum(1.0 / float(x.po_tf[q]) for q in pair)
        if s + add > CAP and out:
            break
        out += pair; s += add
    # 2.0 未満の点を含むなら組めない（本番ゲート）→ 呼び出し側で gate_ok が落とす
    return out or None


def build_k1(x, car):
    """tier_axis と同じコード形。1着=堅い1車。"""
    cand = [q for q, v in x.po_tf.items() if v > 0 and q[0] == car]
    cand.sort(key=lambda q: -float(x.pr_tf.get(q, 0.0)))
    s, k_fit = 0.0, 0
    for q in cand:
        if s + 1.0 / float(x.po_tf[q]) > CAP:
            break
        s += 1.0 / float(x.po_tf[q]); k_fit += 1
    k_use = max(TL.TIER_MIN_LEGS, min(8, k_fit))
    out = [q for q in cand if float(x.po_tf[q]) >= TL.MIN_POINT_ODDS][:k_use]
    return out if len(out) >= TL.TIER_MIN_LEGS else None


def build_ctrl(x, n):
    """④: 210目から確率降順・2.0 未満を除いて先頭 n 点・dutch（形の制約なし）。"""
    cand = [q for q, v in x.po_tf.items() if v >= TL.MIN_POINT_ODDS]
    cand.sort(key=lambda q: -float(x.pr_tf.get(q, 0.0)))
    got = alloc(x, cand[:n])
    return got


def ana_row(x):
    """③ の穴目（型A=A_ana / 他=T_upset）を本番の組み方で。本番ゲートを掛ける。"""
    plan = PLANS["A_ana"] if x.shape.type_label == "A" else PLANS["T_upset"]
    got = S.build(x, plan)
    if not got:
        return None, plan.key
    stakes, odds, used, mean = got
    if not S.gate_ok(stakes, odds, mean):
        return None, plan.key
    inv, pay = S.settle(x, stakes, used.bet_type == "trio")
    return dict(inv=inv, pay=pay, n=len(stakes), plan=used.key, stakes=dict(stakes),
                comp=float(1.0 / sum(1.0 / float(odds[c]) for c in stakes))), plan.key


def h19_for(x, p3, th):
    """1レースの H19 構成（① 非依存）。

    返り値 dict: k, k_raw, firm, kind('k0'|'built'|'fallback'|'discard'), r2（②の行 or None）, r3（③の行 or None・
    穴目なら is_ana=True）, c2/c3（④）, ana_why（穴目が落ちた理由）。
    """
    order = list(x.shape.order)
    firm_all = [c for c in order if p3[c] >= th]
    k_raw = len(firm_all)
    k = min(k_raw, 3)
    res = dict(k=k, k_raw=k_raw, firm=firm_all[:3], kind="k0", r2=None, r3=None, c2=None, c3=None, ana_why=None)
    if k == 0:
        return res
    firm = firm_all[:3]
    if k == 3:
        legs = build_k3(x, firm)
        got = alloc(x, legs) if legs else None
        if got and got[1]:
            res["kind"] = "built"
            res["r2"] = _row(x, got[0]); res["r2"]["is_ana"] = False
            res["r3"] = res["r2"]
        else:
            res["kind"] = "discard"
            res["discard_why"] = "1点でも合成2.2倍未満" if not legs else "本番ゲート落ち"
            ar, why = ana_row(x)
            if ar is not None:
                ar["is_ana"] = True
                res["r3"] = ar
            else:
                res["ana_why"] = why
    else:
        legs = build_k2(x, firm, order) if k == 2 else build_k1(x, firm[0])
        got = alloc(x, legs) if legs else None
        if got and got[1]:
            res["kind"] = "built"
            res["r2"] = _row(x, got[0]); res["r2"]["is_ana"] = False
            res["r3"] = res["r2"]
        else:
            res["kind"] = "fallback"          # ① に戻す
    # ④
    if res["r2"] is not None:
        g = build_ctrl(x, res["r2"]["n"])
        res["c2"] = _row(x, g[0]) if g else None
    if res["r3"] is not None:
        if res["r3"] is res["r2"]:
            res["c3"] = res["c2"]
        else:                                  # 穴目に対する④₃（確率上位 n 点・弱い比較）
            g = build_ctrl(x, res["r3"]["n"])
            res["c3"] = _row(x, g[0]) if g else None
    return res


def main():
    t0 = time.time()
    H19_DIR.mkdir(parents=True, exist_ok=True)
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}
    z = S.board()
    P3 = b["P3"]
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    pos = {cache[i].key: i for i in ok}
    print(f"[母集団] {len(idx)}R → ctx {len(ok)}R  {time.time()-t0:.0f}s", flush=True)
    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    recs = H.run15(ok, cache)
    assert len(ref) == len(recs) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in recs)) < 1e-6 \
        and abs(sum(r["inv"] for r in ref) - sum(r["inv"] for r in recs)) < 1e-6, "lineup_arms.run と一致しない"
    cur = {r["race_key"]: r for r in recs}
    assert len(cur) == len(recs), "1レースに複数の行がある"
    print(f"[検証] lineup_arms.run と一致 {len(recs)}行 / {len(cur)}R  (main {sum(r['slot']=='main' for r in recs)} 高額枠 {sum(r['slot']=='highpay' for r in recs)})", flush=True)

    races = {}
    for i in ok:
        x = cache[i]
        races[x.key] = dict(day=x.date, rtype=x.rtype, tl=x.shape.type_label, i=i)
    for th in THS:
        out = {}
        for i in ok:
            x = cache[i]
            p3 = {c: float(P3[i][c - 1]) for c in range(1, 8)}
            out[x.key] = h19_for(x, p3, th)
        pickle.dump(dict(th=th, arms=out, cur=cur, races=races), open(H19_DIR / f"h19_arms_{th:.2f}.pkl", "wb"))
        ks = np.array([v["k_raw"] for v in out.values()])
        print(f"[th={th}] k_raw 分布 " + str({int(a): int((ks == a).sum()) for a in sorted(set(ks))}) + f"  {time.time()-t0:.0f}s", flush=True)
    return cache


if __name__ == "__main__":
    main()
