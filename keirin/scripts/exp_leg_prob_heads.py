#!/usr/bin/env python3
"""三連単の買い目確率を「1ヘッド」から「複数ヘッドの合成」へ変える検証。

## 何が問題か

7T1 / 7T2 の買い目確率は `rank_7t1_pl_prob(win_probs, leg)` ＝
**1着率だけ**の Plackett-Luce:

    P(x,y,z) = s_x/S × s_y/(S−s_x) × s_z/(S−s_x−s_y)      s = pred_win

3つの着順すべてを**同じ強度ベクトル**で説明する仮定を置いており、
既に持っている 3着内率（`lgbm_wt_eval`）・2着内率（`lgbm_wt_top2`）・
大敗率（`lgbm_wt_bad`）を一切使っていない。

中央（JRA）の v27 は同じ形の問題を「順位回帰ヘッド + 着外率ヘッドの合成」
（`z_blend = z(−reg_rank) − 0.5 × z(out_probability)`）に置き換えて
1位馬勝率 27.08→28.40% / レース内 Spearman 0.4783→0.5094 を得た。
**オッズを一切使わずに、既にあるデータの組み合わせ方だけで改善している。**

## 測るもの（ROI では判断しない）

買い目の当たりやすさは順位づけの問題なので、**確率としての良し悪し**を直接見る:

- `logloss`  … 実際の決着 −log P（低いほど良い・proper scoring rule）
- `median順位` … 210通りの中で実際の決着が何位に来るか（低いほど良い）
- `top1/3/6`  … 上位k点に実際の決着が入る割合

🔴 **ROI で採否を決めない。** 買い目確率の改善は「同じ点数でより当たる」
   ことであって収益ではない（控除率の壁は動かない）。

## 位置ごとに強度を変えられるのが要点

    s1 = pw^a1 · p3^(1−a1)      1着に置く車の強度
    s2 = pw^a2 · p3^(1−a2)      2着
    s3 = pw^a3 · p3^(1−a3)      3着

a=1 が現行（全位置で1着率）。a=0 なら全位置で3着内率。
**3着に近いほど p3 寄りが良いはず**という仮説を、掃引で確かめる。
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path: str, limit: int | None = None) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("win"):
                continue
            rows.append({
                "race_date": r["race_date"],
                "p3": {int(k): float(v) for k, v in r["p3"].items()},
                "pw": {int(k): float(v) for k, v in r["pw"].items()},
                "win": list(r["win"]),
            })
            if limit and len(rows) >= limit:
                break
    return rows


def strengths(pw: dict[int, float], p3: dict[int, float], a: float) -> dict[int, float]:
    """1着率と3着内率の幾何ブレンド。a=1 が現行（1着率のみ）。"""
    if a >= 1.0:
        return dict(pw)
    if a <= 0.0:
        return dict(p3)
    return {c: max(pw[c], 1e-9) ** a * max(p3[c], 1e-9) ** (1.0 - a) for c in pw}


def leg_probs(pw, p3, a1: float, a2: float, a3: float) -> dict[tuple, float]:
    """全順列の確率。位置ごとに別の強度ベクトルを使う一般化 Plackett-Luce。

    ⚠️ 位置ごとに強度を変えると**確率の和が1にならない**ので最後に正規化する。
       絶対値は使わず順位づけにしか使わないが、logloss を測るには正規化が要る。
    """
    s1 = strengths(pw, p3, a1)
    s2 = strengths(pw, p3, a2)
    s3 = strengths(pw, p3, a3)
    out = {}
    tot = 0.0
    cars = list(pw)
    for x, y, z in itertools.permutations(cars, 3):
        d2 = sum(s2[c] for c in cars if c != x)
        d3 = sum(s3[c] for c in cars if c not in (x, y))
        if d2 <= 0 or d3 <= 0:
            continue
        v = (s1[x] / sum(s1.values())) * (s2[y] / d2) * (s3[z] / d3)
        out[(x, y, z)] = v
        tot += v
    if tot <= 0:
        return {}
    return {k: v / tot for k, v in out.items()}


def evaluate(rows: list[dict], a1: float, a2: float, a3: float) -> dict:
    ll, ranks, t1, t3, t6, n = 0.0, [], 0, 0, 0, 0
    for r in rows:
        p = leg_probs(r["pw"], r["p3"], a1, a2, a3)
        if not p:
            continue
        wins = [tuple(int(x) for x in w.split("-")) for w in r["win"]]
        # 🔴 同着では当たり目が複数。**確率は足す**（どれかが来ればよい）
        q = sum(p.get(w, 0.0) for w in wins)
        if q <= 0:
            continue
        n += 1
        ll += -math.log(q)
        order = sorted(p, key=lambda k: -p[k])
        rk = min(order.index(w) + 1 for w in wins if w in p)
        ranks.append(rk)
        t1 += rk <= 1
        t3 += rk <= 3
        t6 += rk <= 6
    return dict(n=n, logloss=ll / n, med_rank=st.median(ranks),
                top1=t1 / n, top3=t3 / n, top6=t6 / n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--split", default=None,
                    help="YYYY-MM-DD。**この日より前で係数を選び、以降で確認する**")
    args = ap.parse_args()

    rows = load(args.cache, args.limit)
    if args.split:
        # 🔴 掃引と確認を**時間で**分ける。同じデータで係数を選んで同じデータで
        #    良さを主張すると、必ず上振れを掴む。
        sel = [r for r in rows if r["race_date"] < args.split]
        conf = [r for r in rows if r["race_date"] >= args.split]
        print(f"掃引窓 {len(sel)}R（〜{args.split}） / 確認窓 {len(conf)}R（{args.split}〜）\n")
    else:
        sel, conf = rows, []
        print(f"評価 {len(rows)}R（決着の確定しているレースのみ）\n")
    arms = [
        ("現行: 全位置 1着率 (1,1,1)", 1.0, 1.0, 1.0),
        ("全位置 3着内率 (0,0,0)", 0.0, 0.0, 0.0),
        ("全位置 半々 (.5,.5,.5)", .5, .5, .5),
        ("後ろほど p3 (1,.5,0)", 1.0, .5, 0.0),
        ("後ろほど p3 (1,.75,.5)", 1.0, .75, .5),
        ("後ろほど p3 (1,1,.5)", 1.0, 1.0, .5),
        ("後ろほど p3 (1,1,0)", 1.0, 1.0, 0.0),
        ("後ろほど p3 (.75,.5,.25)", .75, .5, .25),
        ("逆: 後ろほど pw (0,.5,1)", 0.0, .5, 1.0),
    ]
    def run(data, title):
        print(f"--- {title} ---")
        print(f"{'案':34}{'logloss':>9}{'中央順位':>9}{'top1':>8}{'top3':>8}{'top6':>8}")
        base = None
        out = {}
        for name, a1, a2, a3 in arms:
            r = evaluate(data, a1, a2, a3)
            out[name] = r
            if base is None:
                base = r
            d = "" if r is base else f"  Δll {r['logloss'] - base['logloss']:+.4f}"
            print(f"{name:34}{r['logloss']:>9.4f}{r['med_rank']:>9.0f}"
                  f"{r['top1']:>8.2%}{r['top3']:>8.2%}{r['top6']:>8.2%}{d}")
        print()
        return out

    a = run(sel, "掃引窓" if conf else "全期間")
    if conf:
        b = run(conf, "確認窓（係数を選んだ後に一度だけ見る）")
        best = min((k for k in a if k != arms[0][0]), key=lambda k: a[k]["logloss"])
        base = arms[0][0]
        print(f"掃引窓の最良: {best}")
        print(f"  掃引窓 Δtop1 {(a[best]['top1']-a[base]['top1'])*100:+.2f}pt / "
              f"Δlogloss {a[best]['logloss']-a[base]['logloss']:+.4f}")
        print(f"  確認窓 Δtop1 {(b[best]['top1']-b[base]['top1'])*100:+.2f}pt / "
              f"Δlogloss {b[best]['logloss']-b[base]['logloss']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
