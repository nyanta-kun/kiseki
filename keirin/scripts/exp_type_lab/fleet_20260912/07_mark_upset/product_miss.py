#!/usr/bin/env python3
"""C. 現行商品が「1着◎○以外 ∧ ◎○が2-3着」で何を取り逃しているか。

入力: product.pkl（現行1商品・買い目つき）＋ event_table.pkl。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
P = pd.DataFrame(pickle.load((HERE / "product.pkl").open("rb")))
E = pickle.load((HERE / "event_table.pkl").open("rb"))
D = P.merge(E[["i", "hon", "tai", "y", "mk23", "both23", "tf_odds", "hon_in3", "tai_in3",
               "pw_rank_w", "p3_rank_w", "mark_w", "pw_hon", "pw_w", "honta_is_axis"]], on="i")
D["y23"] = D.y & D.mk23
MARK = {1: "◎", 2: "○", 3: "▲", 4: "△", 0: "無"}


def classify(r) -> str:
    """外れの排他分解（買い目に対して）。"""
    if r.pay > 0:
        return "①的中"
    fin = r.fin
    legs = list(r.stakes)
    if r.trio:
        sets = {frozenset(c) for c in legs}
        if frozenset(fin) in sets:
            return "①的中"
        if r.a1 in fin and r.a2 in fin:
            return "③相手外し"
        return "④軸崩壊"
    sets = {frozenset(c) for c in legs}
    if frozenset(fin) in sets:
        return "②順序違い"
    if r.a1 in fin and r.a2 in fin:
        return "③相手外し"
    return "④軸崩壊"


def first_col(r) -> str:
    """買い目の1着列に来ている車（賭け金加重）。"""
    if r.trio:
        return "三連複"
    w = {}
    for c, s in r.stakes.items():
        w[c[0]] = w.get(c[0], 0) + s
    tot = sum(w.values())
    return " ".join(f"{MARK.get(int(m), '?')}" for m in [])  # placeholder


def main() -> None:
    for win in ("confirm", "explore"):
        S = D[D.win == win]
        sold = S[S.gate & S.axis_ok]
        print(f"\n===== {win}: 組めた {len(S):,}R / 入稿母集団(ゲート∧軸ゲート) {len(sold):,}R =====")
        for nm, G in (("全レース", sold), ("y∧mk23(1着◎○以外∧◎○2-3着)", sold[sold.y23]),
                      ("y∧¬mk23(◎○とも3着外)", sold[sold.y & ~sold.mk23]), ("¬y(1着が◎○)", sold[~sold.y])):
            if len(G) == 0:
                continue
            cls = G.apply(classify, axis=1).value_counts(normalize=True).sort_index()
            shown = ((G.pay >= G.inv)).mean()
            print(f"  [{nm}] n={len(G):,} ({len(G)/len(sold)*100:.1f}%)  的中 {(G.pay>0).mean()*100:.2f}%  表示的中 {shown*100:.2f}%  "
                  f"払戻/投資 {G.pay.sum()/G.inv.sum()*100:.1f}%  | " + "  ".join(f"{k}:{v*100:.1f}%" for k, v in cls.items()))
        # 入稿母集団に入る割合（母集団別）
        for nm, m in (("y∧mk23", S.y23), ("¬y", ~S.y)):
            g = S[m]
            print(f"  入稿率 {nm}: ゲート通過 {g.gate.mean()*100:.1f}%  軸ゲート通過 {g.axis_ok.mean()*100:.1f}%  両方 {(g.gate&g.axis_ok).mean()*100:.1f}%")
        G = sold[sold.y23]
        print(f"  [y∧mk23 の内訳] プラン: " + "  ".join(f"{k}:{v*100:.1f}%" for k, v in G.key.value_counts(normalize=True).items()))
        tf = G[~G.trio]
        # 1着列に◎○以外を置いている買い目の割合（賭け金加重）と、決着の1着車が1着列にあるか
        w_hon = w_tai = w_oth = 0.0; n_w1 = 0; n_w1_any = 0
        for r in tf.itertuples():
            firsts = {}
            for c, s in r.stakes.items():
                firsts[c[0]] = firsts.get(c[0], 0) + s
            tot = sum(firsts.values())
            w_hon += firsts.get(r.hon, 0) / tot; w_tai += firsts.get(r.tai, 0) / tot
            w_oth += sum(v for k, v in firsts.items() if k not in (r.hon, r.tai)) / tot
            n_w1 += r.fin[0] in firsts
            n_w1_any += any(r.fin[0] in c for c in r.stakes)
        n = max(len(tf), 1)
        print(f"  [買い目の1着列・三連単 n={len(tf)}] 賭け金の配分 ◎ {w_hon/n*100:.1f}% ○ {w_tai/n*100:.1f}% その他 {w_oth/n*100:.1f}%  |  "
              f"決着の1着車を1着列に持っていた {n_w1/n*100:.1f}%  買い目のどこかに持っていた {n_w1_any/n*100:.1f}%")
        # 決着の目の予測オッズ・帯・Σ
        po = G.win_po
        print(f"  [決着の目の予測三連単オッズ] 中央 {po.median():.1f} p25 {po.quantile(.25):.1f} p75 {po.quantile(.75):.1f}  "
              f"NaN {po.isna().mean()*100:.1f}%  確定 中央 {G.tf_odds.median():.1f}")
        band_ok = (tf.win_po >= tf.min_odds)
        print(f"  帯の内側(予測>=min_odds) {band_ok.mean()*100:.1f}%  確率順位(λμ) 中央 {tf.rank_prob.median():.0f} "
              f"<=k {((tf.rank_prob < tf.k)).mean()*100:.1f}%  <=2k {((tf.rank_prob < 2*tf.k)).mean()*100:.1f}%")
        sig_after = tf.sigma + 1.0 / tf.win_po
        print(f"  Σ(1/予測) 現行 中央 {tf.sigma.median():.3f} → 決着の目を1点足すと {sig_after.median():.3f}  "
              f"足してもΣ<0.5(ダッチで平均2万を保つ) {(sig_after < 0.5).mean()*100:.1f}%  (現行でΣ<0.5 {(tf.sigma<0.5).mean()*100:.1f}%)")
        # 全 y∧mk23（組めたが売っていないものも含む）での配当帯
        print(f"  [参考] y∧mk23 全体 n={S.y23.sum():,} 確定オッズ中央 {S[S.y23].tf_odds.median():.1f}  入稿母集団の中 {G.tf_odds.median():.1f}")

    # ── 1件目視 ──
    S = D[(D.win == "confirm") & D.gate & D.axis_ok & D.y23 & (D.pay == 0) & (~D.trio)]
    med = S.tf_odds.median()
    r = S.iloc[(S.tf_odds - med).abs().argsort().iloc[0]]
    from scripts.exp_type_lab import common as C  # noqa
    z = C.board(); i = int(r.i)
    P3, PW, MK, RP, LG, LP = z["P3"][i], z["PW"][i], z["A_prediction_mark"][i].astype(int), z["A_race_point"][i], z["LG"][i], z["A_line_pos"][i]
    print(f"\n===== 目視1件: {z['KEY'][i]} {z['DATE'][i]} {z['RTYPE'][i]} {z['GRADE'][i]} 型{r.type} プラン {r.key} 点数 {r.k} 投資 {r.inv:,.0f} =====")
    print(f"  決着 {r.fin[0]}-{r.fin[1]}-{r.fin[2]}  確定三連単 {r.tf_odds:.1f}倍  予測三連単 {r.win_po:.1f}倍  確率順位(λμ) {r.rank_prob+1}位  "
          f"◎={r.hon} ○={r.tai}  1着車の印 {MARK[int(r.mark_w)]} pw順位 {r.pw_rank_w} p3順位 {r.p3_rank_w}")
    print("  車  印  ライン(位置)  得点   p3     pw")
    for c in range(1, 8):
        print(f"  {c}   {MARK[MK[c-1]]:2s}  {LG[c-1]}({int(LP[c-1]) if np.isfinite(LP[c-1]) else '-'})        {RP[c-1]:5.2f}  {P3[c-1]:.3f}  {PW[c-1]:.3f}")
    print("  買い目(1-2-3)   賭け金   予測オッズ")
    for c, s in sorted(r.stakes.items(), key=lambda kv: -kv[1]):
        print(f"  {c[0]}-{c[1]}-{c[2]}          {s:6,d}   {r.po_legs[c]:7.1f}")
    print(f"  Σ(1/予測)={r.sigma:.3f} 平均想定払戻 {r['mean']:,.0f}円 → 決着の目を足すと Σ={r.sigma + 1/r.win_po:.3f}")
    print(f"  分解: {classify(r)}")


if __name__ == "__main__":
    main()


def extra() -> None:
    print("\n\n######## 追加: y∧mk23 の内訳（both23 / ◎だけ / ○だけ）と p_y_model 五分位 × 現行商品 ########")
    E2 = pickle.load((HERE / "event_table.pkl").open("rb"))[["i", "p_y_model", "fin"]].rename(columns={"fin": "fin_e"})
    D2 = D.merge(E2, on="i")
    qs = D2[D2.win == "explore"].p_y_model.quantile([.2, .4, .6, .8]).values
    D2["pq"] = np.digitize(D2.p_y_model, qs) + 1
    for win in ("confirm", "explore"):
        sold = D2[(D2.win == win) & D2.gate & D2.axis_ok]
        G = sold[sold.y23]
        print(f"\n===== {win} 入稿母集団 y∧mk23 n={len(G):,} =====")
        for nm, m in (("◎○とも2-3着", G.both23), ("◎だけ2-3着(○は4着以下)", G.hon_in3 & ~G.tai_in3), ("○だけ2-3着(◎は4着以下)", ~G.hon_in3 & G.tai_in3)):
            g = G[m]
            cls = g.apply(classify, axis=1).value_counts(normalize=True).sort_index()
            print(f"  [{nm}] n={len(g):,} ({len(g)/len(G)*100:.1f}%) 的中 {(g.pay>0).mean()*100:.2f}% 確定中央 {g.tf_odds.median():.1f} 予測中央 {g.win_po.median():.1f} | "
                  + "  ".join(f"{k}:{v*100:.1f}%" for k, v in cls.items()))
        print("  p_y_model(1-pw◎-pw○) 五分位(探索窓の分位) × 現行商品（入稿母集団）:")
        print("    Q   n    件%   y%   y∧mk23%  表示的中%  払戻/投資%  ④軸崩壊%  ②順序違い%  10万+/件%")
        for q in range(1, 6):
            g = sold[sold.pq == q]
            cls = g.apply(classify, axis=1).value_counts(normalize=True)
            print(f"    {q}  {len(g):5,} {len(g)/len(sold)*100:5.1f} {g.y.mean()*100:5.1f} {g.y23.mean()*100:7.1f}  "
                  f"{(g.pay>=g.inv).mean()*100:8.2f}  {g.pay.sum()/g.inv.sum()*100:8.1f}  {cls.get('④軸崩壊',0)*100:7.1f}  {cls.get('②順序違い',0)*100:8.1f}  {(g.pay>=100000).mean()*100:7.2f}")
    # ② 順序違いの目視1件（y∧mk23・集合は買えていた）
    S = D2[(D2.win == "confirm") & D2.gate & D2.axis_ok & D2.y23 & (D2.pay == 0) & (~D2.trio)]
    S = S[S.apply(classify, axis=1) == "②順序違い"]
    med = S.tf_odds.median()
    r = S.iloc[(S.tf_odds - med).abs().argsort().iloc[0]]
    from scripts.exp_type_lab import common as C  # noqa
    z = C.board(); i = int(r.i)
    P3, PW, MK, RP, LG, LP = z["P3"][i], z["PW"][i], z["A_prediction_mark"][i].astype(int), z["A_race_point"][i], z["LG"][i], z["A_line_pos"][i]
    print(f"\n===== 目視2件目(②順序違い): {z['KEY'][i]} {z['DATE'][i]} {z['RTYPE'][i]} {z['GRADE'][i]} 型{r.type} プラン {r.key} 点数 {r.k} 投資 {r.inv:,.0f} =====")
    print(f"  決着 {r.fin[0]}-{r.fin[1]}-{r.fin[2]}  確定三連単 {r.tf_odds:.1f}倍  予測三連単 {r.win_po:.1f}倍  確率順位(λμ) {r.rank_prob+1}位  "
          f"◎={r.hon} ○={r.tai}  1着車の印 {MARK[int(r.mark_w)]} pw順位 {r.pw_rank_w} p3順位 {r.p3_rank_w}")
    print("  車  印  ライン(位置)  得点   p3     pw")
    for c in range(1, 8):
        print(f"  {c}   {MARK[MK[c-1]]:2s}  {LG[c-1]}({int(LP[c-1]) if np.isfinite(LP[c-1]) else '-'})        {RP[c-1]:6.2f}  {P3[c-1]:.3f}  {PW[c-1]:.3f}")
    print("  買い目(1-2-3)   賭け金   予測オッズ")
    for c, s in sorted(r.stakes.items(), key=lambda kv: -kv[1]):
        print(f"  {c[0]}-{c[1]}-{c[2]}          {s:6,d}   {r.po_legs[c]:7.1f}")
    print(f"  Σ(1/予測)={r.sigma:.3f} 平均想定払戻 {r['mean']:,.0f}円 → 決着の目を足すと Σ={r.sigma + 1/r.win_po:.3f}")


if __name__ == "__main__":
    extra()
