#!/usr/bin/env python3
"""「軸2車がともに3着以内（そろい）」をレース単位の目的変数にした台を作る（2026-09-11）。

台: /tmp/race_type_board.npz（7車 36,427R・vintage walk-forward の p3/pw）
   + data/feature_cache/wtfeat_20221201_20260831_f70_*.pkl（FEATURE_COLS_WT 70列・選手単位）

出力: <scratch>/01_axis_bust/table.pkl   1行 = 1レース
  y        : p3 上位2車（race_shape と同じ (-p3, 車番) 順）がともに3着以内
  既存6量  : axis_sum / pw_ent / arare / gap / pw_gap12 / rp_sd（old_rank_ideas と同じ定義）
  G1 確率  : p3/pw の降順ベクトル・軸2車の p3/pw・積・最小・p3 エントロピー
  G2 軸対  : 同ライン・隣接・先頭/番手・印・競走得点順位・脚質（軸2車の配置）
  G3 集約  : FEATURE_COLS_WT 70列 × {mean,std,max,min} + 軸1・軸2の値
  G4 メタ  : 開催日目・グレード・種別ダミー
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from src.preprocessing.feature_wt import FEATURE_COLS_WT  # noqa: E402
from src.type_lab import win_entropy  # noqa: E402

OUT = Path(__file__).resolve().parent / "table.pkl"
CACHE = REPO / "data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"


def main() -> None:
    t0 = time.time()
    z = C.board()
    A = {k: z[k] for k in ("KEY", "DATE", "P3", "PW", "TRIO_WIN", "WIN", "TYPE", "AXIS_SUM",
                           "ARARE", "GAP", "A_race_point", "A_line_size", "A_line_pos",
                           "A_is_line_leader", "A_prediction_mark", "A_n_lines", "LG", "ST",
                           "BEHIND", "DAYI", "RTYPE", "GRADE", "VENUE", "OKPRED", "TRIO_PAY",
                           "PAY", "AGREE")}
    idx = C.select(None, "all")
    idx = idx[np.array([str(A["TYPE"][i]) in "ABCDEF" for i in idx])]
    print(f"板の対象 {len(idx):,}R  ({time.time()-t0:.1f}s)", flush=True)

    keys = set(str(A["KEY"][i]) for i in idx)
    df = pd.read_pickle(CACHE)
    df = df[df["race_key"].isin(keys)].copy()
    df = df.sort_values(["race_key", "frame_no"])
    print(f"特徴キャッシュ {len(df):,}行 / {df['race_key'].nunique():,}R  ({time.time()-t0:.1f}s)",
          flush=True)
    feat_cols = [c for c in FEATURE_COLS_WT if c in df.columns]
    missing = [c for c in FEATURE_COLS_WT if c not in df.columns]
    print(f"FEATURE_COLS_WT {len(FEATURE_COLS_WT)} 列中 {len(feat_cols)} 列あり  欠け={missing}")
    # 選手単位 → レース単位: 7車分を車番順に並べた行列
    g = df.groupby("race_key")
    n_ok = g.size()
    keys7 = set(n_ok[n_ok == 7].index)
    M = {}
    for rk, sub in g:
        if rk not in keys7:
            continue
        if list(sub["frame_no"]) != [1, 2, 3, 4, 5, 6, 7]:
            continue
        M[rk] = sub[feat_cols].to_numpy(dtype=float)
    print(f"7車揃い {len(M):,}R  ({time.time()-t0:.1f}s)", flush=True)

    rows = []
    for n, i in enumerate(idx):
        rk = str(A["KEY"][i])
        if rk not in M:
            continue
        p3 = A["P3"][i].astype(float)
        pw = A["PW"][i].astype(float)
        cars = list(range(1, 8))
        order = sorted(cars, key=lambda c: (-p3[c - 1], c))
        a1, a2 = order[0], order[1]
        win3 = set(C.CANON3[int(A["TRIO_WIN"][i])])
        y = (a1 in win3) and (a2 in win3)
        pws = sorted(pw, reverse=True)
        p3s = sorted(p3, reverse=True)
        rp = A["A_race_point"][i].astype(float)
        lg = [str(v) for v in A["LG"][i]]
        lp = A["A_line_pos"][i].astype(float)
        lead = A["A_is_line_leader"][i].astype(float)
        ls = A["A_line_size"][i].astype(float)
        mark = A["A_prediction_mark"][i].astype(float)
        st = [str(v) for v in A["ST"][i]]
        beh = A["BEHIND"][i].astype(float)
        same_line = (lg[a1 - 1] == lg[a2 - 1]) and lg[a1 - 1] not in ("", "0", "None")
        rp_rank = {c: r for r, c in enumerate(sorted(cars, key=lambda c: -rp[c - 1]))}
        p3n = p3 / max(p3.sum(), 1e-9)
        d = dict(
            i=int(i), key=rk, date=str(A["DATE"][i]), type=str(A["TYPE"][i]),
            dayi=int(A["DAYI"][i]), rtype=str(A["RTYPE"][i]), grade=str(A["GRADE"][i]),
            venue=str(A["VENUE"][i]), agree=bool(A["AGREE"][i]),
            a1=a1, a2=a2, y=bool(y),
            a1_in3=a1 in win3, a2_in3=a2 in win3,
            trio_pay=float(A["TRIO_PAY"][i]), tf_pay=float(A["PAY"][i]),
            # 既存6量
            axis_sum=float(A["AXIS_SUM"][i]), pw_ent=float(win_entropy({c: pw[c - 1] for c in cars})),
            arare=int(A["ARARE"][i]), gap=float(A["GAP"][i]),
            pw_gap12=float(pws[0] - pws[1]), rp_sd=float(rp.std()),
            # G1 確率
            p3_a1=float(p3[a1 - 1]), p3_a2=float(p3[a2 - 1]), pw_a1=float(pw[a1 - 1]),
            pw_a2=float(pw[a2 - 1]), p3_prod=float(p3[a1 - 1] * p3[a2 - 1]),
            p3_min2=float(min(p3[a1 - 1], p3[a2 - 1])), p3_gap12=float(p3s[0] - p3s[1]),
            p3_gap23=float(p3s[1] - p3s[2]), p3_sum3=float(sum(p3s[:3])),
            p3_ent=float(-(p3n * np.log(np.clip(p3n, 1e-12, None))).sum()),
            p3_total=float(p3.sum()), pw_sum2=float(pws[0] + pws[1]),
            **{f"p3s{k}": float(p3s[k]) for k in range(7)},
            **{f"pws{k}": float(pws[k]) for k in range(7)},
            # G2 軸対の配置
            same_line=int(same_line),
            adjacent=int(same_line and abs(lp[a1 - 1] - lp[a2 - 1]) == 1),
            a1_leads_a2=int(same_line and lp[a1 - 1] + 1 == lp[a2 - 1]),
            a2_leads_a1=int(same_line and lp[a2 - 1] + 1 == lp[a1 - 1]),
            a1_leader=float(lead[a1 - 1]), a2_leader=float(lead[a2 - 1]),
            a1_lsize=float(ls[a1 - 1]), a2_lsize=float(ls[a2 - 1]),
            a1_lpos=float(lp[a1 - 1]), a2_lpos=float(lp[a2 - 1]),
            a1_mark=float(mark[a1 - 1]), a2_mark=float(mark[a2 - 1]),
            a1_rp=float(rp[a1 - 1]), a2_rp=float(rp[a2 - 1]),
            a1_rprank=rp_rank[a1], a2_rprank=rp_rank[a2],
            a1_beh=float(beh[a1 - 1]), a2_beh=float(beh[a2 - 1]),
            a1_st_oi=int(st[a1 - 1] == "追"), a2_st_oi=int(st[a2 - 1] == "追"),
            a1_st_nige=int(st[a1 - 1] == "逃"), a2_st_nige=int(st[a2 - 1] == "逃"),
            n_lines=float(A["A_n_lines"][i][0]),
            n_solo=int(sum(1 for c in cars if lg[c - 1] in ("", "0", "None") or ls[c - 1] <= 1)),
            max_lsize=float(ls.max()),
            rp_top_gap=float(sorted(rp, reverse=True)[0] - sorted(rp, reverse=True)[1]),
        )
        X = M[rk]
        for j, c in enumerate(feat_cols):
            col = X[:, j]
            d[f"m_{c}"] = float(col.mean())
            d[f"s_{c}"] = float(col.std())
            d[f"x_{c}"] = float(col.max())
            d[f"n_{c}"] = float(col.min())
            d[f"a1_{c}"] = float(col[a1 - 1])
            d[f"a2_{c}"] = float(col[a2 - 1])
        rows.append(d)
        if n % 5000 == 0:
            print(f"  {n:,}/{len(idx):,}  ({time.time()-t0:.1f}s)", flush=True)
    T = pd.DataFrame(rows)
    T["win"] = np.where(T["date"] <= "2025-12-31", "explore", "confirm")
    print(f"行 {len(T):,}  列 {T.shape[1]}  そろい率 {T['y'].mean()*100:.2f}%  "
          f"(探索 {T[T.win=='explore']['y'].mean()*100:.2f} / 確認 {T[T.win=='confirm']['y'].mean()*100:.2f})")
    pickle.dump(dict(T=T, feat_cols=feat_cols), OUT.open("wb"))
    print("→", OUT, f"({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
