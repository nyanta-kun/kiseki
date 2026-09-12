#!/usr/bin/env python3
"""A. 「1着が WT◎○ 以外」の頻度・配当・モデル側の見え方（記述）。

台: /tmp/race_type_board.npz（7車・型A〜F・vintage p3/pw）。印は板の
`A_prediction_mark`（0=なし 1=◎ 2=○ 3=▲ 4=△。DB `keirin.wt_entries.prediction_mark`
と4レースで突き合わせて一致を確認済み）。
出力: <scratch>/07_mark_upset/event_table.pkl（1行=1レース）と標準出力の表。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from src.type_lab import win_entropy  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "event_table.pkl"


def build() -> pd.DataFrame:
    z = C.board()
    A = {k: z[k] for k in ("KEY", "DATE", "P3", "PW", "WIN", "PAY", "TRIO_PAY", "TYPE",
                           "AXIS_SUM", "ARARE", "GAP", "A_race_point", "A_line_size",
                           "A_line_pos", "A_prediction_mark", "LG", "DAYI", "RTYPE",
                           "GRADE", "VENUE", "AGREE", "A_n_lines")}
    idx = C.select(None, "all")
    idx = idx[np.array([str(A["TYPE"][i]) in "ABCDEF" for i in idx])]
    rows = []
    n_nomark = 0
    for i in idx:
        mark = A["A_prediction_mark"][i].astype(int)
        hon = [c + 1 for c in range(7) if mark[c] == 1]
        tai = [c + 1 for c in range(7) if mark[c] == 2]
        if len(hon) != 1 or len(tai) != 1:
            n_nomark += 1
            continue
        hon, tai = hon[0], tai[0]
        p3 = A["P3"][i].astype(float)
        pw = A["PW"][i].astype(float)
        pwn = pw / max(pw.sum(), 1e-9)
        rp = A["A_race_point"][i].astype(float)
        lg = [str(v) for v in A["LG"][i]]
        lp = A["A_line_pos"][i].astype(float)
        ls = A["A_line_size"][i].astype(float)
        cars = list(range(1, 8))
        pw_rank = {c: r + 1 for r, c in enumerate(sorted(cars, key=lambda c: (-pw[c - 1], c)))}
        p3_rank = {c: r + 1 for r, c in enumerate(sorted(cars, key=lambda c: (-p3[c - 1], c)))}
        rp_rank = {c: r + 1 for r, c in enumerate(sorted(cars, key=lambda c: (-rp[c - 1], c)))}
        fin = C.CANON[int(A["WIN"][i])]
        w = fin[0]
        y = w not in (hon, tai)
        mk23 = (hon in fin[1:]) or (tai in fin[1:])
        both23 = (hon in fin[1:]) and (tai in fin[1:])
        a1, a2 = sorted(cars, key=lambda c: (-p3[c - 1], c))[:2]
        rows.append(dict(
            i=int(i), key=str(A["KEY"][i]), date=str(A["DATE"][i]), type=str(A["TYPE"][i]),
            rtype=str(A["RTYPE"][i]), grade=str(A["GRADE"][i]), venue=str(A["VENUE"][i]),
            dayi=int(A["DAYI"][i]), agree=bool(A["AGREE"][i]),
            hon=hon, tai=tai, fin=fin, w=w, y=bool(y), mk23=bool(mk23), both23=bool(both23),
            hon_in3=hon in fin, tai_in3=tai in fin, hon_1st=w == hon, tai_1st=w == tai,
            tf_odds=float(A["PAY"][i]) / 100.0, trio_odds=float(A["TRIO_PAY"][i]),
            axis_sum=float(A["AXIS_SUM"][i]), arare=int(A["ARARE"][i]), gap=float(A["GAP"][i]),
            pw_ent=float(win_entropy({c: pw[c - 1] for c in cars})),
            pw_gap12=float(np.sort(pw)[-1] - np.sort(pw)[-2]), rp_sd=float(rp.std()),
            pw_hon=float(pwn[hon - 1]), pw_tai=float(pwn[tai - 1]),
            pw_hon_raw=float(pw[hon - 1]), pw_tai_raw=float(pw[tai - 1]),
            p3_hon=float(p3[hon - 1]), p3_tai=float(p3[tai - 1]),
            pw_w=float(pwn[w - 1]), p3_w=float(p3[w - 1]),
            pw_rank_hon=pw_rank[hon], pw_rank_tai=pw_rank[tai],
            p3_rank_hon=p3_rank[hon], p3_rank_tai=p3_rank[tai],
            pw_rank_w=pw_rank[w], p3_rank_w=p3_rank[w], rp_rank_w=rp_rank[w],
            mark_w=int(mark[w - 1]), lpos_w=float(lp[w - 1]), lsize_w=float(ls[w - 1]),
            w_same_line_hon=(lg[w - 1] == lg[hon - 1]) and lg[w - 1] not in ("", "0", "None"),
            w_same_line_tai=(lg[w - 1] == lg[tai - 1]) and lg[w - 1] not in ("", "0", "None"),
            hon_is_a1=hon == a1, honta_is_axis=({hon, tai} == {a1, a2}),
            pw_max_other=float(max(pwn[c - 1] for c in cars if c not in (hon, tai))),
            p_y_model=float(1.0 - pwn[hon - 1] - pwn[tai - 1]),
            n_lines=float(A["A_n_lines"][i][0]),
        ))
    T = pd.DataFrame(rows)
    T["win"] = np.where(T["date"] <= "2025-12-31", "explore", "confirm")
    T.attrs["n_nomark"] = n_nomark
    T.attrs["n_all"] = int(len(idx))
    return T


def pct(x) -> str:
    return f"{100*x:.2f}%"


def dist(o: pd.Series) -> str:
    if len(o) == 0:
        return "(n=0)"
    q = o.quantile([.5, .75, .9])
    return (f"n={len(o):,} 中央 {q[.5]:.1f}倍 p75 {q[.75]:.1f} p90 {q[.9]:.1f} "
            f"100倍+ {pct((o>=100).mean())} 300倍+ {pct((o>=300).mean())} 万車券(100倍+) 同左")


def main() -> None:
    if OUT.exists():
        T = pickle.load(OUT.open("rb"))
    else:
        T = build()
        pickle.dump(T, OUT.open("wb"))
    print(f"対象 {T.attrs.get('n_all', len(T)):,}R のうち 印(◎○が各1車)取得 {len(T):,}R "
          f"除外 {T.attrs.get('n_nomark', 0):,}R ({T.attrs.get('n_nomark',0)/max(T.attrs.get('n_all',1),1)*100:.2f}%)")
    print(f"  ◎○ = モデル軸2車(p3上位2車) と一致: {pct(T.honta_is_axis.mean())}  "
          f"◎ = p3 1位: {pct(T.hon_is_a1.mean())}  ◎ = pw 1位: {pct((T.pw_rank_hon==1).mean())}")
    for win in ("explore", "confirm"):
        S = T[T.win == win]
        print(f"\n===== {win}  n={len(S):,} =====")
        print(f"[A1] 1着が◎○以外 (y): {pct(S.y.mean())}   ◎が1着 {pct(S.hon_1st.mean())}  ○が1着 {pct(S.tai_1st.mean())}")
        print("  型別:", "  ".join(f"{t}:{pct(S[S.type==t].y.mean())}(n={(S.type==t).sum()})" for t in "ABCDEF"))
        qs = T[T.win == "explore"]["axis_sum"].quantile([.2, .4, .6, .8]).values
        S = S.assign(aq=np.digitize(S.axis_sum, qs) + 1)
        print("  axis_sum五分位(探索窓の分位):", "  ".join(f"Q{q}:{pct(S[S.aq==q].y.mean())}" for q in range(1, 6)))
        print(f"[A2] 三連単オッズ  全体: {dist(S.tf_odds)}")
        print(f"     y=1 (1着◎○以外): {dist(S[S.y].tf_odds)}")
        print(f"     y=0 (1着◎○): {dist(S[~S.y].tf_odds)}")
        print("[A3] 2×2 (行=1着が◎○以外 / 列=◎or○が2-3着)")
        ct = pd.crosstab(S.y, S.mk23, normalize=False)
        print(ct.to_string())
        print((pd.crosstab(S.y, S.mk23, normalize=True) * 100).round(2).to_string())
        sub = S[S.y & S.mk23]
        print(f"     本命母集団 y∧mk23: {pct(len(sub)/len(S))}  そのうち◎○両方2-3着 {pct(sub.both23.mean())} "
              f"◎が2-3着 {pct(sub.hon_in3.mean())}  ○が2-3着 {pct(sub.tai_in3.mean())}")
        print(f"     配当 y∧mk23: {dist(sub.tf_odds)}")
        print(f"     配当 y∧¬mk23(◎○とも3着外): {dist(S[S.y & ~S.mk23].tf_odds)}")
        print(f"     配当 y∧両方2-3着: {dist(sub[sub.both23].tf_odds)}   y∧片方だけ: {dist(sub[~sub.both23].tf_odds)}")
        print("     型別 y∧mk23 割合:", "  ".join(f"{t}:{pct((S[S.type==t].y & S[S.type==t].mk23).mean())}" for t in "ABCDEF"))
        print("[A4] y=1 のときの1着車のモデル側順位")
        Y = S[S.y]
        for col, name in (("pw_rank_w", "pw順位"), ("p3_rank_w", "p3順位"), ("rp_rank_w", "競走得点順位")):
            vc = Y[col].value_counts(normalize=True).sort_index()
            print(f"     {name}: " + "  ".join(f"{k}:{100*v:.1f}%" for k, v in vc.items()))
        vc = Y.mark_w.value_counts(normalize=True).sort_index()
        print("     1着車の印: " + "  ".join(f"{ {0:'無',3:'▲',4:'△'}.get(k,k)}:{100*v:.1f}%" for k, v in vc.items()))
        vc = Y.lpos_w.value_counts(normalize=True).sort_index()
        print("     1着車のライン位置: " + "  ".join(f"{int(k) if np.isfinite(k) else 'nan'}:{100*v:.1f}%" for k, v in vc.items()))
        print(f"     1着車が◎と同ライン {pct(Y.w_same_line_hon.mean())}  ○と同ライン {pct(Y.w_same_line_tai.mean())}")
        print(f"     1着車が pw1位 (◎○以外なのにモデル1位): {pct((Y.pw_rank_w==1).mean())}  pw上位2位以内 {pct((Y.pw_rank_w<=2).mean())}  pw上位3位以内 {pct((Y.pw_rank_w<=3).mean())}")
        print(f"     ◎の pw 順位 (y=1): " + "  ".join(f"{k}:{100*v:.1f}%" for k, v in Y.pw_rank_hon.value_counts(normalize=True).sort_index().items()))
        print(f"     ◎の pw 順位 (y=0): " + "  ".join(f"{k}:{100*v:.1f}%" for k, v in S[~S.y].pw_rank_hon.value_counts(normalize=True).sort_index().items()))
        print("[A4b] pw(◎) と pw(1着車) の分布 (正規化 pw)")
        for nm, g in (("y=0", S[~S.y]), ("y=1", Y), ("y∧mk23", sub)):
            print(f"     {nm}: pw◎ 中央 {g.pw_hon.median():.3f} p25 {g.pw_hon.quantile(.25):.3f} p75 {g.pw_hon.quantile(.75):.3f} | "
                  f"pw○ 中央 {g.pw_tai.median():.3f} | pw(1着) 中央 {g.pw_w.median():.3f} p25 {g.pw_w.quantile(.25):.3f} p75 {g.pw_w.quantile(.75):.3f} | "
                  f"pw_max_other 中央 {g.pw_max_other.median():.3f} | 1-pw◎-pw○ 中央 {g.p_y_model.median():.3f}")
        # モデルの較正: p_y_model 十分位 vs 実測 y
        S2 = S.assign(dq=pd.qcut(S.p_y_model, 10, labels=False, duplicates="drop"))
        g = S2.groupby("dq").agg(pred=("p_y_model", "mean"), obs=("y", "mean"), n=("y", "size"))
        print("[A4c] 較正: 1-pw◎-pw○ 十分位 → 実測 y")
        print("     " + "  ".join(f"D{int(k)+1}:{r.pred:.3f}/{r.obs:.3f}" for k, r in g.iterrows()))


if __name__ == "__main__":
    main()
