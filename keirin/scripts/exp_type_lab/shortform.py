#!/usr/bin/env python3
"""短期（直近N走）の脚質・調子は相手選定／軸崩壊の事前検知に使えるか（2026-09-14）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/shortform.py <section>

section: leak | desc | resid | auc | partner | product

台: /tmp/ratebranch/rows.pkl（27,799商品）+ /tmp/shortform/feat.pkl（47本の短期量）
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

ROWS = FEAT = None


def load():
    global ROWS, FEAT
    if ROWS is None:
        ROWS = pickle.load(open("/tmp/ratebranch/rows.pkl", "rb"))
        FEAT = pickle.load(open("/tmp/shortform/feat.pkl", "rb"))
        idx = {n: j for j, n in enumerate(FEAT["names"])}
        by = FEAT["by_key"]
        keep = []
        for r in ROWS:
            a = by.get(r["race_key"])
            if a is None:
                continue
            r["sf"] = a
            keep.append(r)
        ROWS = keep
        FEAT["idx"] = idx
    return ROWS


def F(r, name):
    """(7,) の短期量。"""
    return r["sf"][:, FEAT["idx"][name]]


def wins(rows=None):
    rows = rows or load()
    return {w: [r for r in rows if r["win"] == w] for w in ("explore", "confirm")}


def auc_of(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, bool)
    m = np.isfinite(x)
    x, y = x[m], y[m]
    if len(x) == 0 or y.all() or not y.any():
        return float("nan")
    r = np.argsort(np.argsort(x)) + 1.0
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ───────────────────── §0 リーク検査 ─────────────────────

def leak():
    """① 定義どおり当日を除外できているか（独立再計算との一致）
       ② 当日を含めると何が起きるか（＝リークした場合の見え方）
       ③ ソース列の年次充足率（serve 時に無い列を学習で使っていないか）"""
    import os
    import pandas as pd
    from sqlalchemy import create_engine, text as sa_text

    rows = load()
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as c:
        H = pd.read_sql_query(sa_text(
            "SELECT e.race_key, e.frame_no, e.player_id, e.finish_order, e.factor, "
            "e.res_back, e.res_standing, e.final_half, r.race_date, r.race_no "
            "FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key=r.race_key"), c)
    eng.dispose()
    H["_dt"] = pd.to_datetime(H["race_date"])
    fo = pd.to_numeric(H["finish_order"], errors="coerce")
    H["_ok"] = fo >= 1
    H["_top3"] = (fo <= 3).astype(float).where(H["_ok"])
    H["_b"] = pd.to_numeric(H["res_back"], errors="coerce").where(H["_ok"])

    print("§0-① 独立再計算との一致（当日を除外できているか）\n")
    rng = np.random.default_rng(7)
    samp = [rows[i] for i in rng.choice(len(rows), 400, replace=False)]
    pid_map = {}
    for rk, fn, pid in H[["race_key", "frame_no", "player_id"]].to_numpy():
        pid_map[(str(rk), int(fn))] = int(pid)
    Hs = H.sort_values(["player_id", "_dt", "race_no"])
    byp = {p: g for p, g in Hs.groupby("player_id")}
    for name, col, N in (("top3_5", "_top3", 5), ("b5", "_b", 5), ("top3_10", "_top3", 10)):
        d = []
        for r in samp:
            for fn in range(1, 8):
                p = pid_map.get((r["race_key"], fn))
                if p is None:
                    continue
                g = byp[p]
                past = g[g["_dt"] < pd.Timestamp(r["date"])]
                v = past[col].tail(N)
                ref = float(v.mean()) if v.notna().any() else np.nan
                got = float(F(r, {"top3_5": "top35", "b5": "b5",
                                  "top3_10": "top310"}[name])[fn - 1])
                if np.isnan(ref) and np.isnan(got):
                    continue
                d.append(abs((ref if ref == ref else 0) - (got if got == got else 0)))
        d = np.array(d)
        print(f"  {name:10s} n={len(d):5d}  最大差 {d.max():.6f}  "
              f"不一致(>1e-6) {int((d > 1e-6).sum())} 件")

    print("\n§0-② 当日を含めた場合（＝リークしていたらこう見える）\n")
    print(f"{'量':16s}{'当日除外(採用)':>16s}{'当日込み(リーク)':>18s}   ←自分の3着内を当てるAUC")
    # 当日込みの値を作り、当日の 3着内 を当てる AUC を比べる
    Hs2 = Hs.reset_index(drop=True)
    pid_a = Hs2["player_id"].to_numpy()
    gs = np.zeros(len(Hs2), np.int64)
    st = np.flatnonzero(np.r_[True, pid_a[1:] != pid_a[:-1]])
    gs[st] = st
    gs = np.maximum.accumulate(gs)
    idx = np.arange(len(Hs2), dtype=np.int64)

    def roll(v, N, incl):
        ok = np.isfinite(v)
        S = np.r_[0.0, np.cumsum(np.where(ok, v, 0.0))]
        C = np.r_[0, np.cumsum(ok.astype(np.int64))]
        hi = idx + 1 if incl else idx
        lo = np.maximum(gs, hi - N)
        cnt = C[hi] - C[lo]
        return np.where(cnt > 0, (S[hi] - S[lo]) / np.maximum(cnt, 1), np.nan)

    keyfn = {(str(a), int(b)): i for i, (a, b) in
             enumerate(Hs2[["race_key", "frame_no"]].to_numpy())}
    tg3 = Hs2["_top3"].to_numpy(float)
    for nm, col, N in (("top3(直近5走)", "_top3", 5), ("B取得率(直近5走)", "_b", 5)):
        v = Hs2[col].to_numpy(float)
        a_ex, a_in = roll(v, N, False), roll(v, N, True)
        pos = [keyfn[(r["race_key"], fn)] for r in rows for fn in range(1, 8)
               if (r["race_key"], fn) in keyfn]
        pos = np.array(pos)
        y = tg3[pos]
        m = np.isfinite(y)
        print(f"  {nm:16s}{auc_of(a_ex[pos][m], y[m] > 0.5):16.4f}"
              f"{auc_of(a_in[pos][m], y[m] > 0.5):18.4f}")

    print("\n§0-③ ソース列の年次充足率（NULL でない割合）\n")
    H["_y"] = H["race_date"].str[:4]
    print(f"{'年':6s}{'finish_order':>14s}{'res_back':>11s}{'final_half':>12s}{'factor':>9s}{'n':>10s}")
    for y, g in H.groupby("_y"):
        if y < "2023":
            continue
        print(f"{y:6s}{g['finish_order'].notna().mean()*100:13.1f}%"
              f"{g['res_back'].notna().mean()*100:10.1f}%"
              f"{g['final_half'].notna().mean()*100:11.1f}%"
              f"{(g['factor'].fillna('')!='').mean()*100:8.1f}%{len(g):10,d}")


# ───────────────────── §1 記述: 3着目になる率 ─────────────────────

NEW = ["top35", "top310", "ord5", "ord10", "win5", "b5", "b10", "s5",
       "front5", "front10", "nige10", "maku10", "sashi10", "mark10",
       "fhrel5", "fhrel10", "dnf3", "dnf5", "rp_d5", "rp_d10",
       "drift_front", "drift_mark", "dsince", "nhist"]
JP = {"top35": "直近5走 3着内率", "top310": "直近10走 3着内率",
      "ord5": "直近5走 正規化着順", "ord10": "直近10走 正規化着順",
      "win5": "直近5走 1着率", "b5": "直近5走 B取得率", "b10": "直近10走 B取得率",
      "s5": "直近5走 S取得率", "front5": "直近5走 自力率(逃/捲)",
      "front10": "直近10走 自力率", "nige10": "直近10走 逃げ率",
      "maku10": "直近10走 捲り率", "sashi10": "直近10走 差し率",
      "mark10": "直近10走 マーク率", "fhrel5": "直近5走 上がり相対",
      "fhrel10": "直近10走 上がり相対", "dnf3": "直近3走 欠車/失格率",
      "dnf5": "直近5走 欠車/失格率", "rp_d5": "得点 − 直近5走平均",
      "rp_d10": "得点 − 直近10走平均", "drift_front": "脚質ズレ(逃−自力実行)",
      "drift_mark": "脚質ズレ(追−マーク実行)", "dsince": "前走からの日数",
      "nhist": "履歴走数"}


def _cands(r):
    return [c for c in np.argsort(-r["p3vec"]) + 1 if c not in (r["a1"], r["a2"])]


def desc():
    """軸2車そろい時の『3着目になる率』を短期量の五分位で（相手5車プールの中で）。"""
    W = wins()
    print("§1 軸2車そろい回の相手5車を短期量の五分位に割り、その車が3着目だった率\n")
    print(f"{'量':24s}" + "".join(f"{f'Q{q}':>9s}" for q in range(1, 6)) +
          f"{'単調':>7s}   窓")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        recs = []
        for r in both:
            cand = _cands(r)[:5]
            for c in cand:
                recs.append((r, c, c == r["third_car"]))
        for nm in NEW:
            x = np.array([F(r, nm)[c - 1] for r, c, _ in recs])
            y = np.array([h for _, _, h in recs])
            m = np.isfinite(x)
            if m.sum() < 100:
                continue
            qs = np.quantile(x[m], [.2, .4, .6, .8])
            b = np.digitize(x[m], qs)
            v = [y[m][b == q].mean() * 100 for q in range(5)]
            mono = "↑" if all(v[i] <= v[i + 1] for i in range(4)) else (
                "↓" if all(v[i] >= v[i + 1] for i in range(4)) else "-")
            print(f"{JP[nm]:24s}" + "".join(f"{z:8.2f}%" for z in v) +
                  f"{mono:>7s}   {w} n={int(m.sum()):,}")
        print(f"{'（母数）':24s}{np.mean([h for _,_,h in recs])*100:8.2f}%\n")


# ───────────────────── §2 残差テスト ─────────────────────

def resid():
    """p3 の相手順位で層別したうえで、短期量が層の中でさらに分けるか。"""
    W = wins()
    print("§2 p3 相手順位で層別 → 層の中で短期量の上位半分／下位半分に分ける\n")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        print(f"--- {w} (n={len(both):,}) ---")
        cells = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        base = defaultdict(lambda: [0, 0])
        for r in both:
            cand = _cands(r)[:5]
            for nm in NEW:
                v = F(r, nm)
                vs = [v[c - 1] for c in cand]
                if not np.isfinite(vs).all():
                    continue
                med = np.median(vs)
                for j, c in enumerate(cand, 1):
                    hi = vs[j - 1] >= med
                    cell = cells[nm][(j, hi)]
                    cell[0] += 1
                    cell[1] += c == r["third_car"]
            for j, c in enumerate(cand, 1):
                base[j][0] += 1
                base[j][1] += c == r["third_car"]
        print(f"{'量':24s}" + "".join(f"{f'順位{j}':>15s}" for j in range(1, 6)))
        print(f"{'（母数）':24s}" +
              "".join(f"{base[j][1]/base[j][0]*100:14.2f}%" for j in range(1, 6)))
        for nm in NEW:
            out = []
            for j in range(1, 6):
                lo, hi = cells[nm][(j, False)], cells[nm][(j, True)]
                if lo[0] == 0 or hi[0] == 0:
                    out.append("      -        ")
                    continue
                d = hi[1] / hi[0] * 100 - lo[1] / lo[0] * 100
                out.append(f"{hi[1]/hi[0]*100:8.2f}%{d:+6.1f}")
            print(f"{JP[nm]:24s}" + "".join(out))
        print()


# ───────────────────── §3 軸崩壊の事前検知（AUC） ─────────────────────

def auc():
    """『軸2車が飛ぶ（軸崩壊）』を短期量で事前に分けられるか。"""
    W = wins()

    def feats(r):
        a1, a2 = r["a1"], r["a2"]
        cand = _cands(r)[:3]
        out = {}
        for nm in NEW:
            v = F(r, nm)
            out[f"{nm}: 軸2車の平均"] = (v[a1 - 1] + v[a2 - 1]) / 2
            out[f"{nm}: 軸−相手上位3"] = ((v[a1 - 1] + v[a2 - 1]) / 2
                                        - float(np.mean([v[c - 1] for c in cand])))
        out["（参考）axis_sum"] = r["axis"]
        out["（参考）rp_sd"] = r["rp_sd"]
        return out

    print("§3 『軸2車が飛ぶ（軸崩壊）』の事前 AUC（0.5＝情報なし）\n")
    names = list(feats(W["confirm"][0]).keys())
    res = {}
    for w in ("confirm", "explore"):
        rs = W[w]
        y = [not r["both_in3"] for r in rs]
        Fm = [feats(r) for r in rs]
        res[w] = ({n: auc_of([f[n] for f in Fm], y) for n in names}, len(rs),
                  np.mean(y) * 100)
    print(f"{'量':34s}{'確認':>9}{'探索':>9}")
    ordered = sorted(names, key=lambda n: -abs(res["confirm"][0][n] - 0.5))
    for n in ordered:
        c, e = res["confirm"][0][n], res["explore"][0][n]
        flag = "  ★" if (c - 0.5) * (e - 0.5) > 0 and min(abs(c - .5), abs(e - .5)) >= .02 else ""
        print(f"{n:34s}{c:9.3f}{e:9.3f}{flag}")
    for w in ("confirm", "explore"):
        print(f"  {w}: n={res[w][1]:,}  軸崩壊 {res[w][2]:.1f}%")


# ───────────────────── §4 相手1車目の当て率 ─────────────────────

def _mkt(r):
    s = np.zeros(7)
    for c, o in r["po_tf"].items():
        if o > 0:
            for car in c:
                s[car - 1] = max(s[car - 1], 1.0 / o)
    return s


def _line(r):
    lg = r["lg"]
    tgt = {lg[r["a1"] - 1], lg[r["a2"] - 1]}
    return np.array([(1.0 if lg[c] in tgt else 0.0) + 1e-6 * r["p3vec"][c] for c in range(7)])


def partner():
    """3着目を相手 m 車で覆える率。短期量単独 / p3 に短期量を加点した合成。"""
    W = wins()
    sels = {
        "モデル p3（現行）": lambda r: r["p3vec"],
        "同ライン優先": _line,
        "市場（予測オッズ）": _mkt,
    }
    for nm in ("top35", "top310", "b10", "front10", "ord5", "rp_d5"):
        sels[f"短期単独: {JP[nm]}"] = (lambda r, nm=nm: np.nan_to_num(F(r, nm), nan=-9))
    # p3 に短期量の z を加点（重み 0.1 / 0.3）
    for nm in ("top35", "front10", "rp_d5"):
        for wgt in (0.1, 0.3):
            def fn(r, nm=nm, wgt=wgt):
                v = np.nan_to_num(F(r, nm), nan=np.nan)
                z = (v - np.nanmean(v)) / (np.nanstd(v) + 1e-9)
                z = np.nan_to_num(z, nan=0.0)
                p = r["p3vec"]
                pz = (p - p.mean()) / (p.std() + 1e-9)
                return pz + wgt * z
            sels[f"p3 + {wgt}×{JP[nm]}"] = fn
    print("§4 軸2車そろい回の「3着目」を相手上位 m 車で覆える率\n")
    print(f"{'選び方':34s}" + "".join(f"{f'm={m}':>9s}" for m in (1, 2, 3)) + "   窓")
    for w in ("confirm", "explore"):
        both = [r for r in W[w] if r["both_in3"] and r["third_car"]]
        for name, f in sels.items():
            cov = []
            for m in (1, 2, 3):
                ok = 0
                for r in both:
                    s = f(r)
                    cand = [c for c in np.argsort(-s) + 1 if c not in (r["a1"], r["a2"])]
                    ok += r["third_car"] in cand[:m]
                cov.append(ok / len(both) * 100)
            print(f"{name:34s}" + "".join(f"{v:8.2f}%" for v in cov) + f"   {w} n={len(both):,}")
        print()


# ───────────────────── §5 axis_sum の残差として軸崩壊を分けるか ─────────────────────

def _gap(r, nm, k=3):
    """短期量の『軸2車の平均 − 相手上位3車の平均』。"""
    v = F(r, nm)
    cand = _cands(r)[:k]
    return (v[r["a1"] - 1] + v[r["a2"] - 1]) / 2 - float(np.mean([v[c - 1] for c in cand]))


def resid2():
    """axis_sum の五分位の中で、短期量の gap が軸崩壊率をさらに分けるか。
    （長期量・ライン量は全て残差ゼロだった。rp_sd だけが残差を持っていた）"""
    W = wins()
    tgt = ["ord10", "ord5", "front10", "b10", "nige10", "top310", "fhrel10", "drift_mark"]
    print("§5 axis_sum 五分位の中での軸崩壊率（短期 gap の上位半分 / 下位半分）\n")
    for w in ("confirm", "explore"):
        rs = W[w]
        ax = np.array([r["axis"] for r in rs])
        qs = np.quantile(ax, [.2, .4, .6, .8])
        bins = np.digitize(ax, qs)
        print(f"--- {w} (n={len(rs):,}) ---")
        print(f"{'量':22s}" + "".join(f"{f'axisQ{q+1}':>16s}" for q in range(5)) + f"{'同符号':>7s}")
        base = [np.mean([not r["both_in3"] for r, b in zip(rs, bins) if b == q]) * 100
                for q in range(5)]
        print(f"{'（母数）':22s}" + "".join(f"{v:15.2f}%" for v in base) + " " * 7)
        for nm in tgt:
            out, signs = [], []
            for q in range(5):
                sub = [r for r, b in zip(rs, bins) if b == q]
                g = np.array([_gap(r, nm) for r in sub])
                y = np.array([not r["both_in3"] for r in sub])
                m = np.isfinite(g)
                if m.sum() < 50:
                    out.append(" " * 15 + " ")
                    signs.append(0)
                    continue
                med = np.median(g[m])
                hi, lo = y[m][g[m] >= med].mean() * 100, y[m][g[m] < med].mean() * 100
                out.append(f"{hi:9.2f}%{hi-lo:+6.1f}")
                signs.append(np.sign(hi - lo))
            same = abs(sum(signs))
            print(f"{JP[nm]:22s}" + "".join(out) + f"{int(same)}/5".rjust(7))
        print()


# ───────────────────── §6 商品化 ─────────────────────

def product():
    """短期量を E_hit/F_hit の選抜確率へ条件つきで載せ、商品KPIを測る。
    点数・帯・配分・ゲートは現行のまま。対照は同じ形の無作為ボーナス 20 seed。"""
    import random
    from src.type_lab import PLANS, RaceShape, allocate, build_legs, mean_expected_payout
    MINM, MINP = 20_000, 2.0
    W = wins()

    def shape_of(r):
        order = tuple(sorted(range(1, 8), key=lambda c: (-r["p3vec"][c - 1], c)))
        return RaceShape(r["type"], float(r["axis"]), int(r["arare"]), float(r["gap"]),
                         False, order, float(r["pw_ent"]))

    def zvec(r, nm, sign=1.0):
        v = F(r, nm).astype(float)
        if not np.isfinite(v).all():
            return None
        z = (v - v.mean()) / (v.std() + 1e-9)
        return sign * z

    def run(rows, arm, seed=None):
        rng = random.Random(seed) if seed is not None else None
        n = shown = 0
        inv = pay = 0.0
        pays = []
        for r in rows:
            plan = PLANS[r["plan"]]
            sh = shape_of(r)
            pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"] else (r["po_tf"], r["pr_tf"]))
            if arm is not None:
                nm, sign, w = arm
                z = zvec(r, nm, sign)
                if z is not None:
                    if rng is not None:
                        z = np.array(z)
                        rng.shuffle(z)
                    mult = np.exp(w * z)
                    prb = {c: v * float(np.prod([mult[x - 1] for x in
                                                 (tuple(c) if r["trio"] else c)]))
                           for c, v in prb.items()}
            legs = build_legs(sh, plan, pod, prb)
            if not legs:
                continue
            st = allocate(legs, pod, prb, plan)
            if not st:
                continue
            m = mean_expected_payout(st, pod)
            if m <= MINM or min(float(pod[c]) for c in st) < MINP:
                continue
            b = float(sum(st.values()))
            if r["trio"]:
                p = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
            else:
                p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
            n += 1
            inv += b
            pay += p
            shown += (p >= b)
            pays.append(p)
        return n, shown / n * 100, pay / inv * 100, pays

    ARMS = [("直近10走 上がり相対（速い側を上げる）", ("fhrel10", -1.0, 0.3)),
            ("同 w=0.6", ("fhrel10", -1.0, 0.6)),
            ("直近10走 マーク率", ("mark10", 1.0, 0.3)),
            ("直近10走 3着内率", ("top310", 1.0, 0.3)),
            ("直近10走 正規化着順（良い側）", ("ord10", -1.0, 0.3))]
    print("§6 型E/F の選抜確率に短期量の z を exp(w·z) で載せる（対照=同じ z を車番シャッフル 20seed）\n")
    for w in ("confirm", "explore"):
        rows = [r for r in W[w] if r["plan"] in ("E_hit", "F_hit")]
        base = run(rows, None)
        print(f"--- {w}  対象 {len(rows):,}商品 ---")
        print(f"{'腕':36s}{'件数':>7}{'表示的中':>10}{'ROI':>9}{'Δ表示的中':>11}{'対照勝ち':>9}")
        print(f"{'現行':36s}{base[0]:7d}{base[1]:9.2f}%{base[2]:8.1f}%")
        for label, arm in ARMS:
            a = run(rows, arm)
            ctrl = [run(rows, arm, seed=1000 + s)[1] for s in range(20)]
            print(f"{label:36s}{a[0]:7d}{a[1]:9.2f}%{a[2]:8.1f}%{a[1]-base[1]:+10.2f}pt"
                  f"{sum(1 for c in ctrl if a[1] > c):6d}/20")
        print()


if __name__ == "__main__":
    {"leak": leak, "desc": desc, "resid": resid, "auc": auc, "partner": partner,
     "resid2": resid2, "product": product}[sys.argv[1]]()
