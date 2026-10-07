#!/usr/bin/env python3
"""H15 台側: 高額枠（(a) F_sign 看板枠・(b) {B,C,D}_sign 高額枠）の 2025 探索窓での検証（事前登録どおり）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h15_run.py
- 台・母集団・売り分けは h03_run と同じ（lineup_arms.run と全行一致を assert）。本番関数のみ使用。
- 同一レース対: sign 行 ↔ そのレースの {型}_hit（S.build → 入稿ゲート）。hit が組めない/ゲート落ちは「対にならない」として別掲。
- 結果は stdout と data/exp_bet_review/h15_result.pkl。
"""
from __future__ import annotations
import pickle, time
from collections import defaultdict
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL
from src.type_lab import PLANS, HIGHPAY_SLOTS_PER_DAY, SIGNBOARD_RACE_TYPES, SIGNBOARD_TARGET, BUDGET
import h03_run as H3

_G = S._G
N_BOOT = 2000


def run15(idx, cache):
    """h03_run.run(exempt=None) と同じ。高額枠に落ちた理由（gate/cap）と 供給 を記録する。"""
    byday = defaultdict(list)
    for i in idx:
        x = cache[i]
        if x is not None:
            byday[x.date].append(x)
    recs = []
    for day, races in sorted(byday.items()):
        cand = []
        for x in races:
            main = H3.race_main(x)
            if main is None:
                continue
            raw = _G.passes_axis_gate(main["plan"], float(x.shape.axis_sum), 7)
            cand.append((x, main, raw))
        judged = [c for c in cand if not _G.daily_cap_exempt(c[0].rtype, c[0].cupg)
                  and not _G.daily_cap_exempt_plan(c[1]["plan"])]
        budget = max(1, int(len(judged) * float(_G.DAILY_CAP_RACE_FRACTION))) if judged else None
        order = sorted(cand, key=lambda c: -_G.cap_priority(c[1]["plan"], float(c[0].shape.axis_sum), c[0].rp_sd))
        n_capped, dropped = 0, []
        for x, main, passes in order:
            ex = _G.daily_cap_exempt(x.rtype, x.cupg) or _G.daily_cap_exempt_plan(main["plan"])
            if not passes:
                dropped.append((x, "gate")); continue
            if budget is not None and not ex and n_capped >= budget:
                dropped.append((x, "cap")); continue
            if not ex:
                n_capped += 1
            inv, pay = S.settle(x, main["stakes"], main["trio"])
            recs.append(dict(day=day, plan=main["plan"], inv=inv, pay=pay, mean=main["mean"], n=main["n"],
                             slot="main", race_key=x.key, rtype=x.rtype, type=x.shape.type_label,
                             axis=float(x.shape.axis_sum), stakes=main["stakes"], trio=main["trio"], why=None))
        n_done = 0
        for x, why in dropped:
            if n_done >= HIGHPAY_SLOTS_PER_DAY:
                break
            hp = R.highpay_row(x, {}, n_done)
            if not hp:
                continue
            inv, pay = S.settle(x, hp["stakes"], hp["trio"])
            recs.append(dict(day=day, plan=hp["plan"], inv=inv, pay=pay, mean=hp["mean"], n=hp["n"],
                             slot="highpay", race_key=x.key, rtype=x.rtype, type=x.shape.type_label,
                             axis=float(x.shape.axis_sum), stakes=hp["stakes"], trio=hp["trio"], why=why))
            n_done += 1
    return recs


def hit_counterpart(x, t):
    """同じレースの {t}_hit（入稿ゲートまで）。(row or None, 理由)。"""
    plan = PLANS[f"{t}_hit"]
    got = S.build(x, plan)
    if not got:
        return None, "組めない"
    stakes, odds, used, mean = got
    if not S.gate_ok(stakes, odds, mean):
        return None, "入稿ゲート落ち"
    inv, pay = S.settle(x, stakes, used.bet_type == "trio")
    return dict(plan=used.key, inv=inv, pay=pay, n=len(stakes), mean=mean, stakes=stakes), None


def ci(a):
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def main():
    t0 = time.time()
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    byk = {cache[i].key: cache[i] for i in ok}
    print(f"[母集団] {len(idx)}R → ctx {len(ok)}R  {time.time()-t0:.0f}s", flush=True)
    days_all = sorted({cache[i].date for i in ok})
    ND = len(days_all)
    dpos = {d: j for j, d in enumerate(days_all)}

    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    recs = run15(ok, cache)
    assert len(ref) == len(recs) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in recs)) < 1e-6 \
        and abs(sum(r["inv"] for r in ref) - sum(r["inv"] for r in recs)) < 1e-6, "lineup_arms.run と一致しない"
    print(f"[検証] lineup_arms.run と一致 ({len(recs)} 行)  日数={ND}", flush=True)

    # 供給
    F_sign = [r for r in recs if r["plan"] == "F_sign"]
    hp_all = [r for r in recs if r["slot"] == "highpay"]
    print(f"[行] F_sign={len(F_sign)} (main)  高額枠 計{len(hp_all)}: " + str({k: sum(1 for r in hp_all if r['plan'] == k) for k in sorted({r['plan'] for r in hp_all})}), flush=True)
    print(f"  高額枠の供給源: " + str({k: sum(1 for r in hp_all if r['why'] == k) for k in ('gate', 'cap')}), flush=True)
    print(f"  F_sign の種別: " + str({k: sum(1 for r in F_sign if r['rtype'] == k) for k in sorted({r['rtype'] for r in F_sign})}), flush=True)

    arms = {"a": F_sign, "b": [r for r in hp_all if r["plan"] in ("B_sign", "C_sign", "D_sign")]}
    out = {}
    rng = np.random.default_rng(20261008)
    for arm, rows in arms.items():
        pairs, nopair = [], defaultdict(int)
        for r in rows:
            x = byk[r["race_key"]]
            t = "F" if arm == "a" else r["plan"][0]
            h, why = hit_counterpart(x, t)
            if h is None:
                nopair[why] += 1
                continue
            # 軸信頼ゲート（hit 側）も記録（「本来売らない」側かの判定）
            h["axis_pass"] = bool(_G.passes_axis_gate(h["plan"], r["axis"], 7))
            pairs.append((r, h))
        out[arm] = dict(rows=rows, pairs=pairs, nopair=dict(nopair))
        print(f"\n=== ({arm}) sign 行 {len(rows)} → 対になる {len(pairs)}  対にならない {dict(nopair)} ===", flush=True)
        # 日行列
        def dmat(items, key):
            inv = np.zeros(ND); pay = np.zeros(ND); big = np.zeros(ND); hit = np.zeros(ND); sh = np.zeros(ND); n = np.zeros(ND)
            for it in items:
                j = dpos[key(it)]
                inv[j] += it["inv"]; pay[j] += it["pay"]; big[j] += it["pay"] >= 100_000
                hit[j] += it["pay"] > 0; sh[j] += it["pay"] > it["inv"]; n[j] += 1
            return inv, pay, big, hit, sh, n
        S_ = dmat([p[0] for p in pairs], None) if False else None
        sg = dmat([p[0] for p in pairs], lambda it: it["day"] if "day" in it else None) if False else None
        sg = dmat(pairs_s := [p[0] for p in pairs], lambda it: it["day"])
        # hit 側の day は sign 側の day を使う
        hit_items = []
        for r, h in pairs:
            hh = dict(h); hh["day"] = r["day"]; hit_items.append(hh)
        ht = dmat(hit_items, lambda it: it["day"])
        out[arm]["dm"] = (sg, ht)
        def summ(name, items, dm):
            inv, pay, big, hit, sh, n = dm
            pays = sorted((it["pay"] for it in items), reverse=True)
            hitpays = [it["pay"] for it in items if it["pay"] > 0]
            tot = inv.sum()
            res = dict(name=name, R=len(items), days=int((n > 0).sum()), inv=tot, roi=pay.sum() / tot * 100,
                       hits=int(hit.sum()), shown=int(sh.sum()), shown_rate=sh.sum() / len(items) * 100,
                       big=int(big.sum()), big_per_day=big.sum() / ND,
                       med_hit=float(np.median(hitpays)) if hitpays else float("nan"),
                       ex=[(pay.sum() - sum(pays[:k])) / tot * 100 for k in (1, 3, 5)])
            print(f"[{name}] R={res['R']} 日={res['days']} ROI={res['roi']:.2f}% 的中(pay>0)={res['hits']} 表示的中={res['shown']}({res['shown_rate']:.2f}%) "
                  f"10万+={res['big']}({res['big_per_day']:.4f}/日) 的中時払戻中央={res['med_hit']:,.0f} 上位1/3/5除外={res['ex'][0]:.1f}/{res['ex'][1]:.1f}/{res['ex'][2]:.1f}%", flush=True)
            return res
        out[arm]["sign"] = summ("sign", pairs_s, sg)
        out[arm]["hit"] = summ("hit ", hit_items, ht)
        # Δ と CI
        def stat(ix, f):
            return f(ix)
        def droi(ix):
            return (sg[1][ix].sum() / sg[0][ix].sum() - ht[1][ix].sum() / ht[0][ix].sum()) * 100
        def dshown(ix):
            return (sg[4][ix].sum() / sg[5][ix].sum() - ht[4][ix].sum() / ht[5][ix].sum()) * 100
        def dbig(ix):
            return (sg[2][ix].sum() - ht[2][ix].sum()) / len(ix)
        allix = np.arange(ND)
        bix = [rng.integers(0, ND, ND) for _ in range(N_BOOT)]
        for nm, f in (("ΔROI", droi), ("Δ表示的中(pt)", dshown), ("Δ10万+/日", dbig)):
            v = f(allix); bs = np.array([f(ix) for ix in bix])
            out[arm][nm] = (v, *ci(bs))
            print(f"  {nm} (sign−hit) = {v:+.4f}  95%CI [{ci(bs)[0]:+.4f}, {ci(bs)[1]:+.4f}]", flush=True)
        # sign の絶対損益（売らない比較用）: sign ROI の CI
        def sroi(ix):
            return sg[1][ix].sum() / sg[0][ix].sum() * 100
        bs = np.array([sroi(ix) for ix in bix]); out[arm]["sign_roi_ci"] = ci(bs)
        def hroi(ix):
            return ht[1][ix].sum() / ht[0][ix].sum() * 100
        bs2 = np.array([hroi(ix) for ix in bix]); out[arm]["hit_roi_ci"] = ci(bs2)
        print(f"  sign ROI CI {out[arm]['sign_roi_ci']}  hit ROI CI {out[arm]['hit_roi_ci']}", flush=True)
        # 上位3件除外の CI（日ブートストラップ: 各ブートで上位3件を取り直す）
        sp = np.array([(r["day"], r["pay"], r["inv"]) for r, h in pairs], dtype=object)
        def ex3_roi(items_by_day_ix, items):
            pass
        # 実装: 日ごとの払戻リスト
        def ex_ci(items, k):
            byd = defaultdict(list)
            for it in items:
                byd[dpos[it["day"]]].append(it)
            vals = []
            for ix in bix:
                pays, inv = [], 0.0
                for j in ix:
                    for it in byd.get(j, ()):
                        pays.append(it["pay"]); inv += it["inv"]
                pays.sort(reverse=True)
                vals.append((sum(pays) - sum(pays[:k])) / inv * 100)
            return ci(vals)
        out[arm]["ex3_ci_sign"] = ex_ci(pairs_s, 3); out[arm]["ex3_ci_hit"] = ex_ci(hit_items, 3)
        print(f"  上位3除外 ROI CI: sign {out[arm]['ex3_ci_sign']}  hit {out[arm]['ex3_ci_hit']}", flush=True)
        # (b) 供給源別 ΔROI（日次上限落ちのみ / 軸ゲート落ちのみ）
        if arm == "b":
            for w in ("cap", "gate"):
                sel = [(r, h) for r, h in pairs if r["why"] == w]
                sw = dmat([p[0] for p in sel], lambda it: it["day"])
                hw_items = [dict(h, day=r["day"]) for r, h in sel]
                hw = dmat(hw_items, lambda it: it["day"])
                f = lambda ix, sw=sw, hw=hw: (sw[1][ix].sum() / sw[0][ix].sum() - hw[1][ix].sum() / hw[0][ix].sum()) * 100
                v = f(allix); bs = np.array([f(ix) for ix in bix])
                out[arm]["src_" + w] = (v, *ci(bs), len(sel), int(sw[3].sum()))
                print(f"  [供給源 {w}] R={len(sel)} sign的中={int(sw[3].sum())} ΔROI={v:+.2f}pt [{ci(bs)[0]:+.2f},{ci(bs)[1]:+.2f}]", flush=True)
        # 上期/下期
        half = np.array([int(d[5:7]) <= 6 for d in days_all])
        for lab, msk in (("上期", half), ("下期", ~half)):
            ixh = np.flatnonzero(msk)
            d0 = droi(ixh)
            bsh = np.array([droi(ixh[rng.integers(0, len(ixh), len(ixh))]) for _ in range(N_BOOT)])
            sr = sg[1][ixh].sum() / sg[0][ixh].sum() * 100; hr = ht[1][ixh].sum() / ht[0][ixh].sum() * 100
            out[arm][lab] = (d0, *ci(bsh), sr, hr, int(sg[5][ixh].sum()), int(sg[3][ixh].sum()))
            print(f"  [{lab}] ΔROI={d0:+.2f} [{ci(bsh)[0]:+.2f},{ci(bsh)[1]:+.2f}] sign={sr:.2f}% hit={hr:.2f}% R={int(sg[5][ixh].sum())} sign的中={int(sg[3][ixh].sum())}", flush=True)
        # 内訳
        if arm == "a":
            for rt in sorted({p[0]["rtype"] for p in pairs}):
                sub = [p for p in pairs if p[0]["rtype"] == rt]
                si = sum(p[0]["inv"] for p in sub); sp_ = sum(p[0]["pay"] for p in sub)
                hi = sum(p[1]["inv"] for p in sub); hp_ = sum(p[1]["pay"] for p in sub)
                print(f"   種別 {rt}: R={len(sub)} sign ROI {sp_/si*100:.1f}% hit ROI {hp_/hi*100:.1f}%", flush=True)
            print(f"   F_hit の軸信頼ゲート通過: {sum(p[1]['axis_pass'] for p in pairs)}/{len(pairs)}  F_sign 点数平均 {np.mean([p[0]['n'] for p in pairs]):.2f} / F_hit 点数平均 {np.mean([p[1]['n'] for p in pairs]):.2f}", flush=True)
            print(f"   計画平均払戻 中央: sign {np.median([p[0]['mean'] for p in pairs]):,.0f} / hit {np.median([p[1]['mean'] for p in pairs]):,.0f}", flush=True)
        else:
            for t in "BCD":
                sub = [p for p in pairs if p[0]["plan"][0] == t]
                if not sub: continue
                si = sum(p[0]["inv"] for p in sub); sp_ = sum(p[0]["pay"] for p in sub)
                hi = sum(p[1]["inv"] for p in sub); hp_ = sum(p[1]["pay"] for p in sub)
                print(f"   型{t}: R={len(sub)} sign ROI {sp_/si*100:.1f}% (的中{sum(p[0]['pay']>0 for p in sub)}) hit ROI {hp_/hi*100:.1f}% (的中{sum(p[1]['pay']>0 for p in sub)})", flush=True)
            for w in ("gate", "cap"):
                sub = [p for p in pairs if p[0]["why"] == w]
                if not sub: continue
                si = sum(p[0]["inv"] for p in sub); sp_ = sum(p[0]["pay"] for p in sub)
                hi = sum(p[1]["inv"] for p in sub); hp_ = sum(p[1]["pay"] for p in sub)
                print(f"   供給源 {w}: R={len(sub)} sign ROI {sp_/si*100:.1f}% hit ROI {hp_/hi*100:.1f}% (hit が軸ゲート通過 {sum(p[1]['axis_pass'] for p in sub)})", flush=True)
            print(f"   sign 点数平均 {np.mean([p[0]['n'] for p in pairs]):.2f} / hit {np.mean([p[1]['n'] for p in pairs]):.2f}", flush=True)
        # 全体のラインナップに対する寄与（全 recs の中の 10万+）
    big_all = sum(1 for r in recs if r["pay"] >= 100_000)
    big_hp = sum(1 for r in recs if r["pay"] >= 100_000 and (r["plan"].endswith("_sign")))
    print(f"\n[全体] 行 {len(recs)}  10万+ {big_all} ({big_all/ND:.4f}/日)  うち *_sign 由来 {big_hp} ({big_hp/ND:.4f}/日)  F_sign由来 {sum(1 for r in F_sign if r['pay']>=100000)}", flush=True)
    pickle.dump(dict(recs=recs, out={a: {k: v for k, v in o.items() if k not in ('dm',)} for a, o in out.items()},
                     days_all=days_all), open(D / "h15_result.pkl", "wb"))
    print(f"完了 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
