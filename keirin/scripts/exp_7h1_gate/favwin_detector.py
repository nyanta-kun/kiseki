#!/usr/bin/env python3
"""「本命が勝つレース」を判定して 7H1 から外せるか（ユーザー提案・2026-08-25）。

7H1 の買い目は本命を全列から外すので、**本命が3着以内に入った時点で的中は不可能**。
したがって「本命が勝つレース」を事前に外せれば、外したぶんだけ的中率もROIも
機械的に上がる。上限（オラクル）と、実際に作れる判定器の到達点を測る。

honest キャッシュ（`build_cache.py`・月次凍結 vintage）を使い、
探索 2024-04〜2025-12 / 確認 2026-01〜 の2窓で評価する。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/favwin_detector.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CACHE = REPO / "data" / "exp" / "7h1_gate_cache.jsonl"
EXPLORE = ("2024-04-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-12-31")
FEATS = ("fav_ppw", "fav_pp3", "gap12", "fav_ppw_norm", "gap12_norm",
         "bust_prob", "lead_rank")


def load(selected_only: bool):
    rows = []
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("scored"):
            continue
        if selected_only and not r.get("selected"):
            continue
        lead = int(str(r["legs_tf"][0]).split("-")[0])
        oth = list(r.get("others") or [])
        r["lead_rank"] = float(oth.index(lead)) if lead in oth else -1.0
        rows.append(r)
    return rows


def win(rows, w):
    return [r for r in rows if w[0] <= r["race_date"] <= w[1]]


def auc(y, s):
    """ラベル y（0/1）とスコア s の AUC（同値は 0.5 として扱う素朴実装）。"""
    pos = [a for a, b in zip(s, y) if b]
    neg = [a for a, b in zip(s, y) if not b]
    if not pos or not neg:
        return float("nan")
    neg_sorted = sorted(neg)
    import bisect
    tot = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg_sorted, p)
        hi = bisect.bisect_right(neg_sorted, p)
        tot += lo + (hi - lo) * 0.5
    return tot / (len(pos) * len(neg))


def stat(rows):
    n = len(rows)
    if not n:
        return "n=0"
    hit = sum(r["hit"] for r in rows)
    pay = sum(r["payout"] for r in rows)
    bet = sum(r["bet_amount"] for r in rows)
    fw = sum(r["fav_win"] for r in rows)
    return (f"n={n:5d} 本命1着={fw / n * 100:5.1f}% 的中={hit:3d}({hit / n * 100:5.2f}%) "
            f"ROI={pay / bet * 100:6.1f}%")


def main() -> None:
    sel = load(selected_only=True)
    print(f"7H1 選別・採点済み {len(sel)}件")

    print("\n## 0. オラクル上限（本命が1着のレースを完全に見抜いて外した場合）")
    for label, w in (("探索", EXPLORE), ("確認", CONFIRM)):
        sub = win(sel, w)
        keep = [r for r in sub if not r["fav_win"]]
        print(f"  {label}: 現行 {stat(sub)}")
        print(f"  {'':4s}  オラクル {stat(keep)}  （{len(sub) - len(keep)}件を除外）")

    print("\n## 1. 既存量の判定力（目的変数=本命1着・7H1選別内）")
    print(f"{'特徴':16s} {'探索AUC':>8} {'確認AUC':>8}")
    for f in FEATS:
        a = [auc([r["fav_win"] for r in win(sel, w)], [r[f] for r in win(sel, w)])
             for w in (EXPLORE, CONFIRM)]
        print(f"{f:16s} {a[0]:8.4f} {a[1]:8.4f}")

    print("\n## 2. 67特徴を使わない小モデル（LightGBM・窓をまたいで学習/評価）")
    try:
        import lightgbm as lgb
    except ImportError:
        print("  lightgbm 未導入のためスキップ")
        return
    # 学習は「7H1 選別前の全母集団」（件数を稼ぐ）。評価は選別後の 7H1 母集団。
    allrows = load(selected_only=False)
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15,
              "min_data_in_leaf": 200, "feature_fraction": 0.9, "verbose": -1,
              "seed": 42}
    for tr_w, te_w, name in ((EXPLORE, CONFIRM, "探索で学習→確認で評価"),
                             (CONFIRM, EXPLORE, "確認で学習→探索で評価")):
        tr = win(allrows, tr_w)
        Xtr = np.array([[r[f] for f in FEATS] for r in tr], dtype=float)
        ytr = np.array([r["fav_win"] for r in tr])
        m = lgb.train(params, lgb.Dataset(Xtr, label=ytr, feature_name=list(FEATS)),
                      num_boost_round=250)
        te = win(sel, te_w)
        Xte = np.array([[r[f] for f in FEATS] for r in te], dtype=float)
        p = m.predict(Xte)
        print(f"\n-- {name}  学習n={len(tr):,} 評価n={len(te)}  "
              f"AUC={auc([r['fav_win'] for r in te], list(p)):.4f}")
        order = sorted(zip(p, te), key=lambda x: -x[0])
        for frac in (0.10, 0.20, 0.30):
            k = int(len(order) * frac)
            drop = [r for _s, r in order[:k]]
            keep = [r for _s, r in order[k:]]
            print(f"   上位{frac:.0%}を見送り: 見送り群 {stat(drop)}")
            print(f"   {'':13s} 残る群   {stat(keep)}")


if __name__ == "__main__":
    main()
