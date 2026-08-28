#!/usr/bin/env python3
"""Phase 0 補足 — F_pay ↔ F_hit の paired 検定と n / 9車の実数（読むだけ）。"""
from __future__ import annotations
import importlib.util, os, random, statistics
from pathlib import Path
import psycopg2, psycopg2.extras

REPO = Path(__file__).resolve().parents[3]
_s = importlib.util.spec_from_file_location("g", REPO / "backend/src/services/keirin_type_lab_gate.py")
_g = importlib.util.module_from_spec(_s); _s.loader.exec_module(_g)
gate = _g.passes_axis_gate

EXPLORE = ("2025-01-01", "2025-12-31"); CONFIRM = ("2026-01-01", "2026-08-26")
USER5 = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit"]

def fetch(mode):
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT race_key, race_date::text AS race_date, race_type, plan_key,"
                        " n_entries, axis_sum, budget, settled_at, hit, payout"
                        " FROM keirin.type_lab_picks WHERE mode=%s", (mode,))
            return [dict(r) for r in cur.fetchall()]

def paired(rows, lo, hi, use_gate):
    """同一レース上の F_hit / F_pay を組にする。"""
    f = [r for r in rows if r["plan_key"] in ("F_hit", "F_pay")
         and lo <= r["race_date"] <= hi and r["settled_at"] is not None]
    if use_gate:
        f = [r for r in f if gate(r["plan_key"], float(r["axis_sum"]) if r["axis_sum"] is not None else None, r["n_entries"])]
    by = {}
    for r in f:
        by.setdefault(r["race_key"], {})[r["plan_key"]] = r
    return [(v["F_hit"], v["F_pay"]) for v in by.values() if len(v) == 2]

def boot(pairs, fn, n=2000, seed=7):
    rnd = random.Random(seed)
    obs = fn(pairs)
    out = []
    for _ in range(n):
        s = [pairs[rnd.randrange(len(pairs))] for _ in range(len(pairs))]
        out.append(fn(s))
    out.sort()
    return obs, out[int(n * .025)], out[int(n * .975)]

def d_shown(pairs):
    sh = lambda r: 1 if (r["hit"] and int(r["payout"] or 0) > int(r["budget"])) else 0
    return (sum(sh(p) for _, p in pairs) - sum(sh(h) for h, _ in pairs)) / len(pairs) * 100

def d_roi(pairs):
    inv = sum(int(h["budget"]) for h, _ in pairs)
    return (sum(int(p["payout"] or 0) for _, p in pairs) - sum(int(h["payout"] or 0) for h, _ in pairs)) / inv * 100

for wn, w in (("探索窓 2025", EXPLORE), ("確認窓 2026", CONFIRM)):
    print(f"\n=== 型F 直接対決（同一レース・paired bootstrap 2000回）— {wn} ===")
    rows = fetch("paper")
    for gn, ug in (("ゲートなし", False), ("ゲートあり", True)):
        pr = paired(rows, *w, ug)
        o1, l1, h1 = boot(pr, d_shown); o2, l2, h2 = boot(pr, d_roi)
        print(f"  {gn}  n={len(pr):,}R   表示的中 F_pay−F_hit = {o1:+.2f}pt CI[{l1:+.2f},{h1:+.2f}]"
              f"   ROI 差 = {o2:+.2f}pt CI[{l2:+.2f},{h2:+.2f}]")

p9 = fetch("paper9")
print("\n=== 9車 決勝の実数 ===")
for pk in ("F_hit", "F_pay"):
    s = [r for r in p9 if r["plan_key"] == pk and str(r["race_type"] or "") == "決勝" and r["settled_at"] is not None]
    sh = [r for r in s if r["hit"] and int(r["payout"] or 0) > int(r["budget"])]
    print(f"  {pk}: 採点済み {len(s)}件 / 表示的中 {len(sh)}件 "
          f"({len(sh)/len(s)*100 if s else 0:.2f}%) / 払戻合計 {sum(int(r['payout'] or 0) for r in s):,}円")

print("\n=== 9車 型F以外（A~E hit）の実数 ===")
s = [r for r in p9 if r["plan_key"] in USER5 and r["settled_at"] is not None]
d = {r["race_date"] for r in p9 if r["plan_key"] in USER5}
print(f"  採点済み {len(s)}件 / {len(d)}日 = {len(s)/len(d):.2f}件/日")
