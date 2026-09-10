#!/usr/bin/env python3
"""レース選別（日単位KPI）の共通台（2026-09-10）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

入稿ループ `scripts/netkeirin_submit_type_lab.py::run` の順序:

  1. `sell_plans_for(型, 車数, 種別, pw_ent=, trio_ok=)` … 1レース**0か1**商品
  2. `_reject`: 無効/既出/他ランク取得/型ラボ取得/締切/**並び未公開**（判定に数えない）
     → **軸信頼ゲート** `passes_axis_gate`（判定に数える）
     → **入稿ゲート** `_gate_reason`（平均想定払戻>2万・全点の予測>=2.0倍・判定に数える）
  3. 日次上限 `cap_budget = max(1, int(n_judged * DAILY_CAP_RACE_FRACTION))`
     - `n_judged` = **枠外を除いた**判定済みレース数
     - 枠外 = 9車 / `daily_cap_exempt`（"決勝" 部分一致・`cup_grade>=3`）
     - 残す順 = `cap_priority(plan, axis_sum, rp_sd)` = (2*軸信頼順位 + 実力伯仲順位)/3
  4. 上限に当たった行は **高額枠**（B/C/D の `_sign`/`_big`・5本/日）へ差し替えを試みる

この台は 1〜3 を再現する。**4（高額枠）は再現しない**——`{型}_sign`/`{型}_big` は
2026-09-06 新設で paper 行（〜2026-08-26）に存在しないため。

## 台

`keirin.type_lab_picks` の `mode='paper'` × `n_entries=7`（本番の生成器が作った行）。
`rp_sd`（競走得点のレース内SD）と `cup_grade` は `/tmp/race_type_board.npz` から join。

  探索 2025-01-01〜2025-12-31 / 確認 2026-01-01〜2026-08-26

⚠️ 予測オッズ `odds_tf_n7` の train_end は 2025-12-31 ＝**確認窓だけが本番相当**。
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.type_lab import sell_plans_for  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "tl_gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
GATE = importlib.util.module_from_spec(_s)
_s.loader.exec_module(GATE)  # type: ignore[union-attr]

SCRATCH = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
               "7fb0ddaa-8674-4079-9511-429f747e6533/scratchpad")
PAPER = SCRATCH / "paper7.csv"
BOARD = SCRATCH / "board_meta.csv"

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
EXPLORE = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-26")

COLS = ("race_key date venue race_no rtype n_entries dayidx tl plan axis gap arare "
        "pw_ent budget pred_mean min_odds inv settled hit payout bet_type n_legs").split()


def _f(v):
    return float(v) if v not in ("", None) else None


def gate_ok(r: dict) -> bool:
    """`_gate_reason` と同じ2条件（判定できないものは通す）。"""
    if r["pred_mean"] is not None and r["pred_mean"] <= MIN_MEAN_PAYOUT:
        return False
    if r["min_odds"] is not None and r["min_odds"] < MIN_POINT_ODDS:
        return False
    return True


def load_meta():
    rp, cg = {}, {}
    with BOARD.open() as f:
        for k, sd, g, _v, _gr in csv.reader(f):
            if sd:
                rp[k] = float(sd)
            if g:
                try:
                    cg[k] = int(g)
                except ValueError:
                    pass
    return rp, cg


def load_races() -> list[dict]:
    """レース単位。本番の `sell_plans_for` で売る1商品を決め、入稿ゲートまで当てる。

    戻り値の各行は「判定した」レース（＝日次上限の分母に入る候補）。
    `ok_axis` / `ok_gate` はそれぞれのゲートを通ったか。
    """
    rp_sd, cup = load_meta()
    by_race: dict[str, dict[str, dict]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    with PAPER.open() as f:
        for rec in csv.reader(f):
            d = dict(zip(COLS, rec))
            rk = d["race_key"]
            by_race[rk][d["plan"]] = dict(
                plan=d["plan"], axis=_f(d["axis"]),
                pred_mean=_f(d["pred_mean"]), min_odds=_f(d["min_odds"]),
                inv=_f(d["inv"]) or 0.0, pay=_f(d["payout"]) or 0.0,
                settled=d["settled"] == "1", n_legs=int(d["n_legs"]),
                bet_type=d["bet_type"])
            meta[rk] = dict(date=d["date"], venue=d["venue"],
                            race_no=int(d["race_no"]), rtype=d["rtype"],
                            tl=d["tl"], dayidx=int(d["dayidx"]),
                            gap=_f(d["gap"]), arare=_f(d["arare"]),
                            pw_ent=_f(d["pw_ent"]))
    out = []
    for rk, plans in by_race.items():
        m = meta[rk]
        trio = plans.get("A_trio")
        trio_ok = bool(trio) and gate_ok(trio)
        keys = [p.key for p in sell_plans_for(m["tl"], 7, m["rtype"],
                                              pw_ent=m["pw_ent"], trio_ok=trio_ok)]
        if len(keys) != 1:
            continue
        r = plans.get(keys[0])
        if r is None or r["axis"] is None:
            continue
        cg = cup.get(rk)
        row = dict(m)
        row.update(race_key=rk, plan=r["plan"], axis=r["axis"],
                   inv=r["inv"], pay=r["pay"], settled=r["settled"],
                   n_legs=r["n_legs"], bet_type=r["bet_type"],
                   pred_mean=r["pred_mean"], min_odds=r["min_odds"],
                   rp_sd=rp_sd.get(rk), cup_grade=cg,
                   ok_gate=gate_ok(r),
                   ok_axis=GATE.passes_axis_gate(r["plan"], r["axis"], 7),
                   exempt=GATE.daily_cap_exempt(m["rtype"], cg))
        row["prio"] = 1.0 if row["exempt"] else GATE.cap_priority(
            r["plan"], r["axis"], row["rp_sd"])
        out.append(row)
    return out


def apply_cap(races: list[dict], fraction: float | None = None,
              prio=None, keep_exempt: bool = True,
              extra_pick=None) -> list[dict]:
    """日次上限を当てて**実際に入稿する行**を返す（本番 `run` の 2パス目）。

    fraction=None なら `DAILY_CAP_RACE_FRACTION`。0 で無効。
    prio(row)->float を渡すと残す順を差し替えられる（既定は本番の `cap_priority`）。
    extra_pick(day_rows_nonexempt_eligible, cap) を渡すと選抜そのものを差し替える。
    """
    frac = GATE.DAILY_CAP_RACE_FRACTION if fraction is None else fraction
    pf = prio or (lambda r: r["prio"])
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in races:
        by_day[r["date"]].append(r)
    sold = []
    for day, rows in by_day.items():
        ex = [r for r in rows if r["exempt"] and keep_exempt]
        non = [r for r in rows if not (r["exempt"] and keep_exempt)]
        sold += [r for r in ex if r["ok_axis"] and r["ok_gate"]]
        elig = [r for r in non if r["ok_axis"] and r["ok_gate"]]
        if not frac:
            sold += elig
            continue
        cap = max(1, int(len(non) * float(frac)))
        if extra_pick is not None:
            sold += extra_pick(elig, cap)
        else:
            elig.sort(key=lambda r: (-pf(r), r["venue"], r["race_no"]))
            sold += elig[:cap]
    return sold


def window(rows, name):
    lo, hi = EXPLORE if name == "explore" else CONFIRM
    return [r for r in rows if lo <= r["date"] <= hi]


# ───────────────────────── 指標 ─────────────────────────

def stats(sold: list[dict]) -> dict:
    st = [r for r in sold if r["settled"]]
    days = sorted({r["date"] for r in sold})
    nd = max(len(days), 1)
    if not st:
        return dict(n=len(sold), nd=nd, perday=len(sold) / nd)
    inv = np.array([r["inv"] for r in st])
    pay = np.array([r["pay"] for r in st])
    shown = pay >= inv
    hit = pay > 0
    return dict(n=len(sold), nd=nd, perday=len(sold) / nd,
                hit=hit.mean() * 100, shown=shown.mean() * 100,
                roi=pay.sum() / inv.sum() * 100,
                med=float(np.median(pay[shown])) if shown.any() else 0.0,
                big=(pay >= 100_000).sum() / nd,
                inv_pd=inv.sum() / nd)


def daily_frame(sold: list[dict]) -> dict:
    """日ごとの (件数, 表示的中数, 投資, 払戻, 10万+件数)。採点済みのみ。"""
    agg: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    for r in sold:
        if not r["settled"]:
            continue
        a = agg[r["date"]]
        a[0] += 1
        a[1] += 1 if r["pay"] >= r["inv"] else 0
        a[2] += r["inv"]
        a[3] += r["pay"]
        a[4] += 1 if r["pay"] >= 100_000 else 0
    ds = sorted(agg)
    return dict(date=np.array(ds),
                n=np.array([agg[d][0] for d in ds]),
                k=np.array([agg[d][1] for d in ds]),
                inv=np.array([agg[d][2] for d in ds]),
                pay=np.array([agg[d][3] for d in ds]),
                big=np.array([agg[d][4] for d in ds]))


def daily_stats(sold: list[dict], min_n: int = 5) -> dict:
    """日単位KPI。⚠️ `min_n` 件未満の日は率が不安定なので分布から外す。"""
    F = daily_frame(sold)
    m = F["n"] >= min_n
    rate = F["k"][m] / F["n"][m] * 100
    roi = np.where(F["inv"][m] > 0, F["pay"][m] / np.maximum(F["inv"][m], 1) * 100, 0.0)
    ov = F["k"].sum() / max(F["n"].sum(), 1) * 100
    return dict(days=int(m.sum()), all_days=len(F["n"]),
                med_rate=float(np.median(rate)) if len(rate) else 0.0,
                q1=float(np.percentile(rate, 25)) if len(rate) else 0.0,
                q3=float(np.percentile(rate, 75)) if len(rate) else 0.0,
                iqr=float(np.percentile(rate, 75) - np.percentile(rate, 25)) if len(rate) else 0.0,
                sd_rate=float(np.std(rate)) if len(rate) else 0.0,
                overall=ov,
                p_half=float((rate < ov / 2).mean() * 100) if len(rate) else 0.0,
                p_zero=float((F["k"][m] == 0).mean() * 100) if len(rate) else 0.0,
                p_roi100=float((roi >= 100).mean() * 100) if len(roi) else 0.0,
                med_roi=float(np.median(roi)) if len(roi) else 0.0,
                big_days=float((F["big"][m] > 0).mean() * 100) if len(rate) else 0.0)


HDR = ("{:36s} {:>6s} {:>8s} {:>7s} {:>9s} {:>8s} {:>8s}"
       .format("腕", "件/日", "表示的中%", "ROI%", "払戻中央", "10万+/日", "投資/日"))


def line(label, s):
    if "shown" not in s:
        return f"{label:36s}  (採点済みなし)"
    return (f"{label:36s} {s['perday']:6.2f} {s['shown']:7.2f}% {s['roi']:6.1f}% "
            f"{s['med']:9,.0f} {s['big']:8.3f} {s['inv_pd']:8,.0f}")


DHDR = ("{:36s} {:>5s} {:>8s} {:>8s} {:>7s} {:>7s} {:>8s} {:>8s} {:>8s}"
        .format("腕", "日数", "日中央%", "日IQR", "日SD", "半減日%", "0件日%",
                "日ROI100%", "10万日%"))


def dline(label, d):
    return (f"{label:36s} {d['days']:5d} {d['med_rate']:7.2f}% "
            f"{d['q1']:5.1f}-{d['q3']:<5.1f} {d['sd_rate']:6.2f} "
            f"{d['p_half']:6.1f}% {d['p_zero']:6.1f}% {d['p_roi100']:7.1f}% "
            f"{d['big_days']:7.1f}%")
