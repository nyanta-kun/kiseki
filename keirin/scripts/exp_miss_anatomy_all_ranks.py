#!/usr/bin/env python3
"""全ランクの「外れ方」を分類し、条件別に走査する（2026-08-19）。

## 何を測るか

`picks_history`（本番記録）の買い目を分解し、外れたレースを**どこで落ちたか**で分ける:

    axis1_out   軸1が3着内に来なかった
    axis2_out   軸2が3着内に来なかった（軸1は来た）
    both_out    軸2車とも来なかった
    leg_out     軸2車は来たが3列目が買っていない車だった（＝相手の絞り漏れ）

三連単ランク（7T1 等）は着順つきなので `order_out`（顔ぶれは合ったが着順違い）も出す。

## 🔴 既に潰れている道（再走査しない）

- 軸の選び直し … 天井 +2.62pt（[[keirin_axis_miss_anatomy_2026_08_17]]）
- 選別の学習スコア化 … 件数を揃えると差が消える（[[keirin_race_selection_meta_2026_08_18]]）
- 細切れ番組・風速×脚質・場のgood/bad・前日の外れ条件 … すべて不採用

## 多重比較の扱い

条件走査は**区分の総数を必ず表示**し、各区分の差は帰無分布（ランダムに同数を
抜いたときの分布）と比べる。上位だけを拾って報告しない。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_miss_anatomy_all_ranks.py \
        --from 2025-01-01 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402


def parse_combo(s: str) -> tuple[str, list[int], list[int]] | None:
    """`7=3-1,2,4,5,6` / `三単:2-4-6` / `1=2=3` を (種別, 軸, 相手) へ。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.startswith("三単"):
        nums = [int(x) for x in re.findall(r"\d+", s)]
        return ("trifecta", nums, []) if len(nums) >= 3 else None
    s = s.split(" ")[0]                       # `(axis_sum=1.4)` 等を落とす
    if "-" in s:
        head, tail = s.split("-", 1)
        axes = [int(x) for x in re.split(r"[=]", head) if x.strip().isdigit()]
        legs = [int(x) for x in re.split(r"[,]", tail) if x.strip().isdigit()]
        if axes and legs:
            return "trio_axis", axes, legs
    nums = [int(x) for x in re.split(r"[-=]", s) if x.strip().isdigit()]
    return ("trio_fixed", nums, []) if len(nums) == 3 else None


def load(d1: str, d2: str):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT split_part(race_key,'#',1) rk, race_date, rank, pred_combo, "
            "       hit, payout, bet_amount "
            "FROM picks_history WHERE race_date BETWEEN ? AND ? AND bet_amount > 0",
            (d1, d2))
        picks = [dict(rk=a, date=b, rank=c, combo=d, hit=e, pay=f, bet=g)
                 for a, b, c, d, e, f, g in cur.fetchall()]
        keys = sorted({p["rk"] for p in picks})
        fin: dict[str, dict[int, int]] = defaultdict(dict)
        marks: dict[str, dict[int, int]] = defaultdict(dict)
        lines: dict[str, dict[int, tuple]] = defaultdict(dict)
        p3: dict[str, dict[int, float]] = defaultdict(dict)
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order, prediction_mark, "
                 "       line_group, line_size, is_line_leader, pred_top3_pct "
                 "FROM wt_entries WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for rk, fn, fo, mk, lg, ls, ld, pp in conn.execute(q, ch).fetchall():
                if fo:
                    fin[rk][int(fn)] = int(fo)
                if mk:
                    marks[rk][int(mk)] = int(fn)
                lines[rk][int(fn)] = (lg, ls, ld)
                if pp is not None:
                    p3[rk][int(fn)] = float(pp) / 100.0
        meta: dict[str, tuple] = {}
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, race_type, cup_grade, n_entries, start_at "
                 "FROM wt_races WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for rk, rt, g, ne, sa in conn.execute(q, ch).fetchall():
                meta[rk] = (rt, g, ne, sa)
    return picks, fin, marks, lines, p3, meta


def classify(p, fin, ) -> str | None:
    got = parse_combo(p["combo"])
    if not got:
        return None
    kind, axes, legs = got
    f = fin.get(p["rk"]) or {}
    if len(f) < 3:
        return None
    top3 = {n for n, o in f.items() if o <= 3}
    if len(top3) != 3:
        return None
    if kind == "trifecta":
        want = tuple(axes[:3])
        actual = tuple(sorted(top3, key=lambda n: f[n]))
        if want == actual:
            return "hit"
        if set(want) == top3:
            return "order_out"          # 顔ぶれは合ったが着順違い
        axes = list(want[:2])           # 以降は軸2車として扱う
        legs = [want[2]]
    a_in = [a for a in axes[:2] if a in top3]
    if len(a_in) < len(axes[:2]):
        if not a_in:
            return "both_out"
        return "axis2_out" if axes[0] in top3 else "axis1_out"
    if kind == "trio_axis":
        third = next(iter(top3 - set(axes[:2])), None)
        return "hit" if third in legs else "leg_out"
    return "hit" if set(axes[:3]) == top3 else "leg_out"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    args = ap.parse_args()

    picks, fin, marks, lines, p3, meta = load(args.d1, args.d2)
    for p in picks:
        p["cls"] = classify(p, fin)
    ok = [p for p in picks if p["cls"]]
    print(f"\n分類できた推奨 {len(ok):,} / {len(picks):,}件 [{args.d1}〜{args.d2}]")

    CLS = ("hit", "leg_out", "order_out", "axis2_out", "axis1_out", "both_out")
    by = defaultdict(list)
    for p in ok:
        by[p["rank"]].append(p)
    print(f"\n===== ランク別の外れ方（行内%）=====")
    print(f"  {'':10}{'R':>7}" + "".join(f"{c:>11}" for c in CLS))
    for rank in sorted(by, key=lambda r: -len(by[r])):
        v = by[rank]
        if len(v) < 100:
            continue
        cnt = defaultdict(int)
        for p in v:
            cnt[p["cls"]] += 1
        print(f"  {rank.replace('RANK_',''):10}{len(v):>7}"
              + "".join(f"{100*cnt[c]/len(v):>11.1f}" for c in CLS))

    # 「軸2だけ来ず」が主因かの確認（既知の結論の再現）
    tot = defaultdict(int)
    for p in ok:
        tot[p["cls"]] += 1
    n = len(ok)
    print(f"\n  全体: " + " / ".join(f"{c} {100*tot[c]/n:.1f}%" for c in CLS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
