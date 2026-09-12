#!/usr/bin/env python3
"""重ね買い（下帯8割＋上帯2割）— 無作為対照 20seed つき。

🔴 対照が要る理由: 「N の制限（1着を◎○以外にして◎○を2・3着へ）が効いている」のか
   「未購入の目を4点足しただけ」なのかを分けるため。`overlay_upper_band_2026_09_04`
   の `perm` 版（+0.21〜0.64pt）と同じ枠組みで、上帯の中身だけを替えている。
"""
from __future__ import annotations

import sys
from importlib import import_module

import numpy as np

sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                   "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/"
                   "08_mark_second_third")
C3 = import_module("03_cmp")
ROWS, NDAYS, WINS = C3.ROWS, C3.NDAYS, C3.WINS
kpi, line, HEAD, boot2, _mk = C3.kpi, C3.line, C3.HEAD, C3.boot2, C3._mk

ARMS = ("Mx2", "Mx4", "Nx2", "Nx4", "allx2", "allx4", "bustx4")


def main():
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win and r["base"]
              and r["base"]["gate"] and r["base"]["axis_ok"]
              and not r["base"]["trio"]]
        nd = NDAYS[win]
        print(f"\n{'='*150}\n=== {lbl} — 重ね買い（三連単プランのみ・n={len(rs):,}）===")
        print(HEAD)
        cur = [r["base"] for r in rs]
        print(line("現行（上帯なし）", kpi(cur, nd)))
        base_mk = _mk(cur)
        for a in ARMS:
            recs = [r["ov"].get(a) or r["base"] for r in rs]
            ap = sum(1 for r in rs if a in r["ov"]) / len(rs) * 100
            print(line(f"{a} (適用{ap:.0f}%)", kpi(recs, nd)))
            res = boot2(base_mk, _mk(recs))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()))
        # ── 無作為対照 ──
        for m in (2, 4):
            ctl = []
            for sd in range(20):
                k = f"rnd{m}s{sd}"
                recs = [r["ov"].get(k) or r["base"] for r in rs]
                s = kpi(recs, nd)
                ctl.append((s["shown"], s["bigr"], s["roi"]))
            sh = sorted(c[0] for c in ctl)
            bg = sorted(c[1] for c in ctl)
            ro = sorted(c[2] for c in ctl)
            print(f"\n  -- 無作為 {m}点 対照 20seed --")
            print(f"     表示的中  中央 {sh[10]:.2f}  範囲 [{sh[0]:.2f}, {sh[-1]:.2f}]")
            print(f"     10万+率   中央 {bg[10]:.2f}  範囲 [{bg[0]:.2f}, {bg[-1]:.2f}]")
            print(f"     ROI       中央 {ro[10]:.1f}  範囲 [{ro[0]:.1f}, {ro[-1]:.1f}]")
            for a in (f"Mx{m}", f"Nx{m}", f"allx{m}"):
                if not any(a in r["ov"] for r in rs):
                    continue
                s = kpi([r["ov"].get(a) or r["base"] for r in rs], nd)
                w = sum(1 for c in ctl if s["shown"] > c[0])
                wb = sum(1 for c in ctl if s["bigr"] > c[1])
                print(f"     {a}: 表示的中 {s['shown']:.2f} → 対照に {w}/20 勝ち"
                      f"   10万+率 {s['bigr']:.2f} → {wb}/20 勝ち")
        print(f"\n  （投資/日は全腕で {sum(r['base']['inv'] for r in rs)/nd:,.0f}円・"
              f"件/日 {len(rs)/nd:.2f} で不変）")


if __name__ == "__main__":
    main()
