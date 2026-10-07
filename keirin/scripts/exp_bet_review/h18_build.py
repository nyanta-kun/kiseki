#!/usr/bin/env python3
"""H18 段1: 2025・7車・型F の全レースについて、同一レースの F_hit / F_sign の払戻と、事前登録の要因を作る。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h18_build.py
出力: data/exp_bet_review/h18/rows.pkl（DataFrame・git 管理外）

- 台・組み方は h17_run と同じ（h15_run.hit_counterpart と同じ規則で F_sign も組む）。
- sign 対象（現行ラインナップが F_sign を売るレース）は h17 と同じ集合で、payoff が recs の F_sign 行と一致することを assert。
- 要因は事前登録の固定リストのみ。すべて朝 07:00 に得られる量（台は vintage の p3/pw・探索用予測オッズ・WT 印/並び）。
"""
from __future__ import annotations
import json, pickle
import numpy as np, pandas as pd
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import h15_run as H
from src.type_lab import PLANS

OUT = D / "h18"


def build_one(x, plan_key):
    got = S.build(x, PLANS[plan_key])
    if not got:
        return None, "組めない"
    stakes, odds, used, mean = got
    if not S.gate_ok(stakes, odds, mean):
        return None, "入稿ゲート落ち"
    inv, pay = S.settle(x, stakes, used.bet_type == "trio")
    return dict(inv=inv, pay=pay, n=len(stakes), plan=used.key), None


def entropy(p):
    p = np.asarray(p, float); p = p[p > 0]
    return float(-(p * np.log(p)).sum())


def main():
    b = load_board_2025(); S._Z = {k: b[k] for k in S._NEED}; z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    rowof = {cache[i].key: i for i in ok}
    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    recs = H.run15(ok, cache)
    assert len(ref) == len(recs) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in recs)) < 1e-6
    sign_rec = {r["race_key"]: r for r in recs if r["plan"] == "F_sign"}
    print(f"[台] ctx {len(ok)}R / 型F {sum(1 for i in ok if cache[i].shape.type_label=='F')}R / 現行 F_sign {len(sign_rec)}R", flush=True)

    # 補助データ
    bank = json.load(open(OUT / "bank_length.json"))
    start = json.load(open(D / "start_at_2025.json"))
    pr = pickle.load(open(D / "h13" / "pred_2025.pkl", "rb"))
    bpred = pr.groupby("race_key")["p_new"].max().to_dict()      # レース内 1 位の B 確率
    bsum = pr.groupby("race_key")["p_new"].sum().to_dict()

    rows, miss = [], dict()
    for i in ok:
        x = cache[i]
        if x.shape.type_label != "F":
            continue
        k = x.key
        h, why_h = build_one(x, "F_hit")
        s, why_s = build_one(x, "F_sign")
        if h is None or s is None:
            miss[(why_h, why_s)] = miss.get((why_h, why_s), 0) + 1
            continue
        if k in sign_rec:       # 現行の F_sign 行と同じ組み方か
            r = sign_rec[k]
            assert abs(r["pay"] - s["pay"]) < 1e-6 and abs(r["inv"] - s["inv"]) < 1e-6, k
        # --- 市場（予測オッズ）
        po = np.array(list(x.po_tf.values()), float)
        pn = (1.0 / po); pn = pn / pn.sum()
        top3 = float(np.sort(pn)[-3:].sum())
        f = float(po.min())
        # --- WT ◎○ とモデル上位2車
        rows.append(dict(key=k, date=x.date, f=f, top3=top3, po_ent=entropy(pn), i=i,
                         sign=int(k in sign_rec), rtype=x.rtype, cupg=x.cupg,
                         axis_sum=float(x.shape.axis_sum), gap=float(x.shape.gap), pw_ent=float(x.shape.pw_ent),
                         win_gap12=float(x.shape.win_gap), arare=int(x.shape.arare),
                         order=x.shape.order,
                         inv_h=h["inv"], pay_h=h["pay"], n_h=h["n"], inv_s=s["inv"], pay_s=s["pay"], n_s=s["n"],
                         pay_tf=float(x.pay_tf), win_tf=x.win_tf))
    print(f"[型F 対] {len(rows)}R  対にならない {miss}", flush=True)
    df = pd.DataFrame(rows)

    # --- 盤から引く列（要因）
    zz = np.load(D / "race_type_board.npz", allow_pickle=True)
    keyidx = {str(k): j for j, k in enumerate(zz["KEY"])}
    jj = np.array([keyidx[k] for k in df.key])
    MK = zz["A_prediction_mark"][jj]; LG = zz["LG"][jj]; ST = zz["ST"][jj]; RP = zz["A_race_point"][jj]
    P3 = zz["P3"][jj]; NL = zz["A_n_lines"][jj]; DAYI = zz["DAYI"][jj]; VEN = zz["VENUE"][jj]; GR = zz["GRADE"][jj]
    ov, nlines, nnige, nsolo, rpgap = [], [], [], [], []
    for r in range(len(df)):
        order = df["order"].iloc[r]
        top2 = set(order[:2])
        wt = {c for c in range(1, 8) if MK[r][c - 1] in (1.0, 2.0)}
        ov.append(len(top2 & wt))
        groups = {}
        for c in range(1, 8):
            groups.setdefault(str(LG[r][c - 1]), []).append(c)
        sums = sorted((sum(float(RP[r][c - 1]) for c in cs) for cs in groups.values()), reverse=True)
        nlines.append(len(groups))
        nsolo.append(sum(1 for cs in groups.values() if len(cs) == 1))
        nnige.append(sum(1 for c in range(1, 8) if str(ST[r][c - 1]) == "逃"))
        rpgap.append(sums[0] - sums[1] if len(sums) >= 2 else 0.0)
    df["wt_overlap_n"] = ov; df["n_lines"] = nlines; df["n_solo"] = nsolo; df["n_nige"] = nnige
    df["line_rp_gap_top"] = rpgap
    df["is_s"] = (GR == "S級").astype(int)
    df["dayi"] = DAYI.astype(int)
    df["bank"] = [bank.get(str(v)) for v in VEN]
    hr = []
    for k in df.key:
        v = start.get(k)
        hr.append(((int(v[1]) + 9 * 3600) // 3600) % 24 if v else np.nan)
    df["start_hour"] = hr
    df["night"] = (df["start_hour"] >= 17).astype(float).where(df["start_hour"].notna())
    df["rt3"] = np.where(df.rtype.isin(["決勝", "チャレンジ決勝"]), "決勝",
                         np.where(df.rtype.isin(["準決勝", "チャレンジ準決勝"]), "準決勝", "その他"))
    df["b_top1"] = [bpred.get(k, np.nan) for k in df.key]
    df["b_sum"] = [bsum.get(k, np.nan) for k in df.key]
    # --- 目的変数
    df["Y"] = (df.pay_h > df.pay_s).astype(int)
    df["Y_firm"] = (df.pay_tf <= 20.0).astype(int)
    df["Y_axis12"] = [int({o[0], o[1]} == set(w[:2])) for o, w in zip(df["order"], df["win_tf"])]
    df = df.drop(columns=["order", "win_tf"])
    df.to_pickle(OUT / "rows.pkl")
    print(df.describe().T.to_string(), flush=True)
    print("欠損:", df.isna().sum()[df.isna().sum() > 0].to_dict())
    print("Y 平均 H1/H2:", df[df.date <= "2025-06-30"].Y.mean(), df[df.date > "2025-06-30"].Y.mean(),
          " sign 対象:", int(df.sign.sum()))
    # 目視: 目的変数の定義を1レース表示（sign 対象・Y=1 と Y=0 を各1）
    for yy in (1, 0):
        r = df[(df.sign == 1) & (df.Y == yy)].iloc[3]
        print(f"[目視 Y={yy}] {r.key} {r.rtype} f={r.f:.2f} F_hit: {r.n_h}点 投資{r.inv_h:.0f} 払戻{r.pay_h:.0f} | "
              f"F_sign: {r.n_s}点 投資{r.inv_s:.0f} 払戻{r.pay_s:.0f} | 確定三連単 {r.pay_tf:.1f}倍 | "
              f"wt_overlap={r.wt_overlap_n} n_lines={r.n_lines} n_nige={r.n_nige} B1={r.b_top1:.3f}", flush=True)


if __name__ == "__main__":
    main()
