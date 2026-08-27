#!/usr/bin/env python3
"""型ラボ既定6プランを「1日30R前後」へ絞ったとき ROI が上がるかを測る（2026-08-27）。

🔴 **絞る条件は探索窓だけで決め、確認窓で一度きり評価する。**
   同じ窓で選んで測ると必ず良く見える（CLAUDE.md「検証の作法」#5）。
🔴 **ROI 差は day クラスタの bootstrap CI で見る。** 1レース1万円なので
   1本の万車券で日次が跳ね、単純な差は簡単に符号が変わる。

    python scripts/exp_type_lab/race_filter.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

PLANS = ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit")
#: 控除率の壁（三連単 74.85% / 三連複 74.6% 前後）。ここを超えないと意味がない。
WALL = 74.85
EXPLORE = ("2026-01-01", "2026-04-30")
CONFIRM = ("2026-05-01", "2026-08-26")


def load() -> list[dict]:
    q = ("SELECT race_key, race_date, venue_name, race_type, day_index, type_label, "
         "       plan_key, axis_sum, arare, budget, hit, payout, pred_mean_payout "
         "FROM type_lab_picks "
         "WHERE mode='paper' AND settled_at IS NOT NULL "
         f"  AND plan_key IN ({','.join(['?'] * len(PLANS))})")
    cols = ("race_key", "race_date", "venue_name", "race_type", "day_index",
            "type_label", "plan_key", "axis_sum", "arare", "budget", "hit",
            "payout", "pred_mean_payout")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q, PLANS).fetchall()]
    # 1レース1プランの確認（既定6プランは型ごとに1つなので競合は起きないはず）
    seen: dict[str, int] = defaultdict(int)
    for r in rows:
        seen[r["race_key"]] += 1
    dup = sum(1 for v in seen.values() if v > 1)
    if dup:
        print(f"⚠️ 同じレースに2プラン: {dup}件（除外する）")
        rows = [r for r in rows if seen[r["race_key"]] == 1]
    for r in rows:
        r["race_date"] = str(r["race_date"])
        r["budget"] = int(r["budget"])
        r["payout"] = int(r["payout"] or 0)
    return rows


def window(rows: list[dict], w: tuple[str, str]) -> list[dict]:
    return [r for r in rows if w[0] <= r["race_date"] <= w[1]]


def roi(rows: list[dict]) -> float:
    inv = sum(r["budget"] for r in rows)
    return sum(r["payout"] for r in rows) / inv * 100 if inv else 0.0


def shown_hit(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["payout"] > r["budget"]) / len(rows) * 100


def per_day(rows: list[dict]) -> float:
    days = {r["race_date"] for r in rows}
    return len(rows) / len(days) if days else 0.0


def boot_ci(rows: list[dict], n: int = 2000, seed: int = 7) -> tuple[float, float]:
    """日をクラスタとして resample した ROI の 95% CI。"""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["race_date"]].append(r)
    days = list(by_day)
    if len(days) < 5:
        return (0.0, 0.0)
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        inv = ret = 0
        for _ in days:
            for r in by_day[rnd.choice(days)]:
                inv += r["budget"]
                ret += r["payout"]
        out.append(ret / inv * 100 if inv else 0.0)
    out.sort()
    return (round(out[int(n * 0.025)], 1), round(out[int(n * 0.975)], 1))


def table(title: str, groups: dict[str, list[dict]], min_n: int = 60) -> None:
    print(f"\n== {title}")
    print(f"{'区分':22}{'n':>6}{'件/日':>7}{'表示的中':>9}{'ROI':>8}")
    for k in sorted(groups, key=lambda x: -roi(groups[x])):
        g = groups[k]
        if len(g) < min_n:
            continue
        mark = "🟢" if roi(g) > WALL else "🔴"
        print(f"{k:22}{len(g):6d}{per_day(g):7.2f}{shown_hit(g):8.2f}%{roi(g):7.1f}%{mark}")


def main() -> None:
    rows = load()
    ex, cf = window(rows, EXPLORE), window(rows, CONFIRM)
    print(f"探索窓 {EXPLORE[0]}〜{EXPLORE[1]}: {len(ex)}R / {len({r['race_date'] for r in ex})}日")
    print(f"確認窓 {CONFIRM[0]}〜{CONFIRM[1]}: {len(cf)}R / {len({r['race_date'] for r in cf})}日")
    for label, g in (("探索窓", ex), ("確認窓", cf)):
        print(f"{label} 全体: {per_day(g):.1f}件/日 表示的中 {shown_hit(g):.2f}% "
              f"ROI {roi(g):.1f}% CI{boot_ci(g)}")

    def by(key, rowset):
        d: dict[str, list[dict]] = defaultdict(list)
        for r in rowset:
            d[str(r[key])].append(r)
        return d

    table("種別（探索窓）", by("race_type", ex))
    table("種別（確認窓）", by("race_type", cf))

    # --- 探索窓の ROI 降順で種別を積み上げ、確認窓で一度きり評価する ---
    ex_by = by("race_type", ex)
    order = [k for k in sorted(ex_by, key=lambda x: -roi(ex_by[x])) if len(ex_by[k]) >= 60]
    print("\n== 探索窓の順で種別を積み上げ → 確認窓で評価")
    print(f"{'積み上げた種別数':>10}{'確認 件/日':>12}{'確認 表示的中':>13}{'確認 ROI':>11}  追加した種別")
    keep: list[str] = []
    for k in order:
        keep.append(k)
        sub = [r for r in cf if r["race_type"] in keep]
        if not sub:
            continue
        print(f"{len(keep):>10}{per_day(sub):>12.2f}{shown_hit(sub):>12.2f}%"
              f"{roi(sub):>10.1f}%  +{k}")

    # --- 30件/日 に最も近い積み上げ点を CI つきで出す ---
    print("\n== 1日30R前後になる点（確認窓）")
    best, keep = None, []
    for k in order:
        keep.append(k)
        sub = [r for r in cf if r["race_type"] in keep]
        d = abs(per_day(sub) - 30)
        if best is None or d < best[0]:
            best = (d, list(keep), sub)
    if best:
        _, keys, sub = best
        rest = [r for r in cf if r["race_type"] not in keys]
        print(f"採用 {len(keys)}種別: {', '.join(keys)}")
        print(f"  採用群 {per_day(sub):.2f}件/日 表示的中 {shown_hit(sub):.2f}% "
              f"ROI {roi(sub):.1f}% CI{boot_ci(sub)}")
        print(f"  除外群 {per_day(rest):.2f}件/日 表示的中 {shown_hit(rest):.2f}% "
              f"ROI {roi(rest):.1f}% CI{boot_ci(rest)}")

    # --- 種別以外の候補も同じ作法で（順位が窓で反転しないかを見る） ---
    def quint(key, rowset, n=5):
        vals = [r for r in rowset if r[key] is not None]
        qs = sorted(float(r[key]) for r in vals)
        if not qs:
            return {}
        edges = [qs[int(len(qs) * i / n)] for i in range(1, n)]
        d: dict[str, list[dict]] = defaultdict(list)
        for r in vals:
            v = float(r[key])
            i = sum(1 for e in edges if v >= e)
            d[f"{key}[{i + 1}/{n}]"].append(r)
        return d

    for key in ("axis_sum", "arare", "pred_mean_payout", "day_index"):
        table(f"{key}（探索窓）", quint(key, ex))
        table(f"{key}（確認窓）", quint(key, cf))
    table("競輪場（探索窓）", by("venue_name", ex), min_n=120)
    table("競輪場（確認窓）", by("venue_name", cf), min_n=120)


if __name__ == "__main__":
    main()
