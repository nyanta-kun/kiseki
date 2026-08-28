#!/usr/bin/env python3
"""型F を「軸1と軸2の乖離」で F_hit / F_pay に振り分けられるか（2026-08-28・ユーザー提案）。

乖離は **行に焼き付いた買い目の確率から導く**（後から引き直さない）:
    F_hit は `all6`（軸2車+相手2車の6順列すべて）なので、買い目の prob を
    「1着が軸1の目」「1着が軸2の目」に振り分けられる。
        d = (P(1着=軸1) - P(1着=軸2)) / (P(1着=軸1) + P(1着=軸2))
    d が大きい = 軸1が抜けている。**同じレースの F_pay 行にも同じ d を当てる。**

🔴 モデルを引き直さないのが要点。`p3_order` と同じ理由で、後から再計算すると
   当時と違う並び・違う確率になる（RUNBOOK の食い違い 34% の件）。
"""
from __future__ import annotations
import importlib.util, json, os, random, statistics
from pathlib import Path
import psycopg2, psycopg2.extras

REPO = Path(__file__).resolve().parents[3]
_s = importlib.util.spec_from_file_location("g", REPO / "backend/src/services/keirin_type_lab_gate.py")
_g = importlib.util.module_from_spec(_s); _s.loader.exec_module(_g)
gate = _g.passes_axis_gate

EXPLORE = ("2025-01-01", "2025-12-31"); CONFIRM = ("2026-01-01", "2026-08-26")

def fetch(mode):
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT race_key, race_date::text AS race_date, race_type, plan_key,"
                        " n_entries, axis_sum, axis1, axis2, budget, settled_at, hit, payout, legs"
                        " FROM keirin.type_lab_picks WHERE mode=%s AND plan_key IN ('F_hit','F_pay')", (mode,))
            return [dict(r) for r in cur.fetchall()]

def legs_of(r):
    v = r["legs"]
    return json.loads(v) if isinstance(v, str) else (v or [])

def divergence(fhit_row):
    a1, a2 = int(fhit_row["axis1"]), int(fhit_row["axis2"])
    p1 = p2 = 0.0
    for lg in legs_of(fhit_row):
        head = lg["combo"].split("-")[0]
        if head == str(a1): p1 += float(lg.get("prob") or 0)
        elif head == str(a2): p2 += float(lg.get("prob") or 0)
    return (p1 - p2) / (p1 + p2) if (p1 + p2) > 0 else None

def pairs_with_d(rows, lo, hi, use_gate=True):
    f = [r for r in rows if lo <= r["race_date"] <= hi and r["settled_at"] is not None]
    if use_gate:
        f = [r for r in f if gate(r["plan_key"], float(r["axis_sum"]) if r["axis_sum"] is not None else None, r["n_entries"])]
    by = {}
    for r in f: by.setdefault(r["race_key"], {})[r["plan_key"]] = r
    out = []
    for v in by.values():
        if len(v) != 2: continue
        d = divergence(v["F_hit"])
        if d is None: continue
        out.append((d, v["F_hit"], v["F_pay"]))
    out.sort(key=lambda x: x[0])
    return out

def m(rows):
    if not rows: return dict(n=0, shown=0.0, med=0, big=0, roi=0.0)
    sh = [r for r in rows if r["hit"] and int(r["payout"] or 0) > int(r["budget"])]
    big = [r for r in rows if int(r["payout"] or 0) >= 100_000]
    inv = sum(int(r["budget"]) for r in rows); ret = sum(int(r["payout"] or 0) for r in rows)
    return dict(n=len(rows), shown=len(sh)/len(rows)*100,
                med=statistics.median([int(r["payout"] or 0) for r in sh]) if sh else 0,
                big=len(big), roi=ret/inv*100 if inv else 0.0)

def boot_diff(pr, pick, n=2000, seed=11):
    """pick(d) -> 'F_hit' | 'F_pay' の割り当て規則 vs 全部F_hit / 全部F_pay の ROI・表示的中差。"""
    rnd = random.Random(seed)
    def score(sample):
        sel = [(h if pick(d) == "F_hit" else p) for d, h, p in sample]
        return m(sel)["shown"], m(sel)["roi"], m([h for _,h,_ in sample])["shown"], m([h for _,h,_ in sample])["roi"], \
               m([p for _,_,p in sample])["shown"], m([p for _,_,p in sample])["roi"]
    obs = score(pr)
    ds, dr = [], []
    for _ in range(n):
        s = [pr[rnd.randrange(len(pr))] for _ in range(len(pr))]
        v = score(s); ds.append(v[0]-v[4]); dr.append(v[1]-v[5])
    ds.sort(); dr.sort()
    return obs, (ds[int(n*.025)], ds[int(n*.975)]), (dr[int(n*.025)], dr[int(n*.975)])

for mode, cars in (("paper", "7車"), ("paper9", "9車")):
    rows = fetch(mode)
    wins = (("探索窓 2025", EXPLORE), ("確認窓 2026", CONFIRM)) if mode == "paper" else (("全期間", ("2025-01-01","2026-08-31")),)
    for wn, w in wins:
        pr = pairs_with_d(rows, *w)
        if len(pr) < 50: print(f"\n{cars} {wn}: n={len(pr)} — 少なすぎ"); continue
        print(f"\n{'='*104}\n{cars} {wn} — 型F {len(pr):,}R を乖離 d の5分位で分割（軸信頼ゲートあと）\n{'='*104}")
        print(f"{'d 帯':>16} {'n':>5}  {'F_hit 表示的中':>13} {'払戻中央':>9} {'10万+':>6} {'ROI':>7}   "
              f"{'F_pay 表示的中':>13} {'払戻中央':>9} {'10万+':>6} {'ROI':>7}")
        q = len(pr)//5
        for i in range(5):
            seg = pr[i*q:(i+1)*q] if i < 4 else pr[4*q:]
            h, p = m([x[1] for x in seg]), m([x[2] for x in seg])
            print(f"  Q{i+1} [{seg[0][0]:+.3f},{seg[-1][0]:+.3f}] {len(seg):5,}  "
                  f"{h['shown']:12.2f}% {h['med']:9,.0f} {h['big']:6d} {h['roi']:6.1f}%   "
                  f"{p['shown']:12.2f}% {p['med']:9,.0f} {p['big']:6d} {p['roi']:6.1f}%")
        med = statistics.median([d for d,_,_ in pr])
        for name, pick in (("ユーザー案 乖離大→F_hit / 小→F_pay", lambda d, mm=med: "F_hit" if d >= mm else "F_pay"),
                           ("逆案     乖離大→F_pay / 小→F_hit", lambda d, mm=med: "F_pay" if d >= mm else "F_hit")):
            obs, ci_s, ci_r = boot_diff(pr, pick)
            print(f"  {name}: 表示的中 {obs[0]:.2f}% / ROI {obs[1]:.1f}%   "
                  f"vs 全F_pay 差 表示的中 {obs[0]-obs[4]:+.2f}pt CI[{ci_s[0]:+.2f},{ci_s[1]:+.2f}] "
                  f"ROI {obs[1]-obs[5]:+.1f}pt CI[{ci_r[0]:+.1f},{ci_r[1]:+.1f}]")
        print(f"  参考: 全部F_hit 表示的中 {m([x[1] for x in pr])['shown']:.2f}% / ROI {m([x[1] for x in pr])['roi']:.1f}%"
              f"   全部F_pay 表示的中 {m([x[2] for x in pr])['shown']:.2f}% / ROI {m([x[2] for x in pr])['roi']:.1f}%"
              f"   （d 中央値 {med:+.3f}）")
