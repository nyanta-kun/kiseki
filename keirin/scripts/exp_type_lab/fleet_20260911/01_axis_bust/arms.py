#!/usr/bin/env python3
"""商品まで通す。(a)(b) 堅い/混戦の判定量を差し替え、(c) 軸信頼ゲートの判定量を差し替える。

台: products.pkl（現行ラベル/反転ラベルの商品・入稿ゲート通過後）× scores.pkl（vintage スコア）
窓: 探索OOS 2025-01〜12（四半期WFの OOS）/ 確認 2026-01〜08
🔴 件数を動かす腕には「同じ件数を無作為に動かす対照」を 20 seed 置く。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
P = pickle.load((HERE / "products.pkl").open("rb"))
T: pd.DataFrame = pickle.load((HERE / "scores.pkl").open("rb"))
T = T[T.i.isin(P)].reset_index(drop=True)
WINS = {"探索": ("2025-01-01", "2025-12-31"), "確認": ("2026-01-01", "2026-12-31")}
QUARTERS = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
            ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30"),
            ("2026-07-01", "2026-09-30")]
GATED = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_sign"}
NSEED = 20


def n_days(win):
    lo, hi = WINS[win]
    return len(set(C.board()["DATE"][C.select(None, "explore" if win == "探索" else "confirm")]
                   ) & set(T.date[(T.date >= lo) & (T.date <= hi)])) or 1


def kpi(rows, nd):
    s = C.summarize(rows, nd)
    return s


def fmt(name, s, extra=""):
    if not s.get("n"):
        return f"  {name:34s} (該当なし)"
    return (f"  {name:34s} {s['perday']:6.2f}件/日 表示的中 {s['shown']:6.2f}% 的中 {s['hit']:6.2f}%"
            f" ガミ {s['gami']:5.2f}% 払戻中央 {s['med_pay']:7,.0f} 2倍+/日 {s['two_per_day']:5.2f}"
            f" 10万+/日 {s['big_per_day']:5.3f} ROI {s['roi']:5.1f}{extra}")


def rows_for(sel_flip: np.ndarray, m: np.ndarray):
    """m の範囲で、sel_flip[r] が True なら反転ラベルの商品、else 現行の商品。"""
    out, sold, shown, both = [], np.zeros(len(T), bool), np.zeros(len(T), bool), np.zeros(len(T), bool)
    for r in np.flatnonzero(m):
        rec = P[int(T.i[r])]
        row = rec["flip"] if sel_flip[r] else rec["cur"]
        if row is None:
            continue
        out.append(row); sold[r] = True; shown[r] = row["pay"] >= row["inv"]; both[r] = T.y[r]
    return out, sold, shown, both


def boot_delta(m, sold_a, shown_a, sold_b, shown_b, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    ix_all = np.flatnonzero(m)
    out = []
    for _ in range(n):
        ix = rng.choice(ix_all, len(ix_all))
        ra = shown_a[ix].sum() / max(sold_a[ix].sum(), 1)
        rb = shown_b[ix].sum() / max(sold_b[ix].sum(), 1)
        out.append((rb - ra) * 100)
    return np.percentile(out, 2.5), np.percentile(out, 97.5)


def thresholds_by_quarter(score: str, share_mult: float = 1.0):
    """四半期ごとに、学習窓（〜前日）の現行 firm 率 × share_mult になる分位で閾値を引く。"""
    thr = np.full(len(T), np.nan)
    d = T.date.values
    firm_cur = T.type.isin(list("ABC")).values
    for qlo, qhi in QUARTERS:
        tr = (d >= "2024-07-01") & (d < qlo)
        te = (d >= qlo) & (d <= qhi)
        share = min(max(firm_cur[tr].mean() * share_mult, 0.02), 0.98)
        s = T.loc[tr, score].values
        s = s[~np.isnan(s)]
        if len(s) < 1000:
            # WF の最初の四半期は学習窓に OOS スコアが無い → その四半期自身の分位で件数だけ揃える
            s = T.loc[te, score].values
            s = s[~np.isnan(s)]
        if len(s) == 0:
            continue                      # スコア自体が無い四半期は現行の判定のまま
        thr[te] = np.quantile(s, 1 - share)
    return thr


def arm_firm(score: str, share_mult: float = 1.0):
    thr = thresholds_by_quarter(score, share_mult)
    s = T[score].values
    firm_new = np.where(np.isnan(s), T.type.isin(list("ABC")).values, s >= thr)
    return firm_new != T.type.isin(list("ABC")).values, firm_new


def section_a():
    print("\n" + "=" * 110)
    print("## (a) 堅い/混戦の判定量を差し替える（firm 率は学習窓の現行に揃える・arare は据え置き）")
    print("=" * 110)
    firm_cur = T.type.isin(list("ABC")).values
    for win, (lo, hi) in WINS.items():
        m = ((T.date >= lo) & (T.date <= hi)).values
        nd = n_days(win)
        base_rows, s0, h0, b0 = rows_for(np.zeros(len(T), bool), m)
        base = kpi(base_rows, nd)
        print(f"\n### {win}  {lo}〜{hi}  台 {m.sum():,}R  日数 {nd}")
        print(fmt("現行 axis_sum>=1.44", base,
                  f"  軸2そろい(売った商品) {b0[s0].mean()*100:.2f}%"))
        for score in ("axis_sum", "p_both", "l_exist_pb", "s_full", "l_exist_pb_full"):
            flip, firm_new = arm_firm(score)
            n_up = int((flip & ~firm_cur & m).sum()); n_dn = int((flip & firm_cur & m).sum())
            rows, s1, h1, b1 = rows_for(flip, m)
            k = kpi(rows, nd)
            ci = boot_delta(m, s0, h0, s1, h1)
            # 対照: 同数を無作為に反転
            ctrl = []
            for sd in range(NSEED):
                rng = np.random.default_rng(sd)
                f = np.zeros(len(T), bool)
                up = np.flatnonzero(~firm_cur & m); dn = np.flatnonzero(firm_cur & m)
                f[rng.choice(up, n_up, replace=False)] = True
                f[rng.choice(dn, n_dn, replace=False)] = True
                r_, _, _, _ = rows_for(f, m)
                ctrl.append(kpi(r_, nd))
            cs = np.array([c["shown"] for c in ctrl]); cb = np.array([c["big_per_day"] for c in ctrl])
            wins_ = int((k["shown"] > cs).sum())
            print(fmt(f"{score} (↑{n_up} ↓{n_dn})", k,
                      f"  軸2そろい {b1[s1].mean()*100:.2f}%  Δ表示的中 CI[{ci[0]:+.2f},{ci[1]:+.2f}]"
                      f"  対照中央 {np.median(cs):.2f}% 勝ち {wins_}/{NSEED}  対照10万+ {np.median(cb):.3f}"))


def section_b():
    print("\n" + "=" * 110)
    print("## (b) 境界を新スコアの分位で動かす（firm 率 = 現行 × 倍率）")
    print("=" * 110)
    firm_cur = T.type.isin(list("ABC")).values
    for win, (lo, hi) in WINS.items():
        m = ((T.date >= lo) & (T.date <= hi)).values
        nd = n_days(win)
        base_rows, s0, h0, b0 = rows_for(np.zeros(len(T), bool), m)
        base = kpi(base_rows, nd)
        print(f"\n### {win}")
        print(fmt("現行", base, f"  firm率 {firm_cur[m].mean()*100:.1f}%"))
        for score in ("axis_sum", "p_both", "s_full"):
            for mult in (0.8, 0.9, 1.1, 1.2):
                flip, firm_new = arm_firm(score, mult)
                n_up = int((flip & ~firm_cur & m).sum()); n_dn = int((flip & firm_cur & m).sum())
                rows, s1, h1, b1 = rows_for(flip, m)
                k = kpi(rows, nd)
                ci = boot_delta(m, s0, h0, s1, h1)
                ctrl = []
                for sd in range(NSEED):
                    rng = np.random.default_rng(sd)
                    f = np.zeros(len(T), bool)
                    up = np.flatnonzero(~firm_cur & m); dn = np.flatnonzero(firm_cur & m)
                    f[rng.choice(up, n_up, replace=False)] = True
                    f[rng.choice(dn, n_dn, replace=False)] = True
                    r_, _, _, _ = rows_for(f, m)
                    ctrl.append(kpi(r_, nd))
                cs = np.array([c["shown"] for c in ctrl])
                print(fmt(f"{score} ×{mult} firm率{firm_new[m].mean()*100:.1f}% (↑{n_up} ↓{n_dn})", k,
                          f"  Δ CI[{ci[0]:+.2f},{ci[1]:+.2f}] 対照中央 {np.median(cs):.2f}%"
                          f" 勝ち {int((k['shown'] > cs).sum())}/{NSEED}"))


def section_c():
    print("\n" + "=" * 110)
    print("## (c) 軸信頼ゲート — プラン内 下位30% を落とす判定量を差し替える（現行商品・件数同一）")
    print("=" * 110)
    plan = np.array([P[int(i)]["plan_cur"] for i in T.i])
    sold = np.array([P[int(i)]["cur"] is not None for i in T.i])
    for win, (lo, hi) in WINS.items():
        m = ((T.date >= lo) & (T.date <= hi)).values & sold
        nd = n_days(win)
        gated = m & np.isin(plan, list(GATED))
        base_rows, s0, h0, b0 = rows_for(np.zeros(len(T), bool), m)
        print(f"\n### {win}  売った商品 {m.sum():,}  うちゲート対象プラン {gated.sum():,}")
        print(fmt("ゲートなし（全商品）", kpi(base_rows, nd), f"  軸2そろい {b0[s0].mean()*100:.2f}%"))

        def apply(keep: np.ndarray, name: str, ctrl_stats=None):
            rows, s1, h1, b1 = rows_for(np.zeros(len(T), bool), m & keep)
            k = kpi(rows, nd)
            ci = boot_delta(m, s0, h0, s1, h1)
            ex = f"  軸2そろい {b1[s1].mean()*100:.2f}%  Δ CI[{ci[0]:+.2f},{ci[1]:+.2f}]"
            if ctrl_stats is not None:
                cs, cb = ctrl_stats
                ex += (f"  対照中央 {np.median(cs):.2f}% 勝ち {int((k['shown'] > cs).sum())}/{NSEED}"
                       f"  対照10万+ {np.median(cb):.3f}")
            print(fmt(name, k, ex))
            return k

        # 落とす件数はプラン内 30%（現行と同じ深さ）
        drop_n = {p: int(round(0.30 * (gated & (plan == p)).sum())) for p in GATED}
        # 無作為対照
        ctrl = []
        for sd in range(NSEED):
            rng = np.random.default_rng(sd)
            keep = np.ones(len(T), bool)
            for p, k_ in drop_n.items():
                ix = np.flatnonzero(gated & (plan == p))
                keep[rng.choice(ix, k_, replace=False)] = False
            r_, _, _, _ = rows_for(np.zeros(len(T), bool), m & keep)
            s_ = kpi(r_, nd); ctrl.append((s_["shown"], s_["big_per_day"]))
        cs = np.array([c[0] for c in ctrl]); cb = np.array([c[1] for c in ctrl])
        for score in ("axis_sum", "pw_ent", "p_both", "l_exist_pb", "s_full", "l_exist_pb_full"):
            sv = T[score].values.astype(float)
            if score == "pw_ent":
                sv = -sv
            keep = np.ones(len(T), bool)
            for p, k_ in drop_n.items():
                ix = np.flatnonzero(gated & (plan == p))
                ix = ix[np.argsort(np.nan_to_num(sv[ix], nan=np.inf))]   # 低い順に落とす
                keep[ix[:k_]] = False
            apply(keep, f"下位30%落とし: {score}", (cs, cb))


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
