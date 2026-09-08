#!/usr/bin/env python3
"""型C（C_hit）の帯 15倍下限を下げる / 順当な目を差し込む（2026-09-08・ユーザー提案）。

発端: 2026-09-08 大垣7R（型C・C_hit 12点）。決着 7-3-5 は**モデルの3着内率
      上位3車がそのまま順当に決まった目**で、確定 6.0倍が付いていたが
      帯15倍に切られて買い目に無かった。
      「順当でも一定以上のオッズが付くなら買い目に含めてよいのでは？」

作法: `common.py` / `typef_racetype.py` と同じ。
  - 本番の関数（`build_legs` / `_build_plan`）で組む
  - 入稿ゲート2段（1点でも予測<2.0倍 / 平均想定払戻<=2万円）を通してから比べる
  - 採否は確認窓(2026)。予測オッズモデル train_end 2025-12-31 のため探索窓は in-sample
  - ROI では採否を決めない（表示的中で見る）。C_hit は軸信頼ゲート対象外

  セクション: diag（伸びしろ）/ arms（帯と差込の腕）/ ctrl（対照とCI）/
             band（帯を下げてゲート落ちは現行）/ final（採用候補）/
             verify（本番の関数で再現するか）/ floor（差込の下限の掃引）/
             minpay（差込点が当たるといくら返るか）

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_c_lowband.py diag
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import common as C  # noqa: E402
from typef_racetype import ctx  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, Plan, _build_plan, mean_expected_payout, build_legs)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
WIN = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))
CUR = PLANS["C_hit"]


def _plan(min_odds: float, max_legs: int = 12) -> Plan:
    # key は C_hit にする（`alloc_fallback` が本番と同じに効くようにするため）
    return Plan("C_hit", "C", "trifecta", "prob_top", 0, min_odds=min_odds,
                max_legs=max_legs, alloc="conf", floor_mult=CUR.floor_mult)


# ───────────────────────── 診断 ─────────────────────────

def diag() -> None:
    """帯15倍に切られた目がどれくらい決着しているか。"""
    for label, win in WIN:
        idx = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        n = tot = 0
        cut_win = cut_win_top1 = cut_win_top3 = 0
        bands = {6: 0, 8: 0, 10: 0, 12: 0, 15: 0}
        pos_of_win: list[int] = []
        cut_odds: list[float] = []
        for i in idx:
            x = ctx(int(i))
            if x is None:
                continue
            got = _build_plan(x.shape, CUR, x.po_tf, x.pr_tf)
            if not got:
                continue
            legs, st = got
            if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
                continue
            if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
                continue
            tot += 1
            w = x.win_tf
            if w in st:
                continue                       # 買えていた
            o = x.po_tf.get(w)
            if o is None:
                continue
            n += 1
            if float(o) < CUR.min_odds:        # 帯に切られて外した
                cut_win += 1
                cut_odds.append(float(o))
                # その目はモデルの確率で何位だったか
                rank = sorted(x.pr_tf, key=lambda k: -x.pr_tf[k]).index(w) + 1
                pos_of_win.append(rank)
                cut_win_top1 += rank == 1
                cut_win_top3 += rank <= 3
                for b in bands:
                    bands[b] += float(o) >= b
        print(f"\n=== 型C {label}  入稿ゲート通過 {tot:,}R / {nd}日 ===")
        print(f"  外した（買い目に無かった）           {n:,}R  ({n/tot*100:.1f}%)")
        print(f"  うち帯15倍未満で切っていた           {cut_win:,}R  ({cut_win/tot*100:.1f}% of 全体)")
        if cut_win:
            print(f"    その目のモデル確率順位  1位 {cut_win_top1:,} ({cut_win_top1/cut_win*100:.1f}%)"
                  f" / 3位以内 {cut_win_top3:,} ({cut_win_top3/cut_win*100:.1f}%)"
                  f" / 中央 {median(pos_of_win):.0f}位")
            print(f"    切った目の予測オッズ  中央 {median(cut_odds):.1f}倍"
                  f"  (p25 {sorted(cut_odds)[len(cut_odds)//4]:.1f} /"
                  f" p75 {sorted(cut_odds)[len(cut_odds)*3//4]:.1f})")
            print("    帯を下げれば拾えた件数（=全体に対する上限の伸びしろ）:")
            for b in sorted(bands, reverse=True):
                print(f"      帯{b:2d}倍+ にすると +{bands[b]:4,}R  (+{bands[b]/tot*100:5.2f}pt)")


# ───────────────────────── 腕 ─────────────────────────

def _insert_top(x, base_legs, min_o: float, keep_k: bool) -> list | None:
    """帯に切られた**確率1位**の目を差し込む（`keep_k` なら点数を12に保つ）。"""
    cand = [k for k, v in x.po_tf.items()
            if v and float(v) >= min_o and len(set(k)) == 3]
    if not cand:
        return None
    top = max(cand, key=lambda k: x.pr_tf.get(k, 0.0))
    if top in base_legs:
        return None
    out = [top] + list(base_legs)
    return out[:len(base_legs)] if keep_k else out


ARMS: dict[str, object] = {}
for _b in (15.0, 12.0, 10.0, 8.0, 6.0, 5.0, 0.0):
    ARMS[("現行 帯15倍+12点" if _b == 15 else
          ("帯なし 12点" if _b == 0 else f"帯{_b:.0f}倍+ 12点"))] = _plan(_b)
for _m in (5.0, 6.0, 8.0):
    ARMS[f"差込 確率1位≧{_m:.0f}倍(12点維持)"] = ("ins", _m, True)
    ARMS[f"差込 確率1位≧{_m:.0f}倍(13点)"] = ("ins", _m, False)


def run_arm(x, name: str) -> dict | None:
    a = ARMS[name]
    if isinstance(a, Plan):
        got = _build_plan(x.shape, a, x.po_tf, x.pr_tf)
        if not got:
            return None
        legs, st = got
        used = a
    else:
        _, min_o, keep_k = a
        base = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
        if not base:
            return None
        legs = _insert_top(x, base, min_o, keep_k) or list(base)
        from src.type_lab import allocate, alloc_fallback
        st = allocate(legs, x.po_tf, x.pr_tf, CUR)
        if not st:
            fb = alloc_fallback(CUR)
            st = allocate(legs, x.po_tf, x.pr_tf, fb) if fb else None
        if not st:
            return None
        legs = [c for c in legs if c in st]
        used = CUR
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, x.po_tf))


def arms() -> None:
    names = list(ARMS)
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        pre = {}
        for i in base:
            x = ctx(int(i))
            if x is None:
                continue
            pre[int(i)] = {nm: run_arm(x, nm) for nm in names}
        print(f"\n===== 型C {label}  対象 {len(pre):,}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日")
        for nm in names:
            recs = [r for v in pre.values() if (r := v[nm])]
            s = C.summarize(recs, nd)
            big = s.get("big_per_day", 0.0) if s.get("n") else 0.0
            print(C.line(nm, s) + f" {big:9.3f}")
        print("\n  ── 現行と両方が組めたレースだけの対応比較 ──")
        cur = "現行 帯15倍+12点"
        print(f"  {'腕':28s} {'R':>6s} {'表示的中% 現行→案':>24s} {'ROI% 現行→案':>20s}"
              f" {'払戻中央 現行→案':>22s}")
        for nm in names[1:]:
            both = [v for v in pre.values() if v[cur] and v[nm]]
            if not both:
                print(f"  {nm:28s}  (該当なし)")
                continue
            a = C.summarize([v[cur] for v in both], nd)
            b = C.summarize([v[nm] for v in both], nd)
            print(f"  {nm:28s} {len(both):6d} "
                  f"{a['shown']:9.2f} → {b['shown']:6.2f} ({b['shown']-a['shown']:+5.2f}) "
                  f"{a['roi']:6.1f} → {b['roi']:5.1f} ({b['roi']-a['roi']:+5.1f}) "
                  f"{a['med_pay']:8,.0f} → {b['med_pay']:8,.0f}")




# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — 対照・CI・ハイブリッド
#
# 🔴 差込は「買い目を1点入れ替える」操作なので、**同数を無作為に入れ替えた対照**に
#    勝つかを必ず見る（`race_filter_2026_08_27.md` と同型の作法）。
#    対照は2種:
#      ① 市場1位差込 … 帯未満で**最も人気**（予測オッズ最小）の目を入れる
#      ② 無作為差込  … 帯未満の目から無作為に1点（20 seed）
# 🔴 差込はレースの 12〜13% を入稿ゲート（平均想定払戻2万）から落とす。
#    落ちたぶんを**現行の買い目で拾う**ハイブリッドも測る（在庫を減らさない形）。
# ═══════════════════════════════════════════════════════════════════════════

import random  # noqa: E402
from src.type_lab import allocate, alloc_fallback  # noqa: E402


def _finish(x, legs) -> dict | None:
    st = allocate(legs, x.po_tf, x.pr_tf, CUR)
    if not st:
        fb = alloc_fallback(CUR)
        st = allocate(legs, x.po_tf, x.pr_tf, fb) if fb else None
    if not st:
        return None
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, x.po_tf))


def _pick(x, base, min_o: float, how: str, rnd: random.Random | None):
    """帯未満（=現行が切っている）の目から1点選ぶ。無ければ None。"""
    cand = [k for k, v in x.po_tf.items()
            if v and len(set(k)) == 3 and min_o <= float(v) < CUR.min_odds
            and k not in base]
    if not cand:
        return None
    if how == "prob":
        return max(cand, key=lambda k: x.pr_tf.get(k, 0.0))
    if how == "cheap":
        return min(cand, key=lambda k: float(x.po_tf[k]))
    return rnd.choice(cand)


def _arm2(x, min_o: float, how: str, hybrid: bool, rnd=None) -> dict | None:
    base = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
    if not base:
        return None
    base = list(base)
    cur = _finish(x, base)
    top = _pick(x, base, min_o, how, rnd)
    if top is None:
        return cur if hybrid else cur
    got = _finish(x, [top] + base[:len(base) - 1])
    if got:
        return got
    return cur if hybrid else None


def _boot(pairs, n=2000, seed=0):
    """対応ありブートストラップ（レース単位）で Δ表示的中 の95%CI。"""
    rnd = random.Random(seed)
    m = len(pairs)
    out = []
    for _ in range(n):
        s = [pairs[rnd.randrange(m)] for _ in range(m)]
        a = sum(p[0] for p in s) / m * 100
        b = sum(p[1] for p in s) / m * 100
        out.append(b - a)
    out.sort()
    return out[int(n * 0.025)], out[int(n * 0.975)]


def ctrl() -> None:
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        xs = [x for i in base if (x := ctx(int(i))) is not None]
        print(f"\n===== 型C {label}  対象 {len(xs):,}R / {nd}日 =====")
        print(C.HEAD)
        rows = {}
        for nm, kw in (("現行", None),
                       ("差込 確率1位≧5倍", dict(min_o=5.0, how="prob", hybrid=False)),
                       ("差込 確率1位≧5倍→落ち現行", dict(min_o=5.0, how="prob", hybrid=True)),
                       ("差込 確率1位≧6倍→落ち現行", dict(min_o=6.0, how="prob", hybrid=True)),
                       ("対照① 市場1位≧5倍→落ち現行", dict(min_o=5.0, how="cheap", hybrid=True)),
                       ):
            if kw is None:
                recs = {id(x): _finish(x, list(build_legs(x.shape, CUR, x.po_tf, x.pr_tf) or []))
                        for x in xs if build_legs(x.shape, CUR, x.po_tf, x.pr_tf)}
            else:
                recs = {id(x): _arm2(x, **kw) for x in xs}
            rows[nm] = recs
            got = [r for r in recs.values() if r]
            print(C.line(nm, C.summarize(got, nd)))

        # 対照② 無作為差込 20 seed
        best = []
        for sd in range(20):
            rnd = random.Random(sd)
            recs = {id(x): _arm2(x, 5.0, "rand", True, rnd) for x in xs}
            got = [r for r in recs.values() if r]
            best.append(C.summarize(got, nd)["shown"])
        best.sort()
        print(f"  {'対照② 無作為≧5倍→落ち現行(20seed)':26s} 表示的中 中央 {best[10]:.2f}%"
              f"  範囲 {best[0]:.2f}〜{best[-1]:.2f}%")

        # CI（現行と提案の両方が組めたレースだけ・対応あり）
        cur = rows["現行"]
        for nm in ("差込 確率1位≧5倍→落ち現行", "差込 確率1位≧6倍→落ち現行",
                   "対照① 市場1位≧5倍→落ち現行"):
            b = rows[nm]
            pairs = [(1.0 if (cur[k]["pay"] > cur[k]["inv"]) else 0.0,
                      1.0 if (b[k]["pay"] > b[k]["inv"]) else 0.0)
                     for k in cur if cur.get(k) and b.get(k)]
            lo, hi = _boot(pairs)
            a = sum(p[0] for p in pairs) / len(pairs) * 100
            c2 = sum(p[1] for p in pairs) / len(pairs) * 100
            print(f"  {nm:26s} 対応 {len(pairs):5d}R  表示的中 {a:5.2f} → {c2:5.2f}"
                  f"  Δ {c2-a:+5.2f}pt  95%CI [{lo:+5.2f}, {hi:+5.2f}]")




# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — 「帯を下げる → 入稿ゲートに落ちたら現行で拾う」（`GATE_FALLBACK` と同型）
#
# Phase 2 で、差込は**モデル1位でも無作為でも同じ**と分かった（＝効いているのは
# 「帯の下の目を1点でも買うこと」自体）。ならば1点に限定する理由が無いので、
# 帯そのものを下げて、平均想定払戻2万を割ったレースだけ現行へ戻す形を測る。
# ＝ 在庫は現行と同じまま、通るレースでだけ帯が下がる。
# ═══════════════════════════════════════════════════════════════════════════

def _band_hybrid(x, lo: float) -> dict | None:
    cur_legs = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
    cur = _finish(x, list(cur_legs)) if cur_legs else None
    legs = build_legs(x.shape, _plan(lo), x.po_tf, x.pr_tf)
    got = _finish(x, list(legs)) if legs else None
    return got or cur


def band() -> None:
    LOS = (12.0, 10.0, 8.0, 6.0, 5.0, 3.0, 2.0)
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        xs = [x for i in base if (x := ctx(int(i))) is not None]
        print(f"\n===== 型C {label}  対象 {len(xs):,}R / {nd}日 =====")
        print(C.HEAD)
        cur = {}
        for x in xs:
            lg = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
            cur[id(x)] = _finish(x, list(lg)) if lg else None
        print(C.line("現行 帯15倍+12点", C.summarize([r for r in cur.values() if r], nd)))
        for lo in LOS:
            recs = {id(x): _band_hybrid(x, lo) for x in xs}
            got = [r for r in recs.values() if r]
            print(C.line(f"帯{lo:.0f}倍+→落ちたら現行", C.summarize(got, nd)))
            pairs = [(1.0 if cur[k]["pay"] > cur[k]["inv"] else 0.0,
                      1.0 if recs[k]["pay"] > recs[k]["inv"] else 0.0)
                     for k in cur if cur.get(k) and recs.get(k)]
            l2, h2 = _boot(pairs)
            a = sum(p[0] for p in pairs) / len(pairs) * 100
            b = sum(p[1] for p in pairs) / len(pairs) * 100
            fire = sum(1 for k in cur if cur.get(k) and recs.get(k)
                       and recs[k]["mean"] != cur[k]["mean"])
            print(f"      Δ表示的中 {a:5.2f} → {b:5.2f}  {b-a:+5.2f}pt"
                  f"  95%CI [{l2:+5.2f}, {h2:+5.2f}]  帯が下がったレース {fire/len(pairs)*100:.1f}%")




# ═══════════════════════════════════════════════════════════════════════════
# Phase 4 — 採用候補の確定（差込 1点 / 2点・選び方 3通り・10万+ と発火率つき）
# ═══════════════════════════════════════════════════════════════════════════

def _insert_n(x, min_o: float, how: str, n: int, rnd=None) -> dict | None:
    base = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
    if not base:
        return None
    base = list(base)
    cur = _finish(x, base)
    cand = [k for k, v in x.po_tf.items()
            if v and len(set(k)) == 3 and min_o <= float(v) < CUR.min_odds and k not in base]
    if not cand:
        return cur
    if how == "prob":
        cand.sort(key=lambda k: -x.pr_tf.get(k, 0.0))
    elif how == "cheap":
        cand.sort(key=lambda k: float(x.po_tf[k]))
    else:
        rnd.shuffle(cand)
    add = cand[:n]
    got = _finish(x, add + base[:len(base) - len(add)])
    return got or cur


def final() -> None:
    ARM = [("現行 帯15倍+12点", None)]
    for how, lab in (("cheap", "市場1位"), ("prob", "確率1位")):
        for n in (1, 2):
            ARM.append((f"差込 {lab}≧5倍 {n}点→落ち現行", (5.0, how, n)))
    ARM.append(("差込 市場1位≧8倍 1点→落ち現行", (8.0, "cheap", 1)))
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        xs = [x for i in base if (x := ctx(int(i))) is not None]
        print(f"\n===== 型C {label}  対象 {len(xs):,}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日   発火%")
        cur = {}
        for x in xs:
            lg = build_legs(x.shape, CUR, x.po_tf, x.pr_tf)
            cur[id(x)] = _finish(x, list(lg)) if lg else None
        for nm, kw in ARM:
            recs = cur if kw is None else {id(x): _insert_n(x, *kw) for x in xs}
            got = [r for r in recs.values() if r]
            s = C.summarize(got, nd)
            pairs = [(1.0 if cur[k]["pay"] > cur[k]["inv"] else 0.0,
                      1.0 if recs[k]["pay"] > recs[k]["inv"] else 0.0)
                     for k in cur if cur.get(k) and recs.get(k)]
            fire = (sum(1 for k in cur if cur.get(k) and recs.get(k)
                        and recs[k]["mean"] != cur[k]["mean"]) / max(len(pairs), 1) * 100)
            print(C.line(nm, s) + f" {s.get('big_per_day', 0):9.3f} {fire:7.1f}")
            if kw is None:
                continue
            l2, h2 = _boot(pairs)
            a = sum(p[0] for p in pairs) / len(pairs) * 100
            b = sum(p[1] for p in pairs) / len(pairs) * 100
            ra = sum(cur[k]["pay"] for k in cur if cur.get(k) and recs.get(k)) / \
                sum(cur[k]["inv"] for k in cur if cur.get(k) and recs.get(k)) * 100
            rb = sum(recs[k]["pay"] for k in cur if cur.get(k) and recs.get(k)) / \
                sum(recs[k]["inv"] for k in cur if cur.get(k) and recs.get(k)) * 100
            print(f"      Δ表示的中 {b-a:+5.2f}pt 95%CI [{l2:+5.2f}, {h2:+5.2f}]"
                  f"   ΔROI {rb-ra:+5.1f}pt")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5 — 出荷後の突き合わせ（**本番の関数**で同じ数字が出るか）
#
# 🔴 Phase 1〜4 は実験用に組んだ買い目。実装したら**本番の入口**
#    （`build_with_gate_fallback`＝`build_type_lab_picks` が呼ぶもの）で
#    測り直して、採用した数字が再現することを確かめる。
#    ⚠️ 台の `RaceShape` はライン情報を持たないので `apply_line_swap` は発火しない
#       （実験窓の測定と同条件・本番はここで更に差し替えが入る）。
# ═══════════════════════════════════════════════════════════════════════════

def verify() -> None:
    from dataclasses import replace as _replace
    from src.type_lab import build_with_gate_fallback

    BEFORE = _replace(CUR, underband_min=0.0)      # 実装前の C_hit
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        rows = {"実装前 (underband_min=0)": [], "本番 build_with_gate_fallback": []}
        fired = seen = 0
        for i in base:
            x = ctx(int(i))
            if x is None:
                continue
            for nm, pl in (("実装前 (underband_min=0)", BEFORE),
                           ("本番 build_with_gate_fallback", CUR)):
                got = build_with_gate_fallback(x.shape, pl, x.po_tf, x.pr_tf, n_entries=7)
                if not got:
                    continue
                legs, st, used = got
                if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
                    continue
                if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
                    continue
                if pl is CUR:
                    seen += 1
                    fired += used.underband_min > 0
                pay = (float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0)
                       if x.win_tf in st else 0.0)
                rows[nm].append(dict(date=x.date, inv=float(sum(st.values())), pay=pay,
                                     k=len(st),
                                     mean=mean_expected_payout(st, x.po_tf)))
        print(f"\n===== 型C {label}  / {nd}日 =====")
        print(C.HEAD)
        for nm, recs in rows.items():
            print(C.line(nm, C.summarize(recs, nd)))
        print(f"  差込が実際に効いたレース {fired:,}/{seen:,} ({fired/max(seen,1)*100:.1f}%)")

# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — 差し込む点の下限（`underband_min`）の掃引（2026-09-08・ユーザー指摘）
#
# > 置き換える1点のオッズがあまりに低い場合、金額の割り当てが多くなってしまう
#
# `conf` の床は 予算 × `MIN_PAYOUT_MULT` ÷ 予測オッズ なので、安い点ほど必ず厚い。
# **下限は事故防止であると同時に性能の設定でもある**（下限なしは表示的中も落ちる）。
# 🔴 見るのは「表示的中」だけでなく **差込点に乗る賭け金の割合**（懸念そのもの）。
# ═══════════════════════════════════════════════════════════════════════════

FLOORS = (0.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)


def _sold(x, plan):
    """本番の入口で組んで、入稿ゲートを通ったものだけ返す。"""
    from src.type_lab import build_with_gate_fallback
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


def floor() -> None:
    from dataclasses import replace as _replace
    for label, win in WIN:
        base = C.select("C", win)
        nd = C.days_of(C.select(None, win))
        xs = [x for i in base if (x := ctx(int(i))) is not None]
        print(f"\n===== 型C {label}  {len(xs):,}R / {nd}日 =====")
        print("  下限   件/日  表示的中%   払戻中央   ROI%  発火%"
              "   差込点の賭け金/予算            差込点の予測オッズ")
        print("                                                  "
              "中央    p95    最大     中央   最小")
        for lo in FLOORS:
            pl = _replace(CUR, underband_min=lo)
            recs, shares, odds, fired, seen = [], [], [], 0, 0
            for x in xs:
                got = _sold(x, pl)
                if not got:
                    continue
                legs, st, _ = got
                seen += 1
                under = [c for c in st if float(x.po_tf[c]) < pl.min_odds]
                if under:
                    fired += 1
                    shares.append(st[under[0]] / sum(st.values()))
                    odds.append(float(x.po_tf[under[0]]))
                pay = (float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0)
                       if x.win_tf in st else 0.0)
                recs.append(dict(date=x.date, inv=float(sum(st.values())), pay=pay,
                                 k=len(st), mean=mean_expected_payout(st, x.po_tf)))
            s = C.summarize(recs, nd)
            if shares:
                sh, od = sorted(shares), sorted(odds)
                ext = (f" {sh[len(sh)//2]*100:6.1f}% {sh[int(len(sh)*.95)]*100:6.1f}%"
                       f" {sh[-1]*100:6.1f}%  {od[len(od)//2]:7.1f} {od[0]:6.1f}")
            else:
                ext = "        —"
            nm = "なし" if lo == 0 else f"{lo:.0f}倍"
            print(f"  {nm:5s} {s['perday']:6.2f} {s['shown']:8.2f} {s['med_pay']:10,.0f}"
                  f" {s['roi']:6.1f} {fired/max(seen,1)*100:6.1f}{ext}")


def minpay() -> None:
    """差し込んだ1点が当たったときいくら返るか（理屈でなく実データで確かめる）。"""
    def q(a, p):
        return sorted(a)[min(int(len(a) * p), len(a) - 1)]
    for label, win in WIN:
        ins, allmin, hit = [], [], []
        for i in C.select("C", win):
            x = ctx(int(i))
            if x is None:
                continue
            got = _sold(x, CUR)
            if not got:
                continue
            _, st, _ = got
            pays = {c: st[c] * float(x.po_tf[c]) for c in st}
            allmin.append(min(pays.values()))
            under = [c for c in st if float(x.po_tf[c]) < CUR.min_odds]
            if not under:
                continue
            ins.append(pays[under[0]])
            if x.win_tf == under[0]:
                hit.append(float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0))
        print(f"\n=== 型C {label} ===")
        print(f"  商品内の最低想定払戻   最小 {min(allmin):,.0f}円 /"
              f" p05 {q(allmin, .05):,.0f} / 中央 {q(allmin, .5):,.0f}")
        print(f"  差込点の想定払戻       最小 {min(ins):,.0f}円 / p05 {q(ins, .05):,.0f} /"
              f" 中央 {q(ins, .5):,.0f} / 最大 {max(ins):,.0f}")
        print(f"  うち2万円未満          {sum(1 for v in ins if v < 20_000):,}/{len(ins):,}件"
              " （床が置けず旧配分へ落ちたレース）")
        if hit:
            print(f"  差込点が実際に当たった {len(hit):,}件  確定払戻 中央 {q(hit, .5):,.0f}円"
                  f" / 最小 {min(hit):,.0f} / 投資割れ {sum(1 for v in hit if v < 10_000):,}件")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diag"
    {"diag": diag, "arms": arms, "ctrl": ctrl, "band": band, "final": final,
     "verify": verify, "floor": floor, "minpay": minpay}[cmd]()
