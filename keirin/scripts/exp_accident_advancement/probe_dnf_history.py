"""選手の落車・失格（DNF）履歴が特徴量になるかを実測する（調査専用・DB は読むだけ）。

- DNF の正本は本番学習コードと同じ `wt_entries.finish_order == 0`
  （`src/cli/main.py` の `_dnf0`）。ただしレース中止（`wt_races.cancel=1`）は
  選手の事故ではないので母集団から外す。
- 「過去の DNF 率」は必ず **そのレースより前だけ**で作る（as-of）。
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import psycopg2

SQL = """
SELECT r.race_date, e.race_key, e.player_id, e.frame_no, e.finish_order,
       e.pred_top3_pct, r.race_type, e.player_class
FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
WHERE r.cancel = 0 AND e.finish_order IS NOT NULL
ORDER BY r.race_date, e.race_key, e.frame_no
"""


def main() -> None:
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as c:
        df = pd.read_sql(SQL, c)
    df["dnf"] = (df["finish_order"] == 0).astype(int)
    df["top3"] = df["finish_order"].between(1, 3).astype(int)
    print(f"rows={len(df):,}  races={df.race_key.nunique():,}  "
          f"players={df.player_id.nunique():,}  DNF率={df.dnf.mean():.4%}")

    # --- 1) 選手間のばらつきは本物か（二項分布より過分散か） ---
    g = df.groupby("player_id")["dnf"].agg(["size", "sum"])
    g = g[g["size"] >= 100]
    p = g["sum"].sum() / g["size"].sum()
    obs = ((g["sum"] - g["size"] * p) ** 2 / (g["size"] * p * (1 - p))).sum()
    dof = len(g) - 1
    print(f"\n[過分散検定] 選手 {len(g):,}人(100走以上)  全体DNF率={p:.4%}")
    print(f"  chi2={obs:,.0f}  dof={dof:,}  chi2/dof={obs / dof:.3f}  "
          f"(1.0 なら選手差はゼロ＝全部たまたま)")

    # --- 2) as-of の過去DNF率が「次のDNF」を当てるか（前後半の相関） ---
    df = df.sort_values(["race_date", "race_key"]).reset_index(drop=True)
    df["cum_n"] = df.groupby("player_id").cumcount()
    df["cum_dnf"] = df.groupby("player_id")["dnf"].cumsum() - df["dnf"]
    sub = df[df["cum_n"] >= 100].copy()
    sub["prior_rate"] = sub["cum_dnf"] / sub["cum_n"]
    q = pd.qcut(sub["prior_rate"], 5, labels=False, duplicates="drop")
    print(f"\n[as-of 予測力] 母集団 {len(sub):,}行（過去100走以上ある行のみ）")
    print("  過去DNF率5分位  ->  そのレースのDNF率 / 3着内率")
    for i in sorted(pd.Series(q).dropna().unique()):
        m = q == i
        print(f"   Q{int(i) + 1}  prior={sub.loc[m,'prior_rate'].mean():.3%}  "
              f"n={m.sum():,}  DNF={sub.loc[m,'dnf'].mean():.3%}  "
              f"top3={sub.loc[m,'top3'].mean():.2%}")
    lo, hi = q == q.min(), q == q.max()
    a, b = sub.loc[lo, "dnf"], sub.loc[hi, "dnf"]
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    print(f"  Q5-Q1 差 = {(b.mean() - a.mean()):+.3%}  (SE {se:.3%}, "
          f"z={(b.mean() - a.mean()) / se:+.2f})")

    # --- 3) 直近窓（過去1年）で見た場合 ---
    df["race_date"] = pd.to_datetime(df["race_date"])
    print("\n[直近1年窓] 実装コストが高いので、代わりに直近20走窓で近似する")
    df["roll20_dnf"] = (df.groupby("player_id")["dnf"]
                          .transform(lambda s: s.shift(1).rolling(20, min_periods=20).sum()))
    s2 = df.dropna(subset=["roll20_dnf"])
    print("  直近20走のDNF回数 -> 次のDNF率 / 3着内率")
    for k, gg in s2.groupby(s2["roll20_dnf"].clip(upper=3)):
        print(f"   {int(k)}回  n={len(gg):,}  DNF={gg.dnf.mean():.3%}  "
              f"top3={gg.top3.mean():.2%}")


if __name__ == "__main__":
    main()


def incremental() -> None:
    """モデル予測（pred_top3_pct）の上に DNF 履歴が何か足すかを見る。

    ⚠️ `pred_top3_pct` は全期間 full-refit のモデルが書いた列で過去分は in-sample。
       ここでは「順位の統制」としてだけ使い、水準は読まない。
    """
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as c:
        df = pd.read_sql(SQL, c)
    df["dnf"] = (df["finish_order"] == 0).astype(int)
    df["top3"] = df["finish_order"].between(1, 3).astype(int)
    df = df.sort_values(["race_date", "race_key"]).reset_index(drop=True)
    df["cum_n"] = df.groupby("player_id").cumcount()
    df["cum_dnf"] = df.groupby("player_id")["dnf"].cumsum() - df["dnf"]
    df = df[(df["cum_n"] >= 100) & df["pred_top3_pct"].notna()].copy()
    df["prior_rate"] = df["cum_dnf"] / df["cum_n"]
    df["pd_bin"] = pd.qcut(df["pred_top3_pct"].astype(float), 10,
                           labels=False, duplicates="drop")
    print(f"\n[増分] n={len(df):,}  pred_top3_pct 十分位 × 過去DNF率 上下半分")
    print("  bin  n      予測3着内   低DNF群top3  高DNF群top3   差")
    tot_lo = tot_hi = 0.0
    n_lo = n_hi = 0
    for b, gg in df.groupby("pd_bin"):
        med = gg["prior_rate"].median()
        lo, hi = gg[gg.prior_rate <= med], gg[gg.prior_rate > med]
        if not len(lo) or not len(hi):
            continue
        print(f"  {int(b):3d}  {len(gg):7,}  {gg.pred_top3_pct.astype(float).mean():.3f}   "
              f"{lo.top3.mean():.2%}      {hi.top3.mean():.2%}     "
              f"{hi.top3.mean() - lo.top3.mean():+.2%}")
        tot_lo += lo.top3.sum(); n_lo += len(lo)
        tot_hi += hi.top3.sum(); n_hi += len(hi)
    print(f"  合計 低DNF群 {tot_lo / n_lo:.2%} (n={n_lo:,}) / "
          f"高DNF群 {tot_hi / n_hi:.2%} (n={n_hi:,})  差 {tot_hi / n_hi - tot_lo / n_lo:+.2%}")

    # レース単位: 軸2車（pred_top3 上位2）の過去DNF率で二軸そろい率が動くか
    df["rk"] = df.groupby("race_key")["pred_top3_pct"].rank(ascending=False, method="first")
    ax = df[df.rk <= 2]
    per = ax.groupby("race_key").agg(n=("rk", "size"), prior=("prior_rate", "max"),
                                     both=("top3", "sum"), dnf=("dnf", "sum"))
    per = per[per.n == 2]
    per["both3"] = (per["both"] == 2).astype(int)
    per["axis_dnf"] = (per["dnf"] > 0).astype(int)
    per["q"] = pd.qcut(per["prior"], 5, labels=False, duplicates="drop")
    print(f"\n[レース単位] 軸2車の過去DNF率(max) 5分位  races={len(per):,}")
    for q, gg in per.groupby("q"):
        print(f"   Q{int(q) + 1}  n={len(gg):,}  prior_max={gg.prior.mean():.3%}  "
              f"軸DNF発生={gg.axis_dnf.mean():.3%}  二軸そろい={gg.both3.mean():.2%}")


if __name__ == "__main__":
    incremental()
