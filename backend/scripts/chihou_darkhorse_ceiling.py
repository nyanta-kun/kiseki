"""地方競馬 穴馬推奨で「到達可能な ROI の上限」を DISCOVERY 期間で見極める。

STEP 2 の事前登録空間では最良でも ROI 0.81 だった。
これが「仮説空間の切り方が悪かった」のか「構造的な天井」なのかを分ける。

見るもの:
  A. オッズ帯そのものの素の ROI（＝何も予想しないときの水準）
  B. 帯の中でモデル勝率上位を取ったときに ROI が素の水準を超えるか
     （超えないなら、モデルは的中率を上げるがオッズを同率以上に下げており、
       穴馬の妙味を作れていない）
  C. 後知恵で最良の条件を選んだ場合の上限（＝どれだけ都合よく選んでも届かない、を示す）
  D. 複勝（place）でも同じことが言えるか

DISCOVERY 期間のみ使用。
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

from scripts.chihou_darkhorse_discovery import add_derived  # noqa: E402
from scripts.chihou_darkhorse_power import load, split  # noqa: E402

RNG = np.random.default_rng(0)
N_BOOT = 4000


def _ci(vals: np.ndarray) -> tuple[float, float, float]:
    if len(vals) == 0:
        return 0.0, 0.0, 0.0
    boot = RNG.choice(vals, size=(N_BOOT, len(vals)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(vals.mean()), float(lo), float(hi)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = add_derived(load(args.csv))
    disc, _ = split(df)
    disc = disc.copy()
    disc["place_hit"] = (disc["finish_position"] <= 3).astype(int)
    # 複勝は7頭以下だと2着までしか払い戻されない（NAR/JRA共通）
    disc.loc[disc["head_count"] < 8, "place_hit"] = (
        disc.loc[disc["head_count"] < 8, "finish_position"] <= 2
    ).astype(int)
    disc["place_payout"] = disc["place_hit"] * disc["place_odds"].fillna(0.0)

    bands = [("10-15", 10, 15), ("15-20", 15, 20), ("20-30", 20, 30),
             ("30-50", 30, 50), ("10-20", 10, 20), ("10-30", 10, 30)]

    print(f"\n{'=' * 104}")
    print("  [A/B] オッズ帯の素の ROI vs 帯の中でモデル上位を取ったときの ROI（DISCOVERY）")
    print(f"{'=' * 104}")
    print(f"{'帯':>8} {'素のn':>8} {'素のROI':>9} {'素の95%CI':>18} │ "
          f"{'モデル上位20%':>14} {'n':>7} {'ROI':>7} {'95%CI':>18} {'差':>8}")
    for lab, lo, hi in bands:
        b = disc[(disc["win_odds"] >= lo) & (disc["win_odds"] < hi)]
        if len(b) < 200:
            continue
        r0, l0, h0 = _ci(b["payout"].values)
        thr = b["p_norm"].quantile(0.80)
        t = b[b["p_norm"] >= thr]
        r1, l1, h1 = _ci(t["payout"].values)
        print(f"{lab:>8} {len(b):>8,} {r0:>9.3f} {f'[{l0:.3f}, {h0:.3f}]':>18} │ "
              f"{'p_norm上位20%':>14} {len(t):>7,} {r1:>7.3f} {f'[{l1:.3f}, {h1:.3f}]':>18} "
              f"{r1 - r0:>+8.3f}")

    print(f"\n{'=' * 104}")
    print("  [B2] 帯内モデル勝率の五分位別 ROI（モデルが帯の中で妙味を作れているか）")
    print(f"{'=' * 104}")
    for lab, lo, hi in [("10-15", 10, 15), ("15-20", 15, 20), ("10-30", 10, 30)]:
        b = disc[(disc["win_odds"] >= lo) & (disc["win_odds"] < hi)].copy()
        b["q"] = pd.qcut(b["p_norm"], 5, labels=False, duplicates="drop")
        cells = []
        for q, g in b.groupby("q"):
            r, l, h = _ci(g["payout"].values)
            cells.append(f"Q{int(q) + 1}: {r:.3f}[{l:.2f},{h:.2f}] n={len(g):,}")
        print(f"  {lab:>6} │ " + "  ".join(cells))

    print(f"\n{'=' * 104}")
    print("  [C] 後知恵の上限: DISCOVERY で最も ROI が高くなる『場 × 帯』を選んだ場合")
    print(f"{'=' * 104}")
    d = disc[disc["win_odds"] >= 10].copy()
    d["band"] = pd.cut(d["win_odds"], bins=[10, 15, 20, 30, 50, 10**6],
                       labels=["10-15", "15-20", "20-30", "30-50", "50+"])
    rows = []
    for (course, band), g in d.groupby(["course_name", "band"], observed=True):
        if len(g) < 300:
            continue
        r, l, h = _ci(g["payout"].values)
        rows.append({"course": course, "band": str(band), "n": len(g),
                     "roi": r, "lo": l, "hi": h})
    tbl = pd.DataFrame(rows).sort_values("roi", ascending=False)
    print(f"  n>=300 のセル {len(tbl)} 個のうち上位10 / 下位3\n")
    print(f"{'場':>8} {'帯':>8} {'n':>8} {'ROI':>7} {'95%CI':>18} {'CI下限>1.0':>12}")
    for _, r in pd.concat([tbl.head(10), tbl.tail(3)]).iterrows():
        flag = "○" if r["lo"] > 1.0 else "×"
        print(f"{r['course']:>8} {r['band']:>8} {int(r['n']):>8,} {r['roi']:>7.3f} "
              f"{f'[{r.lo:.3f}, {r.hi:.3f}]':>18} {flag:>12}")
    n_over1 = int((tbl["roi"] > 1.0).sum())
    n_ci_over1 = int((tbl["lo"] > 1.0).sum())
    print(f"\n  → ROI>1.0 のセル: {n_over1}/{len(tbl)}   "
          f"うち 95%CI 下限まで >1.0: {n_ci_over1}/{len(tbl)}")

    print(f"\n{'=' * 104}")
    print("  [D] 複勝（place）での同じ検査")
    print(f"{'=' * 104}")
    pv = disc[disc["place_odds"].notna() & (disc["place_odds"] > 0)]
    print(f"  複勝オッズ取得済み: {len(pv):,}行 / 全体 {len(disc):,}行 "
          f"({len(pv) / len(disc) * 100:.1f}%)\n")
    print(f"{'帯':>8} {'n':>8} {'複勝的中率':>10} {'複勝ROI':>9} {'95%CI':>18}")
    for lab, lo, hi in bands:
        b = pv[(pv["win_odds"] >= lo) & (pv["win_odds"] < hi)]
        if len(b) < 200:
            continue
        r, l, h = _ci(b["place_payout"].values)
        print(f"{lab:>8} {len(b):>8,} {b['place_hit'].mean():>10.4f} {r:>9.3f} "
              f"{f'[{l:.3f}, {h:.3f}]':>18}")
    # モデル上位に絞った複勝
    print()
    for lab, lo, hi in [("10-15", 10, 15), ("10-20", 10, 20), ("10-30", 10, 30)]:
        b = pv[(pv["win_odds"] >= lo) & (pv["win_odds"] < hi)]
        if len(b) < 200:
            continue
        for rank_max in (2, 3, 5):
            t = b[b["idx_rank_wf"] <= rank_max]
            if len(t) < 100:
                continue
            r, l, h = _ci(t["place_payout"].values)
            print(f"  {lab:>6} & idx<={rank_max}  n={len(t):>6,}  複勝ROI={r:.3f}  "
                  f"95%CI [{l:.3f}, {h:.3f}]")


if __name__ == "__main__":
    main()
