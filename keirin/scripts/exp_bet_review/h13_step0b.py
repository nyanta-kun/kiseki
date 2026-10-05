#!/usr/bin/env python3
"""H13 Step0b: 「B を取るとしたライン」の 先頭→番手 1-2着 の、最終オッズに対する発生倍率。

  腕N = 新モデルの B 予測最大の選手が属するライン / 腕F = 本番の隊列推定で最前（formation_pos_frac 最小）のライン。
  先頭 = line_pos 1、番手 = line_pos 2（各1人ずつ明示・2車以上のライン）。無ければその腕のそのレースは対象外。
  発生倍率 = Σ実的中 / Σ期待,  期待 = Σ_z c/final_odds(先頭,番手,z),  c = 1/Σ_210(1/final_odds)（h01_step0 の fin_c と同一）。
  副: 0.75/final_odds 版（fin_075）。確定板 h01_final_tf_2025.npz（欠け <15% のレースのみ）。
  差 = 腕N − 腕F の倍率差（開催日ブートストラップ 2,000 回・両腕とも同じ日を引く）。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from h13_features import D
import itertools
PERMS = list(itertools.permutations(range(1, 8), 3)); PIDX = {p: i for i, p in enumerate(PERMS)}
SEED, NB = 20261005, 2000


def lead_dep(s, line):
    m = s[s.line_group == line]
    a = m[m.line_pos == 1]; b = m[m.line_pos == 2]
    if len(m) >= 2 and len(a) == 1 and len(b) == 1:
        return int(a.frame_no.iloc[0]), int(b.frame_no.iloc[0])
    return None


def ratio_boot(di, nd, cols_h, cols_e, mask_pairs):
    pass


def main():
    te = pd.read_pickle(D / "pred_2025.pkl")
    z = np.load(D.parent / "race_type_board.npz", allow_pickle=True)
    m = (z["DATE"] >= "2025-01-01") & (z["DATE"] <= "2025-12-31")
    KEY = z["KEY"][m].astype(str); WIN = z["WIN"][m]; DATE = z["DATE"][m]
    fz = np.load(D.parent / "h01_final_tf_2025.npz", allow_pickle=True)
    assert (fz["KEY"].astype(str) == KEY).all()
    FIN = fz["FIN"].astype(np.float64)
    fin_ok = (np.isfinite(FIN).sum(1) >= 0.85 * 210) & (WIN >= 0)
    kidx = {k: i for i, k in enumerate(KEY)}
    rows, stats = [], dict(races=0, fin_ok=0, no_lineinfo_new=0, no_lineinfo_form=0, same_line=0, diff_line=0, both_elig=0)
    for k, s in te.groupby("race_key", sort=False):
        i = kidx[k]
        if not fin_ok[i]:
            continue
        stats["fin_ok"] += 1
        s = s.sort_values("frame_no")
        ln = s.line_group.values
        l_new = int(s.line_group.values[int(np.argmax(s.p_new.values))])
        l_nv = int(s.line_group.values[int(np.argmax(s["p_naive0.01"].values))])
        l_for = int(s.line_group.values[int(np.argmin(s.formation_pos_frac.values))])
        f = FIN[i]; cf = 1.0 / np.nansum(1.0 / f); win = PERMS[int(WIN[i])]
        rec = dict(day=DATE[i], half=int(DATE[i] >= "2025-07-01"), same=int(l_new == l_for))
        for nm, l in (("N", l_new), ("F", l_for), ("V", l_nv)):
            ld = lead_dep(s, l)
            if ld is None:
                rec[nm] = None; continue
            x, y = ld
            ix = [PIDX[(x, y, zz)] for zz in range(1, 8) if zz not in (x, y)]
            rec[nm] = (float((win[0], win[1]) == (x, y)), float(np.nansum([cf / f[j] for j in ix])), float(np.nansum([0.75 / f[j] for j in ix])))
        rows.append(rec)
    stats["races"] = len(rows)
    stats["same_line"] = sum(r["same"] for r in rows); stats["diff_line"] = len(rows) - stats["same_line"]
    stats["no_lineinfo_new"] = sum(r["N"] is None for r in rows); stats["no_lineinfo_form"] = sum(r["F"] is None for r in rows)
    days = np.array([r["day"] for r in rows]); ud, inv = np.unique(days, return_inverse=True)
    half = np.array([r["half"] for r in rows]); same = np.array([r["same"] for r in rows])

    def arr(nm, j):
        return np.array([(r[nm][j] if r[nm] is not None else 0.0) for r in rows]), np.array([r[nm] is not None for r in rows])
    out = {"stats": stats}
    rng = np.random.default_rng(SEED)
    ix_all = rng.integers(0, len(ud), (NB, len(ud)))

    def day_sum(v, mask):
        return np.bincount(inv, weights=np.where(mask, v, 0.0), minlength=len(ud))
    for ecol, ename in ((1, "fin_c"), (2, "fin_075")):
        for sel_name, sel in (("all", np.ones(len(rows), bool)), ("H1", half == 0), ("H2", half == 1), ("discordant", same == 0),
                              ("discordant_H1", (same == 0) & (half == 0)), ("discordant_H2", (same == 0) & (half == 1))):
            hs, es, ns = {}, {}, {}
            for nm in ("N", "F", "V"):
                h, el = arr(nm, 0); e, _ = arr(nm, ecol)
                hs[nm] = day_sum(h, sel & el); es[nm] = day_sum(e, sel & el); ns[nm] = int((sel & el).sum())
            ndays = int((np.bincount(inv, weights=sel.astype(float), minlength=len(ud)) > 0).sum())
            def ratio(nm, ix):
                return hs[nm][ix].sum() / max(es[nm][ix].sum(), 1e-12)
            rec = {"days": ndays}
            full = np.arange(len(ud))
            for nm in ("N", "F", "V"):
                bs = np.array([ratio(nm, ix) for ix in ix_all])
                rec[nm] = dict(n=ns[nm], hits=int(hs[nm].sum()), exp=float(es[nm].sum()), ratio=float(ratio(nm, full)),
                               lo=float(np.quantile(bs, 0.025)), hi=float(np.quantile(bs, 0.975)))
            for a, b in (("N", "F"), ("N", "V")):
                bs = np.array([ratio(a, ix) - ratio(b, ix) for ix in ix_all])
                rec[f"{a}-{b}"] = dict(diff=float(ratio(a, full) - ratio(b, full)), lo=float(np.quantile(bs, 0.025)), hi=float(np.quantile(bs, 0.975)))
            out[f"{ename}|{sel_name}"] = rec
    json.dump(out, open(D / "step0b.json", "w"), indent=1, default=float)
    print(stats)
    for k, v in out.items():
        if k == "stats": continue
        print(k, "days", v["days"], {n: (v[n]["n"], v[n]["hits"], round(v[n]["ratio"], 3), round(v[n]["lo"], 3), round(v[n]["hi"], 3)) for n in "NFV"},
              "N-F", {a: round(b, 3) for a, b in v["N-F"].items()})


if __name__ == "__main__":
    main()
