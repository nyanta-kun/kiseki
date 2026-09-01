#!/usr/bin/env python3
"""配分（賭け金の置き方）の A/B（2026-08-30・ユーザー指示）。

## 発端

2026-08-30 四日市6R（C_hit）で **的中しながら払戻 10,080円**（投資10,000円）。
当たった目 7-2-3 の**予測 23.5倍 → 確定 7.2倍（実/予 0.31）**で、
配分が `∝1/予測オッズ` なので**最も外した目に最も厚く賭けていた**（1,400円）。

同レースの合成オッズは 予測3.18倍 → 確定2.54倍と**当たっている**。
つまり壊れているのは合成ではなく**点ごとの予測**で、誤差は平均化されないため
配分にだけ効く。

## 測り方

🔴 **買い目は一切変えない。** 同じレース・同じ点を、配分だけ差し替えて採点し直す。
   的中／不的中は動かないので、動くのは払戻（＝ROI・表示的中・ガミ）だけ。
   これで「配分が原因か」を買い目の良し悪しから切り離せる。

🔴 **`final_odds`（的中した目の確定オッズ）を使う。** 外れた行は払戻0で配分に
   よらないので、必要なのは的中行だけ。`wt_odds` を引き直さずに済む。
   ⚠️ 同着で当たり目が複数ある行は `payout != final_odds × stake` になる
      （実測 0.2% 程度）。その行は母集団から外す。

## 配分の候補

    現行      `legs[].stake`（プランごとに ダッチ ∝1/予測オッズ か 信頼度傾斜）
    均等      予算 ÷ 点数
    確率比例  ∝ `legs[].prob`（PL の的中確率。**予測オッズを使わない**）
    純ダッチ  ∝ 1/予測オッズ（傾斜プランでも強制的にダッチへ）

⚠️ **ゲートは動かさない。** 配分を変えると `pred_mean_payout` / `pred_min_payout` も
   変わり、2万円ゲート・1点2.0倍ゲートの通過が変わる。ここでは母集団を固定して
   配分だけを比べる（ゲートまで動かすと何が効いたか分からなくなる）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/alloc_ab.py
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


def _floor(x: float) -> int:
    return int(x) // UNIT * UNIT


def allocs(legs: list[dict]) -> dict[str, list[int]]:
    """配分の候補。**合計が予算を超えないよう 100円単位で切り捨てる**（本番と同じ）。"""
    k = len(legs)
    out = {"現行": [int(l["stake"]) for l in legs]}
    out["均等"] = [_floor(BUDGET / k)] * k
    for name, w in (("確率比例", [float(l.get("prob") or 0) for l in legs]),
                    ("純ダッチ", [1.0 / float(l["pred_odds"]) if float(l.get("pred_odds") or 0) > 0
                                else 0.0 for l in legs])):
        s = sum(w)
        out[name] = ([_floor(BUDGET * x / s) for x in w] if s > 0
                     else [_floor(BUDGET / k)] * k)
    return out


def main() -> int:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.race_date, t.mode, t.plan_key, t.type_label, t.axis_sum, "
            "       t.n_entries, t.race_type, t.legs, t.hit, t.payout, t.budget, "
            "       t.win_combo, t.final_odds, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode IN (?, ?) AND t.settled_at IS NOT NULL AND t.budget > 0",
            ("paper", "paper9"))]

    names = ["現行", "均等", "確率比例", "純ダッチ"]
    for win, (lo, hi) in WINDOWS.items():
        agg = {n: {"bet": 0, "pay": 0, "hit": 0, "net": 0, "pays": []} for n in names}
        n_rows = n_skip = 0
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
            idx = None
            if d["hit"]:
                # 🔴 同着で当たり目が複数ある行は `payout` が合算されていて
                #    `final_odds × stake` と一致しない。配分の比較に混ぜられない。
                if not d.get("final_odds"):
                    n_skip += 1
                    continue
                for i, l in enumerate(legs):
                    if str(l["combo"]) == str(d["win_combo"]):
                        idx = i
                        break
                if idx is None:
                    n_skip += 1
                    continue
                if abs(int(legs[idx]["stake"]) * float(d["final_odds"])
                       - int(d["payout"])) > 2:
                    n_skip += 1
                    continue
            n_rows += 1
            for name, st in allocs(legs).items():
                a = agg[name]
                bet = sum(st)
                pay = int(st[idx] * float(d["final_odds"])) if idx is not None else 0
                a["bet"] += bet
                a["pay"] += pay
                a["hit"] += int(idx is not None)
                a["net"] += int(pay >= bet)
                if idx is not None:
                    a["pays"].append(pay)

        print(f"\n=== {win}（{n_rows:,}件・同着等で除外 {n_skip}件） ===")
        print(f"  {'配分':<10}{'表示的中':>9}{'素の的中':>9}{'ROI':>8}"
              f"{'的中中央':>10}{'ガミ率':>8}")
        for n in names:
            a = agg[n]
            if not a["bet"]:
                continue
            p = sorted(a["pays"])
            gami = (a["hit"] - a["net"]) / a["hit"] if a["hit"] else 0
            print(f"  {n:<10}{a['net'] / n_rows:>9.2%}{a['hit'] / n_rows:>9.2%}"
                  f"{a['pay'] / a['bet']:>8.1%}{(p[len(p) // 2] if p else 0):>9,}円"
                  f"{gami:>8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
