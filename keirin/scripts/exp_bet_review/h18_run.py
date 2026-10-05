#!/usr/bin/env python3
"""H18 段2: 要因ごとの ΔAUC（対 f だけ）・全要因 LightGBM・停止判定・（通過時のみ）腕・オラクル。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h18_run.py
学習 = 2025 上期（1〜6月）・評価 = 2025 下期（7〜12月）。README「H18 改訂」。ハイパーパラメータは固定（H2 を見て変えない）。
"""
from __future__ import annotations
import pickle, sys, time
import numpy as np, pandas as pd
from scipy.stats import rankdata
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss
from h01_common import D

OUT = D / "h18"
N_BOOT, SEEDS = 2000, 20
# 事前登録の要因（解析中に足さない）。(名前, 列のリスト, 区分)
FACTORS = [
    ("top3（予測確率上位3目の合計）", ["top3"], "市場"),
    ("po_ent（予測オッズの散らばり）", ["po_ent"], "市場"),
    ("wt_overlap_n（WT◎○とモデル上位2車の重なり）", ["wt_overlap_n"], "市場"),
    ("axis_sum", ["axis_sum"], "モデル"),
    ("win_gap12（1着率の1位-2位）", ["win_gap12"], "モデル"),
    ("pw_ent", ["pw_ent"], "モデル"),
    ("gap", ["gap"], "モデル"),
    ("n_lines（ライン数）", ["n_lines"], "ライン"),
    ("n_nige（逃げの人数）", ["n_nige"], "ライン"),
    ("n_solo（単騎の数）", ["n_solo"], "ライン"),
    ("line_rp_gap_top（最強ライン−次点ラインの得点合計差）", ["line_rp_gap_top"], "ライン"),
    ("arare", ["arare"], "ライン"),
    ("種別（決勝/準決勝/その他）", ["rt_final", "rt_semi"], "文脈"),
    ("グレード（S級）", ["is_s"], "文脈"),
    ("周長", ["bank"], "文脈"),
    ("昼夜（発走17時以降）", ["night"], "文脈"),
    ("開催日目", ["dayi"], "文脈"),
    ("B 取り予測（レース内1位の確率）", ["b_top1"], "H13"),
]
ALL_COLS = ["lf"] + [c for _, cs, _ in FACTORS for c in cs]
LGB_PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=7, min_data_in_leaf=60, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=4)
LGB_ROUNDS, LGB_SEEDS = 150, 5


def auc_w(y, p, w):
    """重み（ブートストラップの出現回数）付き AUC（同点は 0.5）。"""
    m = w > 0
    y, p, w = y[m], p[m], w[m]
    u, inv = np.unique(p, return_inverse=True)
    wp = np.bincount(inv, weights=w * (y == 1), minlength=len(u))
    wn = np.bincount(inv, weights=w * (y == 0), minlength=len(u))
    Wp, Wn = wp.sum(), wn.sum()
    if Wp == 0 or Wn == 0:
        return np.nan
    below = np.cumsum(wn) - wn
    return float((wp * (below + 0.5 * wn)).sum() / (Wp * Wn))


def ci(a):
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def fit_logit(Xtr, ytr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0); sd[sd == 0] = 1.0
    m = LogisticRegression(C=1.0, max_iter=1000).fit((Xtr - mu) / sd, ytr)
    return m.predict_proba((Xte - mu) / sd)[:, 1], m.coef_[0]


def fit_lgb(Xtr, ytr, Xte):
    ps = []
    for s in range(LGB_SEEDS):
        b = lgb.train({**LGB_PARAMS, "seed": s}, lgb.Dataset(Xtr, ytr), LGB_ROUNDS)
        ps.append(b.predict(Xte))
    return np.mean(ps, 0)


def main():
    t0 = time.time()
    df = pd.read_pickle(OUT / "rows.pkl")
    df = df[df.b_top1.notna()].copy()                   # B 予測の無い 1R だけ除く
    df["lf"] = np.log(df.f)
    df["rt_final"] = (df.rt3 == "決勝").astype(float); df["rt_semi"] = (df.rt3 == "準決勝").astype(float)
    for c in ("night", "bank"):
        df[c] = df[c].astype(float)
    assert df[ALL_COLS].isna().sum().sum() == 0, df[ALL_COLS].isna().sum()[lambda s: s > 0]
    H1 = (df.date <= "2025-06-30").to_numpy(); H2 = ~H1
    Q3 = (df.date >= "2025-07-01").to_numpy() & (df.date <= "2025-09-30").to_numpy(); Q4 = (df.date >= "2025-10-01").to_numpy()
    print(f"[母集団] 型F 対 {len(df)}R  学習(上期) {H1.sum()}  評価(下期) {H2.sum()}（7-9月 {Q3.sum()} / 10-12月 {Q4.sum()}）  sign 対象 下期 {int(df.sign[H2].sum())}R", flush=True)
    days = sorted(df.date[H2].unique()); dpos = {d: j for j, d in enumerate(days)}; ND = len(days)
    print(f"  下期の開催日 {ND}", flush=True)
    rng = np.random.default_rng(20261018)
    bw = np.stack([np.bincount(rng.integers(0, ND, ND), minlength=ND) for _ in range(N_BOOT)]).astype(float)   # (B, ND)
    d_of = np.array([dpos[d] for d in df.date[H2]])
    pops = {"型F 全レース(下期)": np.ones(H2.sum(), bool), "sign 対象(下期)": df.sign[H2].to_numpy() == 1}
    out = {}

    for ylabel in ("Y", "Y_firm", "Y_axis12"):
        y = df[ylabel].to_numpy()
        ytr, yte = y[H1], y[H2]
        # f だけ
        p0, c0 = fit_logit(df.loc[H1, ["lf"]].to_numpy(float), ytr, df.loc[H2, ["lf"]].to_numpy(float))
        preds = {"f だけ": p0}
        coefs = {}
        for name, cs, grp in FACTORS:
            cols = ["lf"] + cs
            p, c = fit_logit(df.loc[H1, cols].to_numpy(float), ytr, df.loc[H2, cols].to_numpy(float))
            preds[name] = p; coefs[name] = c
        p_all_logit, _ = fit_logit(df.loc[H1, ALL_COLS].to_numpy(float), ytr, df.loc[H2, ALL_COLS].to_numpy(float))
        p_lgb = fit_lgb(df.loc[H1, ALL_COLS].to_numpy(float), ytr, df.loc[H2, ALL_COLS].to_numpy(float))
        preds["全要因 logistic"] = p_all_logit; preds["全要因 LightGBM（f を含む）"] = p_lgb
        # f を除く単独の LightGBM（参考: 市場 f 抜きでどこまで見分けられるか）
        cols_nof = [c for c in ALL_COLS if c != "lf"]
        p_lgb_nof = fit_lgb(df.loc[H1, cols_nof].to_numpy(float), ytr, df.loc[H2, cols_nof].to_numpy(float))
        preds["（参考）全要因 LightGBM（f を含まない）"] = p_lgb_nof
        out[ylabel] = dict(preds=preds, coefs=coefs)
        if ylabel != "Y":
            # 記述: 全要因 LGB の ΔAUC と f 単独だけ
            for pn, pm in pops.items():
                a0 = roc_auc_score(yte[pm], p0[pm]); a1 = roc_auc_score(yte[pm], p_lgb[pm])
                print(f"[記述 {ylabel}] {pn}: 基準 {yte[pm].mean():.3f} / AUC f だけ {a0:.4f} → 全要因LGB {a1:.4f}（Δ{a1-a0:+.4f}）", flush=True)
            continue

        for pn, pm in pops.items():
            print(f"\n===== 目的変数 Y（F_hit>F_sign）/ {pn} n={pm.sum()}（Y 平均 {yte[pm].mean():.3f}）=====", flush=True)
            idxs = {"全体": pm, "7-9月": pm & Q3[H2], "10-12月": pm & Q4[H2]}
            rows = []
            ll0 = log_loss(yte[pm], np.clip(p0[pm], 1e-9, 1 - 1e-9))
            a0_all = roc_auc_score(yte[pm], p0[pm])

            def boot(p, mask):
                """(AUC 点推定, Δ 用の bootstrap 配列) — mask 内で日を再標本化。"""
                w_all = bw[:, d_of[mask]]
                v = np.array([auc_w(yte[mask], p[mask], w_all[b]) for b in range(N_BOOT)])
                return v
            base_boot = {k: boot(p0, m_) for k, m_ in idxs.items()}
            for name in list(preds)[1:]:
                p = preds[name]
                rec = dict(name=name)
                for k, m_ in idxs.items():
                    a = roc_auc_score(yte[m_], p[m_]); a_0 = roc_auc_score(yte[m_], p0[m_])
                    rec[k] = a - a_0
                    if k == "全体":
                        bv = boot(p, m_)
                        rec["ci"] = ci(bv - base_boot[k])
                        rec["auc"] = a; rec["auc0"] = a_0
                        rec["dll"] = log_loss(yte[m_], np.clip(p0[m_], 1e-9, 1 - 1e-9)) - log_loss(yte[m_], np.clip(p[m_], 1e-9, 1 - 1e-9))
                        # Δlogloss の bootstrap
                        w_all = bw[:, d_of[m_]]
                        l0 = -(yte[m_] * np.log(np.clip(p0[m_], 1e-9, 1)) + (1 - yte[m_]) * np.log(np.clip(1 - p0[m_], 1e-9, 1)))
                        l1 = -(yte[m_] * np.log(np.clip(p[m_], 1e-9, 1)) + (1 - yte[m_]) * np.log(np.clip(1 - p[m_], 1e-9, 1)))
                        dd = (w_all * (l0 - l1)[None, :]).sum(1) / w_all.sum(1)
                        rec["dll_ci"] = ci(dd)
                rows.append(rec)
            # 要因単独の AUC（f なし・向きは生の値）
            for r in rows:
                nm = r["name"]
                for fn, cs, grp in FACTORS:
                    if fn == nm:
                        col = cs[0]
                        r["solo"] = roc_auc_score(yte[pm], df.loc[H2, col].to_numpy(float)[pm]) if len(cs) == 1 else np.nan
                        r["coef"] = float(coefs[fn][-1]) if len(cs) == 1 else np.nan
                        r["grp"] = grp
            out[(ylabel, pn)] = rows
            print(f"f だけ: AUC {a0_all:.4f}  logloss {ll0:.4f}（基準率のみ logloss {log_loss(yte[pm], np.full(pm.sum(), yte[pm].mean())):.4f}）", flush=True)
            hdr = "| 要因 | 区分 | 単独AUC | AUC(f+要因) | ΔAUC [CI] | Δlogloss [CI] | Δ7-9月 | Δ10-12月 | 符号一致 | ノイズ扱い(片方のみ>0.02) |"
            print(hdr); print("|" + "---|" * 10)
            for r in rows:
                s1, s2 = r["7-9月"], r["10-12月"]
                agree = (s1 > 0) == (s2 > 0)
                noise = (abs(s1) > 0.02) != (abs(s2) > 0.02)
                r["agree"], r["noise"] = agree, noise
                print(f"| {r['name']} | {r.get('grp','')} | {r.get('solo', float('nan')):.4f} | {r['auc']:.4f} | {r['全体']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}] | "
                      f"{r['dll']:+.4f} [{r['dll_ci'][0]:+.4f}, {r['dll_ci'][1]:+.4f}] | {s1:+.4f} | {s2:+.4f} | {'○' if agree else '×'} | {'ノイズ' if noise else '-'} |", flush=True)

    # ---- 停止判定（主 = 型F 全レース(下期) の 全要因 LightGBM）
    key_name = "全要因 LightGBM（f を含む）"
    stop_info = {}
    for pn in pops:
        r = [x for x in out[("Y", pn)] if x["name"] == key_name][0]
        lo = r["ci"][0]
        stop_info[pn] = (r["全体"], r["ci"], lo <= 0.01)
        print(f"\n[停止判定 / {pn}] 全要因 LightGBM ΔAUC {r['全体']:+.4f} CI[{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}] → CI下限 {'≦' if lo <= 0.01 else '>'} 0.01 → {'停止' if lo <= 0.01 else '通過'}", flush=True)
    out["stop"] = stop_info
    pickle.dump(out, open(OUT / "h18_result.pkl", "wb"))
    primary_stop = stop_info["型F 全レース(下期)"][2]
    print(f"\n[主判定] {'停止（規則は作らない）' if primary_stop else '通過'}  {time.time()-t0:.0f}s", flush=True)

    # ---- 天井（オラクル）: 看板枠の対象
    oracle(df, H1, H2, Q3, Q4)
    if not primary_stop:
        arms(df, H2, out["Y"]["preds"][key_name])


def metrics(inv, pay, days_idx, ND_, w=None):
    inv = np.asarray(inv); pay = np.asarray(pay)
    return dict(R=len(inv), roi=pay.sum() / inv.sum() * 100, shown=float((pay > inv).mean() * 100),
                big=int((pay >= 1e5).sum()), hit=int((pay > 0).sum()),
                medhit=float(np.median(pay[pay > 0])) if (pay > 0).any() else float("nan"))


def oracle(df, H1, H2, Q3, Q4):
    print("\n===== 天井（オラクル）: 看板枠の対象（現行 F_sign を売るレース）で、結果を見て本線/看板枠の良い方を選ぶ =====", flush=True)
    for label, mask in (("2025 通年", np.ones(len(df), bool)), ("上期", H1), ("下期", H2)):
        d = df[mask & (df.sign.to_numpy() == 1)]
        nd = d.date.nunique()
        pay_best = np.maximum(d.pay_h.to_numpy(), d.pay_s.to_numpy())
        inv_best = np.where(d.pay_h.to_numpy() > d.pay_s.to_numpy(), d.inv_h.to_numpy(), d.inv_s.to_numpy())
        o = metrics(inv_best, pay_best, None, None)
        s = metrics(d.inv_s, d.pay_s, None, None); h = metrics(d.inv_h, d.pay_h, None, None)
        print(f"[{label}] R={len(d)} 日数={nd} | ① 現行(F_sign): 回収率 {s['roi']:.1f}% 表示的中 {s['shown']:.2f}% 10万+ {s['big']}件({s['big']/nd:.3f}/日) 的中時払戻中央 {s['medhit']:,.0f}"
              f" | 全部 F_hit: {h['roi']:.1f}% {h['shown']:.2f}% {h['big']}件 {h['medhit']:,.0f}"
              f" | オラクル: 回収率 {o['roi']:.1f}% 表示的中 {o['shown']:.2f}% 10万+ {o['big']}件({o['big']/nd:.3f}/日) 的中時払戻中央 {o['medhit']:,.0f}", flush=True)


def arms(df, H2, p_lgb):
    """通過時のみ: 下期の sign 対象で ① 現行 / ② モデルで本線へ / ④ 無作為 20 seed。"""
    d = df[H2].copy(); d["p"] = p_lgb
    d = d[d.sign == 1].reset_index(drop=True)
    days = sorted(d.date.unique()); dpos = {x: j for j, x in enumerate(days)}; ND = len(days)
    di = np.array([dpos[x] for x in d.date])
    k = 200   # H17 ② の 下期 切替本数（T1 = 200）と同じ
    sel = np.zeros(len(d), bool); sel[np.argsort(-d.p.to_numpy())[:k]] = True
    print(f"\n===== 腕（下期・sign 対象 {len(d)}R・本線へ回す本数 {k}=H17 ② と同じ） =====", flush=True)
    S_inv, S_pay, H_inv, H_pay = d.inv_s.to_numpy(), d.pay_s.to_numpy(), d.inv_h.to_numpy(), d.pay_h.to_numpy()

    def dm(selv):
        inv = np.where(selv, H_inv, S_inv); pay = np.where(selv, H_pay, S_pay)
        M = np.zeros((ND, 4))
        for c, v in enumerate((inv, pay, (pay > inv).astype(float), (pay >= 1e5).astype(float))):
            np.add.at(M[:, c], di, v)
        return M, np.bincount(di, minlength=ND)
    rng = np.random.default_rng(20261019)
    W = np.stack([np.bincount(rng.integers(0, ND, ND), minlength=ND) for _ in range(N_BOOT)]).astype(float)

    def stat(M, n, Wm=None):
        if Wm is None:
            T = M.sum(0); N = n.sum()
            return dict(roi=T[1] / T[0] * 100, shown=T[2] / N * 100, big=T[3])
        T = Wm @ M; N = Wm @ n
        return dict(roi=T[:, 1] / T[:, 0] * 100, shown=T[:, 2] / N * 100, big=T[:, 3])
    M1, n = dm(np.zeros(len(d), bool)); M2, _ = dm(sel)
    ctr = []
    for sd in range(SEEDS):
        r_ = np.random.default_rng(2000 + sd); s = np.zeros(len(d), bool); s[r_.choice(len(d), k, replace=False)] = True
        ctr.append(dm(s)[0])
    a, a1 = stat(M2, n), stat(M1, n)
    cs = [stat(M, n) for M in ctr]
    c_sh = float(np.median([c["shown"] for c in cs])); c_roi = float(np.median([c["roi"] for c in cs])); c_big = float(np.median([c["big"] for c in cs]))
    b2, b1 = stat(M2, n, W), stat(M1, n, W)
    bc = [stat(M, n, W) for M in ctr]
    c_sh_b = np.median(np.stack([c["shown"] for c in bc]), 0); c_roi_b = np.median(np.stack([c["roi"] for c in bc]), 0)
    d_sh = b2["shown"] - c_sh_b
    print(f"② 回収率 {a['roi']:.2f}%（①{a1['roi']:.2f}% / 無作為中央 {c_roi:.2f}%） 表示的中 {a['shown']:.2f}%（①{a1['shown']:.2f}% / 無作為中央 {c_sh:.2f}%） 10万+ {a['big']:.0f}件（①{a1['big']:.0f} / 無作為中央 {c_big:.0f}）", flush=True)
    print(f"   表示的中差(②−無作為中央) {a['shown']-c_sh:+.2f}pt CI[{ci(d_sh)[0]:+.2f},{ci(d_sh)[1]:+.2f}]  ROI差(②−無作為中央) {a['roi']-c_roi:+.2f}pt  ΔROI(②−①) {a['roi']-a1['roi']:+.2f}pt CI[{ci(b2['roi']-b1['roi'])[0]:+.2f},{ci(b2['roi']-b1['roi'])[1]:+.2f}]", flush=True)
    c1 = ci(d_sh)[0] > 0 and (a['roi'] - c_roi) > 0; c2 = ci(b2['roi'] - b1['roi'])[0] > -5
    print(f"   判定: 見分ける意味 {'○' if c1 else '×'} 全体を悪くしない {'○' if c2 else '×'}", flush=True)


if __name__ == "__main__":
    main()
