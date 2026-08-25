"""9車の軸モデル A/B（honest walk-forward・4腕）。

腕:
  A0 現行     : FEATURE_COLS_WT / 全車数で学習
  A1 車数対応 : + n_entries と車数正規化した相対特徴 / 全車数で学習
  A2 9車専用  : FEATURE_COLS_WT / 9車だけで学習
  A3 9車専用+ : A1 の特徴 / 9車だけで学習

指標（テストは常に9車のみ）:
  concordance（レース内 3着内 識別）/ 二軸そろい / 軸1 3着内
"""
import sys, pickle, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, '.')
from src.preprocessing.feature_wt import FEATURE_COLS_WT, TARGET_COL_WT
from src.database import get_connection

WF = [("2024-07-01","2024-09-30"),("2024-10-01","2024-12-31"),
      ("2025-01-01","2025-03-31"),("2025-04-01","2025-06-30"),
      ("2025-07-01","2025-09-30"),("2025-10-01","2025-12-31"),
      ("2026-01-01","2026-03-31"),("2026-04-01","2026-06-30"),
      ("2026-07-01","2026-08-04")]
SEEDS = [42, 101]

df = pd.read_pickle('/tmp/feat_all.pkl')
with get_connection() as c:
    ne = dict(c.execute("SELECT race_key, n_entries FROM wt_races"))
df["n_entries"] = pd.to_numeric(df["race_key"].map(ne), errors="coerce")
df = df[df.n_entries.notna()].copy()
n = df["n_entries"].astype(float)

# --- 車数正規化した相対特徴（A1/A3） ---
df["score_rank_n"]  = (df["score_rank"] - 1) / (n - 1)
df["wr_rank_n"]     = (df["wr_rank"] - 1) / (n - 1)
df["top3r_rank_n"]  = (df["top3r_rank"] - 1) / (n - 1)
df["frame_frac"]    = (df["frame_no"] - 1) / (n - 1)
df["is_outer_n"]    = (df["frame_no"] >= n - 2).astype(int)
df["n_senko_frac"]  = df["n_senko"] / n
df["n_lines_frac"]  = df["n_lines"] / n
EXTRA = ["n_entries","score_rank_n","wr_rank_n","top3r_rank_n","frame_frac",
         "is_outer_n","n_senko_frac","n_lines_frac"]

ARMS = {
    "A0 現行":      (FEATURE_COLS_WT, "all"),
    "A1 車数対応":  (FEATURE_COLS_WT + EXTRA, "all"),
    "A2 9車専用":   (FEATURE_COLS_WT, "n9"),
    "A3 9車専用+":  (FEATURE_COLS_WT + EXTRA, "n9"),
}

fo = pd.to_numeric(df["finish_order"], errors="coerce")
df["top3"] = ((fo >= 1) & (fo <= 3)).astype(int)

out = []
for w_from, w_to in WF:
    tr_all = df[df.race_date < w_from]
    te = df[(df.race_date >= w_from) & (df.race_date <= w_to) & (df.n_entries == 9)]
    if len(te) == 0 or len(tr_all) < 20000:
        print(f"[skip] {w_from}", flush=True); continue
    base = te[["race_key","frame_no","top3","race_date"]].copy()
    for name, (cols, scope) in ARMS.items():
        tr = tr_all if scope == "all" else tr_all[tr_all.n_entries == 9]
        ps = []
        for s in SEEDS:
            m = lgb.LGBMClassifier(objective="binary", n_estimators=500,
                learning_rate=0.05, num_leaves=31, min_child_samples=20,
                subsample=0.8, colsample_bytree=0.8, random_state=s,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(tr[cols], tr[TARGET_COL_WT])
            ps.append(m.predict_proba(te[cols])[:, 1])
        base[name] = np.mean(ps, axis=0)
        print(f"[{w_from}] {name} train={len(tr):,} done", flush=True)
    out.append(base)

R = pd.concat(out, ignore_index=True)
R.to_pickle("/tmp/ab9.pkl")
print(f"\n完了 {R.race_key.nunique():,}R", flush=True)
