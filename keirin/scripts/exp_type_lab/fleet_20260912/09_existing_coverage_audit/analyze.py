#!/usr/bin/env python3
"""09 既存商品カバレッジ監査 — 07_mark_upset の product.pkl / event_table.pkl を
読み取り専用で再利用し、追加の切り口だけ計算する。

読むだけ（他セッションのファイルを一切書き換えない）。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))

SRC = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
           "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/07_mark_upset")
HERE = Path(__file__).resolve().parent


def load():
    P = pd.DataFrame(pickle.load((SRC / "product.pkl").open("rb")))
    E = pd.DataFrame(pickle.load((SRC / "event_table.pkl").open("rb")))
    D = P.merge(E.drop(columns=["type", "rtype", "win", "fin", "key", "pw_ent"]),
                on=["i"], how="inner")
    D["y23"] = D.y & D.mk23
    D["sold"] = D.gate & D.axis_ok
    return D


MARK = {1: "◎", 2: "○", 3: "▲", 4: "△", 0: "無"}


def classify(r) -> str:
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


def sec1_exclusion(D: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("§1 印(prediction_mark)の除外率 — event_table.py の除外は既に0件と報告済み")
    print("=" * 90)
    # event_table.py 側で「◎○が各1車」を満たさないレースは event_table.pkl に
    # 行そのものが無い。board 全体(TYPE in ABCDEF)との差分で除外率を出す。
    z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
    tp = np.array([str(v) for v in z["TYPE"]])
    okpred = z["OKPRED"]
    trio_win = z["TRIO_WIN"]
    total_typed = int(np.sum(np.isin(tp, list("ABCDEF")) & okpred & (trio_win >= 0)
                             & np.isfinite(z["TRIO_PAY"])))
    have_marks = len(D[["i"]].drop_duplicates())
    print(f"  型が付き結果も確定しているレース: {total_typed:,}")
    print(f"  ◎○が各1車ちょうど揃っている(event_table.pkl に行がある): {have_marks:,}")
    print(f"  除外率: {(1 - have_marks / total_typed) * 100:.2f}%")


def sec2_agree_breakdown(D: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("§2 「④軸崩壊」の内訳 — モデル軸(a1,a2)と公式印(◎○)の不一致がどれだけ効いているか")
    print("=" * 90)
    for win in ("confirm", "explore"):
        S = D[(D.win == win) & D.sold]
        G = S[S.y23]
        print(f"\n-- {win}: 入稿母集団の y∧mk23 n={len(G):,} --")
        print(f"  全体の agree(モデル軸=公式印) 率: {G.agree.mean()*100:.1f}%"
              f"  (参考: 母集団全体では {S.agree.mean()*100:.1f}%)")
        for tag, m in (("agree=True (モデル軸=◎○)", G.agree),
                       ("agree=False (モデル軸≠◎○)", ~G.agree)):
            g = G[m]
            if len(g) == 0:
                continue
            cls = g.apply(classify, axis=1).value_counts(normalize=True)
            print(f"    [{tag}] n={len(g):,} ({len(g)/len(G)*100:.1f}%)  的中 {(g.pay>0).mean()*100:.2f}%"
                  f"  ④軸崩壊 {cls.get('④軸崩壊',0)*100:.1f}%  ②順序違い {cls.get('②順序違い',0)*100:.1f}%"
                  f"  ③相手外し {cls.get('③相手外し',0)*100:.1f}%")


def sec3_by_plan(D: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("§3 商品(プラン)別 — y∧mk23 の入稿母集団の中で、プランごとの的中率・払戻・10万+")
    print("=" * 90)
    for win in ("confirm", "explore"):
        S = D[(D.win == win) & D.sold]
        G = S[S.y23]
        print(f"\n-- {win}  y∧mk23 sold n={len(G):,} / 全sold n={len(S):,} "
              f"({len(G)/len(S)*100:.1f}%) --")
        print(f"  {'プラン':8s} {'件':>6s} {'占有%':>6s} {'的中%':>7s} {'表示的中%':>8s}"
              f" {'払戻/投資%':>9s} {'払戻中央(的中時)':>14s} {'10万+件':>7s} {'10万+率%':>8s}")
        for key, g in G.groupby("key"):
            hits = g[g.pay > 0]
            shown = g[g.pay >= g.inv]
            big = g[g.pay >= 100_000]
            med_pay = hits.pay.median() if len(hits) else float("nan")
            print(f"  {key:8s} {len(g):6d} {len(g)/len(G)*100:6.1f} {len(hits)/len(g)*100:7.2f}"
                  f" {len(shown)/len(g)*100:8.2f} {g.pay.sum()/g.inv.sum()*100:9.1f}"
                  f" {med_pay:14,.0f} {len(big):7d} {len(big)/len(g)*100:8.2f}")


def sec4_100k_origin(D: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("§4 10万+の的中は今どの商品が作っているか。そのうち y∧mk23 型がどれだけ含まれるか")
    print("=" * 90)
    for win in ("confirm", "explore"):
        S = D[(D.win == win) & D.sold]
        big = S[S.pay >= 100_000]
        print(f"\n-- {win}  10万+ 的中 n={len(big):,} / 全的中 n={(S.pay>0).sum():,} "
              f"/ 全sold n={len(S):,} --")
        print(f"  10万+のうち y∧mk23 型(1着◎○以外∧◎○のどちらかが2-3着): "
              f"{big.y23.mean()*100:.1f}%  (参考: both23限定なら {(big.y & big.both23).mean()*100:.1f}%)")
        print("  プラン別 10万+ 件数・その中で y23 が占める割合:")
        for key, g in big.groupby("key"):
            print(f"    {key:8s} n={len(g):4d}  y23占有率 {g.y23.mean()*100:5.1f}%"
                  f"  10万+率(そのプランのsold中) {len(g)/len(S[S.key==key])*100:5.2f}%")


def sec5_user_feeling(D: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("§5 ユーザーの体感「この選手が2,3着になった場合の万車券が買えないことが多い」の検証")
    print("=" * 90)
    for win in ("confirm", "explore"):
        S = D[D.win == win]
        base = S[S.sold]
        # 万車券 = 100倍+ の確定三連単オッズ（100円で1万円以上）
        Gall = S[S.y23]                     # 組めた全レース（入稿ゲート等は問わない）
        Gsold = base[base.y23]              # 実際に入稿している母集団
        Gmanshaken = Gall[Gall.tf_odds >= 100.0]
        Gm_sold = Gsold[Gsold.tf_odds >= 100.0]
        n_all_typed = len(S)
        print(f"\n-- {win} --")
        print(f"  y∧mk23(この選手が2,3着になる万車券の元)の頻度: {len(Gall)/n_all_typed*100:.2f}%"
              f"  (n={len(Gall):,}/{n_all_typed:,})")
        print(f"  そのうち確定オッズ100倍+(万車券)の割合: {len(Gmanshaken)/len(Gall)*100:.1f}%"
              f"  → 全レース比 {len(Gmanshaken)/n_all_typed*100:.2f}%")
        print(f"  入稿ゲート(平均想定払戻∧最低倍率)通過率: {len(Gsold)/len(Gall)*100:.1f}%"
              f"  軸信頼ゲートも含めた通過率: 同じ列で再計算 → "
              f"{base.y23.sum()/len(Gall)*100:.1f}%")
        n_hit = (Gm_sold.pay > 0).sum()
        print(f"  万車券(y∧mk23∧100倍+)のうち、入稿できていたのは {len(Gm_sold)}/{len(Gmanshaken)}"
              f" = {len(Gm_sold)/max(len(Gmanshaken),1)*100:.1f}%")
        print(f"  さらにそのうち実際に的中(買い目に入っていた)のは {n_hit}/{len(Gm_sold)}"
              f" = {n_hit/max(len(Gm_sold),1)*100:.1f}%"
              f"  （＝万車券化した y∧mk23 全体のうち回収できたのは"
              f" {n_hit/max(len(Gmanshaken),1)*100:.1f}%）")
        cls = Gm_sold.apply(classify, axis=1).value_counts(normalize=True) if len(Gm_sold) else {}
        print(f"  入稿できていたぶんの外れの内訳: " +
              "  ".join(f"{k}:{v*100:.1f}%" for k, v in cls.items()))
        not_sold = Gmanshaken[~Gmanshaken.i.isin(Gm_sold.i)]
        print(f"  入稿されなかった万車券(y∧mk23) n={len(not_sold)}"
              f"  ゲート落ち(mean/min_odds) {(~not_sold.gate).mean()*100:.1f}%"
              f"  軸ゲート落ち {(~not_sold.axis_ok).mean()*100:.1f}%")
        print(f"  取り逃した配当(確定三連単オッズ)の分布: 中央 {not_sold.tf_odds.median():.1f}倍"
              f"  p25 {not_sold.tf_odds.quantile(.25):.1f}  p75 {not_sold.tf_odds.quantile(.75):.1f}"
              f"  最大 {not_sold.tf_odds.max():.1f}")


if __name__ == "__main__":
    D = load()
    sec1_exclusion(D)
    sec2_agree_breakdown(D)
    sec3_by_plan(D)
    sec4_100k_origin(D)
    sec5_user_feeling(D)
