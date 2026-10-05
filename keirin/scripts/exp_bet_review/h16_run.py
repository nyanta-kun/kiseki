#!/usr/bin/env python3
"""H16 本体: 3車目が絞れていれば三連複にする（事前登録どおり・掃引なし）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h16_run.py [limit]
腕（同一レース・1万円・入稿ゲート・軸信頼ゲート・日次上限・高額枠は本番関数のまま）:
  cur … 現行（三連単）
  a2  … hit 三連単プランの買い目が覆う3車の組が 3組以下 → その組を三連複で買う（予測三連複オッズでダッチ）。4組以上は現行
  a3  … a2 のうち、組ごとに 予測三連複オッズ >= その組で買う三連単の予測合成オッズ のときだけ三連複（他は三連単のまま）
  a3b … （参考）a3 で、各組の合計賭け金を現行のまま保つ版（配分の違いが結果を動かすかの確認）
対象プラン: 三連単の *_hit（A/B/C/E/F）。D_hit/A_trio は元から三連複。*_sign/*_big/A_ana/F_pay は触らない。
予測オッズ = 探索用 vintage（train_end 2024-12-31）。三連複予測 = 1/Σ(1/三連単予測)（本番 _fold_to_trio と同式）。
入稿ゲート（平均想定払戻>2万円 ∧ 全点>=2.0倍）を三連複の腕にも掛け、落ちたレースは見送り（その腕では売らない）。
"""
from __future__ import annotations
import pickle, sys, time
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL

HIT3 = frozenset({"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"})
MAX_GROUPS = 3
ARM = {"v": "cur"}
FINT3, FINTF = {}, {}


def load_final():
    """確定板（2025）。FINTF[key]={perm:odds}, FINT3[key]={frozenset:odds}。欠損は載せない。"""
    a = np.load(D / "h01_final_tf_2025.npz", allow_pickle=True)
    for k, row in zip(a["KEY"], a["FIN"]):
        FINTF[str(k)] = {PERMS[j]: float(v) for j, v in enumerate(row) if np.isfinite(v)}
    b = np.load(D / "h16_final_trio_2025.npz", allow_pickle=True)
    C3 = S.C3
    for k, row in zip(b["KEY"], b["FIN3"]):
        FINT3[str(k)] = {frozenset(C3[j]): float(v) for j, v in enumerate(row) if np.isfinite(v)}
INFO = {}     # (arm, race_key) -> dict


def prep():
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"])
    return z, np.flatnonzero(m)


def comp_odds(po, perms):
    return 1.0 / sum(1.0 / po[p] for p in perms)


def convert(x, stakes, arm):
    """三連単の stakes -> 腕に応じた stakes（キーは三連単=tuple / 三連複=frozenset）。変換不能なら None。"""
    groups = {}
    for p, s in stakes.items():
        groups.setdefault(frozenset(p), []).append(p)
    info = dict(ng=len(groups), conv=0)
    if len(groups) > MAX_GROUPS:
        return None, info
    keys = list(groups)
    if any(g not in x.po_t3 for g in keys):
        return None, info
    comp = {g: comp_odds(x.po_tf, groups[g]) for g in keys}
    if arm == "a2":
        convg = set(keys)
    elif arm == "a3o":
        # 参考（look-ahead・買えない）: 確定三連複 >= 確定側の合成 のときだけ
        f3, ftf = FINT3.get(x.key), FINTF.get(x.key)
        convg = set()
        if f3 is not None and ftf is not None:
            for g in keys:
                ps = groups[g]
                if g in f3 and all(p in ftf for p in ps):
                    if f3[g] >= comp_odds(ftf, ps) * (1 - 1e-9):
                        convg.add(g)
    else:
        # 浮動小数の丸めで「数学的に等しい」が落ちないよう相対 1e-9 の許容を持たせる
        convg = {g for g in keys if x.po_t3[g] >= comp[g] * (1 - 1e-9)}
    info["conv"] = len(convg)
    info["detail"] = [(tuple(sorted(g)), x.po_t3[g], comp[g], g in convg) for g in keys]
    if not convg:
        return None, info
    total = int(sum(stakes.values()))
    nu = total // 100
    if arm == "a3b":
        gst = {g: sum(stakes[p] for p in groups[g]) for g in keys}
        out = {}
        for g in keys:
            if g in convg:
                out[g] = gst[g]
            else:
                for p in groups[g]:
                    out[p] = stakes[p]
        return out, info
    eff = {g: (x.po_t3[g] if g in convg else comp[g]) for g in keys}
    w = [1.0 / eff[g] for g in keys]
    units = TL._proportional(w, nu)
    out = {}
    for g, u in zip(keys, units):
        gs = u * 100
        if gs <= 0:
            continue
        if g in convg:
            out[g] = gs
        else:
            ps = groups[g]
            uu = TL._proportional([stakes[p] for p in ps], u)
            for p, k in zip(ps, uu):
                if k > 0:
                    out[p] = k * 100
    return out, info


def build_h16(x, plan):
    odds, prob = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    got = TL.build_with_gate_fallback(
        x.shape, plan, odds, prob, 7,
        order_probs=None if plan.bet_type == "trio" else x.ord_tf)
    if not got:
        return None
    legs, stakes, used = got
    mean = TL.mean_expected_payout(stakes, odds)
    legs, stakes, _r = TL.add_upper_band(legs, stakes, used, odds, prob, 7)
    arm = ARM["v"]
    if arm != "cur" and used.key in HIT3 and used.bet_type == "trifecta":
        new, info = convert(x, stakes, arm)
        INFO[(arm, x.key, used.key)] = info
        if new is not None:
            allo = {**x.po_tf, **x.po_t3}
            mean2 = sum(new[c] * float(allo[c]) for c in new) / len(new)
            return new, allo, used, mean2
    return stakes, odds, used, mean


def settle_mixed(x, stakes, trio):
    inv = float(sum(stakes.values()))
    pay = 0.0
    for k, s in stakes.items():
        if isinstance(k, frozenset):
            if k == x.win_t3:
                pay += s * x.odds_t3
        elif k == x.win_tf:
            pay += s * x.pay_tf
    return inv, pay


def run_arm(name, ok, cache):
    ARM["v"] = name
    R.build = build_h16
    R.settle = settle_mixed
    return R.run(name, {}, ok, cache)


if __name__ == "__main__":
    t0 = time.time()
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    load_final()
    z, idx = prep()
    if lim:
        idx = idx[:lim]
    cache = {int(i): S.ctx(int(i)) for i in idx}
    ok = [int(i) for i in idx if cache.get(int(i)) is not None]
    ndays = len({cache[i].date for i in ok})
    print(f"対象 {len(idx)}R 組めた {len(ok)}R / {ndays}日 ({time.time()-t0:.0f}s)", flush=True)
    out = {"ndays": ndays}
    for arm in ("cur", "a2", "a3", "a3b", "a3o"):
        recs = run_arm(arm, ok, cache)
        out[arm] = recs
        print(arm, len(recs), f"({time.time()-t0:.0f}s)", flush=True)
    out["info"] = INFO
    out["keys"] = {i: cache[i].key for i in ok}
    pickle.dump(out, open(D / ("h16_recs%s.pkl" % (f"_{lim}" if lim else "")), "wb"))
