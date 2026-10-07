#!/usr/bin/env python3
"""H19 集計: h19_build.py が作った data/exp_bet_review/h19/h19_arms_{TH}.pkl から表と機械判定を出す（事前登録どおり）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h19_run.py
出力: stdout と data/exp_bet_review/h19/h19_tables.md（Markdown）。
- 母集団 = ① が売るレース（main + 高額枠・L_lead なし・lineup_arms.run と一致）。k=0 / 組めない k=1,2 は ② ③ ④ も ① のまま。
- V1（全体対比）: ② の全体 vs ① の全体（② は k=3 で見送るので投資が減る）。V2（同一購入レース対比）: ① を ② が買ったレースに絞る。
- CI = 開催日ブートストラップ 2,000 回・95%（上期/下期は当該期の日で再標本化）。払戻 = 最終オッズ × 賭け金（上限値）。
- 表示的中 = 払戻 > 投資。的中 = 払戻 > 0。10万+ = 払戻 >= 100,000。/日 は 365 日で割る。
"""
from __future__ import annotations
import pickle, sys
import numpy as np
from h01_common import *   # noqa

N_BOOT = 2000
OUT = []


def P(s=""):
    print(s, flush=True)
    OUT.append(s)


def load(th):
    z = pickle.load(open(D / "h19" / f"h19_arms_{th:.2f}.pkl", "rb"))
    return z


def make_arms(z):
    arms, cur, races = z["arms"], z["cur"], z["races"]
    keys = sorted(cur)                      # ① が売るレース
    days_all = sorted({races[k]["day"] for k in races})
    dpos = {d: i for i, d in enumerate(days_all)}
    N = len(keys)
    A = dict(day=np.array([dpos[races[k]["day"]] for k in keys]),
             half=np.array([int(races[k]["day"][5:7]) <= 6 for k in keys]),
             k=np.array([arms[k]["k"] for k in keys]),
             kind=np.array([arms[k]["kind"] for k in keys]),
             slot=np.array([cur[k]["slot"] for k in keys]),
             tl=np.array([races[k]["tl"] for k in keys]),
             plan=np.array([cur[k]["plan"] for k in keys]),
             keys=keys, ND=len(days_all))
    def arr(f):
        inv = np.zeros(N); pay = np.zeros(N); buy = np.zeros(N, bool)
        for j, k in enumerate(keys):
            r = f(k)
            if r is not None:
                inv[j], pay[j], buy[j] = r["inv"], r["pay"], True
        return dict(inv=inv, pay=pay, buy=buy)
    c1 = arr(lambda k: cur[k])
    def a2(k):
        a = arms[k]
        if a["kind"] in ("k0", "fallback"): return cur[k]
        return a["r2"]                      # built or None(discard)
    def a3(k):
        a = arms[k]
        if a["kind"] in ("k0", "fallback"): return cur[k]
        return a["r3"]
    def c2(k):
        a = arms[k]
        if a["kind"] in ("k0", "fallback"): return cur[k]
        return a["c2"]
    def c3(k):
        a = arms[k]
        if a["kind"] in ("k0", "fallback"): return cur[k]
        return a["c3"]
    A["①"], A["②"], A["③"] = c1, arr(a2), arr(a3)
    A["④₂"], A["④₃"] = arr(c2), arr(c3)
    # ③ の穴目行か
    A["ana3"] = np.array([arms[k]["kind"] == "discard" and arms[k]["r3"] is not None for k in keys])
    return A


def dm(A, arm, mask):
    """日×列の行列: [inv, pay, nbuy, nshown, nhit, nbig]"""
    a = A[arm]
    M = np.zeros((A["ND"], 6))
    m = mask & a["buy"]
    inv, pay = a["inv"][m], a["pay"][m]
    for col, v in enumerate((inv, pay, np.ones(m.sum()), (pay > inv).astype(float), (pay > 0).astype(float), (pay >= 1e5).astype(float))):
        np.add.at(M[:, col], A["day"][m], v)
    return M


def met(T):
    """T: (..., 6) → dict of roi, shown, hit, big, n"""
    with np.errstate(invalid="ignore", divide="ignore"):
        return dict(roi=T[..., 1] / T[..., 0] * 100, shown=T[..., 3] / T[..., 2] * 100, hit=T[..., 4] / T[..., 2] * 100,
                    big=T[..., 5], n=T[..., 2])


def ci(a):
    a = np.asarray(a); a = a[np.isfinite(a)]
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def boot_W(A, mask_days, rng):
    dd = np.flatnonzero(mask_days)
    return np.stack([np.bincount(rng.choice(dd, len(dd)), minlength=A["ND"]) for _ in range(N_BOOT)]).astype(float)


def median_hit(A, arm, mask):
    a = A[arm]; m = mask & a["buy"] & (a["pay"] > 0)
    return float(np.median(a["pay"][m])) if m.any() else float("nan")


def fmt(x, d=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def arm_row(A, arm, mask, nd):
    M = dm(A, arm, mask); t = M.sum(0); m = met(t)
    sold = int(mask.sum())
    return dict(R=int(t[2]), skip=sold - int(t[2]), hit=m["hit"], shown=m["shown"], roi=m["roi"], big=m["big"], big_d=m["big"] / nd,
                med=median_hit(A, arm, mask), inv=t[0], pay=t[1])


def table_arms(A, mask, nd, arms=("①", "②", "③", "④₂", "④₃")):
    P("| 腕 | 買った件数 | 見送り | 的中率 | 表示的中 | 回収率 | 10万+（件・/日） | 的中時払戻 中央値 |")
    P("|---|---|---|---|---|---|---|---|")
    for arm in arms:
        r = arm_row(A, arm, mask, nd)
        P(f"| {arm} | {r['R']:,} | {r['skip']:,} | {fmt(r['hit'])}% | {fmt(r['shown'])}% | {fmt(r['roi'])}% | {int(r['big'])}（{r['big_d']:.4f}） | {fmt(r['med'],0)}円 |")


def delta(A, arm, base, mask, W, matched):
    """arm − base の (ΔROI, Δshown) の点推定と CI。matched=True なら base を arm が買ったレースに絞る。"""
    Ma = dm(A, arm, mask)
    bm = mask & A[arm]["buy"] if matched else mask
    Mb = dm(A, base, bm)
    ta, tb = Ma.sum(0), Mb.sum(0)
    pa, pb = met(ta), met(tb)
    Ta, Tb = W @ Ma, W @ Mb
    ba, bb = met(Ta), met(Tb)
    d_roi = ba["roi"] - bb["roi"]; d_sh = ba["shown"] - bb["shown"]
    return dict(roi=pa["roi"] - pb["roi"], roi_ci=ci(d_roi), sh=pa["shown"] - pb["shown"], sh_ci=ci(d_sh))


def main():
    rng = np.random.default_rng(20261019)
    z = load(0.60)
    A = make_arms(z)
    ND = A["ND"]; N = len(A["keys"])
    allm = np.ones(N, bool); H1 = A["half"]; H2 = ~A["half"]
    days_h1 = np.zeros(ND, bool); days_h1[A["day"][H1]] = True
    days_h2 = np.zeros(ND, bool); days_h2[A["day"][H2]] = True
    # 365 日全体で再標本化（k 別でも同じ日抽選）
    days_all = np.ones(ND, bool)
    W = {"通年": boot_W(A, days_all, rng), "上期": boot_W(A, days_h1, rng), "下期": boot_W(A, days_h2, rng)}
    nds = {"通年": ND, "上期": int(days_h1.sum()), "下期": int(days_h2.sum())}
    per = {"通年": allm, "上期": H1, "下期": H2}
    ks = {"全体": allm, "k=0": A["k"] == 0, "k=1": A["k"] == 1, "k=2": A["k"] == 2, "k=3": A["k"] == 3}

    # ── 0. k の分布
    arms_all = z["arms"]
    kk = np.array([v["k_raw"] for v in arms_all.values()])
    P("## 1. k の分布（2025・7車の台・p3 ≥ 0.60）\n")
    P(f"台の全レース {len(kk):,}R（ctx が組めたもの）。k_raw = p3≥0.60 の人数（3以上は k=3 として扱う。k_raw≥4 は p3 上位3車を堅い3車とする）。\n")
    P("| | k=0 | k=1 | k=2 | k=3（うち k_raw≥4） | 計 |")
    P("|---|---|---|---|---|---|")
    c = [int((kk == i).sum()) for i in range(3)] + [int((kk >= 3).sum())]
    P("| 台の全レース | " + " | ".join(f"{v:,}（{v/len(kk)*100:.1f}%）" for v in c[:3]) + f" | {c[3]:,}（{c[3]/len(kk)*100:.1f}%、うち {int((kk>=4).sum())}） | {len(kk):,} |")
    sk = A["k"]
    c2 = [int((sk == i).sum()) for i in range(4)]
    P("| ① が売るレース | " + " | ".join(f"{v:,}（{v/N*100:.1f}%）" for v in c2[:3]) + f" | {c2[3]:,}（{c2[3]/N*100:.1f}%） | {N:,} |")
    for sl in ("main", "highpay"):
        m = A["slot"] == sl
        c3 = [int(((sk == i) & m).sum()) for i in range(4)]
        P(f"| 　うち ①が{sl} | " + " | ".join(f"{v:,}" for v in c3) + f" | {m.sum():,} |")
    P("")
    unsold = [v for kx, v in arms_all.items() if kx not in z["cur"]]
    P("① が売らない台のレース（軸信頼ゲート落ち・日次上限・組めない等）: " + str(len(unsold)) + "R。k=0/1/2/3 = "
      + "/".join(str(sum(1 for v in unsold if v["k"] == i)) for i in range(4)) + "（H19 は ① が売るレースだけを差し替えるので対象外）。")
    P("")
    P("① が売るレースでの H19 の組み上がり（th=0.60）:\n")
    P("| k | ① が売る | 組めた（②=③） | ① に戻す（組めない） | 捨て（k=3） | 捨て→③の穴目が組めた |")
    P("|---|---|---|---|---|---|")
    for i in range(4):
        m = A["k"] == i
        P(f"| {i} | {int(m.sum()):,} | {int((m & (A['kind']=='built')).sum()):,} | {int((m & (A['kind']=='fallback')).sum()):,} | {int((m & (A['kind']=='discard')).sum()):,} | {int((m & A['ana3']).sum()):,} |")
    P("")
    for i in (1, 2, 3):
        m = (A["k"] == i) & (A["kind"] == "built")
        rr = [z["arms"][k]["r2"] for k, mm in zip(A["keys"], m) if mm]
        P(f"- ②=③ の k={i} の買い目（組めたレース {len(rr):,}R）: 点数 平均 {np.mean([r['n'] for r in rr]):.2f}・合成オッズ（予測）平均 {np.mean([r['comp'] for r in rr]):.2f}倍")
    P("")

    # ── 2. 全体・k 別
    P("## 2. 腕の比較（2025 通年・① が売る 12,148R・開催日 365 日）\n")
    for name, m in ks.items():
        P(f"### {name}（① が売る {int(m.sum()):,}R）\n")
        table_arms(A, m, ND)
        if name == "k=3":
            P("")
            kept = m & (A["kind"] == "built"); disc = m & (A["kind"] == "discard")
            P(f"k=3 の内訳: 組めた {int(kept.sum()):,}R / 捨て {int(disc.sum()):,}R。")
            P("\n捨てたレースだけ（① が売っていた商品 / ② 見送り / ③ 穴目 / ④₃ 確率上位 n 点）:\n")
            table_arms(A, disc, ND, arms=("①", "②", "③", "④₃"))
            P("\n組めたレースだけ（②=③）:\n")
            table_arms(A, kept, ND, arms=("①", "②", "④₂"))
        P("")

    # ── 3. 期別
    P("## 3. 上期（1〜6月）/ 下期（7〜12月）\n")
    for pn in ("上期", "下期"):
        for name in ("全体", "k=1", "k=2", "k=3"):
            m = ks[name] & per[pn]
            P(f"### {pn}・{name}（{int(m.sum()):,}R・{nds[pn]}日）\n")
            table_arms(A, m, nds[pn])
            P("")

    # ── 4. 差と CI
    P("## 4. 差と CI（開催日ブートストラップ 2,000 回・95%）\n")
    P("V1 = ② の全体 − ① の全体（k=3 の見送りで投資が減る分も含む）。V2 = ① を ② が買ったレースに絞った対比。\n")
    res = {}
    P("### 4.1 ②/③ − ①\n")
    P("| 腕 | 範囲 | 期 | 版 | ΔROI（pt）[CI] | Δ表示的中（pt）[CI] |")
    P("|---|---|---|---|---|---|")
    for arm in ("②", "③"):
        for name in ("全体", "k=1", "k=2", "k=3"):
            for pn in ("通年", "上期", "下期"):
                m = ks[name] & per[pn]
                for ver, matched in (("V1", False), ("V2", True)):
                    d = delta(A, arm, "①", m, W[pn], matched)
                    res[(arm, name, pn, ver)] = d
                    P(f"| {arm} | {name} | {pn} | {ver} | {d['roi']:+.2f} [{d['roi_ci'][0]:+.2f}, {d['roi_ci'][1]:+.2f}] | {d['sh']:+.2f} [{d['sh_ci'][0]:+.2f}, {d['sh_ci'][1]:+.2f}] |")
    P("")
    P("### 4.2 形の効果: ②−④₂、③−④₃（同じレース・同じ点数・買うレースは同一）\n")
    P("| 腕 | 範囲 | 期 | Δ回収率（pt）[CI] | Δ表示的中（pt）[CI] |")
    P("|---|---|---|---|---|")
    for arm, ctl in (("②", "④₂"), ("③", "④₃")):
        for name in ("全体", "k=1", "k=2", "k=3"):
            for pn in ("通年", "上期", "下期"):
                m = ks[name] & per[pn]
                Ma, Mc = dm(A, arm, m), dm(A, ctl, m)
                # 同じ購入レースか（③−④₃ は穴目で ④₃ が組めないレースがあり得る）
                Ta, Tc = W[pn] @ Ma, W[pn] @ Mc
                ba, bc = met(Ta), met(Tc)
                pa, pc = met(Ma.sum(0)), met(Mc.sum(0))
                dr, ds = ci(ba["roi"] - bc["roi"]), ci(ba["shown"] - bc["shown"])
                res[(arm, ctl, name, pn)] = (pa["roi"] - pc["roi"], dr, pa["shown"] - pc["shown"], ds)
                P(f"| {arm}−{ctl} | {name} | {pn} | {pa['roi']-pc['roi']:+.2f} [{dr[0]:+.2f}, {dr[1]:+.2f}] | {pa['shown']-pc['shown']:+.2f} [{ds[0]:+.2f}, {ds[1]:+.2f}] |")
    P("")

    # ── 5. 機械判定
    P("## 5. 機械判定（事前登録: 主 = ②または③−① の ΔROI の CI 下限 > −2pt ∧ 表示的中の差の CI 下限 > 0 ∧ 上期・下期で同符号）\n")
    P("上期・下期の「同符号」は ΔROI と Δ表示的中のそれぞれについて、上期と下期の点推定の符号が一致することとして機械的に判定する。\n")
    P("| 腕 | 版 | ΔROI 通年 | CI下限>−2pt | Δ表示的中 通年 | CI下限>0 | ΔROI 上/下 | 同符号 | Δ表示的中 上/下 | 同符号 | **判定** |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    verdict = {}
    for arm in ("②", "③"):
        for ver in ("V1", "V2"):
            dt = res[(arm, "全体", "通年", ver)]
            d1, d2 = res[(arm, "全体", "上期", ver)], res[(arm, "全体", "下期", ver)]
            c_roi = dt["roi_ci"][0] > -2
            c_sh = dt["sh_ci"][0] > 0
            s_roi = np.sign(d1["roi"]) == np.sign(d2["roi"])
            s_sh = np.sign(d1["sh"]) == np.sign(d2["sh"])
            ok = bool(c_roi and c_sh and s_roi and s_sh)
            verdict[(arm, ver)] = ok
            P(f"| {arm} | {ver} | {dt['roi']:+.2f}pt [{dt['roi_ci'][0]:+.2f}, {dt['roi_ci'][1]:+.2f}] | {'○' if c_roi else '×'} | {dt['sh']:+.2f}pt [{dt['sh_ci'][0]:+.2f}, {dt['sh_ci'][1]:+.2f}] | {'○' if c_sh else '×'} "
              f"| {d1['roi']:+.2f} / {d2['roi']:+.2f} | {'○' if s_roi else '×'} | {d1['sh']:+.2f} / {d2['sh']:+.2f} | {'○' if s_sh else '×'} | **{'満たす' if ok else '満たさない'}** |")
    P("")
    P("形の効果（②−④₂・③−④₃ の全体・通年）:\n")
    for arm, ctl in (("②", "④₂"), ("③", "④₃")):
        r = res[(arm, ctl, "全体", "通年")]
        P(f"- {arm}−{ctl}: 回収率 {r[0]:+.2f}pt [{r[1][0]:+.2f}, {r[1][1]:+.2f}] / 表示的中 {r[2]:+.2f}pt [{r[3][0]:+.2f}, {r[3][1]:+.2f}]")
    P("")

    # ── 6. 上位 N 件除外
    P("## 6. 払戻の上位 1・3・5 件（レース単位）を除いた回収率\n")
    P("| 範囲 | 腕 | 除外なし | 上位1除外 | 上位3除外 | 上位5除外 |")
    P("|---|---|---|---|---|---|")
    for name in ("全体", "k=1", "k=2", "k=3"):
        m = ks[name]
        for arm in ("①", "②", "③", "④₂"):
            a = A[arm]; mm = m & a["buy"]
            inv, pay = a["inv"][mm], a["pay"][mm]
            o = np.argsort(-pay)
            def roi_ex(n):
                keep = np.ones(len(pay), bool); keep[o[:n]] = False
                return pay[keep].sum() / inv[keep].sum() * 100
            P(f"| {name} | {arm} | {roi_ex(0):.2f}% | {roi_ex(1):.2f}% | {roi_ex(3):.2f}% | {roi_ex(5):.2f}% |")
    P("")
    # ── 7. ① の商品（slot）別の差
    P("## 7. ① が売っていた枠別（main / 高額枠）の ②−①（V1・通年・k≥1 のみ差し替わる）\n")
    P("| ① の枠 | 腕 | R | ① 回収率 / 表示的中 | 腕 回収率 / 表示的中 | ΔROI [CI] | Δ表示的中 [CI] |")
    P("|---|---|---|---|---|---|---|")
    for sl in ("main", "highpay"):
        m = A["slot"] == sl
        for arm in ("②", "③"):
            d = delta(A, arm, "①", m, W["通年"], False)
            a, b = arm_row(A, arm, m, ND), arm_row(A, "①", m, ND)
            P(f"| {sl} | {arm} | {int(m.sum()):,} | {b['roi']:.2f}% / {b['shown']:.2f}% | {a['roi']:.2f}% / {a['shown']:.2f}% | {d['roi']:+.2f} [{d['roi_ci'][0]:+.2f}, {d['roi_ci'][1]:+.2f}] | {d['sh']:+.2f} [{d['sh_ci'][0]:+.2f}, {d['sh_ci'][1]:+.2f}] |")
    P("")
    # 型別（① の型）
    P("## 8. 型別（k≥1・② − ① の V1・通年）\n")
    P("| 型 | R | ① 回収率 | ② 回収率 | ΔROI [CI] | ① 表示的中 | ② 表示的中 |")
    P("|---|---|---|---|---|---|---|")
    for tl in "ABCDEF":
        m = (A["tl"] == tl)
        if m.sum() < 30: continue
        d = delta(A, "②", "①", m, W["通年"], False)
        a, b = arm_row(A, "②", m, ND), arm_row(A, "①", m, ND)
        P(f"| {tl} | {int(m.sum()):,} | {b['roi']:.2f}% | {a['roi']:.2f}% | {d['roi']:+.2f} [{d['roi_ci'][0]:+.2f}, {d['roi_ci'][1]:+.2f}] | {b['shown']:.2f}% | {a['shown']:.2f}% |")
    P("")

    # ── 8b. ① の商品の性格別（判定外・記述）
    P("## 8b. ① の商品の性格別（判定外・記述）\n")
    P("H19 は ① が売る商品をすべて差し替える（穴系 = `A_ana` と `*_sign`〔看板枠・高額枠〕も的中系へ替わる）。事前登録の母集団は変えず、性格別に分けて ΔROI を見る。\n")
    ana_m = np.array([(p == "A_ana") or p.endswith("_sign") for p in A["plan"]])
    P("| ① の商品の性格 | 範囲 | R | ① 回収率 / 表示的中 / 10万+ | ② 回収率 / 表示的中 / 10万+ | ΔROI [CI] | Δ表示的中 [CI] |")
    P("|---|---|---|---|---|---|---|")
    for nm, gm in (("的中系（A_hit/A_trio/B〜F_hit）", ~ana_m), ("穴系（A_ana・*_sign）", ana_m)):
        for kn in ("全体", "k=1", "k=2", "k=3"):
            m = gm & ks[kn]
            if m.sum() < 30: continue
            d = delta(A, "②", "①", m, W["通年"], False)
            a, b = arm_row(A, "②", m, ND), arm_row(A, "①", m, ND)
            P(f"| {nm} | {kn} | {int(m.sum()):,} | {b['roi']:.2f}% / {b['shown']:.2f}% / {int(b['big'])} | {a['roi']:.2f}% / {a['shown']:.2f}% / {int(a['big'])} | {d['roi']:+.2f} [{d['roi_ci'][0]:+.2f}, {d['roi_ci'][1]:+.2f}] | {d['sh']:+.2f} [{d['sh_ci'][0]:+.2f}, {d['sh_ci'][1]:+.2f}] |")
    P("")
    P("① の 10万+（144件）の出どころ: " + ", ".join(f"{p} {int(((A['plan']==p)&(A['①']['pay']>=1e5)).sum())}" for p in sorted(set(A['plan'])) if ((A['plan']==p)&(A['①']['pay']>=1e5)).sum()) + "。")
    P("")

    # ── 9. 感度（判定外）
    P("## 9. 感度（判定外）: 閾値 0.55 / 0.65\n")
    P("| 閾値 | k=0 / 1 / 2 / 3+（① が売る） | 腕 | 買った件数 | 見送り | 表示的中 | 回収率 | 10万+/日 | ΔROI V1 [CI] | Δ表示的中 V1 [CI] | ΔROI V2 [CI] |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    for th in (0.55, 0.60, 0.65):
        zz = load(th); AA = make_arms(zz)
        mm = np.ones(len(AA["keys"]), bool)
        dist = "/".join(str(int((AA["k"] == i).sum())) for i in range(4))
        r1 = arm_row(AA, "①", mm, ND)
        P(f"| {th:.2f} | {dist} | ① | {r1['R']:,} | 0 | {r1['shown']:.2f}% | {r1['roi']:.2f}% | {r1['big_d']:.4f} | | | |")
        for arm in ("②", "③"):
            r = arm_row(AA, arm, mm, ND)
            d1 = delta(AA, arm, "①", mm, W["通年"], False)
            d2 = delta(AA, arm, "①", mm, W["通年"], True)
            P(f"| {th:.2f} | | {arm} | {r['R']:,} | {r['skip']:,} | {r['shown']:.2f}% | {r['roi']:.2f}% | {r['big_d']:.4f} | {d1['roi']:+.2f} [{d1['roi_ci'][0]:+.2f}, {d1['roi_ci'][1]:+.2f}] | {d1['sh']:+.2f} [{d1['sh_ci'][0]:+.2f}, {d1['sh_ci'][1]:+.2f}] | {d2['roi']:+.2f} [{d2['roi_ci'][0]:+.2f}, {d2['roi_ci'][1]:+.2f}] |")
    P("")
    open(D / "h19" / "h19_tables.md", "w").write("\n".join(OUT))
    pickle.dump(dict(res=res, verdict=verdict), open(D / "h19" / "h19_result.pkl", "wb"))


if __name__ == "__main__":
    main()
