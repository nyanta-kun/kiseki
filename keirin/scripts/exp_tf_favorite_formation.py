#!/usr/bin/env python3
"""「本命が勝てそうか」でレースを厳選し、着固定フォーメーションを比べる。

## ユーザー指示（2026-08-23）

> 着を固定したフォーメーションによる買い目点数の削減検討。
> 1番人気が固いレースでの1着固定と、1番人気の信頼度が低いレースにおける
> 1番人気以外を1着に固定した買い目での比較。
> **いずれも全レース対象ではなく、1番人気が勝てそうかをレースとして予測した上で
> 購入レースの厳選をベースとする**

## 設計

### 「1番人気」の定義（🔴 競輪には単勝の板が無い）

朝8:00 に手に入るもので市場の本命を作る:

  - `market`  … 予測三連単板を周辺化した市場1着率のトップ
                （`Σ_{j,k} 1/o_{i-j-k}` を i について集計）
  - `mark`    … winticket 公式印 ◎
  - `model`   … モデルの1着率トップ（＝市場ではない。比較用）

### 「勝てそうか」のレース単位予測

本命の **`pw`（1着率）** と **`bad`（大敗率＝6着以下）** の2ヘッドで測る。
🔴 `bad` は 7H1 が使う唯一の「市場に無い」実証済みシグナル（AUC 0.6848）。
`pw` だけだと市場と同じ向きの情報しか見ていない。

    firm_score = z(pw_fav) − z(bad_fav)      レース内ではなくレース間で標準化

上位を「固い」・下位を「信頼度が低い」とする。**厳選率を掃引する**。

### 比較する買い目（すべて1レース1万円・均等）

| 記号 | 1着 | 2着 | 3着 |
|---|---|---|---|
| `FAV-a` | 本命固定 | 上位k車 | 上位m車 |
| `FAV-b` | 本命固定 | 総流し | 総流し（点数削減の比較用） |
| `ANTI`  | **本命以外**固定（モデル上位で本命でない最上位） | 本命含む上位 | 上位 |
| `ANTI-L`| **別ライン先頭**固定（7H1 と同じ考え方） | 上位 | 上位 |

🔴 **ROI で採否を決めない。** 判定は
「回収率100%超の日」「目標額（点数×1万円）到達の頻度」「0円の日」。
ただし全数走査で妙味が見つかっていない以上、**ROI が壁を超えることは期待しない**。
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

random.seed(91)
BUDGET = 10_000


def market_win_probs(row: dict) -> dict[int, float]:
    """予測三連単板を周辺化した『市場の1着率』。競輪に単勝の板は無いので作る。"""
    inv: dict[int, float] = defaultdict(float)
    tot = 0.0
    for leg, o in row["odds"].items():
        if not o or o <= 0:
            continue
        v = 1.0 / o
        inv[int(leg.split("-")[0])] += v
        tot += v
    return {c: v / tot for c, v in inv.items()} if tot > 0 else {}


def favorite(row: dict, kind: str) -> int | None:
    if kind == "market":
        m = market_win_probs(row)
        return max(m, key=m.get) if m else None
    if kind == "mark":
        for c, mk in row.get("mark", {}).items():
            if mk and str(mk).strip() in ("◎", "1"):
                return int(c)
        return None
    pw = {int(k): v for k, v in row["pw"].items()}
    return max(pw, key=pw.get) if pw else None


def firm_score(row: dict, fav: int) -> float | None:
    """本命が『勝てそう』か。1着率が高く・大敗率が低いほど大。"""
    pw = {int(k): v for k, v in row["pw"].items()}
    bad = {int(k): v for k, v in (row.get("bad") or {}).items()}
    if fav not in pw or fav not in bad:
        return None
    return float(pw[fav]) - float(bad[fav])


def _order(row: dict) -> list[int]:
    p3 = {int(k): v for k, v in row["p3"].items()}
    return [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]


def legs_for(row: dict, shape: str, fav: int, k2: int, k3: int) -> list[str]:
    """フォーメーションを展開する。返すのは着順つきの目。"""
    order = _order(row)
    lg = {int(k): v for k, v in row["line_group"].items()}
    lp = {int(k): v for k, v in row["line_pos"].items()}
    if shape.startswith("FAV"):
        first = fav
    elif shape == "ANTI":
        first = next((c for c in order if c != fav), None)
    else:                                   # ANTI-L: 別ライン先頭
        first = next((c for c in order
                      if c != fav and lg.get(c) != lg.get(fav)
                      and str(lp.get(c)) in ("1", "None", "")), None)
        if first is None:
            first = next((c for c in order if c != fav and lg.get(c) != lg.get(fav)), None)
    if first is None:
        return []
    rest = [c for c in order if c != first]
    second = rest[:k2]
    third = rest[:k3]
    out = []
    for y in second:
        for z in third:
            if z == y:
                continue
            out.append(f"{first}-{y}-{z}")
    return out


def run(rows, shape, fav_kind, k2, k3, keep_frac, firm_high: bool, cap=20):
    scored = []
    for row in rows:
        fav = favorite(row, fav_kind)
        if fav is None:
            continue
        fs = firm_score(row, fav)
        if fs is None:
            continue
        scored.append((fs, row, fav))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0] if firm_high else t[0])
    keep = scored[:max(1, int(len(scored) * keep_frac))]

    by_day = defaultdict(list)
    for fs, row, fav in keep:
        legs = [l for l in legs_for(row, shape, fav, k2, k3) if l in row["_board"]]
        if not legs:
            continue
        stake = unit_stake(len(legs))
        bet = stake * len(legs)
        pay = next((row["win"][l] * stake // 100 for l in legs if l in row["win"]), 0)
        by_day[row["race_date"]].append((bet, pay, len(legs), fs))
    if not by_day:
        return None
    days = []
    for d, ps in by_day.items():
        sel = sorted(ps, key=lambda x: -x[3])[:cap]
        days.append((sum(x[0] for x in sel), sum(x[1] for x in sel), len(sel),
                     sum(1 for x in sel if x[1] > 0),
                     st.mean([x[2] for x in sel]),
                     [x[1] for x in sel if x[1] > 0]))
    n = len(days)
    bet = sum(x[0] for x in days); pay = sum(x[1] for x in days)
    rois = [x[1] / x[0] for x in days]
    pl = sorted(p for x in days for p in x[5])
    target = int(st.mean([x[2] for x in days])) * 10_000
    return dict(days=n, per_day=st.mean([x[2] for x in days]),
                pts=st.mean([x[4] for x in days]),
                roi=pay / bet, hit=sum(x[3] for x in days) / sum(x[2] for x in days),
                over100=sum(1 for r in rois if r >= 1) / n,
                zero=sum(1 for r in rois if r == 0) / n,
                big=sum(1 for x in days for p in x[5] if p >= target) / n,
                med=(st.median(pl) if pl else 0), target=target)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--fav", default="market", choices=("market", "mark", "model"))
    ap.add_argument("--cap", type=int, default=20)
    args = ap.parse_args()

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("win") or not r.get("bad"):
                continue
            r["_board"] = set(r["board"])
            rows.append(r)
    print(f"{len(rows):,}R / 本命の定義 = {args.fav} / 1日 {args.cap}件\n")

    hdr = (f"{'買い目':10}{'選別':16}{'厳選率':>7}{'件/日':>6}{'点':>5}{'的中%':>7}"
           f"{'ROI':>8}{'100%超':>8}{'0円日':>7}{'目標到達/日':>11}{'中央払戻':>10}")
    print(hdr)
    for shape, k2, k3, firm in (
            ("FAV-a", 2, 3, True), ("FAV-a", 2, 4, True), ("FAV-a", 3, 4, True),
            ("FAV-b", 6, 6, True),
            ("ANTI", 2, 3, False), ("ANTI", 3, 4, False),
            ("ANTI-L", 2, 3, False), ("ANTI-L", 3, 4, False)):
        for frac in (0.1, 0.25, 0.5, 1.0):
            r = run(rows, shape, args.fav, k2, k3, frac, firm, args.cap)
            if not r:
                continue
            sel = "本命が固い上位" if firm else "本命の信頼度が低い側"
            print(f"{f'{shape} {k2}x{k3}':10}{sel:16}{frac:>7.0%}{r['per_day']:>6.1f}"
                  f"{r['pts']:>5.1f}{r['hit']:>7.2%}{r['roi']:>8.1%}"
                  f"{r['over100']:>8.1%}{r['zero']:>7.1%}{r['big']:>11.2f}"
                  f"{r['med']:>10,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
