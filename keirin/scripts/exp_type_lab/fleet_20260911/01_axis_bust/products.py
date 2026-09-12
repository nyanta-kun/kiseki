#!/usr/bin/env python3
"""板の全レースについて「現行の型」と「堅い/混戦を反転した型」の両方で商品を組む。

本番の関数で組む（`typef_racetype.ctx` → `_plan_for` → `_run_named`）。
型判定を差し替える腕は firm だけを動かし arare(s) は据え置くので、
1レースにつき 現行ラベル と 反転ラベル（A↔D / B↔E / C↔F）の2通りで足りる。

出力: products.pkl  {i: {"cur": row|None, "flip": row|None, "plan_cur":..., "plan_flip":...}}
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for, _run_named  # noqa: E402
from src.type_lab import SIGNBOARD_RACE_TYPES, RaceShape  # noqa: E402

HERE = Path(__file__).resolve().parent
FLIP = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C"}


def main() -> None:
    t0 = time.time()
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    idx = [int(i) for i in C.select(None, "all") if tp[int(i)] in "ABCDEF"]
    out = {}
    for n, i in enumerate(idx):
        x = ctx(i)
        if x is None:
            continue
        rec = {}
        for tag, lab in (("cur", tp[i]), ("flip", FLIP[tp[i]])):
            sh = x.shape
            x.shape = RaceShape(lab, sh.axis_sum, sh.arare, sh.gap, lab in "ABC", sh.order,
                                sh.pw_ent, sh.lines, sh.line_pair)
            trio_ok = (_run_named(x, "A_trio") is not None) if lab == "A" else False
            key = _plan_for(lab, rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            r = _run_named(x, key)
            rec[tag] = r
            rec[f"plan_{tag}"] = key
            x.shape = sh
        out[i] = rec
        if n % 4000 == 0:
            print(f"  {n:,}/{len(idx):,}  ({time.time()-t0:.0f}s)", flush=True)
    pickle.dump(out, (HERE / "products.pkl").open("wb"))
    print(f"→ products.pkl  {len(out):,}R  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
