#!/usr/bin/env python3
"""事後の診断（事前登録の判定には使わない）。

1. 台の current が売った目の 実際/Σp（PL 板）と 実際/Σp*（M2）。ライブ実売の 0.87 を台が再現するか
2. 表示的中率と計画払戻の関係: 市場が効率的なら P(的中) ≈ 0.75 × 賭け金 / 計画払戻

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/board_calib/diag.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arms as A  # noqa: E402
import lineup_arms as R  # noqa: E402
import lineup_sim as S  # noqa: E402


def main():
    z = A.load_board()
    coef = json.load(open(A.D / "board_calib_coef.json"))
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
         & z["OKPRED"] & (z["DATE"] >= "2026-01-01") & (z["DATE"] <= "2026-08-04"))
    idx = [int(i) for i in np.flatnonzero(m)]
    base = {i: S.ctx(i) for i in idx}
    ps = A.pstar_rows(coef, "M2")
    ok = [i for i in idx if base[i] is not None and i in ps]
    c2 = {i: A.with_probs(base[i], ps[i]) for i in ok}
    bykey = {base[i].key: i for i in ok}
    recs = R.run("current", {}, ok, base)
    g = defaultdict(lambda: dict(n=0, hit=0, shown=0, sp=0.0, sps=0.0, mean=[]))
    for r in recs:
        i = bykey[r["race_key"]]
        x, y = base[i], c2[i]
        pr, prs = (x.pr_t3, y.pr_t3) if r["trio"] else (x.pr_tf, y.pr_tf)
        win = x.win_t3 if r["trio"] else x.win_tf
        for k in (r["plan"], "全体"):
            d = g[k]
            d["n"] += 1; d["hit"] += win in r["stakes"]; d["shown"] += r["pay"] > r["inv"]
            d["sp"] += sum(pr.get(c, 0.0) for c in r["stakes"])
            d["sps"] += sum(prs.get(c, 0.0) for c in r["stakes"])
            d["mean"].append(r["mean"])
    print(f"{'プラン':<8}{'件':>6}{'的中(ガミ込)':>12}{'実/Σp':>8}{'実/Σp*':>8}{'表示的中':>9}{'計画払戻中央':>12}{'0.75×1万/計画':>14}")
    for k, d in sorted(g.items(), key=lambda kv: -kv[1]["n"]):
        med = float(np.median(d["mean"]))
        print(f"{k:<8}{d['n']:>6}{d['hit'] / d['n'] * 100:>11.1f}%{d['hit'] / d['sp']:>8.2f}{d['hit'] / d['sps']:>8.2f}"
              f"{d['shown'] / d['n'] * 100:>8.1f}%{med:>12,.0f}{0.75 * 10000 / med * 100:>13.1f}%")


if __name__ == "__main__":
    main()
