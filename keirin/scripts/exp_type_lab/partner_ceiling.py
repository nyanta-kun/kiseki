#!/usr/bin/env python3
"""「相手（3着目）」に残っている伸び幅 — オラクル天井と学習モデル（2026-09-14）。

`order_ceiling_2026_09_11.md`（順序側）と同じ形で**相手側**を測る。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/partner_ceiling.py <section>

section:
    anatomy  台の再現（miss_anatomy §1）
    oracle   相手オラクル / 順序オラクル / 両方 の天井（同じ台・同じ母集団）
    build    候補特徴のキャッシュ /tmp/pc_feats.npz を作る

学習・商品KPI は `pc_fast.py`（このキャッシュを読む高速版）。

🔴 学習は必ず「対象レースより過去だけ」。
    fold B（本命）: train = explore(2024-07〜2025-12) / eval = confirm(2026-01〜08)
    fold A（対照窓）: train = 2024-07〜2025-06 / eval = 2025-07〜12
    explore 全体は学習に使うので評価に使えない（order_ceiling §8 と同じ制約）。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.type_lab import (  # noqa: E402
    PLANS, RaceShape, allocate, build_legs, mean_expected_payout)

MINM, MINP = 20_000, 2.0
ROWS = None
FOLD_A_TRAIN_END = "2025-06-30"
STYLES = ("逃", "追", "両", "自", "差", "捲")


def load():
    global ROWS
    if ROWS is None:
        with open("/tmp/ratebranch/rows.pkl", "rb") as f:
            ROWS = pickle.load(f)
    return ROWS


def shape_of(r):
    order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
    return RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                     False, order, float(r["pw_ent"]))


def base_bet(r, prb=None):
    """本番と同じ経路で買い目・賭け金を組む。ゲートに落ちたら None。"""
    plan = PLANS[r["plan"]]
    pod, prb0 = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
    prb = prb0 if prb is None else prb
    legs = build_legs(shape_of(r), plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MINM or min(float(pod[c]) for c in st) < MINP:
        return None
    return st


def payout(r, st):
    if st is None:
        return None
    if r["trio"]:
        p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
    else:
        p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
    return float(sum(st.values())), p


def kpi(recs):
    """recs: list[(inv, pay)]。件数・表示的中・ROI・払戻中央・10万+件数。"""
    if not recs:
        return dict(n=0)
    inv = np.array([a for a, _ in recs], float)
    pay = np.array([b for _, b in recs], float)
    hits = pay[pay > 0]
    return dict(n=len(recs), shown=float((pay >= inv).mean() * 100),
                hit=float((pay > 0).mean() * 100),
                roi=float(pay.sum() / inv.sum() * 100),
                med=float(np.median(hits)) if len(hits) else 0.0,
                big=int((pay >= 100_000).sum()))


def boot_ci(a, b, n=2000, seed=0):
    """同一レース対比較の Δ表示的中（pt）の 95%CI。a,b は 0/1 の配列。"""
    a = np.asarray(a, float); b = np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return float((b.mean() - a.mean()) * 100), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


# ───────────────────────────── §1 台の再現 ─────────────────────────────

def anatomy():
    rows = load()
    for w in ("confirm", "explore"):
        rs = [r for r in rows if r["win"] == w]
        n = len(rs)
        a = [sum(1 for r in rs if r["hit"]),
             sum(1 for r in rs if not r["hit"] and r["set_hit"]),
             sum(1 for r in rs if not r["hit"] and not r["set_hit"] and r["both_in3"]),
             sum(1 for r in rs if not r["hit"] and not r["set_hit"] and not r["both_in3"])]
        print(f"{w:8s} n={n:,}  ①的中 {a[0]/n*100:.2f} ②順序違い {a[1]/n*100:.2f} "
              f"③相手外し {a[2]/n*100:.2f} ④軸崩壊 {a[3]/n*100:.2f}  "
              f"表示的中 {sum(r['shown'] for r in rs)/n*100:.2f}%")


# ───────────────────────────── §2 オラクル天井 ─────────────────────────────

def _band_ok(plan, o):
    o = float(o)
    return (o >= MINP and o >= (plan.min_odds or 0.0)
            and (not plan.max_odds or o <= plan.max_odds))


def _swap(r, st, target, prefer_drop=None):
    """買い目の1点を `target` へ入れ替えて再配分。帯・ゲートを守れなければ None。"""
    plan = PLANS[r["plan"]]
    pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
    if target in st:
        return st
    o = pod.get(target)
    if o is None or not _band_ok(plan, o):
        return None
    legs = list(st)
    pool = [c for c in legs if prefer_drop is None or c in prefer_drop] or legs
    drop = min(pool, key=lambda c: float(prb.get(c, 0.0)))
    new = [target if c == drop else c for c in legs]
    st2 = allocate(new, pod, prb, plan)
    if not st2:
        return None
    if mean_expected_payout(st2, pod) <= MINM or min(float(pod[c]) for c in st2) < MINP:
        return None
    return st2


def _best_perm_of_set(r, S):
    """集合 S のうち、現行モデルが最も高く置く並び（帯を通るものの中から）。"""
    plan = PLANS[r["plan"]]
    pod, prb = r["po_tf"], r["pr_tf"]
    cand = [p for p in itertools.permutations(sorted(S))
            if pod.get(p) is not None and _band_ok(plan, pod[p])]
    if not cand:
        return None
    return max(cand, key=lambda p: float(prb.get(p, 0.0)))


def _oracle_row(r, st, mode):
    """mode: 'partner_set' | 'partner_order' | 'order' | 'both'"""
    S = frozenset(r["fin"])
    fixed = st
    miss_partner = r["both_in3"] and not r["set_hit"]
    miss_order = (not r["trio"]) and r["set_hit"] and not r["hit"]
    if mode in ("partner_set", "partner_order", "both") and miss_partner:
        if r["trio"]:
            t = S
        elif mode == "partner_set":
            t = _best_perm_of_set(r, S)
        else:
            t = r["fin"]
        if t is not None:
            st2 = _swap(r, st, t)
            if st2 is not None:
                fixed = st2
    if mode in ("order", "both") and miss_order:
        keep = {c for c in st if frozenset(c) == S}
        st2 = _swap(r, st, r["fin"], prefer_drop=keep)
        if st2 is not None:
            fixed = st2
    return fixed


def oracle():
    rows = load()
    modes = [("現行", None),
             ("相手オラクル（集合だけ・順序は現行モデル）", "partner_set"),
             ("相手オラクル（集合＋順序）", "partner_order"),
             ("順序オラクル（order_ceiling と同型）", "order"),
             ("相手＋順序（両方）", "both")]
    print("§2 オラクル天井（ラインナップ全体・入稿ゲートと帯を守ったまま1点だけ入れ替える）\n")
    for w in ("confirm", "explore"):
        rs = [r for r in rows if r["win"] == w]
        days = len({r["date"] for r in rs})
        base_st = {}
        out = {}
        for name, mode in modes:
            recs, sh = [], []
            fix = 0
            for j, r in enumerate(rs):
                st = base_st.get(j)
                if st is None:
                    st = base_bet(r)
                    base_st[j] = st
                if st is None:
                    continue
                st2 = st if mode is None else _oracle_row(r, st, mode)
                fix += (st2 is not st)
                iv, py = payout(r, st2)
                recs.append((iv, py))
                sh.append(py >= iv)
            out[name] = (kpi(recs), np.array(sh, float), fix)
        b = out["現行"][1]
        print(f"--- {w}  n={out['現行'][0]['n']:,}商品・営業日{days} ---")
        print(f"{'腕':42s}{'件/日':>7}{'表示的中':>9}{'的中':>8}{'払戻中央':>10}"
              f"{'10万+/日':>9}{'ROI':>7}{'直せた':>8}  Δ表示的中 95%CI")
        for name, _ in modes:
            k, sh, fix = out[name]
            d = "" if name == "現行" else "  {:+.2f} [{:+.2f},{:+.2f}]".format(*boot_ci(b, sh))
            print(f"{name:42s}{k['n']/days:7.2f}{k['shown']:8.2f}%{k['hit']:7.2f}%"
                  f"{k['med']:10,.0f}{k['big']/days:9.3f}{k['roi']:7.1f}{fix:8d}{d}")
        print()

    # 到達可能性の分解
    print("§2.2 相手外し（軸2車そろい ∧ 集合を買えていない）の内訳\n")
    for w in ("confirm", "explore"):
        rs = [r for r in rows if r["win"] == w]
        tot = mp = band = gate = 0
        for r in rs:
            st = base_bet(r)
            if st is None:
                continue
            tot += 1
            if not (r["both_in3"] and not r["set_hit"]):
                continue
            mp += 1
            S = frozenset(r["fin"])
            t = S if r["trio"] else _best_perm_of_set(r, S)
            if t is None:
                continue
            band += 1
            if _swap(r, st, t) is not None:
                gate += 1
        print(f"{w:8s} 商品 {tot:,} / 相手外し {mp:,}（{mp/tot*100:.2f}%）"
              f" / 帯を通る {band:,}（{band/mp*100:.1f}%）"
              f" / 入れ替えてもゲートを通る {gate:,}（{gate/mp*100:.1f}%）")


# ───────────────────────────── §3 学習モデル ─────────────────────────────

def _mkt_tf(r):
    s = np.zeros(7)
    for c, o in r["po_tf"].items():
        if o > 0:
            for car in c:
                s[car - 1] = max(s[car - 1], 1.0 / float(o))
    return s


def _line_score(r):
    lg = r["lg"]
    tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
    return np.array([(1.0 if lg[c] in tgt else 0.0) + 1e-6 * r["p3vec"][c] for c in range(7)])


def cands(r):
    return [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]


FEAT_NAMES: list[str] = []


def feats(r):
    """候補5車ぶんの特徴行列（5, F）。point-in-time 安全な列のみ。"""
    global FEAT_NAMES
    cs = cands(r)
    a1, a2 = r["a1"], r["a2"]
    lg, lpos, lsz, llead = r["lg"], r["lpos"], r["lsize"], r["llead"]
    p3, pw, rp, rate = r["p3vec"], r["pwvec"], r["rp"], r["rate"]
    mtf = _mkt_tf(r)
    tgt = {lg[a1 - 1], lg[a2 - 1]}
    sum_p3 = float(sum(p3[c - 1] for c in cs)) or 1.0
    mt3 = {}
    for c in cs:
        o = r["po_t3"].get(frozenset({a1, a2, c}))
        mt3[c] = (1.0 / float(o)) if o and float(o) > 0 else np.nan
    s_mt3 = np.nansum(list(mt3.values())) or 1.0
    pr3 = {c: float(r["pr_t3"].get(frozenset({a1, a2, c}), np.nan)) for c in cs}
    s_pr3 = np.nansum(list(pr3.values())) or 1.0
    n_axis_line = sum(1 for c in cs if lg[c - 1] in tgt)
    rows = []
    for j, c in enumerate(cs):
        i = c - 1
        f = {
            # モデル出力（既に読んでいる側）
            "p3": float(p3[i]), "p3_rank": float(j), "p3_share": float(p3[i]) / sum_p3,
            "p3_gap_top": float(p3[cs[0] - 1] - p3[i]),
            "pw": float(pw[i]), "pw_rank": float(np.argsort(np.argsort(-pw))[i]),
            "pr_t3_share": pr3[c] / s_pr3,
            # 市場（モデルに入っていない）
            "mkt_t3_share": mt3[c] / s_mt3, "mkt_t3": mt3[c], "mkt_tf": float(mtf[i]),
            "mkt_rank": float(sorted(cs, key=lambda x: -(mt3[x] if mt3[x] == mt3[x] else -1)).index(c)),
            # ライン内部構造
            "same_line_a1": float(lg[i] == lg[a1 - 1]), "same_line_a2": float(lg[i] == lg[a2 - 1]),
            "same_line_any": float(lg[i] in tgt),
            "lpos": float(lpos[i]), "lsize": float(lsz[i]), "llead": float(llead[i]),
            "lpos_minus_a1": float(lpos[i] - lpos[a1 - 1]),
            "is_solo": float(lsz[i] == 1),
            "n_cand_in_axis_line": float(n_axis_line),
            "axis_line_size": float(lsz[a1 - 1]),
            "axes_same_line": float(lg[a1 - 1] == lg[a2 - 1]),
            "nlines": float(r["nlines"]),
            # 脚質
            "style": float(STYLES.index(str(r["style"][i])) if str(r["style"][i]) in STYLES else -1),
            "style_a1": float(STYLES.index(str(r["style"][a1 - 1]))
                              if str(r["style"][a1 - 1]) in STYLES else -1),
            # 素の力量（すべて FEATURE_COLS_WT 済み）
            "rp": float(rp[i]), "rp_z": float((rp[i] - rp.mean()) / (rp.std() + 1e-9)),
            "rate1": float(rate[i, 0]), "rate2": float(rate[i, 1]), "rate3": float(rate[i, 2]),
            "rate3_z": float((rate[i, 2] - rate[:, 2].mean()) / (rate[:, 2].std() + 1e-9)),
            "rate3_minus_axis": float(rate[i, 2] - (rate[a1 - 1, 2] + rate[a2 - 1, 2]) / 2),
            # レース文脈
            "axis_sum": float(r["axis"]), "gap": float(r["gap"]), "arare": float(r["arare"]),
            "pw_ent": float(r["pw_ent"]), "rp_sd": float(r["rp_sd"]),
            "agree": float(r["agree"]),
            "type": float("ABCDEF".index(str(r["type"]))),
        }
        if not FEAT_NAMES:
            FEAT_NAMES = list(f)
        rows.append([f[k] for k in FEAT_NAMES])
    return cs, np.array(rows, np.float32)


CACHE = "/tmp/pc_feats.npz"


def build_cache():
    """全行ぶんの候補特徴を1度だけ作って保存（以降の節はこれを読む）。"""
    rows = load()
    X = np.zeros((len(rows), 5, 0), np.float32)
    mats, cands_all, lab = [], [], []
    for j, r in enumerate(rows):
        if j % 5000 == 0:
            print(f"  {j:,}/{len(rows):,}", flush=True)
        cs, F = feats(r)
        mats.append(F)
        cands_all.append(cs)
        lab.append(cs.index(r["third_car"]) if r["third_car"] in cs else -1)
    np.savez_compressed(CACHE, X=np.stack(mats), C=np.array(cands_all, np.int8),
                        y=np.array(lab, np.int8),
                        names=np.array(FEAT_NAMES),
                        win=np.array([r["win"] for r in rows]),
                        date=np.array([r["date"] for r in rows]))
    print(f"保存 {CACHE}  {len(rows):,}行 × 5候補 × {len(FEAT_NAMES)}特徴")


if __name__ == "__main__":
    {"anatomy": anatomy, "oracle": oracle, "build": build_cache}[sys.argv[1]]()
