#!/usr/bin/env python3
"""期待値・軸信頼で**プラン内**の優劣を付けられるかを測る（2026-08-27）。

本番の定義に合わせる:
  EV      = Σ(その目の確率 × 賭け金 × 予測オッズ) ÷ 総賭け金
            （`src/confident_pick.expected_value_from_lines` と同じ式）
  軸信頼   = 上位2車の**較正後**3着内率の合計 ÷ 2.00
            （`backend/src/services/keirin_p3_calibration.confidence_pct`）
            較正は race_type 群ごとの単調変換なので、**群内の順位は較正前の
            `axis_sum` と同一**。型ラボは axis_sum を行に持っているのでそれを使う。

🔴 **五分位の境界は探索窓だけで作り、確認窓へ当てはめる。**
   同じ窓で切って同じ窓で測ると、必ず綺麗な単調性が出る。
🔴 **同じ件数を無作為に落とす対照を必ず置く**（[[keirin_type_lab_race_filter_rejected]]）。
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from race_filter import CONFIRM, EXPLORE, WALL, boot_ci, per_day, roi, shown_hit  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

PLANS = ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit")
NQ = 5


def load() -> list[dict]:
    q = ("SELECT race_key, race_date, race_type, plan_key, axis_sum, budget, hit, "
         "       payout, legs, n_legs "
         "FROM type_lab_picks WHERE mode='paper' AND settled_at IS NOT NULL "
         f"  AND plan_key IN ({','.join(['?'] * len(PLANS))})")
    cols = ("race_key", "race_date", "race_type", "plan_key", "axis_sum",
            "budget", "hit", "payout", "legs", "n_legs")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q, PLANS).fetchall()]
    out = []
    for r in rows:
        legs = r["legs"]
        legs = json.loads(legs) if isinstance(legs, str) else (legs or [])
        stake = sum(float(x.get("stake") or 0) for x in legs)
        if stake <= 0:
            continue
        # 本番と同じ式。1点でも欠けたら捨てる（部分計算をしない）
        if any(not x.get("pred_odds") or x.get("prob") is None for x in legs):
            continue
        r["ev"] = sum(float(x["prob"]) * float(x["stake"]) * float(x["pred_odds"])
                      for x in legs) / stake
        r["psum"] = sum(float(x["prob"]) for x in legs)
        r["race_date"] = str(r["race_date"])
        r["budget"] = int(r["budget"])
        r["payout"] = int(r["payout"] or 0)
        r["axis_sum"] = float(r["axis_sum"]) if r["axis_sum"] is not None else None
        out.append(r)
    print(f"読み込み {len(out)}行（legs から EV を計算できたもの / 元 {len(rows)}行）")
    return out


def win(rows, w):
    return [r for r in rows if w[0] <= r["race_date"] <= w[1]]


def edges_from(rows: list[dict], key: str) -> list[float]:
    v = sorted(float(r[key]) for r in rows if r[key] is not None)
    return [v[int(len(v) * i / NQ)] for i in range(1, NQ)] if v else []


def qof(val: float, edges: list[float]) -> int:
    return sum(1 for e in edges if val >= e)


def analyse(ex, cf, key: str) -> None:
    print(f"\n{'=' * 78}\n== {key}  （境界は**探索窓のプラン内**五分位）")
    # プランごとに探索窓で境界を作る
    edges = {p: edges_from([r for r in ex if r["plan_key"] == p], key) for p in PLANS}
    def tag(rows):
        d = defaultdict(list)
        for r in rows:
            if r[key] is None or not edges[r["plan_key"]]:
                continue
            d[qof(float(r[key]), edges[r["plan_key"]])].append(r)
        return d
    te, tc = tag(ex), tag(cf)
    print(f"{'五分位':8}{'探索 n':>8}{'探索ROI':>9}{'確認 n':>8}{'確認ROI':>9}"
          f"{'確認 表示的中':>12}{'確認 95%CI':>18}")
    for i in range(NQ):
        e, c = te.get(i, []), tc.get(i, [])
        if not e or not c:
            continue
        lo, hi = boot_ci(c)
        print(f"{i + 1}/{NQ:<6}{len(e):8d}{roi(e):8.1f}%{len(c):8d}{roi(c):8.1f}%"
              f"{shown_hit(c):11.2f}%   [{lo:.1f}, {hi:.1f}]"
              f"{'🟢' if lo > WALL else ''}")
    # 上位/下位の向きが両窓で一致するか
    hi_e, lo_e = te.get(NQ - 1, []), te.get(0, [])
    hi_c, lo_c = tc.get(NQ - 1, []), tc.get(0, [])
    if hi_e and lo_e and hi_c and lo_c:
        de = roi(hi_e) - roi(lo_e)
        dc = roi(hi_c) - roi(lo_c)
        same = "🟢 同じ向き" if de * dc > 0 else "🔴 向きが逆"
        print(f"  上位−下位: 探索 {de:+.1f}pt / 確認 {dc:+.1f}pt  {same}")

    # 運用テスト: プラン内で下位1/5を落とす vs 同数を無作為に落とす
    keep = [r for i, g in tc.items() if i > 0 for r in g]
    drop_n = len(cf) - len(keep)
    print(f"  下位1/5を外す（確認窓）: {per_day(keep):5.1f}件/日 ROI {roi(keep):5.1f}% "
          f"CI{boot_ci(keep)} 表示的中 {shown_hit(keep):.2f}%")
    for seed in (11, 22, 33):
        rnd = random.Random(seed)
        ctrl = rnd.sample(cf, len(cf) - drop_n)
        print(f"    対照 無作為に同数を外す seed={seed}: ROI {roi(ctrl):5.1f}% "
              f"CI{boot_ci(ctrl)}")


def main():
    rows = load()
    ex, cf = win(rows, EXPLORE), win(rows, CONFIRM)
    print(f"探索窓 {len(ex)}R / 確認窓 {len(cf)}R")
    print(f"確認窓 全体 ROI {roi(cf):.1f}% CI{boot_ci(cf)}  {per_day(cf):.1f}件/日")
    for key in ("ev", "psum", "axis_sum"):
        analyse(ex, cf, key)

    print(f"\n{'=' * 78}\n== プラン別に上位1/5 と 下位1/5（EV・確認窓）")
    edges = {p: edges_from([r for r in ex if r["plan_key"] == p], "ev") for p in PLANS}
    print(f"{'plan':8}{'下位ROI':>9}{'上位ROI':>9}{'差':>8}  (n 下/上)")
    for p in PLANS:
        g = [r for r in cf if r["plan_key"] == p and edges[p]]
        lo = [r for r in g if qof(r["ev"], edges[p]) == 0]
        hi = [r for r in g if qof(r["ev"], edges[p]) == NQ - 1]
        if not lo or not hi:
            continue
        print(f"{p:8}{roi(lo):8.1f}%{roi(hi):8.1f}%{roi(hi) - roi(lo):+7.1f}pt"
              f"  ({len(lo)}/{len(hi)})")


if __name__ == "__main__":
    main()
