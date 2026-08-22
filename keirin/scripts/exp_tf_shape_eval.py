#!/usr/bin/env python3
"""三連単の「買い目の形」をオフラインで比較する（`exp_tf_shape_cache.py` の出力を読む）。

## 比較する形（すべて 1着=軸1 固定・二軸ブランドを保つ）

| 記号 | 形 | 点数 |
|---|---|---|
| `F1` | 1着=軸1 / 2着=軸2 / 3着=相手 | k |
| `F2` | 1着=軸1 / 2・3着に軸2と相手（**両順**） | 2k |
| `F3` | F2 の候補から目的値上位 k 点だけ採る（**ユーザー案の「2〜3点に絞る」**） | k |

🔴 **理論上、点数は `P(払戻>=T)` を動かさない。**
1点 = 10,000/k 円なので `払戻>=T ⟺ オッズ >= kT/10,000`。市場効率下で
`p_i ≒ ROI/o_i` なら `Σp_i <= ROI × k ÷ (kT/10,000) = ROI × 10,000/T` で **k が消える**。
それでも実測で差が出るなら、原因は
①帯 ROI が完全にはフラットでない ②**予測オッズと確定オッズのずれ** のどちらか。
この2つを分けて見るために、選別に使う `T_sel` と到達判定の `T_eval` を分離してある
（`T_sel > T_eval` が下振れマージン m）。

## 点数の決め方（形によらず共通・現行 7T1 と同じ自己整合）

    k 点等分なら 1点 = floor(10000/k/100)*100 円
    払戻 >= T_sel ⟺ その点のオッズ >= T_sel / 1点あたり賭け金
    → 「その足切りを通る点」を Σ(Plackett-Luce 確率) 最大で k 点採るときの
      目的値が最大になる k を選ぶ

⚠️ 足切りは `T_sel × k / 10000` ではなく **切り捨て後の実額**で割ること
（3点なら 3,333 ではなく 3,300 円。ここを間違えると最小の点だけ目標に届かない）。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_wt import (  # noqa: E402
    RANK_7T1_KMAX, rank_7t1_is_cross_line, rank_7t1_is_target_race_type,
    rank_7t1_pl_prob, unit_stake,
)

BUDGET = 10_000


# ── 軸の選び方 ────────────────────────────────────────────────────────────
def axis_simple(p3: dict[int, float], pw: dict[int, float]) -> tuple[int, int]:
    """軸1 = 1着率最上位 / 軸2 = 3着内率最上位（軸1を除く）。

    現行 7T1 は42順序対を総当たりして目的値最大の対を採るが、ここでは
    **形だけを比べたい**ので軸の決め方を固定する。
    """
    a1 = max(pw, key=lambda f: (pw[f], p3[f]))
    a2 = max((f for f in p3 if f != a1), key=lambda f: p3[f])
    return a1, a2


# ── 形ごとの候補脚 ────────────────────────────────────────────────────────
def legs_f1(a1: int, a2: int, cars: list[int]) -> list[str]:
    return [f"{a1}-{a2}-{c}" for c in cars if c not in (a1, a2)]


def legs_f2(a1: int, a2: int, cars: list[int]) -> list[str]:
    out = []
    for c in cars:
        if c in (a1, a2):
            continue
        out.append(f"{a1}-{a2}-{c}")
        out.append(f"{a1}-{c}-{a2}")
    return out


SHAPES = {"F1": legs_f1, "F2": legs_f2, "F3": legs_f2}
#: F3 は F2 と同じ候補集合から**点数を自己整合で絞る**（別の候補集合ではない）
_KMAX = {"F1": RANK_7T1_KMAX, "F2": 2 * RANK_7T1_KMAX, "F3": 3}


def select(cand: list[str], odds: dict[str, float], pw: dict[int, float],
           t_sel: int, kmax: int) -> tuple[list[str], int] | None:
    """自己整合で点数と買い目を決める。返り値は (買い目, 1点あたり賭け金)。"""
    best = None
    for k in range(1, min(kmax, len(cand)) + 1):
        stake = unit_stake(k)
        need = t_sel / stake
        elig = [l for l in cand if odds.get(l, 0.0) >= need]
        if len(elig) < k:
            continue
        elig.sort(key=lambda l: -(rank_7t1_pl_prob(pw, l) or 0.0))
        take = elig[:k]
        obj = sum(rank_7t1_pl_prob(pw, l) or 0.0 for l in take)
        if best is None or obj > best[0]:
            best = (obj, take, stake)
    return (best[1], best[2]) if best else None


# ── 母集団 ────────────────────────────────────────────────────────────────
def pops(row: dict, p3: dict[int, float]) -> dict[str, bool]:
    lg = {int(k): v for k, v in row["line_group"].items()}
    lp = {int(k): v for k, v in row["line_pos"].items()}
    cross = rank_7t1_is_cross_line(p3, lg, lp)
    final = rank_7t1_is_target_race_type(row.get("race_type"))
    return {"全7車": True, "別ライン": bool(cross),
            "決勝系×別ライン": bool(cross and final)}


def evaluate(rows: list[dict], shape: str, t_sel: int, t_eval: int,
             pop: str, cap: int) -> dict | None:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        p3 = {int(k): v for k, v in row["p3"].items()}
        pw = {int(k): v for k, v in row["pw"].items()}
        if not pops(row, p3)[pop]:
            continue
        a1, a2 = axis_simple(p3, pw)
        cars = sorted(p3)
        cand = [l for l in SHAPES[shape](a1, a2, cars) if l in row["_board"]]
        if not cand:
            continue
        sel = select(cand, row["odds"], pw, t_sel, _KMAX[shape])
        if sel is None:
            continue
        legs, stake = sel
        bet = stake * len(legs)
        payout = 0
        for l in legs:
            if l in row["win"]:
                payout = row["win"][l] * stake // 100
                break
        by_day[row["race_date"]].append(dict(
            bet=bet, payout=payout, hit=int(payout > 0), n=len(legs),
            # 選別の順序に使う: 期待回収倍率
            ev=sum((rank_7t1_pl_prob(pw, l) or 0.0) * row["odds"].get(l, 0.0)
                   for l in legs) * stake / max(bet, 1)))

    rois, ns, hits, big, zero, npt = [], [], [], 0, 0, []
    tb = tp = 0
    paylist = []
    for d, ps in by_day.items():
        sel = sorted(ps, key=lambda r: -r["ev"])[:cap]
        if not sel:
            continue
        b = sum(r["bet"] for r in sel); p = sum(r["payout"] for r in sel)
        rois.append(p / b); ns.append(len(sel)); hits.append(sum(r["hit"] for r in sel))
        npt += [r["n"] for r in sel]
        paylist += [r["payout"] for r in sel if r["payout"] > 0]
        tb += b; tp += p
        big += sum(1 for r in sel if r["payout"] >= t_eval)
        zero += (p == 0)
    if not rois:
        return None
    n = len(rois); s = sorted(rois)
    return dict(days=n, per_day=st.mean(ns), n_pts=st.mean(npt), roi=tp / tb,
                hit_rate=sum(hits) / sum(ns), hits=st.mean(hits),
                over100=sum(1 for r in rois if r >= 1) / n, zero=zero / n,
                med=st.median(rois), p90=s[int(n * .9)], big=big / n,
                med_pay=int(st.median(paylist)) if paylist else 0,
                reach=(big / sum(hits)) if sum(hits) else 0.0)


def load(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            r["_board"] = set(r["board"])
            rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--t-eval", type=int, default=150_000)
    ap.add_argument("--t-sels", default="150000,200000,300000")
    ap.add_argument("--caps", default="15")
    args = ap.parse_args()

    rows = load(args.cache)
    days = len({r["race_date"] for r in rows})
    print(f"キャッシュ {len(rows)}R / {days}日  到達判定 T_eval={args.t_eval:,}\n")

    hdr = (f"{'形':4}{'母集団':16}{'T_sel':>8}{'m':>5}{'N':>4}{'件/日':>6}{'点':>5}"
           f"{'ROI':>7}{'的中%':>7}{'到達/日':>8}{'到達率':>7}{'100%超':>7}"
           f"{'0円日':>7}{'中央払戻':>9}")
    print(hdr)
    for shape in ("F1", "F2", "F3"):
        for pop in ("全7車", "別ライン", "決勝系×別ライン"):
            for t_sel in [int(x) for x in args.t_sels.split(",")]:
                for cap in [int(x) for x in args.caps.split(",")]:
                    r = evaluate(rows, shape, t_sel, args.t_eval, pop, cap)
                    if not r:
                        continue
                    print(f"{shape:4}{pop:16}{t_sel:>8,}{t_sel/args.t_eval:>5.2f}{cap:>4}"
                          f"{r['per_day']:>6.1f}{r['n_pts']:>5.1f}{r['roi']:>7.1%}"
                          f"{r['hit_rate']:>7.2%}{r['big']:>8.2f}{r['reach']:>7.1%}"
                          f"{r['over100']:>7.1%}{r['zero']:>7.1%}{r['med_pay']:>9,}")
        print("-" * len(hdr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
