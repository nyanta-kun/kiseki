#!/usr/bin/env python3
"""型Fの hit/pay 振り分けに **無作為対照** を置く（2026-08-28）。

🔴 「半分を F_hit に替える」だけで表示的中は必ず上がる。d による分割が
   意味を持つのは **同数を無作為に振り分けた対照に勝つとき**だけ。
   （`race_filter_2026_08_27.md` が「件数を動かす検証には必ず無作為対照」と
   結論した件と同型。ここは件数でなく構成の入れ替え。）
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
            cur.execute("SELECT race_key, race_date::text AS race_date, plan_key, n_entries,"
                        " axis_sum, axis1, axis2, budget, settled_at, hit, payout, legs"
                        " FROM keirin.type_lab_picks WHERE mode=%s AND plan_key IN ('F_hit','F_pay')", (mode,))
            return [dict(r) for r in cur.fetchall()]

def div(r):
    a1, a2 = str(r["axis1"]), str(r["axis2"]); p1 = p2 = 0.0
    lg = r["legs"]; lg = json.loads(lg) if isinstance(lg, str) else (lg or [])
    for x in lg:
        h = x["combo"].split("-")[0]
        if h == a1: p1 += float(x.get("prob") or 0)
        elif h == a2: p2 += float(x.get("prob") or 0)
    return (p1 - p2) / (p1 + p2) if p1 + p2 > 0 else None

def prep(rows, lo, hi):
    f = [r for r in rows if lo <= r["race_date"] <= hi and r["settled_at"] is not None
         and gate(r["plan_key"], float(r["axis_sum"]) if r["axis_sum"] is not None else None, r["n_entries"])]
    by = {}
    for r in f: by.setdefault(r["race_key"], {})[r["plan_key"]] = r
    out = []
    for v in by.values():
        if len(v) != 2: continue
        d = div(v["F_hit"])
        if d is None: continue
        pack = lambda r: (1 if (r["hit"] and int(r["payout"] or 0) > int(r["budget"])) else 0,
                          int(r["payout"] or 0), int(r["budget"]))
        out.append((d, pack(v["F_hit"]), pack(v["F_pay"])))
    return out

def score(sel):
    n = len(sel)
    return (sum(s for s, _, _ in sel) / n * 100,
            sum(p for _, p, _ in sel) / sum(b for _, _, b in sel) * 100,
            sum(1 for _, p, _ in sel if p >= 100_000) )

for mode, cars, wins in (("paper", "7車", (("探索窓 2025", EXPLORE), ("確認窓 2026", CONFIRM))),
                         ("paper9", "9車", (("全期間", ("2025-01-01", "2026-08-31")),))):
    rows = fetch(mode)
    for wn, w in wins:
        pr = prep(rows, *w)
        n = len(pr); half = n // 2
        srt = sorted(pr, key=lambda x: x[0])
        allh = score([h for _, h, _ in pr]); allp = score([p for _, _, p in pr])
        user = score([h for _, h, _ in srt[half:]] + [p for _, _, p in srt[:half]])   # 乖離大→hit
        inv  = score([p for _, _, p in srt[half:]] + [h for _, h, _ in srt[:half]])   # 乖離大→pay
        ctl = []
        for seed in range(20):
            rnd = random.Random(seed); idx = list(range(n)); rnd.shuffle(idx)
            pick = set(idx[:half])
            ctl.append(score([(pr[i][1] if i in pick else pr[i][2]) for i in range(n)]))
        cs = sorted(c[0] for c in ctl); cr = sorted(c[1] for c in ctl); cb = sorted(c[2] for c in ctl)
        print(f"\n=== {cars} {wn}  型F {n:,}R（軸信頼ゲートあと・半分を F_hit に置換）===")
        print(f"{'腕':28} {'表示的中':>9} {'ROI':>8} {'10万+件':>8}")
        print(f"{'全部 F_hit':28} {allh[0]:8.2f}% {allh[1]:7.1f}% {allh[2]:8d}")
        print(f"{'全部 F_pay':28} {allp[0]:8.2f}% {allp[1]:7.1f}% {allp[2]:8d}")
        print(f"{'無作為 20本 中央':28} {statistics.median(cs):8.2f}% {statistics.median(cr):7.1f}% {statistics.median(cb):8.0f}"
              f"   [min {min(cs):.2f}%,{min(cr):.1f}%  max {max(cs):.2f}%,{max(cr):.1f}%]")
        for nm, v in (("ユーザー案 乖離大→F_hit", user), ("逆案     乖離大→F_pay", inv)):
            wins_s = sum(1 for c in ctl if v[0] > c[0]); wins_r = sum(1 for c in ctl if v[1] > c[1])
            print(f"{nm:28} {v[0]:8.2f}% {v[1]:7.1f}% {v[2]:8d}   無作為対照に 表示的中 {wins_s}/20 勝ち・ROI {wins_r}/20 勝ち")
