#!/usr/bin/env python3
"""合成オッズの下限で点数を切る A/B（2026-08-30・ユーザー指示）。

## 発端

2026-08-30 四日市6R（C_hit・12点）が**的中して払戻 10,080円**（投資10,000円）。
配分・予測精度は正常で（`alloc_ab.py` / 月次の実/予 は 0.87〜0.96 で一定）、
原因は **12点も買うと合成オッズが 3.18倍しかない**こと。ダッチは
「どこが当たっても合成オッズぶん」なので、**点数がそのまま払戻の天井**になる。

「的中したのに払戻が投資の1.2倍未満」は確認窓でも **6.4%＝16回に1回**あり、
これは事故ではなく設計。

## 測り方

🔴 **買い目の作り方は変えない。** 既存の `legs` を**確率の高い順**に残し、
   合成オッズ `1/Σ(1/予測オッズ)` が下限を超えるところで打ち切る。
   残した点でダッチし直して採点する（当たり目が残っていれば
   `新しい賭け金 × final_odds`、落としていれば 0）。

🔴 **ゲートは動かさない。** 点数を減らすと `pred_mean_payout` が上がり
   2万円ゲートを**通る商品が増える**。母集団を固定して点数だけ比べる
   （ゲートまで動かすと何が効いたか分からなくなる）。

⚠️ **これは的中率と払戻の交換**であって ROI の改善策ではない
   （[[keirin_trio_exclusion_model_2026_08_25]]：点数を減らすと表示的中は上がるが
   ROI は動かない）。見るのは 件数・表示的中・払戻の帯・10万円超の本数。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/points_floor.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402
from src.type_lab import BUDGET, UNIT, sell_plans_for   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}
FLOORS = [0.0, 3.0, 4.0, 5.0, 6.0, 8.0]

#: 🔴 **点数が可変なプランにだけ掛ける**（2026-08-30）。型F の6順列・型D の
#:    「最人気を1点外した4点」・型A の「1着2着固定で3着流し」は**構成が商品の定義**で、
#:    そこから確率順に削ると別物になる（`tests/test_type_lab.py` が3本落ちる）。
VARIABLE_PLANS = {"B_hit", "C_hit", "E_hit"}


def cut(legs: list[dict], floor: float) -> list[dict]:
    """確率の高い順に積み、合成オッズが `floor` を超えたら打ち切る。

    🔴 **最低2点は残す**（1点にすると商品の性格が別物になる）。
    """
    if floor <= 0:
        return legs
    ordered = sorted(legs, key=lambda l: -float(l.get("prob") or 0))
    keep: list[dict] = []
    inv = 0.0
    for l in ordered:
        o = float(l.get("pred_odds") or 0)
        if o <= 0:
            continue
        if len(keep) >= 2 and inv > 0 and 1 / (inv + 1 / o) < floor:
            break
        keep.append(l)
        inv += 1 / o
    return keep or legs[:2]


def main() -> int:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.race_date, t.plan_key, t.type_label, t.axis_sum, t.n_entries, "
            "       t.race_type, t.legs, t.hit, t.win_combo, t.final_odds, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode IN (?, ?) AND t.settled_at IS NOT NULL AND t.budget > 0",
            ("paper", "paper9"))]

    for win, (lo, hi) in WINDOWS.items():
        sub = []
        for d in rows:
            if not (lo <= str(d["race_date"]) <= hi):
                continue
            want = {p.key for p in sell_plans_for(
                str(d["type_label"]), int(d["n_entries"] or 7), d.get("race_type"))}
            if d["plan_key"] not in want:
                continue
            if not is_fill_target(d.get("race_type"), d.get("cup_grade")):
                if not _GATE.passes_axis_gate(
                        str(d["plan_key"]),
                        float(d["axis_sum"]) if d["axis_sum"] is not None else None,
                        int(d["n_entries"]) if d["n_entries"] is not None else None):
                    continue
            legs = d["legs"] if isinstance(d["legs"], list) else json.loads(d["legs"] or "[]")
            if not legs:
                continue
            if d["hit"] and not d.get("final_odds"):
                continue
            sub.append((d, legs))
        days = len({str(d["race_date"]) for d, _ in sub})
        # 🔴 **2万円ゲートへの影響も出す。** 点数が減ると平均想定払戻が上がり、
        #    いま `gate_mean_payout` で落ちている商品が通るようになる（件数が増える）。
        def _mean_pay(keep):
            inv = sum(1.0 / float(l["pred_odds"]) for l in keep
                      if float(l.get("pred_odds") or 0) > 0)
            return (BUDGET / inv) if inv > 0 else 0.0
        print(f"\n=== {win}（{len(sub):,}件 / {days}日） ===")
        print(f"  {'合成下限':<10}{'点数':>6}{'表示的中':>9}{'ROI':>8}"
              f"{'的中中央':>10}{'10万+/日':>9}{'元返し':>12}{'2万超/日':>11}")
        for f in FLOORS:
            bet = pay = hit = net = big = thin = 0
            pts = []
            pays = []
            for d, legs in sub:
                keep = cut(legs, f) if d["plan_key"] in VARIABLE_PLANS else legs
                k = len(keep)
                st = BUDGET // k // UNIT * UNIT
                pts.append(k)
                bet += st * k
                won = (d["hit"] and any(str(l["combo"]) == str(d["win_combo"])
                                        for l in keep))
                if won:
                    p = int(st * float(d["final_odds"]))
                    pay += p
                    hit += 1
                    net += int(p >= st * k)
                    big += int(p >= 100_000)
                    thin += int(p < st * k * 1.2)
                    pays.append(p)
            pays.sort()
            lab = "現行" if f == 0 else f"{f:.0f}倍"
            gate = sum(1 for d, legs in sub
                       if _mean_pay(cut(legs, f) if d["plan_key"] in VARIABLE_PLANS
                                    else legs) > 20_000)
            print(f"  {lab:<10}{sum(pts) / len(pts):>6.1f}"
                  f"{net / len(sub):>9.2%}{pay / bet:>8.1%}"
                  f"{(pays[len(pays) // 2] if pays else 0):>9,}円{big / days:>9.3f}"
                  f"{thin / hit if hit else 0:>12.1%}{gate / days:>11.1f}")
        print(f"\n  プラン別 表示的中（{win}）")
        print(f"  {'plan':<8}" + "".join(f"{('現行' if f == 0 else f'{f:.0f}倍'):>9}"
                                         for f in FLOORS))
        for plan in sorted(VARIABLE_PLANS):
            line = f"  {plan:<8}"
            for f in FLOORS:
                rs = [(d, l) for d, l in sub if d["plan_key"] == plan]
                if not rs:
                    line += f"{'—':>9}"
                    continue
                net = 0
                for d, legs in rs:
                    keep = cut(legs, f)
                    st = BUDGET // len(keep) // UNIT * UNIT
                    if d["hit"] and any(str(l["combo"]) == str(d["win_combo"])
                                        for l in keep):
                        net += int(int(st * float(d["final_odds"])) >= st * len(keep))
                line += f"{net / len(rs):>9.2%}"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
