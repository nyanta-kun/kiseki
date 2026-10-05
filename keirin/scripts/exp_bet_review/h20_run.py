#!/usr/bin/env python3
"""H20 本体: 共通項による除外（事前登録どおり）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h20_run.py
前提: alloc_common.py build が data/exp_bet_review/alloc_base.pkl を作っていること。
出力: data/exp_bet_review/h20/{h20_result.pkl, h20_tables.md}

手順（README「事前登録 — H20」・解釈の固定は reports/exp_H20.md §0）:
  1. 上期（〜2025-06-30）の①（現行ラインナップ）の売った行に層ラベルを付け、層ごとに回収率と開催日ブートストラップ CI。
     候補 = CI 上限 < 上期全体の回収率 ∧ 件数 >= 200 ∧ 開催日 >= 60。候補は全部まとめて1つの除外集合。
  2. 下期: ①（除外なし） / ②（除外集合に当たるレースを日次上限・高額枠の前に forced skip） / ③（同数の無作為除外・20 seed）。
版 A = L_lead あり / 版 B = L_lead なし（それぞれ別々に候補を決める）。
"""
from __future__ import annotations
import pickle, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C    # noqa: E402
import h20_common as H      # noqa: E402

D = C.D
OUT = D / "h20"
OUT.mkdir(exist_ok=True)
N_BOOT, N_SEED = 2000, 20
MIN_N, MIN_DAYS = 200, 60


def pooled(inv, pay, B):
    with np.errstate(invalid="ignore", divide="ignore"):
        return pay[B].sum(1) / inv[B].sum(1) * 100


def ci(a):
    return float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))


# ───────────────────────────── 段1: 上期の層別 ─────────────────────────────
def step1(L, recs, rec_lab, days1, cuts):
    """上期の売った行に全次元ラベルを付けて層表を作る。"""
    rows, labs = [], []
    for day in days1:
        for s in L[day][0]:
            rec = REC[s["key"]]
            rows.append((day, s))
            labs.append(H.sold_labels(s, rec_lab[rec["i"]], rec, cuts))
    acc = H.stratum_day_sums(rows, days1, labs)
    nd = len(days1)
    rng = np.random.default_rng(20261008)
    B = rng.integers(0, nd, size=(N_BOOT, nd))
    inv_all = sum(a[0] for k, a in acc.items() if k[0] == "型")
    pay_all = sum(a[1] for k, a in acc.items() if k[0] == "型")
    overall = pay_all.sum() / inv_all.sum() * 100
    ov_ci = ci(pooled(inv_all, pay_all, B))
    n_all = int(sum(a[3].sum() for k, a in acc.items() if k[0] == "型"))
    hit_all = float(sum(a[2].sum() for k, a in acc.items() if k[0] == "型")) / n_all * 100
    tab = []
    for (dim, val), a in acc.items():
        inv, pay, hit, n = a
        r = pay.sum() / inv.sum() * 100
        lo, hi = ci(pooled(inv, pay, B))
        tab.append(dict(dim=dim, val=val, n=int(n.sum()), days=int((n > 0).sum()), roi=r, lo=lo, hi=hi,
                        shown=float(hit.sum() / n.sum() * 100),
                        cand=bool(hi < overall and n.sum() >= MIN_N and (n > 0).sum() >= MIN_DAYS)))
    tab.sort(key=lambda t: (H.ALL_DIMS.index(t["dim"]), t["val"]))
    return tab, overall, ov_ci, n_all, hit_all


# ───────────────────────────── 段2: 下期 ─────────────────────────────
def excluded_keys(L0, rows_by_day, rec_lab, cand_race, cand_prod, cuts, days):
    """日 -> 除外するレースキー集合。レース次元は全レース、商品次元は①で売った行の商品で判定。"""
    out, n_sold, n_unsold = {}, Counter(), Counter()
    for day in days:
        sold0 = {s["key"]: s for s in L0[day][0]}
        E = set()
        for r in rows_by_day[day]:
            lab = rec_lab[r["i"]]
            hit = any(lab[d] == v for d, v in cand_race)
            if not hit and r["key"] in sold0 and cand_prod:
                pl = H.sold_labels(sold0[r["key"]], lab, r, cuts)
                hit = any(pl[d] == v for d, v in cand_prod)
            if hit:
                E.add(r["key"])
        out[day] = E
        n_sold[day] = len(E & set(sold0))
        n_unsold[day] = len(E - set(sold0))
    return out, n_sold, n_unsold


def control_skip(L0, rows_by_day, E, days, seed):
    """日ごとに②と同数を無作為に除外: ①で売ったレースから a 件、売らなかったレースから b 件。"""
    rng = np.random.default_rng(seed)
    out = {}
    for day in days:
        sold0 = sorted({s["key"] for s in L0[day][0]})
        rest = sorted({r["key"] for r in rows_by_day[day]} - set(sold0))
        a = len(E[day] & set(sold0)); b = len(E[day] - set(sold0))
        pick = set()
        if a:
            pick |= set(rng.choice(sold0, a, replace=False))
        if b:
            pick |= set(rng.choice(rest, b, replace=False))
        out[day] = pick
    return out


def metrics_arr(L, days):
    A = H.arr_from_lineup(L, days)
    return A


def summarize(A, hp, ld, days_ix=None):
    nd = len(A.days)
    m = C.metrics(A)
    m["highpay_day"] = float(hp.mean()); m["lead_day"] = float(ld.mean())
    return m


def step2(recs, morning, rows_by_day, rec_lab, cands, with_lead, days2, L_full, cuts):
    cand_race = [(t["dim"], t["val"]) for t in cands if t["dim"] in H.RACE_DIMS]
    cand_prod = [(t["dim"], t["val"]) for t in cands if t["dim"] in H.PROD_DIMS]
    L0 = {d: L_full[d] for d in days2}
    E, ns, nu = excluded_keys(L0, rows_by_day, rec_lab, cand_race, cand_prod, cuts, days2)
    skip_ix = {d: E[d] for d in days2}
    res = {"E": E, "ns": ns, "nu": nu}
    R_by_day = {d: rows_by_day[d] for d in days2}
    L2 = {d: C.run_day(R_by_day[d], morning, legacy=False, with_lead=with_lead,
                       forced={k: "skip" for k in skip_ix[d]}) for d in days2}
    res["L0"], res["L2"] = L0, L2
    res["L3"] = []
    for s in range(N_SEED):
        P = control_skip(L0, rows_by_day, E, days2, 7000 + s)
        res["L3"].append({d: C.run_day(R_by_day[d], morning, legacy=False, with_lead=with_lead,
                                       forced={k: "skip" for k in P[d]}) for d in days2})
    return res


def evaluate(res, days2):
    nd = len(days2)
    rng = np.random.default_rng(20261009)
    B = rng.integers(0, nd, size=(N_BOOT, nd))
    A0 = H.arr_from_lineup(res["L0"], days2); A2 = H.arr_from_lineup(res["L2"], days2)
    A3 = [H.arr_from_lineup(L, days2) for L in res["L3"]]
    out = {}
    out["m0"] = C.metrics(A0); out["m2"] = C.metrics(A2)
    out["m3"] = [C.metrics(a) for a in A3]
    for nm, L in (("0", res["L0"]), ("2", res["L2"])):
        out["hp" + nm] = float(H.highpay_per_day(L, days2).mean())
        out["ld" + nm] = float(H.lead_per_day(L, days2).mean())
    out["hp3"] = float(np.mean([H.highpay_per_day(L, days2).mean() for L in res["L3"]]))
    out["ld3"] = float(np.mean([H.lead_per_day(L, days2).mean() for L in res["L3"]]))
    p2 = pooled(A2.inv, A2.pay, B)
    p0 = pooled(A0.inv, A0.pay, B)
    p3 = np.array([pooled(a.inv, a.pay, B) for a in A3])         # (seed, boot)
    med3 = np.median(p3, axis=0)
    out["d_ctrl_ci"] = ci(p2 - med3)
    out["d_ctrl_pt"] = out["m2"]["roi"] - float(np.median([m["roi"] for m in out["m3"]]))
    out["d_base_ci"] = ci(p2 - p0)
    out["d_base_pt"] = out["m2"]["roi"] - out["m0"]["roi"]
    out["ctrl_wins"] = int(sum(1 for m in out["m3"] if out["m2"]["roi"] > m["roi"]))
    # 表示的中の差 (②−①) の CI
    def shown_b(A):
        return (A.hit[B].sum(1) / np.maximum(A.n[B].sum(1), 1)) * 100
    out["d_shown_ci"] = ci(shown_b(A2) - shown_b(A0))
    out["d_shown_pt"] = out["m2"]["shown"] - out["m0"]["shown"]
    out["ctrl_shown_med"] = float(np.median([m["shown"] for m in out["m3"]]))
    def big_b(A):
        return A.big[B].sum(1) / nd
    out["big_rel"] = (out["m2"]["big"] - out["m0"]["big"]) / out["m0"]["big"] * 100 if out["m0"]["big"] else float("nan")
    out["big_ctrl_med"] = float(np.median([m["big"] for m in out["m3"]]))
    out["nsold_day"] = float(np.mean([res["ns"][d] for d in days2]))
    out["nunsold_day"] = float(np.mean([res["nu"][d] for d in days2]))
    out["nex_races"] = int(sum(len(res["E"][d]) for d in days2))
    out["pass_roi"] = out["d_ctrl_ci"][0] > 0
    out["pass_shown"] = out["d_shown_pt"] >= -1.0
    out["pass_big"] = out["big_rel"] >= -20.0
    out["passed"] = bool(out["pass_roi"] and out["pass_shown"] and out["pass_big"])
    return out


REC = None


def main():
    global REC
    recs, start = C.load_base()
    REC = {r["key"]: r for r in recs.values()}
    morning = C.morning_set(start)
    rec_lab, cut_axis = H.race_labels(recs, start)
    rows_by_day = C.by_day(recs)
    days_all = sorted(rows_by_day)
    days1 = [d for d in days_all if d <= H.H1_END]
    days2 = [d for d in days_all if d > H.H1_END]
    print(f"開催日 上期 {len(days1)} / 下期 {len(days2)}", flush=True)
    result = {}
    for name, with_lead in (("A", True), ("B", False)):
        L_full = C.lineup(recs, morning, legacy=False, with_lead=with_lead)
        cuts = H.prod_cuts(recs, L_full)
        tab, overall, ov_ci, n_all, hit_all = step1(L_full, recs, rec_lab, days1, cuts)
        cands = [t for t in tab if t["cand"]]
        print(f"[版{name}] 上期 売った行 {n_all} 全体ROI {overall:.2f} [{ov_ci[0]:.2f},{ov_ci[1]:.2f}] 表示的中 {hit_all:.2f}  層 {len(tab)} 候補 {len(cands)}", flush=True)
        for t in cands:
            print(f"   候補 {t['dim']}={t['val']} n={t['n']} days={t['days']} ROI {t['roi']:.1f} [{t['lo']:.1f},{t['hi']:.1f}]", flush=True)
        r = dict(tab=tab, overall=overall, ov_ci=ov_ci, n_all=n_all, hit_all=hit_all, cands=cands,
                 cuts_prod=[list(map(float, c)) for c in cuts], cut_axis=list(map(float, cut_axis)))
        if cands:
            res = step2(recs, morning, rows_by_day, rec_lab, cands, with_lead, days2, L_full, cuts)
            ev = evaluate(res, days2)
            r["ev"] = ev
            r["E"] = {d: sorted(res["E"][d]) for d in days2}
            r["L0"], r["L2"] = res["L0"], res["L2"]
            # 候補層の下期での回収率（①の売った行）— 参考
            rows, labs = [], []
            for day in days2:
                for s in res["L0"][day][0]:
                    rows.append((day, s)); labs.append(H.sold_labels(s, rec_lab[REC[s["key"]]["i"]], REC[s["key"]], cuts))
            acc2 = H.stratum_day_sums(rows, days2, labs)
            rng = np.random.default_rng(20261010)
            B2 = rng.integers(0, len(days2), size=(N_BOOT, len(days2)))
            tot_inv = sum(a[0] for k, a in acc2.items() if k[0] == "型"); tot_pay = sum(a[1] for k, a in acc2.items() if k[0] == "型")
            r["h2_overall"] = float(tot_pay.sum() / tot_inv.sum() * 100)
            r["h2_cand"] = []
            for t in cands:
                a = acc2.get((t["dim"], t["val"]))
                if a is None:
                    r["h2_cand"].append(dict(dim=t["dim"], val=t["val"], n=0)); continue
                lo, hi = ci(pooled(a[0], a[1], B2))
                r["h2_cand"].append(dict(dim=t["dim"], val=t["val"], n=int(a[3].sum()), roi=float(a[1].sum() / a[0].sum() * 100),
                                         lo=lo, hi=hi, shown=float(a[2].sum() / a[3].sum() * 100)))
            e = ev
            print(f"[版{name}] 下期 ①ROI {e['m0']['roi']:.2f} ②ROI {e['m2']['roi']:.2f} 対照中央 {np.median([m['roi'] for m in e['m3']]):.2f} "
                  f"ΔvsCtrl {e['d_ctrl_pt']:+.2f} {e['d_ctrl_ci']} ②−① {e['d_base_pt']:+.2f} {e['d_base_ci']} "
                  f"表示的中Δ {e['d_shown_pt']:+.2f} 10万+ {e['big_rel']:+.1f}% pass={e['passed']}", flush=True)
        result[name] = r
    pickle.dump(result, open(OUT / "h20_result.pkl", "wb"))


if __name__ == "__main__":
    main()
