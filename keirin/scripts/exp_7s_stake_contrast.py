#!/usr/bin/env python3
"""賭け金の**メリハリ配分**（2026-08-23・ユーザー提案）。

## 提案

> 買い目を絞れない場合もオッズのメリハリをつけるのが良い。
> 買い目の総数の半分より下は**レースの投資金額の1.2倍目安の払い戻し**での購入金額設定、
> 残りを上位に割り当てるような調整が良いのではないか。

## 🔴 目的関数を ROI にしてはいけない

買う目の集合が同じなら**的中率は1ptも動かない**。そして1レースの投資が
10,000円で固定なら `ROI = E[払戻] / 10,000` なので、**配分で ROI が動くのは
我々の確率が市場より正確なときだけ**。このプロジェクトは「市場は効率的」を
何度も確認しているので、期待できるのは ROI ではなく**払戻の形**:

  - **ガミ率**（的中したのに払戻 < 投資）の低下 ← 提案の「1.2倍を目安」が狙うもの
  - **日次100%超の割合**の改善

## 腕（7S＝軸2車＋残り5車の総流し・1レース10,000円）

| 腕 | 配分 |
|---|---|
| 現行 | 均等（2,000円 × 5点） |
| B | **下位2点**は払戻が 1.2×10,000 になる額、残りを上位3点へ均等 |
| C | **下位3点**を同様にし、残りを上位2点へ均等 |
| D | 全点ダッチング（どこが当たっても払戻が等しい）※参考 |
| E | 全点で 1.2倍を保証し、余りを上位へ ※参考 |

⚠️ **本スクリプトは配分に確定オッズを使う**（`wt_odds`）。本番は朝の**予測オッズ**で
   配分するので、ここで出る数字は**上限**。効果が見合うと分かってから
   予測オッズ版を作る。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_7s_leg_selection import CONFIRM, SEARCH_END, build  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402

BUDGET = 10_000
UNIT = 100
TARGET = 1.2          # 下位の目標払戻＝投資の1.2倍


def _round(x: int) -> int:
    return max(UNIT, (int(x) // UNIT) * UNIT)


def allocate(rule: str, legs: list[int], odds: dict, budget: int = BUDGET):
    """legs は p3 降順。→ {leg: 賭け金}（合計は budget 以下）。"""
    n = len(legs)
    if rule == "現行(均等)":
        st = _round(budget // n)
        return {c: st for c in legs}
    if rule.startswith("B:") or rule.startswith("C:"):
        k = 2 if rule.startswith("B:") else 3
        low, high = legs[n - k:], legs[:n - k]
        if not high:
            return None
        out = {}
        for c in low:
            o = odds.get(c)
            if not o or o <= 0:
                return None
            out[c] = _round(TARGET * budget / o)
        used = sum(out.values())
        if used >= budget or not high:
            return None          # 下位だけで枠を食い潰す＝この配分は成立しない
        st = _round((budget - used) // len(high))
        for c in high:
            out[c] = st
        return out
    if rule == "D:全点ダッチング":
        inv = [1.0 / odds[c] for c in legs if odds.get(c)]
        if len(inv) != n:
            return None
        s = sum(inv)
        return {c: _round(budget * (1.0 / odds[c]) / s) for c in legs}
    if rule == "E:全点1.2倍保証+上位へ":
        out = {}
        for c in legs:
            o = odds.get(c)
            if not o or o <= 0:
                return None
            out[c] = _round(TARGET * budget / o)
        used = sum(out.values())
        if used > budget:
            return None
        out[legs[0]] += _round(budget - used)
        return out
    raise ValueError(rule)


RULES = ["現行(均等)", "B:下位2点を1.2倍", "C:下位3点を1.2倍",
         "D:全点ダッチング", "E:全点1.2倍保証+上位へ"]


def run(rows, board, label):
    print(f"\n===== {label} ・ {len(rows):,}R =====")
    print(f"{'配分':>22}{'成立率':>8}{'的中%':>8}{'ROI':>8}{'ガミ率':>8}"
          f"{'中央払戻':>10}{'最大払戻':>11}{'100%超の日':>11}")
    for rule in RULES:
        d = defaultdict(lambda: [0.0, 0.0])
        n = ok = hit = gami = 0
        pays, mx = [], 0
        for r in rows:
            b = board.get(r["key"])
            if not b:
                continue
            n += 1
            ks = {c: frozenset((r["a1"], r["a2"], c)) for c in r["rest"]}
            ks = {c: k for c, k in ks.items() if k in b}
            if len(ks) < 5:
                continue
            od = {c: b[k] for c, k in ks.items()}
            st = allocate(rule, [c for c in r["rest"] if c in ks], od)
            if st is None:
                continue
            ok += 1
            bet = sum(st.values())
            pay = sum(int(od[c] * 100) * st[c] // 100
                      for c, k in ks.items() if k in r["wins"])
            h = any(k in r["wins"] for k in ks.values())
            hit += h
            if h:
                pays.append(pay)
                gami += int(pay < bet)
                mx = max(mx, pay)
            z = d[r["date"]]; z[0] += bet; z[1] += pay
        if ok < 100:
            print(f"{rule:>22}  成立 {ok}/{n}（不足）")
            continue
        v = np.array([[x[0], x[1]] for x in d.values()], float)
        over = float(np.mean(v[:, 1] >= v[:, 0]))
        print(f"{rule:>22}{ok/n:>8.1%}{hit/ok:>8.2%}"
              f"{v[:, 1].sum()/v[:, 0].sum():>8.1%}{gami/max(hit,1):>8.1%}"
              f"{np.median(pays):>10,.0f}{mx:>11,.0f}{over:>11.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    for label, (lo, hi) in (("探索 2024-01〜2025-12", ("2024-01-01", SEARCH_END)),
                            ("確認 2026-01〜06", CONFIRM)):
        rows = build(lo, hi)
        run(rows, load_boards([r["key"] for r in rows]), label)
    print("\n⚠️ 配分に**確定オッズ**を使っているので、これは上限の数字。"
          "本番は朝の予測オッズで配分する。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
