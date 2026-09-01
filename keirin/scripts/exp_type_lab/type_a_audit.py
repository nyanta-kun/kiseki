#!/usr/bin/env python3
"""型A の結論の再検査（2026-08-31）。

## なぜ

HANDOFF_2026-08-31 の §2「型A は買い方をどう変えても割に合わない」と §3「A-split」は
**均等配分・CIなし・無作為対照なし**で測られている。本番の A_hit は
`alloc="conf"`（信頼度傾斜）なので、配分だけで的中中央もガミ率も変わる。

ここでは `type_lab_picks.legs` の**実際に組んだ賭け金**をそのまま使い、
本番と同じ母集団（軸信頼ゲート通過＋看板）で測り直す。

🔴 **`分岐割れ` は「的中のうち払戻が (投資/的中率) を割る割合」**。
   平均払戻 = ROI × その閾値 なので、ROI<1 のとき閾値は必ず平均払戻より上。
   右に裾を引く分布では P(払戻<平均) が既に 65〜75% あるので、
   **分岐割れが 70〜85% に張り付くのは分布の歪度から来る恒等的な性質**。
   腕の良し悪しを分ける指標にならないことを、`平均割れ` を併記して示す。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_audit.py
"""
from __future__ import annotations

import importlib.util
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}


def load(plans=("A_hit", "A_pay")):
    ph = ",".join("?" * len(plans))
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT race_key, race_date, race_type, axis_sum, arare, gap, "
            f"       axis1, axis2, plan_key, legs, budget, hit, payout, "
            f"       p3_order, win_tf_odds, win_combo, day_index, venue_name "
            f"FROM type_lab_picks WHERE mode = 'paper' AND settled_at IS NOT NULL "
            f"  AND n_entries = 7 AND plan_key IN ({ph}) AND win_tf_odds IS NOT NULL",
            tuple(plans))]
    out = []
    for d in rows:
        # 本番と同じ売る／売らないの判定（軸信頼ゲート／看板は素通し）
        if not (is_fill_target(d.get("race_type"), None)
                or _GATE.passes_axis_gate(
                    d["plan_key"],
                    float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)):
            continue
        legs = d["legs"]
        if isinstance(legs, str):
            import json
            legs = json.loads(legs)
        if not legs:
            continue
        d["legs"] = legs
        d["inv"] = sum(int(x["stake"]) for x in legs)
        d["pay"] = int(d["payout"] or 0)
        d["date"] = str(d["race_date"])
        d["order"] = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
        out.append(d)
    return out


def boot_roi(rs, n=2000, seed=0):
    rnd = random.Random(seed)
    m = len(rs)
    inv = [r["inv"] for r in rs]
    pay = [r["pay"] for r in rs]
    vals = []
    for _ in range(n):
        ii = pp = 0.0
        for _ in range(m):
            j = rnd.randrange(m)
            ii += inv[j]; pp += pay[j]
        vals.append(pp / ii * 100 if ii else 0.0)
    vals.sort()
    return vals[int(n * .025)], vals[int(n * .975)]


def boot_shown(rs, n=2000, seed=0):
    rnd = random.Random(seed)
    m = len(rs)
    ok = [1 if r["pay"] > r["inv"] else 0 for r in rs]
    vals = sorted(sum(ok[rnd.randrange(m)] for _ in range(m)) / m * 100 for _ in range(n))
    return vals[int(n * .025)], vals[int(n * .975)]


def stat(rs, ci=True):
    if not rs:
        return None
    n = len(rs)
    nd = len({r["date"] for r in rs})
    inv = sum(r["inv"] for r in rs)
    pay = sum(r["pay"] for r in rs)
    hits = [r for r in rs if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] > r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    hr = len(hits) / n
    need = (inv / n) / hr if hr else 0.0
    meanpay = (pay / len(hits)) if hits else 0.0
    s = dict(
        n=n, nd=nd, perday=n / nd, k=sum(len(r["legs"]) for r in rs) / n,
        hit=hr * 100, shown=len(shown) / n * 100,
        gami=(len(hits) - len(shown)) / len(hits) * 100 if hits else 0.0,
        med=median(pays) if pays else 0, roi=pay / inv * 100 if inv else 0.0,
        need=need, meanpay=meanpay,
        below_need=sum(1 for x in pays if x < need) / len(pays) * 100 if pays else 0.0,
        below_mean=sum(1 for x in pays if x < meanpay) / len(pays) * 100 if pays else 0.0,
        big=sum(1 for x in pays if x >= 100_000) / nd,
    )
    if ci:
        s["roi_lo"], s["roi_hi"] = boot_roi(rs)
        s["sh_lo"], s["sh_hi"] = boot_shown(rs)
    return s


HEAD = ("    {:<24}{:>6}{:>5}{:>8}{:>7}{:>9}{:>18}{:>10}{:>18}{:>9}{:>9}"
        .format("腕", "件/日", "点", "素の的中", "ガミ%", "表示的中",
                "表示的中CI", "払戻中央", "ROI(CI)", "分岐割れ", "平均割れ"))


def line(name, s):
    if not s:
        return f"    {name:<24}  (該当なし)"
    ci1 = f"[{s['sh_lo']:.1f},{s['sh_hi']:.1f}]" if "sh_lo" in s else ""
    ci2 = (f"{s['roi']:.1f}[{s['roi_lo']:.0f},{s['roi_hi']:.0f}]"
           if "roi_lo" in s else f"{s['roi']:.1f}")
    return (f"    {name:<24}{s['perday']:>6.2f}{s['k']:>5.1f}{s['hit']:>8.2f}"
            f"{s['gami']:>7.1f}{s['shown']:>9.2f}{ci1:>18}{s['med']:>10,.0f}"
            f"{ci2:>18}{s['below_need']:>9.0f}{s['below_mean']:>9.0f}")


def main() -> int:
    data = load()
    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        print(f"\n=== {win} ===")
        print(HEAD)
        for pk in ("A_hit", "A_pay"):
            sub = [d for d in rs if d["plan_key"] == pk]
            print(line(pk + "（本番配分）", stat(sub)))

        # ── 波乱の正体: 確定オッズ帯ごとに「軸が残ったか」 ──
        a = [d for d in rs if d["plan_key"] == "A_hit"]
        print(f"\n  ▼ 型A の決着を確定三連単オッズ帯で割る（n={len(a):,}）")
        print(f"    {'帯':<12}{'R数':>7}{'割合':>8}{'軸1が1着':>10}{'軸1が3着内':>12}"
              f"{'軸2が3着内':>12}{'◎○そろい':>11}{'3着が p3 4位以下':>17}")
        bands = [("<10倍", 0, 10), ("10-30倍", 10, 30), ("30-100倍", 30, 100),
                 ("100倍+", 100, 10 ** 9)]
        for lab, b0, b1 in bands:
            sub = [d for d in a if b0 <= float(d["win_tf_odds"]) < b1]
            if not sub:
                continue
            def frac(fn):
                return sum(1 for d in sub if fn(d)) / len(sub)
            def fin(d):
                return [int(x) for x in str(d["win_combo"]).split("-")]
            a1 = lambda d: d["order"][0]          # noqa: E731  軸1 = p3 1位
            a2 = lambda d: d["order"][1]          # noqa: E731
            print(f"    {lab:<12}{len(sub):>7,}{len(sub)/len(a):>8.1%}"
                  f"{frac(lambda d: fin(d)[0] == a1(d)):>10.1%}"
                  f"{frac(lambda d: a1(d) in fin(d)):>12.1%}"
                  f"{frac(lambda d: a2(d) in fin(d)):>12.1%}"
                  f"{frac(lambda d: a1(d) in fin(d) and a2(d) in fin(d)):>11.1%}"
                  f"{frac(lambda d: any(d['order'].index(c) >= 3 for c in fin(d))):>17.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
