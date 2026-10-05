#!/usr/bin/env python3
"""H06 準備: 母集団・mdl_ent・p80・vintage 予測オッズの整列・対象レースの最終三連単オッズ抽出。

読み取りのみ（KEIRIN_DB_URL・readonly）。出力は data/exp_bet_review/h06_*.pkl（git 管理外）。
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h06_prep.py
"""
from __future__ import annotations
import itertools, math, pickle, sys
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts")); sys.path.insert(0, str(REPO / "scripts" / "exp_bet_review"))
D = REPO / "data" / "exp_bet_review"
CANON = list(itertools.permutations(range(1, 8), 3))
CS = ["-".join(map(str, c)) for c in CANON]
CMAP = {s: i for i, s in enumerate(CS)}


def ent(pw):
    v = np.asarray(pw, float); v = v[v > 0]; t = v.sum()
    if t <= 0:
        return 0.0
    p = v / t
    return float(-(p * np.log(p)).sum())


def main():
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    KEY, DATE, PW = z["KEY"], z["DATE"], z["PW"]
    WIN, PAY = z["WIN"], z["PAY"]
    m25 = (DATE >= "2025-01-01") & (DATE <= "2025-12-31")
    print("board 2025 7車:", m25.sum())
    # vintage 予測オッズ
    v = np.load(D / "odds_tf_vintage2024_2025.npz")
    n7 = v["n_car"] == 7
    rk, cb, po = v["race_key"][n7], v["combination"][n7], v["pred_odds"][n7]
    ur, inv = np.unique(rk, return_inverse=True)
    ci = np.array([CMAP[s] for s in cb]) if False else None
    uc, cinv = np.unique(cb, return_inverse=True)
    cmap_u = np.array([CMAP[s] for s in uc])
    V = np.full((len(ur), 210), np.nan)
    V[inv, cmap_u[cinv]] = po
    full = np.isfinite(V).sum(1) == 210
    print("vintage 7車レース:", len(ur), "210点そろい:", full.sum())
    vpos = {k: i for i, k in enumerate(ur)}
    # board の 2025 行へ整列
    idx25 = np.flatnonzero(m25)
    # 母集団: 勝ち目が判明（WIN>=0・PAY有限）かつ PW 有限
    pwok = np.isfinite(PW).all(1) & (PW.sum(1) > 0)
    pop_all = [int(i) for i in idx25 if WIN[i] >= 0 and np.isfinite(PAY[i]) and pwok[i]]
    in_v = [i for i in pop_all if KEY[i] in vpos and full[vpos[KEY[i]]]]
    print("母集団(board 2025・WIN有)", len(pop_all), "うち vintage あり", len(in_v), "除外", len(pop_all) - len(in_v))
    E = {i: ent(PW[i]) for i in in_v}
    arr = np.array([E[i] for i in in_v])
    p80 = float(np.percentile(arr, 80))
    # np.percentile(linear) と quantile は同じ。H2 は pandas quantile(0.8)（linear）
    print("p80 mdl_ent (母集団=vintage あり):", p80)
    arr_all = np.array([ent(PW[i]) for i in pop_all])
    print("p80 (board 2025 母集団全体):", float(np.percentile(arr_all, 80)))
    # 参考: wf_preds の 2025 全7車
    import train_odds_prediction_tf as T
    T.N_CAR = 7
    tgt = [i for i in in_v if E[i] >= p80]
    print("対象レース:", len(tgt), "開催日", len({DATE[i] for i in tgt}))
    out = dict(pop=in_v, pop_all=pop_all, ent={i: E[i] for i in in_v}, p80=p80,
               p80_boardall=float(np.percentile(arr_all, 80)), tgt=tgt,
               V={int(i): V[vpos[KEY[i]]].copy() for i in in_v})
    pickle.dump(out, open(D / "h06_pop.pkl", "wb"))
    # 3車 wf_preds 全7車での p80（母集団定義の頑健性）
    try:
        T.N_CAR = 7
        preds = {k: x for k, x in T._load_wf_preds().items() if "2025-01-01" <= x[2] <= "2025-12-31"}
        e2 = np.array([ent(list(x[1].values())) for x in preds.values()])  # 2025 全7車 23,056R
        print("p80 (wf_preds 2025 全7車 n=%d):" % len(e2), float(np.percentile(e2, 80)))
        out["p80_wf"] = float(np.percentile(e2, 80)); out["n_wf"] = len(e2)
        pickle.dump(out, open(D / "h06_pop.pkl", "wb"))
    except BaseException as e:
        print("wf_preds 読めず:", repr(e)[:200])

    # 最終三連単オッズ（対象レースのみ・月ごと）
    from _db import connect
    keys = sorted({str(KEY[i]) for i in tgt})
    rows = []
    with connect() as c, c.cursor() as cur:
        for j in range(0, len(keys), 500):
            ch = keys[j:j + 500]
            cur.execute("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
                        "WHERE bet_type='trifecta' AND race_key = ANY(%s)", (ch,))
            rows += cur.fetchall()
        # 欠車確認用: 7車レースの出走車番
        ent_rows = []
        for j in range(0, len(keys), 500):
            ch = keys[j:j + 500]
            cur.execute("SELECT race_key, array_agg(frame_no ORDER BY frame_no) FROM keirin.wt_entries "
                        "WHERE race_key = ANY(%s) GROUP BY race_key", (ch,))
            ent_rows += cur.fetchall()
    df = pd.DataFrame(rows, columns=["race_key", "combination", "odds_value"])
    df["combination"] = df.combination.str.replace("=", "-", regex=False)
    print("final trifecta rows:", len(df), "races:", df.race_key.nunique(), "sep sample:", df.combination.head(2).tolist())
    FIN = {}
    for k, g in df.groupby("race_key"):
        d = {}
        for cmb, o in zip(g.combination, g.odds_value):
            if cmb in CMAP and o and o > 0:
                d[CMAP[cmb]] = float(o)
        FIN[k] = d
    ENTR = {k: list(a) for k, a in ent_rows}
    pickle.dump(dict(FIN=FIN, ENTR=ENTR), open(D / "h06_final.pkl", "wb"))
    print("FIN races", len(FIN), "medianpoints", np.median([len(d) for d in FIN.values()]))
    odd = [k for k, a in ENTR.items() if a != list(range(1, 8))]
    print("対象レースで出走車番が 1..7 でない:", len(odd), odd[:5])


if __name__ == "__main__":
    main()
