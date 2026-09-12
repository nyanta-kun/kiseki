#!/usr/bin/env python3
"""軸2車の p3 を「集中度×人気帯」で較正し直し、axis_sum' を作って分離力を測る。

fit は explore窓だけ（vintage）。confirm窓へ forward apply。
"""
from __future__ import annotations

import sys

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, "scripts/exp_type_lab")
import common  # noqa: E402

CANON = common.CANON


def horse_frame(window: str):
    z = common.board()
    idx = common.select(None, window)
    P3 = z["P3"][idx]
    PW = z["PW"][idx]
    AX = z["AXIS_SUM"][idx]
    WIN = z["WIN"][idx]
    KEY = z["KEY"][idx]
    n = len(idx)
    top3_mask = np.zeros((n, 7), dtype=bool)
    for i in range(n):
        for c in CANON[int(WIN[i])]:
            top3_mask[i, c - 1] = True
    finite = np.isfinite(P3).all(axis=1)
    P3, PW, AX, top3_mask, KEY = P3[finite], PW[finite], AX[finite], top3_mask[finite], KEY[finite]
    order = np.argsort(-P3, axis=1)
    rank = np.empty_like(order)
    for i in range(len(P3)):
        rank[i, order[i]] = np.arange(7)
    return dict(p3=P3, top3=top3_mask.astype(np.float64), axis_sum=AX, pw=PW, rank=rank,
                key=KEY, order=order, n=len(P3))


def odds_band_idx(pw: np.ndarray) -> np.ndarray:
    """0.75/pw を帯 index (0..7) へ。pw<=0 は帯7(最遠)扱い。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        odds = np.where(pw > 1e-9, 0.75 / pw, np.inf)
    edges = np.array([5, 10, 20, 30, 50, 100, 300, np.inf])
    return np.searchsorted(edges, odds, side="right")


def conc_quintile_idx(axis_sum_race: np.ndarray, edges: np.ndarray) -> np.ndarray:
    return np.searchsorted(edges, axis_sum_race, side="right") - 1  # 0..4


def build_axis2_table(frame):
    """レースごとの軸2車 (idx0, idx1) の p3・top3・odds帯・race concentration quintile。"""
    order = frame["order"]
    rank = frame["rank"]
    axis0 = order[:, 0]
    axis1 = order[:, 1]
    n = frame["n"]
    rows = dict(
        h0=axis0, h1=axis1,
        p3_0=frame["p3"][np.arange(n), axis0], p3_1=frame["p3"][np.arange(n), axis1],
        top3_0=frame["top3"][np.arange(n), axis0], top3_1=frame["top3"][np.arange(n), axis1],
        pw_0=frame["pw"][np.arange(n), axis0], pw_1=frame["pw"][np.arange(n), axis1],
        axis_sum=frame["axis_sum"], key=frame["key"],
    )
    return rows


def main():
    frames = {w: horse_frame(w) for w in ("explore", "confirm")}
    axis2 = {w: build_axis2_table(frames[w]) for w in frames}

    conc_edges = np.quantile(axis2["explore"]["axis_sum"], [0, .2, .4, .6, .8, 1.0])
    conc_edges[0], conc_edges[-1] = -np.inf, np.inf

    # ── フィット用: explore窓の軸2車 (p3, top3) を集めてセル別バイアスを推定 ──
    ex = axis2["explore"]
    n_ex = len(ex["h0"])
    conc_q = conc_quintile_idx(ex["axis_sum"], conc_edges)
    p3_all = np.concatenate([ex["p3_0"], ex["p3_1"]])
    top3_all = np.concatenate([ex["top3_0"], ex["top3_1"]])
    pw_all = np.concatenate([ex["pw_0"], ex["pw_1"]])
    conc_all = np.concatenate([conc_q, conc_q])
    band_all = odds_band_idx(pw_all)

    cell_delta = {}
    cell_n = {}
    for cq in range(5):
        for bd in range(8):
            m = (conc_all == cq) & (band_all == bd)
            if m.sum() >= 30:
                cell_delta[(cq, bd)] = float(top3_all[m].mean() - p3_all[m].mean())
                cell_n[(cq, bd)] = int(m.sum())
    print("セル別 バイアス(実測-予測)・explore窓・n>=30のみ")
    print("  {:>4s} {:>4s} {:>8s} {:>10s}".format("conc", "band", "n", "delta"))
    for (cq, bd), d in sorted(cell_delta.items()):
        print(f"  {cq:>4d} {bd:>4d} {cell_n[(cq,bd)]:>8d} {d:>+10.4f}")

    # 1次元バックオフ（(conc平均, band平均)）も作って穴を埋める
    conc_delta_1d = {}
    for cq in range(5):
        m = conc_all == cq
        conc_delta_1d[cq] = float(top3_all[m].mean() - p3_all[m].mean()) if m.sum() else 0.0
    band_delta_1d = {}
    for bd in range(8):
        m = band_all == bd
        band_delta_1d[bd] = float(top3_all[m].mean() - p3_all[m].mean()) if m.sum() else 0.0

    def calibrated_p3(p3, pw, conc_idx):
        band = odds_band_idx(pw)
        out = np.array(p3, dtype=np.float64, copy=True)
        for i in range(len(p3)):
            key = (int(conc_idx[i]), int(band[i]))
            if key in cell_delta:
                d = cell_delta[key]
            else:
                d = 0.5 * (conc_delta_1d.get(int(conc_idx[i]), 0.0)
                           + band_delta_1d.get(int(band[i]), 0.0))
            out[i] = np.clip(p3[i] + d, 1e-6, 1 - 1e-6)
        return out

    # ── Isotonic（大域・単純）も比較用に fit ──
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p3_all, top3_all)

    print("\n## axis_sum' の再構築と分離力（2軸そろい率の分離 AUC）")
    for window in ("explore", "confirm"):
        f = axis2[window]
        conc_q_w = conc_quintile_idx(f["axis_sum"], conc_edges)
        cal_p3_0 = calibrated_p3(f["p3_0"], f["pw_0"], conc_q_w)
        cal_p3_1 = calibrated_p3(f["p3_1"], f["pw_1"], conc_q_w)
        axis_sum_cal = cal_p3_0 + cal_p3_1
        iso_p3_0 = iso.predict(f["p3_0"])
        iso_p3_1 = iso.predict(f["p3_1"])
        axis_sum_iso = iso_p3_0 + iso_p3_1

        both = (f["top3_0"] > 0) & (f["top3_1"] > 0)
        auc_raw = roc_auc_score(both, f["axis_sum"])
        auc_cell = roc_auc_score(both, axis_sum_cal)
        auc_iso = roc_auc_score(both, axis_sum_iso)
        print(f"\n  --- {window} 窓 (n={len(f['h0'])}, 2軸そろい率={both.mean()*100:.2f}%) ---")
        print(f"    AUC  raw axis_sum        = {auc_raw:.4f}")
        print(f"    AUC  cell較正 axis_sum'  = {auc_cell:.4f}  (Δ={auc_cell-auc_raw:+.4f})")
        print(f"    AUC  isotonic axis_sum'  = {auc_iso:.4f}  (Δ={auc_iso-auc_raw:+.4f})")
        print(f"    axis_sum   平均={f['axis_sum'].mean():.4f} sd={f['axis_sum'].std():.4f}")
        print(f"    axis_sum'  平均={axis_sum_cal.mean():.4f} sd={axis_sum_cal.std():.4f}"
              f"  (raw比 相関(Pearson)={np.corrcoef(f['axis_sum'], axis_sum_cal)[0,1]:.4f}"
              f"  Spearman近似={np.corrcoef(np.argsort(np.argsort(f['axis_sum'])), np.argsort(np.argsort(axis_sum_cal)))[0,1]:.4f})")

        # 境界 1.44 に対応する位置（堅い側が同じ割合になるカロリー調整済みの境界を探す）
        firm_rate_raw = float((f["axis_sum"] >= 1.44).mean())
        # calibrated 側で同じ「堅い割合」になる分位点をとる
        thr_cal = np.quantile(axis_sum_cal, 1 - firm_rate_raw)
        firm_rate_cal_at_1_44 = float((axis_sum_cal >= 1.44).mean())
        print(f"    堅い割合(raw>=1.44)      = {firm_rate_raw*100:.2f}%")
        print(f"    堅い割合(cal>=1.44)      = {firm_rate_cal_at_1_44*100:.2f}%  (境界を動かさないとズレる)")
        print(f"    cal側で同じ割合になる境界 = {thr_cal:.4f}")

        # ラベル入替: raw境界1.44 vs cal同割合境界 で type が変わるレースの割合・その実際のそろい率
        raw_firm = f["axis_sum"] >= 1.44
        cal_firm = axis_sum_cal >= thr_cal
        flip = raw_firm != cal_firm
        print(f"    型(堅い/混戦)が入れ替わるレース = {flip.mean()*100:.2f}%  (n={flip.sum()})")
        if flip.sum() > 0:
            newly_firm = flip & cal_firm      # 混戦→堅い
            newly_loose = flip & (~cal_firm)  # 堅い→混戦
            firm_avg = both[raw_firm].mean() if raw_firm.sum() else np.nan
            loose_avg = both[~raw_firm].mean() if (~raw_firm).sum() else np.nan
            print(f"      堅い側の平均そろい率(raw)={firm_avg*100:.2f}% / 混戦側={loose_avg*100:.2f}%")
            if newly_firm.sum() > 0:
                print(f"      混戦→堅いに変わった{newly_firm.sum()}件のそろい率={both[newly_firm].mean()*100:.2f}%"
                      f"  (堅い平均に近いか: {'近い' if abs(both[newly_firm].mean()-firm_avg) < abs(both[newly_firm].mean()-loose_avg) else '遠い'})")
            if newly_loose.sum() > 0:
                print(f"      堅い→混戦に変わった{newly_loose.sum()}件のそろい率={both[newly_loose].mean()*100:.2f}%"
                      f"  (混戦平均に近いか: {'近い' if abs(both[newly_loose].mean()-loose_avg) < abs(both[newly_loose].mean()-firm_avg) else '遠い'})")

    # axis_gate 用: axis_sum の population rank が calibration でどれだけ動くか（p30閾値の意味が保たれるか）
    print("\n## axis_gate p30 への影響（population rank の変動）")
    for window in ("explore", "confirm"):
        f = axis2[window]
        conc_q_w = conc_quintile_idx(f["axis_sum"], conc_edges)
        cal_p3_0 = calibrated_p3(f["p3_0"], f["pw_0"], conc_q_w)
        cal_p3_1 = calibrated_p3(f["p3_1"], f["pw_1"], conc_q_w)
        axis_sum_cal = cal_p3_0 + cal_p3_1
        p30_raw = np.quantile(f["axis_sum"], 0.30)
        p30_cal = np.quantile(axis_sum_cal, 0.30)
        below_raw = f["axis_sum"] < p30_raw
        below_cal = axis_sum_cal < p30_cal
        agree = (below_raw == below_cal).mean()
        print(f"  {window}: 下位30%の一致率={agree*100:.2f}%  (p30 raw={p30_raw:.4f} cal={p30_cal:.4f})")


if __name__ == "__main__":
    main()
