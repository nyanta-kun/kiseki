#!/usr/bin/env python3
"""機会費用 — 置き換え範囲を複数測る + 無作為対照20seed。

範囲:
  all              売る分を全部置き換える
  typeBCD/typeA/typeF   型を限る
  L:<層>           その層の売る分だけ置き換える（③選別との掛け合わせ）
  axisdrop         軸信頼ゲートに落ちて今は売っていないレース（**純増**）
  gatedrop         入稿ゲートに落ちて今は売っていないレース（純増）
  capdrop          日次上限で捨てるレース（型B/C/D・高額枠の母集団・純増）
  ana              `A_ana` が既に取っている範囲（型A × pw_ent 上位10%）
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import lib as L

rows, thr = L.load()
ANA_PW_ENT_MIN = 1.4076
ONESHOT = ("A_ana", "F_sign")


def sold(r) -> bool:
    return bool(r["base"]["gate"] and r["base"]["axis_ok"])


# ── 日次上限（判定対象 × 0.5・cap_priority 降順・決勝/GIII+ は枠外）──
def _cap_flags():
    out = {}
    for win in ("explore", "confirm"):
        by = {}
        for r in rows:
            if r["win"] != win:
                continue
            by.setdefault(r["date"], []).append(r)
        for d, rs in by.items():
            n = len(rs)
            cap = int(n * 0.5)
            ex = [r for r in rs if ("決勝" in r["rtype"])
                  or (r["cupg"].isdigit() and int(r["cupg"]) >= 3)]
            rest = sorted((r for r in rs if r not in ex),
                          key=lambda r: -r["base"]["capp"])
            keep = set(id(r) for r in ex) | set(id(r) for r in rest[:cap])
            for r in rs:
                out[(r["win"], r["key"])] = id(r) in keep
    return out


CAPKEEP = _cap_flags()


def scope(r, name: str) -> bool:
    if name == "all":
        return sold(r)
    if name.startswith("type"):
        return sold(r) and r["type"] in name[4:]
    if name.startswith("L:"):
        return sold(r) and L.inlay(r, name[2:])
    if name == "ana":
        return sold(r) and r["type"] == "A" and r["pw_ent"] >= ANA_PW_ENT_MIN
    if name == "axisdrop":
        return bool(r["base"]["gate"] and not r["base"]["axis_ok"])
    if name == "gatedrop":
        return bool(not r["base"]["gate"])
    if name == "capdrop":
        return bool(sold(r) and r["type"] in "BCD"
                    and not CAPKEEP[(r["win"], r["key"])])
    raise KeyError(name)


def overview():
    for win in ("explore", "confirm"):
        rs = [r for r in rows if r["win"] == win]
        nd = L.ndays(win)
        print(f"\n=== [{win}] n={len(rs):,}R 日数={nd} — 現行ラインナップ ===")
        print(L.HEAD)
        for tag, f in (
                ("現行 全体(売る分)", sold),
                ("  うち 当てにいく", lambda r: sold(r) and r["base"]["plan"] not in ONESHOT),
                ("  うち 一撃", lambda r: sold(r) and r["base"]["plan"] in ONESHOT),
                ("軸信頼ゲート落ち(未売)", lambda r: scope(r, "axisdrop")),
                ("入稿ゲート落ち(未売)", lambda r: scope(r, "gatedrop")),
                ("日次上限落ち BCD(未売)", lambda r: scope(r, "capdrop")),
        ):
            print(L.line(tag, L.summ([r["base"] for r in rs if f(r)], nd)))
        inv = sum(r["base"]["inv"] for r in rs if sold(r)) / nd
        print(f"  投資/日 {inv:,.0f}円")
        for nm in ("axis_sum_lo25", "pw_gap12_lo25", "pw_max_lo25", "pw_ent_hi10", "dis"):
            s = [r for r in rs if sold(r) and L.inlay(r, nm)]
            a = [r for r in rs if scope(r, "axisdrop") and L.inlay(r, nm)]
            print(f"  層 {nm:15s} 売る分に {len(s)/max(sum(1 for r in rs if sold(r)),1)*100:5.1f}% "
                  f"({len(s)/nd:5.2f}件/日) / 軸ゲート落ちに {len(a)/nd:5.2f}件/日 / "
                  f"軸1圏外率 {np.mean([r['a1_out'] for r in s])*100 if s else 0:5.1f}%")


def lineup(scope_name: str, arms: list[str], seeds: int = 20):
    """ラインナップ全体での増減 + 層を無作為に置き換えた対照 20seed。"""
    add = scope_name in ("axisdrop", "gatedrop", "capdrop")
    for win in ("explore", "confirm"):
        rs = [r for r in rows if r["win"] == win]
        nd = L.ndays(win)
        cur = [r["base"] for r in rs if sold(r)]
        tgt = [r for r in rs if scope(r, scope_name)]
        print(f"\n=== [{win}] ラインナップ全体  範囲={scope_name} "
              f"({len(tgt):,}R = {len(tgt)/nd:.2f}件/日) ===")
        print(L.HEAD)
        s0 = L.summ(cur, nd)
        print(L.line("現行", s0))
        pool = [r for r in rs if sold(r)] if not add else None
        for a in arms:
            new = []
            for r in rs:
                if scope(r, scope_name) and a in r["arms"] and r["arms"][a]["gate"]:
                    if add:
                        if sold(r):
                            new.append(r["base"])
                        new.append(r["arms"][a])
                    else:
                        new.append(r["arms"][a])
                elif sold(r):
                    new.append(r["base"])
            s = L.summ(new, nd)
            print(L.line(f"{a}{'(純増)' if add else '(置換)'}", s)
                  + f"  投資/日 {s['inv_day']:,.0f}")
            # ── 無作為対照: 同数の売る分を無作為に選んで同じ腕へ置換 ──
            if not add and scope_name.startswith("L:"):
                k = len(tgt)
                wins_shown = wins_big = 0
                vals = []
                for sd in range(seeds):
                    rng = np.random.default_rng(9000 + sd)
                    pick = set(rng.choice(len(pool), min(k, len(pool)),
                                          replace=False).tolist())
                    nw = []
                    for j, r in enumerate(pool):
                        if j in pick and a in r["arms"] and r["arms"][a]["gate"]:
                            nw.append(r["arms"][a])
                        else:
                            nw.append(r["base"])
                    ss = L.summ(nw, nd)
                    vals.append((ss["shown"], ss["big"]))
                    wins_shown += int(s["shown"] > ss["shown"])
                    wins_big += int(s["big"] > ss["big"])
                sh = sorted(v[0] for v in vals); bg = sorted(v[1] for v in vals)
                print(f"      無作為対照20seed 表示的中 中央 {sh[10]:.2f} "
                      f"({sh[0]:.2f}〜{sh[-1]:.2f}) → 層版 {s['shown']:.2f} "
                      f"= {wins_shown}/20 勝ち  |  10万+/日 中央 {bg[10]:.3f} "
                      f"({bg[0]:.3f}〜{bg[-1]:.3f}) → {s['big']:.3f} = {wins_big}/20")


def scope_cmp(scope_name: str, arms: list[str]):
    """その範囲だけの同一レース対比較（基準は現行 base）。"""
    for win in ("explore", "confirm"):
        rs = [r for r in rows if r["win"] == win and scope(r, scope_name)]
        nd = L.ndays(win)
        if len(rs) < 50:
            print(f"[{win}] {scope_name} n不足 {len(rs)}")
            continue
        print(f"\n=== [{win}] 範囲={scope_name} n={len(rs):,}R ===")
        print(L.HEAD)
        print(L.line("現行(この範囲)", L.summ([r["base"] for r in rs], nd)))
        for a in arms:
            pr = [(r["base"], r["arms"][a]) for r in rs
                  if a in r["arms"] and r["arms"][a]["gate"]]
            if len(pr) < 40:
                print(f"  {a:20s} (n={len(pr)} 不足)")
                continue
            b = [x[0] for x in pr]; ar = [x[1] for x in pr]
            print(L.line(a, L.summ(ar, nd)))
            ds, lo, hi = L.paired(L.shown_vec(b), L.shown_vec(ar))
            db, blo, bhi = L.paired(L.big_vec(b), L.big_vec(ar))
            d3, l3, h3 = L.paired(L.big_vec(b, 300_000), L.big_vec(ar, 300_000))
            dr, rlo, rhi = L.roi_pair(b, ar)
            print(f"      Δ表示的中 {ds:+6.2f} [{lo:+6.2f},{hi:+6.2f}]"
                  f"  Δ10万+率 {db:+5.2f} [{blo:+5.2f},{bhi:+5.2f}]"
                  f"  Δ30万+率 {d3:+5.2f} [{l3:+5.2f},{h3:+5.2f}]"
                  f"  ΔROI {dr:+6.2f} [{rlo:+6.2f},{rhi:+6.2f}]  (n={len(pr)})")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "overview":
        overview()
    elif cmd == "lineup":
        lineup(sys.argv[2], sys.argv[3].split(","))
    elif cmd == "scope":
        scope_cmp(sys.argv[2], sys.argv[3].split(","))
