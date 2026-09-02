#!/usr/bin/env python3
"""型F を **レース種別** で割り、買い方を分けられるか（2026-09-02・ユーザー提案）。

## 発端

> 決勝・特選が難易度が上がり F に寄る、結果当たりづらいは現状のモデルとして
> 正しそうだが、F についても決勝・特選の F と下位レースの F では傾向や狙い方が
> 異なる可能性があるように思う。

現行は `TYPE_F_SELL_BY_RACE_TYPE = {"決勝": "F_pay", "チャレンジ決勝": "F_pay"}` で
**決勝だけ** F_pay へ分岐し、残りは全部 `F_hit`（確率上位12点）。
特選・選抜・一般はすべて同じ買い方になっている。

## 作法

🔴 これは**見送り（件数を減らす）ではなく振り分け**なので、件数は動かない。
   それでも「半分を別プランに替えれば表示的中は動く」ので、
   **同数を無作為に振り分けた対照に勝つか**を必ず見る
   （`typef_split_control.py` / `race_filter_2026_08_27.md` と同型）。
🔴 台の PO（予測オッズ）は `odds_tf_n7` train_end 2025-12-31 ＝ **探索窓は in-sample**。
   採否は確認窓で決め、探索窓は符号の一致確認にだけ使う。
🔴 ROI では採否を決めない（この層は ±2.5pt に収めるのに約15.6年）。表示的中で見る。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/typef_racetype.py desc
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import scripts.exp_type_lab.common as C  # noqa: E402

#: 種別のまとめ方。**勝ち上がり戦（予選→準決勝）と番組編成戦（決勝・特選・一般）**で割る。
GROUPS = {
    "決勝系": ("決勝", "チャレンジ決勝"),
    "特選・選抜系": ("特選", "初特選", "選抜", "チャレンジ選抜"),
    "一般系": ("一般", "特一般", "チャレンジ一般"),
    "準決勝系": ("準決勝", "チャレンジ準決勝"),
    "予選系": ("予選", "特予選", "チャレンジ予選", "予選(第１走)", "予選(第２走)"),
}
G_OF = {t: g for g, ts in GROUPS.items() for t in ts}


def top3_of(i: int) -> set[int]:
    return set(C.CANON[int(C.board()["WIN"][i])])


def stats(idx: np.ndarray) -> dict:
    z = C.board()
    n = len(idx)
    if not n:
        return dict(n=0)
    both = half = broken = 0
    pays, agree, axs, arare, gaps = [], 0, [], [], []
    for i in idx:
        o = C.p3_order(int(i))
        a = {o[0], o[1]}
        k = len(a & top3_of(int(i)))
        both += k == 2
        half += k == 1
        broken += k == 0
        pays.append(float(z["PAY"][i]) / 100.0)
        agree += bool(z["AGREE"][i])
        axs.append(float(z["AXIS_SUM"][i]))
        arare.append(float(z["ARARE"][i]))
        gaps.append(float(z["GAP"][i]))
    return dict(n=n, both=both / n * 100, half=half / n * 100, broken=broken / n * 100,
                med=median(pays), p100=sum(1 for p in pays if p >= 100) / n * 100,
                agree=agree / n * 100, axis=sum(axs) / n, arare=sum(arare) / n,
                gap=sum(gaps) / n)


def desc() -> None:
    z = C.board()
    rt = np.array([str(x) for x in z["RTYPE"]])
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = C.select("F", win)
        print(f"\n=== 型F {label}  n={len(base):,} ===")
        print(f"  {'群':14s} {'n':>5s} {'件/日':>6s} {'二軸そろい':>9s} {'片軸':>7s} "
              f"{'軸崩壊':>7s} {'中央倍率':>8s} {'100倍+':>7s} {'印一致':>7s} "
              f"{'axis_sum':>9s} {'荒れ度':>6s} {'gap':>7s}")
        nd = C.days_of(C.select(None, win))
        rows = []
        for g in list(GROUPS) + ["__ALL__"]:
            sel = base if g == "__ALL__" else base[np.isin(rt[base], GROUPS[g])]
            s = stats(sel)
            if not s["n"]:
                continue
            rows.append((g, s))
            print(f"  {('全体' if g=='__ALL__' else g):14s} {s['n']:5d} {s['n']/nd:6.2f} "
                  f"{s['both']:8.2f}% {s['half']:6.2f}% {s['broken']:6.2f}% "
                  f"{s['med']:8.1f} {s['p100']:6.2f}% {s['agree']:6.2f}% "
                  f"{s['axis']:9.4f} {s['arare']:6.2f} {s['gap']:7.4f}")




# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — 種別群ごとに買い方候補を測る
#
# 🔴 **本番の関数で組む**（`src.type_lab.build_legs` / `allocate`）。実験用に
#    書き直すと「測ったもの」と「売るもの」がずれる（CLAUDE.md 検証の作法）。
# 🔴 **板の PROB は同ライン隣接ボーナスが入っていない**ので、`build_type_lab_picks`
#    と同じく `rank_7t3_blend_probs(line_group, line_pos)` で組み直す。
# 🔴 ゲートは2段。①軸信頼ゲート（`AXIS_GATE_MIN`）②入稿ゲート（平均想定払戻>2万・
#    1点でも予測<2.0倍なら見送り）。**通してから比べる**。
# ═══════════════════════════════════════════════════════════════════════════

import importlib.util  # noqa: E402
import itertools  # noqa: E402

from src.type_lab import (  # noqa: E402
    BUDGET, PLANS, Plan, RaceShape, allocate, build_legs, mean_expected_payout,
    win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS = C.CANON
C3 = C.CANON3

#: 測る買い方。現行3つ＋既存の型の帯・点数を型Fへ当てたもの＋三連複。
#: `gate_key` は軸信頼ゲートに使うプラン名（母集団を現行と揃えるため全部 F_hit の 1.230）。
CAND: dict[str, Plan] = {
    "F_hit 確率上位12点":   PLANS["F_hit"],
    "F_pay 軸1固定4点":     PLANS["F_pay"],
    "F_sign 看板枠":        PLANS["F_sign"],
    "確率上位6点":          Plan("x", "F", "trifecta", "prob_top", 0, max_legs=6, alloc="conf"),
    "確率上位8点":          Plan("x", "F", "trifecta", "prob_top", 0, max_legs=8, alloc="conf"),
    "確率上位18点":         Plan("x", "F", "trifecta", "prob_top", 0, max_legs=18, alloc="conf"),
    "帯15倍+ 12点(C式)":    Plan("x", "F", "trifecta", "prob_top", 0, min_odds=15.0, max_legs=12, alloc="dutch"),
    "帯30倍+ 14点(E式)":    Plan("x", "F", "trifecta", "prob_top", 0, min_odds=30.0, max_legs=14, alloc="dutch"),
    "Σ床3万(B式)":          Plan("x", "F", "trifecta", "prob_top", 0, max_legs=8, sigma_max=1/3.0, alloc="dutch"),
    "三連複 軸2車+相手3":    Plan("x", "F", "trio", "axis2_drop_fav", 3, alloc="dutch"),
    "三連複 軸2車+相手2":    Plan("x", "F", "trio", "axis2_flow", 2, alloc="dutch"),
}
TRIO_FREE = ("三連複 10倍+ 4点", "三連複 15倍+ 5点")

#: 🔴 `F_hit`（12点・帯なし）は**平均想定払戻2万円のゲートに 24% 落ちる**
#:    （12点で平均2万＝平均予測オッズ24倍が要る）。落ちるのは買い目が人気側に
#:    寄ったレース＝本来いちばん当たる側なので、そこを帯15倍で拾えるかを見る。
HYBRID = "F_hit→落ちたら帯15倍12点"
HYBRID_B = "F_hit→落ちたらΣ床3万"
HYBRID_C = "F_hit→落ちたら確率上位8点"


class Ctx:
    """1レースぶんの本番相当の入力。"""
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date")


def _arrays():
    z = C.board()
    return {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "PO", "PROB", "WIN", "PAY",
                              "TRIO_PO", "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY", "DATE", "TYPE",
                              "AXIS_SUM", "ARARE", "GAP", "RTYPE")}


_A = None


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
                        float(a["GAP"][i]), False, order, win_entropy(pw))
    x.po_tf, x.pr_tf = po, pr
    x.po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {}
    for j, c in enumerate(C3):
        x.pr_t3[frozenset(c)] = sum(pr.get(p, 0.0) for p in itertools.permutations(c))
    x.win_tf = PERMS[int(a["WIN"][i])]
    x.pay_tf = float(a["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(a["TRIO_WIN"][i])])
    x.odds_t3 = float(a["TRIO_PAY"][i])
    x.date = str(a["DATE"][i])
    return x


def free_trio(x: Ctx, lo: float, k: int):
    """帯 lo 以上の三連複を確率降順に k 点（`type_f.md` の採用候補）。"""
    cand = [c for c, o in x.po_t3.items() if o >= lo]
    cand.sort(key=lambda c: -x.pr_t3.get(c, 0.0))
    return cand[:k] if len(cand) >= k else None


def run_arm(x: Ctx, name: str) -> dict | None:
    """1レース1腕。ゲートを通らなければ None。"""
    if name in (HYBRID, HYBRID_B, HYBRID_C):
        fb = {HYBRID: "帯15倍+ 12点(C式)", HYBRID_B: "Σ床3万(B式)",
              HYBRID_C: "確率上位8点"}[name]
        return run_arm(x, "F_hit 確率上位12点") or run_arm(x, fb)
    if name in TRIO_FREE:
        lo, k = (10.0, 4) if "10倍" in name else (15.0, 5)
        legs = free_trio(x, lo, k)
        if not legs:
            return None
        plan = Plan("x", "F", "trio", "axis2_flow", k, alloc="dutch")
        st = allocate(legs, x.po_t3, x.pr_t3, plan)
    else:
        plan = CAND[name]
        pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
        legs = build_legs(x.shape, plan, pod, prb)
        if not legs:
            return None
        st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    pod = x.po_t3 if (name in TRIO_FREE or CAND[name].bet_type == "trio") else x.po_tf
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    inv = float(sum(st.values()))
    if name in TRIO_FREE or CAND[name].bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=inv, pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod))


def plans_cmd() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    names = list(CAND) + list(TRIO_FREE) + [HYBRID, HYBRID_B, HYBRID_C]
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = C.select("F", win)
        base = base[np.array([float(z["AXIS_SUM"][i]) >= AXIS_GATE_MIN["F_hit"] for i in base])]
        nd = C.days_of(C.select(None, win))
        print("")
        print("=" * 118)
        print(f"=== {label}  型F(軸信頼ゲート後) n={len(base):,} ===")
        for g in list(GROUPS) + ["__ALL__"]:
            sel = base if g == "__ALL__" else base[np.isin(rt[base], GROUPS[g])]
            if len(sel) < 50:
                continue
            print("")
            print(f"-- {'全体' if g=='__ALL__' else g}  n={len(sel)} --")
            print(C.HEAD)
            recs = {n: [] for n in names}
            for i in sel:
                x = ctx(int(i))
                if x is None:
                    continue
                for n in names:
                    r = run_arm(x, n)
                    if r:
                        recs[n].append(r)
            for n in names:
                print(C.line(n, C.summarize(recs[n], nd)))




# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — ラインナップ全体を再現し、**開催日目ごと**の表示的中を見る
#
# 発端（2026-09-02 の分解）: 型F率は 初日 8.4% -> 2日目 28.2% -> **最終日 52.6%**。
# その最終日に集中する種別（決勝系・準決勝系・特選）は、いま `SIGNBOARD_RACE_TYPES`
# によって **看板枠 `F_sign`（表示的中 5%）** へ振り分けられている。
# ＝ 「最終日は当たらない」の主因は型Fそのものではなく**看板枠の置き場所**の可能性。
#
# 🔴 看板枠は 2026-09-01 にユーザー判断で入れたばかりの意図的な取引
#    （表示的中 25.52 -> 22.13% と引き換えに 10万+ を 0.139 -> 0.338件/日）。
#    ここで測るのは「同じ 10万+ を保ったまま最終日の表示的中を戻せるか」。
# ═══════════════════════════════════════════════════════════════════════════

from src.type_lab import (  # noqa: E402
    ANA_PW_ENT_MIN, SIGNBOARD_RACE_TYPES, TYPE_F_SELL_BY_RACE_TYPE,
    TYPE_F_SELL_DEFAULT)

MARQUEE_RT = ("決勝", "チャレンジ決勝", "準決勝", "チャレンジ準決勝",
              "特選", "初特選", "特秀")
GENERAL_RT = ("一般", "特一般", "チャレンジ一般", "選抜", "チャレンジ選抜")
HEAT_RT = ("予選", "特予選", "チャレンジ予選", "予選(第１走)", "予選(第２走)")

#: 看板枠を置く種別の候補。名前 -> その集合（None = 型F 全部）。
MENUS: dict[str, object] = {
    "0 看板枠なし": (),
    "1 現行(決勝系+準決勝系+特選)": tuple(SIGNBOARD_RACE_TYPES),
    "2 決勝系のみ": ("決勝", "チャレンジ決勝"),
    "3 決勝系+特選": ("決勝", "チャレンジ決勝", "特選", "初特選", "特秀"),
    "7 決勝系+準決勝系(特選を外す)": ("決勝", "チャレンジ決勝", "準決勝", "チャレンジ準決勝"),
    "4 一般系+予選系へ移す": GENERAL_RT + HEAT_RT,
    "5 一般系へ移す": GENERAL_RT,
    "6 型F全部": None,
}

#: 看板枠の種別 × 型F の非看板レースをハイブリッドで拾うか、の組み合わせ。
MENUS2: dict[str, tuple] = {
    "1  現行(決勝系+準決勝系+特選)":        (tuple(SIGNBOARD_RACE_TYPES), False),
    "1H 現行 + ハイブリッド":              (tuple(SIGNBOARD_RACE_TYPES), True),
    "2  決勝系のみ":                      (("決勝", "チャレンジ決勝"), False),
    "2H 決勝系のみ + ハイブリッド":         (("決勝", "チャレンジ決勝"), True),
    "3H 決勝系+特選 + ハイブリッド":        (("決勝", "チャレンジ決勝", "特選", "初特選", "特秀"), True),
    "0H 看板枠なし + ハイブリッド":         ((), True),
}


def _plan_for(tl: str, rtype: str, pw_ent: float, trio_ok: bool,
              sign_rt) -> str:
    """`sell_plans_for` を再現しつつ、看板枠の種別だけ差し替える。"""
    if tl == "F":
        if sign_rt is None or (sign_rt and rtype in sign_rt):
            return "F_sign"
        return TYPE_F_SELL_BY_RACE_TYPE.get(rtype, TYPE_F_SELL_DEFAULT)
    if tl == "A":
        if pw_ent >= ANA_PW_ENT_MIN:
            return "A_ana"
        return "A_trio" if trio_ok else "A_hit"
    return {"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]


#: ハイブリッドのフォールバック（`帯15倍+ 12点`）。`PLANS` には無いのでここで持つ。
_FALLBACK = Plan("_C15", "F", "trifecta", "prob_top", 0, min_odds=15.0,
                 max_legs=12, alloc="dutch")


def _run_named(x, key: str):
    plan = _FALLBACK if key == "_C15" else PLANS[key]
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod))


def lineup() -> None:
    """🔴 **1レースぶんの結果は先に1回だけ作る。** メニューは「型Fで F_hit と F_sign の
    どちらを採るか」しか変えないので、レースごとに両方を作っておけば7通りを
    使い回せる（毎回組み直すと7倍の時間がかかる）。"""
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    dayi = np.array([int(v) for v in z["DAYI"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = np.array([float(v) for v in z["AXIS_SUM"]])
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        nd = C.days_of(C.select(None, win))
        pre = {}          # i -> {plan_key: rec or None}
        for i in base:
            x = ctx(i)
            if x is None:
                continue
            tl = tp[i]
            if tl == "F":
                keys = ["F_hit", "F_pay", "F_sign"]
            elif tl == "A":
                keys = ["A_ana", "A_trio", "A_hit"]
            else:
                keys = [{"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]]
            pre[i] = {k: _run_named(x, k) for k in keys}
            if tl == "F":
                pre[i]["F_hyb"] = pre[i]["F_hit"] or _run_named(x, "_C15")
            pre[i]["_pw"] = x.shape.pw_ent
        print("")
        print("=" * 118)
        print(f"=== {label}  n={len(pre):,}R  日数={nd} ===")
        for mname, (sign_rt, hyb) in MENUS2.items():
            recs, recs_day = [], defaultdict(list)
            for i, d in pre.items():
                tl = tp[i]
                key = _plan_for(tl, rt[i], d["_pw"],
                                d.get("A_trio") is not None, sign_rt)
                if hyb and key == "F_hit":
                    key = "F_hyb"
                if axs[i] < AXIS_GATE_MIN.get("F_hit" if key == "F_hyb" else key, 0.0):
                    continue
                r = d.get(key)
                if not r:
                    continue
                recs.append(r)
                recs_day[min(int(dayi[i]), 3)].append(r)
            print("")
            print(f"  [{mname}]  {'区分':8s} {'件/日':>6s} {'表示的中%':>9s} {'ROI%':>7s} "
                  f"{'10万+/日':>8s} {'払戻中央':>9s}")
            for tag, rr in [("全体", recs)] + [(f"{d}日目", recs_day[d]) for d in (1, 2, 3)]:
                t = C.summarize(rr, nd)
                if not t.get("n"):
                    continue
                print(f"  {'':12s}  {tag:8s} {t['perday']:6.2f} {t['shown']:8.2f}% "
                      f"{t['roi']:7.1f} {t['big_per_day']:8.3f} {t['med_pay']:9,.0f}")


def marginal() -> None:
    """ハイブリッドで**新たに拾えたレースだけ**を見る（F_hit がゲートに落ちた側）。

    🔴 ハイブリッドは既存の行を1つも書き換えない（F_hit が組めたレースはそのまま）。
       ＝ 差の検定は不要で、問うべきは「**足したぶんの表示的中が体系の平均に見合うか**」。
    """
    import math
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    axs = np.array([float(v) for v in z["AXIS_SUM"]])
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = [int(i) for i in C.select("F", win)
                if axs[int(i)] >= AXIS_GATE_MIN["F_hit"]]
        nd = C.days_of(C.select(None, win))
        add = []
        for i in base:
            x = ctx(i)
            if x is None or run_arm(x, "F_hit 確率上位12点") is not None:
                continue
            r = run_arm(x, "帯15倍+ 12点(C式)")
            if r:
                r["g"] = G_OF.get(rt[i], "?")
                add.append(r)
        for tag in ["__ALL__"] + list(GROUPS):
            rr = add if tag == "__ALL__" else [r for r in add if r["g"] == tag]
            if len(rr) < 30:
                continue
            t = C.summarize(rr, nd)
            n, k = t["n"], t["shown"] / 100
            se = math.sqrt(k * (1 - k) / n) * 100
            print(f"{label}  {'追加ぶん合計' if tag=='__ALL__' else tag:14s} "
                  f"n={n:5d} {t['perday']:5.2f}件/日 表示的中 {t['shown']:5.2f}% "
                  f"[{k*100-1.96*se:5.2f},{k*100+1.96*se:5.2f}] ROI {t['roi']:5.1f}% "
                  f"払戻中央 {t['med_pay']:7,.0f}円")
        print("")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "desc"
    {"desc": desc, "plans": plans_cmd, "lineup": lineup, "marginal": marginal}[cmd]()
