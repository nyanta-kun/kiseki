"""地方競馬 穴馬推奨条件の探索（DISCOVERY 期間限定）。

## 設計方針

「ROI>1 のセグメントを総当たりで探す」ことはしない。
`chihou_darkhorse_power.py` [4] のとおり、エッジがゼロでも n=100 のセグメントは
14.7% の確率で ROI>1.2 を示す。153 セグメント舐めれば ROI>1.1 が期待値 27 件出る。
**総当たりは必ず「候補」を生む。それは発見ではない。**

代わりに検出力の高い順に段階を踏む:

  STEP 1（単一仮説・n=10万規模）:
      穴馬帯でモデルのスコアが市場（オッズ）を超える情報を持つか。
      持たないなら、そこから切り出すどんな条件も ROI>1 にはならない。
      ここで落ちれば以降は全て不要。

  STEP 2（STEP 1 が通った場合のみ）:
      事前登録した小さな仮説空間だけを評価し、Benjamini-Hochberg で FDR を制御。
      仮説は「オッズ帯 × モデル順位 × モデルEV」の直積に限定し、
      場・距離・馬場といった細分軸は**入れない**（n が落ちて検出不能になるため）。

  STEP 3:
      通過した条件のうち、確認期間で検定可能な n を確保できるものだけを凍結する。

HOLDOUT 期間は本スクリプトでは一切読まない。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_discovery.py --csv /path/to/wf.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.chihou_darkhorse_power import DARKHORSE_MIN_ODDS, load, split  # noqa: E402

RNG = np.random.default_rng(0)
N_BOOT = 4000


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """レース内正規化した勝率と、市場に対する超過（EV）を付与する。"""
    df = df.copy()
    # LGB の binary 出力はレース内で合計 1 にならないので正規化する
    s = df.groupby("race_id")["win_prob_wf"].transform("sum")
    df["p_norm"] = df["win_prob_wf"] / s.where(s > 0, np.nan)
    # 市場の含意確率（控除前）。オッズの逆数をレース内で正規化する
    inv = 1.0 / df["win_odds"]
    df["p_mkt"] = inv / inv.groupby(df["race_id"]).transform("sum")
    # モデル EV（賭け金 1 に対する期待回収）。1 を超えるほど割安
    df["ev"] = df["p_norm"] * df["win_odds"]
    # モデルと市場の乖離倍率
    df["edge_ratio"] = df["p_norm"] / df["p_mkt"]
    return df


def _roi_ci(sub: pd.DataFrame) -> tuple[int, float, float, float, float]:
    """(n, 的中率, ROI, CI下限, CI上限)。CI はブートストラップ・パーセンタイル法。"""
    n = len(sub)
    if n == 0:
        return 0, 0.0, 0.0, 0.0, 0.0
    pay = sub["payout"].values
    roi = pay.mean()
    boot = RNG.choice(pay, size=(N_BOOT, n), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return n, sub["hit"].mean(), roi, lo, hi


def _p_value_vs(sub: pd.DataFrame, null_roi: float) -> float:
    """H0: 真のROI = null_roi に対する片側 p 値（ブートストラップ）。

    観測 ROI が null 以下なら p=1 とする（穴馬推奨として使えないため）。
    """
    n = len(sub)
    if n == 0:
        return 1.0
    pay = sub["payout"].values
    roi = pay.mean()
    if roi <= null_roi:
        return 1.0
    centered = pay - roi + null_roi  # 平均を null に揃えた分布
    boot = RNG.choice(centered, size=(N_BOOT, n), replace=True).mean(axis=1)
    return float((boot >= roi).mean())


def benjamini_hochberg(pvals: list[float], alpha: float = 0.10) -> list[bool]:
    """BH 法で FDR を alpha に制御する。戻り値は各仮説の採択可否。"""
    m = len(pvals)
    order = np.argsort(pvals)
    passed = np.zeros(m, dtype=bool)
    thresh = -1
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= alpha * rank / m:
            thresh = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= thresh:
            passed[idx] = True
    return passed.tolist()


def step1(dark: pd.DataFrame) -> bool:
    """穴馬帯でモデルが市場超えの情報を持つかを単一仮説で検定する。"""
    print(f"\n{'=' * 96}")
    print("  STEP 1: 穴馬帯でモデルは市場を超える情報を持つか（単一仮説・全穴馬使用）")
    print(f"{'=' * 96}")
    base = dark["payout"].mean()
    print(f"  母集団: n={len(dark):,}  ベースROI={base:.4f}（この帯を機械的に全部買った場合）\n")

    verdicts = []
    for col, label in [("p_norm", "モデル勝率(レース内正規化)"),
                       ("ev", "モデルEV = 勝率×オッズ"),
                       ("edge_ratio", "モデル/市場 の乖離倍率")]:
        dark = dark.copy()
        dark["dec"] = pd.qcut(dark[col], 10, labels=False, duplicates="drop")
        print(f"  ── {label} の十分位別 ROI ──")
        print(f"{'decile':>8} {'n':>8} {'的中率':>8} {'ROI':>8} {'95%CI':>18} {'平均オッズ':>10}")
        rois = []
        for dec, g in dark.groupby("dec"):
            n, hit, roi, lo, hi = _roi_ci(g)
            rois.append(roi)
            print(f"{int(dec) + 1:>8} {n:>8,} {hit:>8.4f} {roi:>8.3f} "
                  f"{f'[{lo:.3f}, {hi:.3f}]':>18} {g['win_odds'].mean():>10.1f}")
        # 最上位十分位 vs 最下位十分位、および単調性（Spearman）
        top = dark[dark["dec"] == dark["dec"].max()]
        bot = dark[dark["dec"] == dark["dec"].min()]
        diff = top["payout"].mean() - bot["payout"].mean()
        bt = RNG.choice(top["payout"].values, size=(N_BOOT, len(top)), replace=True).mean(axis=1)
        bb = RNG.choice(bot["payout"].values, size=(N_BOOT, len(bot)), replace=True).mean(axis=1)
        dlo, dhi = np.percentile(bt - bb, [2.5, 97.5])
        from scipy.stats import spearmanr
        rho = spearmanr(range(len(rois)), rois).statistic
        sig = dlo > 0
        verdicts.append(sig)
        print(f"  → 最上位-最下位 ROI差 = {diff:+.3f}  95%CI [{dlo:+.3f}, {dhi:+.3f}]  "
              f"単調性ρ={rho:+.2f}  {'有意' if sig else '有意でない'}\n")

    ok = any(verdicts)
    print(f"  STEP 1 判定: {'通過（穴馬帯にモデル情報あり）' if ok else '不通過（穴馬帯でモデルは市場に勝てていない）'}")
    return ok


def step2(dark: pd.DataFrame, null_roi: float) -> pd.DataFrame:
    """事前登録した小さな仮説空間を BH-FDR 付きで評価する。"""
    print(f"\n{'=' * 96}")
    print("  STEP 2: 事前登録した仮説空間（オッズ帯 × モデル順位 × モデルEV）")
    print(f"{'=' * 96}")

    odds_gates = [("10-15", 10, 15), ("10-20", 10, 20), ("10-30", 10, 30),
                  ("15-30", 15, 30), ("20-50", 20, 50)]
    rank_gates = [("idx1", 1), ("idx<=2", 2), ("idx<=3", 3), ("idx<=5", 5)]
    ev_gates = [("EV>=0", 0.0), ("EV>=0.8", 0.8), ("EV>=1.0", 1.0)]

    rows = []
    for o_lab, o_lo, o_hi in odds_gates:
        for r_lab, r_max in rank_gates:
            for e_lab, e_min in ev_gates:
                sub = dark[
                    (dark["win_odds"] >= o_lo) & (dark["win_odds"] < o_hi)
                    & (dark["idx_rank_wf"] <= r_max) & (dark["ev"] >= e_min)
                ]
                if len(sub) < 100:  # 検定に耐えない小標本は最初から評価しない
                    continue
                n, hit, roi, lo, hi = _roi_ci(sub)
                pv = _p_value_vs(sub, null_roi)
                rows.append({
                    "rule": f"{o_lab} & {r_lab} & {e_lab}", "n": n, "hit": hit,
                    "roi": roi, "ci_lo": lo, "ci_hi": hi, "p": pv,
                    "n_per_year": n / 1.25,  # DISCOVERY は 15 ヶ月
                })
    res = pd.DataFrame(rows)
    if res.empty:
        print("  評価可能な仮説がありませんでした")
        return res
    res["bh_pass"] = benjamini_hochberg(res["p"].tolist(), alpha=0.10)
    res = res.sort_values("roi", ascending=False).reset_index(drop=True)
    print(f"  評価した仮説: {len(res)} 件（n<100 は除外済み）")
    print(f"  帰無仮説: 真のROI = {null_roi:.3f}（穴馬帯を機械的に買ったときの水準）")
    print(f"  多重比較: Benjamini-Hochberg FDR 10%\n")
    print(f"{'条件':>30} {'n':>7} {'年間':>7} {'的中率':>8} {'ROI':>7} {'95%CI':>18} {'p':>7} {'BH':>5}")
    for _, r in res.iterrows():
        print(f"{r['rule']:>30} {int(r['n']):>7,} {r['n_per_year']:>7,.0f} {r['hit']:>8.4f} "
              f"{r['roi']:>7.3f} {f'[{r.ci_lo:.3f}, {r.ci_hi:.3f}]':>18} {r['p']:>7.3f} "
              f"{'○' if r['bh_pass'] else '×':>5}")
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--out-json", default=None, help="凍結する候補条件の書き出し先")
    args = p.parse_args()

    df = add_derived(load(args.csv))
    disc, _hold = split(df)
    print(f"DISCOVERY のみ使用: {len(disc):,}行 / {disc['race_id'].nunique():,}レース")

    dark = disc[disc["win_odds"] >= DARKHORSE_MIN_ODDS].copy()
    null_roi = float(dark["payout"].mean())

    ok = step1(dark)
    if not ok:
        print("\nSTEP 1 が不通過のため STEP 2 は実施しない（探索しても偽陽性しか出ない）。")
        return

    res = step2(dark, null_roi)
    if args.out_json and not res.empty:
        cand = res[res["bh_pass"]].to_dict("records")
        with open(args.out_json, "w") as f:
            json.dump({"null_roi": null_roi, "candidates": cand}, f, ensure_ascii=False, indent=2)
        print(f"\n候補を凍結: {args.out_json} ({len(cand)} 件)")


if __name__ == "__main__":
    main()
