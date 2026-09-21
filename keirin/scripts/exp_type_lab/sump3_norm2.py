#!/usr/bin/env python3
"""Σp3 正規化 × 閾値引き直し — 型ラボ商品KPI（2026-09-21・`sump3_norm.py` の続き）。

`sump3_norm.py` で「正規化して 1.44 のまま」は商品KPIを改善しないと出た。
ただし **1.44 は生の値の上で決めた定数**（`strategy_wt.py` の既存コメントも
「直すなら閾値の掃引をやり直すこと」と書いている）。正規化でスケールが動くので、
**firm 率を現行に合わせた閾値**に引き直した腕を足して測り直す。

🔴 閾値は**探索窓だけで決めて確認窓へそのまま持ち込む**（確認窓で合わせない）。
🔴 対照は「同じ件数だけ無作為に firm を反転」（件数を減らす検証の作法）。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for, _run_named  # noqa: E402
from src.type_lab import AXIS_SUM_FIRM, SIGNBOARD_RACE_TYPES  # noqa: E402

FIRM_MAP = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C"}
CACHE = Path("/tmp/ratebranch/sump3_norm2.pkl")


def keys_for(tl: str) -> list[str]:
    if tl == "F":
        return ["F_hit", "F_pay", "F_sign"]
    if tl == "A":
        return ["A_ana", "A_trio", "A_hit"]
    return [{"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]]


def collect():
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    P3 = z["P3"].astype(float)
    out = {}
    for win in ("explore", "confirm"):
        base = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        meta, recs = [], {}
        for n, i in enumerate(base):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(base):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            raw = tp[i]
            p = np.sort(P3[i])[::-1]
            tot = P3[i].sum()
            an = (p[0] + p[1]) * 3.0 / tot if tot > 0 else float("nan")
            # 型を決める2要素のうち **s（ライン形）側は不変**。firm だけ差し替える
            recs[i] = {k: _run_named(x, k) for k in set(keys_for(raw)) | set(keys_for(FIRM_MAP[raw]))}
            meta.append(dict(i=i, raw=raw, rtype=rt[i], pw=x.shape.pw_ent,
                             firm_raw=raw in "ABC", axis_norm=an))
        out[win] = (meta, recs, C.days_of(C.select(None, win)))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("wb") as f:
        pickle.dump(out, f)
    return out


def main() -> None:
    data = collect() if not CACHE.exists() else pickle.load(CACHE.open("rb"))
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)

    def run(meta, recs, firm_of) -> list:
        res = []
        for m in meta:
            tl = m["raw"] if firm_of(m) == m["firm_raw"] else FIRM_MAP[m["raw"]]
            trio_ok = recs[m["i"]].get("A_trio") is not None if tl == "A" else False
            r = recs[m["i"]].get(_plan_for(tl, m["rtype"], m["pw"], trio_ok, sign_rt))
            if r:
                res.append(r)
        return res

    # 探索窓で firm 率を現行に合わせる閾値を決める
    me, _, _ = data["explore"]
    rate = np.mean([m["firm_raw"] for m in me])
    THR = float(np.quantile([m["axis_norm"] for m in me], 1 - rate))
    print(f"現行の firm 率 {rate*100:.2f}%  → 正規化スケールの同率閾値 THR={THR:.4f}"
          f"（探索窓で決定・確認窓へそのまま適用）")

    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        meta, recs, nd = data[win]
        arms = [
            ("① 現行（生 1.44）", lambda m: m["firm_raw"]),
            ("② 正規化・閾値1.44のまま", lambda m: m["axis_norm"] >= AXIS_SUM_FIRM),
            (f"③ 正規化・同率閾値{THR:.3f}", lambda m: m["axis_norm"] >= THR),
        ]
        print("\n" + "=" * 122)
        n_flip = sum(1 for m in meta if (m["axis_norm"] >= THR) != m["firm_raw"])
        print(f"=== {label}  n={len(meta):,}R  日数={nd}  ③の入れ替わり {n_flip:,}"
              f"（{n_flip/len(meta)*100:.1f}%）===")
        print(C.HEAD)
        for name, f in arms:
            print(C.line(name, C.summarize(run(meta, recs, f), nd)))
        # 対照: ③ と同じ件数だけ無作為に反転
        firm_i = [m["i"] for m in meta if m["firm_raw"]]
        soft_i = [m["i"] for m in meta if not m["firm_raw"]]
        n_off = sum(1 for m in meta if m["firm_raw"] and (m["axis_norm"] < THR))
        n_on = sum(1 for m in meta if not m["firm_raw"] and (m["axis_norm"] >= THR))
        for seed in range(5):
            rng = np.random.default_rng(seed)
            flip = set(rng.choice(firm_i, min(n_off, len(firm_i)), replace=False)) | \
                   set(rng.choice(soft_i, min(n_on, len(soft_i)), replace=False))
            print(C.line(f"④ 対照 同数を無作為 seed{seed}",
                         C.summarize(run(meta, recs, lambda m: m["firm_raw"] ^ (m["i"] in flip)), nd)))


if __name__ == "__main__":
    main()
