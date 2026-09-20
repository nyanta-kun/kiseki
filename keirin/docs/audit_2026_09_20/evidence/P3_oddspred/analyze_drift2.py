import numpy as np, pandas as pd
BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
d = pd.read_pickle(f"{BASE}/P3_oddspred/pred_vs_final_7car_v2.pkl")
d["logratio"] = np.log10(d.pred_coh / d.final)
d["abs_logratio"] = d.logratio.abs()
d["ratio"] = d.pred_coh / d.final
pd.set_option("display.width", 140)

print("=== 月別（v2: wt_entries.pred_* [現在値] + 現行凍結odds_tfモデル vs 確定・7車）===")
g = d.groupby("ym")
tab = g.agg(n=("logratio", "size"),
            median_ratio=("ratio", "median"),
            mae_log=("abs_logratio", "mean"),
            within2x=("ratio", lambda s: ((s >= 0.5) & (s <= 2.0)).mean()))
print(tab.round(4))

print("\n=== train_end(2025-12-31)基準 in-sample/OOS ===")
d["insample"] = d.ym <= "2025-12"
for tag, sub in d.groupby("insample"):
    print(tag, "n=", len(sub), "median_ratio=", round(sub.ratio.median(), 4),
          "mae_log=", round(sub.abs_logratio.mean(), 4))

print("\n=== 予測オッズ帯別×月（2026年のみ）===")
d2026 = d[d.ym >= "2026-01"].copy()
d2026["band"] = pd.cut(d2026.pred_coh, [0, 2, 5, 10, 20, 50, 100, 1e9],
                        labels=["0-2", "2-5", "5-10", "10-20", "20-50", "50-100", "100+"])
bt = d2026.groupby(["ym", "band"], observed=True).agg(
    n=("logratio", "size"), median_ratio=("ratio", "median"))
print(bt.round(4).to_string())

print("\n=== 帯別合計(2026)===")
print(d2026.groupby("band", observed=True).agg(n=("ratio", "size"), median_ratio=("ratio", "median"),
                                                mae_log=("abs_logratio", "mean")).round(4))
