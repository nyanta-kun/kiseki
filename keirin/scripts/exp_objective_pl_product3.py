#!/usr/bin/env python3
"""採用候補「PL の p3 × 現行 binary の pw」を2窓 + 対レースCIで測る（2026-09-14）。

`exp_objective_pl_product2.py` で、確認窓の商品KPI が

    現行 binary                 36.49件/日  表示的中 26.10%  ROI 79.4%
    PL の p3 × binary の pw      36.57件/日  表示的中 **26.74%**  ROI 80.6%

となった（PL の pw まで置き換えると 25.00% へ落ちる＝**pw は置き換えない**）。
ここでは
  ① 窓をもう1つ足す（B: 学習 〜2025-06-30 / 評価 2025-07-01〜12-31）
  ② **同一レース対比較の 95%CI**（レース単位 bootstrap）
を出す。

⚠️ 窓B は予測オッズ `odds_tf_n7`(train_end 2025-12-31) の in-sample 側。
   **符号の一致確認にだけ使う**（`common.py` の作法）。
⚠️ どちらも `keirin_protocol.BURNED_WINDOWS` の内側＝VAL。採否は宣言できない。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_product3.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import exp_objective_pl_ab as M  # noqa: E402
import exp_objective_pl_product as P  # noqa: E402
import scripts.exp_type_lab.common as C  # noqa: E402

SEEDS = [42, 101]
#: (名前, 学習終端, 評価開始, 評価終了)
WINDOWS = [("確認 2026-01〜08", "2025-12-31", "2026-01-01", "2026-12-31"),
           ("探索 2025-07〜12", "2025-06-30", "2025-07-01", "2025-12-31")]
_DF = None


def fit(seed: int, train_to: str, test_from: str):
    global _DF
    if _DF is None:
        d = pd.read_pickle(P.FEAT)
        _DF = d[d["finish_order"].notna()].copy()
    df = _DF
    tr = df[(df["race_date"] >= P.TRAIN_FROM) & (df["race_date"] <= train_to)].reset_index(drop=True)
    te = df[df["race_date"] >= test_from].reset_index(drop=True)
    cols = list(M.FEATURE_COLS_WT)
    Xtr, Xte = tr[cols].values.astype(np.float64), te[cols].values.astype(np.float64)
    p = dict(M.PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
    y3 = tr[M.TARGET_COL_WT].values
    y1 = (pd.to_numeric(tr["finish_order"], errors="coerce") == 1).astype(int).values
    b3 = lgb.train(dict(p, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y3, free_raw_data=False), num_boost_round=500)
    b1 = lgb.train(dict(p, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y1, free_raw_data=False), num_boost_round=500)
    lay_tr, lay_te = M.race_layout(tr), M.race_layout(te)
    pl = lgb.train(dict(p, objective=M.PLObjective(lay_tr, 3), metric="None",
                        boost_from_average=False),
                   lgb.Dataset(Xtr, label=np.zeros(len(Xtr)), free_raw_data=False),
                   num_boost_round=500)
    _pw, p3p = M.pl_probs(lay_te, pl.predict(Xte))
    print(f"  学習 {len(tr):,}行 / 予測 {len(te):,}行", flush=True)
    return te[["race_key", "frame_no"]], {
        "現行 binary": (b3.predict(Xte), b1.predict(Xte)),
        "PL の p3 × binary の pw": (p3p, b1.predict(Xte)),
    }


def boot_ci(pairs: list[tuple[int, int]], n: int = 4000, seed: int = 7):
    """対レースの (a_shown, b_shown) から Δ(b−a) の平均と 95%CI（pt）。"""
    a = np.array([x for x, _ in pairs], float)
    b = np.array([y for _, y in pairs], float)
    d = (b - a) * 100
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n, len(d)))
    bs = d[idx].mean(1)
    return d.mean(), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def main() -> None:
    z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
    for wname, train_to, tfrom, tto in WINDOWS:
        base = C.select(None, "all")
        idx = np.array([i for i in base if z["TYPE"][i] in "ABCDEF"
                        and tfrom <= str(z["DATE"][i]) <= tto])
        nd = len(set(z["DATE"][idx]))
        target_firm = float(np.mean([str(z["TYPE"][i]) in "ABC" for i in idx]))
        print(f"\n######## {wname}  {len(idx):,}R / {nd}日 / 堅い側 {target_firm*100:.2f}%",
              flush=True)
        agg: dict[str, list] = defaultdict(list)
        deltas = []
        for seed in SEEDS:
            print(f"#### seed={seed}", flush=True)
            meta, preds = fit(seed, train_to, tfrom)
            per_race: dict[str, dict[int, int]] = {}
            for arm, (p3, pw) in preds.items():
                vecs = P.as_board_vecs(meta, p3, pw)
                axs = []
                for i in idx:
                    v = vecs.get(str(z["KEY"][i]))
                    if v is not None:
                        s = np.sort(v[0])[::-1]
                        axs.append(s[0] + s[1])
                ft = float(np.percentile(axs, 100 * (1 - target_firm)))
                recs, keep = [], {}
                for n, i in enumerate(idx):
                    v = vecs.get(str(z["KEY"][i]))
                    if v is None:
                        continue
                    r = P.build_one(int(i), v, z, None, ft)
                    if r:
                        r["i"] = int(i)
                        recs.append(r)
                    if n % 4000 == 0:
                        print(f"    [{arm}] {n:,}/{len(idx):,}", flush=True)
                byp: dict[str, list] = defaultdict(list)
                for r in recs:
                    byp[r["plan"]].append(r["axis"])
                thr = {k: float(np.percentile(v, 20)) for k, v in byp.items() if len(v) >= 50}
                recs = [dict(r, passed=r["axis"] >= thr.get(r["plan"], 0.0)) for r in recs]
                sel = [r for r in recs if r["passed"]]
                for r in sel:
                    keep[r["i"]] = 1 if r["pay"] >= r["inv"] and r["pay"] > 0 else 0
                per_race[arm] = keep
                agg[arm].append(P.show(f"{arm}", recs, nd))
            a, b = list(preds)
            both = sorted(set(per_race[a]) & set(per_race[b]))
            pairs = [(per_race[a][i], per_race[b][i]) for i in both]
            m, lo, hi = boot_ci(pairs)
            deltas.append((len(pairs), m, lo, hi))
            print(f"  ▶ 対レース Δ表示的中（{b} − {a}）= {m:+.2f}pt "
                  f"CI[{lo:+.2f}, {hi:+.2f}]  n={len(pairs):,}R", flush=True)
        print(f"  == {wname} seed平均 ==")
        for k, ts in agg.items():
            f = lambda n: np.mean([t.get(n, 0) for t in ts])  # noqa: E731
            print(f"    {k:26s} 件/日 {f('perday'):5.2f} 表示的中 {f('shown'):6.2f}% "
                  f"ROI {f('roi'):6.1f}% 払戻中央 {f('med_pay'):8.0f} "
                  f"10万+/日 {f('big_per_day'):.3f}")
        print(f"    Δ 平均 {np.mean([d[1] for d in deltas]):+.2f}pt / "
              f"CI下限の最小 {min(d[2] for d in deltas):+.2f} / "
              f"CI上限の最大 {max(d[3] for d in deltas):+.2f}")


if __name__ == "__main__":
    main()
