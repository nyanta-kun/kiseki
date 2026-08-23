#!/usr/bin/env python3
"""**構成の統一比較台（作り直し版）**（2026-08-23・§32）。

`exp_unified_compare.py` には**2つの欠陥**があり結論に使えなかった
（[[keirin_handoff_2026_08_23_pm]]「使ってはいけないもの」）。本版はそれを直す。

## 欠陥1: in-sample 混入 — 探索窓で学習し**同じ窓で**評価していた

旧版は `jm/pm/rm = fit(...S...)` の直後に `build_pool(S, ...)` で**同じ S を予測**
していた。モデル系の腕（`AX_PAIR` / `LG_JOINT`）だけが in-sample で持ち上がり、
探索窓で ROI 106〜109% と出て確認窓で崩れた。**選択そのものが壊れる**のが問題で、
表示が甘くなるだけの話ではない。

→ **探索窓は交差適合（out-of-fold）で予測する。**
🔴 **fold は「日」で交互に割る。** 年や前後で割ると片側の学習窓が消え、
   結局 in-sample のスコアで層を切ることになる
   （[[keirin_trio_joint_probability_2026_08_23]] で偽の改善 +2.43pt を出した型）。
🔴 レース型の3分割しきい値（配当の3分位）も**fold の学習側だけ**から決める。
   全 S から決めると、そのレースの配当が自分のブロック定義に混ざる。

確認窓は S 全体で学習して C を予測する（通常の hold-out）。

## 欠陥2: 母集団不揃い — 別のレース集合の数字を1つの表で並べていた

旧版の `evaluate` は構成ごとに**違うレースを落としていた**:

| 落ち方 | どの構成で起きるか |
|---|---|
| `len(sel) < RANK_7C_LEGS_MIN` で「買わない」 | `N_GAP` だけ |
| 盤面に無い買い目を除いた結果 0 点 | 点数・相手ルールで変わる |
| 軸が組めない | `AX_SWAP` |

それを1つの表に ROI 降順で並べ、`AX_P3/LG_P3/N_GAP`（＝7C の母集団）を
「現行相当」として横に置いていた。**分母のレースが違うので優劣を語れない。**

→ **2枚に分ける。混ぜない。**

| 面 | 母集団 | 答える問い |
|---|---|---|
| **A 対応比較** | **全構成が買えるレースだけ**（共通母集団） | 買い方として**どのルールが優れているか** |
| **B 体系値** | 和集合（**見送りは投資0として算入**） | ポートフォリオとして**1円あたり幾ら戻るか** |

🔴 **A は ROI を横に比べてよい。B は比べてはいけない**（件数が違うので、
   絞れば ROI は上がる）。B は事前登録の目的関数どおり
   **日次ROI・件数/日・投資/日・的中率をセットでしか読まない**。
🔴 選択は **A の「現行相当との差」＋日ブロック bootstrap CI** で行う。
   絶対 ROI の降順で選ぶと、試した構成の数だけ多重比較になる（旧版はこれ）。

## 欠陥3: `LG_JOINT` が軸を動かす構成で**黙って `LG_P3` に退化**していた

スモークで `AX_SWAP/LG_P3/N4` と `AX_SWAP/LG_JOINT/N4` が**完全に同じ数字**に
なって見つかった。原因は同時確率の出所が `build_A` だったこと:

    build_A は a1, a2 = order[0], order[1]（**p3 上位2車**）で固定して行を作る

したがって辞書のキーは p3 上位2軸のものしか無く、`AX_PAIR` / `AX_SWAP` では
**全件が lookup に失敗**して `p3 * 1e-6` のフォールバック＝p3 降順へ落ちる。
36構成のうち **24構成が実質 12構成の重複**だった（エラーも警告も出ない）。

→ **相手の並べ替えは `build_B`（三連複の組み合わせモデル・§24 腕B の学習器）で
統一する。** これは `frozenset(3車)` をキーにするのでどの軸でも引ける。
🔴 フォールバックは残すが、**発生率を必ず出す**（黙って退化させない）。

## そのほか直したこと

- 点数下限を `RANK_7C_LEGS_MIN` から取る（旧版は `4` をベタ書き）
- `n < 300` の暗黙の足切りをやめ、**件数を出したうえで印を付ける**
- p3 の出所を **vintage（`wf_preds_*.pkl`）既定**にした（§31）。
  ⚠️ vintage の被覆は 2024-07〜 なので**探索窓は 2024-07-01〜2025-12-31 に短くなる**
  （事前登録は 2024-01〜。短くなる方向なので窓の汚染は増えない）。
  `--source backfill` で旧来の窓に戻せるが、絶対水準が約2pt甘い

## 手順（事前登録どおり）

1. 探索窓で全構成を出し、**A 面の差**でブロックごとに最良を選ぶ
2. 確認窓で、**選んだ構成だけ**を当てる
3. 封印窓 2026-07-01〜08-22 は読まない
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import build as build_race  # noqa: E402
from scripts.exp_axis1_bust_stratified import load_rich  # noqa: E402
from scripts.exp_race_regime_3class import NAMES, fit_multi, label_P  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    build_B, fit, load_any, load_boards, load_entries)
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN, rank_7c_cut_legs_by_gap, rank_7c_select_legs, unit_stake)

SEARCH_END = "2025-12-31"
CONFIRM_START, CONFIRM_END = "2026-01-01", "2026-06-30"
SEALED_START = "2026-07-01"
PAYOUT_RATE = 0.7485
BASE = "AX_P3/LG_P3/N_GAP"          # 現行相当（7C の形）
AXES = ["AX_P3", "AX_PAIR", "AX_SWAP"]
LEGS = ["LG_P3", "LG_JOINT"]
NPTS = ["N1", "N2", "N3", "N4", "N5", "N_GAP"]
# 🔴 相手の**並べ替え**が買い目を変えない点数ルール。相手5車のうち
#    N5 は全部買い、N_GAP は p3 で決まる集合を買うので、どちらも順序不変。
#    ここを外さないと 36構成のうち6組が完全な重複になり、
#    「多重比較の数」を実際より多く数えてしまう。
ORDER_FREE = {"N5", "N_GAP"}


def all_configs():
    """相異なる構成だけを返す（順序不変の点数ルールに LG_JOINT を作らない）。"""
    return [f"{a}/{lg}/{n}" for a in AXES for lg in LEGS for n in NPTS
            if not (n in ORDER_FREE and lg != "LG_P3")]

SRC = {
    "vintage": ("data/exp/trio7_cache_wf_train.jsonl",
                "data/exp/trio7_cache_wf_test.jsonl"),
    "backfill": ("data/exp/trio_rank_cache.jsonl",
                 "data/exp/tf_shape_cache4.jsonl"),
}


# ───────────────────────── 交差適合（欠陥1の修正） ─────────────────────────

def day_folds(rows, k):
    """🔴 fold は**日で交互に**割る。年や前後で割ってはいけない。"""
    days = sorted({r["date"] for r in rows})
    return {d: i % k for i, d in enumerate(days)}


def _mean_pay(key, fins, board):
    o3 = fins.get(key); b = board.get(key)
    if not o3 or not b:
        return None
    v = [b[k] for k in {frozenset(w) for w in winning_trifectas(o3)} if k in b]
    return float(np.mean(v)) if v else None


def _predict(train_rows, pred_rows, ent_p_tr, ent_p_pr, ent_r_tr, ent_r_pr,
             fin_tr, fin_pr, board_tr, rounds):
    """train_rows で学習し pred_rows を予測して (joint, pair, blk, cuts) を返す。"""
    Xa, ya, _ = build_B(train_rows, ent_p_tr, fin_tr)
    jm = fit(Xa, ya, rounds)
    Xp, yp, _ = build_pairs(train_rows, ent_p_tr, fin_tr)
    pm = fit(Xp, yp, rounds)

    # レース型: しきい値も**学習側だけ**から決める
    _, _, _, Xr_tr, _, mr_tr = build_race(train_rows, ent_r_tr, fin_tr)
    pays = np.array([_mean_pay(m[0], fin_tr, board_tr) or np.nan for m in mr_tr])
    ok = ~np.isnan(pays)
    cuts = tuple(np.quantile(pays[ok], [1 / 3, 2 / 3]))
    y = np.array([label_P(p, cuts) if o else 0 for p, o in zip(pays, ok)])
    rm = fit_multi(Xr_tr[ok], y[ok], 500)

    Xa2, _, ma2 = build_B(pred_rows, ent_p_pr, fin_pr)
    joint = defaultdict(dict)
    for (key, _d, combo), p in zip(ma2, jm.predict(Xa2)):
        joint[key][combo] = float(p)
    Xp2, _, mp2 = build_pairs(pred_rows, ent_p_pr, fin_pr)
    pair = defaultdict(dict)
    for (key, _d, a, b, *_), p in zip(mp2, pm.predict(Xp2)):
        pair[key][frozenset((a, b))] = float(p)
    _, _, _, Xr2, _, mr2 = build_race(pred_rows, ent_r_pr, fin_pr)
    blk = {m[0]: int(k) for m, k in zip(mr2, rm.predict(Xr2).argmax(1))}
    return joint, pair, blk, cuts


def oof_predict(rows, ent_p, ent_r, fins, board, rounds, k):
    """探索窓の out-of-fold 予測。**同じレースを学習にも評価にも使わない。**"""
    fold = day_folds(rows, k)
    joint, pair, blk = {}, {}, {}
    for f in range(k):
        tr = [r for r in rows if fold[r["date"]] != f]
        pr = [r for r in rows if fold[r["date"]] == f]
        if not pr:
            continue
        j, p, b, cuts = _predict(tr, pr, ent_p, ent_p, ent_r, ent_r,
                                 fins, fins, board, rounds)
        joint.update(j); pair.update(p); blk.update(b)
        print(f"  fold {f}: 学習 {len(tr):,}R → 予測 {len(pr):,}R "
              f"（配当しきい値 {cuts[0]:.1f} / {cuts[1]:.1f}倍）", flush=True)
    return joint, pair, blk


# ───────────────────────────── プールと買い方 ─────────────────────────────

def build_pool(rows, fins, board, joint, pair, blk):
    out = []
    for r in rows:
        key = r["key"]
        o3 = fins.get(key); b = board.get(key)
        if not o3 or not b or key not in blk or key not in pair:
            continue
        out.append(dict(key=key, date=r["date"], o=r["order"], p3=r["p3"],
                        wins={frozenset(w) for w in winning_trifectas(o3)},
                        board=b, joint=joint.get(key, {}),
                        pair=pair[key], blk=blk[key]))
    return out


def axes_of(r, rule):
    o = r["o"]
    if rule == "AX_P3":
        return o[0], o[1]
    if rule == "AX_PAIR":
        best = max(r["pair"].items(), key=lambda kv: kv[1])[0]
        a, b = sorted(best, key=lambda c: -r["p3"][c])
        return a, b
    a1 = o[1]                                   # AX_SWAP
    cand = [c for c in o if c not in (o[0], o[1])]
    if not cand:
        return None
    return a1, max(cand, key=lambda c: r["pair"].get(frozenset((a1, c)), 0.0))


FALLBACK = [0, 0]      # [フォールバック件数, 参照件数]（黙って退化させないため）


def legs_of(r, a1, a2, rule):
    rest = [c for c in r["o"] if c not in (a1, a2)]
    if rule == "LG_P3":
        return sorted(rest, key=lambda c: (-r["p3"][c], c))
    j = r["joint"]
    # 🔴 キーは frozenset(3車)。`build_A` 由来の (a1,a2,c) にすると軸を動かす構成で
    #    全件 lookup に失敗し、警告も出さずに p3 降順へ退化する（欠陥3）。
    FALLBACK[1] += len(rest)
    FALLBACK[0] += sum(1 for c in rest if frozenset((a1, a2, c)) not in j)
    return sorted(rest,
                  key=lambda c: -j.get(frozenset((a1, a2, c)), r["p3"][c] * 1e-6))


def npts_of(r, a1, a2, legs, rule):
    if rule[1:].isdigit():
        return legs[:int(rule[1:])]
    rest = [c for c in r["o"] if c not in (a1, a2)]
    sel = rank_7c_select_legs(rest, r["p3"])
    if len(sel) < RANK_7C_LEGS_MIN:
        return []                               # 7C はこのレースを買わない
    keep = set(rank_7c_cut_legs_by_gap(sel, r["p3"]))
    return [c for c in legs if c in keep]


def bet_of(r, cfg):
    """→ (hit, pay, stake) / 買わないなら None。"""
    ax, lg, np_ = cfg.split("/")
    a = axes_of(r, ax)
    if a is None:
        return None
    a1, a2 = a
    legs = npts_of(r, a1, a2, legs_of(r, a1, a2, lg), np_)
    ks = [frozenset((a1, a2, c)) for c in legs]
    if not ks or any(k not in r["board"] for k in ks):
        # 🔴 一部だけ盤面に無い場合も**買わない**扱いにする。
        #    旧版は在る目だけ買って点数を変えており、構成ごとに別の商品になっていた。
        return None
    st = unit_stake(len(ks))
    pay = sum(int(r["board"][k] * 100) * st // 100 for k in ks if k in r["wins"])
    return int(any(k in r["wins"] for k in ks)), pay, len(ks) * st


# ───────────────────────────── 集計と検定 ─────────────────────────────

def paired_ci(days, B=3000, seed=5):
    """days={date: [bet, pay_ref, pay_alt]} → ROI差の95%CI。"""
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def system_ci(days, B=3000, seed=11):
    """days={date: [bet_new, pay_new, bet_cur, pay_cur]} → 体系ROI差の95%CI。

    🔴 件数が違う体系どうしなので**レース対応は取れない**。日を単位に
       Σ払戻÷Σ投資 をそれぞれ作って差を取る（事前登録 §6-1 の「日ブロック」）。
    """
    v = np.array([[d[0], d[1], d[2], d[3]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    new = v[idx, 1].sum(1) / np.maximum(v[idx, 0].sum(1), 1)
    cur = v[idx, 3].sum(1) / np.maximum(v[idx, 2].sum(1), 1)
    d = np.sort(new - cur)
    return d[int(B * .025)], d[int(B * .975)]


def summarize(bets, n_days):
    """bets=[(date, hit, pay, stake)] → 集計。"""
    if not bets:
        return None
    bet = sum(x[3] for x in bets); pay = sum(x[2] for x in bets)
    return dict(n=len(bets), per_day=len(bets) / n_days, roi=pay / bet,
                hit=sum(x[1] for x in bets) / len(bets), inv_day=bet / n_days,
                bet=bet, pay=pay)


def run_block(pool, cfgs, blk, n_days, label, top=8):
    """A面（共通母集団の対応比較）と B面（実母集団の体系値）を出す。"""
    races = [r for r in pool if r["blk"] == blk]
    made = {c: {} for c in cfgs}
    for r in races:
        for c in cfgs:
            b = bet_of(r, c)
            if b is not None:
                made[c][r["key"]] = (r["date"], *b)
    common = set.intersection(*[set(m) for m in made.values()]) if made else set()
    print(f"===== {label}「{NAMES[blk]}」 {len(races):,}R "
          f"→ 共通母集団 {len(common):,}R ({len(common)/max(len(races),1):.0%}) =====")
    if not common:
        print("  共通母集団が空。比較できない。\n")
        return None

    # ── A面: 共通母集団の対応比較（ROI を横に比べてよい唯一の面）──
    def seg(c, keys):
        return [made[c][k] for k in keys]
    ref = seg(BASE, common)
    rows = []
    for c in cfgs:
        s = seg(c, common)
        a = summarize(s, n_days)
        dd = defaultdict(lambda: [0.0, 0.0, 0.0])
        for (d, _h, p, b), (_d2, _h2, p2, _b2) in zip(s, ref):
            z = dd[d]; z[0] += b; z[1] += p2; z[2] += p
        lo, hi = paired_ci(dd)
        rows.append((c, a, lo, hi))
    base_a = next(x[1] for x in rows if x[0] == BASE)
    rows.sort(key=lambda x: -(x[1]["roi"] - base_a["roi"]))
    print(f"  [A 対応比較・共通母集団 {len(common):,}R] "
          f"— ROI は横に比べてよい／差は現行相当との対応比較")
    print(f"  {'構成':>22}{'的中%':>8}{'ROI':>8}{'ROI差':>24}")
    for c, a, lo, hi in rows[:top]:
        if c == BASE:
            continue
        flag = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        d = (a["roi"] - base_a["roi"]) * 100
        diff = f"{d:+.1f}pt[{lo * 100:+.1f},{hi * 100:+.1f}]{flag}"
        print(f"  {c:>22}{a['hit']:>8.1%}{a['roi']:>8.1%}{diff:>24}")
    print(f"  {'（現行相当）' + BASE:>22}{base_a['hit']:>8.1%}{base_a['roi']:>8.1%}")

    # ── B面: 実母集団（見送りは投資0）──
    print(f"\n  [B 体系値・実母集団] — 🔴 **ROI を横に比べない**"
          f"（件数が違う。絞れば上がる）")
    print(f"  {'構成':>22}{'選出率':>8}{'件/日':>8}{'投資/日':>10}{'的中%':>8}{'ROI':>8}")
    for c, _a, _lo, _hi in rows[:top] + [(BASE, None, None, None)]:
        s = list(made[c].values())
        a = summarize(s, n_days)
        if not a:
            continue
        tag = "（現行相当）" if c == BASE else ""
        print(f"  {tag+c:>22}{len(s)/len(races):>8.0%}{a['per_day']:>8.2f}"
              f"{a['inv_day']:>10,.0f}{a['hit']:>8.1%}{a['roi']:>8.1%}")
    print()
    # 選択は A面の差（現行相当より有意に上）で行う。無ければ現行相当を残す。
    win = [x for x in rows if x[0] != BASE and x[2] > 0]
    return (win[0][0] if win else BASE), len(cfgs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SRC), default="vintage")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--smoke", type=int, default=0,
                    help="各窓を先頭N日だけに絞って動作確認する（結論には使わない）")
    args = ap.parse_args()

    ca, cb = SRC[args.source]
    allr = load_any(ca) + load_any(cb)
    S = [r for r in allr if r["date"] <= SEARCH_END]
    C = [r for r in allr if CONFIRM_START <= r["date"] <= CONFIRM_END]
    if args.smoke:
        ds = sorted({r["date"] for r in S})[:args.smoke]
        dc = sorted({r["date"] for r in C})[:args.smoke]
        S = [r for r in S if r["date"] in set(ds)]
        C = [r for r in C if r["date"] in set(dc)]
        print(f"⚠️ SMOKE モード（各窓 先頭{args.smoke}日）— 結論には使わない")
    print(f"p3 の出所: {args.source}")
    print(f"探索 {len(S):,}R（{min(r['date'] for r in S)}〜{max(r['date'] for r in S)}）"
          f" / 確認 {len(C):,}R（{CONFIRM_START}〜{CONFIRM_END}）"
          f"   🔒 封印 {SEALED_START}〜 は読まない\n")

    ks, kc = [r["key"] for r in S], [r["key"] for r in C]
    ep_s, ep_c = load_entries(ks), load_entries(kc)
    er_s, er_c = load_rich(ks), load_rich(kc)
    fs, fc = _load_finishes(ks), _load_finishes(kc)
    bs, bc = load_boards(ks), load_boards(kc)

    print(f"[探索窓] 交差適合 {args.folds} fold（日で交互に割る）")
    js, ps, bls = oof_predict(S, ep_s, er_s, fs, bs, args.rounds, args.folds)
    print("[確認窓] 探索窓 全体で学習して確認窓を予測", flush=True)
    jc, pc, blc, _ = _predict(S, C, ep_s, ep_c, er_s, er_c, fs, fc, bs, args.rounds)

    PS = build_pool(S, fs, bs, js, ps, bls)
    PC = build_pool(C, fc, bc, jc, pc, blc)
    dS = len({r["date"] for r in PS}); dC = len({r["date"] for r in PC})
    print(f"\nプール 探索 {len(PS):,}R/{dS}日 ・ 確認 {len(PC):,}R/{dC}日\n")

    cfgs = all_configs()
    best = {}
    for blk in range(3):
        got = run_block(PS, cfgs, blk, dS, "[探索]")
        if got:
            best[blk], n_cfg = got
    fb = FALLBACK[0] / max(FALLBACK[1], 1)
    print(f"[LG_JOINT] 同時確率が引けず p3 へフォールバックした割合: {fb:.3%}"
          + ("  🔴 高すぎる。モデルの被覆を確認すること" if fb > 0.01 else "  🟢"))
    print(f"⚠️ 1ブロックあたり {len(cfgs)} 構成を比べている（多重比較）。"
          f"探索窓の🟢は確認窓で当てるまで結論にしない。\n")

    print("===== [確認] 探索窓で選んだ構成だけを当てる =====")
    print(f"{'ブロック':>10}{'構成':>22}{'選出率':>8}{'件/日':>8}{'投資/日':>10}"
          f"{'的中%':>8}{'ROI':>8}{'ROI差(vs現行)':>26}")
    tot, cur = [], []
    for blk in range(3):
        c = best.get(blk)
        if not c:
            continue
        races = [r for r in PC if r["blk"] == blk]
        date_of = {r["key"]: r["date"] for r in races}
        mk = {r["key"]: bet_of(r, c) for r in races}
        mb = {r["key"]: bet_of(r, BASE) for r in races}
        both = [k for k in mk if mk[k] and mb.get(k)]
        s = [(date_of[k], *mk[k]) for k in mk if mk[k]]
        a = summarize(s, dC)
        dd = defaultdict(lambda: [0.0, 0.0, 0.0])
        for k in both:
            z = dd[date_of[k]]
            z[0] += mk[k][2]; z[1] += mb[k][1]; z[2] += mk[k][1]
        lo, hi = paired_ci(dd) if dd else (0.0, 0.0)
        ab = summarize([(0, *mb[k]) for k in both], dC)
        aa = summarize([(0, *mk[k]) for k in both], dC)
        flag = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        d = (aa["roi"] - ab["roi"]) * 100
        diff = f"{d:+.1f}pt[{lo * 100:+.1f},{hi * 100:+.1f}]{flag}"
        rate = len(s) / max(len(races), 1)
        print(f"{NAMES[blk]:>10}{c:>22}{rate:>8.0%}"
              f"{a['per_day']:>8.2f}{a['inv_day']:>10,.0f}{a['hit']:>8.1%}"
              f"{a['roi']:>8.1%}{diff:>26}")
        tot += s
        cur += [(date_of[k], *mb[k]) for k in mb if mb[k]]
    an, ac = summarize(tot, dC), summarize(cur, dC)
    for nm, a in (("体系（ブロック別最良）", an), ("現行相当（全ブロック一律）", ac)):
        if a:
            print(f"\n  {nm}: {a['per_day']:.2f}件/日 ・ 投資 {a['inv_day']:,.0f}円/日"
                  f" ・ 的中 {a['hit']:.2%} ・ ROI {a['roi']:.1%}")
    if an and ac:
        days = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        for d, _h, pay, bet in tot:
            z = days[d]; z[0] += bet; z[1] += pay
        for d, _h, pay, bet in cur:
            z = days[d]; z[2] += bet; z[3] += pay
        lo, hi = system_ci(days)
        d_roi = (an["roi"] - ac["roi"]) * 100
        d_hit = (an["hit"] - ac["hit"]) * 100
        ratio = an["n"] / max(ac["n"], 1)
        print(f"\n  体系ROI差 {d_roi:+.1f}pt [{lo * 100:+.1f},{hi * 100:+.1f}]"
              f" ・ 的中差 {d_hit:+.2f}pt ・ 件数比 {ratio:.2f}倍")
        print("\n  === 採否ライン（事前登録 §6・事後に動かさない）===")
        for ok, txt in (
                (lo > 0, f"1 体系ROI差の CI下限 > 0（実測 {lo * 100:+.1f}pt）"),
                (d_hit >= -1.0, f"2 的中率が現行 −1.0pt 以内（実測 {d_hit:+.2f}pt）"),
                (ratio >= 0.8, f"3 件数が現行の 0.8倍以上（実測 {ratio:.2f}倍）"),
                (None, "4 探索窓と確認窓で符号一致（上の A面と見比べて人が判定）")):
            mark = "—" if ok is None else ("✅" if ok else "❌")
            print(f"    {mark} {txt}")
    print("\n🔴 体系どうしの ROI は件数が違えば直接比べられない。"
          "採否は事前登録 §6 の4条件で判定すること。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
