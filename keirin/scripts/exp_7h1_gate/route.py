#!/usr/bin/env python3
"""「本命が勝つ／生きているレース」を判定して 7H1 の券種を振り分けられるか。

## 構造（`switch_trio.py` の実測）

7H1 が選んだレースは、本命の生死で**買い目の当たり方が完全に排他**になる:

|            | 三連単F8点(現行) | 三連複 二軸総流し5点 |
|---|---|---|
| 本命がバスト (約27%) | 的中 17〜19% / ROI 277〜317% | **的中 0%** |
| 本命が生存 (約73%)   | **的中 0%**                  | 的中 49〜57% / ROI 90〜107% |

したがって「どちらの世界かを当てる」判定器があれば大きな利得がある。
本スクリプトはその判定器を**本番と同じ67特徴**で月次walk-forwardに学習し、
振り分けたときの合成成績を2窓で測る。

⚠️ 学習は「その月より前のレースだけ」。特徴量自体も月次凍結 vintage
   （`build_cache.py --out data/exp/7h1_feat_*.jsonl` が生成）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/route.py
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.exp_7h1_gate.switch_trio import score as score_both  # noqa: E402

FEAT_GLOB = str(REPO / "data" / "exp" / "7h1_feat_*.jsonl")
WINDOWS = (("探索 2024-04〜2025-12", "2024-04-01", "2025-12-31"),
           ("確認 2026-01〜", "2026-01-01", "2026-12-31"))


def load_feat():
    rows = []
    for path in sorted(glob.glob(FEAT_GLOB)):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def auc(y, s):
    pos = [a for a, b in zip(s, y) if b]
    neg = sorted(a for a, b in zip(s, y) if not b)
    if not pos or not neg:
        return float("nan")
    tot = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        hi = bisect.bisect_right(neg, p)
        tot += lo + (hi - lo) * 0.5
    return tot / (len(pos) * len(neg))


def walk_forward_scores(rows, target: str):
    """月次walk-forward（その月より前だけで学習）で全行にOOSスコアを付ける。"""
    import lightgbm as lgb
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 80, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 42}
    months = sorted({r["race_date"][:7] for r in rows})
    out: dict[str, float] = {}
    for m in months:
        tr = [r for r in rows if r["race_date"][:7] < m]
        te = [r for r in rows if r["race_date"][:7] == m]
        if len(tr) < 5000:
            continue                     # 学習量が足りない先頭の月は捨てる
        X = np.array([r["feat"] for r in tr], dtype=float)
        y = np.array([r[target] for r in tr])
        mdl = lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=300)
        p = mdl.predict(np.array([r["feat"] for r in te], dtype=float))
        for r, v in zip(te, p):
            out[r["race_key"]] = float(v)
    return out


def stat(items, pick):
    """pick(x) が '_tf' か '_tr' を返す。合成結果を集計する。"""
    n = len(items)
    if not n:
        return "n=0"
    hit = pay = bet = real = 0
    pays = []
    for x in items:
        d = x[pick(x)]
        hit += d["hit"]
        pay += d["payout"]
        bet += d["bet"]
        if d["hit"]:
            pays.append(d["payout"])
            if d["payout"] > d["bet"]:
                real += 1
    pays.sort()
    med = pays[len(pays) // 2] if pays else 0
    return (f"n={n:5d} 的中{hit:4d}({hit / n * 100:5.2f}%) 表示{real / n * 100:5.2f}% "
            f"ROI={pay / bet * 100:6.1f}% 払戻中央={med:,}円 "
            f"2万+={sum(1 for x in pays if x >= 20000)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="fav_alive",
                    choices=("fav_alive", "fav_win"),
                    help="判定する事象。fav_alive=本命が3着以内")
    args = ap.parse_args()

    feats = load_feat()
    for r in feats:
        r["fav_alive"] = 1 - r["fav_bust"]
    print(f"母集団（軸1==◎・7車・買い目成立）{len(feats):,}件 "
          f"/ 本命3着内率 {np.mean([r['fav_alive'] for r in feats]) * 100:.2f}%")

    scores = walk_forward_scores(feats, args.target)
    print(f"OOSスコアを付けた行: {len(scores):,}")

    sel = [r for r in feats if r.get("selected") and r.get("scored")
           and r["race_key"] in scores]
    scored = score_both(sel)              # 両券種の結果を採点
    by_key = {x["race_key"]: x for x in scored}
    print(f"7H1 選別かつ両券種採点済み: {len(scored)}件")

    for label, a, b in WINDOWS:
        sub = [x for x in scored if a <= x["race_date"] <= b]
        if not sub:
            continue
        y = [1 - x["fav_bust"] for x in sub]
        s = [scores[x["race_key"]] for x in sub]
        print(f"\n== {label}  判定器AUC(本命3着内)={auc(y, s):.4f} ==")
        print(f"  常に三連単   {stat(sub, lambda x: '_tf')}")
        print(f"  常に三連複   {stat(sub, lambda x: '_tr')}")
        order = sorted(sub, key=lambda x: -scores[x["race_key"]])
        for frac in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            k = int(len(order) * frac)
            top = {x["race_key"] for x in order[:k]}   # 本命が生きていそう→三連複
            print(f"  上位{frac:.0%}を三連複へ  "
                  + stat(sub, lambda x: "_tr" if x["race_key"] in top else "_tf"))
        # オラクル（実際の生死で振り分け）
        print("  【オラクル】     "
              + stat(sub, lambda x: "_tr" if not x["fav_bust"] else "_tf"))
    _ = by_key


if __name__ == "__main__":
    main()
