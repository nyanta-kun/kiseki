#!/usr/bin/env python3
"""H20 継続監視: 種別「特一般」「特予選」の実売の回収率と売上を、それ以外と並べる（読み取りのみ）。

事前登録: `docs/bet_review/README.md`「H20 継続監視」。前向きの窓は 2026-10-06〜。

    set -a; source ~/.config/kiseki/env >/dev/null 2>&1; set +a
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h20_monitor.py [--from 20261006] [--to 20261231] [--all-cars]

母集団 = `netkeirin_submissions`（published・未削除・採点済み・settled_bet>0）。払戻は実払戻（settled_payout）。
既定は 7車だけ（2025 の台と同じ）。`--all-cars` で 9車も含める（参考）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import q  # noqa: E402

TARGET = {"特一般", "特予選"}
N_BOOT = 2000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="20261006")
    ap.add_argument("--to", dest="d2", default="20991231")
    ap.add_argument("--all-cars", action="store_true")
    a = ap.parse_args()
    cars = "" if a.all_cars else "and w.n_entries = 7"
    _, rows = q(f"""
        select s.race_key, w.race_date, w.race_type, s.settled_bet, s.settled_payout, coalesce(sr.sold_paid_points, 0)
          from keirin.netkeirin_submissions s
          join keirin.wt_races w on w.race_key = s.race_key
          left join keirin.netkeirin_sales_race sr on sr.race_key = s.race_key
         where s.race_key >= %s and s.race_key < %s and s.deleted_at is null and s.status = 'published'
           and s.settled_at is not null and s.settled_bet > 0 {cars}""", (a.d1, a.d2 + "z"))
    recs = [dict(day=str(d), tgt=(rt or "").strip() in TARGET, bet=float(b), pay=float(p), pt=float(pt))
            for _, d, rt, b, p, pt in rows]
    if not recs:
        print(f"{a.d1}〜{a.d2}: 採点済みの実売なし")
        return
    days = sorted({r["day"] for r in recs})
    print(f"窓 {days[0]}〜{days[-1]}  {len(days)}日  実売 {len(recs)}本  ({'7車+9車' if a.all_cars else '7車'})")
    for name, f in [("特一般・特予選", lambda r: r["tgt"]), ("それ以外", lambda r: not r["tgt"])]:
        xs = [r for r in recs if f(r)]
        if not xs:
            print(f"  {name}: 0本"); continue
        bet = sum(r["bet"] for r in xs); pay = sum(r["pay"] for r in xs)
        hit = np.mean([r["pay"] > r["bet"] for r in xs])
        print(f"  {name}: {len(xs)}本 ({len(xs)/len(days):.2f}/日)  回収率 {pay/bet:.1%}  表示的中 {hit:.1%}"
              f"  1本あたり売上 {np.mean([r['pt'] for r in xs]):.0f}pt  的中 {sum(r['pay'] > r['bet'] for r in xs)}件")
    # 回収率の差（対象 − それ以外）の開催日ブートストラップ
    byd = defaultdict(list)
    for r in recs:
        byd[r["day"]].append(r)
    rng = np.random.default_rng(0)
    diffs = []
    for _ in range(N_BOOT):
        pick = [r for i in rng.choice(len(days), len(days)) for r in byd[days[i]]]
        t = [r for r in pick if r["tgt"]]; o = [r for r in pick if not r["tgt"]]
        if t and o:
            diffs.append(sum(r["pay"] for r in t) / sum(r["bet"] for r in t)
                         - sum(r["pay"] for r in o) / sum(r["bet"] for r in o))
    if diffs:
        print(f"  回収率の差（対象 − それ以外）: {np.mean(diffs)*100:+.1f}pt "
              f"[{np.percentile(diffs, 2.5)*100:+.1f}, {np.percentile(diffs, 97.5)*100:+.1f}]")


if __name__ == "__main__":
    main()
