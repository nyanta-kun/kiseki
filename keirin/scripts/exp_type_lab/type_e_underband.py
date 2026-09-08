#!/usr/bin/env python3
"""型E（E_hit）にも帯の下から1点を差し込むか（2026-09-08・ユーザー依頼）。

型C（`type_c.md` 11章・PR#508）と型F（`type_f.md` 7章・PR#513）に続く3件目。

## 型C・型F との違い（先に読むこと）

| | 帯 | 点数 | 帯下の幅 |
|---|--:|--:|---|
| `C_hit` | 15倍 | 12 | 5〜15倍（狭い） |
| `F_hit` | **本命は5倍**（代替が15倍） | 12 | 5〜15倍（代替のみ） |
| `E_hit` | **30倍** | **14** | **5〜30倍（広い）** |

🔴 **帯が広いので「差し込む1点の下限」の選択が型C より効く。** 5倍の目を入れると
   床（予算×2.0÷5.0＝4,000円）が予算の4割を食い、残り13点を30倍以上で賄えず
   `conf` が組めない → ダッチへ落ち → 平均想定払戻2万ゲートに落ちる。
   **下限は必ず掃引すること**（型C の 5.0 をそのまま持ち込まない）。

🔴 **既知の否定結果**（`type_e_2026_09_01.md` / [[keirin-type-e-no-edge-2026-09-01]]）:
   型E の外れの主因は**帯ではなく確率14点に入らないこと**（49.1% ↔ 帯 29.0%。
   型C は帯 56.5%）。「同じ処方は効かない」と書いてある。ここではその予測を
   実測で確かめる。帯 L=30 自体を動かす根拠が無いことも既に測ってある。

## 作法

- 母集団は 型E × 7車 × 軸信頼ゲート（`AXIS_GATE_MIN["E_hit"]=1.245`）
- 本番の入口（`build_with_gate_fallback`）で組む。差込がゲートを割ったら
  差込なしへ戻す（型C と同じ `GATE_FALLBACK` の形）＝**在庫を減らさない**
- 判断は表示的中。ROI では決めない。**無作為対照を必ず置く**

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_e_underband.py diag
      diag   帯30倍で切った目がどれくらい決着しているか
      floor  差し込む1点の下限の掃引（対照つき）
"""
from __future__ import annotations

import random
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import common as C  # noqa: E402
from typef_racetype import ctx, AXIS_GATE_MIN  # noqa: E402
from src.type_lab import (  # noqa: E402
    GATE_FALLBACK, PLANS, build_legs, build_with_gate_fallback,
    mean_expected_payout,
)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
WIN = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))
CUR = PLANS["E_hit"]
#: 差し込みがゲートを割ったときの受け皿（＝現行の E_hit）。
FB_PLAIN = replace(CUR, underband_min=0.0, note="E_hit の受け皿: 差込なしで14点")


@contextmanager
def fallback(fbs):
    import src.type_lab as T
    had = "E_hit" in T.GATE_FALLBACK
    orig = T.GATE_FALLBACK.get("E_hit")
    T.GATE_FALLBACK["E_hit"] = tuple(fbs)
    try:
        yield
    finally:
        if had:
            T.GATE_FALLBACK["E_hit"] = orig
        else:
            del T.GATE_FALLBACK["E_hit"]


def pop(win: str) -> list:
    z = C.board()
    idx = C.select("E", win)
    idx = idx[np.array([float(z["AXIS_SUM"][i]) >= AXIS_GATE_MIN["E_hit"] for i in idx])]
    return [x for i in idx if (x := ctx(int(i))) is not None]


def sold(x, plan):
    got = build_with_gate_fallback(x.shape, plan, x.po_tf, x.pr_tf, n_entries=7,
                                   min_mean_payout=MIN_MEAN_PAYOUT)
    if not got:
        return None
    legs, st, used = got
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    return legs, st, used


def rec_of(x, st) -> dict:
    pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, x.po_tf))


def _boot(pairs, n=2000, seed=0):
    rnd = random.Random(seed)
    m = len(pairs)
    out = []
    for _ in range(n):
        s = [pairs[rnd.randrange(m)] for _ in range(m)]
        out.append((sum(p[1] for p in s) - sum(p[0] for p in s)) / m * 100)
    out.sort()
    return out[int(n * 0.025)], out[int(n * 0.975)]


# ───────────────────────── 診断 ─────────────────────────

def diag() -> None:
    for label, win in WIN:
        xs = pop(win)
        nd = C.days_of(C.select(None, win))
        tot = miss = cut = 0
        cut_odds, bands = [], {5: 0, 8: 0, 10: 0, 15: 0, 20: 0, 25: 0}
        for x in xs:
            got = sold(x, CUR)
            if not got:
                continue
            legs, st, used = got
            tot += 1
            if x.win_tf in st:
                continue
            miss += 1
            o = x.po_tf.get(x.win_tf)
            if o is not None and float(o) < CUR.min_odds:
                cut += 1
                cut_odds.append(float(o))
                for b in bands:
                    bands[b] += float(o) >= b
        print(f"\n===== 型E {label}  入稿ゲート通過 {tot:,}R / {nd}日 =====")
        print(f"  外した（買い目に無かった）        {miss/tot*100:5.1f}%  ({miss:,}R)")
        print(f"  🔴 うち帯30倍未満で切っていた      {cut/tot*100:5.2f}%  ({cut:,}R)"
              f"   ← 型C 29.7 / 26.9%・型F 5.5 / 5.1%")
        if cut_odds:
            cs = sorted(cut_odds)
            print(f"     切った目の予測オッズ  中央 {median(cs):.1f}倍"
                  f"  p25 {cs[len(cs)//4]:.1f} / p75 {cs[len(cs)*3//4]:.1f}")
            print("     下限別に拾えた件数（上限の伸びしろ）:")
            for b in sorted(bands, reverse=True):
                print(f"       ≧{b:2d}倍 → {bands[b]:4,}R  (+{bands[b]/tot*100:5.2f}pt)")


# ───────────────────────── 掃引 ─────────────────────────

FLOORS = (5.0, 8.0, 10.0, 15.0, 20.0, 25.0)


def _rand_arm(x, lo: float, rnd):
    """対照: 帯下から**無作為**に1点。実装と同じ経路（受け皿つき）で組む。"""
    from src.type_lab import allocate, alloc_fallback
    base = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
    plain = sold(x, FB_PLAIN)
    if not base or not plain:
        return plain and rec_of(x, plain[1])
    base = list(base)
    cand = [k for k, v in x.po_tf.items()
            if v and len(set(k)) == 3 and lo <= float(v) < CUR.min_odds and k not in base]
    if not cand:
        return rec_of(x, plain[1])
    legs = [rnd.choice(cand)] + base[:len(base) - 1]
    st = allocate(legs, x.po_tf, x.pr_tf, CUR)
    if not st:
        f2 = alloc_fallback(CUR)
        st = allocate(legs, x.po_tf, x.pr_tf, f2) if f2 else None
    if (not st or mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT
            or min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS):
        return rec_of(x, plain[1])
    return rec_of(x, st)


def floor() -> None:
    for label, win in WIN:
        xs = pop(win)
        nd = C.days_of(C.select(None, win))
        print(f"\n===== 型E {label}  {len(xs):,}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日   発火%  差込点の賭け金/予算 中央/p95/最大")
        with fallback((FB_PLAIN,)):
            cur = {id(x): (rec_of(x, g[1]) if (g := sold(x, FB_PLAIN)) else None)
                   for x in xs}
        s0 = C.summarize([r for r in cur.values() if r], nd)
        print(C.line("現行 帯30倍+14点", s0) + f" {s0.get('big_per_day', 0):9.3f}")
        for lo in FLOORS:
            pl = replace(CUR, underband_min=lo)
            recs, shares, fired, seen = {}, [], 0, 0
            with fallback((FB_PLAIN,)):
                for x in xs:
                    got = sold(x, pl)
                    if not got:
                        continue
                    legs, st, used = got
                    seen += 1
                    under = [c for c in st if float(x.po_tf[c]) < CUR.min_odds]
                    if under:
                        fired += 1
                        shares.append(st[under[0]] / sum(st.values()))
                    recs[id(x)] = rec_of(x, st)
            s = C.summarize(list(recs.values()), nd)
            sh = sorted(shares) or [0.0]
            print(C.line(f"差込 市場1位≧{lo:.0f}倍", s)
                  + f" {s.get('big_per_day', 0):9.3f} {fired/max(seen,1)*100:6.1f}"
                  + f"  {sh[len(sh)//2]*100:5.1f}% {sh[int(len(sh)*.95)]*100:5.1f}%"
                    f" {sh[-1]*100:5.1f}%")
            pr = [(1.0 if cur[k]["pay"] > cur[k]["inv"] else 0.0,
                   1.0 if recs[k]["pay"] > recs[k]["inv"] else 0.0)
                  for k in cur if cur.get(k) and recs.get(k)]
            a = sum(p[0] for p in pr) / len(pr) * 100
            b = sum(p[1] for p in pr) / len(pr) * 100
            l2, h2 = _boot(pr)
            shown = []
            for sd in range(20):
                rnd = random.Random(sd)
                with fallback((FB_PLAIN,)):
                    rr = [r for x in xs if (r := _rand_arm(x, lo, rnd))]
                shown.append(C.summarize(rr, nd)["shown"])
            shown.sort()
            mark = "✅外" if not (shown[0] <= b <= shown[-1]) else "❌範囲内"
            print(f"      Δ表示的中 {b-a:+5.2f}pt 95%CI [{l2:+5.2f}, {h2:+5.2f}]"
                  f"   対照 無作為20seed 中央 {shown[10]:.2f}% 範囲 {shown[0]:.2f}〜{shown[-1]:.2f}"
                  f"  → 市場1位 {b:.2f}% は {mark}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diag"
    {"diag": diag, "floor": floor}[cmd]()
