#!/usr/bin/env python3
"""ラインナップの腕比較（日次上限・軸信頼ゲート・高額枠まで再現）。"""
from __future__ import annotations
import argparse, pickle, statistics, sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineup_sim as S  # noqa: F401
from lineup_sim import board, ctx, build, gate_ok, settle, _G
import src.type_lab as TL
from src.type_lab import PLANS, sell_plans_for, highpay_plan_for, HIGHPAY_SLOTS_PER_DAY

CORE = ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit")


def race_rows(x, plans: dict):
    """そのレースで売りうる行（本命1つ＋高額枠候補）。"""
    # A_trio が組めてゲートを通るか（sell_plans_for の trio_ok）
    trio_ok = None
    if x.shape.type_label == "A":
        got = build(x, plans.get("A_trio", PLANS["A_trio"]))
        trio_ok = bool(got and gate_ok(got[0], got[1], got[3]))
    sell = sell_plans_for(x.shape.type_label, 7, x.rtype,
                          pw_ent=x.shape.pw_ent, trio_ok=trio_ok)
    if not sell:
        return None, None
    plan = plans.get(sell[0].key, sell[0])
    got = build(x, plan)
    main = None
    if got and gate_ok(got[0], got[1], got[3]):
        stakes, odds, used, mean = got
        main = dict(plan=used.key, stakes=stakes, trio=used.bet_type == "trio",
                    mean=mean, n=len(stakes))
    return main, None


def highpay_row(x, plans: dict, n_done: int):
    key = highpay_plan_for(x.shape.type_label, 7, n_done)
    if not key:
        return None
    got = build(x, plans.get(key, PLANS[key]))
    if not got or not gate_ok(got[0], got[1], got[3]):
        return None
    stakes, odds, used, mean = got
    return dict(plan=used.key, stakes=stakes, trio=used.bet_type == "trio",
                mean=mean, n=len(stakes))


#: 軸信頼ゲートを外す腕のためのスイッチ（`+nogate`）。
AXIS_GATE = True

#: 引き直した軸信頼ゲートの閾値（探索窓 2024-07〜2025-12・入稿ゲート通過後の
#: プラン内分位。`scripts/exp_type_lab/axis_gate_redraw.py` が出す）。
#: 🔴 `A_ana` は `AXIS_GATE_EXEMPT_PLANS` なので表に入れない（掛けると逆効果）。
AXIS_GATE_QUANTILES: dict[int, dict[str, float]] = {
    10: {"A_hit": 1.543, "A_trio": 1.469, "B_hit": 1.461, "C_hit": 1.455,
         "D_hit": 1.208, "E_hit": 1.206, "F_hit": 1.198, "F_sign": 1.185},
    20: {"A_hit": 1.595, "A_trio": 1.504, "B_hit": 1.483, "C_hit": 1.469,
         "D_hit": 1.265, "E_hit": 1.263, "F_hit": 1.246, "F_sign": 1.238},
    30: {"A_hit": 1.632, "A_trio": 1.527, "B_hit": 1.505, "C_hit": 1.485,
         "D_hit": 1.305, "E_hit": 1.297, "F_hit": 1.279, "F_sign": 1.276},
    40: {"A_hit": 1.666, "A_trio": 1.551, "B_hit": 1.529, "C_hit": 1.501,
         "D_hit": 1.337, "E_hit": 1.326, "F_hit": 1.304, "F_sign": 1.304},
}


def run(arm_name: str, plans: dict, idx, cache, cap=True, highpay=True):
    byday = defaultdict(list)
    for i in idx:
        x = cache[i]
        if x is not None:
            byday[x.date].append(x)
    recs = []
    for day, races in sorted(byday.items()):
        cand = []
        for x in races:
            main, _ = race_rows(x, plans)
            if main is None:
                continue
            passes = (_G.passes_axis_gate(main["plan"], float(x.shape.axis_sum), 7)
                      if AXIS_GATE else True)
            cand.append((x, main, passes))
        # 日次上限: 枠外（決勝・高グレード）を除いた判定対象の半分
        judged = [c for c in cand if not _G.daily_cap_exempt(c[0].rtype, c[0].cupg)
                  and not _G.daily_cap_exempt_plan(c[1]["plan"])]
        budget = max(1, int(len(judged) * float(_G.DAILY_CAP_RACE_FRACTION))) if cap and judged else None
        order = sorted(cand, key=lambda c: -_G.cap_priority(
            c[1]["plan"], float(c[0].shape.axis_sum), c[0].rp_sd))
        n_capped, dropped = 0, []
        for x, main, passes in order:
            exempt = (_G.daily_cap_exempt(x.rtype, x.cupg)
                      or _G.daily_cap_exempt_plan(main["plan"]))
            if not passes:
                dropped.append(x); continue
            if budget is not None and not exempt and n_capped >= budget:
                dropped.append(x); continue
            if not exempt:
                n_capped += 1
            inv, pay = settle(x, main["stakes"], main["trio"])
            recs.append(dict(day=day, plan=main["plan"], inv=inv, pay=pay,
                             mean=main["mean"], n=main["n"], slot="main"))
        if highpay:
            n_done = 0
            for x in dropped:
                if n_done >= HIGHPAY_SLOTS_PER_DAY:
                    break
                hp = highpay_row(x, plans, n_done)
                if not hp:
                    continue
                inv, pay = settle(x, hp["stakes"], hp["trio"])
                recs.append(dict(day=day, plan=hp["plan"], inv=inv, pay=pay,
                                 mean=hp["mean"], n=hp["n"], slot="highpay"))
                n_done += 1
    return recs


def summarize(recs, ndays):
    if not recs:
        return None
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > r["inv"]]      # 表示的中＝払戻>投資
    pays = sorted(r["pay"] for r in hits)
    return dict(n=len(recs), perday=len(recs)/ndays, shown=len(hits)/len(recs)*100,
                roi=pay/inv*100, med=statistics.median(pays) if pays else 0,
                b2=sum(1 for p in pays if p >= 20_000)/ndays,
                b3=sum(1 for p in pays if p >= 30_000)/ndays,
                b4=sum(1 for p in pays if p >= 40_000)/ndays,
                b7=sum(1 for p in pays if p >= 70_000)/ndays,
                b10=sum(1 for p in pays if p >= 100_000)/ndays,
                k=statistics.mean(r["n"] for r in recs),
                mean_plan=statistics.median([r["mean"] for r in recs]),
                inv_day=inv/ndays)


HEAD = (f"{'腕':<16}{'件/日':>7}{'表示的中':>9}{'ROI':>7}{'払戻中央':>9}"
        f"{'2万+/日':>8}{'3万+/日':>8}"
        f"{'4万+/日':>8}{'7万+/日':>8}{'10万+/日':>9}{'点数':>6}{'計画中央':>9}")


def line(name, s):
    if not s: return f"{name:<22} (該当なし)"
    return (f"{name:<16}{s['perday']:>7.1f}{s['shown']:>9.2f}{s['roi']:>7.1f}"
            f"{s['med']:>9,.0f}{s['b2']:>8.2f}{s['b3']:>8.2f}{s['b4']:>8.2f}{s['b7']:>8.2f}"
            f"{s['b10']:>9.3f}{s['k']:>6.1f}{s['mean_plan']:>9,.0f}")


def v0907() -> dict:
    """9/08〜9/21 の「払戻を下げる側」の変更を全部外した配置（= 9/07 時点）。

    - `C_hit`: 帯下1点差込(#508) と τ適応(#567) を外す → 帯15倍・12点
    - `F_hit`: 安すぎる目の下限5倍(#513) を外す（代替の差込も外す）
    - `E_hit`: 計画払戻の床3.5万(#586) を外す → 14点固定
    - `A_hit`/`B_hit`/`E_hit`: 並び違いの押さえ1点(#598) を外す
    """
    out = {}
    out["C_hit"] = replace(PLANS["C_hit"], underband_min=0.0, tau_adaptive=False,
                           tau_floor=0, tau_max_legs=0)
    out["F_hit"] = replace(PLANS["F_hit"], min_odds=0.0)
    out["E_hit"] = replace(PLANS["E_hit"], tau_adaptive=False, tau_floor=0,
                           tau_max_legs=0, max_legs=14)
    return out


def dutch_target(T: int, types: str = "ABCDEF", max_odds: float = 600.0) -> dict:
    """core を「ダッチで計画払戻 T 円」（= 看板枠と同じ構造）へ置き換える。"""
    out = {}
    for t in types:
        k = f"{t}_hit"
        p = PLANS[k]
        out[k] = replace(p, structure="signboard", alloc="dutch", target=T,
                         n_partners=0, min_odds=0.0, max_odds=max_odds,
                         max_legs=0, sigma_max=0.0, tau_adaptive=False,
                         tau_floor=0, tau_max_legs=0, underband_min=0.0, bust=False)
    # 9車型F の三連複（F_line）と A_trio はそのまま
    return out


def core_floor(T: int, max_legs: int = 20) -> dict:
    """core 6プランの計画払戻の床を T にした PLANS 差分。"""
    out = {}
    for k in CORE:
        p = PLANS[k]
        out[k] = replace(p, tau_adaptive=True, tau_floor=T,
                         tau_max_legs=max_legs,
                         max_legs=max(p.max_legs, max_legs) if p.max_legs else max_legs,
                         sigma_max=0.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--by-plan", action="store_true")
    ap.add_argument("--arms", default="current,25000,30000,35000,40000,50000")
    args = ap.parse_args()
    z = board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
         & z["OKPRED"] & (z["DATE"] >= args.start) & (z["DATE"] <= args.end))
    idx = np.flatnonzero(m)
    print(f"対象 {len(idx)}R  {args.start}〜{args.end}", flush=True)
    cache = {int(i): ctx(int(i)) for i in idx}
    ok = [int(i) for i in idx if cache.get(int(i)) is not None]
    ndays = len({cache[i].date for i in ok})
    print(f"組めた {len(ok)}R / {ndays}日", flush=True)
    print(HEAD)
    big0 = TL.HIGHPAY_BIG_SLOTS
    addperm0, gf0 = TL.ADD_PERM_PLANS, dict(TL.GATE_FALLBACK)
    gate_min0 = dict(_G.AXIS_GATE_MIN)
    for a in args.arms.split(","):
        spec = a.split("+")
        base = spec[0]
        # 🔴 高額枠の内訳（_big=計画40万）を止める腕は module 定数を差し替える
        TL.HIGHPAY_BIG_SLOTS = frozenset() if "h1" in spec[1:] else big0
        globals()["AXIS_GATE"] = "nogate" not in spec[1:]
        _q = [t for t in spec[1:] if t.startswith("p") and t[1:].isdigit()]
        _G.AXIS_GATE_MIN = (dict(AXIS_GATE_QUANTILES[int(_q[0][1:])])
                            if _q else dict(gate_min0))
        TL.ADD_PERM_PLANS = frozenset() if base == "v0907" else addperm0
        if base == "v0907":
            plans = v0907()
            TL.GATE_FALLBACK = {k: v for k, v in gf0.items() if k != "F_hit"}
        else:
            TL.GATE_FALLBACK = gf0
        if base == "v0907":
            pass
        elif base == "current":
            plans = {}
        elif base.startswith("dutch"):
            parts = base.split(":")
            plans = dutch_target(int(parts[1]),
                                 parts[2] if len(parts) > 2 else "ABCDEF",
                                 float(parts[3]) if len(parts) > 3 else 600.0)
        else:
            plans = core_floor(int(base))
        recs = run(a, plans, ok, cache)
        print(line(a, summarize(recs, ndays)), flush=True)
        if args.by_plan:
            g = {}
            for r in recs:
                g.setdefault(r["plan"], []).append(r)
            for k in sorted(g, key=lambda k: -len(g[k])):
                print("   " + line(k, summarize(g[k], ndays)), flush=True)
    TL.HIGHPAY_BIG_SLOTS, TL.ADD_PERM_PLANS = big0, addperm0
    TL.GATE_FALLBACK = gf0
    _G.AXIS_GATE_MIN = gate_min0


if __name__ == "__main__":
    main()
