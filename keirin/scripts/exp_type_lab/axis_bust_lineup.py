#!/usr/bin/env python3
"""「軸が飛びそうな上位X%だけ、軸を外した買い目へ差し替える」をラインナップ全体で測る。

型ごとの単体比較は小標本で ROI が 30〜158% と暴れる（`axis_bust_buy.py`）。
採否は**全8プランを組んだ状態**で、同一レースの対応のあるブートストラップで見る
（`typef_band_lineup.py` と同じ作法）。

腕:
  現行            … `sell_plans_for` 相当（型A の A_ana 含む＝既に一部は導入済み）
  ANA全型 X% k点  … pw_ent 上位X% のレースを **全型で** 軸1外し k点へ差し替える
  無作為 X% k点   … 同数を無作為に選んで差し替える（検出が効いているかの対照・20seed）

🔴 確認窓(2026)が本番相当。🔴 ROI 単独では決めない（±2.5pt に約15.6年）。
"""
from __future__ import annotations
import sys, random
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import (ctx, _run_named, _plan_for, AXIS_GATE_MIN,
                            MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import (PLANS, Plan, SIGNBOARD_RACE_TYPES, allocate,
                          build_legs, mean_expected_payout)
from axis_bust_buy import run_plan

KS = (5, 12)
ANA = {k: Plan("x", "?", "trifecta", "bust_top", 0, max_legs=k, alloc="dutch") for k in KS}
QS = (0.90, 0.80)


def boot(a: list, b: list, nb: int = 2000, seed: int = 7):
    """対応のあるブートストラップ（同一レース）。戻り値 (Δ表示的中CI, ΔROI CI)。"""
    rng = np.random.default_rng(seed)
    n = len(a)
    idx = rng.integers(0, n, size=(nb, n))
    ds, dr = [], []
    for row in idx:
        sa = [a[j] for j in row if a[j]]
        sb = [b[j] for j in row if b[j]]
        if not sa or not sb:
            continue
        Sa, Sb = C.summarize(sa, 1), C.summarize(sb, 1)
        ds.append(Sb["shown"] - Sa["shown"]); dr.append(Sb["roi"] - Sa["roi"])
    q = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return q(ds), q(dr)


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = z["AXIS_SUM"].astype(float)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)

    for label, win in (("探索 2024-07〜2025-12 (in-sample)", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        nd = C.days_of(C.select(None, win))
        rows = []
        for i in [int(x) for x in C.select(None, win) if tp[int(x)] in "ABCDEF"]:
            x = ctx(i)
            if x is None:
                continue
            t = tp[i]
            trio_ok = (_run_named(x, "A_trio") is not None) if t == "A" else False
            key = _plan_for(t, rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            cur = None if axs[i] < AXIS_GATE_MIN.get(key, 0.0) else _run_named(x, key)
            rows.append(dict(pw=x.shape.pw_ent, cur=cur,
                             ana={k: run_plan(x, p) for k, p in ANA.items()},
                             cur_key=key))
        pw = np.array([r["pw"] for r in rows])
        base = C.summarize([r["cur"] for r in rows if r["cur"]], nd)
        print("\n" + "=" * 120)
        print(f"███ {label}   n={len(rows):,}R / {nd}日")
        print(C.HEAD + "  10万+/日")
        print(C.line("現行", base) + f"  {base['big_per_day']:8.3f}")
        for q in QS:
            thr = float(np.quantile(pw, q))
            sel = pw >= thr
            print(f"\n  ▼ pw_ent 上位{int((1-q)*100)}%（thr={thr:.4f}・{int(sel.sum())}R）"
                  f"  うち現行が A_ana なのは "
                  f"{sum(1 for r,s in zip(rows,sel) if s and r['cur_key']=='A_ana')}R")
            for k in KS:
                arm, a_paired, b_paired = [], [], []
                for r, s in zip(rows, sel):
                    use = r["ana"][k] if s else r["cur"]
                    if use:
                        arm.append(use)
                    if s:
                        a_paired.append(r["cur"]); b_paired.append(r["ana"][k])
                sarm = C.summarize(arm, nd)
                print(C.line(f"  差し替え ANA{k}点", sarm)
                      + f"  {sarm['big_per_day']:8.3f}")
                ci_s, ci_r = boot(a_paired, b_paired)
                print(f"      └ 差し替えたレースだけの対応比較 Δ表示的中 "
                      f"[{ci_s[0]:+.2f},{ci_s[1]:+.2f}]pt  ΔROI [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]pt")
                # 無作為に同数を差し替える対照
                wins_s = wins_r = 0
                ms, mr, mb = [], [], []
                for seed in range(20):
                    rng = random.Random(seed * 977 + 13)
                    pick = set(rng.sample(range(len(rows)), int(sel.sum())))
                    ctrl = []
                    for j, r in enumerate(rows):
                        use = r["ana"][k] if j in pick else r["cur"]
                        if use:
                            ctrl.append(use)
                    cs = C.summarize(ctrl, nd)
                    ms.append(cs["shown"]); mr.append(cs["roi"]); mb.append(cs["big_per_day"])
                    wins_s += sarm["shown"] > cs["shown"]; wins_r += sarm["roi"] > cs["roi"]
                print(f"      └ 無作為に同数差し替え20本 中央 表示的中 {np.median(ms):5.2f}% /"
                      f" ROI {np.median(mr):5.1f}  →  検出が勝ち "
                      f"表示的中 {wins_s}/20・ROI {wins_r}/20"
                      f" / 10万+ 中央 {np.median(mb):.3f}")


if __name__ == "__main__":
    main()
