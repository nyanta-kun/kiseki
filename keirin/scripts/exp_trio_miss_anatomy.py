#!/usr/bin/env python3
"""外れているレースの解剖 — 二軸が揃わなかった46%に傾向はあるか。

## ユーザー指示（2026-08-23）

> まだ的中できていないレースについても傾向がないか確認して

二軸的中は 53.8%（ペアモデルで 54.8%）。残る **46%** に構造があるなら、
そこが上限を上げる余地になる。無ければ「上限は情報の限界」という結論になる。

## 見るもの

1. **外れ方の内訳** — 軸1だけ / 軸2だけ / 両方
2. **誰が入ってきたか** — 我々が持っていなかった3着内車のモデル順位
3. **欠車・失格（DNF）の寄与** — 走らずに消えたのか、走って負けたのか
   🔴 [[keirin_dnf_handling_rejected_2026_08_04]]: DNF は稀・無偏り・予測不能で
      母集団をいじっても動かない。**それでも量は把握しておく**
4. **較正** — ペアモデルの予測確率と実測が合っているか。
   合っていれば「外れは低確率の裾」であって傾向ではない。
   ずれている帯があれば、そこが残っている余地
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.result_top3 import winning_trifectas  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    args = ap.parse_args()

    rows = []
    with open(args.cache) as f:
        for x in f:
            r = json.loads(x)
            if r.get("win"):
                rows.append(r)
    keys = [r["race_key"] for r in rows]
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    fin: dict[str, dict[int, int]] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, frame_no, finish_order from keirin.wt_entries "
                    "where race_key = any(%s)", (keys[i:i + 2000],))
        for rk, fn, fo in cur.fetchall():
            fin[rk][int(fn)] = int(fo) if fo is not None else -1

    n = 0
    kind = Counter()
    intruder = Counter()
    dnf_axis = Counter()
    axis_fin = defaultdict(Counter)
    for r in rows:
        f = fin.get(r["race_key"])
        if not f or len(f) < 7:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        order = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        if len(order) < 7:
            continue
        rank = {c: i + 1 for i, c in enumerate(order)}
        a1, a2 = order[0], order[1]
        top3 = {c for w in winning_trifectas(
            [(f[c], c) for c in f if 1 <= f[c] <= 3]) for c in w} if False else None
        # 3着内の車番（同着込み）
        top3 = {c for c, o in f.items() if 1 <= o <= 3}
        if len(top3) < 3:
            continue
        n += 1
        in1, in2 = a1 in top3, a2 in top3
        kind[("両方3着内" if in1 and in2 else
              "軸2だけ外れ" if in1 else
              "軸1だけ外れ" if in2 else "両方外れ")] += 1
        for c in (a1, a2):
            o = f.get(c, -1)
            axis_fin["軸1" if c == a1 else "軸2"][o if o > 0 else 0] += 1
            if o <= 0:
                dnf_axis["軸1" if c == a1 else "軸2"] += 1
        if not (in1 and in2):
            for c in top3:
                if c not in (a1, a2):
                    intruder[rank[c]] += 1

    print(f"対象 {n:,}R\n")
    print("=== ① 外れ方の内訳 ===")
    for k, v in kind.most_common():
        print(f"  {k:12}{v:>8,}{v/n:>8.2%}")

    print("\n=== ② 割って入った車のモデル順位（二軸が揃わなかったレース）===")
    tot = sum(intruder.values())
    for rk in sorted(intruder):
        print(f"  順位{rk}: {intruder[rk]:>7,} ({intruder[rk]/tot:>6.2%})")

    print("\n=== ③ 軸の着順分布（0 = 欠車・失格）===")
    print(f"{'':6}" + "".join(f"{f'{i}着':>8}" for i in range(1, 8)) + f"{'DNF':>8}")
    for a in ("軸1", "軸2"):
        c = axis_fin[a]
        s = sum(c.values())
        print(f"{a:6}" + "".join(f"{c.get(i,0)/s:>8.1%}" for i in range(1, 8))
              + f"{c.get(0,0)/s:>8.1%}")
    print(f"\n  DNF は軸1 {dnf_axis['軸1']/n:.2%} / 軸2 {dnf_axis['軸2']/n:.2%}"
          f"  ← 走らずに消えた分。ここは予測不能")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
