#!/usr/bin/env python3
"""H17: 看板枠 F_sign を「堅さ f（三連単予測オッズの最小値）」で F_hit と分ける（事前登録どおり）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h17_run.py
台・母集団・同一レース対は h15_run と同じ（lineup_arms.run と一致を assert）。結果: data/exp_bet_review/h17_result.pkl
"""
from __future__ import annotations
import pickle, time
from collections import defaultdict
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import h15_run as H

N_BOOT = 2000
SEEDS = 20
SALES_PT = 814.0
YEN_PT = 0.21


def ci(a):
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def main():
    t0 = time.time()
    b = load_board_2025(); S._Z = {k: b[k] for k in S._NEED}; z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    byk = {cache[i].key: cache[i] for i in ok}
    days_all = sorted({cache[i].date for i in ok}); ND = len(days_all); dpos = {d: j for j, d in enumerate(days_all)}
    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    recs = H.run15(ok, cache)
    assert len(ref) == len(recs) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in recs)) < 1e-6
    print(f"[検証] lineup_arms.run と一致 {len(recs)}行 日数={ND}", flush=True)
    F_sign = [r for r in recs if r["plan"] == "F_sign"]
    pairs = []; nop = 0
    for r in F_sign:
        x = byk[r["race_key"]]
        h, why = H.hit_counterpart(x, "F")
        if h is None:
            nop += 1; continue
        f = float(min(x.po_tf.values()))
        pairs.append(dict(day=r["day"], key=r["race_key"], f=f, rtype=r["rtype"],
                          s=dict(inv=r["inv"], pay=r["pay"], n=r["n"], stakes=r["stakes"], trio=r["trio"]),
                          h=dict(inv=h["inv"], pay=h["pay"], n=h["n"], stakes=h["stakes"], plan=h["plan"])))
    N = len(pairs)
    print(f"[母集団] F_sign {len(F_sign)} → 対 {N}R（対にならない {nop}）", flush=True)
    day_i = np.array([dpos[p["day"]] for p in pairs])
    half = np.array([int(p["day"][5:7]) <= 6 for p in pairs])    # True=上期
    f = np.array([p["f"] for p in pairs])
    # 三分位境界（上期の対象レースで固定）
    q1, q2 = np.percentile(f[half], [100 / 3, 200 / 3])
    tert = np.where(f <= q1, 0, np.where(f <= q2, 1, 2))          # 0=最も堅い
    print(f"[境界] 上期 n={half.sum()} q33.3={q1:.4f} q66.7={q2:.4f}  f 全体 min={f.min():.3f} med={np.median(f):.3f} max={f.max():.3f}", flush=True)
    print("  三分位別 R: " + str({t: int((tert == t).sum()) for t in range(3)}) + " 上期 " + str({t: int(((tert == t) & half).sum()) for t in range(3)}), flush=True)

    def arr(side, key):
        return np.array([p[side][key] for p in pairs], float)
    Sinv, Spay, Hinv, Hpay = arr("s", "inv"), arr("s", "pay"), arr("h", "inv"), arr("h", "pay")
    Ssh, Hsh = (Spay > Sinv).astype(float), (Hpay > Hinv).astype(float)
    Sbig, Hbig = (Spay >= 1e5).astype(float), (Hpay >= 1e5).astype(float)
    Shit, Hhit = (Spay > 0).astype(float), (Hpay > 0).astype(float)

    def dayagg(sel_hit, mask=None):
        """sel_hit: bool(N) True=F_hit を買う。→ 日×指標 行列 (ND, [inv,pay,sh,big,n,hit,nsw])"""
        inv = np.where(sel_hit, Hinv, Sinv); pay = np.where(sel_hit, Hpay, Spay)
        sh = np.where(sel_hit, Hsh, Ssh); big = np.where(sel_hit, Hbig, Sbig); hit = np.where(sel_hit, Hhit, Shit)
        msk = np.ones(N, bool) if mask is None else mask
        M = np.zeros((ND, 7))
        for col, v in enumerate((inv, pay, sh, big, np.ones(N), hit, sel_hit.astype(float))):
            np.add.at(M[:, col], day_i[msk], v[msk])
        return M

    def point(M, nd):
        inv, pay, sh, big, n, hit, nsw = M.sum(0)
        return dict(R=int(n), roi=pay / inv * 100, shown=sh / n * 100, shown_n=int(sh), big=int(big), big_day=big / nd,
                    hit=int(hit), nsw=int(nsw))

    def med_hitpay(sel_hit, mask):
        v = np.where(sel_hit, Hpay, Spay)[mask]
        v = v[v > 0]
        return float(np.median(v)) if len(v) else float("nan")

    allm = np.ones(N, bool)
    periods = {"通年": allm, "上期": half, "下期": ~half}
    ndays = {"通年": ND, "上期": len({d for d, h in zip(day_i, half) if h}), "下期": len({d for d, h in zip(day_i, half) if not h})}
    rng = np.random.default_rng(20261009)
    bix = rng.integers(0, ND, (N_BOOT, ND))
    W = np.stack([np.bincount(ix, minlength=ND) for ix in bix]).astype(float)   # (B, ND)
    half_days = np.array([False] * ND)
    for d, h in zip(day_i, half):
        if h: half_days[d] = True
    out = {"q": (float(q1), float(q2))}

    def boot_metric(M, Wm):
        T = Wm @ M       # (B,7)
        return dict(roi=T[:, 1] / T[:, 0] * 100, shown=T[:, 2] / T[:, 4] * 100, big=T[:, 3])

    # ---- 三分位別 F_sign / F_hit
    print("\n=== 三分位別（堅い→緩い）F_sign vs F_hit（同一レース） ===", flush=True)
    tt = {}
    for t in range(3):
        for pn, pm in periods.items():
            mk = (tert == t) & pm
            Ms, Mh = dayagg(np.zeros(N, bool), mk), dayagg(np.ones(N, bool), mk)
            ps, ph = point(Ms, ndays[pn]), point(Mh, ndays[pn])
            tt[(t, pn)] = (ps, ph, int(mk.sum()))
            print(f"T{t+1} {pn} R={mk.sum()} f∈[{f[mk].min():.2f},{f[mk].max():.2f}]  sign: ROI {ps['roi']:.1f}% 表示的中 {ps['shown']:.2f}% 10万+ {ps['big']}件 | hit: ROI {ph['roi']:.1f}% 表示的中 {ph['shown']:.2f}% 10万+ {ph['big']}件", flush=True)
    out["tert"] = tt

    # ---- 腕
    sel1 = np.zeros(N, bool)                       # ①
    sel2 = (tert == 0)                              # ②
    # ③ 上期で三分位ごとに ROI の高い方
    choice = {}
    for t in range(3):
        mk = (tert == t) & half
        rs = Spay[mk].sum() / Sinv[mk].sum(); rh = Hpay[mk].sum() / Hinv[mk].sum()
        choice[t] = rh > rs
        print(f"[③ 上期] T{t+1}: sign {rs*100:.1f}% hit {rh*100:.1f}% → {'F_hit' if rh > rs else 'F_sign'}", flush=True)
    sel3 = np.array([choice[t] for t in tert])      # 下期にのみ当てる
    out["choice"] = choice

    def control(sel, mask, rng_):
        """sel の本数を mask 内の無作為レースへ（mask 内で同数）。"""
        k = int(sel[mask].sum()); pool = np.flatnonzero(mask)
        s = np.zeros(N, bool); s[rng_.choice(pool, k, replace=False)] = True
        return s

    def evaluate(name, sel, evalmask_name, mask_for_control):
        pm = periods[evalmask_name]
        wm = (np.ones(ND, bool) if evalmask_name == "通年" else (half_days if evalmask_name == "上期" else ~half_days))
        # 上期・下期の日は排他（日付で期が決まる）
        # 無作為: 期ごとに同数を引く
        ctrls = []
        for sd in range(SEEDS):
            r_ = np.random.default_rng(1000 + sd)
            s = np.zeros(N, bool)
            for hm in ((half, ~half) if evalmask_name == "通年" else (pm,)):
                s |= control(sel & hm if evalmask_name != "通年" else sel, hm, r_) if False else control_in(sel, hm, r_)
            ctrls.append(s)
        selm = sel & pm
        Marm = dayagg(selm, pm)
        Mbase = dayagg(np.zeros(N, bool), pm)
        Mc = [dayagg(c & pm, pm) for c in ctrls]
        nd = ndays[evalmask_name]
        Wm = W * wm[None, :]
        # 期内日だけで再標本化（期が通年でなければ当該期の日のみ）
        if evalmask_name != "通年":
            # 期内の日を ND_p 個、置換抽出
            dd = np.flatnonzero(wm)
            Wm = np.stack([np.bincount(rng.choice(dd, len(dd)), minlength=ND) for _ in range(N_BOOT)]).astype(float)
        a = boot_metric(Marm, Wm); base = boot_metric(Mbase, Wm)
        cs = [boot_metric(M, Wm) for M in Mc]
        cmed_sh = np.median(np.stack([c["shown"] for c in cs]), 0); cmed_roi = np.median(np.stack([c["roi"] for c in cs]), 0)
        d_sh = a["shown"] - cmed_sh
        pa, pb = point(Marm, nd), point(Mbase, nd)
        pcs = [point(M, nd) for M in Mc]
        c_sh = float(np.median([p["shown"] for p in pcs])); c_roi = float(np.median([p["roi"] for p in pcs]))
        c_big = float(np.median([p["big"] for p in pcs])); c_hit = float(np.median([p["hit"] for p in pcs]))
        d_roi_vs1 = a["roi"] - base["roi"]
        res = dict(name=name, period=evalmask_name, arm=pa, base=pb, ctrl=dict(shown=c_sh, roi=c_roi, big=c_big, hit=c_hit),
                   d_shown=(pa["shown"] - c_sh, *ci(d_sh)), d_roi_ctrl=pa["roi"] - c_roi,
                   d_roi_ctrl_ci=ci(a["roi"] - cmed_roi),
                   d_roi_vs1=(pa["roi"] - pb["roi"], *ci(d_roi_vs1)),
                   medhit=med_hitpay(sel, pm), medhit1=med_hitpay(np.zeros(N, bool), pm),
                   dsw=pa["nsw"], nd=nd)
        cond1 = res["d_shown"][1] > 0 and res["d_roi_ctrl"] > 0
        cond2 = res["d_roi_vs1"][1] > -5
        res["pass_select"] = bool(cond1); res["pass_nobad"] = bool(cond2)
        print(f"\n[{name} / {evalmask_name}] R={pa['R']} 切替{pa['nsw']}本({pa['nsw']/nd:.3f}/日) ROI {pa['roi']:.2f}%(①{pb['roi']:.2f}% 無作為中央{c_roi:.2f}%) "
              f"表示的中 {pa['shown']:.2f}%(①{pb['shown']:.2f}% 無作為中央{c_sh:.2f}%) 10万+ {pa['big']}件={pa['big_day']:.4f}/日(①{pb['big']}件 無作為中央{c_big:.0f}) "
              f"的中時払戻中央 {res['medhit']:,.0f}(①{res['medhit1']:,.0f})", flush=True)
        print(f"   差(表示的中,arm−無作為中央) {res['d_shown'][0]:+.2f}pt CI[{res['d_shown'][1]:+.2f},{res['d_shown'][2]:+.2f}]  差(ROI,arm−無作為中央) {res['d_roi_ctrl']:+.2f}pt CI[{res['d_roi_ctrl_ci'][0]:+.2f},{res['d_roi_ctrl_ci'][1]:+.2f}]"
              f"  ΔROI(arm−①) {res['d_roi_vs1'][0]:+.2f}pt CI[{res['d_roi_vs1'][1]:+.2f},{res['d_roi_vs1'][2]:+.2f}]", flush=True)
        print(f"   判定: 堅さで選ぶ意味 {'○' if cond1 else '×'}  全体を悪くしない {'○' if cond2 else '×'}", flush=True)
        return res

    def control_in(sel, hm, r_):
        k = int((sel & hm).sum()); pool = np.flatnonzero(hm)
        s = np.zeros(N, bool); s[r_.choice(pool, k, replace=False)] = True
        return s

    out["arms"] = {}
    for pn in ("通年", "上期", "下期"):
        out["arms"][("②", pn)] = evaluate("②", sel2, pn, None)
    # ③ は下期のみ（上期は in-sample）
    sel3d = sel3 & ~half
    out["arms"][("③", "下期")] = evaluate("③", sel3d, "下期", None)
    # ② の下期も同じ下期で並べる（既に上で）
    # 売上影響の目安: 切替本数 × 814pt × 0.21円
    for k, r in out["arms"].items():
        nsw, nd = r["dsw"], r["nd"]
        print(f"[売上目安] {k}: 看板枠 {nsw}本 減（{nsw/nd:.3f}本/日）→ 約 {nsw*SALES_PT*YEN_PT:,.0f}円（{nsw*SALES_PT*YEN_PT/nd:,.0f}円/日）減（F_sign が hit より 814pt/本 多く売れるとした場合）", flush=True)
    # 確認用: 堅さ上位・下位のレース
    order = np.argsort(f)
    out["pairs"] = pairs; out["tert_arr"] = tert; out["half"] = half
    print("\n[f 最小 3 件 / 最大 3 件]", flush=True)
    for i in list(order[:3]) + list(order[-3:]):
        p = pairs[i]
        x = byk[p["key"]]
        print(f"  {p['key']} f={p['f']:.3f} T{tert[i]+1} {p['rtype']} 着={x.win_tf} | F_sign {p['s']['n']}点 pay={p['s']['pay']:.0f} 買い目={[(k if not isinstance(k,tuple) else k) for k in list(p['s']['stakes'])[:4]]} | F_hit {p['h']['n']}点 pay={p['h']['pay']:.0f}", flush=True)
    pickle.dump(out, open(D / "h17_result.pkl", "wb"))
    print(f"完了 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
