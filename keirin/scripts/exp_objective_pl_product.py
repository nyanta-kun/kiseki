#!/usr/bin/env python3
"""目的関数 A/B を **商品KPI** まで通す（2026-09-14）。

`exp_objective_pl_ab.py` の PL-K3 と現行 binary を、**同じ切り（学習 〜2025-12-31）**で
学習し直し、`/tmp/race_type_board.npz` の確認窓 2026-01-01〜08-04 のレースへ当てて
**本番の関数で**商品を組む（`type_lab.race_shape` → `sell_plans_for` 相当の
`_plan_for` → `build_with_gate_fallback` → 入稿ゲート → 軸信頼ゲート）。

🔴 **`build_with_gate_fallback` はゲートを判定しない**（見送りの判断は入稿側）。
   呼ぶ側で 平均想定払戻 > 20,000円 / 全点の予測オッズ >= 2.0倍 を掛ける。
🔴 **軸信頼ゲート `AXIS_GATE_MIN` は絶対閾値**（探索窓のプラン内20パーセンタイル）。
   p3 の較正が動くと同じ数字が別の操作になるため、
   (a) 本番の絶対閾値そのまま と (b) 各腕で確認窓のプラン内20パーセンタイルを
   引き直した版 の両方を出す。(b) が腕間で公平な比較。
⚠️ 確認窓は予測オッズ `odds_tf_n7`(train_end 2025-12-31) の OOS 側＝本番相当。
⚠️ それでも `keirin_protocol.BURNED_WINDOWS` の内側なので **採否は宣言できない**。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_product.py
"""
from __future__ import annotations

import itertools
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import exp_objective_pl_ab as M  # noqa: E402
import scripts.exp_type_lab.common as C  # noqa: E402
from scripts.exp_type_lab.typef_racetype import AXIS_GATE_MIN, _plan_for  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_P3_SUM_MIN, rank_7t3_blend_probs, rank_7t3_order_swap_probs)
from dataclasses import replace as _dc_replace  # noqa: E402

from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, build_with_gate_fallback, mean_expected_payout,
    race_shape)

FEAT = Path("/tmp/plab/feat_full.pkl")
TRAIN_FROM, TRAIN_TO = "2024-04-01", "2025-12-31"
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS, C3 = C.CANON, C.CANON3
SEEDS = [42, 101]
_DF = None


def fit_models(seed: int):
    global _DF
    if _DF is None:
        d = pd.read_pickle(FEAT)
        _DF = d[d["finish_order"].notna()].copy()
    df = _DF
    tr = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] <= TRAIN_TO)].reset_index(drop=True)
    te = df[(df["race_date"] > TRAIN_TO)].reset_index(drop=True)
    cols = list(M.FEATURE_COLS_WT)
    Xtr, Xte = tr[cols].values.astype(np.float64), te[cols].values.astype(np.float64)
    P = dict(M.PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
    y3 = tr[M.TARGET_COL_WT].values
    y1 = (pd.to_numeric(tr["finish_order"], errors="coerce") == 1).astype(int).values
    print(f"  学習 {len(tr):,}行 / 予測 {len(te):,}行", flush=True)
    out = {}
    b3 = lgb.train(dict(P, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y3, free_raw_data=False), num_boost_round=500)
    b1 = lgb.train(dict(P, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y1, free_raw_data=False), num_boost_round=500)
    out["binary(現行)"] = (b3.predict(Xte), b1.predict(Xte))
    lay_tr, lay_te = M.race_layout(tr), M.race_layout(te)
    obj = M.PLObjective(lay_tr, 3)
    pl = lgb.train(dict(P, objective=obj, metric="None", boost_from_average=False),
                   lgb.Dataset(Xtr, label=np.zeros(len(Xtr)), free_raw_data=False),
                   num_boost_round=500)
    pw, p3 = M.pl_probs(lay_te, pl.predict(Xte))
    out["PL-K3"] = (p3, pw)
    return te[["race_key", "frame_no"]], out


def as_board_vecs(meta: pd.DataFrame, p3: np.ndarray, pw: np.ndarray) -> dict:
    """race_key -> (p3(7,), pw(7,))。7車そろわないレースは落とす。"""
    d: dict[str, np.ndarray] = {}
    fn = pd.to_numeric(meta["frame_no"], errors="coerce").fillna(0).astype(int).values
    rk = meta["race_key"].values
    for i in range(len(meta)):
        if not (1 <= fn[i] <= 7):
            continue
        a = d.setdefault(str(rk[i]), np.full((2, 7), np.nan))
        a[0, fn[i] - 1] = p3[i]
        a[1, fn[i] - 1] = pw[i]
    return {k: v for k, v in d.items() if np.isfinite(v).all()}


def shape_of(i: int, vec: np.ndarray, z, firm_thr: float | None):
    cars = list(range(1, 8))
    p3 = {c: float(vec[0][c - 1]) for c in cars}
    pw = {c: float(vec[1][c - 1]) for c in cars}
    lg = {c: str(z["LG"][i][c - 1]) for c in cars}
    lp = {c: float(z["A_line_pos"][i][c - 1]) for c in cars}
    st = {c: str(z["ST"][i][c - 1]) for c in cars}
    rp = {c: float(z["A_race_point"][i][c - 1]) for c in cars}
    bh = {c: float(z["BEHIND"][i][c - 1]) for c in cars}
    sh = race_shape(p3, lg, lp, st, rp, bh, int(z["DAYI"][i]), win_probs=pw)
    if sh is None:
        return None, p3, pw, lg, lp
    if firm_thr is not None:
        # 🔴 型の境界 `AXIS_SUM_FIRM=1.44` は **p3 の較正に依存する絶対閾値**。
        #    腕ごとに「堅い側の割合」を揃えた分位点へ引き直す。
        firm = sh.axis_sum >= firm_thr
        lab = (("A" if sh.arare <= -1 else "B" if sh.arare == 0 else "C") if firm
               else ("D" if sh.arare <= -1 else "E" if sh.arare == 0 else "F"))
        sh = _dc_replace(sh, type_label=lab, firm=firm)
    return sh, p3, pw, lg, lp


def build_one(i: int, vec: np.ndarray, z, thr: dict | None, firm_thr: float | None = None):
    cars = list(range(1, 8))
    shape, p3, pw, lg, lp = shape_of(i, vec, z, firm_thr)
    if shape is None:
        return None
    po = {PERMS[t]: float(z["PO"][i][t]) for t in range(210)
          if np.isfinite(z["PO"][i][t]) and z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    osw = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po3 = {frozenset(c): float(z["TRIO_PO"][i][j]) for j, c in enumerate(C3)
           if np.isfinite(z["TRIO_PO"][i][j]) and z["TRIO_PO"][i][j] > 0}
    pr3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
           for c in C3}
    # A_trio が組めるか（`_plan_for` の trio_ok）
    trio_ok = False
    if shape.type_label == "A":
        g = build_with_gate_fallback(shape, PLANS["A_trio"], po3, pr3, 7,
                                     MIN_MEAN_PAYOUT, osw)
        trio_ok = bool(g and mean_expected_payout(g[1], po3) > MIN_MEAN_PAYOUT
                       and min(float(po3[c]) for c in g[1]) >= MIN_POINT_ODDS)
    key = _plan_for(shape.type_label, str(z["RTYPE"][i]), shape.pw_ent, trio_ok,
                    tuple(SIGNBOARD_RACE_TYPES))
    plan = PLANS[key]
    pod, prb = (po3, pr3) if plan.bet_type == "trio" else (po, pr)
    got = build_with_gate_fallback(shape, plan, pod, prb, 7, MIN_MEAN_PAYOUT, osw)
    if not got:
        return None
    legs, stakes, used = got
    if not stakes:
        return None
    if mean_expected_payout(stakes, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in stakes) < MIN_POINT_ODDS:
        return None
    gate = (thr or AXIS_GATE_MIN).get(used.key, 0.0)
    passed = shape.axis_sum >= gate
    if used.bet_type == "trio":
        wc = frozenset(C3[int(z["TRIO_WIN"][i])])
        pay = float(stakes[wc] * float(z["TRIO_PAY"][i])) if wc in stakes else 0.0
    else:
        wc = PERMS[int(z["WIN"][i])]
        pay = float(stakes[wc] * float(z["PAY"][i]) / 100.0) if wc in stakes else 0.0
    return dict(date=str(z["DATE"][i]), plan=used.key, type=shape.type_label,
                k=len(stakes), inv=float(sum(stakes.values())), pay=pay,
                mean=float(mean_expected_payout(stakes, pod)),
                axis=float(shape.axis_sum), passed=passed)


def run(tag: str, idx, vecs: dict, z, thr: dict | None,
        firm_thr: float | None = None) -> list[dict]:
    recs = []
    for n, i in enumerate(idx):
        v = vecs.get(str(z["KEY"][i]))
        if v is None:
            continue
        r = build_one(int(i), v, z, thr, firm_thr)
        if r:
            recs.append(r)
        if n % 2000 == 0:
            print(f"    [{tag}] {n:,}/{len(idx):,}", flush=True)
    return recs


def show(tag: str, recs: list[dict], nd: int) -> dict:
    sel = [r for r in recs if r["passed"]]
    t = C.summarize(sel, nd)
    print(f"  {tag:22s} n={t.get('n',0):5d} 件/日 {t.get('perday',0):5.2f} "
          f"点数 {t.get('k',0):4.1f} 表示的中 {t.get('shown',0):6.2f}% "
          f"ROI {t.get('roi',0):6.1f}% 払戻中央 {t.get('med_pay',0):8.0f} "
          f"10万+/日 {t.get('big_per_day',0):.3f}", flush=True)
    return t


def main() -> None:
    z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
    idx = C.select(None, "confirm")
    idx = np.array([i for i in idx if z["TYPE"][i] in "ABCDEF"])
    nd = len(set(z["DATE"][idx]))
    print(f"確認窓 {len(idx):,}R / {nd}日  {sorted(set(z['DATE'][idx]))[0]}〜{sorted(set(z['DATE'][idx]))[-1]}",
          flush=True)
    target_firm = float(np.mean([str(z["TYPE"][i]) in "ABC" for i in idx]))
    print(f"台の堅い側(A/B/C)の割合 = {target_firm*100:.2f}%", flush=True)
    agg: dict[str, list] = defaultdict(list)
    for seed in SEEDS:
        print(f"\n#### seed={seed}", flush=True)
        meta, preds = fit_models(seed)
        for arm, (p3, pw) in preds.items():
            vecs = as_board_vecs(meta, p3, pw)
            recs = run(arm, idx, vecs, z, None)          # 本番の絶対閾値
            # 腕ごとに引き直した閾値（プラン内20パーセンタイル・確認窓）
            byplan: dict[str, list] = defaultdict(list)
            for r in recs:
                byplan[r["plan"]].append(r["axis"])
            thr = {k: float(np.percentile(v, 20)) for k, v in byplan.items() if len(v) >= 50}
            recs2 = [dict(r, passed=r["axis"] >= thr.get(r["plan"], 0.0)) for r in recs]
            print(f"  [{arm}] axis_sum 平均 {np.mean([r['axis'] for r in recs]):.4f} / "
                  f">=1.44 {np.mean([r['axis'] >= RANK_7C_P3_SUM_MIN for r in recs])*100:.1f}% / "
                  f"型 " + " ".join(
                      f"{t}{sum(1 for r in recs if r['type']==t)}" for t in "ABCDEF"),
                  flush=True)
            a = show(f"{arm}/本番閾値", recs, nd)
            b = show(f"{arm}/引き直し20%", recs2, nd)
            agg[arm + "|abs"].append(a)
            agg[arm + "|re"].append(b)

            # ── 型の境界も腕ごとに引き直す（堅い側の割合を台と揃える）──
            axs = []
            for i in idx:
                v = vecs.get(str(z["KEY"][i]))
                if v is None:
                    continue
                p3v = np.sort(v[0])[::-1]
                axs.append(p3v[0] + p3v[1])
            ft = float(np.percentile(axs, 100 * (1 - target_firm)))
            recs3 = run(arm + "/firm", idx, vecs, z, None, ft)
            byp3: dict[str, list] = defaultdict(list)
            for r in recs3:
                byp3[r["plan"]].append(r["axis"])
            thr3 = {k: float(np.percentile(v, 20)) for k, v in byp3.items() if len(v) >= 50}
            recs4 = [dict(r, passed=r["axis"] >= thr3.get(r["plan"], 0.0)) for r in recs3]
            print(f"  [{arm}] 型境界 引き直し {ft:.4f} / 型 " + " ".join(
                f"{t}{sum(1 for r in recs3 if r['type']==t)}" for t in "ABCDEF"), flush=True)
            agg[arm + "|firm+re"].append(show(f"{arm}/型境界も引直し", recs4, nd))
    print("\n==== seed 平均 ====")
    for k, ts in agg.items():
        f = lambda n: np.mean([t.get(n, 0) for t in ts])  # noqa: E731
        print(f"  {k:28s} 件/日 {f('perday'):5.2f} 表示的中 {f('shown'):6.2f}% "
              f"ROI {f('roi'):6.1f}% 払戻中央 {f('med_pay'):8.0f} "
              f"10万+/日 {f('big_per_day'):.3f}")


if __name__ == "__main__":
    main()
