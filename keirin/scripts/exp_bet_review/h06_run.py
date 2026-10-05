#!/usr/bin/env python3
"""H06 本体: ①現行商品 ②H06 ③無作為対照 を 2025 の探索窓で測る（事前登録どおり・掃引なし）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h06_run.py [--po board]
前提: h06_prep.py が data/exp_bet_review/h06_pop.pkl / h06_final.pkl を作っていること。
KEIRIN_DB_URL（readonly・欠車確認のみ）。結果は stdout と data/exp_bet_review/h06_result.pkl。

- 台: data/exp_bet_review/race_type_board.npz（lineup_sim の /tmp ハードコードを差し替える）
- ①: lineup_arms.run（本番の sell_plans_for / build_with_gate_fallback / 入稿ゲート / 軸信頼ゲート p20 /
     日次上限 / 高額枠）。予測オッズ PO は既定で探索用 vintage に差し替え（--po board で本番 PO のまま）
- ②: 探索用 vintage の予測オッズ >= 100 の三連単を昇順 10 点・ダッチ（1/予測オッズ・合計 1 万円）
- ③: 母集団から同件数を無作為抽出して ② と同じ買い方（20 seed）
"""
from __future__ import annotations
import argparse, pickle, sys, time
from collections import defaultdict
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
sys.path.insert(0, str(REPO / "scripts" / "exp_bet_review"))
D = REPO / "data" / "exp_bet_review"

import lineup_sim as S                                   # noqa: E402
import lineup_arms as R                                  # noqa: E402
import src.type_lab as TL                                # noqa: E402

P80 = 1.6004156508169105     # 2025 全7車(23,056R・walk-forward pw)の mdl_ent p80（固定）
N_BOOT, N_SEED = 2000, 20
K, BAND = 10, 100.0
CANON = S.PERMS


def load_board(use_vintage_po: bool, pop):
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    b = {k: z[k] for k in S._NEED}
    if use_vintage_po:
        po = b["PO"].copy()
        po[:] = np.nan
        for i, v in pop["V"].items():
            po[i] = v.astype(po.dtype)
        b["PO"] = po
    S._Z = b
    return b


def h06_stakes(vrow, floor_h2=False):
    """② の買い目。vrow = 210 点の予測オッズ（CANON 順）。"""
    idx = np.flatnonzero(vrow >= BAND)
    if len(idx) == 0:
        return {}
    idx = idx[np.lexsort((idx, vrow[idx]))][:K]      # 予測オッズ昇順（同値は目の番号順）
    w = [1.0 / float(vrow[j]) for j in idx]
    if floor_h2:
        tot = sum(w)
        st = [max(int(np.floor(10_000 * x / tot / 100)) * 100, 100) for x in w]
    else:
        units = TL._proportional(w, 100)
        st = [u * 100 for u in units]
    return {int(j): s for j, s in zip(idx, st) if s > 0}


def settle_idx(stakes, win_idx, pay_per100):
    inv = float(sum(stakes.values()))
    pay = float(stakes[win_idx] * pay_per100 / 100.0) if win_idx in stakes else 0.0
    return inv, pay


def boot_days(days, fn, rng, n=N_BOOT):
    """日単位ブートストラップ。fn(sampled_day_index_array)->scalar"""
    nd = len(days)
    out = np.empty(n)
    for b in range(n):
        out[b] = fn(rng.integers(0, nd, nd))
    return out


def ci(a):
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


class Agg:
    """レース行 (day, inv, pay) を日配列にする。"""

    def __init__(self, days):
        self.days = list(days)
        self.pos = {d: i for i, d in enumerate(self.days)}
        self.inv = np.zeros(len(self.days)); self.pay = np.zeros(len(self.days))
        self.n = np.zeros(len(self.days)); self.hit = np.zeros(len(self.days))
        self.big = np.zeros(len(self.days))
        self.rows = []

    def add(self, day, inv, pay, key=""):
        i = self.pos[day]
        self.inv[i] += inv; self.pay[i] += pay; self.n[i] += 1
        self.hit[i] += pay > 0
        self.big[i] += pay >= 100_000
        self.rows.append((day, inv, pay, key))

    def roi(self, ix=None):
        if ix is None:
            return self.pay.sum() / self.inv.sum() * 100 if self.inv.sum() else float("nan")
        s = self.inv[ix].sum()
        return self.pay[ix].sum() / s * 100 if s else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--po", default="vintage", choices=["vintage", "board"])
    ap.add_argument("--p80", type=float, default=P80)
    args = ap.parse_args()
    t0 = time.time()
    pop = pickle.load(open(D / "h06_pop.pkl", "rb"))
    fin = pickle.load(open(D / "h06_final.pkl", "rb"))
    FIN = fin["FIN"]
    assert abs(pop["p80_wf"] - P80) < 1e-9, (pop.get("p80_wf"), P80) if args.p80 == P80 else None
    print("FIN min points", min(len(d) for d in FIN.values()), flush=True)
    b = load_board(args.po == "vintage", pop)
    ids = pop["pop"]
    KEY, DATE = b["KEY"], b["DATE"]
    E = pop["ent"]
    tgt = [i for i in ids if E[i] >= args.p80]
    tgt_set = set(tgt)
    print(f"[設定] PO={args.po} p80={args.p80:.6f} 母集団={len(ids)} 対象={len(tgt)} 開催日={len({DATE[i] for i in tgt})}", flush=True)
    days_all = sorted({str(DATE[i]) for i in ids})
    print("母集団の開催日", len(days_all), flush=True)

    # 欠車確認（母集団の出走車番）
    import os, psycopg2
    ent = {}
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); con.set_session(readonly=True, autocommit=True)
    cur = con.cursor()
    keys = [str(KEY[i]) for i in ids]
    for j in range(0, len(keys), 1500):
        cur.execute("SELECT race_key, array_agg(frame_no ORDER BY frame_no) FROM keirin.wt_entries "
                    "WHERE race_key = ANY(%s) GROUP BY race_key", (keys[j:j + 1500],))
        for k, a in cur.fetchall():
            ent[k] = list(a)
    con.close()
    bad = {str(KEY[i]) for i in ids if ent.get(str(KEY[i])) != list(range(1, 8))}
    print(f"[欠車] 出走車番が 1..7 でない母集団レース = {len(bad)}（これらは全腕から除外）", flush=True)
    ids = [i for i in ids if str(KEY[i]) not in bad]
    tgt = [i for i in tgt if str(KEY[i]) not in bad]
    tgt_set = set(tgt)

    # ---- ① 現行商品（全母集団で日次上限・高額枠まで再現）
    cache = {}
    for n, i in enumerate(ids):
        cache[i] = S.ctx(i)
        if n % 4000 == 0:
            print("ctx", n, f"{time.time()-t0:.0f}s", flush=True)
    ok = [i for i in ids if cache[i] is not None]
    print(f"ctx 構築 {len(ok)}/{len(ids)}  {time.time()-t0:.0f}s", flush=True)
    R.AXIS_GATE = True
    recs = R.run("current", {}, ok, cache)
    print(f"① rows {len(recs)}  {time.time()-t0:.0f}s", flush=True)
    key2idx = {str(KEY[i]): i for i in ok}
    rec1 = {}
    for r in recs:
        assert r["race_key"] not in rec1, "1レース2商品"
        rec1[r["race_key"]] = r

    # ---- 台帳: 全レースの勝ち目 / 払戻
    WIN, PAY = b["WIN"], b["PAY"]
    VV = pop["V"]

    def two_rows(i, floor_h2=False):
        st = h06_stakes(VV[i], floor_h2)
        if not st:
            return None
        inv, pay = settle_idx(st, int(WIN[i]), float(PAY[i]))
        return st, inv, pay

    tgt_ok = [i for i in tgt if cache[i] is not None]
    print(f"対象のうち台(ctx)が組めたもの {len(tgt_ok)}/{len(tgt)}", flush=True)

    days_t = sorted({str(DATE[i]) for i in tgt_ok})
    rng = np.random.default_rng(20261006)
    res = {}

    # ② 全対象
    A2 = Agg(days_t); A2b = Agg(days_t)       # A2b = H2 式（floor）
    for i in tgt_ok:
        r = two_rows(i)
        if r:
            A2.add(str(DATE[i]), r[1], r[2], str(KEY[i]))
        r = two_rows(i, True)
        if r:
            A2b.add(str(DATE[i]), r[1], r[2], str(KEY[i]))
    # ① 対象レース上の行
    A1 = Agg(days_t); A2p = Agg(days_t)      # A2p = ① があるレースでの ②
    A2n = Agg(days_t)                       # ① が無いレースでの ②
    n_has = n_no = 0
    slot_ct = defaultdict(int); plan_ct = defaultdict(int)
    for i in tgt_ok:
        k = str(KEY[i]); d = str(DATE[i])
        r2 = two_rows(i)
        r1 = rec1.get(k)
        if r1:
            n_has += 1
            A1.add(d, r1["inv"], r1["pay"], k)
            slot_ct[r1["slot"]] += 1; plan_ct[r1["plan"]] += 1
            if r2:
                A2p.add(d, r2[1], r2[2], k)
        else:
            n_no += 1
            if r2:
                A2n.add(d, r2[1], r2[2], k)
    print(f"[対象 {len(tgt_ok)}R] ①あり {n_has} / ①なし {n_no}  slot={dict(slot_ct)}", flush=True)
    print("  ①のプラン内訳:", dict(sorted(plan_ct.items(), key=lambda x: -x[1])), flush=True)
    print(f"  ② 買い目が組めた {len(A2.rows)}  (点数不足で組めず {len(tgt_ok)-len(A2.rows)})", flush=True)

    # 母集団全体の ①（参照）
    A1all = Agg(days_all)
    for r in recs:
        A1all.add(r["day"], r["inv"], r["pay"], r["race_key"])

    def summ(name, A, ext=""):
        print(f"[{name}] R={len(A.rows)} 日={int((A.n>0).sum())} 投資={A.inv.sum():,.0f} 払戻={A.pay.sum():,.0f} "
              f"ROI={A.roi():.2f}% 的中(払戻>0)={int(A.hit.sum())} 10万+={int(A.big.sum())} {ext}", flush=True)

    summ("②全対象", A2); summ("②H2式floor", A2b)
    summ("①(対象上)", A1); summ("②(①あり)", A2p); summ("②(①なし)", A2n); summ("①(母集団全体)", A1all)

    nd = len(days_t)

    def roi_fn(A):
        return lambda ix: A.pay[ix].sum() / A.inv[ix].sum() * 100 if A.inv[ix].sum() else np.nan

    # ② CI
    bs2 = boot_days(days_t, roi_fn(A2), rng)
    print(f"[②全対象 ROI CI] {A2.roi():.2f}% [{ci(bs2)[0]:.2f}, {ci(bs2)[1]:.2f}]", flush=True)
    # Δ(②−①) 同一レース対（①あり）
    def d_fn(ix):
        i2 = A2p.inv[ix].sum(); i1 = A1.inv[ix].sum()
        return (A2p.pay[ix].sum() / i2 - A1.pay[ix].sum() / i1) * 100
    allix = np.arange(nd)
    delta = d_fn(allix)
    bsd = boot_days(days_t, d_fn, rng)
    print(f"[Δ(②−①) ①ありレース対] {delta:+.2f}pt  [{ci(bsd)[0]:+.2f}, {ci(bsd)[1]:+.2f}]  ①={A1.roi():.2f}% ②={A2p.roi():.2f}%", flush=True)
    # Δ ポートフォリオ（全対象で ② / ① は売るところだけ）
    def dp_fn(ix):
        return (A2.pay[ix].sum() / A2.inv[ix].sum() - A1.pay[ix].sum() / A1.inv[ix].sum()) * 100
    dp = dp_fn(allix); bsp = boot_days(days_t, dp_fn, rng)
    print(f"[Δ ポートフォリオ ②全対象 − ①(売るレースのみ)] {dp:+.2f}pt [{ci(bsp)[0]:+.2f}, {ci(bsp)[1]:+.2f}]", flush=True)
    # ①なしレースの ②
    if len(A2n.rows):
        bsn = boot_days(days_t, roi_fn(A2n), rng)
        print(f"[②(①なし)] ROI {A2n.roi():.2f}% [{ci(bsn)[0]:.2f}, {ci(bsn)[1]:.2f}]  R={len(A2n.rows)}", flush=True)

    # 上期 / 下期
    half = np.array([int(d[5:7]) <= 6 for d in days_t])
    for lab, msk in (("上期", half), ("下期", ~half)):
        ix = np.flatnonzero(msk)
        def dfun(j, ix=ix):
            jj = ix[j]
            return (A2p.pay[jj].sum() / A2p.inv[jj].sum() - A1.pay[jj].sum() / A1.inv[jj].sum()) * 100
        dd = dfun(np.arange(len(ix)))
        bh = boot_days(list(ix), dfun, rng)
        r2h = A2.roi(ix)
        bh2 = boot_days(list(ix), lambda j, ix=ix: roi_fn(A2)(ix[j]), rng)
        print(f"[{lab}] 日={len(ix)} R(②)={int(A2.n[ix].sum())} ②全対象ROI={r2h:.2f}% [{ci(bh2)[0]:.2f},{ci(bh2)[1]:.2f}] "
              f"①(上)={A1.roi(ix):.2f}% ②(①あり)={A2p.roi(ix):.2f}% Δ={dd:+.2f}pt [{ci(bh)[0]:+.2f},{ci(bh)[1]:+.2f}] 的中={int(A2.hit[ix].sum())}", flush=True)

    # 上位 1/3/5 件除外（払戻を除く・投資は残す）
    pays = sorted([r[2] for r in A2.rows], reverse=True)
    for nn in (1, 3, 5):
        print(f"[②上位{nn}件の払戻除外] ROI={(A2.pay.sum()-sum(pays[:nn]))/A2.inv.sum()*100:.2f}%  除外払戻={[round(x) for x in pays[:nn]]}", flush=True)
    pays1 = sorted([r[2] for r in A1.rows], reverse=True)
    print(f"[①(対象上)上位3件除外] ROI={(A1.pay.sum()-sum(pays1[:3]))/A1.inv.sum()*100:.2f}%", flush=True)
    hits2 = [p for p in pays if p > 0]
    print(f"[②] 的中={len(hits2)} 払戻中央={np.median(hits2):,.0f} 最大={max(hits2):,.0f}  開催日(投票あり)={int((A2.n>0).sum())}  "
          f"10万+/日={A2.big.sum()/nd:.3f} (件={int(A2.big.sum())})  ①10万+/日(同レース)={A1.big.sum()/nd:.3f} (件={int(A1.big.sum())})", flush=True)
    sh1 = sum(1 for r in A1.rows if r[2] > r[1])
    print(f"[①] 表示的中(払戻>投資)={sh1}/{len(A1.rows)}={sh1/len(A1.rows)*100:.2f}%  ②: 的中>投資={sum(1 for r in A2.rows if r[2]>r[1])}/{len(A2.rows)}", flush=True)

    # ③ 無作為対照
    pool = list(ids)
    pool = [i for i in pool if h06_stakes(VV[i])]
    res3 = []
    A3s = []
    for s in range(N_SEED):
        rs = np.random.default_rng(1000 + s)
        pick = rs.choice(len(pool), len(A2.rows), replace=False)
        A3 = Agg(days_all)
        for j in pick:
            i = pool[j]
            r = two_rows(i)
            A3.add(str(DATE[i]), r[1], r[2], str(KEY[i]))
        A3s.append(A3); res3.append(A3.roi())
    # ② を days_all 軸へ載せ替え
    A2f = Agg(days_all)
    for d, inv, pay, k in A2.rows:
        A2f.add(d, inv, pay, k)
    wins = sum(1 for r in res3 if A2.roi() > r)
    print(f"[③] ROI 20seed: 中央={np.median(res3):.2f}% min={min(res3):.2f} max={max(res3):.2f}; ②>③ = {wins}/{N_SEED}", flush=True)
    nda = len(days_all)
    medr = lambda ix: np.median([a.pay[ix].sum() / a.inv[ix].sum() * 100 for a in A3s])
    dif = lambda ix: A2f.pay[ix].sum() / A2f.inv[ix].sum() * 100 - medr(ix)
    bd3 = boot_days(days_all, dif, rng, N_BOOT)
    print(f"[②−③(seed中央) 差] {dif(np.arange(nda)):+.2f}pt [{ci(bd3)[0]:+.2f}, {ci(bd3)[1]:+.2f}] (2000回)", flush=True)

    # 発生倍率
    exp_sum = 0.0; hit_ct = 0; cov = 0
    day_e = defaultdict(float); day_h = defaultdict(float)
    for i in tgt_ok:
        k = str(KEY[i]); st = h06_stakes(VV[i])
        f = FIN.get(k)
        if not st or not f or any(j not in f for j in st):
            continue
        cov += 1
        e = sum(0.75 / f[j] for j in st)
        h = 1 if int(WIN[i]) in st else 0
        exp_sum += e; hit_ct += h
        day_e[str(DATE[i])] += e; day_h[str(DATE[i])] += h
    dl = sorted(day_e)
    ea = np.array([day_e[d] for d in dl]); ha = np.array([day_h[d] for d in dl])
    bsr = boot_days(dl, lambda ix: ha[ix].sum() / ea[ix].sum(), rng)
    print(f"[発生倍率] 被覆 {cov}/{len(A2.rows)}R 的中={hit_ct} 期待(0.75/最終)={exp_sum:.2f} 倍率={hit_ct/exp_sum:.3f} [{ci(bsr)[0]:.3f}, {ci(bsr)[1]:.3f}]", flush=True)
    for lab, mk in (("上期", lambda d: int(d[5:7]) <= 6), ("下期", lambda d: int(d[5:7]) > 6)):
        m = np.array([mk(d) for d in dl])
        print(f"   {lab}: 的中={int(ha[m].sum())} 期待={ea[m].sum():.2f} 倍率={ha[m].sum()/ea[m].sum():.3f}", flush=True)
    # ROI も最終オッズ市場の期待(=0.75)に対する形で
    # 平均的市場確率合計（買い目の最終市場確率和の1レース平均）
    print(f"   買い目の最終市場確率和(0.75/odds を 1/odds 化)の1レース平均 = {exp_sum/0.75/cov:.4f}  / 実的中率={hit_ct/cov:.4f}", flush=True)

    # L_flat 重なり（別掲）
    ov = []
    for i in tgt_ok:
        x = cache[i]
        ax = float(x.shape.axis_sum)
        lf = None
        if ax < TL.FLAT_LEAD_AXIS_SUM_MAX:
            lf = S.build(x, TL.PLANS["L_flat"])
        ov.append((i, ax, lf is not None))
    n_ax = sum(1 for _, a, _ in ov if a < 1.327); n_lf = sum(1 for *_, f in ov if f)
    print(f"[別掲] 対象 {len(ov)}R 中 axis_sum<1.327 = {n_ax}  うち L_flat が組める(重なり) = {n_lf}", flush=True)
    for lab, sel in (("重なり(L_flat 組める)", lambda a, f: f), ("重ならない", lambda a, f: not f),
                     ("axis_sum<1.327 全体", lambda a, f: a < 1.327), ("axis_sum>=1.327", lambda a, f: a >= 1.327)):
        keys_sel = {str(KEY[i]) for i, a, f in ov if sel(a, f)}
        r2 = [r for r in A2.rows if r[3] in keys_sel]
        r1 = [r for r in A1.rows if r[3] in keys_sel]
        r2p = [r for r in A2p.rows if r[3] in keys_sel]
        def roi_(rs): return sum(r[2] for r in rs) / sum(r[1] for r in rs) * 100 if rs else float("nan")
        print(f"   {lab}: R(②)={len(r2)} ②ROI={roi_(r2):.2f}% 的中={sum(1 for r in r2 if r[2]>0)} | ①あり R={len(r1)} ①ROI={roi_(r1):.2f}% ②(同レース)={roi_(r2p):.2f}%", flush=True)

    pickle.dump(dict(A2=A2.rows, A1=A1.rows, A2p=A2p.rows, A2n=A2n.rows, res3=res3,
                     cfg=vars(args)), open(D / f"h06_result_{args.po}.pkl", "wb"))
    print(f"完了 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
