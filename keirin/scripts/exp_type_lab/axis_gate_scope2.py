#!/usr/bin/env python3
"""軸信頼ゲートの条件を見直すと的中率・ROI が変わるか（2026-09-09・ユーザー依頼）。

## 先に本番を読む（CLAUDE.md「測る前に本番コードを読む」）

判定の正本は `backend/src/services/keirin_type_lab_gate.py`:

  - `AXIS_GATE_MIN` … **A_hit / D_hit / E_hit / F_hit の4プランだけ**に下限。
    値は探索窓 2025年の**プラン内 p20**（＝そのプランの下位1/5を外す）。
  - `AXIS_GATE_EXEMPT_PLANS` … 意図して掛けないプラン（2026-09-03 に縮小）。
  - `passes_axis_gate` は **7車以外を素通し**・表に無い鍵も素通し。
  - 入稿ループの順序は「並び未公開 → **軸ゲート** → 入稿ゲート(平均想定払戻/1点オッズ)」。

## 台

**`keirin.type_lab_picks` の `mode='paper'`（本番の生成器 `build_type_lab_picks` が
作った行）を DB から取り、採点済みの実払戻で評価する。** 実験用に組み直さないので
`build_with_gate_fallback` や賭け金0円の除去まで本番と同じ。

  探索 2025-01-01〜12-31 / 確認 2026-01-01〜08-26（予測オッズ train_end 2025-12-31）

売る1商品は本番の `sell_plans_for`（看板枠 → 型Aの3分割 → 型F の種別）で選び、
`trio_ok` は同じレースの `A_trio` 行が入稿ゲートを通るかで決める（本番と同じ）。

🔴 件数が変わる腕には**無作為対照20本**（`docs/RECOMMENDATION.md` §6）。
🔴 差は**レース単位ブートストラップ**の 95%CI で出す。ROI で採否は決めない。
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.type_lab import sell_plans_for  # noqa: E402

import importlib.util  # noqa: E402
_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]

CSV_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/paper_rows.csv")
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
EXPLORE = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-26")


def gate_ok(r: dict) -> bool:
    """`_gate_reason` と同じ2条件（判定できないものは通す）。"""
    if r["mean"] is not None and r["mean"] <= MIN_MEAN_PAYOUT:
        return False
    if r["min_odds"] is not None and r["min_odds"] < MIN_POINT_ODDS:
        return False
    return True


def load():
    by_race: dict[str, dict[str, dict]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    with CSV_PATH.open() as f:
        for d in csv.DictReader(f):
            rk = d["race_key"]
            row = dict(
                plan=d["plan_key"], axis=float(d["axis_sum"]),
                mean=float(d["pred_mean_payout"]) if d["pred_mean_payout"] else None,
                min_odds=float(d["min_odds"]) if d["min_odds"] else None,
                inv=float(d["inv"]), pay=float(d["payout"] or 0),
                date=d["race_date"])
            by_race[rk][d["plan_key"]] = row
            meta[rk] = dict(date=d["race_date"], rtype=d["race_type"],
                            tl=d["type_label"],
                            pw_ent=float(d["pw_ent"]) if d["pw_ent"] else None)
    sold = []
    for rk, plans in by_race.items():
        m = meta[rk]
        trio = plans.get("A_trio")
        trio_ok = bool(trio) and gate_ok(trio)
        keys = [p.key for p in sell_plans_for(m["tl"], 7, m["rtype"],
                                              pw_ent=m["pw_ent"], trio_ok=trio_ok)]
        if len(keys) != 1:
            continue
        r = plans.get(keys[0])
        if r is None or not gate_ok(r):          # 入稿ゲート落ち＝そもそも売らない
            continue
        sold.append(r)
    return sold


def split(sold):
    out = {}
    for name, (lo, hi) in (("explore", EXPLORE), ("confirm", CONFIRM)):
        rows = [r for r in sold if lo <= r["date"] <= hi]
        out[name] = dict(
            plan=np.array([r["plan"] for r in rows]),
            axis=np.array([r["axis"] for r in rows]),
            inv=np.array([r["inv"] for r in rows]),
            pay=np.array([r["pay"] for r in rows]),
            date=np.array([r["date"] for r in rows]),
            nd=len({r["date"] for r in rows}))
    return out


def quantiles(W, q: float) -> dict[str, float]:
    """探索窓の**プラン内**分位（本番の閾値の作り方と同じ）。"""
    a = W["explore"]
    out = {}
    for p in np.unique(a["plan"]):
        out[str(p)] = float(np.percentile(a["axis"][a["plan"] == p], q))
    return out


def stats(A, mask, nd) -> dict:
    inv, pay = A["inv"][mask], A["pay"][mask]
    n = len(inv)
    if n == 0:
        return dict(n=0)
    hit = pay > 0
    shown = pay >= inv
    pays = np.sort(pay[hit])
    return dict(n=n, perday=n / nd, hit=hit.mean() * 100, shown=shown.mean() * 100,
                roi=pay.sum() / inv.sum() * 100,
                med=float(np.median(pays)) if len(pays) else 0.0,
                big=(pay >= 100_000).sum() / nd, net=(pay.sum() - inv.sum()) / nd)


def mask_for(A, thr: dict[str, float] | None) -> np.ndarray:
    if not thr:
        return np.ones(len(A["axis"]), bool)
    lo = np.array([thr.get(str(p), 0.0) for p in A["plan"]])
    return A["axis"] >= lo


def controls(A, nd, k: int, base_mask: np.ndarray, s: dict, seeds=20):
    """同数を無作為に選ぶ対照（母集団はゲート無しの全体）。"""
    idx = np.flatnonzero(base_mask)
    cs, cr = [], []
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        pick = rng.choice(idx, size=k, replace=False)
        m = np.zeros(len(A["axis"]), bool)
        m[pick] = True
        u = stats(A, m, nd)
        cs.append(u["shown"]); cr.append(u["roi"])
    return (sum(s["shown"] > c for c in cs), float(np.median(cs)),
            sum(s["roi"] > c for c in cr), float(np.median(cr)))


def boot_delta(A, m1, m2, nd, iters=1000, seed=0):
    """レース単位ブートストラップで (表示的中差, ROI差) の95%CI。m1 − m2。"""
    rng = np.random.default_rng(seed)
    n = len(A["axis"])
    ds, dr = [], []
    inv, pay = A["inv"], A["pay"]
    shown = (pay >= inv).astype(float)
    for _ in range(iters):
        b = rng.integers(0, n, n)
        for m, acc_s, acc_r in ((m1, [], []),):
            pass
        s1, s2 = m1[b], m2[b]
        if s1.sum() == 0 or s2.sum() == 0:
            continue
        ds.append(shown[b][s1].mean() * 100 - shown[b][s2].mean() * 100)
        dr.append(pay[b][s1].sum() / inv[b][s1].sum() * 100
                  - pay[b][s2].sum() / inv[b][s2].sum() * 100)
    f = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return (float(np.mean(ds)), *f(ds), float(np.mean(dr)), *f(dr))


HEAD = ("    {:34s} {:>6s} {:>7s} {:>9s} {:>7s} {:>9s} {:>8s} {:>9s}"
        .format("腕", "件/日", "的中%", "表示的中%", "ROI%", "払戻中央", "10万+/日", "収支/日"))


def main() -> None:
    sold = load()
    W = split(sold)
    print(f"売る商品（入稿ゲート後） 探索 {len(W['explore']['axis']):,}件 / "
          f"確認 {len(W['confirm']['axis']):,}件")

    # ── 台の検算: 本番の閾値を再現できるか ───────────────────────────────
    p20 = quantiles(W, 20)
    print("\n■ 検算（探索窓 2025 のプラン内 p20 ↔ 本番 `AXIS_GATE_MIN`）")
    for k, v in sorted(_G.AXIS_GATE_MIN.items()):
        print(f"    {k:8s} 本番 {v:.3f}  ↔  この台 {p20.get(k, float('nan')):.3f}")

    cur = dict(_G.AXIS_GATE_MIN)
    allp = sorted({str(p) for p in W["confirm"]["plan"]})
    print(f"    売られているプラン: {allp}")

    for wn, lab in (("explore", "探索 2025"), ("confirm", "確認 2026-01〜08（本番相当）")):
        A, nd = W[wn], W[wn]["nd"]
        full = np.ones(len(A["axis"]), bool)
        base = mask_for(A, cur)
        s_base = stats(A, base, nd)
        arms: list[tuple[str, dict | None]] = [("⓪ ゲート無し", None),
                                               ("① 現行（4プランに p20）", cur)]
        for q in (10, 30, 40, 50):
            t = quantiles(W, q)
            arms.append((f"② 現行4プランを p{q} へ", {k: t[k] for k in cur}))
        for q in (10, 20, 30):
            t = quantiles(W, q)
            arms.append((f"③ 全プランに p{q}"
                         + ("（2026-09-03 以前）" if q == 20 else ""), t))
        for add in ("B_hit", "C_hit", "A_trio", "A_ana", "F_sign"):
            t = quantiles(W, 20)
            if add in t:
                arms.append((f"④ 現行 + {add} を戻す", dict(cur, **{add: t[add]})))
        for q in (20, 30, 40):
            t = quantiles(W, q)
            arms.append((f"⑥ A_ana 以外の全プランに p{q}",
                         {k: v for k, v in t.items() if k != "A_ana"}))
        t30 = quantiles(W, 30)
        arms.append(("⑦ 現行4は p20 のまま + 他(A_ana除く)を p30",
                     dict({k: v for k, v in t30.items() if k not in cur and k != "A_ana"},
                          **cur)))
        for drop in sorted(cur):
            d = {k: v for k, v in cur.items() if k != drop}
            arms.append((f"⑤ 現行 − {drop} を外す", d))

        print("\n" + "=" * 118)
        print(f"=== {lab}   日数={nd}")
        print("=" * 118)
        print(HEAD + "   {:>26s}   {:>34s}".format(
            "無作為対照20本（同数）", "現行との差 95%CI（表示的中 / ROI）"))
        for name, thr in arms:
            m = mask_for(A, thr)
            s = stats(A, m, nd)
            if not s["n"]:
                continue
            c = controls(A, nd, s["n"], full, s) if s["n"] < len(A["axis"]) else None
            ctl = (f"的中 {c[0]:2d}/20(中{c[1]:5.2f}) ROI {c[2]:2d}/20(中{c[3]:5.1f})"
                   if c else " " * 26)
            if name.startswith("①"):
                dd = " " * 34
            else:
                b = boot_delta(A, m, base, nd)
                dd = (f"{b[0]:+5.2f}pt [{b[1]:+5.2f},{b[2]:+5.2f}]  "
                      f"{b[3]:+5.1f} [{b[4]:+5.1f},{b[5]:+5.1f}]")
            print(f"    {name:34s} {s['perday']:6.2f} {s['hit']:7.2f} {s['shown']:9.2f}"
                  f" {s['roi']:7.1f} {s['med']:9,.0f} {s['big']:8.3f} {s['net']:+9,.0f}"
                  f"   {ctl}   {dd}")

        # プラン別（通過 − 落ちた）を p20 で
        print(f"\n  ■ プラン別 p20 の効き（通過 − 落ちた の表示的中pt）  {lab}")
        t20 = quantiles(W, 20)
        for p in allp:
            sel = A["plan"] == p
            if sel.sum() < 50:
                continue
            hi = sel & (A["axis"] >= t20[p])
            lo = sel & (A["axis"] < t20[p])
            if lo.sum() < 20:
                continue
            a, b = stats(A, hi, nd), stats(A, lo, nd)
            mark = " ←現行掛かる" if p in cur else ""
            print(f"    {p:8s} 通過 n={a['n']:5,} 表示的中 {a['shown']:5.2f}% ROI {a['roi']:5.1f}"
                  f"  ↔ 落ちた n={b['n']:4,} {b['shown']:5.2f}% ROI {b['roi']:5.1f}"
                  f"   差 {a['shown']-b['shown']:+6.2f}pt / ROI {a['roi']-b['roi']:+6.1f}{mark}")


if __name__ == "__main__":
    main()
