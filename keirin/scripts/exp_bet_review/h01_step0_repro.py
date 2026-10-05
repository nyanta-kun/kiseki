"""H01 Step0 付録: line_lead §3 の「②」(逃げ先頭・得点1位でないライン・得点1位のラインが3車以下) を
2025・最終オッズ(0.75/odds)で再現する。k 区分とは別の再現確認で、判定には使わない。"""
import numpy as np
from h01_common import *   # noqa
from src.type_lab import _lines_of, _line_pos_no

b = load_board_2025()
FIN = np.load(D / "h01_final_tf_2025.npz", allow_pickle=True)["FIN"].astype(float)
fin_ok = (np.isfinite(FIN).sum(1) >= 0.85 * 210) & (b["WIN"] >= 0)
rows = []
for i in np.flatnonzero(fin_ok):
    cars = range(1, 8)
    lgd = {c: b["LG"][i][c-1] for c in cars}; lpd = {c: float(b["A_line_pos"][i][c-1]) for c in cars}
    rp = {c: float(b["A_race_point"][i][c-1]) for c in cars if float(b["A_race_point"][i][c-1]) > 0}
    if len(rp) < 2:
        continue
    lines = _lines_of(lgd, lpd, min_size=2); rp1 = min(rp, key=lambda c: (-rp[c], c))
    rp1_line = next((ln for ln in lines if rp1 in ln), (rp1,))
    if len(rp1_line) > 3:
        continue
    win = PERMS[int(b["WIN"][i])]
    for ln in lines:
        if rp1 in ln or b["ST"][i][ln[0]-1] != "逃":
            continue
        x, y = ln[0], ln[1]
        e = float(np.nansum([0.75 / FIN[i][PIDX[(x, y, z)]] for z in range(1, 8) if z not in (x, y)]))
        rows.append((b["DATE"][i], float((win[0], win[1]) == (x, y)), e))
days = sorted({r[0] for r in rows}); di = {d: j for j, d in enumerate(days)}
h = np.zeros(len(days)); e = np.zeros(len(days))
for d, hh, ee in rows:
    h[di[d]] += hh; e[di[d]] += ee
rng = np.random.default_rng(20261005)
bs = [h[s].sum() / e[s].sum() for s in (rng.integers(0, len(days), len(days)) for _ in range(2000))]
print(f"② 再現(2025・最終・0.75/odds): n={len(rows)} hit={int(h.sum())} 倍率={h.sum()/e.sum():.3f} [{np.quantile(bs,.025):.3f},{np.quantile(bs,.975):.3f}]  (line_lead §3: 探索 1.19 [1.14,1.24] / 確認 1.20 [1.13,1.26])")
