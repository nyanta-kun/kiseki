#!/usr/bin/env python3
"""9車の型Fを種別・指数差で割り、拾えるセルがあるか探す（2026-08-28）。

🔴 **9車は看板（決勝など大きいレース）が対象になり、決勝は必ず出す方針**
   （ルート CLAUDE.md「看板レース」）。したがってここでの問いは
   「売れるか」だけでなく **「決勝でどの買い方が最もマシか」** でもある。

⚠️ 既知の作法（`docs/trifecta_playbook.md` §3.2・§4）:
   - 種別は**帯をまたぐと順位が変わる**。両窓で一貫するのは限られる
   - **記述的傾向を買い目の制約に変換すると必ず負ける**
   → ここでは「制約にする」前に、まず**両窓で同じ向きか**だけを見る。
"""
from __future__ import annotations

import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

WALL = 74.85
EX = ("2025-01-01", "2025-12-31")
CF = ("2026-01-01", "2026-08-26")


def load(mode: str, d1: str, d2: str, plans=("F_hit",), label=None):
    ph = ",".join("?" * len(plans))
    q = ("SELECT race_key, race_date, race_type, type_label, axis_sum, arare, gap, "
         "       plan_key, budget, hit, payout, n_legs "
         f"FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
         f"  AND plan_key IN ({ph}) AND race_date BETWEEN ? AND ?")
    cols = ("race_key", "race_date", "race_type", "type_label", "axis_sum",
            "arare", "gap", "plan_key", "budget", "hit", "payout", "n_legs")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r)))
                for r in c.execute(q, (mode, *plans, d1, d2)).fetchall()]
    if label:
        rows = [r for r in rows if r["type_label"] == label]
    for r in rows:
        r["race_date"] = str(r["race_date"])
        r["budget"] = int(r["budget"])
        r["payout"] = int(r["payout"] or 0)
    return rows


def stat(v):
    if not v:
        return None
    inv = sum(r["budget"] for r in v)
    ret = sum(r["payout"] for r in v)
    shown = sum(1 for r in v if r["payout"] > r["budget"])
    med = stx.median([r["payout"] for r in v if r["payout"] > r["budget"]] or [0])
    days = len({r["race_date"] for r in v})
    return dict(n=len(v), per_day=len(v) / max(days, 1),
                shown=shown / len(v) * 100, med=med,
                roi=ret / inv * 100 if inv else 0)


def compare(ex, cf, keyfn, title, min_n=25):
    ge, gc = defaultdict(list), defaultdict(list)
    for r in ex:
        ge[keyfn(r)].append(r)
    for r in cf:
        gc[keyfn(r)].append(r)
    print(f"\n== {title}")
    print(f"{'区分':22}{'探索 n':>7}{'探索ROI':>9}{'確認 n':>7}{'確認ROI':>9}"
          f"{'確認 表示的中':>13}{'確認 払戻中央':>13}  判定")
    keys = sorted(set(ge) | set(gc),
                  key=lambda k: -(stat(gc.get(k, [])) or {"roi": 0})["roi"])
    for k in keys:
        e, c = stat(ge.get(k, [])), stat(gc.get(k, []))
        if not e or not c or e["n"] < min_n or c["n"] < min_n:
            continue
        both = "🟢両窓で壁超え" if e["roi"] > WALL and c["roi"] > WALL else (
            "🔴片窓だけ" if (e["roi"] > WALL) != (c["roi"] > WALL) else "")
        print(f"{str(k):22}{e['n']:7d}{e['roi']:8.1f}%{c['n']:7d}{c['roi']:8.1f}%"
              f"{c['shown']:12.2f}%{c['med']:13,.0f}  {both}")


def quint(rows, key, n=4):
    v = sorted(float(r[key]) for r in rows if r[key] is not None)
    if not v:
        return lambda r: "—"
    edges = [v[int(len(v) * i / n)] for i in range(1, n)]

    def f(r):
        if r[key] is None:
            return f"{key}=NA"
        x = float(r[key])
        i = sum(1 for e in edges if x >= e)
        return f"{key} {i + 1}/{n}"
    return f


def main() -> None:
    ex = load("paper9", *EX, label="F")
    cf = load("paper9", *CF, label="F")
    print(f"9車 型F  探索 {len(ex)}件 / 確認 {len(cf)}件")

    compare(ex, cf, lambda r: r["race_type"], "種別（9車 型F）", min_n=25)
    compare(ex, cf, quint(ex, "axis_sum"), "軸の堅さ axis_sum（型F内の四分位）")
    compare(ex, cf, quint(ex, "gap"), "相手の開き gap（型F内の四分位）")
    compare(ex, cf, lambda r: f"荒れ度 s={min(int(r['arare'] or 0), 5)}", "荒れ度")

    # 決勝だけを取り出して、買い方を並べる
    print(f"\n{'=' * 92}\n== 9車の決勝系（必ず出す対象）で、どの買い方が最もマシか")
    for name, plans in (("F_hit（現行・12点）", ("F_hit",)),
                        ("F_pay（一撃・4点）", ("F_pay",))):
        for lab, w in (("探索", EX), ("確認", CF)):
            rows = [r for r in load("paper9", *w, plans)
                    if r["type_label"] == "F"
                    and "決勝" in str(r["race_type"] or "")]
            s = stat(rows)
            if not s:
                continue
            print(f"  {name:20} {lab}  n={s['n']:4d}  表示的中 {s['shown']:5.2f}%  "
                  f"払戻中央 {s['med']:8,.0f}  ROI {s['roi']:6.1f}%"
                  + ("🟢" if s["roi"] > WALL else ""))
    # 型を問わず 9車の決勝系すべて
    print("\n  -- 型を問わず 9車の決勝系すべて（F_hit）")
    for lab, w in (("探索", EX), ("確認", CF)):
        rows = [r for r in load("paper9", *w, ("F_hit",))
                if "決勝" in str(r["race_type"] or "")]
        s = stat(rows)
        if s:
            print(f"     {lab}  n={s['n']:4d}  表示的中 {s['shown']:5.2f}%  "
                  f"払戻中央 {s['med']:8,.0f}  ROI {s['roi']:6.1f}%"
                  + ("🟢" if s["roi"] > WALL else ""))


if __name__ == "__main__":
    main()
