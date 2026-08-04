"""地方競馬 穴馬推奨の「そもそも検証可能か」を決める統計的検出力分析。

## なぜ最初にこれをやるか

穴馬（高オッズ馬）の単勝は 1 ベットあたりの払戻分散が極端に大きい。
的中率 7% / オッズ 15 倍なら 1 ベットの標準偏差は約 3.7（＝ROI 1 本分の 3.7 倍）。
つまり **n が小さいセグメントの ROI は、真のエッジが 0 でも簡単に 1.3 を超える**。

過去の探索（`chihou_walkforward_sweep.py`）が見つけた「ROI≥1.10 候補5件（n=46〜123）」も、
まずこの分散で説明できてしまう。したがって条件を探す前に

  「利用可能な標本数で、ROI≥1.0 をノイズと区別できるのか」

を先に確定させる。ここで必要 n が現実的に到達不能と分かれば、
以降のどんな探索結果も「採用してはいけない」と事前に判定できる。

## 出力

  1. 控除率ベースライン（全馬・帯別の実測 ROI）
  2. 1 ベットあたり払戻の実測 sd（オッズ帯別）
  3. 必要ベット数: 観測 ROI が X のとき 95%CI 下限 > 1.0 となる最小 n
  4. 利用可能ベット数: 選択率を変えたときに 1 年あたり何ベット取れるか

探索期間（DISCOVERY）のデータだけを使う。分散はエッジではないので厳密には
どの期間でもよいが、確認用期間に一切触れない原則を機械的に守る。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_power.py --csv /path/to/wf.csv
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

# ── 期間の凍結（このファイルが唯一の定義。下流スクリプトはここから import する）──
# DISCOVERY: 条件探索に何度使ってもよい
# HOLDOUT  : 確認のため一度だけ使う。探索中は絶対に開かない
DISCOVERY_START, DISCOVERY_END = "20240701", "20250930"
HOLDOUT_START, HOLDOUT_END = "20251001", "20260731"

# 穴馬の定義（単勝オッズ下限）。人気順ではなくオッズで定義するのは、
# 頭数によって「5番人気」の意味が変わるため。
DARKHORSE_MIN_ODDS = 10.0


def load(csv: str) -> pd.DataFrame:
    df = pd.read_csv(csv)
    df["date"] = df["date"].astype(str)
    df["hit"] = (df["finish_position"] == 1).astype(int)
    df["payout"] = df["hit"] * df["win_odds"]  # 100円賭けたときの回収（単位: 賭け金）
    return df


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    disc = df[(df["date"] >= DISCOVERY_START) & (df["date"] <= DISCOVERY_END)].copy()
    hold = df[(df["date"] >= HOLDOUT_START) & (df["date"] <= HOLDOUT_END)].copy()
    return disc, hold


def _n_required(roi_obs: float, sd: float, target: float = 1.0, z: float = 1.96) -> float:
    """観測 ROI が roi_obs のとき、95%CI 下限が target を超える最小 n。

    roi_obs - z*sd/sqrt(n) > target  ⇔  n > (z*sd/(roi_obs-target))^2
    """
    if roi_obs <= target:
        return float("inf")
    return (z * sd / (roi_obs - target)) ** 2


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = load(args.csv)
    disc, hold = split(df)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース")
    print(f"  DISCOVERY {DISCOVERY_START}-{DISCOVERY_END}: "
          f"{len(disc):,}行 / {disc['race_id'].nunique():,}レース")
    print(f"  HOLDOUT   {HOLDOUT_START}-{HOLDOUT_END}: "
          f"{len(hold):,}行 / {hold['race_id'].nunique():,}レース  ← 本スクリプトでは開かない")

    d = disc
    d["odds_band"] = pd.cut(
        d["win_odds"], bins=[0, 2, 4, 7, 10, 15, 20, 30, 50, 100, 10**6],
        labels=["<2", "2-4", "4-7", "7-10", "10-15", "15-20", "20-30", "30-50", "50-100", "100+"],
    )

    # ── 1&2. 控除率ベースラインと払戻分散 ──
    print(f"\n{'=' * 92}")
    print("  [1] オッズ帯別 実測ベースライン（DISCOVERY・全出走馬を機械的に買った場合）")
    print(f"{'=' * 92}")
    print(f"{'帯':>8} {'n':>8} {'的中率':>8} {'ROI':>7} {'払戻sd':>8} "
          f"{'SE(ROI)@n':>10} {'ROI1.0の95%CI幅':>16}")
    rows = []
    for band, g in d.groupby("odds_band", observed=True):
        n = len(g)
        roi = g["payout"].mean()
        sd = g["payout"].std(ddof=1)
        se = sd / np.sqrt(n)
        rows.append({"band": str(band), "n": n, "hit": g["hit"].mean(), "roi": roi, "sd": sd})
        print(f"{str(band):>8} {n:>8,} {g['hit'].mean():>8.4f} {roi:>7.3f} {sd:>8.3f} "
              f"{se:>10.4f} {'±' + format(1.96 * se, '.3f'):>16}")
    all_roi = d["payout"].mean()
    print(f"\n  全帯まとめ: n={len(d):,}  ROI={all_roi:.4f}  "
          f"（＝実質控除率 {(1 - all_roi) * 100:.1f}%。これが超えるべき壁）")

    # ── 3. 穴馬帯で ROI≥1.0 を主張するのに必要なベット数 ──
    dark = d[d["win_odds"] >= DARKHORSE_MIN_ODDS]
    sd_dark = dark["payout"].std(ddof=1)
    print(f"\n{'=' * 92}")
    print(f"  [2] 穴馬帯（単勝≥{DARKHORSE_MIN_ODDS:.0f}倍）で「ROI>1.0」を統計的に主張するのに必要なベット数")
    print(f"{'=' * 92}")
    print(f"  母集団: n={len(dark):,}  的中率={dark['hit'].mean():.4f}  "
          f"ROI={dark['payout'].mean():.3f}  1ベット払戻sd={sd_dark:.3f}")
    print(f"\n  ※ 選び方で sd は多少変わるが、桁は変わらない。以下は sd={sd_dark:.2f} を仮定\n")
    print(f"{'観測ROI':>10} {'必要n(95%CI下限>1.0)':>24} {'現実性':>10}")
    months = 15.0  # DISCOVERY の長さ
    for roi_obs in [1.05, 1.10, 1.20, 1.30, 1.50, 2.00]:
        n_req = _n_required(roi_obs, sd_dark)
        feasible = "○" if n_req <= 3000 else ("△" if n_req <= 20000 else "×")
        print(f"{roi_obs:>10.2f} {n_req:>24,.0f} {feasible:>10}")

    # ── 4. 選択率と取得可能ベット数 ──
    n_races_disc = d["race_id"].nunique()
    races_per_month = n_races_disc / months
    print(f"\n{'=' * 92}")
    print("  [3] 推奨の絞り込み度合いと、確保できるベット数")
    print(f"{'=' * 92}")
    print(f"  DISCOVERY: {n_races_disc:,}レース / {months:.0f}ヶ月 = 月 {races_per_month:,.0f}レース")
    print(f"\n{'推奨頻度':>22} {'年間ベット数':>14} {'2年で':>10} {'ROI1.2を証明できるか':>22}")
    n_req_12 = _n_required(1.20, sd_dark)
    for label, rate in [("全レースの1/2", 0.5), ("1/5", 0.2), ("1/10", 0.1),
                        ("1/20", 0.05), ("1/50", 0.02), ("1/100", 0.01)]:
        per_year = races_per_month * 12 * rate
        two_year = per_year * 2
        verdict = "可" if two_year >= n_req_12 else f"不可(必要{n_req_12:,.0f})"
        print(f"{label:>22} {per_year:>14,.0f} {two_year:>10,.0f} {verdict:>22}")

    # ── 5. 「偶然 ROI>1.2 が出る」確率（多重比較の実感）──
    print(f"\n{'=' * 92}")
    print("  [4] エッジがゼロでも偶然 ROI が高く出る確率（ブートストラップ）")
    print(f"{'=' * 92}")
    rng = np.random.default_rng(0)
    pay = dark["payout"].values
    base = pay.mean()
    print(f"  帰無仮説: 穴馬帯からランダムに n 頭選ぶ（真のROI={base:.3f}・エッジなし）\n")
    print(f"{'n':>8} {'ROI>1.0の確率':>16} {'ROI>1.2の確率':>16} {'ROI>1.5の確率':>16} "
          f"{'153セグメント中の期待該当数(>1.1)':>34}")
    for n in [50, 100, 200, 500, 1000, 2000, 5000]:
        sims = rng.choice(pay, size=(4000, n), replace=True).mean(axis=1)
        p10, p12, p15 = (sims > 1.0).mean(), (sims > 1.2).mean(), (sims > 1.5).mean()
        p11 = (sims > 1.1).mean()
        print(f"{n:>8,} {p10:>16.3f} {p12:>16.3f} {p15:>16.3f} {p11 * 153:>34.1f}")

    print(f"\n{'=' * 92}")
    print("  結論の読み方: [2] の必要 n を [3] の確保可能ベット数が下回るなら、")
    print("  その ROI 水準は現データでは『主張できない』。[4] は探索でヒットが出ても")
    print("  それが偶然で説明できる規模かを判断する基準になる。")
    print(f"{'=' * 92}")


if __name__ == "__main__":
    main()
