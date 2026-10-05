#!/usr/bin/env python3
"""H03 本体: 決勝の軸信頼ゲート免除（②）vs 現行（①）を 2025 の探索窓で測る（事前登録どおり）。

    set -a; source ~/.config/kiseki/env; set +a   # 欠車確認の DB 読み取りのみ（readonly）
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h03_run.py
結果は stdout と data/exp_bet_review/h03_result.pkl。

- 台: race_type_board.npz（7車・2025）＋探索用 vintage 予測オッズ（h01_common.load_board_2025）
- ①: lineup_arms.run と同じ処理（本番の sell_plans_for / build / 入稿ゲート / 軸信頼ゲート p20 / 日次上限 / 高額枠）
- ②: 同じ処理で、race_type が「決勝」「チャレンジ決勝」に完全一致するレースだけ軸信頼ゲートを常に通す
- 副腕（事前登録外・参考）: 決勝の型F を F_sign でなく F_hit で売る
"""
from __future__ import annotations
import pickle, sys, time, os
from collections import defaultdict
from pathlib import Path
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL
from src.type_lab import PLANS, sell_plans_for, highpay_plan_for, HIGHPAY_SLOTS_PER_DAY

_G = S._G
FINALS = frozenset({"決勝", "チャレンジ決勝"})
N_BOOT = 2000


def is_final(x):
    return x.rtype in FINALS            # 完全一致（部分一致禁止）


def race_main(x, swap=None):
    """R.race_rows と同じ。swap={(型,種別): プランkey} で売るプランを差し替える（副腕用）。"""
    trio_ok = None
    if x.shape.type_label == "A":
        got = S.build(x, PLANS["A_trio"])
        trio_ok = bool(got and S.gate_ok(got[0], got[1], got[3]))
    sell = sell_plans_for(x.shape.type_label, 7, x.rtype, pw_ent=x.shape.pw_ent, trio_ok=trio_ok)
    if not sell:
        return None
    plan = sell[0]
    if swap and (x.shape.type_label, x.rtype) in swap:
        plan = PLANS[swap[(x.shape.type_label, x.rtype)]]
    got = S.build(x, plan)
    if got and S.gate_ok(got[0], got[1], got[3]):
        stakes, odds, used, mean = got
        return dict(plan=used.key, stakes=stakes, trio=used.bet_type == "trio", mean=mean, n=len(stakes))
    return None


def run(idx, cache, exempt=None, swap=None):
    """R.run の写し（plans={} / cap・highpay あり）。exempt(x)=True のレースは軸信頼ゲートを通す。
    返り値 recs と、レースごとの情報 info[race_key]（main プラン・ゲート通過・axis_sum）。"""
    byday = defaultdict(list)
    for i in idx:
        x = cache[i]
        if x is not None:
            byday[x.date].append(x)
    recs, info = [], {}
    for day, races in sorted(byday.items()):
        cand = []
        for x in races:
            main = race_main(x, swap)
            if main is None:
                info[x.key] = dict(main=None)
                continue
            raw = _G.passes_axis_gate(main["plan"], float(x.shape.axis_sum), 7)
            passes = True if (exempt and exempt(x)) else raw
            info[x.key] = dict(main=main["plan"], raw_pass=raw, axis=float(x.shape.axis_sum),
                               type=x.shape.type_label, rtype=x.rtype)
            cand.append((x, main, passes))
        judged = [c for c in cand if not _G.daily_cap_exempt(c[0].rtype, c[0].cupg)
                  and not _G.daily_cap_exempt_plan(c[1]["plan"])]
        budget = max(1, int(len(judged) * float(_G.DAILY_CAP_RACE_FRACTION))) if judged else None
        order = sorted(cand, key=lambda c: -_G.cap_priority(c[1]["plan"], float(c[0].shape.axis_sum), c[0].rp_sd))
        n_capped, dropped = 0, []
        for x, main, passes in order:
            ex = _G.daily_cap_exempt(x.rtype, x.cupg) or _G.daily_cap_exempt_plan(main["plan"])
            if not passes:
                dropped.append(x); info[x.key]["fate"] = "gate"; continue
            if budget is not None and not ex and n_capped >= budget:
                dropped.append(x); info[x.key]["fate"] = "cap"; continue
            if not ex:
                n_capped += 1
            inv, pay = S.settle(x, main["stakes"], main["trio"])
            recs.append(dict(day=day, plan=main["plan"], inv=inv, pay=pay, mean=main["mean"], n=main["n"],
                             slot="main", race_key=x.key, stakes=main["stakes"], trio=main["trio"], rtype=x.rtype))
            info[x.key]["fate"] = "main"
        n_done = 0
        for x in dropped:
            if n_done >= HIGHPAY_SLOTS_PER_DAY:
                break
            hp = R.highpay_row(x, {}, n_done)
            if not hp:
                continue
            inv, pay = S.settle(x, hp["stakes"], hp["trio"])
            recs.append(dict(day=day, plan=hp["plan"], inv=inv, pay=pay, mean=hp["mean"], n=hp["n"],
                             slot="highpay", race_key=x.key, stakes=hp["stakes"], trio=hp["trio"], rtype=x.rtype))
            info[x.key]["fate"] = "highpay"
            n_done += 1
    return recs, info


def ci(a):
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def roi(rs):
    inv = sum(r["inv"] for r in rs)
    return sum(r["pay"] for r in rs) / inv * 100 if inv else float("nan")


def daymat(rs, days):
    pos = {d: i for i, d in enumerate(days)}
    inv = np.zeros(len(days)); pay = np.zeros(len(days)); big = np.zeros(len(days)); hit = np.zeros(len(days)); n = np.zeros(len(days))
    for r in rs:
        i = pos[r["day"]]
        inv[i] += r["inv"]; pay[i] += r["pay"]; n[i] += 1
        big[i] += r["pay"] >= 100_000; hit[i] += r["pay"] > 0
    return inv, pay, big, hit, n


def main():
    t0 = time.time()
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    print(f"[母集団] board 2025・7車 {len(z['KEY'])}R → 勝ち目・払戻・三連複・vintage PO あり {len(idx)}R", flush=True)
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    keys = {i: str(z["KEY"][i]) for i in ok}
    print(f"ctx 構築 {len(ok)}/{len(idx)}  {time.time()-t0:.0f}s", flush=True)
    finals = [i for i in ok if is_final(cache[i])]
    print(f"[決勝(完全一致)] {len(finals)}R  内訳 {dict((k, sum(1 for i in finals if cache[i].rtype==k)) for k in FINALS)}  開催日 {len({cache[i].date for i in finals})}", flush=True)

    # 欠車確認（決勝のみ）
    import psycopg2
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); con.set_session(readonly=True, autocommit=True)
    cur = con.cursor(); ent = {}
    fk = [keys[i] for i in finals]
    for j in range(0, len(fk), 1500):
        cur.execute("SELECT race_key, array_agg(frame_no ORDER BY frame_no) FROM keirin.wt_entries WHERE race_key = ANY(%s) GROUP BY race_key", (fk[j:j+1500],))
        for k, a in cur.fetchall():
            ent[k] = list(a)
    con.close()
    bad = [k for k in fk if ent.get(k) != list(range(1, 8))]
    print(f"[欠車] 決勝のうち出走車番が 1..7 でない = {len(bad)}", flush=True)

    # 検証: 自前 run(exempt なし) == R.run
    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    rec1, info1 = run(ok, cache)
    assert len(ref) == len(rec1) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in rec1)) < 1e-6 \
        and abs(sum(r["inv"] for r in ref) - sum(r["inv"] for r in rec1)) < 1e-6, "①が lineup_arms.run と一致しない"
    print(f"[検証] ① = lineup_arms.run と一致 ({len(rec1)} 行)", flush=True)
    rec2, info2 = run(ok, cache, exempt=is_final)
    rec3, info3 = run(ok, cache, exempt=is_final, swap={("F", "決勝"): "F_hit", ("F", "チャレンジ決勝"): "F_hit"})
    print(f"②行 {len(rec2)}  副腕行 {len(rec3)}  {time.time()-t0:.0f}s", flush=True)

    fset = {keys[i] for i in finals}
    by1 = {r["race_key"]: r for r in rec1}; by2 = {r["race_key"]: r for r in rec2}; by3 = {r["race_key"]: r for r in rec3}
    f1 = [r for r in rec1 if r["race_key"] in fset]; f2 = [r for r in rec2 if r["race_key"] in fset]
    f3 = [r for r in rec3 if r["race_key"] in fset]
    nonf1 = [r for r in rec1 if r["race_key"] not in fset]; nonf2 = [r for r in rec2 if r["race_key"] not in fset]
    # 分類
    new_keys = [k for k in fset if k in by2 and k not in by1]
    repl_keys = [k for k in fset if k in by2 and k in by1 and by1[k]["slot"] == "highpay"]
    same_keys = [k for k in fset if k in by2 and k in by1 and by1[k]["slot"] == "main"]
    lost_keys = [k for k in fset if k in by1 and k not in by2]
    nosell2 = [k for k in fset if k not in by2]
    print(f"[決勝の売り分け] 総数{len(fset)}  ①売る{len(f1)}(main {sum(r['slot']=='main' for r in f1)} / highpay {sum(r['slot']=='highpay' for r in f1)})"
          f"  ②売る{len(f2)}  新たに売る{len(new_keys)}  ①highpay→②main に置換{len(repl_keys)}  不変main{len(same_keys)}  ①のみ売る{len(lost_keys)}  ②でも売らない{len(nosell2)}", flush=True)
    why = defaultdict(int)
    for k in nosell2:
        i1 = info1.get(k, {})
        why[("商品なし(組めない/入稿ゲート)" if i1.get("main") is None else "fate=" + str(info2[k].get("fate")))] += 1
    print("  ②でも売らない決勝の理由:", dict(why), flush=True)
    gate_drop = [k for k in fset if info1.get(k, {}).get("main") and not info1[k]["raw_pass"]]
    print(f"  ①で軸信頼ゲート落ち(raw)の決勝 = {len(gate_drop)}  うち高額枠で救済 {sum(1 for k in gate_drop if k in by1)}", flush=True)
    pc = defaultdict(int)
    for k in new_keys: pc[(info2[k]["type"], info2[k]["main"])] += 1
    print("  新たに売る決勝の 型×プラン:", dict(sorted(pc.items())), flush=True)
    rc = defaultdict(int)
    for k in new_keys: rc[info2[k]["rtype"]] += 1
    print("  新たに売る決勝の種別:", dict(rc), flush=True)
    pc2 = defaultdict(int)
    for k in repl_keys: pc2[(by1[k]["plan"], "→", by2[k]["plan"])] += 1
    print("  置換(高額枠→main):", dict(pc2), flush=True)
    pc3 = defaultdict(int)
    for r in f1: pc3[r["plan"] + "/" + r["slot"]] += 1
    print("  ①の決勝プラン内訳:", dict(sorted(pc3.items(), key=lambda x: -x[1])), flush=True)
    pc4 = defaultdict(int)
    for r in f2: pc4[r["plan"] + "/" + r["slot"]] += 1
    print("  ②の決勝プラン内訳:", dict(sorted(pc4.items(), key=lambda x: -x[1])), flush=True)

    days_f = sorted({cache[i].date for i in finals})
    days_all = sorted({cache[i].date for i in ok})
    ND = len(days_all)
    rng = np.random.default_rng(20261007)

    def block(name, rs, days=days_all):
        inv, pay, big, hit, n = daymat(rs, days)
        pays = sorted((r["pay"] for r in rs), reverse=True)
        tot_inv = inv.sum()
        print(f"[{name}] R={len(rs)} 日={int((n>0).sum())} 投資={tot_inv:,.0f} 払戻={pay.sum():,.0f} ROI={pay.sum()/tot_inv*100 if tot_inv else float('nan'):.2f}% "
              f"的中(払戻>0)={int(hit.sum())} 表示的中(払戻>投資)={sum(1 for r in rs if r['pay']>r['inv'])}({sum(1 for r in rs if r['pay']>r['inv'])/max(len(rs),1)*100:.2f}%) "
              f"10万+={int(big.sum())}", flush=True)
        for nn in (1, 3, 5):
            print(f"    上位{nn}件除外 ROI={(pay.sum()-sum(pays[:nn]))/tot_inv*100:.2f}%  除外払戻={[round(x) for x in pays[:nn]]}", flush=True)
        return inv, pay, big, hit, n

    print("\n=== 決勝全体 ===", flush=True)
    A1 = block("①決勝", f1); A2 = block("②決勝", f2); A3 = block("副腕決勝", f3)
    nr = [by2[k] for k in new_keys]
    print("\n=== 新たに売る決勝 ===", flush=True)
    AN = block("新規(②)", nr)
    nbig_perday = {}
    # 同一レースでの ① は無いので、新規だけ。比較用: ①で売っていた決勝(不変main)
    sm1 = [by1[k] for k in same_keys]
    ASM = block("不変main(①=②)", sm1)
    rep1 = [by1[k] for k in repl_keys]; rep2 = [by2[k] for k in repl_keys]
    block("置換①(高額枠)", rep1); block("置換②(main)", rep2)

    # ΔROI 決勝全体 と CI
    def dfun(ix, A=A2, B=A1):
        return (A[1][ix].sum() / A[0][ix].sum() - B[1][ix].sum() / B[0][ix].sum()) * 100
    allix = np.arange(ND)
    def boot(f, n=N_BOOT):
        return np.array([f(rng.integers(0, ND, ND)) for _ in range(n)])
    d = dfun(allix); bs = boot(dfun)
    print(f"\n[決勝全体 ΔROI ②−①] {d:+.2f}pt  95%CI [{ci(bs)[0]:+.2f}, {ci(bs)[1]:+.2f}]  ①={A1[1].sum()/A1[0].sum()*100:.2f}% ②={A2[1].sum()/A2[0].sum()*100:.2f}%", flush=True)
    d3 = dfun(allix, A3, A1); bs3 = boot(lambda ix: dfun(ix, A3, A1))
    print(f"[副腕 ΔROI (免除+F決勝をF_hit)−①] {d3:+.2f}pt  [{ci(bs3)[0]:+.2f}, {ci(bs3)[1]:+.2f}]", flush=True)
    # 新規の ROI CI
    rn = lambda ix: AN[1][ix].sum() / AN[0][ix].sum() * 100 if AN[0][ix].sum() else np.nan
    bn = boot(rn)
    print(f"[新規決勝 ROI] {rn(allix):.2f}%  [{np.nanpercentile(bn,2.5):.2f}, {np.nanpercentile(bn,97.5):.2f}]", flush=True)
    # 上位3件除外 の CI
    pays_n = sorted((r["pay"] for r in nr), reverse=True)
    # 上期/下期
    half = np.array([int(d_[5:7]) <= 6 for d_ in days_all])
    print("\n=== 上期/下期 ===", flush=True)
    for lab, msk in (("上期", half), ("下期", ~half)):
        ix = np.flatnonzero(msk)
        f = lambda j, ix=ix: dfun(ix[j])
        dd = f(np.arange(len(ix)))
        bh = np.array([f(rng.integers(0, len(ix), len(ix))) for _ in range(N_BOOT)])
        nn_ = AN[1][ix].sum() / AN[0][ix].sum() * 100 if AN[0][ix].sum() else float("nan")
        hs = [k for k in new_keys if (int(cache[[i for i in finals if keys[i] == k][0]].date[5:7]) <= 6) == (lab == "上期")]
        print(f"[{lab}] 日={len(ix)} 決勝全体 ΔROI={dd:+.2f}pt [{ci(bh)[0]:+.2f},{ci(bh)[1]:+.2f}]  ①={A1[1][ix].sum()/A1[0][ix].sum()*100:.2f}% ②={A2[1][ix].sum()/A2[0][ix].sum()*100:.2f}%  "
              f"新規 R={len(hs)} ROI={nn_:.2f}% 的中={int(AN[3][ix].sum())}", flush=True)

    # 副指標
    sh = lambda rs: sum(1 for r in rs if r["pay"] > r["inv"])
    print("\n=== 副指標 ===", flush=True)
    print(f"決勝 表示的中: ① {sh(f1)}/{len(f1)}={sh(f1)/len(f1)*100:.2f}%  ② {sh(f2)}/{len(f2)}={sh(f2)/len(f2)*100:.2f}%  (新規 {sh(nr)}/{len(nr)}={sh(nr)/max(len(nr),1)*100:.2f}%)  副腕 {sh(f3)}/{len(f3)}={sh(f3)/len(f3)*100:.2f}%", flush=True)
    # 全体(ラインナップ)の変化
    print(f"全ラインナップ: ① {len(rec1)}行 ROI {roi(rec1):.2f}%  ② {len(rec2)}行 ROI {roi(rec2):.2f}%  副腕 {len(rec3)}行 ROI {roi(rec3):.2f}%", flush=True)
    for nm, rs in (("①", rec1), ("②", rec2), ("副腕", rec3)):
        big = sum(1 for r in rs if r["pay"] >= 100_000)
        print(f"  {nm}: 10万+ 全体 {big}件 = {big/ND:.4f}/日(365日)  決勝由来 {sum(1 for r in rs if r['race_key'] in fset and r['pay']>=100_000)}件  表示的中(全体) {sh(rs)/len(rs)*100:.2f}%  件/日 {len(rs)/ND:.2f}", flush=True)
    print(f"  決勝以外の行の変化(高額枠の玉突き): ① {len(nonf1)}行 ROI {roi(nonf1):.2f}%  ② {len(nonf2)}行 ROI {roi(nonf2):.2f}%  (増減 {len(nonf2)-len(nonf1):+d})", flush=True)
    s1 = {r['race_key'] for r in nonf1}; s2 = {r['race_key'] for r in nonf2}
    print(f"  決勝以外で ②にだけある {len(s2-s1)}R / ①にだけある {len(s1-s2)}R", flush=True)
    for lab, A_ in (("①決勝", A1), ("②決勝", A2), ("副腕決勝", A3)):
        print(f"  {lab}: 10万+ {int(A_[2].sum())}件 = {A_[2].sum()/ND:.4f}/日", flush=True)
    bigf = boot(lambda ix: (A2[2][ix].sum() - A1[2][ix].sum()) / len(ix))
    print(f"  決勝由来 10万+/日の差(②−①) = {(A2[2].sum()-A1[2].sum())/ND:+.4f} [{ci(bigf)[0]:+.4f},{ci(bigf)[1]:+.4f}]", flush=True)
    bigf3 = boot(lambda ix: (A3[2][ix].sum() - A1[2][ix].sum()) / len(ix))
    print(f"  決勝由来 10万+/日の差(副腕−①) = {(A3[2].sum()-A1[2].sum())/ND:+.4f} [{ci(bigf3)[0]:+.4f},{ci(bigf3)[1]:+.4f}]", flush=True)
    print(f"[サンプル量] 新規決勝 R={len(nr)} 的中={int(AN[3].sum())} 開催日(新規あり)={int((AN[4]>0).sum())} / 決勝全体②: 的中={int(A2[3].sum())} 開催日={int((A2[4]>0).sum())}", flush=True)

    # L_lead 補足: 新規決勝を L_lead が拾えるか
    pl = PLANS["L_lead"]
    ll = []
    for k in new_keys:
        i = [i for i in finals if keys[i] == k][0]
        x = cache[i]
        got = S.build(x, pl)
        if got and S.gate_ok(got[0], got[1], got[3]):
            inv, pay = S.settle(x, got[0], got[2].bet_type == "trio")
            ll.append(dict(day=x.date, inv=inv, pay=pay, type=x.shape.type_label, k=k))
    ll_ok = [r for r in ll if r["type"] != "E"]
    print(f"\n[L_lead 補足] 新規 {len(new_keys)}R のうち L_lead が組めて入稿ゲートを通る {len(ll)}R（型E除外後 {len(ll_ok)}R）。ROI(型E除外) {roi(ll_ok):.2f}% 的中 {sum(1 for r in ll_ok if r['pay']>0)}  （準決勝系は決勝なので対象外にならない。モーニング除外は台に時刻が無く未反映）", flush=True)
    nr_ll = [by2[r['k']] for r in ll_ok]
    print(f"   同じ {len(nr_ll)}R の ② 商品 ROI = {roi(nr_ll):.2f}%", flush=True)

    # 目視
    print("\n=== 目視 ===", flush=True)
    for lab, kk in (("ゲート落ち→①売らない/②売る(新規)", new_keys[:1]), ("免除でも不変(①main・ゲート通過)", same_keys[:1])):
        for k in kk:
            i = [i for i in finals if keys[i] == k][0]
            x = cache[i]; inf = info2[k]
            fl = _G.AXIS_GATE_MIN.get(inf["main"])
            r2 = by2[k]
            print(f"{lab}: {k} {x.rtype} 型{inf['type']} plan={inf['main']} axis_sum={inf['axis']:.4f} 下限={fl} ①ゲート通過={info1[k]['raw_pass']} ①fate={info1[k].get('fate')} ②fate={inf.get('fate')}", flush=True)
            top = sorted(r2["stakes"].items(), key=lambda kv: -kv[1])[:5]
            print(f"   ②買い目 {len(r2['stakes'])}点 投資{r2['inv']:,.0f} 払戻{r2['pay']:,.0f} 計画平均払戻{r2['mean']:,.0f} 上位賭け金{[(str(c), s) for c, s in top]}", flush=True)
    pickle.dump(dict(rec1=rec1, rec2=rec2, rec3=rec3, info1=info1, info2=info2, new_keys=new_keys, repl_keys=repl_keys,
                     fset=fset, days_all=days_all), open(D / "h03_result.pkl", "wb"))
    print(f"完了 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
