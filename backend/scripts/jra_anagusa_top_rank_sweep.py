"""JRA 穴ぐさ×指数上位×高オッズ シグナルのサンプル拡張検証

[[jra_anagusa_roi_segment]]で見つけた「穴ぐさ(A/B/C) ∧ composite_rank<=3 ∧
単勝オッズ>=10」の単勝ROI ~1.4(train/testとも)について、サンプルを増やせるか
検証する。sekito.anagusaは2024-01-06以降しかデータがない(3年でなく実質2.5年)
ため日付範囲の拡張はできない。代わりにcomposite_rank閾値・オッズ閾値を
隣接候補で振り、サンプルサイズとROIの安定性のトレードオフを確認する。

使い方:
  cd backend
  .venv/bin/python scripts/jra_anagusa_top_rank_sweep.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

sys.path.insert(0, str(_here.parent))
from jra_axis_segment_deny_analysis import fetch_base  # noqa: E402
from jra_verify_signals import annotate, fetch_external  # noqa: E402

ANAGUSA_START = "20240106"
TRAIN_END = "20250630"
TEST_START = "20250701"
END = "20260726"


def roi_ci(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 5000) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "win_roi": 0.0, "plc_roi": 0.0,
                "wlo": 0.0, "whi": 0.0, "plo": 0.0, "phi": 0.0, "drop1": 0.0}
    fp = sub["finish_position"].to_numpy()
    win_odds = sub["win_odds"].to_numpy()
    plc_odds = sub["place_odds"].to_numpy()
    win_payout = np.where(fp == 1, win_odds, 0.0)
    plc_payout = np.where(fp <= 3, np.nan_to_num(plc_odds, nan=0.0), 0.0)
    win_roi = win_payout.sum() / n
    plc_roi = plc_payout.sum() / n
    wb = [rng.choice(win_payout, size=n, replace=True).mean() for _ in range(n_boot)]
    pb = [rng.choice(plc_payout, size=n, replace=True).mean() for _ in range(n_boot)]
    wlo, whi = np.percentile(wb, [2.5, 97.5])
    plo, phi = np.percentile(pb, [2.5, 97.5])
    drop1 = (win_payout.sum() - win_payout.max()) / max(n - 1, 1) if win_payout.max() > 0 else win_roi
    return {"n": n, "win": (fp == 1).mean() * 100, "plc": (fp <= 3).mean() * 100,
            "win_roi": win_roi, "plc_roi": plc_roi, "wlo": wlo, "whi": whi,
            "plo": plo, "phi": phi, "drop1": drop1}


def main() -> None:
    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch_base(conn, ANAGUSA_START, END)
    ext = fetch_external(conn, ANAGUSA_START, END)
    conn.close()
    df = annotate(df, ext)
    df["date"] = df["date"].astype(str)

    ag = df[df["anagusa_rank"].isin(["A", "B", "C"])].copy()
    print(f"穴ぐさ全体: n={len(ag)} (期間 {ANAGUSA_START}〜{END}, anagusaデータ開始日以降)")

    print("\n" + "#" * 100)
    print("# composite_rank閾値 × オッズ閾値 スイープ (単勝ROI, train+val/test)")
    print("# ★=95%CI下限>1 / ◯=点推定>1のみ")
    print("#" * 100)

    for rank_max in (2, 3, 4, 5, 6):
        for odds_min in (8.0, 10.0, 15.0):
            cond = ag[(ag["composite_rank"] <= rank_max) & (ag["win_odds"] >= odds_min)]
            train = cond[cond["date"] < TRAIN_END]
            test = cond[cond["date"] >= TEST_START]
            if len(train) < 30 or len(test) < 30:
                continue
            st_tr = roi_ci(train, rng, n_boot=1000)
            st_te = roi_ci(test, rng, n_boot=1000)
            tr_mark = "★" if st_tr["wlo"] > 1.0 else ("◯" if st_tr["win_roi"] > 1.0 else " ")
            te_mark = "★" if st_te["wlo"] > 1.0 else ("◯" if st_te["win_roi"] > 1.0 else " ")
            both_positive = st_tr["win_roi"] > 1.0 and st_te["win_roi"] > 1.0
            flag = " <== 両窓>1" if both_positive else ""
            print(f"  rank<={rank_max} odds>={odds_min:<4.0f} | "
                  f"train n={st_tr['n']:>4} ROI={st_tr['win_roi']:.3f}{tr_mark} | "
                  f"test n={st_te['n']:>4} ROI={st_te['win_roi']:.3f}{te_mark}{flag}")

    print("\n" + "#" * 100)
    print("# 確定候補の全期間(FULL)再検証（train+val選定条件をtestに適用した後の最終値）")
    print("#" * 100)
    for rank_max, odds_min in [(3, 10.0), (4, 10.0), (3, 8.0)]:
        cond = ag[(ag["composite_rank"] <= rank_max) & (ag["win_odds"] >= odds_min)]
        st = roi_ci(cond, rng, n_boot=5000)
        mark = "★" if st["wlo"] > 1.0 else ("◯" if st["win_roi"] > 1.0 else " ")
        print(f"  rank<={rank_max} odds>={odds_min:.0f}: n={st['n']} 単勝的中={st['win']:.1f}% "
              f"単ROI={st['win_roi']:.3f}{mark} drop1={st['drop1']:.3f} "
              f"CI[{st['wlo']:.2f},{st['whi']:.2f}] 複ROI={st['plc_roi']:.3f} CI[{st['plo']:.2f},{st['phi']:.2f}]")


if __name__ == "__main__":
    main()
