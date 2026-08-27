#!/usr/bin/env python3
"""軸信頼（= 上位2車の3着内率の合計）の下限をプラン内に置く案の追試（2026-08-27）。

🔴 CI を並べて「重なっていない」で判断しない。**同じ日でペアにした差**を bootstrap する。
🔴 本番 7C は `RANK_7C_P3_SUM_MIN = 1.44`、9C は 1.30 で**既に同じ量をゲートしている**。
   型ラボはこの量を型分けにしか使っておらず、下限を引いていない。
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ev_axis_rank import NQ, PLANS, edges_from, load, qof, win  # noqa: E402
from race_filter import CONFIRM, EXPLORE, WALL, boot_ci, per_day, roi, shown_hit  # noqa: E402

#: 本番 7C のゲート（`strategy_wt.RANK_7C_P3_SUM_MIN`）。較正後の値なので厳密には
#: 同じ土俵ではないが、桁感の当たりを付けるために並べる。
PROD_7C = 1.44


def paired_diff(a: list[dict], b: list[dict], n: int = 4000, seed: int = 5):
    """同じ日でペアにした ROI 差 (a − b) の点推定と 95%CI。"""
    days = sorted({r["race_date"] for r in a} | {r["race_date"] for r in b})
    ga, gb = defaultdict(list), defaultdict(list)
    for r in a:
        ga[r["race_date"]].append(r)
    for r in b:
        gb[r["race_date"]].append(r)

    def _roi(rows):
        inv = sum(r["budget"] for r in rows)
        return sum(r["payout"] for r in rows) / inv * 100 if inv else None

    point = (_roi(a) or 0) - (_roi(b) or 0)
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        ia = ib = ra = rb = 0
        for _ in days:
            d = rnd.choice(days)
            for r in ga.get(d, ()):
                ia += r["budget"]; ra += r["payout"]
            for r in gb.get(d, ()):
                ib += r["budget"]; rb += r["payout"]
        if ia and ib:
            out.append(ra / ia * 100 - rb / ib * 100)
    out.sort()
    return point, out[int(len(out) * 0.025)], out[int(len(out) * 0.975)]


def main():
    rows = load()
    ex, cf = win(rows, EXPLORE), win(rows, CONFIRM)
    edges = {p: edges_from([r for r in ex if r["plan_key"] == p], "axis_sum")
             for p in PLANS}
    print("-- 探索窓で決めたプラン内の五分位境界（axis_sum）")
    for p in PLANS:
        print(f"   {p:7} {[round(e, 3) for e in edges[p]]}")

    def q(r):
        return qof(float(r["axis_sum"]), edges[r["plan_key"]])

    for cut in (1, 2):
        keep = [r for r in cf if r["axis_sum"] is not None and q(r) >= cut]
        drop = [r for r in cf if r["axis_sum"] is not None and q(r) < cut]
        pt, lo, hi = paired_diff(keep, cf)
        print(f"\n== 下位 {cut}/5 を外す（確認窓）")
        print(f"   残す {per_day(keep):5.1f}件/日 ROI {roi(keep):5.1f}% "
              f"表示的中 {shown_hit(keep):.2f}%")
        print(f"   外す {per_day(drop):5.1f}件/日 ROI {roi(drop):5.1f}% "
              f"表示的中 {shown_hit(drop):.2f}%")
        print(f"   🔬 全体との差 {pt:+.1f}pt  95%CI[{lo:+.1f}, {hi:+.1f}]"
              f"  {'🟢 0を跨がない' if lo > 0 else '🔴 0を跨ぐ'}")
        # 同数を無作為に落とす対照を 20 seed
        ctrl = []
        for s in range(20):
            rnd = random.Random(100 + s)
            ctrl.append(roi(rnd.sample(cf, len(keep))))
        ctrl.sort()
        print(f"   対照（無作為に同数）20seed: 中央 {ctrl[10]:.1f}% "
              f"範囲 {ctrl[0]:.1f}〜{ctrl[-1]:.1f}%  "
              f"→ 案は上位 {sum(1 for c in ctrl if c < roi(keep))}/20")

    print("\n== 探索窓でも同じ向きか（下位1/5 を外す）")
    keep_e = [r for r in ex if r["axis_sum"] is not None and q(r) >= 1]
    pt, lo, hi = paired_diff(keep_e, ex)
    print(f"   探索窓 残す ROI {roi(keep_e):.1f}%  全体との差 {pt:+.1f}pt "
          f"95%CI[{lo:+.1f}, {hi:+.1f}] {'🟢' if lo > 0 else '🔴'}")

    print("\n== プラン別の向き（下位1/5 vs 残り・確認窓 / 探索窓）")
    print(f"{'plan':8}{'確認 下位':>10}{'確認 残り':>10}{'差':>8}"
          f"{'探索 下位':>10}{'探索 残り':>10}{'差':>8}")
    for p in PLANS:
        gc = [r for r in cf if r["plan_key"] == p and r["axis_sum"] is not None]
        ge = [r for r in ex if r["plan_key"] == p and r["axis_sum"] is not None]
        if not edges[p] or not gc or not ge:
            continue
        lc = [r for r in gc if q(r) == 0]; rc = [r for r in gc if q(r) > 0]
        le = [r for r in ge if q(r) == 0]; re_ = [r for r in ge if q(r) > 0]
        if not (lc and rc and le and re_):
            continue
        dc, de = roi(rc) - roi(lc), roi(re_) - roi(le)
        mark = "🟢" if dc > 0 and de > 0 else ("🔴" if dc * de < 0 else "")
        print(f"{p:8}{roi(lc):9.1f}%{roi(rc):9.1f}%{dc:+7.1f}pt"
              f"{roi(le):9.1f}%{roi(re_):9.1f}%{de:+7.1f}pt {mark}")

    print(f"\n== 絶対閾値で切る（本番 7C ゲート {PROD_7C} と同じ量・較正前）")
    for th in (1.20, 1.30, 1.40, PROD_7C, 1.50):
        keep = [r for r in cf if r["axis_sum"] is not None and float(r["axis_sum"]) >= th]
        if not keep:
            continue
        pt, lo, hi = paired_diff(keep, cf)
        print(f"   >= {th:.2f}: {per_day(keep):5.1f}件/日 ROI {roi(keep):5.1f}% "
              f"表示的中 {shown_hit(keep):5.2f}%  差 {pt:+.1f}pt "
              f"CI[{lo:+.1f}, {hi:+.1f}]{'🟢' if lo > 0 else ''}")


if __name__ == "__main__":
    main()
