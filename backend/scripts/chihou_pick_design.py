"""「1レースに1〜2頭ピックアップ・入着可能な時のみ推奨」の閾値を設計する。

## 位置づけ

`chihou_popgap_place.py` は**馬単位**で条件の的中率を見る。本スクリプトは
**レース単位**で見る。目的が「レースごとに 1〜2 頭出す / 出せないなら棄権する」
だからで、馬単位の的中率だけでは

  - 1 レースに何頭出てしまうのか
  - 何割のレースで推奨が出るのか（＝棄権率）
  - 棄権したレースで実際に何が起きていたか

が分からない。

## 前提と制約

- 指数は **市場を見ない walk-forward 指数**を使うこと
  （`chihou_darkhorse_wf_build.py --no-market`）。
  市場特徴を入れた指数は「発走5分前は人気薄だが締切までに買われた馬」を
  拾ってしまい look-ahead になる（台帳 10.3 節で実証済み）。
- 人気順位・シェアは **発走 N 分前のスナップショット**から作る。
  `chihou.odds_history` は **2026-04-07 以降しか無い**ため、
  この設計に使える窓は構造的にそこから先だけ。
- 複勝圏は 8頭以上なら3着、7頭以下なら2着まで（`popgap` の `slots` と同じ）。

## 使い方

    cd backend
    .venv/bin/python scripts/chihou_pick_design.py --wf /path/to/wf_nomarket.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.chihou_popgap_place import DISCOVERY, HOLDOUT, load  # noqa: E402

RNG = np.random.default_rng(0)
N_BOOT = 2000
MIN_N = 30  # これ未満は数字を出さない（多重比較で拾ってしまうため）


def _ci_mean(v: np.ndarray) -> tuple[float, float, float]:
    if len(v) == 0:
        return 0.0, 0.0, 0.0
    b = RNG.choice(v, size=(N_BOOT, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), *np.percentile(b, [2.5, 97.5])


def evaluate_rule(
    df: pd.DataFrame,
    *,
    min_pop: int,
    max_idx_rank: int,
    max_share: float | None,
    min_head: int,
    max_picks: int,
) -> dict:
    """1つの推奨ルールをレース単位で評価する。

    ルール: 発走前 min_pop 番人気以下 ∧ 指数 max_idx_rank 位以内
            ∧ (シェア < max_share) ∧ 頭数 >= min_head
            を満たす馬を、指数の良い順に最大 max_picks 頭まで採る。
    """
    c = df[
        (df["pop_rank"] >= min_pop)
        & (df["idx_rank"] <= max_idx_rank)
        & (df["head_count"] >= min_head)
    ]
    if max_share is not None:
        c = c[c["top3_share"] < max_share]
    # 指数の良い順に max_picks 頭まで
    c = c.sort_values(["race_id", "idx_rank"]).groupby("race_id").head(max_picks)

    n_races_total = df["race_id"].nunique()
    n_races_picked = c["race_id"].nunique()
    hit = c["in_place"].values.astype(float)
    roi, lo, hi = _ci_mean(c["place_ret"].values) if len(c) else (0.0, 0.0, 0.0)

    # レース単位: 推奨のうち1頭でも複勝圏に入ったレースの割合
    per_race = c.groupby("race_id")["in_place"].max()

    # 倍率の基準は「同じ人気帯の母集団」でなければならない。
    # 6番人気以下の基準率で 4番人気以下のルールを割ると、人気帯が違うぶんだけ
    # 倍率が水増しされる（4番人気以下は素の複勝圏率が高い）。
    pool = df[(df["pop_rank"] >= min_pop) & (df["head_count"] >= min_head)]
    base_hit = pool["in_place"].mean() if len(pool) else 0.0

    return {
        "base_hit": base_hit,
        "n_picks": len(c),
        "n_races": n_races_picked,
        "coverage": n_races_picked / n_races_total if n_races_total else 0.0,
        "picks_per_race": len(c) / n_races_picked if n_races_picked else 0.0,
        "hit_rate": hit.mean() if len(hit) else 0.0,
        "race_hit_rate": per_race.mean() if len(per_race) else 0.0,
        "roi": roi,
        "roi_lo": lo,
        "roi_hi": hi,
    }


def abstention_audit(df: pd.DataFrame, picked_race_ids: set[int]) -> dict:
    """棄権したレースで実際に何が起きていたかを見る。

    「入着可能な時のみ推奨」が正しく効いているなら、棄権レースは
    人気薄が来にくい（＝推奨しなくて正解）はず。
    """
    pop6 = df[df["pop_rank"] >= 6]
    by_race = pop6.groupby("race_id")["in_place"].max()
    picked = by_race[by_race.index.isin(picked_race_ids)]
    skipped = by_race[~by_race.index.isin(picked_race_ids)]
    return {
        "picked_races": len(picked),
        "picked_upset_rate": picked.mean() if len(picked) else 0.0,
        "skipped_races": len(skipped),
        "skipped_upset_rate": skipped.mean() if len(skipped) else 0.0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wf", required=True, help="--no-market で作った walk-forward CSV")
    p.add_argument("--stage", choices=["discovery", "holdout"], default="discovery")
    p.add_argument("--lead", type=int, default=5)
    args = p.parse_args()

    start, end = DISCOVERY if args.stage == "discovery" else HOLDOUT
    df = load(start, end, args.lead, args.wf)
    n_races = df["race_id"].nunique()

    pop6 = df[df["pop_rank"] >= 6]
    base = pop6["in_place"].mean()
    any_upset = pop6.groupby("race_id")["in_place"].max().mean()

    print("=" * 104)
    print(f"  レース単位の推奨設計  {args.stage}: {start}〜{end}（発走{args.lead}分前）")
    print("=" * 104)
    print(f"  {len(df):,}行 / {n_races:,}レース")
    print(f"  母集団(6番人気以下)の複勝圏率 = {base:.4f}")
    print(f"  6番人気以下が1頭でも複勝圏に入るレース = {any_upset*100:.1f}%")
    print()
    print("  ※ 指数は市場を見ないもの（--no-market）を使うこと。")
    print("     市場入りの指数は締切までに買われた馬を拾う look-ahead になる。")

    header = (
        f"  {'ルール':<44} {'推奨':>5} {'レース':>6} {'網羅':>6} "
        f"{'頭/R':>5} {'複勝率':>7} {'倍率':>5} {'R的中':>7} {'複勝ROI':>8}"
    )
    print("\n" + "=" * 104)
    print("  [1] 指数順位と採用頭数のトレードオフ（6番人気以下・8頭以上・シェア<0.63）")
    print("=" * 104)
    print(header)
    for idx_rank in (2, 3, 4, 5, 6):
        for picks in (1, 2):
            r = evaluate_rule(
                df, min_pop=6, max_idx_rank=idx_rank, max_share=0.63,
                min_head=8, max_picks=picks,
            )
            if r["n_picks"] < MIN_N:
                continue
            label = f"指数{idx_rank}位内 × 最大{picks}頭"
            print(
                f"  {label:<44} {r['n_picks']:>5} {r['n_races']:>6} "
                f"{r['coverage']*100:>5.1f}% {r['picks_per_race']:>5.2f} "
                f"{r['hit_rate']:>7.3f} {r['hit_rate']/r['base_hit']:>5.2f} "
                f"{r['race_hit_rate']:>6.3f} {r['roi']:>8.3f}"
            )

    print("\n" + "=" * 104)
    print("  [2] シェア条件を緩めた場合（網羅率を上げにいく）")
    print("=" * 104)
    print(header)
    for share in (0.63, 0.70, 0.75, None):
        for idx_rank in (3, 5):
            r = evaluate_rule(
                df, min_pop=6, max_idx_rank=idx_rank, max_share=share,
                min_head=8, max_picks=2,
            )
            if r["n_picks"] < MIN_N:
                continue
            s = "なし" if share is None else f"<{share}"
            label = f"シェア{s} × 指数{idx_rank}位内 × 最大2頭"
            print(
                f"  {label:<44} {r['n_picks']:>5} {r['n_races']:>6} "
                f"{r['coverage']*100:>5.1f}% {r['picks_per_race']:>5.2f} "
                f"{r['hit_rate']:>7.3f} {r['hit_rate']/r['base_hit']:>5.2f} "
                f"{r['race_hit_rate']:>6.3f} {r['roi']:>8.3f}"
            )

    print("\n" + "=" * 104)
    print("  [3] 「人気薄」の線引きを変えた場合")
    print("=" * 104)
    print(header)
    for min_pop in (4, 5, 6, 7):
        r = evaluate_rule(
            df, min_pop=min_pop, max_idx_rank=3, max_share=0.63,
            min_head=8, max_picks=2,
        )
        if r["n_picks"] < MIN_N:
            continue
        label = f"{min_pop}番人気以下 × 指数3位内 × 最大2頭"
        print(
            f"  {label:<44} {r['n_picks']:>5} {r['n_races']:>6} "
            f"{r['coverage']*100:>5.1f}% {r['picks_per_race']:>5.2f} "
            f"{r['hit_rate']:>7.3f} {r['hit_rate']/r['base_hit']:>5.2f} "
            f"{r['race_hit_rate']:>6.3f} {r['roi']:>8.3f}"
        )

    print("\n" + "=" * 104)
    print("  [4] 棄権の妥当性（推奨を出したレース vs 出さなかったレース）")
    print("=" * 104)
    print("  「入着可能な時のみ推奨」が効いていれば、棄権レースは人気薄が来にくいはず。")
    for idx_rank, share in ((3, 0.63), (5, 0.70), (5, None)):
        c = df[
            (df["pop_rank"] >= 6) & (df["idx_rank"] <= idx_rank) & (df["head_count"] >= 8)
        ]
        if share is not None:
            c = c[c["top3_share"] < share]
        a = abstention_audit(df, set(c["race_id"].unique()))
        s = "なし" if share is None else f"<{share}"
        print(
            f"  指数{idx_rank}位内・シェア{s:<6} → "
            f"推奨あり {a['picked_races']:>4}R (人気薄が来た率 {a['picked_upset_rate']*100:>5.1f}%) / "
            f"棄権 {a['skipped_races']:>4}R ({a['skipped_upset_rate']*100:>5.1f}%)"
        )
    print()
    print("  ※ 棄権側の率が推奨側より低ければ「出さない判断」が当たっている。")
    print("     逆なら棄権ルールが取りこぼしている。")

    print("\n" + "=" * 104)
    print("  注意")
    print("=" * 104)
    print("  - odds_history が 2026-04-07 以降しか無いため、この設計に使える窓は約4か月しかない")
    print("  - HOLDOUT(2026-07) は先行検証で開封済み。ここで選んだ条件の確認には使えない")
    print("  - 確定は前向き（2026-08 以降）に記録を取ってから")


if __name__ == "__main__":
    main()
