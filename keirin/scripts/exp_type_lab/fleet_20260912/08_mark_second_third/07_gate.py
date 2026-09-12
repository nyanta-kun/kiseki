#!/usr/bin/env python3
"""① 入稿ゲート通過率の表（プール × 計画払戻）
   ② 「1着が◎○以外になりやすい」側だけに絞ったときの対比較（③選別との掛け合わせ）

②の絞りは台にある量だけで作る（予測可能性そのものは別エージェントの担当）:
   `pw_ent`（1着率のエントロピー・`A_ana` の選別量）の上位 10% / 25%
   `axis_sum` の下位 25%（軸が薄い側）
"""
from __future__ import annotations
import sys
import numpy as np
from importlib import import_module
sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                   "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/"
                   "08_mark_second_third")
C3 = import_module("03_cmp")
ROWS, NDAYS, WINS = C3.ROWS, C3.NDAYS, C3.WINS
kpi, line, HEAD, boot2, _mk = C3.kpi, C3.line, C3.HEAD, C3.boot2, C3._mk

POOLS = ("all", "bust", "Mall", "N", "N_hon", "N_pw2", "M")
TS = (20_000, 50_000, 100_000, 150_000, 400_000)


def gate():
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win]
        n = len(rs)
        print(f"\n=== {lbl}  n={n:,}R — 入稿ゲート通過率（組めて2条件を通る割合）===")
        print("  " + f"{'プール':8s}" + "".join(f"{t:>12,}" for t in TS))
        for p in POOLS:
            row = []
            for t in TS:
                k = f"{p}@{t}k0"
                row.append(sum(1 for r in rs if k in r["arms"]) / n * 100)
            print(f"  {p:8s}" + "".join(f"{v:11.1f}%" for v in row))
        print("  （現行の当てにいく商品は帯・点数が固定なので、この表の `all`"
              " 列が「制限なしで同じ計画払戻を狙ったときの通過率」）")


def cond(tag: str, *arms):
    ex = [r for r in ROWS if r["win"] == "explore"]
    # 🔴 閾値は**探索窓の分位**で作る（確認窓を閾値決めに使わない）。
    pe = np.percentile([r["pw_ent"] for r in ex], [75, 90])
    ax = np.percentile([r["axis_sum"] for r in ex], 25)
    tests = {
        "pw_ent 上位10%": lambda r: r["pw_ent"] >= pe[1],
        "pw_ent 上位25%": lambda r: r["pw_ent"] >= pe[0],
        "axis_sum 下位25%": lambda r: r["axis_sum"] <= ax,
    }
    f = tests[tag]
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win and f(r)]
        nd = NDAYS[win]
        base = arms[0]
        print(f"\n=== {lbl}  絞り={tag}  n={len(rs):,}R "
              f"（1着が◎○以外 {np.mean([r['first_out'] for r in rs])*100:.2f}%）===")
        print(HEAD)
        print(line(base, kpi([r["arms"][base] for r in rs if base in r["arms"]], nd)))
        for a in arms[1:]:
            pairs = [(r["arms"][base], r["arms"][a]) for r in rs
                     if base in r["arms"] and a in r["arms"]]
            if len(pairs) < 30:
                continue
            print(line(a, kpi([p[1] for p in pairs], nd)))
            res = boot2(_mk([p[0] for p in pairs]), _mk([p[1] for p in pairs]))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()) + f"  (n={len(pairs)})")


if __name__ == "__main__":
    if sys.argv[1] == "gate":
        gate()
    else:
        cond(sys.argv[2], *sys.argv[3:])
