#!/usr/bin/env python3
"""帯（`min_odds`）と入稿ゲート（`MIN_MEAN_PAYOUT`）の2次元掃引 — 台の作成（2026-09-10）。

## これは何か

型ラボが「本命の並び（◎○△）を最初から買っていない」構造そのものを見直す。
落としている機構は3つあり、いずれも**平均想定払戻2万円の入稿ゲートの下流**にある:

  帯 `min_odds`        C_hit 15倍 / E_hit 30倍 / F_hit 5倍
  Σ制約 `sigma_max`    B_hit（計画払戻 3万円の床）
  `axis2_drop_fav`     D_hit（最人気の相手を1点落とす）

🔴 **ゲート T は買い方の選択（`GATE_FALLBACK`）にも入稿の可否にも効く**ので、
   T を動かすと母集団が変わる。そこで **T に依存しない部分（買い目・賭け金）を
   先に全部作って焼き付け**、T の適用は集計側で行う。

## 本番との対応（測る前に読んだもの）

- 買い方は `src.type_lab.build_with_gate_fallback` を再実装せず**分解して**持つ:
  `_build_plan`（= build_legs + allocate + alloc_fallback）で本命と `GATE_FALLBACK`
  の各段を作り、`line_legs` の差し替え候補（m=2,1）も作る。**選択だけが T 依存**。
- 軸信頼ゲート `passes_axis_gate` を通す（本番と同じ）。
- 売る商品は `sell_plans_for` と同じ分岐（看板枠 → 型A 3分割 → 型F 種別）。
  `trio_ok` は **A_trio 行がゲートを通るか**なので T 依存 → 集計側で判定する。
- `RaceShape.lines` を board の `LG`/`A_line_pos` から作る。
  🔴 これまでの exp スクリプトは `lines` を渡しておらず `line_legs`（ライン決着への
     差し替え）が一度も発火していなかった。ここでは本番どおり発火させる。
- 高額枠（`_big`/`_sign` の B/C/D）は日次上限で捨てるレースに置く商品なので、
  日次上限を模していない本台では扱わない（従来の exp スクリプトと同じ）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/band_gate_build.py
"""
from __future__ import annotations

import importlib.util
import itertools
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, LINE_SWAP_LEGS, Plan, RaceShape, _build_plan, _lines_of, allocate,
    alloc_fallback, line_legs, mean_expected_payout, win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

MIN_POINT_ODDS = 2.0
PERMS = C.CANON
C3 = C.CANON3
CACHE = Path("/tmp/band_gate_rows.pkl")


# ───────────────────────── 腕（variant）の定義 ─────────────────────────

def _c(L: float, ub: float = 5.0) -> Plan:
    """C_hit の帯 L 版。帯が underband 下限以下なら差込は自然に消える。"""
    return replace(PLANS["C_hit"], min_odds=L, underband_min=(ub if L > ub else 0.0))


def _e(L: float, ub: float = 0.0) -> Plan:
    return replace(PLANS["E_hit"], min_odds=L, underband_min=(ub if L > ub else 0.0))


def _f(L: float) -> Plan:
    return replace(PLANS["F_hit"], min_odds=L)


def _b(target: float) -> Plan:
    """B_hit の Σ制約（計画払戻の床）版。sigma_max = 予算/目標払戻。"""
    return replace(PLANS["B_hit"], sigma_max=10_000.0 / target)


def _d(structure: str, n: int) -> Plan:
    return replace(PLANS["D_hit"], structure=structure, n_partners=n)


#: variant名 -> (プラン, フォールバック列)。**フォールバックは本番の `GATE_FALLBACK` と同じ思想**
#: （C_hit=差込なし / F_hit=帯15倍+差込 → 帯15倍）。帯を動かした版にも同じ形で付ける。
def _fb_c(p: Plan) -> tuple[Plan, ...]:
    return (replace(p, underband_min=0.0),) if p.underband_min else ()


def _fb_f(p: Plan) -> tuple[Plan, ...]:
    base = replace(PLANS["F_hit"], min_odds=15.0)
    return (replace(base, underband_min=5.0), base)


VARIANTS: dict[str, tuple[Plan, tuple[Plan, ...]]] = {}
for _k in ("A_hit", "A_trio", "A_ana", "F_pay", "F_sign"):
    VARIANTS[_k] = (PLANS[_k], ())
for _L in (0.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0):
    p = _c(_L)
    VARIANTS[f"C@{_L:g}"] = (p, _fb_c(p))
    if _L > 5:                       # 差込なし版（帯だけ動かす腕）
        q = replace(p, underband_min=0.0)
        VARIANTS[f"C@{_L:g}nb"] = (q, ())
for _L in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0):
    VARIANTS[f"E@{_L:g}"] = (_e(_L), ())
    if _L > 5:
        p = _e(_L, 5.0)
        VARIANTS[f"E@{_L:g}ub"] = (p, (replace(p, underband_min=0.0),))
for _L in (0.0, 3.0, 5.0, 8.0, 10.0, 15.0):
    p = _f(_L)
    VARIANTS[f"F@{_L:g}"] = (p, _fb_f(p))
    VARIANTS[f"F@{_L:g}nofb"] = (p, ())
for _t in (15_000.0, 20_000.0, 25_000.0, 30_000.0, 35_000.0):
    VARIANTS[f"B@{_t/1000:g}k"] = (_b(_t), ())
VARIANTS["D@drop3"] = (PLANS["D_hit"], ())
for _n in (2, 3, 4, 5):
    VARIANTS[f"D@flow{_n}"] = (_d("axis2_flow", _n), ())
VARIANTS["D@drop4"] = (_d("axis2_drop_fav", 4), ())

#: 売るプラン枠 -> その枠で使える variant 名の接頭辞
SLOT_OF = {"A_hit": "A_hit", "A_trio": "A_trio", "A_ana": "A_ana",
           "F_pay": "F_pay", "F_sign": "F_sign"}
for v in VARIANTS:
    if v[0] in "BCDEF" and "@" in v:
        SLOT_OF[v] = {"B": "B_hit", "C": "C_hit", "D": "D_hit",
                      "E": "E_hit", "F": "F_hit"}[v[0]]


# ───────────────────────── 1レース ─────────────────────────

class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date")


_A = None


def _arrays():
    z = C.board()
    return {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "PO", "WIN", "PAY",
                              "TRIO_PO", "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY", "DATE",
                              "TYPE", "AXIS_SUM", "ARARE", "GAP", "RTYPE",
                              "A_prediction_mark", "DAYIDX")}


def ctx(i: int) -> Ctx | None:
    global _A
    if _A is None:
        _A = _arrays()
    a = _A
    cars = list(range(1, 8))
    p3 = {c: float(a["P3"][i][c - 1]) for c in cars}
    pw = {c: float(a["PW"][i][c - 1]) for c in cars}
    lg = {c: a["LG"][i][c - 1] for c in cars}
    lp = {c: a["A_line_pos"][i][c - 1] for c in cars}
    from src.strategy_wt import rank_7t3_blend_probs
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(a["PO"][i][t]) for t in range(210)
          if np.isfinite(a["PO"][i][t]) and a["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    x = Ctx()
    x.shape = RaceShape(str(a["TYPE"][i]), float(a["AXIS_SUM"][i]), int(a["ARARE"][i]),
                        float(a["GAP"][i]), False, order, win_entropy(pw),
                        lines=_lines_of(lg, lp))
    x.po_tf, x.pr_tf = po, pr
    x.po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(a["WIN"][i])]
    x.pay_tf = float(a["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(a["TRIO_WIN"][i])])
    x.odds_t3 = float(a["TRIO_PAY"][i])
    x.date = str(a["DATE"][i])
    return x


def _score(x: Ctx, plan: Plan, legs, st) -> dict:
    pod = x.po_t3 if plan.bet_type == "trio" else x.po_tf
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
        hit_set = x.win_t3 in st
    else:
        pay = float(st[x.win_tf] * x.pay_tf) if x.win_tf in st else 0.0
        hit_set = x.win_tf in st
    return dict(mean=float(mean_expected_payout(st, pod)),
                minodds=float(min(float(pod[c]) for c in st)),
                k=len(st), inv=float(sum(st.values())), pay=pay, hit=hit_set,
                legs=[tuple(sorted(c)) if plan.bet_type == "trio" else tuple(c)
                      for c in st])


def _stage(x: Ctx, plan: Plan) -> dict | None:
    """1段ぶん（本命 or フォールバック1つ）。`line_legs` の候補も付ける。"""
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    got = _build_plan(x.shape, plan, pod, prb)
    if not got:
        return None
    legs, st = got
    out = _score(x, plan, legs, st)
    sw = []
    for m in LINE_SWAP_LEGS:
        nl = line_legs(x.shape, plan, legs, pod, m)
        if not nl:
            continue
        s2 = allocate(nl, pod, prb, plan)
        if not s2 or len(s2) != len(nl):
            continue
        sw.append(_score(x, plan, nl, s2))
    out["swaps"] = sw
    return out


def build() -> list[dict]:
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    rt = np.array([str(v) for v in z["RTYPE"]])
    MK = z["A_prediction_mark"]
    DI = z["DAYIDX"]
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 2000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = tp[i]
            want = []
            if tl == "A":
                want = ["A_hit", "A_trio", "A_ana"]
            elif tl == "F":
                want = [v for v in VARIANTS if v.startswith("F@")] + ["F_pay", "F_sign"]
            else:
                want = [v for v in VARIANTS if v.startswith(tl + "@")]
            chains = {}
            for v in want:
                plan, fbs = VARIANTS[v]
                ch = []
                for pl in (plan,) + fbs:
                    s = _stage(x, pl)
                    ch.append(s)
                if ch[0] is None and all(c is None for c in ch):
                    continue
                chains[v] = ch
            if not chains:
                continue
            mk = {c: int(MK[i][c - 1]) for c in range(1, 8)}
            m3 = tuple(c for c in range(1, 8) if mk[c] in (1, 2, 3))
            # 🔴 **失敗の分解に要る列**（2026-09-10 の方針変更）。
            #    ② 型判定の失敗 = その型の読みなら起きないはずの決着だったか
            #    ③ 買い目の失敗 = 読みどおりの帯の中なのに買えていなかったか
            #    を後から数えられるよう、決着の目の**予測オッズ**と**確率順位**、
            #    軸2車がそろったか、本線（◎○△）の予測オッズと確率を焼き付ける。
            srt = sorted(x.pr_tf, key=lambda k: -x.pr_tf[k])
            try:
                pr_rank = srt.index(x.win_tf) + 1
            except ValueError:
                pr_rank = 999
            a1, a2 = x.shape.order[0], x.shape.order[1]
            top3 = set(x.win_tf)
            hs = tuple(sorted(m3, key=lambda c: mk[c])) if len(m3) == 3 else None
            rows.append(dict(
                win=win, date=x.date, dayidx=int(DI[i]), type=tl, rtype=str(rt[i]),
                axis_sum=float(z["AXIS_SUM"][i]), pw_ent=float(x.shape.pw_ent),
                m3=(m3 if len(m3) == 3 else None),
                m3_win=(len(m3) == 3 and frozenset(x.win_tf) == frozenset(m3)),
                win_tf=x.win_tf, win_t3=tuple(sorted(x.win_t3)),
                po_win=float(x.po_tf.get(x.win_tf, float("nan"))),
                po_win_t3=float(x.po_t3.get(x.win_t3, float("nan"))),
                pr_rank_win=int(pr_rank),
                pr_win=float(x.pr_tf.get(x.win_tf, 0.0)),
                axis_both=bool(a1 in top3 and a2 in top3),
                axis_n=int((a1 in top3) + (a2 in top3)),
                po_m3=(float(x.po_t3.get(frozenset(m3), float("nan")))
                       if len(m3) == 3 else float("nan")),
                po_m3_tf=(float(x.po_tf.get(hs, float("nan")))
                          if hs else float("nan")),
                q_m3=(float(sum(x.pr_tf.get(p, 0.0)
                                for p in itertools.permutations(m3)))
                      if len(m3) == 3 else 0.0),
                pay_tf=float(x.pay_tf), pay_t3=float(x.odds_t3),
                chains=chains))
    return rows


if __name__ == "__main__":
    rows = build()
    pickle.dump(rows, CACHE.open("wb"))
    print(f"台 {len(rows):,}行 -> {CACHE}")
