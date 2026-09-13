#!/usr/bin/env python3
"""ラインの強さは「総和」か「弱い1車を除いた残り」か（2026-09-14・ユーザー質問）。

3車ラインの最後尾は構造的に来ない（3着内率 29.16/29.34% ↔ 先頭59.0/番手60.7・
`line_conditional_summary_2026_09_14.md` §9）。ならばラインの強さを測るとき、
その1車を**含める（総和）**べきか、**外して2車ラインとみなす**べきか。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/line_strength_def.py auc|rank|size
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

_R = None


def load():
    global _R
    if _R is None:
        with open("/tmp/ratebranch/rows.pkl", "rb") as f:
            _R = pickle.load(f)
    return _R


def lines_of(r):
    d = defaultdict(list)
    for c in range(1, 8):
        d[str(r["lg"][c - 1])].append((float(r["lpos"][c - 1]), c))
    return {k: [c for _, c in sorted(v)] for k, v in d.items()}


def defs(rp, cars):
    """ラインの強さの定義いろいろ。rp は車番順の競走得点。"""
    v = sorted((float(rp[c - 1]) for c in cars), reverse=True)
    return {
        "総和（全車）": float(np.sum(v)),
        "平均（全車）": float(np.mean(v)),
        "上位2車の和（弱い1車を除く）": float(np.sum(v[:2])),
        "上位2車の平均": float(np.mean(v[:2])),
        "最強1車": float(v[0]),
        "最弱を除いた和": float(np.sum(v[:-1])) if len(v) > 1 else float(v[0]),
    }


def _auc(x, y):
    x = np.asarray(x, float); y = np.asarray(y, bool)
    if y.all() or not y.any():
        return float("nan")
    r = np.argsort(np.argsort(x)) + 1
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def units(size=3):
    out = []
    for r in load():
        fin = set(r["fin"])
        for g, cars in lines_of(r).items():
            if len(cars) != size:
                continue
            d = defs(r["rp"], cars)
            n3 = sum(1 for c in cars if c in fin)
            out.append(dict(win=r["win"], race_key=r["race_key"], g=g, cars=cars,
                            n_in3=n3, two_plus=n3 >= 2, win1=(r["fin"][0] in cars),
                            **d))
    return out


DEF_NAMES = ["総和（全車）", "平均（全車）", "上位2車の和（弱い1車を除く）",
             "上位2車の平均", "最強1車", "最弱を除いた和"]


def auc():
    print("§1 3車ラインの強さの定義 — 何を当てられるか（AUC・0.5=情報なし）\n")
    u = units(3)
    for tgt, lab in (("two_plus", "そのラインから2車以上が3着以内"),
                     ("win1", "そのラインが1着を取る")):
        print(f"── {lab}")
        print(f"{'定義':30s}{'確認':>9}{'探索':>9}")
        for d in DEF_NAMES:
            row = f"{d:30s}"
            for w in ("confirm", "explore"):
                rs = [x for x in u if x["win"] == w]
                row += f"{_auc([x[d] for x in rs], [x[tgt] for x in rs]):9.3f}"
            print(row)
        for w in ("confirm", "explore"):
            rs = [x for x in u if x["win"] == w]
            print(f"  {w}: n={len(rs):,}  実際に起きる率 {np.mean([x[tgt] for x in rs])*100:.2f}%")
        print()


def rank():
    """レース内でラインを強い順に並べたとき、どの定義が『2車以上入るライン』を1位に置くか。"""
    print("§2 レース内でラインを並べたときの当て率（3車ラインを含むレースのみ）\n")
    by = defaultdict(lambda: defaultdict(list))
    for r in load():
        ls = lines_of(r)
        multi = {g: c for g, c in ls.items() if len(c) >= 2}
        if len(multi) < 2 or not any(len(c) == 3 for c in multi.values()):
            continue
        fin = set(r["fin"])
        best = {g: sum(1 for c in cars if c in fin) for g, cars in multi.items()}
        top = max(best.values())
        if top < 2:
            continue
        truth = {g for g, v in best.items() if v == top}
        for d in DEF_NAMES:
            s = {g: defs(r["rp"], cars)[d] for g, cars in multi.items()}
            pick = max(s, key=lambda g: s[g])
            by[r["win"]][d].append(pick in truth)
    print(f"{'定義':30s}{'確認':>11}{'探索':>11}")
    for d in DEF_NAMES:
        row = f"{d:30s}"
        for w in ("confirm", "explore"):
            v = by[w][d]
            row += f"{np.mean(v)*100:10.2f}%"
        print(row)
    for w in ("confirm", "explore"):
        print(f"  {w}: n={len(by[w][DEF_NAMES[0]]):,}レース")


def size():
    """ライン規模ごとに『最弱を除く』が効くか。"""
    print("\n§3 ライン規模別 — 『最弱を除いた和』は『総和』より当たるか"
          "（AUC: そのラインから2車以上が3着以内）\n")
    print(f"{'規模':>6}{'窓':>10}{'n':>8}{'総和':>9}{'最弱を除いた和':>15}{'上位2車の和':>13}{'差':>9}")
    for s in (2, 3, 4):
        u = units(s)
        for w in ("confirm", "explore"):
            rs = [x for x in u if x["win"] == w]
            if len(rs) < 100:
                continue
            a = _auc([x["総和（全車）"] for x in rs], [x["two_plus"] for x in rs])
            b = _auc([x["最弱を除いた和"] for x in rs], [x["two_plus"] for x in rs])
            c = _auc([x["上位2車の和（弱い1車を除く）"] for x in rs], [x["two_plus"] for x in rs])
            print(f"{s:>5}車{w:>10}{len(rs):8,d}{a:9.3f}{b:15.3f}{c:13.3f}{b-a:+9.3f}")


if __name__ == "__main__":
    {"auc": auc, "rank": rank, "size": size}[sys.argv[1]]()
