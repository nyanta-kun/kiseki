#!/usr/bin/env python3
"""落車・失格(DNF)レースの学習重みが**型ラボの型判定**に効くかを測る（2026-08-28）。

    PYTHONPATH=. .venv/bin/python3 scripts/exp_dnf_weight.py \
        --models lgbm_wt_expdnf100,lgbm_wt_expdnf050,lgbm_wt_expdnf000 \
        --from 2026-01-01 --to 2026-08-26

## 何を見るのか

型ラボは `axis_sum`（3着内率の上位2車の合計）を **絶対閾値 1.44** と比べて
堅い(A/B/C) / 混戦(D/E/F) に割る。したがって効くのは AUC ではなく:

  ① 予測の水準が動くか  … 動くと 1.44 の意味が変わる（＝閾値ごと引き直しが要る）
  ② 堅い割合が動くか    … ①の帰結
  ③ 二軸そろい率の分離  … 堅い − 混戦。型判定そのものの利き
  ④ 軸1の3着内率        … 既存の指標との接続（2026-08-04 の検証で使われた）

🔴 **評価は全レース**（DNF が起きたレースも含む）。学習の重みだけを変える。
   事故込みが実運用なので、評価から外すと選択バイアスになる。

🔴 **特徴量は1回だけ作って全モデルで使い回す**。モデルごとに作り直すと
   時間がかかるうえ、特徴量が同一であることを保証できない。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.models.trainer import load_model          # noqa: E402
from src.preprocessing.feature_wt import (         # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)

AXIS_SUM_FIRM = 1.44


def _finish_and_dnf(keys: list[str]) -> tuple[dict, dict]:
    """({race_key: {車番: 着順}}, {race_key: {DNF の車番}})。"""
    fin: dict[str, dict[int, int]] = defaultdict(dict)
    dnf: dict[str, set[int]] = defaultdict(set)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order IS NOT NULL")
            for rk, fn, fo in c.execute(q, ch).fetchall():
                if int(fo) == 0:
                    dnf[rk].add(int(fn))
                else:
                    fin[rk][int(fn)] = int(fo)
    return dict(fin), dict(dnf)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="カンマ区切りのモデル名")
    ap.add_argument("--from", dest="d1", default="2026-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-26")
    ap.add_argument("--n-entries", type=int, default=7)
    a = ap.parse_args()

    print(f"[exp] 特徴量を作る {a.d1}〜{a.d2} ...", flush=True)
    feats = build_features_wt(load_raw_data_wt(min_date=a.d1, max_date=a.d2))
    if feats is None or not len(feats):
        raise SystemExit("特徴量が作れませんでした")
    X = prepare_X(feats)
    rks = list(feats["race_key"])
    fns = [int(v) for v in feats["frame_no"]]

    keys = sorted(set(rks))
    with get_connection() as c:
        ne = {r[0]: int(r[1]) for r in c.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (a.d1, a.d2)).fetchall() if r[1] is not None}
    fin, dnf = _finish_and_dnf(keys)

    print(f"[exp] 対象 {len(keys):,}R（{a.n_entries}車 "
          f"{sum(1 for k in keys if ne.get(k) == a.n_entries):,}R）\n", flush=True)

    head = (f"{'モデル':22} {'予測平均':>8} {'堅い割合':>8} "
            f"{'堅いそろい':>10} {'混戦そろい':>10} {'分離':>8} {'軸1の3着内':>10}")
    print(head)
    print("-" * len(head))
    for name in a.models.split(","):
        name = name.strip()
        try:
            p3v = load_model(name).predict_proba(X)[:, 1]
        except FileNotFoundError:
            print(f"{name:22}  （モデルが見つかりません）")
            continue
        p3: dict[str, dict[int, float]] = defaultdict(dict)
        for rk, fn, v in zip(rks, fns, p3v):
            p3[rk][fn] = float(v)

        agg = {"firm": [0, 0], "loose": [0, 0]}
        a1_hit = a1_n = 0
        for rk, probs in p3.items():
            if ne.get(rk) != a.n_entries or len(probs) != a.n_entries:
                continue
            f = fin.get(rk, {})
            if len(f) < 3:                      # 3着まで確定していない
                continue
            order = sorted(probs, key=lambda c: (-probs[c], c))
            a1, a2 = order[0], order[1]
            side = "firm" if probs[a1] + probs[a2] >= AXIS_SUM_FIRM else "loose"
            agg[side][1] += 1
            if 1 <= f.get(a1, 99) <= 3 and 1 <= f.get(a2, 99) <= 3:
                agg[side][0] += 1
            a1_n += 1
            a1_hit += 1 if 1 <= f.get(a1, 99) <= 3 else 0

        n_all = agg["firm"][1] + agg["loose"][1]
        firm = agg["firm"][0] / max(agg["firm"][1], 1) * 100
        loose = agg["loose"][0] / max(agg["loose"][1], 1) * 100
        print(f"{name:22} {p3v.mean():8.4f} "
              f"{agg['firm'][1] / max(n_all, 1) * 100:7.1f}% "
              f"{firm:9.2f}% {loose:9.2f}% {firm - loose:+7.2f}pt "
              f"{a1_hit / max(a1_n, 1) * 100:9.2f}%")

    n_dnf = sum(1 for k in keys if dnf.get(k))
    print(f"\n（窓内で DNF が起きたレース: {n_dnf:,} / {len(keys):,} = "
          f"{n_dnf / max(len(keys), 1) * 100:.1f}%・**評価には全部含めている**）")


if __name__ == "__main__":
    main()
