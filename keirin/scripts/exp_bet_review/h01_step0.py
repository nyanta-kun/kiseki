#!/usr/bin/env python3
"""H01 Step0（停止判定）: ライン決着 (先頭→番手 の1-2着・3着不問) の発生倍率。

 発生倍率 = Σ 実的中 / Σ 期待,  期待 = Σ_z c/odds(先頭,番手,z),  c = 1/Σ_210(1/odds)（レース内）
 k 区分（事前登録・排他・優先 k1>k2>k3）:
   k1 = 得点1位の居ない 2車以上ライン ∧ 先頭の脚質が「逃」
   k2 = ライン得点合計が最大の 2車以上ライン（k1 に該当しないもの）
   k3 = その他の 2車以上ライン
 予測オッズ = 探索用 vintage（train_end 2024-12-31）。最終オッズも併記。
 開催日ブートストラップ 2,000 回・seed 固定。
"""
from __future__ import annotations
import json, sys
import numpy as np
from collections import defaultdict
from h01_common import *   # noqa
from src.type_lab import _lines_of, _line_pos_no

SEED, NB = 20261005, 2000


def classify(lg, lp, st, rp):
    """レースの (先頭, 番手, k) のリスト。lg 等は長さ7の配列（車番=index+1）。"""
    cars = list(range(1, 8))
    lgd = {c: lg[c - 1] for c in cars}
    lpd = {c: float(lp[c - 1]) for c in cars}
    lines = _lines_of(lgd, lpd, min_size=2)
    rpd = {c: float(rp[c - 1]) for c in cars if float(rp[c - 1]) > 0}   # 0 = 欠け
    if not lines:
        return [], 0
    rp1 = min(rpd, key=lambda c: (-rpd[c], c)) if rpd else None
    sums = [sum(rpd.get(c, 0.0) for c in ln) for ln in lines]
    best = max(sums)
    out, overlap = [], 0
    for ln, sm in zip(lines, sums):
        if _line_pos_no(lpd.get(ln[0])) != 1 or _line_pos_no(lpd.get(ln[1])) != 2:
            continue   # 先頭・番手の位置が明示されていないラインは対象外
        k1 = (rp1 is not None and rp1 not in ln and st[ln[0] - 1] == "逃")
        k2 = (sm == best and sm > 0)
        if k1 and k2:
            overlap += 1
        k = "k1" if k1 else ("k2" if k2 else "k3")
        out.append((ln[0], ln[1], k))
    return out, overlap


def main():
    b = load_board_2025()
    fz = np.load(D / "h01_final_tf_2025.npz", allow_pickle=True)
    assert (fz["KEY"] == b["KEY"]).all()
    FIN = fz["FIN"].astype(np.float64)
    n = len(b["KEY"])
    ok_pred = b["HAS_VINTAGE"] & (b["WIN"] >= 0)
    fin_ok = (np.isfinite(FIN).sum(1) >= 0.85 * 210) & (b["WIN"] >= 0)
    print(f"台2025 7車 {n}R / vintage あり {int(b['HAS_VINTAGE'].sum())}R / 結果あり∧vintage {int(ok_pred.sum())}R / 最終板>=85% {int(fin_ok.sum())}R")
    rows = []   # (day, k, hit, exp_pred, exp_fin, exp_fin075, half)
    nov = 0
    for i in np.flatnonzero(ok_pred):
        ev, ov = classify(b["LG"][i], b["A_line_pos"][i], b["ST"][i], b["A_race_point"][i])
        nov += ov
        po = b["PO"][i]; c = 1.0 / np.sum(1.0 / po)
        win = PERMS[int(b["WIN"][i])]
        use_fin = fin_ok[i]
        if use_fin:
            f = FIN[i]; cf = 1.0 / np.nansum(1.0 / f)
        for x, y, k in ev:
            idxs = [PIDX[(x, y, z)] for z in range(1, 8) if z not in (x, y)]
            e_p = float(sum(c / po[j] for j in idxs))
            hit = 1.0 if (win[0], win[1]) == (x, y) else 0.0
            if use_fin:
                ef = float(np.nansum([cf / f[j] for j in idxs]))     # 欠けは 0 寄与
                e075 = float(np.nansum([0.75 / f[j] for j in idxs]))
            else:
                ef = e075 = np.nan
            rows.append((b["DATE"][i], k, hit, e_p, ef, e075, 0 if b["DATE"][i] < "2025-07-01" else 1))
    print("k1∧k2 重なり(k1 に割当):", nov)
    rows_a = np.array([(r[2], r[3], r[4], r[5], r[6]) for r in rows], float)
    ks = np.array([r[1] for r in rows]); days = np.array([r[0] for r in rows])
    ud = sorted(set(days)); dix = {d: j for j, d in enumerate(ud)}
    di = np.array([dix[d] for d in days])
    rng = np.random.default_rng(SEED)
    res = {}
    def ratio(mask, ecol, rows_mask_extra=None):
        pass
    cols = {"pred": 1, "fin_c": 2, "fin_075": 3}
    out = {}
    for kk in ("k1", "k2", "k3", "ALL"):
        for half in ("all", "H1", "H2"):
            m = np.ones(len(rows), bool) if kk == "ALL" else (ks == kk)
            if half == "H1": m &= rows_a[:, 4] == 0
            if half == "H2": m &= rows_a[:, 4] == 1
            for cn, cj in cols.items():
                mm = m & np.isfinite(rows_a[:, cj])
                h = np.bincount(di[mm], weights=rows_a[mm, 0], minlength=len(ud))
                e = np.bincount(di[mm], weights=rows_a[mm, cj], minlength=len(ud))
                nd = int((np.bincount(di[mm], minlength=len(ud)) > 0).sum())
                pt = h.sum() / e.sum()
                bs = np.empty(NB)
                for t in range(NB):
                    s = rng.integers(0, len(ud), len(ud))
                    bs[t] = h[s].sum() / max(e[s].sum(), 1e-12)
                lo, hi = np.quantile(bs, [0.025, 0.975])
                out[(kk, half, cn)] = dict(n=int(mm.sum()), hits=int(h.sum()), days=nd, ratio=pt, lo=lo, hi=hi, exp=e.sum())
    for key, v in out.items():
        print(key, f"n={v['n']} hit={v['hits']} days={v['days']} 倍率={v['ratio']:.3f} [{v['lo']:.3f},{v['hi']:.3f}] 期待={v['exp']:.1f}")
    json.dump({"|".join(k): v for k, v in out.items()}, open(D / "h01_step0.json", "w"), default=float, indent=1)


if __name__ == "__main__":
    main()
