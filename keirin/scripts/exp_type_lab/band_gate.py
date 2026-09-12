#!/usr/bin/env python3
"""帯（`min_odds`）と入稿ゲート（`MIN_MEAN_PAYOUT`）— 設計意図の切り分けと2次元掃引。

台: `band_gate_build.py` が作る `/tmp/band_gate_rows.pkl`

🔴 **問い（2026-09-10 ユーザー指摘を受けた再定義）**
   帯は「そのレースをどう読んだか」を表現する道具なのか、
   それとも「平均想定払戻2万円の入稿ゲートを通すための辻褄合わせ」なのか。
   外れも2つに分けて数える:
     ② 読みの失敗（型判定の失敗）… その型の読みなら起きないはずの決着だった
     ③ 買い目の失敗            … 読みどおりの帯の中なのに買えていなかった

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/band_gate.py <section>
      base / intent / miss / typing / band / gate / grid / d / lineup
"""
from __future__ import annotations

import importlib.util
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from band_gate_build import VARIANTS  # noqa: E402
from src.type_lab import (  # noqa: E402
    ANA_PW_ENT_MIN, SIGNBOARD_RACE_TYPES, TYPE_F_SELL_BY_RACE_TYPE)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

MIN_POINT_ODDS = 2.0
T0 = 20_000.0
ROWS = pickle.load(open("/tmp/band_gate_rows.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}
WIN = (("確認 2026-01〜08（本番相当）", "confirm"), ("探索 2024-07〜2025-12", "explore"))
#: 現行の構成
CUR = {"A_hit": "A_hit", "A_trio": "A_trio", "A_ana": "A_ana",
       "B_hit": "B@30k", "C_hit": "C@15", "D_hit": "D@drop3",
       "E_hit": "E@30", "F_hit": "F@5", "F_pay": "F_pay", "F_sign": "F_sign"}
#: 設計上「当てにいかない」商品
BY_DESIGN = {"A_ana", "F_sign"}
BAND_OF = {v: VARIANTS[v][0].min_odds for v in VARIANTS}


def slot_of(r: dict, trio_ok: bool) -> str:
    t, rt = r["type"], r["rtype"]
    if t == "F":
        if rt in SIGNBOARD_RACE_TYPES:
            return "F_sign"
        return TYPE_F_SELL_BY_RACE_TYPE.get(rt, "F_hit")
    if t == "A":
        if r["pw_ent"] >= ANA_PW_ENT_MIN:
            return "A_ana"
        return "A_trio" if trio_ok else "A_hit"
    return f"{t}_hit"


def resolve(chain, T: float):
    """`build_with_gate_fallback` の選択を T のもとで再現する。"""
    if not chain:
        return None
    first = chain[0]
    if first is not None and first["mean"] > T:
        pick = first
    else:
        pick = next((c for c in chain[1:] if c is not None and c["mean"] > T), first)
    if pick is None:
        return None
    for sw in pick.get("swaps", ()):
        if sw["mean"] > T:
            return sw
    return pick


def run(rows, cfg: dict, T: float = T0):
    """売れた商品の一覧を返す。各要素は dict（row の情報 + 買い目の結果）。"""
    out = []
    for r in rows:
        ch = r["chains"]
        a = resolve(ch.get("A_trio"), T) if r["type"] == "A" else None
        trio_ok = bool(a and a["mean"] > T and a["minodds"] >= MIN_POINT_ODDS)
        slot = slot_of(r, trio_ok)
        v = cfg.get(slot)
        res = resolve(ch.get(v), T) if v else None
        if not res:
            continue
        if r["axis_sum"] < AXIS_GATE_MIN.get(slot, 0.0):
            continue
        if res["mean"] <= T or res["minodds"] < MIN_POINT_ODDS:
            continue
        out.append(dict(r, slot=slot, variant=v, band=BAND_OF.get(v, 0.0), **{
            k: res[k] for k in ("k", "inv", "pay", "mean", "hit", "legs")}))
    return out


def agg(sel, nd):
    if not sel:
        return dict(n=0)
    inv = sum(s["inv"] for s in sel)
    pay = sum(s["pay"] for s in sel)
    pays = sorted(s["pay"] for s in sel if s["pay"] > 0)
    shown = [s for s in sel if s["pay"] >= s["inv"]]
    hits = [s for s in sel if s["pay"] > 0]
    return dict(n=len(sel), perday=len(sel) / nd,
                k=float(np.mean([s["k"] for s in sel])),
                inv_day=inv / nd,
                hit=len(hits) / len(sel) * 100,
                gami=(len(hits) - len(shown)) / len(hits) * 100 if hits else 0.0,
                shown=len(shown) / len(sel) * 100,
                med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd,
                roi=pay / inv * 100 if inv else 0.0)


def nd_of(rows):
    return len({r["date"] for r in rows})


def boot(a, b, iters=1000, seed=0):
    """対応比較（同じレース）の Δ表示的中 / ΔROI の 95%CI。"""
    rng = np.random.default_rng(seed)
    A = np.array([x["pay"] >= x["inv"] for x in a], float)
    B = np.array([x["pay"] >= x["inv"] for x in b], float)
    ap = np.array([x["pay"] for x in a]); ai = np.array([x["inv"] for x in a])
    bp = np.array([x["pay"] for x in b]); bi = np.array([x["inv"] for x in b])
    n = len(A); ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((A[j].mean() - B[j].mean()) * 100)
        dr.append(ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100)
    f = lambda v: (float(np.mean(v)), float(np.percentile(v, 2.5)),
                   float(np.percentile(v, 97.5)))
    return f(ds), f(dr)


HDR = (f"  {'腕':30s} {'件/日':>6s} {'点数':>5s} {'的中%':>6s} {'ガミ%':>6s} "
       f"{'表示的中%':>9s} {'払戻中央':>9s} {'10万+/日':>8s} {'投資/日':>8s} {'ROI%':>6s}")


def pr(name, s):
    if not s.get("n"):
        print(f"  {name:30s}  (該当なし)")
        return
    print(f"  {name:30s} {s['perday']:6.2f} {s['k']:5.1f} {s['hit']:6.2f} {s['gami']:6.2f}"
          f" {s['shown']:9.2f} {s['med']:9,.0f} {s['big']:8.3f} {s['inv_day']:8,.0f}"
          f" {s['roi']:6.1f}")


# ═════════════════════════ base ═════════════════════════

def base():
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        sel = run(rows, CUR)
        print(f"\n=== {lab}  {nd}日  対象 {len(rows):,}R ===")
        print(HDR)
        pr("現行ラインナップ", agg(sel, nd))
        cnt = {}
        for s in sel:
            cnt[s["slot"]] = cnt.get(s["slot"], 0) + 1
        print("   内訳:", "  ".join(f"{k} {v:,}" for k, v in sorted(cnt.items())))
        for slot in sorted(cnt):
            pr("   " + slot, agg([s for s in sel if s["slot"] == slot], nd))


# ═════════════════════════ intent ═════════════════════════
#: 帯なしの対応 variant（モデルが素で何を1位に置くかを見るため）
NOBAND = {"C_hit": "C@0", "E_hit": "E@0", "F_hit": "F@0"}


def intent():
    """帯は「読みの表現」か「入稿ゲートの辻褄合わせ」か。"""
    print("\n── (1) 算術: ダッチでも conf(床2.0倍) でも、組める条件は Σ(1/予測オッズ) ≤ 予算/T。")
    print("   k 点を積むには『予測オッズの調和平均 ≥ k×T/予算』が要る（T=20,000 / 予算10,000）。")
    print(f"\n  {'プラン':8s} {'点数k':>5s} {'ゲートが要求する調和平均オッズ':>28s} {'実際の帯':>10s} {'差':>8s}")
    for key, k, b in (("C_hit", 12, 15.0), ("E_hit", 14, 30.0),
                      ("F_hit", 12, 5.0), ("B_hit", 8, 0.0)):
        need = k * T0 / 10_000
        print(f"  {key:8s} {k:5d} {need:28.1f} {b:10.1f} {b-need:+8.1f}")
    print("   ※ B_hit は帯を持たず `sigma_max=1/3.0`（＝計画払戻3万円）で直接 Σ を縛る。")

    for lab, w in WIN:
        rows = W[w]
        sel_all = run(rows, CUR)
        print(f"\n=== {lab} ===")
        print(f"  {'プラン':8s} {'n':>6s} {'帯L':>5s} {'モデル確率1位が帯外':>18s}"
              f" {'◎○△本線が帯外':>16s} {'決着が帯の下':>13s} {'決着が帯の下&軸2そろい':>21s}")
        for slot, v in (("C_hit", "C@15"), ("E_hit", "E@30"), ("F_hit", "F@5")):
            sel = [s for s in sel_all if s["slot"] == slot]
            if not sel:
                continue
            L = BAND_OF[v]
            nb = NOBAND[slot]
            top_out = []
            for s in sel:
                ch = s["chains"].get(nb)
                r0 = ch[0] if ch else None
                if not r0 or not r0["legs"]:
                    continue
                top_out.append(r0["legs"][0] not in set(s["legs"]))
            m3out = [np.isfinite(s["po_m3_tf"]) and s["po_m3_tf"] < L
                     for s in sel if s["m3"]]
            below = [np.isfinite(s["po_win"]) and s["po_win"] < L for s in sel]
            below_ax = [b for b, s in zip(below, sel) if s["axis_both"]]
            print(f"  {slot:8s} {len(sel):6,} {L:5.0f} {np.mean(top_out)*100:17.1f}%"
                  f" {np.mean(m3out)*100:15.1f}% {np.mean(below)*100:12.1f}%"
                  f" {np.mean(below_ax)*100:20.1f}%")


# ═════════════════════════ miss ═════════════════════════

def classify(s):
    """1商品の結果を排他4分類。

    ① hit         的中
    ② read_axis   軸崩壊（型の前提そのものが崩れた＝読みの失敗）
    ③ read_band   決着が**帯の下**（＝この型を「荒れる」と読んだのに本命側で決まった
                  ＝型判定の失敗。買い目をいじっても、読みが正しいなら買ってはいけない目）
    ④ legs        決着は帯の中なのに買えていない（＝買い目の失敗）
    """
    if s["pay"] > 0 or s["hit"]:
        return "hit"
    if not s["axis_both"]:
        return "read_axis"
    L = s["band"]
    if L > 0 and np.isfinite(s["po_win"]) and s["po_win"] < L:
        return "read_band"
    # 🔴 買い目の失敗をさらに2つへ割る。**予算制約が落としたのか、モデルが
    #    読めていないのか**は打ち手が正反対なので、まとめて数えてはいけない。
    #    ④a: モデルは決着の目を「買う点数 k 位以内」に置いていた（＝Σ制約・
    #        `drop_fav`・帯の下限が落とした）→ 設計の破れ
    #    ④b: モデルの確率順位でも k 位より下 → モデルの限界
    if s["k"] and s["pr_rank_win"] <= s["k"]:
        return "legs_budget"
    return "legs_model"


def miss():
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        sel = run(rows, CUR)
        print(f"\n=== {lab}  {nd}日 ===")
        print(f"  {'商品':10s} {'n':>6s} {'①的中':>7s} {'②軸崩壊':>8s} {'③帯下決着':>10s}"
              f" {'④a予算制約':>10s} {'④bモデル':>9s}   {'◎○△決着':>9s} {'うち取りこぼし':>13s}"
              f" {'取こぼ内訳(②/③/④a/④b)':>26s}")
        order = ["A_hit", "A_trio", "A_ana", "B_hit", "C_hit", "D_hit",
                 "E_hit", "F_hit", "F_pay", "F_sign"]
        for slot in order + ["__ALL__"]:
            ss = sel if slot == "__ALL__" else [s for s in sel if s["slot"] == slot]
            if not ss:
                continue
            cls = [classify(s) for s in ss]
            n = len(ss)
            f = lambda t: sum(1 for c in cls if c == t) / n * 100
            m3 = [s for s in ss if s["m3_win"]]
            m3miss = [s for s in m3 if not (s["pay"] > 0 or s["hit"])]
            mc = [classify(s) for s in m3miss]
            g = lambda t: (sum(1 for c in mc if c == t) / len(m3miss) * 100
                           if m3miss else 0.0)
            print(f"  {('全体' if slot=='__ALL__' else slot):10s} {n:6,} {f('hit'):6.2f}%"
                  f" {f('read_axis'):7.2f}% {f('read_band'):9.2f}%"
                  f" {f('legs_budget'):9.2f}% {f('legs_model'):8.2f}%"
                  f"   {len(m3)/n*100:8.2f}%"
                  f" {(len(m3miss)/len(m3)*100 if m3 else 0):12.2f}%"
                  f"   {g('read_axis'):4.1f}/{g('read_band'):4.1f}/"
                  f"{g('legs_budget'):4.1f}/{g('legs_model'):4.1f}")


# ═════════════════════════ typing ═════════════════════════

def typing():
    """型判定そのものは◎○△決着を分離できているか / 型の中で更に分けられるか。"""
    for lab, w in WIN:
        rows = W[w]
        print(f"\n=== {lab}  型別の『実際に本命で決まった率』 ===")
        print(f"  {'型':4s} {'n':>6s} {'◎○△決着':>9s} {'決着<10倍':>9s} {'決着<30倍':>9s}"
              f" {'軸2そろい':>9s} {'決着の予測オッズ中央':>20s} {'q_m3中央':>9s}")
        for t in "ABCDEF":
            ss = [r for r in rows if r["type"] == t]
            if not ss:
                continue
            po = [r["po_win"] for r in ss if np.isfinite(r["po_win"])]
            print(f"  {t:4s} {len(ss):6,} {np.mean([r['m3_win'] for r in ss])*100:8.2f}%"
                  f" {np.mean([p < 10 for p in po])*100:8.2f}%"
                  f" {np.mean([p < 30 for p in po])*100:8.2f}%"
                  f" {np.mean([r['axis_both'] for r in ss])*100:8.2f}%"
                  f" {np.median(po):20.1f} {np.median([r['q_m3'] for r in ss]):9.4f}")
        print(f"\n  ── 型E・型C の中を q_m3（◎○△で決まるモデル確率）五分位で割る ──")
        for t in ("C", "E", "F"):
            ss = [r for r in rows if r["type"] == t]
            if len(ss) < 100:
                continue
            q = np.array([r["q_m3"] for r in ss])
            cut = np.percentile(q, [20, 40, 60, 80])
            print(f"   型{t}  {'Q1':>8s} {'Q2':>8s} {'Q3':>8s} {'Q4':>8s} {'Q5':>8s}")
            row_m3, row_lo = [], []
            for b in range(5):
                lo = -1 if b == 0 else cut[b - 1]
                hi = 1e9 if b == 4 else cut[b]
                g = [r for r in ss if lo <= r["q_m3"] < hi or (b == 4 and r["q_m3"] >= lo)]
                g = [r for r in ss if (r["q_m3"] >= lo if b else True) and (r["q_m3"] < hi)]
                row_m3.append(np.mean([r["m3_win"] for r in g]) * 100 if g else 0)
                po = [r["po_win"] for r in g if np.isfinite(r["po_win"])]
                row_lo.append(np.mean([p < 30 for p in po]) * 100 if po else 0)
            print("        ◎○△決着% " + " ".join(f"{v:7.2f}" for v in row_m3))
            print("        決着<30倍% " + " ".join(f"{v:7.2f}" for v in row_lo))


# ═════════════════════════ band / gate / grid ═════════════════════════

BANDS = {"C_hit": ["C@0", "C@5", "C@8", "C@10", "C@12", "C@15", "C@20"],
         "E_hit": ["E@0", "E@5", "E@10", "E@15", "E@20", "E@25", "E@30"],
         "F_hit": ["F@0", "F@3", "F@5", "F@8", "F@10", "F@15"],
         "B_hit": ["B@15k", "B@20k", "B@25k", "B@30k", "B@35k"]}


def band():
    """帯（と B_hit の Σ床）を1つずつ動かす。ゲートは 20,000 のまま。"""
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        base_sel = run(rows, CUR)
        print(f"\n=== {lab}  {nd}日 ===")
        for slot, vs in BANDS.items():
            print(f"\n  ── {slot} だけ動かす（他は現行・T=20,000）──")
            print(HDR + "   Δ表示的中 95%CI(同一レース)")
            b_only = [s for s in base_sel if s["slot"] == slot]
            pr(f"[{slot}のみ] 現行", agg(b_only, nd))
            for v in vs:
                cfg = dict(CUR, **{slot: v})
                sel = run(rows, cfg)
                only = [s for s in sel if s["slot"] == slot]
                # 同一レース対応比較
                ka = {s["date"] + str(s["win_tf"]) + s["rtype"]: s for s in only}
                kb = {s["date"] + str(s["win_tf"]) + s["rtype"]: s for s in b_only}
                common = sorted(set(ka) & set(kb))
                if common:
                    (ds, dl, dh), _ = boot([ka[k] for k in common],
                                           [kb[k] for k in common])
                    ci = f"  {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}]  n={len(common):,}"
                else:
                    ci = ""
                s = agg(only, nd)
                mark = " *" if v == CUR[slot] else "  "
                pr(f"{mark}{v}", s)
                if ci:
                    print(" " * 32 + ci)


GATES = (12_000.0, 15_000.0, 18_000.0, 20_000.0, 25_000.0)


def gate():
    """入稿ゲート `MIN_MEAN_PAYOUT` の掃引（7車・ラインナップ全体）。"""
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        print(f"\n=== {lab}  {nd}日 ===")
        print(HDR + "  ◎○△取こぼ  日次表示的中(中央/p25/p75) 0件日%")
        for T in GATES:
            sel = run(rows, CUR, T)
            s = agg(sel, nd)
            m3 = [x for x in sel if x["m3_win"]]
            miss3 = (sum(1 for x in m3 if not (x["pay"] > 0 or x["hit"]))
                     / len(m3) * 100 if m3 else 0.0)
            byday = {}
            for x in sel:
                byday.setdefault(x["date"], []).append(x)
            dr = [np.mean([y["pay"] >= y["inv"] for y in v]) * 100
                  for v in byday.values()]
            zero = np.mean([d == 0 for d in dr]) * 100 if dr else 0.0
            pr(("* " if T == T0 else "  ") + f"T={T:,.0f}", s)
            print(" " * 32 + f"{miss3:8.1f}%   {np.median(dr):6.1f} /"
                  f" {np.percentile(dr,25):5.1f} / {np.percentile(dr,75):5.1f}"
                  f"   {zero:5.1f}%")


def grid():
    """帯 × ゲートの2次元。表示的中 / 件·日 / ROI / 10万+ を等高線で。"""
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        print(f"\n=== {lab}  {nd}日 ===")
        for slot, vs in (("C_hit", BANDS["C_hit"]), ("E_hit", BANDS["E_hit"]),
                         ("F_hit", BANDS["F_hit"])):
            cache = {}
            for v in vs:
                for T in GATES:
                    sel = [x for x in run(rows, dict(CUR, **{slot: v}), T)
                           if x["slot"] == slot]
                    cache[(v, T)] = agg(sel, nd)
            for metric, fmt in (("shown", "{:7.2f}"), ("perday", "{:7.2f}"),
                                ("roi", "{:7.1f}"), ("med", "{:7,.0f}")):
                print(f"\n  ── {slot} × ゲート : "
                      f"{ {'shown':'表示的中%','perday':'件/日','roi':'ROI%','med':'払戻中央'}[metric] }"
                      f"（{slot} だけ・他は現行）")
                print("     " + "".join(f"{('T='+format(int(T),',')):>10s}" for T in GATES))
                for v in vs:
                    cells = []
                    for T in GATES:
                        a = cache[(v, T)]
                        cells.append(fmt.format(a[metric]) if a.get("n") else "     -")
                    mk = "*" if v == CUR[slot] else " "
                    print(f"  {mk}{v:8s}" + "".join(f"{c:>10s}" for c in cells))


def d():
    """D_hit の `axis2_drop_fav`（最人気の相手を1点落とす）の是非。"""
    vs = ["D@drop3", "D@drop4", "D@flow2", "D@flow3", "D@flow4", "D@flow5"]
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        print(f"\n=== {lab}  {nd}日 ===")
        for T in (20_000.0, 15_000.0):
            print(f"\n  ── T={T:,.0f} ──")
            print(HDR + "   Δ表示的中 95%CI(同一レース) / ◎○△取こぼ")
            b_only = [s for s in run(rows, CUR, T) if s["slot"] == "D_hit"]
            for v in vs:
                sel = [s for s in run(rows, dict(CUR, D_hit=v), T)
                       if s["slot"] == "D_hit"]
                ka = {s["date"] + str(s["win_tf"]) + s["rtype"]: s for s in sel}
                kb = {s["date"] + str(s["win_tf"]) + s["rtype"]: s for s in b_only}
                common = sorted(set(ka) & set(kb))
                ci = ""
                if common:
                    (ds, dl, dh), _ = boot([ka[k] for k in common],
                                           [kb[k] for k in common])
                    ci = f"  {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}]"
                m3 = [x for x in sel if x["m3_win"]]
                miss3 = (sum(1 for x in m3 if not (x["pay"] > 0 or x["hit"]))
                         / len(m3) * 100 if m3 else 0.0)
                pr(("* " if v == CUR["D_hit"] else "  ") + v, agg(sel, nd))
                print(" " * 32 + ci + f"   ◎○△取こぼ {miss3:5.1f}%")


def lineup():
    """『本命も買う』構成案と現行の取引条件。

    🔴 件数が増える腕は**増えた分だけを切り出して**（marginal）別に評価する。
       無作為対照は「同数を捨てる」腕にしか意味が無い（増える側は選択が無い）。
    """
    cases = {
        "⓪ 現行": (CUR, 20_000.0),
        "① ゲートだけ 15,000": (CUR, 15_000.0),
        "② 帯だけ下げ(C10/E15/F3)": (dict(CUR, C_hit="C@10", E_hit="E@15",
                                           F_hit="F@3"), 20_000.0),
        "③ 対角(C8/E10/F3 + T15k)": (dict(CUR, C_hit="C@8", E_hit="E@10",
                                           F_hit="F@3"), 15_000.0),
        "④ 在庫不変の小修正(B25k/D4/Eub)": (dict(CUR, B_hit="B@25k",
                                                 D_hit="D@drop4",
                                                 E_hit="E@30ub"), 20_000.0),
        "⑤ ④ + ゲート18,000": (dict(CUR, B_hit="B@25k", D_hit="D@drop4",
                                     E_hit="E@30ub"), 18_000.0),
        "⑥ 対角フル + T15k": (dict(CUR, B_hit="B@25k", C_hit="C@8",
                                    D_hit="D@flow4", E_hit="E@10",
                                    F_hit="F@3"), 15_000.0),
    }
    key = lambda x: x["date"] + str(x["win_tf"]) + x["rtype"] + x["slot"]
    for lab, w in WIN:
        rows = W[w]; nd = nd_of(rows)
        print(f"\n=== {lab}  {nd}日 ===")
        print(HDR + "  ◎○△取こぼ ③帯下% ④a予算% ④bモデル%")
        b = run(rows, CUR, 20_000.0)
        kb = {key(x): x for x in b}
        for name, (cfg, T) in cases.items():
            sel = run(rows, cfg, T)
            s = agg(sel, nd)
            m3 = [x for x in sel if x["m3_win"]]
            miss3 = (sum(1 for x in m3 if not (x["pay"] > 0 or x["hit"]))
                     / len(m3) * 100 if m3 else 0.0)
            cls = [classify(x) for x in sel]
            f = lambda t: sum(1 for c in cls if c == t) / len(cls) * 100
            pr(name, s)
            print(" " * 32 + f"{miss3:8.1f}% {f('read_band'):6.2f}%"
                  f" {f('legs_budget'):6.2f}% {f('legs_model'):6.2f}%")
        print("\n  ── 現行と両方が売れたレースでの 維持/破壊/救済 と、増えた分(marginal) ──")
        for name, (cfg, T) in list(cases.items())[1:]:
            sel = run(rows, cfg, T)
            ka = {key(x): x for x in sel}
            common = sorted(set(ka) & set(kb))
            keep = brk = sav = 0
            for k in common:
                hx = ka[k]["pay"] >= ka[k]["inv"]
                hy = kb[k]["pay"] >= kb[k]["inv"]
                keep += hx and hy; brk += hy and not hx; sav += hx and not hy
            (ds, dl, dh), (rs, rl, rh) = boot([ka[k] for k in common],
                                              [kb[k] for k in common])
            extra = [ka[k] for k in set(ka) - set(kb)]
            e = agg(extra, nd)
            es = (f"増分 {e['perday']:5.2f}件/日 表示的中 {e['shown']:5.2f}% "
                  f"ROI {e['roi']:5.1f} 払戻中央 {e['med']:,.0f}") if e.get("n") else "増分なし"
            print(f"  {name:32s} n={len(common):6,} 維持{keep:5,} 破壊{brk:4,} 救済{sav:4,}"
                  f"  Δ表示 {ds:+5.2f}[{dl:+5.2f},{dh:+5.2f}]"
                  f"  ΔROI {rs:+5.1f}[{rl:+5.1f},{rh:+5.1f}]")
            print(" " * 34 + es)


def retype():
    """『型判定の失敗』を型判定側で直せるか — 型の中を q_m3 で割って帯を変える。

    🔴 これは買い目の修正ではなく**読みの細分化**。同じ件数を無作為に割り当てた
       対照（20 seed・中央値）に勝つかを必ず見る（`race_filter_2026_08_27.md` の作法）。
    """
    rng0 = np.random.default_rng(7)
    for slot, hi_v, lo_v in (("E_hit", "E@10", "E@30"), ("C_hit", "C@8", "C@15")):
        thr = float(np.percentile(
            [r["q_m3"] for r in W["explore"] if r["type"] == slot[0]], 80))
        print(f"\n── {slot}: q_m3 の探索窓 p80 = {thr:.4f} を超えたら {hi_v}、"
              f"それ以外は {lo_v} ──")
        for lab, w in WIN:
            rows = W[w]; nd = nd_of(rows)
            base = {}
            for v in (hi_v, lo_v):
                for x in run(rows, dict(CUR, **{slot: v})):
                    if x["slot"] == slot:
                        base.setdefault(v, {})[
                            x["date"] + str(x["win_tf"]) + x["rtype"]] = x
            keys = sorted(set(base.get(hi_v, {})) | set(base.get(lo_v, {})))
            qs = {}
            for v in (hi_v, lo_v):
                for k, x in base.get(v, {}).items():
                    qs[k] = x["q_m3"]
            hi_keys = {k for k in keys if qs.get(k, 0.0) >= thr}
            def build(sel_hi):
                out = []
                for k in keys:
                    v = hi_v if k in sel_hi else lo_v
                    x = base.get(v, {}).get(k) or base.get(
                        hi_v if v == lo_v else lo_v, {}).get(k)
                    if x:
                        out.append(x)
                return out
            arm = build(hi_keys)
            print(f"  [{lab}]")
            print(HDR)
            pr(f"  現行（全部 {lo_v}）", agg(list(base.get(lo_v, {}).values()), nd))
            pr(f"  一律 {hi_v}", agg(list(base.get(hi_v, {}).values()), nd))
            pr(f"  q_m3 上位20%だけ {hi_v}", agg(arm, nd))
            ctrl = []
            for sd in range(20):
                rng = np.random.default_rng(sd)
                pick = set(rng.choice(np.array(keys, dtype=object),
                                      size=len(hi_keys), replace=False))
                ctrl.append(agg(build(pick), nd))
            cs = sorted(c["shown"] for c in ctrl)
            win_n = sum(1 for c in ctrl if agg(arm, nd)["shown"] > c["shown"])
            print(f"    無作為対照20seed 表示的中 中央 {cs[10]:.2f}% "
                  f"（範囲 {cs[0]:.2f}〜{cs[-1]:.2f}） 勝ち {win_n}/20")


if __name__ == "__main__":
    fn = {"base": base, "intent": intent, "miss": miss, "typing": typing,
          "band": band, "gate": gate, "grid": grid, "d": d, "lineup": lineup, "retype": retype}
    for a in (sys.argv[1:] or ["base"]):
        print("\n" + "=" * 130)
        print(f"##### {a}")
        print("=" * 130)
        fn[a]()
