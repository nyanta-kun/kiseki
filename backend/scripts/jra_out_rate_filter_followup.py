"""jra_out_rate_filter_backtest.py の test 予測を用いた追加検証（キャッシュ利用・再学習なし）

観点:
  A. 市場乖離（モデル確率 vs 市場含意確率）で「妙味のある不人気馬」を抽出できるか
  B. 単勝 EV（p_win × 単勝オッズ）による選別
  C. 有望条件の test 前半/後半 再現性（多重比較の耐性チェック）
  D. レース単位の運用（1レース1点買い）でのROI
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

PKL = _root / "models" / "jra_out_rate_test_preds.pkl"


def roi_block(sel: pd.DataFrame) -> dict:
    n = len(sel)
    if n == 0:
        return {"n": 0, "win_rate": 0, "win_roi": 0, "top3_rate": 0, "place_roi": 0,
                "avg_odds": 0}
    win_ret = float(sel.loc[sel["finish_position"] == 1, "win_odds"].fillna(0).sum())
    placed = sel[(sel["finish_position"] <= 3) & (sel["place_odds"].notna())]
    return {
        "n": n,
        "win_rate": (sel["finish_position"] == 1).mean(),
        "win_roi": win_ret / n,
        "top3_rate": (sel["finish_position"] <= 3).mean(),
        "place_roi": float(placed["place_odds"].sum()) / n,
        "avg_odds": float(sel["win_odds"].median()),
    }


def show(label: str, sel: pd.DataFrame) -> dict:
    s = roi_block(sel)
    print(f"{label:<44}{s['n']:>7}{s['win_rate']:>8.3f}{s['win_roi']:>8.3f}"
          f"{s['top3_rate']:>8.3f}{s['place_roi']:>8.3f}{s['avg_odds']:>9.1f}")
    return s


HEADER = f"{'条件':<44}{'n':>7}{'勝率':>8}{'単ROI':>8}{'複率':>8}{'複ROI':>8}{'中央odds':>9}"


def main() -> None:
    te = pd.read_pickle(PKL)
    te = te[te["win_odds"].notna() & (te["win_odds"] >= 1.0)].copy()

    # 市場含意確率（レース内で正規化して控除率を除去）
    te["q_raw"] = 1.0 / te["win_odds"]
    te["q_mkt"] = te["q_raw"] / te.groupby("race_id")["q_raw"].transform("sum")
    # モデル勝率もレース内正規化（Σ=1 に揃えて公平比較）
    te["p_win_n"] = te["p_win"] / te.groupby("race_id")["p_win"].transform("sum")
    te["p_top3_n"] = te["p_top3"] / te.groupby("race_id")["p_top3"].transform("sum") * 3.0
    te["edge_win"] = te["p_win_n"] / te["q_mkt"]
    te["ev_win"] = te["p_win_n"] * te["win_odds"] * 0.8   # 控除20%後の実効EV相当
    te["r_top3"] = te.groupby("race_id")["p_top3"].rank(ascending=False, method="min")

    mid = te["date"].sort_values().iloc[len(te) // 2]
    halves = [("全期間", te), ("前半", te[te["date"] <= mid]), ("後半", te[te["date"] > mid])]

    print("=" * 100)
    print("【A】市場乖離 edge = モデル勝率/市場含意勝率 の十分位別（全馬）")
    print("=" * 100)
    print(HEADER)
    te["edge_dec"] = pd.qcut(te["edge_win"], 10, labels=False, duplicates="drop") + 1
    for d in sorted(te["edge_dec"].dropna().unique()):
        sub = te[te["edge_dec"] == d]
        show(f"edge D{int(d)} ({sub['edge_win'].min():.2f}-{sub['edge_win'].max():.2f})", sub)

    print("\n" + "=" * 100)
    print("【A-2】市場乖離 × 不人気（人気>=5）× 着外率フィルタ")
    print("=" * 100)
    print(HEADER)
    for e in [1.0, 1.2, 1.5, 2.0]:
        for th in [0.60, 0.70, 0.80]:
            sub = te[(te["edge_win"] >= e) & (te["p_out"] <= th)
                     & (te["win_popularity"] >= 5)]
            if len(sub) >= 30:
                show(f"edge>={e} p_out<={th} pop>=5", sub)

    print("\n" + "=" * 100)
    print("【B】単勝EV（正規化モデル勝率×オッズ×0.8）帯別")
    print("=" * 100)
    print(HEADER)
    for lo, hi in [(0.0, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 2.0), (2.0, 99)]:
        sub = te[te["ev_win"].between(lo, hi, inclusive="left")]
        if len(sub) >= 30:
            show(f"EV [{lo},{hi})", sub)

    print("\n" + "=" * 100)
    print("【C】主要候補条件の 前半 / 後半 再現性")
    print("=" * 100)
    conds = {
        "p_out<=0.60 & odds[10,20)":
            lambda d: d[(d["p_out"] <= 0.60) & d["win_odds"].between(10, 20, inclusive="left")],
        "p_out<=0.55 & r_top3<=3 & pop>=4":
            lambda d: d[(d["p_out"] <= 0.55) & (d["r_top3"] <= 3) & (d["win_popularity"] >= 4)],
        "edge>=1.5 & pop>=5":
            lambda d: d[(d["edge_win"] >= 1.5) & (d["win_popularity"] >= 5)],
        "edge>=1.2 & p_out<=0.70 & pop>=5":
            lambda d: d[(d["edge_win"] >= 1.2) & (d["p_out"] <= 0.70) & (d["win_popularity"] >= 5)],
        "r_top3<=2 & odds>=10":
            lambda d: d[(d["r_top3"] <= 2) & (d["win_odds"] >= 10)],
        "r_top3==1 & odds>=7":
            lambda d: d[(d["r_top3"] == 1) & (d["win_odds"] >= 7)],
    }
    print(HEADER)
    for name, fn in conds.items():
        for tag, d in halves:
            show(f"{name} [{tag}]", fn(d))
        print("-" * 100)

    print("\n" + "=" * 100)
    print("【D】1レース1点運用: 各レースの p_top3 最上位馬をオッズ帯で絞る")
    print("=" * 100)
    print(HEADER)
    top1 = te[te["r_top3"] == 1]
    for tag, d in halves:
        t = d[d["r_top3"] == 1]
        show(f"top1 全レース [{tag}]", t)
    for lo, hi in [(3, 7), (7, 15), (15, 50), (7, 50)]:
        for tag, d in halves:
            t = d[(d["r_top3"] == 1) & d["win_odds"].between(lo, hi, inclusive="left")]
            if len(t) >= 20:
                show(f"top1 odds[{lo},{hi}) [{tag}]", t)
        print("-" * 100)

    # 参考: 統計的有意性（単ROI=1.0 を帰無仮説とした簡易ブートストラップ）
    print("\n" + "=" * 100)
    print("【E】最良候補のブートストラップ95%CI（単ROI・全期間）")
    print("=" * 100)
    rng = np.random.default_rng(42)
    for name, fn in conds.items():
        sub = fn(te)
        if len(sub) < 50:
            continue
        ret = np.where(sub["finish_position"] == 1, sub["win_odds"].fillna(0), 0.0).astype(float)
        boots = [rng.choice(ret, size=len(ret), replace=True).mean() for _ in range(2000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"{name:<44} n={len(sub):>5}  単ROI={ret.mean():.3f}  95%CI=[{lo:.3f}, {hi:.3f}]")

    print(f"\n(top1 総数={len(top1)}, test={te['date'].min()}〜{te['date'].max()}, "
          f"{te['race_id'].nunique()}レース)")


if __name__ == "__main__":
    main()
