"""地方競馬 穴馬推奨条件の HOLDOUT 一度きり確認。

DISCOVERY(2024-07〜2025-09) で凍結した条件だけを、
HOLDOUT(2025-10〜2026-07) で **1 回だけ** 評価する。

凍結リスト（このファイルの ARMS が唯一の定義。実行前に確定させたもの）:
  1. オッズ帯の素の ROI       — 「予想しない」ときの水準。天井の再現性確認
  2. STEP 2 で BH-FDR を通った条件
  3. DISCOVERY の後知恵最良セル（笠松 10-15）— 最も有利に選んだ場合の再現性
  4. 逆張り仮説（帯内でモデル勝率が最も低い層）— DISCOVERY で唯一 1.0 に触れた層
  5. 複勝（参考・探索なし）  — 複勝オッズが 2026 年以降にしか無く、
     DISCOVERY 側に対照が作れない。**採否判断には使えない**ため参考値として出す

注意: 本スクリプトが触るのは 2026-07-31 まで。
`chihou_protocol.TEST_START`（当月＝2026-08）には手を付けないため、
プロジェクト正規プロトコルの TEST 期間は未消費のまま残る。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_holdout.py --csv /path/to/wf.csv
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

# DISCOVERY 側で観測した ROI（再現性を見るため併記する）
DISCOVERY_ROI = {
    "帯 10-15（素）": 0.817, "帯 15-20（素）": 0.810, "帯 20-30（素）": 0.742,
    "帯 10-20（素）": 0.814, "帯 10-30（素）": 0.787,
    "10-15 & idx<=5 & EV>=0": 0.807, "10-20 & idx<=5 & EV>=0": 0.789,
    "10-15 & idx<=5 & EV>=0.8": 0.769, "10-30 & idx<=5 & EV>=0": 0.757,
    "10-20 & idx<=5 & EV>=0.8": 0.745, "10-30 & idx<=5 & EV>=0.8": 0.711,
    "笠松 10-15（後知恵最良セル）": 0.988,
    "15-20 & モデル勝率下位20%（逆張り）": 0.994,
    "10-15 & モデル勝率下位20%（逆張り）": 0.864,
}


def _ci(vals: np.ndarray) -> tuple[float, float, float]:
    if len(vals) == 0:
        return 0.0, 0.0, 0.0
    boot = RNG.choice(vals, size=(N_BOOT, len(vals)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(vals.mean()), float(lo), float(hi)


def build_arms(h: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    """凍結した条件をマスクとして構築する。"""
    o, r, ev = h["win_odds"], h["idx_rank_wf"], h["ev"]
    arms: list[tuple[str, pd.Series]] = []

    for lab, lo, hi in [("10-15", 10, 15), ("15-20", 15, 20), ("20-30", 20, 30),
                        ("10-20", 10, 20), ("10-30", 10, 30)]:
        arms.append((f"帯 {lab}（素）", (o >= lo) & (o < hi)))

    for lab, lo, hi, ev_min in [("10-15", 10, 15, 0.0), ("10-20", 10, 20, 0.0),
                                ("10-15", 10, 15, 0.8), ("10-30", 10, 30, 0.0),
                                ("10-20", 10, 20, 0.8), ("10-30", 10, 30, 0.8)]:
        name = f"{lab} & idx<=5 & EV>={ev_min:g}"
        arms.append((name, (o >= lo) & (o < hi) & (r <= 5) & (ev >= ev_min)))

    arms.append(("笠松 10-15（後知恵最良セル）",
                 (h["course_name"] == "笠松") & (o >= 10) & (o < 15)))

    # 逆張り: 帯内でモデル勝率が下位20%。閾値は HOLDOUT 内の分位で取る
    # （DISCOVERY の絶対値を持ち込むと分布シフトで意味が変わるため）
    for lab, lo, hi in [("15-20", 15, 20), ("10-15", 10, 15)]:
        band = (o >= lo) & (o < hi)
        thr = h.loc[band, "p_norm"].quantile(0.20)
        arms.append((f"{lab} & モデル勝率下位20%（逆張り）", band & (h["p_norm"] <= thr)))

    return arms


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = add_derived(load(args.csv))
    _disc, h = split(df)
    h = h.copy()
    print(f"HOLDOUT 一度きり評価: {len(h):,}行 / {h['race_id'].nunique():,}レース "
          f"(2025-10-01〜2026-07-31)")

    print(f"\n{'=' * 108}")
    print("  凍結条件の HOLDOUT 結果（単勝）")
    print(f"{'=' * 108}")
    print(f"{'条件':>34} {'n':>8} {'年間':>7} {'的中率':>8} {'ROI':>7} {'95%CI':>18} "
          f"{'探索期ROI':>10} {'再現':>6} {'CI下限>1':>9}")
    months = 10.0
    for name, mask in build_arms(h):
        sub = h[mask]
        if len(sub) < 30:
            print(f"{name:>34} {len(sub):>8,}  — 標本不足")
            continue
        roi, lo, hi = _ci(sub["payout"].values)
        d = DISCOVERY_ROI.get(name)
        repro = "" if d is None else ("○" if lo <= d <= hi else "×")
        dstr = "—" if d is None else f"{d:.3f}"
        print(f"{name:>34} {len(sub):>8,} {len(sub) / months * 12:>7,.0f} "
              f"{sub['hit'].mean():>8.4f} {roi:>7.3f} {f'[{lo:.3f}, {hi:.3f}]':>18} "
              f"{dstr:>10} {repro:>6} {'○' if lo > 1.0 else '×':>9}")

    # ── 参考: 複勝 ──
    # ⚠️ `place_odds IS NOT NULL` で絞ってはいけない。2026-03 以前の place_odds は
    #    HR 払戻由来で **複勝圏に入った馬にしか存在しない**（実測: 充足行の複勝率が
    #    厳密に 1.0000、欠損行が 0.0000）。素朴に絞ると複勝率・ROI が激しく上振れる。
    #    `_fill_loser_place_odds_from_history()` が効く 2026-04 以降だけが使える。
    h["place_hit"] = np.where(
        h["head_count"] < 8, h["finish_position"] <= 2, h["finish_position"] <= 3
    ).astype(int)
    h["ym"] = h["date"].str[:6]
    cov = h.groupby("ym").apply(
        lambda g: (g["place_odds"].notna() & (g["place_odds"] > 0)).mean(), include_groups=False
    )
    clean_months = sorted(cov[cov >= 0.95].index.tolist())
    print(f"\n{'=' * 108}")
    print("  [参考] 複勝 — 充足率が実質 100% の月のみ使用（充足バイアス回避）")
    print(f"{'=' * 108}")
    print("  月別 place_odds 充足率: " + "  ".join(f"{m}:{v:.2f}" for m, v in cov.items()))
    print(f"  → 採用: {', '.join(clean_months) if clean_months else 'なし'}")
    print("  ⚠️ DISCOVERY 側に複勝オッズが存在せず対照を作れないため、"
          "この表は『探索されていない』ことの保証が無い。採否判断には使えない。")
    hp = h[h["ym"].isin(clean_months)]
    pv = hp[hp["place_odds"].notna() & (hp["place_odds"] > 0)].copy()
    pv["place_payout"] = pv["place_hit"] * pv["place_odds"]
    print(f"\n  対象: {len(pv):,}行 / 当該月 {len(hp):,}行 "
          f"({len(pv) / max(len(hp), 1) * 100:.1f}%)\n")
    print(f"{'条件':>34} {'n':>8} {'複勝的中率':>10} {'複勝ROI':>9} {'95%CI':>18} {'CI下限>1':>9}")
    for lab, lo_o, hi_o in [("10-15", 10, 15), ("15-20", 15, 20), ("20-30", 20, 30),
                            ("10-20", 10, 20), ("10-30", 10, 30)]:
        b = pv[(pv["win_odds"] >= lo_o) & (pv["win_odds"] < hi_o) & (pv["head_count"] >= 8)]
        if len(b) < 100:
            continue
        roi, lo, hi = _ci(b["place_payout"].values)
        print(f"{'帯 ' + lab + '（素）':>34} {len(b):>8,} {b['place_hit'].mean():>10.4f} "
              f"{roi:>9.3f} {f'[{lo:.3f}, {hi:.3f}]':>18} {'○' if lo > 1.0 else '×':>9}")
    for lab, lo_o, hi_o in [("10-15", 10, 15), ("10-20", 10, 20), ("10-30", 10, 30)]:
        for rmax in (3, 5):
            b = pv[(pv["win_odds"] >= lo_o) & (pv["win_odds"] < hi_o)
                   & (pv["head_count"] >= 8) & (pv["idx_rank_wf"] <= rmax)]
            if len(b) < 100:
                continue
            roi, lo, hi = _ci(b["place_payout"].values)
            print(f"{f'{lab} & idx<={rmax}':>34} {len(b):>8,} {b['place_hit'].mean():>10.4f} "
                  f"{roi:>9.3f} {f'[{lo:.3f}, {hi:.3f}]':>18} {'○' if lo > 1.0 else '×':>9}")


if __name__ == "__main__":
    main()
