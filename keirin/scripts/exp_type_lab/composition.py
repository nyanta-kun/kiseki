#!/usr/bin/env python3
"""Phase 0 — 「A〜E=hit / F=pay」構成の実測と入稿ゲート通過後の件数（2026-08-28）。

**読むだけ**。`keirin.type_lab_picks` に書き込みは一切しない。

指標の定義は `backend/src/api/keirin_type_lab_router.combine_plans` と揃えてある:
  表示的中 = hit かつ payout > budget / 2倍+ = payout >= 2*budget
  ROI      = Σpayout / Σbudget（**採点済みの行だけ**）
  件/日    = 行数 ÷ その部分集合に行がある暦日数
"""
from __future__ import annotations

import importlib.util
import json
import os
import statistics
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "backend/src/services/keirin_type_lab_gate.py"
_spec = importlib.util.spec_from_file_location("tl_gate", GATE)
_g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g)
passes_axis_gate = _g.passes_axis_gate

EXPLORE = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-26")

USER = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_pay"]   # ご指定の構成
BASE = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"]   # 既定（doc の数字）

# 入稿ゲート（`keirin/src/stake_allocation.py`）
MIN_MEAN_PAYOUT = 20_000
MIN_POINT_ODDS = 2.0


def fetch(mode: str) -> list[dict]:
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT race_key, race_date::text AS race_date, race_type, type_label,"
                "       plan_key, n_entries, axis_sum, budget, settled_at, hit, payout,"
                "       n_legs, pred_mean_payout, pred_min_payout, legs"
                "  FROM keirin.type_lab_picks WHERE mode = %s", (mode,))
            return [dict(r) for r in cur.fetchall()]


def window(rows, lo, hi):
    return [r for r in rows if lo <= r["race_date"] <= hi]


def gated(rows):
    return [r for r in rows
            if passes_axis_gate(r["plan_key"], float(r["axis_sum"]) if r["axis_sum"] is not None else None,
                                r["n_entries"])]


def min_point_odds(r) -> float | None:
    try:
        legs = json.loads(r["legs"]) if isinstance(r["legs"], str) else r["legs"]
    except (TypeError, ValueError):
        return None
    vals = [float(x["pred_odds"]) for x in legs if x.get("pred_odds")]
    return min(vals) if vals else None


def one_per_race(rows):
    """1レース1商品。選択中プランが同じレースに2つ以上当たったら丸ごと外す。"""
    by = {}
    for r in rows:
        by.setdefault(r["race_key"], []).append(r)
    kept = [g[0] for g in by.values() if len(g) == 1]
    return kept, sum(1 for g in by.values() if len(g) > 1)


def stats(rows) -> dict:
    st = [r for r in rows if r["settled_at"] is not None]
    days = {r["race_date"] for r in rows}
    nd = max(len(days), 1)
    hits = [r for r in st if r["hit"]]
    shown = [r for r in hits if int(r["payout"] or 0) > int(r["budget"])]
    two = [r for r in hits if int(r["payout"] or 0) >= 2 * int(r["budget"])]
    big = [r for r in hits if int(r["payout"] or 0) >= 100_000]
    inv = sum(int(r["budget"]) for r in st)
    ret = sum(int(r["payout"] or 0) for r in st)
    pmp = [float(r["pred_mean_payout"]) for r in rows if r["pred_mean_payout"] is not None]
    return {
        "n": len(rows), "settled": len(st), "days": len(days),
        "per_day": len(rows) / nd,
        "raw_hit": len(hits) / len(st) * 100 if st else 0.0,
        "shown_hit": len(shown) / len(st) * 100 if st else 0.0,
        "gami": (len(hits) - len(shown)) / len(hits) * 100 if hits else 0.0,
        "med_pay": statistics.median([int(r["payout"] or 0) for r in shown]) if shown else 0,
        "mean_pred_pay": statistics.mean(pmp) if pmp else 0.0,
        "two_pd": len(two) / nd, "big_pd": len(big) / nd,
        "roi": ret / inv * 100 if inv else 0.0,
        "inv_pd": inv / nd,
    }


HDR = (f"{'':22} {'件/日':>7} {'表示的中':>8} {'生的中':>7} {'ガミ':>6} "
       f"{'払戻中央':>9} {'想定平均':>9} {'2倍+/日':>8} {'10万+/日':>8} {'ROI':>7} {'投資/日':>10}")


def line(label, s):
    return (f"{label:22} {s['per_day']:7.2f} {s['shown_hit']:7.2f}% {s['raw_hit']:6.2f}% "
            f"{s['gami']:5.1f}% {s['med_pay']:9,.0f} {s['mean_pred_pay']:9,.0f} "
            f"{s['two_pd']:8.3f} {s['big_pd']:8.3f} {s['roi']:6.1f}% {s['inv_pd']:10,.0f}")


def section(title):
    print("\n" + "=" * 118)
    print(title)
    print("=" * 118)


def run_combo(rows, plans, label, win):
    sub = [r for r in rows if r["plan_key"] in plans]
    sub = window(sub, *win)
    kept, conf = one_per_race(sub)
    s = stats(kept)
    return s, conf, kept


def main() -> None:
    p7 = fetch("paper")
    p9 = fetch("paper9")
    print(f"paper 7車 {len(p7):,}行 / paper9 9車 {len(p9):,}行")

    # ── 0-1 / 0-2 構成の実測（7車）──
    for wname, win in (("探索窓 2025", EXPLORE), ("確認窓 2026-01〜08-26", CONFIRM)):
        section(f"【0-1】7車 構成の実測 — {wname}")
        print(HDR)
        for pname, plans in (("ご指定 A~E hit/F pay", USER), ("既定  A~E hit/F hit", BASE)):
            for gname, fn in (("ゲートなし", lambda x: x), ("ゲートあり", gated)):
                s, conf, _ = run_combo(fn(p7), plans, pname, win)
                print(line(f"{pname[:11]} {gname}", s) + (f"  競合{conf}" if conf else ""))

    # ── 型F 直接対決 ──
    for wname, win in (("探索窓 2025", EXPLORE), ("確認窓 2026", CONFIRM)):
        section(f"【0-2】型F 直接対決 F_pay ↔ F_hit（同一レース・7車）— {wname}")
        print(HDR)
        for pk in ("F_hit", "F_pay"):
            for gname, fn in (("ゲートなし", lambda x: x), ("ゲートあり", gated)):
                sub = window([r for r in fn(p7) if r["plan_key"] == pk], *win)
                print(line(f"{pk} {gname}", stats(sub)))

    # ── 0-3 9車 ──
    d9 = sorted({r["race_date"] for r in p9})
    section(f"【0-3】9車 paper9（{d9[0]}〜{d9[-1]}）— 型Fは決勝限定・F_pay か F_hit か")
    print(HDR)
    for pk in ("F_hit", "F_pay"):
        sub = [r for r in p9 if r["plan_key"] == pk and str(r["race_type"] or "") == "決勝"]
        print(line(f"9車 決勝 {pk}", stats(sub)))
    print()
    for label, fplan in (("9車 A~E hit + 決勝F_pay", "F_pay"), ("9車 A~E hit + 決勝F_hit", "F_hit")):
        sub = [r for r in p9
               if (r["plan_key"] in USER[:5]
                   or (r["plan_key"] == fplan and str(r["race_type"] or "") == "決勝"))]
        kept, conf = one_per_race(sub)
        print(line(label, stats(kept)) + (f"  競合{conf}" if conf else ""))

    # ── 0-4 入稿ゲート ──
    for wname, win in (("探索窓 2025", EXPLORE), ("確認窓 2026", CONFIRM)):
        section(f"【0-4】入稿ゲート通過後（7車・ご指定構成・軸信頼ゲートあり）— {wname}")
        _, _, kept = run_combo(gated(p7), USER, "", win)
        print(HDR)
        print(line("ゲート前", stats(kept)))
        mean_ok = [r for r in kept if float(r["pred_mean_payout"] or 0) >= MIN_MEAN_PAYOUT]
        print(line(f"+平均払戻>={MIN_MEAN_PAYOUT:,}", stats(mean_ok)))
        both = [r for r in mean_ok if (min_point_odds(r) or 0) >= MIN_POINT_ODDS]
        print(line(f"+1点>={MIN_POINT_ODDS}倍", stats(both)))
        print("\n  プラン別の落ち方（ゲート前 → 平均払戻 → 1点odds）:")
        for pk in USER:
            a = sum(1 for r in kept if r["plan_key"] == pk)
            b = sum(1 for r in mean_ok if r["plan_key"] == pk)
            c = sum(1 for r in both if r["plan_key"] == pk)
            print(f"    {pk:8} {a:6,} → {b:6,} ({b/a*100 if a else 0:5.1f}%) → {c:6,} ({c/a*100 if a else 0:5.1f}%)")


if __name__ == "__main__":
    main()
