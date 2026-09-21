#!/usr/bin/env python3
"""勝ち上がりの「当落線」— 準決勝3着で決勝を逃した選手の A/B（2026-09-21）。

発端: `docs/meeting_structure_unused_2026_09_21.md` §6。
最終日の非決勝レース（＝勝ち上がりが終わった消化レース）で、前日の準決勝を
**3着**（あと1枠）で落ちた選手に、両窓とも +2.0〜+3.9pt の未カバー残差があった。
4着以下での落選は窓で符号が反転するので、効いているのは「落ちた」ことではなく
「当落線上だった」こと。

⚠️ 残差があっても特徴量にすると増分ゼロ、が同日 [[meeting_form_fillna_2026_09_21]]
   §3 で再現している。**残差だけを根拠に実装しない**ためにこの A/B を取る。

特徴（すべて point-in-time・**自分より前の日**の結果しか見ない）:
    sf_ran        今節ここまでに準決勝を走ったか
    sf_best_order そのときの最良着順（未走は 0）
    sf_bubble     準決勝3着だったか（＝当落線で落ちた／拾われた）

方法論は `exp_meeting_form_ab.py` / `exp_field_size_ab.py` と同一（2窓 × 5seed）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT, TARGET_COL_WT, load_features_wt,
)

TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202, 303, 404]
NEW = ["sf_ran", "sf_best_order", "sf_bubble"]


def add_semifinal_features(df: pd.DataFrame) -> pd.DataFrame:
    """同一節の**前の日**の準決勝の自分の着順を付与する。"""
    import os
    from sqlalchemy import create_engine, text
    sql = ("SELECT e.race_key, e.player_id, e.finish_order, r.cup_id, r.day_index, r.race_type "
           "FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key=r.race_key")
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as c:
        H = pd.read_sql_query(text(sql), c)
    eng.dispose()
    H["_day"] = pd.to_numeric(H["day_index"], errors="coerce")
    H["_fo"] = pd.to_numeric(H["finish_order"], errors="coerce")
    sf = H[H["race_type"].fillna("").str.contains("準決") & H["_fo"].between(1, 9)]
    sf = (sf.groupby(["cup_id", "player_id", "_day"], as_index=False)["_fo"].min()
            .rename(columns={"_fo": "sf_fo", "_day": "sf_day"}))
    # 自分の出走（race_key 単位）へ、**自分より前の日**の準決勝だけを結ぶ
    me = H[["race_key", "player_id", "cup_id", "_day"]].drop_duplicates()
    j = me.merge(sf, on=["cup_id", "player_id"], how="left")
    j = j[j["sf_day"].isna() | (j["sf_day"] < j["_day"])]
    g = j.groupby(["race_key", "player_id"], as_index=False)["sf_fo"].min()
    out = df.merge(g, on=["race_key", "player_id"], how="left")
    out["sf_ran"] = out["sf_fo"].notna().astype(int)
    out["sf_best_order"] = out["sf_fo"].fillna(0.0)
    out["sf_bubble"] = (out["sf_fo"] == 3).astype(int)
    return out.drop(columns=["sf_fo"])


def race_metrics(t: pd.DataFrame, pcol: str) -> dict:
    g = t[t["field_size"] == 7]
    win = top3 = pair = n = 0
    for _, r in g.groupby("race_key"):
        if (r["_fo"] >= 1).sum() < 3:
            continue
        o = r.sort_values(pcol, ascending=False)
        f1, f2 = o["_fo"].iloc[0], o["_fo"].iloc[1]
        n += 1
        win += int(f1 == 1)
        top3 += int(1 <= f1 <= 3)
        pair += int(1 <= f1 <= 3 and 1 <= f2 <= 3)
    return {"n": n, "win": win / n, "top3": top3 / n, "pair": pair / n}


def main() -> None:
    df = load_features_wt("2022-12-01", "2026-08-31", use_cache=True)
    df = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] <= max(t for _, t in WINDOWS.values()))]
    df = add_semifinal_features(df)
    df["field_size"] = df.groupby("race_key")["frame_no"].transform("size").astype(int)
    df["_fo"] = pd.to_numeric(df["finish_order"], errors="coerce").fillna(99)
    print("付与後:", df[NEW].mean().round(4).to_dict(), f"  行 {len(df):,}")

    arms = [("base", list(FEATURE_COLS_WT)),
            ("+当落線", list(FEATURE_COLS_WT) + NEW)]
    for wn, (tf, tt) in WINDOWS.items():
        train = df[df["race_date"] < tf]
        test = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)].copy()
        print(f"\n######## {wn} test={tf}〜{tt}  train {len(train):,} / test {len(test):,}")
        res = {}
        for arm, cols in arms:
            a, w, t3, pr = [], [], [], []
            m = None
            for s in SEEDS:
                m = lgb.LGBMClassifier(objective="binary", n_estimators=500, learning_rate=0.05,
                                       num_leaves=31, min_child_samples=20, subsample=0.8,
                                       colsample_bytree=0.8, random_state=s,
                                       deterministic=True, force_row_wise=True, verbose=-1)
                m.fit(train[cols], train[TARGET_COL_WT])
                p = m.predict_proba(test[cols])[:, 1]
                a.append(roc_auc_score(test[TARGET_COL_WT], p))
                test["_p"] = p
                r = race_metrics(test, "_p")
                w.append(r["win"])
                t3.append(r["top3"])
                pr.append(r["pair"])
            res[arm] = (a, w, t3, pr)
            print(f"== {arm} ({len(cols)}特徴)  AUC {np.mean(a):.5f} ± {np.std(a):.5f}  "
                  f"7車 1位勝率 {np.mean(w)*100:.2f}%  1位3着内 {np.mean(t3)*100:.2f}%  "
                  f"そろい {np.mean(pr)*100:.2f}%")
            if arm != "base":
                imp = pd.Series(m.feature_importances_, index=cols)
                rk = imp.rank(ascending=False).astype(int)
                print("   " + "  ".join(f"{c} imp={imp[c]} 順位{rk[c]}/{len(cols)}" for c in NEW))
        b, v = res["base"], res["+当落線"]
        print(f"== 差分 ΔAUC {np.mean(v[0])-np.mean(b[0]):+.5f} "
              f"(base seed sd {np.std(b[0]):.5f})  "
              f"Δ1位勝率 {(np.mean(v[1])-np.mean(b[1]))*100:+.2f}pt  "
              f"Δ1位3着内 {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt  "
              f"Δそろい {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt")


if __name__ == "__main__":
    main()
