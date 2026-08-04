"""市場（オッズ）を見ないモデルを walk-forward 学習し、穴馬の妙味を再検証する。

## なぜやるか

`chihou_darkhorse_discovery.py` STEP 1 で、モデル EV（勝率×オッズ）の十分位に
妙味が全く出なかった（ρ=−0.59）。しかし本番モデルの `ALL_FEATURES` には
**市場特徴 5 本（odds_rank_n / speed_mkt_gap / kc_mkt_gap / is_heavy_fav /
is_dark_horse）が含まれている**。つまりモデルはオッズを見て確率を出しており、
その確率にオッズを掛けた EV は「市場 × 市場」の同語反復になりうる。

妙味を測るには **市場から独立した確率推定** が要る。
`PROD_FEATURES`（39本・オッズ非依存）だけで学習しなおし、
その確率と市場を突き合わせる。これが古典的な value 検出の形。

これが空振りなら「モデルは市場に無い情報を持たない」と強く言える。

## 出力

  1. 市場非依存モデルの素の性能（指数1位勝率。市場ありに劣るのは想定内）
  2. EV_nomkt = p_nomkt × オッズ の十分位別 ROI（穴馬帯）
  3. 市場との乖離（p_nomkt / p_mkt）の十分位別 ROI
  4. 上記が有望なら、事前登録した閾値での ROI と 95%CI

DISCOVERY 期間のみで判断する。HOLDOUT は開かない。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_nomarket.py --out /path/to/nomkt.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.chihou_darkhorse_wf_build import QUARTERS  # noqa: E402
from scripts.chihou_rebuild_walkforward import (  # noqa: E402
    FULL_POP_QUERY,
    SEED,
    TRAIN_DATA_START,
    TRAIN_QUERY,
    _featurize_full,
    _fetch,
)
from scripts.train_chihou_market_lgb import (  # noqa: E402
    CHIHOU_V9_VERSION,
    PROD_FEATURES,
    build_ct_tables,
    compute_wet_apt_table,
    fetch_hist,
    fetch_hist_cond,
    train_binary_control,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_nomarket")

RNG = np.random.default_rng(0)
N_BOOT = 4000
DISCOVERY_END = "20250930"


def build(out_csv: str) -> pd.DataFrame:
    """市場特徴を除いた walk-forward 予測を生成する。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    logger.info("補助テーブル読み込み中...")
    df_hist = fetch_hist(conn)
    apt_tbl = compute_wet_apt_table(fetch_hist_cond(conn))
    ct_tables = build_ct_tables(conn)

    frames: list[pd.DataFrame] = []
    for train_end, test_start, test_end in QUARTERS:
        logger.info("=== train_end=%s test=%s〜%s ===", train_end, test_start, test_end)
        raw_tr = _fetch(conn, TRAIN_QUERY,
                        {"ver": CHIHOU_V9_VERSION, "start": TRAIN_DATA_START, "end": train_end})
        if raw_tr["race_id"].nunique() < 200:
            continue
        tr = _featurize_full(raw_tr, df_hist, apt_tbl, ct_tables).sort_values("race_id")
        fp = pd.to_numeric(tr["finish_position"], errors="coerce")
        X = tr[PROD_FEATURES].fillna(0.0).values.astype(np.float64)
        m_win = train_binary_control(X, (fp == 1).astype(int).values, SEED,
                                     feature_names=PROD_FEATURES)
        m_top3 = train_binary_control(X, (fp <= 3).astype(int).values, SEED,
                                      feature_names=PROD_FEATURES)

        raw_te = _fetch(conn, FULL_POP_QUERY,
                        {"ver": CHIHOU_V9_VERSION, "start": test_start, "end": test_end})
        if raw_te.empty:
            continue
        te = _featurize_full(raw_te, df_hist, apt_tbl, ct_tables).copy()
        Xt = te[PROD_FEATURES].fillna(0.0).values.astype(np.float64)
        te["p_win_nomkt"] = m_win.predict(Xt)
        te["p_top3_nomkt"] = m_top3.predict(Xt)
        te["rank_nomkt"] = (
            te.groupby("race_id")["p_top3_nomkt"].rank(method="first", ascending=False).astype(int)
        )
        te["quarter"] = f"{test_start}-{test_end}"
        keep = ["race_id", "date", "quarter", "course_name", "horse_id", "horse_number",
                "head_count", "win_odds", "place_odds", "win_popularity", "finish_position",
                "p_win_nomkt", "p_top3_nomkt", "rank_nomkt"]
        settled = te[
            te["finish_position"].notna() & (te["abnormality_code"] == 0)
            & te["win_odds"].notna() & (te["win_odds"] >= 1.0)
        ]
        frames.append(settled[[c for c in keep if c in settled.columns]])

    conn.close()
    full = pd.concat(frames, ignore_index=True)
    full.to_csv(out_csv, index=False)
    logger.info("保存: %s (%d行 / %dレース)", out_csv, len(full), full["race_id"].nunique())
    return full


def _ci(vals: np.ndarray) -> tuple[float, float, float]:
    if len(vals) == 0:
        return 0.0, 0.0, 0.0
    boot = RNG.choice(vals, size=(N_BOOT, len(vals)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(vals.mean()), float(lo), float(hi)


def analyze(full: pd.DataFrame) -> None:
    full = full.copy()
    full["date"] = full["date"].astype(str)
    full["hit"] = (full["finish_position"] == 1).astype(int)
    full["payout"] = full["hit"] * full["win_odds"]
    d = full[full["date"] <= DISCOVERY_END].copy()
    print(f"\nDISCOVERY のみ使用: {len(d):,}行 / {d['race_id'].nunique():,}レース")

    # レース内正規化
    s = d.groupby("race_id")["p_win_nomkt"].transform("sum")
    d["p_norm"] = d["p_win_nomkt"] / s.where(s > 0, np.nan)
    inv = 1.0 / d["win_odds"]
    d["p_mkt"] = inv / inv.groupby(d["race_id"]).transform("sum")
    d["ev"] = d["p_norm"] * d["win_odds"]
    d["edge"] = d["p_norm"] / d["p_mkt"]

    print(f"\n{'=' * 96}")
    print("  [1] 市場非依存モデルの素の性能（DISCOVERY・全馬）")
    print(f"{'=' * 96}")
    t1 = d[d["rank_nomkt"] == 1]
    print(f"  指数1位馬 勝率={(t1['finish_position'] == 1).mean():.4f}  "
          f"複勝率={(t1['finish_position'] <= 3).mean():.4f}  n={len(t1):,}")
    print("  （市場ありモデルは勝率 0.42〜0.47。市場を外した分の低下は想定内）")

    dark = d[d["win_odds"] >= 10].copy()
    base = dark["payout"].mean()
    print(f"\n{'=' * 96}")
    print(f"  [2] 穴馬帯（≥10倍, n={len(dark):,}, ベースROI={base:.3f}）の十分位別 ROI")
    print(f"{'=' * 96}")
    from scipy.stats import spearmanr
    for col, label in [("ev", "EV_nomkt = 市場非依存勝率 × オッズ"),
                       ("edge", "乖離 = 市場非依存勝率 / 市場含意確率"),
                       ("p_norm", "市場非依存勝率そのもの")]:
        dark["dec"] = pd.qcut(dark[col], 10, labels=False, duplicates="drop")
        print(f"\n  ── {label} ──")
        print(f"{'decile':>8} {'n':>8} {'的中率':>8} {'ROI':>8} {'95%CI':>18} {'平均オッズ':>10}")
        rois = []
        for dec, g in dark.groupby("dec"):
            r, lo, hi = _ci(g["payout"].values)
            rois.append(r)
            print(f"{int(dec) + 1:>8} {len(g):>8,} {g['hit'].mean():>8.4f} {r:>8.3f} "
                  f"{f'[{lo:.3f}, {hi:.3f}]':>18} {g['win_odds'].mean():>10.1f}")
        top = dark[dark["dec"] == dark["dec"].max()]["payout"].values
        bot = dark[dark["dec"] == dark["dec"].min()]["payout"].values
        bt = RNG.choice(top, size=(N_BOOT, len(top)), replace=True).mean(axis=1)
        bb = RNG.choice(bot, size=(N_BOOT, len(bot)), replace=True).mean(axis=1)
        dlo, dhi = np.percentile(bt - bb, [2.5, 97.5])
        rho = spearmanr(range(len(rois)), rois).statistic
        print(f"  → 最上位-最下位 = {top.mean() - bot.mean():+.3f}  95%CI [{dlo:+.3f}, {dhi:+.3f}]  "
              f"ρ={rho:+.2f}  最高十分位ROI={max(rois):.3f}  "
              f"{'有意' if dlo > 0 else '有意でない'}")

    print(f"\n{'=' * 96}")
    print("  [3] 事前登録した閾値（EV_nomkt ゲート × オッズ帯）")
    print(f"{'=' * 96}")
    print(f"{'条件':>34} {'n':>8} {'年間':>7} {'的中率':>8} {'ROI':>7} {'95%CI':>18} {'CI下限>1':>9}")
    for o_lab, o_lo, o_hi in [("10-15", 10, 15), ("10-20", 10, 20), ("10-30", 10, 30),
                              ("15-30", 15, 30), ("10-99", 10, 10**6)]:
        for ev_min in (1.0, 1.2, 1.5, 2.0):
            sub = dark[(dark["win_odds"] >= o_lo) & (dark["win_odds"] < o_hi)
                       & (dark["ev"] >= ev_min)]
            if len(sub) < 100:
                continue
            r, lo, hi = _ci(sub["payout"].values)
            print(f"{f'{o_lab} & EV_nomkt>={ev_min}':>34} {len(sub):>8,} {len(sub) / 1.25:>7,.0f} "
                  f"{sub['hit'].mean():>8.4f} {r:>7.3f} {f'[{lo:.3f}, {hi:.3f}]':>18} "
                  f"{'○' if lo > 1.0 else '×':>9}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--reuse", action="store_true", help="既存 CSV を再利用して解析だけ行う")
    args = p.parse_args()
    if args.reuse and Path(args.out).exists():
        full = pd.read_csv(args.out)
        logger.info("再利用: %s (%d行)", args.out, len(full))
    else:
        full = build(args.out)
    analyze(full)


if __name__ == "__main__":
    main()
