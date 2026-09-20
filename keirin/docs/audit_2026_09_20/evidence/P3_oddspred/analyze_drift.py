import numpy as np, pandas as pd
BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
d = pd.read_pickle(f"{BASE}/P3_oddspred/pred_vs_final_7car.pkl")
d["logratio"] = np.log10(d.pred_coh / d.final)
d["abs_logratio"] = d.logratio.abs()
d["ratio"] = d.pred_coh / d.final

print("=== 月別（整合板 vs 確定・7車・無作為4,159R・210点/R）===")
g = d.groupby("ym")
tab = g.agg(n=("logratio", "size"),
            median_log=("logratio", "median"),
            q25=("logratio", lambda s: s.quantile(.25)),
            q75=("logratio", lambda s: s.quantile(.75)),
            mae_log=("abs_logratio", "mean"),
            median_ratio=("ratio", "median"),
            within2x=("ratio", lambda s: ((s >= 0.5) & (s <= 2.0)).mean()))
tab["iqr_log"] = tab.q75 - tab.q25
pd.set_option("display.width", 140)
print(tab[["n", "median_ratio", "median_log", "iqr_log", "mae_log", "within2x"]].round(4))

print("\n=== train_end(2025-12-31)を境に in-sample / OOS ===")
d["insample"] = d.ym <= "2025-12"
for tag, sub in d.groupby("insample"):
    print(tag, "n=", len(sub), "median_ratio=", round(sub.ratio.median(), 4),
          "mae_log=", round(sub.abs_logratio.mean(), 4),
          "within2x=", round(((sub.ratio >= .5) & (sub.ratio <= 2.0)).mean(), 4))

print("\n=== OOS期間だけ・月あたり経過月数との相関 ===")
oos = d[~d.insample].copy()
oos["months_since_train_end"] = oos.ym.apply(lambda s: (int(s[:4]) - 2026) * 12 + int(s[5:7]))
mm = oos.groupby("months_since_train_end").agg(
    n=("logratio", "size"), median_ratio=("ratio", "median"),
    mae_log=("abs_logratio", "mean"))
print(mm.round(4))
from scipy.stats import spearmanr
rho, p = spearmanr(oos.months_since_train_end, oos.abs_logratio)
print("Spearman(経過月数, |log比|) =", round(rho, 4), "p=", p)
rho2, p2 = spearmanr(oos.groupby("months_since_train_end").abs_logratio.mean().index,
                      oos.groupby("months_since_train_end").abs_logratio.mean().values)
print("Spearman(集計後・月次MAE) =", round(rho2, 4))

print("\n=== 予測オッズ帯別（OOS 2026-01〜08）===")
oos["band"] = pd.cut(oos.pred_coh, [0, 2, 5, 10, 20, 50, 100, 1e9],
                      labels=["0-2", "2-5", "5-10", "10-20", "20-50", "50-100", "100+"])
bt = oos.groupby("band", observed=True).agg(
    n=("logratio", "size"), median_ratio=("ratio", "median"),
    mae_log=("abs_logratio", "mean"))
print(bt.round(4))

print("\n=== 予測オッズ帯 × in-sample/OOS ===")
d["band"] = pd.cut(d.pred_coh, [0, 2, 5, 10, 20, 50, 100, 1e9],
                    labels=["0-2", "2-5", "5-10", "10-20", "20-50", "50-100", "100+"])
bt2 = d.groupby(["band", "insample"], observed=True).agg(
    n=("logratio", "size"), median_ratio=("ratio", "median"))
print(bt2.round(4))
