#!/usr/bin/env python3
"""H22 集計: h22_build.py が作った data/exp_bet_review/h22/h22_arms.pkl から表と機械判定を出す（事前登録どおり）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h22_run.py
出力: stdout と data/exp_bet_review/h22/h22_tables.md。

- 母集団 = 台の 7車 全レース（ctx が組めたもの 17,614R）。② は全レースに当てる。① は h15_run.run15（現行ラインナップ・L_lead なし）。
- 見送り: 実際に買う stakes の合成（1/Σ(1/予測オッズ)）× 1万円 < T。入稿ゲート落ちは別に数える（どちらも買わない）。
- 判定1: ② の回収率の CI 下限 > ① の回収率の点推定（1日全体）∧ 上期・下期とも ② の点推定 ≥ ① の点推定。
- 判定2: ②−③（20 seed の中央値）の ΔROI の CI 下限 > 0 ∧ 20 seed 中 18 以上で ② が勝つ。
- CI = 開催日ブートストラップ 2,000 回・95%（上期/下期は当該期の日で再標本化）。表示的中＝払戻 > 投資、的中率＝払戻 > 0、
  ガミ率＝的中のうち払戻 ≦ 投資、10万+＝払戻 ≧ 10万円。/日 は母集団の開催日数で割る。
"""
from __future__ import annotations
import pickle
from collections import Counter
import numpy as np
from h01_common import *   # noqa
import h22_build as B

N_BOOT = 2000
N_SEED = 20
OUT = []
TM = {3: 3.0, 5: 5.0, None: None}          # T(万円) → 合成倍率


def P(s=""):
    print(s, flush=True)
    OUT.append(s)


def fmt(x, d=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def ci(a):
    a = np.asarray(a); a = a[np.isfinite(a)]
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


z = pickle.load(open(D / "h22" / "h22_arms.pkl", "rb"))
arms, cur, races = z["arms"], z["cur"], z["races"]
keys = sorted(arms)
N = len(keys)
days_all = sorted({races[k]["day"] for k in keys})
ND = len(days_all)
dpos = {d: i for i, d in enumerate(days_all)}
DAY = np.array([dpos[races[k]["day"]] for k in keys])
HALF1 = np.array([int(races[k]["day"][5:7]) <= 6 for k in keys])     # 上期
TL_ = np.array([races[k]["tl"] for k in keys])
rng_b = np.random.default_rng(20261021)
days_h1 = np.zeros(ND, bool); days_h1[DAY[HALF1]] = True
days_h2 = np.zeros(ND, bool); days_h2[DAY[~HALF1]] = True


def boot_W(mask_days):
    dd = np.flatnonzero(mask_days)
    return np.stack([np.bincount(rng_b.choice(dd, len(dd)), minlength=ND) for _ in range(N_BOOT)]).astype(float)


W = {"通年": boot_W(np.ones(ND, bool)), "上期": boot_W(days_h1), "下期": boot_W(days_h2)}
PER = {"通年": np.ones(N, bool), "上期": HALF1, "下期": ~HALF1}
NDS = {"通年": ND, "上期": int(days_h1.sum()), "下期": int(days_h2.sum())}

# ───────── 行列の取り出し
_RA = {}


def rowarr(pk, fk):
    if (pk, fk) in _RA:
        return _RA[(pk, fk)]
    ex = np.zeros(N, bool); gate = np.zeros(N, bool)
    comp = np.zeros(N); inv = np.zeros(N); pay = np.zeros(N); n = np.zeros(N, int)
    for j, k in enumerate(keys):
        r = arms[k]["rows"][pk][fk]
        if r:
            ex[j], gate[j], comp[j], inv[j], pay[j], n[j] = True, r["gate"], r["comp"], r["inv"], r["pay"], r["n"]
    _RA[(pk, fk)] = (ex, gate, comp, inv, pay, n)
    return _RA[(pk, fk)]


def arm(pk, form, T):
    """② の腕。form ∈ F1..F6 / F6@2。T ∈ 3, 5, None（万円）。"""
    if form == "F6":
        fk = "F6@5" if T == 5 else "F6@3"          # T なしは T=3万で積んだ買い目から見送りだけ外す（解釈 8）
    else:
        fk = form
    ex, gate, comp, inv, pay, n = rowarr(pk, fk)
    buy = ex & gate
    if T is not None:
        buy = buy & (comp >= TM[T] - 1e-9)
    return dict(buy=buy, inv=np.where(buy, inv, 0.0), pay=np.where(buy, pay, 0.0), n=np.where(buy, n, 0), comp=np.where(buy, comp, 0.0))


def arm_cur():
    buy = np.zeros(N, bool); inv = np.zeros(N); pay = np.zeros(N); n = np.zeros(N, int)
    for j, k in enumerate(keys):
        r = cur.get(k)
        if r:
            buy[j], inv[j], pay[j], n[j] = True, r["inv"], r["pay"], r["n"]
    return dict(buy=buy, inv=inv, pay=pay, n=n, comp=np.zeros(N))


def arm_ctrl(a2, pk, T):
    """④: ② が買ったレースで、同じ点数 n を確率上位から（入稿ゲート・同じ T）。"""
    buy = np.zeros(N, bool); inv = np.zeros(N); pay = np.zeros(N); n = np.zeros(N, int); comp = np.zeros(N)
    for j in np.flatnonzero(a2["buy"]):
        r = arms[keys[j]]["ctrl"].get(int(a2["n"][j]))
        if r is None or not r["gate"]:
            continue
        if T is not None and r["comp"] < TM[T] - 1e-9:
            continue
        buy[j], inv[j], pay[j], n[j], comp[j] = True, r["inv"], r["pay"], r["n"], r["comp"]
    return dict(buy=buy, inv=inv, pay=pay, n=n, comp=comp)


def sub(a, mask):
    """腕 a を mask で絞った腕。"""
    m = a["buy"] & mask
    return dict(buy=m, inv=np.where(m, a["inv"], 0.0), pay=np.where(m, a["pay"], 0.0),
                n=np.where(m, a["n"], 0), comp=np.where(m, a["comp"], 0.0))


def dm(a):
    """日×列: [inv, pay, nbuy, nshown, nhit, nbig, ngami]"""
    m = a["buy"]
    inv, pay = a["inv"][m], a["pay"][m]
    cols = (inv, pay, np.ones(m.sum()), (pay > inv).astype(float), (pay > 0).astype(float),
            (pay >= 1e5).astype(float), ((pay > 0) & (pay <= inv)).astype(float))
    M = np.zeros((ND, 7))
    for c, v in enumerate(cols):
        np.add.at(M[:, c], DAY[m], v)
    return M


def met(T):
    with np.errstate(invalid="ignore", divide="ignore"):
        return dict(roi=T[..., 1] / T[..., 0] * 100, shown=T[..., 3] / T[..., 2] * 100, hit=T[..., 4] / T[..., 2] * 100,
                    gami=T[..., 6] / T[..., 4] * 100, big=T[..., 5], n=T[..., 2], inv=T[..., 0])


def med_hit(a):
    m = a["buy"] & (a["pay"] > 0)
    return float(np.median(a["pay"][m])) if m.any() else float("nan")


def stats(a, per="通年", nd=None):
    msk = PER[per]
    aa = sub(a, msk)
    M = dm(aa); t = M.sum(0); m = met(t)
    Tb = W[per] @ M; bm = met(Tb)
    nd = NDS[per]
    mm = aa["buy"]
    return dict(R=int(t[2]), perday=t[2] / nd, inv=t[0], roi=m["roi"], roi_ci=ci(bm["roi"]), hit=m["hit"], shown=m["shown"],
                gami=m["gami"], big_d=m["big"] / nd, med=med_hit(aa),
                npt=float(aa["n"][mm].mean()) if mm.any() else float("nan"),
                comp=float(aa["comp"][mm].mean()) if mm.any() and aa["comp"][mm].any() else float("nan"),
                big=int(m["big"]))


def pair(a, b, per="通年"):
    """同一レース対（両方が買ったレース）の ΔROI / Δ表示的中 とその CI。"""
    common = a["buy"] & b["buy"] & PER[per]
    A_, B_ = sub(a, common), sub(b, common)
    Ma, Mb = dm(A_), dm(B_)
    ta, tb = met(Ma.sum(0)), met(Mb.sum(0))
    ba, bb = met(W[per] @ Ma), met(W[per] @ Mb)
    return dict(R=int(common.sum()), roi_a=ta["roi"], roi_b=tb["roi"], d_roi=ta["roi"] - tb["roi"], d_roi_ci=ci(ba["roi"] - bb["roi"]),
                sh_a=ta["shown"], sh_b=tb["shown"], d_sh=ta["shown"] - tb["shown"], d_sh_ci=ci(ba["shown"] - bb["shown"]))


def whole_delta(a, b, per="通年"):
    """全体どうし（各腕が買ったレース全部）の ΔROI。"""
    A_, B_ = sub(a, PER[per]), sub(b, PER[per])
    Ma, Mb = dm(A_), dm(B_)
    ta, tb = met(Ma.sum(0)), met(Mb.sum(0))
    ba, bb = met(W[per] @ Ma), met(W[per] @ Mb)
    return dict(d_roi=ta["roi"] - tb["roi"], d_roi_ci=ci(ba["roi"] - bb["roi"]), d_sh=ta["shown"] - tb["shown"], d_sh_ci=ci(ba["shown"] - bb["shown"]))


def random_ctrl(base, a2, seed):
    """③: base（②の T なし版）が買うレースから、② と同じ件数を無作為に残す。"""
    r = np.random.default_rng(1000 + seed)
    pool = np.flatnonzero(base["buy"])
    pick = r.choice(pool, size=int(a2["buy"].sum()), replace=False)
    m = np.zeros(N, bool); m[pick] = True
    return sub(base, m)


def main():
    A1 = arm_cur()
    # ── 母集団
    P("## 1. 母集団と相手の選び方\n")
    P(f"台の 7車 全レース {N:,}R（ctx が組めたもの）・開催日 {ND} 日（上期 {NDS['上期']} / 下期 {NDS['下期']}）。① が売るレース {int(A1['buy'].sum()):,}R"
      f"（{A1['buy'].sum()/ND:.1f}件/日）。\n")
    inf = [arms[k]["info"] for k in keys]
    pp = [i["partners"] for i in inf]
    P("| 項目 | レース数 | 割合 |")
    P("|---|---|---|")
    def row(nm, c):
        P(f"| {nm} | {c:,} | {c/N*100:.1f}% |")
    row("二軸が同じライン（2人以上のライン内）", sum(i["axes_same_line"] for i in inf))
    row("全員単騎（7グループ・ライン情報が欠測）", sum(i["all_single"] for i in inf))
    row("相手 Pp3 = Pline（同じ2車・順序無視）", sum(set(p["Pp3"]) == set(p["Pline"]) for p in pp))
    row("相手 Pline = Pline_t（同じ2車・順序無視）", sum(set(p["Pline"]) == set(p["Pline_t"]) for p in pp))
    row("相手 Pp3 = Pline_t（同じ2車・順序無視）", sum(set(p["Pp3"]) == set(p["Pline_t"]) for p in pp))
    row("3通りとも同じ", sum(set(p["Pp3"]) == set(p["Pline"]) == set(p["Pline_t"]) for p in pp))
    row("3通りとも違う", sum(len({frozenset(p["Pp3"]), frozenset(p["Pline"]), frozenset(p["Pline_t"])}) == 3 for p in pp))
    row("Pline で相手2車とも二軸と同ライン", sum(all(i["lg"][c] in (i["lg"][i["A"]], i["lg"][i["B"]]) and Counter(i["lg"].values())[i["lg"][c]] >= 2 for c in p["Pline"]) for i, p in zip(inf, pp)))
    row("単騎が1人以上いる", sum(i["n_single"] >= 1 for i in inf))
    P("")
    P("頭 H は A,B の pw の高い方（同値は order[0]）。H = order[0]（p3 1位）の割合: "
      f"{sum(i['H']==i['order'][0] for i in inf)/N*100:.1f}%。\n")

    # ── 腕の用意
    forms = ("F1", "F2", "F3", "F4", "F5", "F6")
    Ts = (3, 5, None)
    PKS = B.PKEYS
    ARM = {(pk, f, T): arm(pk, f, T) for pk in PKS for f in forms for T in Ts}
    for pk in PKS:
        ARM[(pk, "F6@2", None)] = arm(pk, "F6@2", None)

    # ── 2. 主の3本
    P("## 2. 主の3本の判定（F6 × 相手 × T=3万円）\n")
    s1 = stats(A1)
    s1h = {p: stats(A1, p) for p in ("上期", "下期")}
    P(f"① 現行ラインナップ（1日全体）: {s1['R']:,}R・{s1['perday']:.1f}件/日・回収率 **{s1['roi']:.2f}%** [{s1['roi_ci'][0]:.2f}, {s1['roi_ci'][1]:.2f}]・"
      f"表示的中 {s1['shown']:.2f}%・的中率 {s1['hit']:.2f}%・ガミ率 {s1['gami']:.2f}%・10万+/日 {s1['big_d']:.4f}・的中時払戻中央 {s1['med']:,.0f}円。"
      f"上期 {s1h['上期']['roi']:.2f}% / 下期 {s1h['下期']['roi']:.2f}%。\n")
    verdict = {}
    P("### 2.1 判定1（プランとして成り立つか）\n")
    P("| 腕 | 買った件数（件/日） | 回収率 [CI] | CI下限 > ①点推定 | 上期 ②/① | 下期 ②/① | 上期・下期とも ② ≥ ① | **判定1** |")
    P("|---|---|---|---|---|---|---|---|")
    main_arms = {pk: ARM[(pk, "F6", 3)] for pk in PKS}
    for pk in PKS:
        a = main_arms[pk]
        s = stats(a); sh = {p: stats(a, p) for p in ("上期", "下期")}
        c1 = s["roi_ci"][0] > s1["roi"]
        c2 = sh["上期"]["roi"] >= s1h["上期"]["roi"] and sh["下期"]["roi"] >= s1h["下期"]["roi"]
        verdict[(pk, 1)] = bool(c1 and c2)
        P(f"| F6×{pk}×3万 | {s['R']:,}（{s['perday']:.1f}） | {s['roi']:.2f}% [{s['roi_ci'][0]:.2f}, {s['roi_ci'][1]:.2f}] | {'○' if c1 else '×'} | "
          f"{sh['上期']['roi']:.2f} / {s1h['上期']['roi']:.2f} | {sh['下期']['roi']:.2f} / {s1h['下期']['roi']:.2f} | {'○' if c2 else '×'} | **{'満たす' if c1 and c2 else '満たさない'}** |")
    P("")
    P("### 2.2 判定2（見送りが効いているか）: ②−③（無作為見送り 20 seed）\n")
    P("③ = ②の T なし版（同じ F6 の買い目・ゲートのみ）が買うレースから、② と同じ件数を全体から無作為に残す。ΔROI は 20 seed の中央値（ブートストラップ各回で中央値を取り、その 2.5/97.5 パーセンタイルを CI とする）。\n")
    P("| 腕 | ② 件数 | ③ の母集団（T なし） | ② 回収率 | ③ 回収率（seed 中央値・範囲） | ΔROI（点推定）[CI] | ② が勝つ seed | CI下限 > 0 | 18/20 以上 | **判定2** | Δ表示的中（点）[CI] |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    res2 = {}
    for pk in PKS:
        a2 = main_arms[pk]; base = ARM[(pk, "F6", None)]
        M2 = dm(a2); b2 = met(W["通年"] @ M2); p2 = met(M2.sum(0))
        rois, shs, bsr, bss = [], [], [], []
        for sd in range(N_SEED):
            a3 = random_ctrl(base, a2, sd)
            M3 = dm(a3); p3_ = met(M3.sum(0)); b3 = met(W["通年"] @ M3)
            rois.append(p3_["roi"]); shs.append(p3_["shown"]); bsr.append(b3["roi"]); bss.append(b3["shown"])
        rois, shs = np.array(rois), np.array(shs)
        d_pt = p2["roi"] - np.median(rois)
        dci = ci(b2["roi"] - np.median(np.stack(bsr), axis=0))
        dsh_pt = p2["shown"] - np.median(shs)
        dsh_ci = ci(b2["shown"] - np.median(np.stack(bss), axis=0))
        wins = int((p2["roi"] > rois).sum())
        c1_, c2_ = dci[0] > 0, wins >= 18
        verdict[(pk, 2)] = bool(c1_ and c2_)
        res2[pk] = dict(d=d_pt, ci=dci, wins=wins, roi3=float(np.median(rois)), roi3_rng=(float(rois.min()), float(rois.max())), sh3=float(np.median(shs)), dsh=dsh_pt, dsh_ci=dsh_ci)
        P(f"| F6×{pk}×3万 | {int(a2['buy'].sum()):,} | {int(base['buy'].sum()):,} | {p2['roi']:.2f}% | {np.median(rois):.2f}%（{rois.min():.2f}〜{rois.max():.2f}） | "
          f"{d_pt:+.2f}pt [{dci[0]:+.2f}, {dci[1]:+.2f}] | {wins}/20 | {'○' if c1_ else '×'} | {'○' if c2_ else '×'} | **{'満たす' if c1_ and c2_ else '満たさない'}** | {dsh_pt:+.2f}pt [{dsh_ci[0]:+.2f}, {dsh_ci[1]:+.2f}] |")
    P("")
    P("### 2.3 機械判定のまとめ\n")
    P("| 腕 | 判定1 | 判定2 | 前向きの紙上記録へ回す候補（1・2 とも満たす） |")
    P("|---|---|---|---|")
    for pk in PKS:
        v1, v2 = verdict[(pk, 1)], verdict[(pk, 2)]
        P(f"| F6×{pk}×3万 | {'満たす' if v1 else '満たさない'} | {'満たす' if v2 else '満たさない'} | {'候補（探索扱い）' if v1 and v2 else '候補ではない'} |")
    P("")
    P("2026 は消費済みのため、基準を満たしても前向きの紙上記録へ回すだけで本番は変わらない。3本で多重比較になるので、満たしても探索扱い。\n")

    # ── 2.4 同一レース対（① 対 ②）・形の制約（④）
    P("### 2.4 ① との同一レース対と、形の制約（④）の効果（主の3本）\n")
    P("同一レース対 = ① と ② の両方が買ったレース（② は ① が売らないレースも買うので ① より広い）。④ = ② が買ったレースで同じ点数を確率上位から（入稿ゲート・T=3万 を同じに掛ける）。\n")
    P("| 腕 | 範囲 | 対象R | 腕の回収率 | 比較の回収率 | ΔROI [CI] | 腕の表示的中 | 比較の表示的中 | Δ表示的中 [CI] |")
    P("|---|---|---|---|---|---|---|---|---|")
    CTRL = {}
    for pk in PKS:
        a2 = main_arms[pk]
        CTRL[pk] = arm_ctrl(a2, pk, 3)
        for per in ("通年", "上期", "下期"):
            d = pair(a2, A1, per)
            P(f"| F6×{pk}×3万 vs ① | {per} | {d['R']:,} | {d['roi_a']:.2f}% | {d['roi_b']:.2f}% | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] | {d['sh_a']:.2f}% | {d['sh_b']:.2f}% | {d['d_sh']:+.2f} [{d['d_sh_ci'][0]:+.2f}, {d['d_sh_ci'][1]:+.2f}] |")
        for per in ("通年", "上期", "下期"):
            d = pair(a2, CTRL[pk], per)
            P(f"| F6×{pk}×3万 vs ④ | {per} | {d['R']:,} | {d['roi_a']:.2f}% | {d['roi_b']:.2f}% | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] | {d['sh_a']:.2f}% | {d['sh_b']:.2f}% | {d['d_sh']:+.2f} [{d['d_sh_ci'][0]:+.2f}, {d['d_sh_ci'][1]:+.2f}] |")
    P("")
    P("④ が ② の何割のレースを買えたか、④ の全体の成績:\n")
    P("| 腕 | ② 件数 | ④ が買えた件数 | ④ の件/日 | ④ 回収率 [CI] | ④ 表示的中 | ④ 平均合成 | ④ 的中時払戻中央 |")
    P("|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        c = CTRL[pk]; s = stats(c)
        P(f"| F6×{pk}×3万 | {int(main_arms[pk]['buy'].sum()):,} | {int(c['buy'].sum()):,} | {s['perday']:.1f} | {s['roi']:.2f}% [{s['roi_ci'][0]:.2f}, {s['roi_ci'][1]:.2f}] | {s['shown']:.2f}% | {fmt(s['comp'])}倍 | {s['med']:,.0f}円 |")
    P("")

    # ── 3. 記述表（全組み合わせ）
    P("## 3. 記述: 形 × 相手 × T の全組み合わせ（通年）\n")
    P("T なし（ゲートのみ）の F6 は「T=3万で積んだ買い目から見送りだけ外したもの」。`F6@2` は T=2万（入稿ゲートの床）で積み・見送りなし（参考）。回収率の CI は開催日ブートストラップ。"
      "ΔROI(対①同一レース対) は ① と ② の両方が買ったレースだけで ②−①。\n")
    P("| 相手 | 形 | T | 件/日 | 投資(百万円) | 回収率 [CI] | 表示的中 | 的中率 | ガミ率 | 的中時払戻中央(円) | 10万+/日 | 平均点数 | 平均合成(予測) | ①と両方買う R | ΔROI 対① [CI] | Δ表示的中 対① |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        for f in forms + ("F6@2",):
            for T in (Ts if f != "F6@2" else (None,)):
                a = ARM[(pk, f, T)]
                s = stats(a); d = pair(a, A1)
                Tl = {3: "3万", 5: "5万", None: "なし"}[T]
                if f == "F6@2":
                    Tl = "なし(2万積)"
                P(f"| {pk} | {f} | {Tl} | {s['perday']:.1f} | {s['inv']/1e6:.1f} | {s['roi']:.2f}% [{s['roi_ci'][0]:.1f}, {s['roi_ci'][1]:.1f}] | {s['shown']:.2f}% | {s['hit']:.2f}% | "
                  f"{fmt(s['gami'])}% | {fmt(s['med'],0)} | {s['big_d']:.4f} | {fmt(s['npt'])} | {fmt(s['comp'])} | {d['R']:,} | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] | {d['d_sh']:+.2f} |")
    P("")
    P("参考 ①: 件/日 %.1f・回収率 %.2f%%・表示的中 %.2f%%・的中率 %.2f%%・ガミ率 %.2f%%・的中時払戻中央 %s円・10万+/日 %.4f・平均点数 %.2f。\n"
      % (s1["perday"], s1["roi"], s1["shown"], s1["hit"], s1["gami"], f"{s1['med']:,.0f}", s1["big_d"], s1["npt"]))

    # ── 3b. 形の制約の効果 ②−④（全組み合わせ・記述）
    P("## 3b. 記述: 形の制約の効果 ②−④（同じレース・同じ点数を確率上位から・入稿ゲートと T を同じに掛ける・通年）\n")
    P("④ は ② が買ったレースのうち、④ も入稿ゲートと T を通ったレースだけ。ΔROI・Δ表示的中は両方が買った同一レース対（= ④ が買ったレース）。\n")
    P("| 相手 | 形 | T | ② 件数 | ④ が買えた件数 | ② 回収率（同一レース） | ④ 回収率 | ΔROI ②−④ [CI] | ② 表示的中 | ④ 表示的中 | Δ表示的中 [CI] |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        for f in forms:
            for T in Ts:
                a = ARM[(pk, f, T)]
                c = arm_ctrl(a, pk, T)
                d = pair(a, c)
                Tl = {3: "3万", 5: "5万", None: "なし"}[T]
                P(f"| {pk} | {f} | {Tl} | {int(a['buy'].sum()):,} | {int(c['buy'].sum()):,} | {d['roi_a']:.2f}% | {d['roi_b']:.2f}% | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] | {d['sh_a']:.2f}% | {d['sh_b']:.2f}% | {d['d_sh']:+.2f} [{d['d_sh_ci'][0]:+.2f}, {d['d_sh_ci'][1]:+.2f}] |")
    P("")

    # ── 4. 上期・下期（主の3本）
    P("## 4. 主の3本の上期・下期\n")
    P("| 腕 | 期 | 件/日 | 回収率 [CI] | 表示的中 | 的中率 | ガミ率 | 的中時払戻中央 | 10万+/日 | 平均点数 | 平均合成 |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, a in [("①", A1)] + [(f"F6×{pk}×3万", main_arms[pk]) for pk in PKS]:
        for per in ("上期", "下期"):
            s = stats(a, per)
            P(f"| {lab} | {per} | {s['perday']:.1f} | {s['roi']:.2f}% [{s['roi_ci'][0]:.1f}, {s['roi_ci'][1]:.1f}] | {s['shown']:.2f}% | {s['hit']:.2f}% | {fmt(s['gami'])}% | {fmt(s['med'],0)} | {s['big_d']:.4f} | {fmt(s['npt'])} | {fmt(s['comp'])} |")
    P("")

    # ── 5. 型別
    P("## 5. 型別（A〜F）— 主の3本と ①（型は ② が買ったレースの型）\n")
    P("| 腕 | 型 | 台のR | ② が買ったR（買う率） | ② 回収率 | ② 表示的中 | ② 的中時払戻中央 | ① が売るR（同型） | ① 回収率 | ① 表示的中 | 同一レース対 R | ΔROI 対① [CI] |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        a = main_arms[pk]
        for t in "ABCDEF":
            m = TL_ == t
            s2 = stats(sub(a, m)); s1t = stats(sub(A1, m)); d = pair(sub(a, m), A1)
            P(f"| F6×{pk}×3万 | {t} | {int(m.sum()):,} | {s2['R']:,}（{s2['R']/m.sum()*100:.0f}%） | {fmt(s2['roi'])}% | {fmt(s2['shown'])}% | {fmt(s2['med'],0)} | {s1t['R']:,} | {fmt(s1t['roi'])}% | {fmt(s1t['shown'])}% | {d['R']:,} | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] |")
    P("")

    # ── 6. F6 が選んでいる形
    P("## 6. F6 が実際に選んでいる目（T=3万・買ったレース）\n")
    P("U の 12 点の分類（H=頭、O=もう一方の軸、P=相手）: HOP（F1∩F2）／OHP（F2のみ）／HPO（F1∩F3）／OPH（F3のみ）／PHO・POH（相手が頭＝F5/F6 だけ）。\n")
    P("| 相手 | 買ったR | 平均点数 | 1点 | 2〜3点 | 4〜6点 | 7〜12点 | 全点が F1 内 | 全点が F2 内 | 全点が F3 内 | F1/F2/F3 のどれにも収まらない | 点数あたりの内訳 HOP / OHP / HPO / OPH / 相手頭 |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        a = main_arms[pk]
        js = np.flatnonzero(a["buy"])
        cls = Counter(); nl = []; inF = {1: 0, 2: 0, 3: 0}; none_ = 0; tot = 0
        for j in js:
            inf_ = arms[keys[j]]["info"]
            H_, O = inf_["H"], inf_["O"]
            legs = list(arms[keys[j]]["rows"][pk]["F6@3"]["stakes"])
            nl.append(len(legs))
            s1_ = {1: True, 2: True, 3: True}
            for q in legs:
                c = "HOP" if q[:2] == (H_, O) else "OHP" if q[:2] == (O, H_) else "HPO" if (q[0] == H_ and q[2] == O) else "OPH" if (q[0] == O and q[2] == H_) else "PXX"
                cls[c] += 1; tot += 1
                s1_[1] &= c in ("HOP", "HPO"); s1_[2] &= c in ("HOP", "OHP"); s1_[3] &= c in ("HPO", "OPH")
            for k_ in (1, 2, 3):
                inF[k_] += s1_[k_]
            none_ += not any(s1_.values())
        nl = np.array(nl); R_ = len(nl)
        P(f"| {pk} | {R_:,} | {nl.mean():.2f} | {(nl==1).sum()/R_*100:.1f}% | {((nl>=2)&(nl<=3)).sum()/R_*100:.1f}% | {((nl>=4)&(nl<=6)).sum()/R_*100:.1f}% | {(nl>=7).sum()/R_*100:.1f}% | "
          f"{inF[1]/R_*100:.1f}% | {inF[2]/R_*100:.1f}% | {inF[3]/R_*100:.1f}% | {none_/R_*100:.1f}% | "
          + " / ".join(f"{cls[c]/tot*100:.1f}%" for c in ("HOP", "OHP", "HPO", "OPH")) + f" / {cls['PXX']/tot*100:.1f}% |")
    P("")
    P("（F1/F2/F3 の集合は互いに重なる＝1点だけのときは複数に入る。「どれにも収まらない」は相手頭の目を含むなど 3 形のいずれの部分集合でもないもの。）\n")

    # ── 7. 見送り
    P("## 7. 見送り率と、見送ったレースの回収率（買っていたら）\n")
    P("見送り = T なし版（ゲートのみ・同じ買い目）が買うレースのうち、合成 < 3万円で外れたもの。入稿ゲート落ち（T なしでも買えない）は別掲。\n")
    P("| 腕 | 台のR | 入稿ゲート落ち | T なし版が買うR | ② が買うR | 見送り（合成<3万） | 見送り率（T なし版比） | 見送りレースの回収率（買っていたら）[CI] | 見送りの表示的中 | 見送りの的中時払戻中央 | ② の回収率 | T なし版の回収率 |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        a2 = main_arms[pk]; base = ARM[(pk, "F6", None)]
        ex, gate, *_ = rowarr(pk, "F6@3")
        skipped = sub(base, base["buy"] & ~a2["buy"])
        s = stats(skipped); s2 = stats(a2); sb = stats(base)
        P(f"| F6×{pk} | {N:,} | {int((ex & ~gate).sum()):,} | {int(base['buy'].sum()):,} | {int(a2['buy'].sum()):,} | {int(skipped['buy'].sum()):,} | {skipped['buy'].sum()/base['buy'].sum()*100:.1f}% | "
          f"{s['roi']:.2f}% [{s['roi_ci'][0]:.1f}, {s['roi_ci'][1]:.1f}] | {s['shown']:.2f}% | {fmt(s['med'],0)} | {s2['roi']:.2f}% | {sb['roi']:.2f}% |")
    P("")

    # ── 8. 上位除外
    P("## 8. 払戻の上位 1・3・5 件（レース単位）を除いた回収率\n")
    P("| 腕 | 除外なし | 上位1除外 | 上位3除外 | 上位5除外 | 10万+ 件数 |")
    P("|---|---|---|---|---|---|")
    for lab, a in [("①", A1)] + [(f"F6×{pk}×3万", main_arms[pk]) for pk in PKS]:
        mm = a["buy"]; inv, pay = a["inv"][mm], a["pay"][mm]; o = np.argsort(-pay)
        def roi_ex(n):
            keep = np.ones(len(pay), bool); keep[o[:n]] = False
            return pay[keep].sum() / inv[keep].sum() * 100
        P(f"| {lab} | {roi_ex(0):.2f}% | {roi_ex(1):.2f}% | {roi_ex(3):.2f}% | {roi_ex(5):.2f}% | {int((pay>=1e5).sum())} |")
    P("")

    # ── 9. F6 の T=3万 と T=2万 積み（点数の効果の切り分け）
    P("## 9. 見送りと点数の切り分け（判定外）\n")
    P("F6 を T=3万で積む ↔ T=2万（入稿ゲートの床）で積む（見送りなし）。同一レース対（両方が買う）。\n")
    P("| 相手 | 対象R | T=3万積み（見送りあり）回収率 | T=2万積み 回収率 | ΔROI [CI] | Δ表示的中 [CI] | T=3万積み 平均点数 | T=2万積み 平均点数 |")
    P("|---|---|---|---|---|---|---|---|")
    for pk in PKS:
        a, b = main_arms[pk], ARM[(pk, "F6@2", None)]
        d = pair(a, b)
        common = a["buy"] & b["buy"]
        P(f"| {pk} | {d['R']:,} | {d['roi_a']:.2f}% | {d['roi_b']:.2f}% | {d['d_roi']:+.2f} [{d['d_roi_ci'][0]:+.2f}, {d['d_roi_ci'][1]:+.2f}] | {d['d_sh']:+.2f} [{d['d_sh_ci'][0]:+.2f}, {d['d_sh_ci'][1]:+.2f}] | {a['n'][common].mean():.2f} | {b['n'][common].mean():.2f} |")
    P("")
    open(D / "h22" / "h22_tables.md", "w").write("\n".join(OUT))
    pickle.dump(dict(verdict=verdict, res2=res2), open(D / "h22" / "h22_result.pkl", "wb"))


if __name__ == "__main__":
    main()
