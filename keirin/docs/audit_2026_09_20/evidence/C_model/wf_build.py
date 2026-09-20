"""honest walk-forward 予測を自前で作る（依頼C・1）。

- 特徴量は data/feature_cache/wtfeat_20221201_20260831_f70_*.pkl（FEATURE_COLS_WT 70列）
- 本番と同じハイパラ（src/models/trainer.train_lgbm の最終 fit と同一）
- expanding: 学習=2022-12-01 〜 窓開始の前日 / 予測=窓
"""
import sys, time, os
import numpy as np, pandas as pd
import lightgbm as lgb

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.preprocessing.feature_wt import FEATURE_COLS_WT

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
CACHE = "/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"

PARAMS = dict(objective="binary", metric="auc", n_estimators=500, learning_rate=0.05,
              num_leaves=31, min_child_samples=20, colsample_bytree=0.8,
              random_state=42, verbose=-1, n_jobs=-1)

WINDOWS = [("2024-07-01","2024-09-30"),("2024-10-01","2024-12-31"),
           ("2025-01-01","2025-03-31"),("2025-04-01","2025-06-30"),
           ("2025-07-01","2025-09-30"),("2025-10-01","2025-12-31"),
           ("2026-01-01","2026-03-31"),("2026-04-01","2026-06-30"),
           ("2026-07-01","2026-08-31")]

def main():
    df = pd.read_pickle(CACHE)
    df = df[df["finish_order"].notna()].copy()   # 本番 train_wt と同じ母集団
    df["race_date"] = df["race_date"].astype(str)
    X_all = df.reindex(columns=FEATURE_COLS_WT).fillna(0).values.astype(np.float32)
    out = []
    for lo, hi in WINDOWS:
        tr = (df["race_date"] < lo).values
        te = ((df["race_date"] >= lo) & (df["race_date"] <= hi)).values
        print(f"[{lo}..{hi}] train={tr.sum():,} test={te.sum():,}", flush=True)
        sub = df.loc[te, ["race_key","race_date","frame_no","player_id",
                          "finish_order","top3_flag","win_flag","top2_flag"]].copy()
        sub["window"] = lo
        for tgt, col in (("top3_flag","p3"), ("win_flag","pw")):
            t0 = time.time()
            m = lgb.LGBMClassifier(**PARAMS)
            m.fit(pd.DataFrame(X_all[tr], columns=FEATURE_COLS_WT), df.loc[tr, tgt].values)
            sub[col] = m.predict_proba(pd.DataFrame(X_all[te], columns=FEATURE_COLS_WT))[:,1]
            print(f"   {col}: fit {time.time()-t0:.0f}s", flush=True)
            if lo == "2026-04-01" and col == "p3":
                imp = pd.Series(m.feature_importances_, index=FEATURE_COLS_WT).sort_values(ascending=False)
                imp.to_csv(f"{OUT}/feat_importance_p3_2026Q2.csv")
        out.append(sub)
    res = pd.concat(out, ignore_index=True)
    res.to_pickle(f"{OUT}/wf_preds_audit.pkl")
    print("saved", res.shape)

main()
