"""実入稿（本番の真実）で、配分と足切りが確定オッズとどれだけ食い違うかを測る。

    PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/01_submissions_vs_final.py [--from 20260805]

母集団は `netkeirin_submissions.bet_detail` に残っている**実際に入稿した買い目**。
既定の開始日 20260805 は**本番モデルの学習終端 2026-08-04 の翌日**＝ honest。
（それ以前を本番モデルで採点すると in-sample になる。過去窓は 02 を使うこと。）
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict

from _common import combo, final_boards, race_inputs  # noqa: E402
from src.odds_prediction import predict_board, model_train_end  # noqa: E402
from src.stake_allocation import (  # noqa: E402
    MIN_EXPECTED_PAYOUT_BY_RANK, MIN_MEAN_PAYOUT, MIN_POINT_ODDS,
)
from _common import q  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="20260805")
    a = ap.parse_args()
    end = (model_train_end() or "").replace("-", "")
    if end and a.date_from <= end:
        raise SystemExit(f"{a.date_from} は学習終端 {end} 以前です（in-sample になる）")

    subs = q("""SELECT race_key, rank_key, origin, bet_detail
                FROM keirin.netkeirin_submissions
                WHERE race_key >= %s AND bet_detail IS NOT NULL ORDER BY race_key""",
             (a.date_from,))
    fin = final_boards(a.date_from)
    boards = {}
    for rk, (cars, p3, pw, meta) in race_inputs({s["race_key"] for s in subs}).items():
        try:
            boards[rk] = predict_board(cars, p3, pw, meta)
        except Exception:
            pass

    rows = []
    for s in subs:
        d = json.loads(s["bet_detail"])
        lines = d.get("lines") or []
        if not lines or any(ln.get("bet_type") != "3連複" for ln in lines):
            continue          # 三連単の商品は対象外（予測オッズは三連複しか作れない）
        rk = s["race_key"]
        board, f = boards.get(rk), fin.get(rk)
        if not board or not f:
            continue
        pts = []
        for ln in lines:
            c = combo(ln["combo"])
            p, v = board.get(c), f.get(c)
            if not p or not v:
                pts = []
                break
            pts.append((int(ln["stake"]), float(p), float(v)))
        if not pts:
            continue
        budget = int(d.get("total") or 10000)
        rows.append(dict(
            rank=s["rank_key"], origin=s["origin"], pts=pts, budget=budget,
            mp=st.mean(p * s_ for s_, p, _ in pts), mf=st.mean(v * s_ for s_, _, v in pts),
            flp=min(p * s_ for s_, p, _ in pts) / budget,
            flf=min(v * s_ for s_, _, v in pts) / budget))
    print(f"対象 {len(rows)} 商品（{a.date_from}〜・三連複・予測オッズが作れたもの）")

    pt = [(p, v) for r in rows for _, p, v in r["pts"]]
    rat = sorted(v / p for p, v in pt)
    print(f"\n【1点ごと】n={len(rat)}  中央 確定/予測 {st.median(rat):.3f}"
          f"  <0.8倍 {100 * sum(1 for x in rat if x < 0.8) / len(rat):.1f}%"
          f"  <0.5倍 {100 * sum(1 for x in rat if x < 0.5) / len(rat):.1f}%")

    print("\n【配分】計画では全点の払戻をそろえるはずだった")
    spp = [max(p * s_ for s_, p, _ in r["pts"]) / min(p * s_ for s_, p, _ in r["pts"]) for r in rows]
    spf = [max(v * s_ for s_, _, v in r["pts"]) / min(v * s_ for s_, _, v in r["pts"]) for r in rows]
    print(f"  払戻の最大/最小 中央: 計画 {st.median(spp):.2f} → 確定 {st.median(spf):.2f}")

    print(f"\n【平均払戻 {MIN_MEAN_PAYOUT:,}円ゲート】")
    ps = [r for r in rows if r["mp"] > MIN_MEAN_PAYOUT]
    cs = [r for r in rows if r["mp"] <= MIN_MEAN_PAYOUT]
    print(f"  予測で通る {len(ps)} / 切る {len(cs)}   実際/予測 中央 "
          f"{st.median([r['mf'] / r['mp'] for r in rows]):.3f}")
    print(f"  通した中で実際に {MIN_MEAN_PAYOUT:,}円超: "
          f"{100 * sum(1 for r in ps if r['mf'] > MIN_MEAN_PAYOUT) / max(len(ps), 1):.1f}%")
    print(f"  切った中で実際は {MIN_MEAN_PAYOUT:,}円超: "
          f"{100 * sum(1 for r in cs if r['mf'] > MIN_MEAN_PAYOUT) / max(len(cs), 1):.1f}%"
          f"（{sum(1 for r in cs if r['mf'] > MIN_MEAN_PAYOUT)}件）")

    fl = [r for r in rows if r["rank"] in MIN_EXPECTED_PAYOUT_BY_RANK]
    if fl:
        thr = MIN_EXPECTED_PAYOUT_BY_RANK[fl[0]["rank"]]
        ps = [r for r in fl if r["flp"] >= thr]
        cs = [r for r in fl if r["flp"] < thr]
        print(f"\n【最低払戻 {thr}倍ゲート（{'/'.join(sorted(MIN_EXPECTED_PAYOUT_BY_RANK))}・{len(fl)}件）】")
        print(f"  予測の下限 中央 {st.median([r['flp'] for r in fl]):.2f} → "
              f"確定の下限 中央 {st.median([r['flf'] for r in fl]):.2f}"
              f"  （実際/予測 中央 {st.median([r['flf'] / r['flp'] for r in fl]):.3f}）")
        print(f"  通した {len(ps)}件のうち実際に {thr}倍以上: "
              f"{100 * sum(1 for r in ps if r['flf'] >= thr) / max(len(ps), 1):.1f}%")
        print(f"  切った {len(cs)}件のうち実際は {thr}倍以上: "
              f"{100 * sum(1 for r in cs if r['flf'] >= thr) / max(len(cs), 1):.1f}%")

    print(f"\n【1点 {MIN_POINT_ODDS}倍ゲート】")
    fp = sum(1 for r in rows if min(p for _, p, _ in r["pts"]) >= MIN_POINT_ODDS
             and min(v for _, _, v in r["pts"]) < MIN_POINT_ODDS)
    fc = sum(1 for r in rows if min(p for _, p, _ in r["pts"]) < MIN_POINT_ODDS
             and min(v for _, _, v in r["pts"]) >= MIN_POINT_ODDS)
    print(f"  予測で通したが確定では割れた {fp}件 / 予測で切ったが確定では割れなかった {fc}件")

    print("\n【ランク別】商品数 / 平均払戻 実際-予測比 / 配分の食い違い")
    by = defaultdict(list)
    for r in rows:
        by[r["rank"]].append(r)
    for rk in sorted(by, key=lambda k: -len(by[k])):
        g = by[rk]
        if len(g) < 15:
            continue
        l1 = []
        for r in g:
            wp = [1 / p for _, p, _ in r["pts"]]
            wf = [1 / v for _, _, v in r["pts"]]
            sp, sf = sum(wp), sum(wf)
            l1.append(sum(abs(x / sp - y / sf) for x, y in zip(wp, wf)))
        print(f"  {rk:4s} {len(g):4d}件  平均払戻比 {st.median([r['mf'] / r['mp'] for r in g]):.3f}"
              f"  配分L1 中央 {st.median(l1):.3f}")


if __name__ == "__main__":
    main()
