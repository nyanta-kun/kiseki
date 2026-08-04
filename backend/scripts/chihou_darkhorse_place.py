"""地方競馬 **複勝** の穴馬推奨条件を、レース構造を条件に入れて検証する。

## 設計を組み直した理由

前段の検証には 2 つの誤りがあった。

**① 複勝の母集団を `place_odds IS NOT NULL` で絞っていた。**
`place_odds` は複勝圏に入った馬にしか存在しない期間があるため、絞ると
「複勝率 100% の集団」を見ることになる。
正しくは **行を絞らず、払戻が無い馬は payout=0 として残す**。
ROI に必要なのは的中馬の払戻だけで、外れ馬のオッズは要らない。
これにより `chihou.race_payouts`（的中馬の払戻のみ保持）がそのまま使える。

**② 単勝を主戦場にしていた。**
複勝は的中率 ~26% / 払戻 ~2-4倍 で、1ベットの払戻 sd が単勝の 1/5 程度。
**同じ結論を出すのに必要な n が桁で少ない**。穴馬を検証するなら複勝が本筋。

## レース構造を条件に入れる

複勝圏は 3 枠（7頭以下は 2 枠）しかない。穴馬が入れるかは、
**その枠を人気馬が何頭で埋めてしまうか**に強く依存する:

  - 実力馬が複数いて人気を分け合うレース → 上位を彼らが占め、穴馬の枠が無い
  - 1頭だけ断然人気のレース → 2,3着が空き、穴馬に枠が回る

これを測る軸を用意する:
  - `fav_odds`        : 1番人気のオッズ（小さいほど断然）
  - `n_strong`        : 単勝 5 倍未満の頭数（人気を分け合う実力馬の数）
  - `mkt_top3_share`  : 市場含意確率の上位3頭合計（大きいほど上位が固い＝枠が埋まる）
  - `mkt_hhi`         : 市場含意確率のハーフィンダール指数（集中度）

頭数は **7頭以下（2枠）と 8頭以上（3枠）を必ず分けて扱う**。
7頭以下は払戻が上がる一方で的中枠が 1 つ減るため、正味の損得は実測でしか分からない。

## 期間

複勝の払戻は `chihou.race_payouts` が 2026 年しか持たない（2025 年は 0 件）。
したがって:
  DISCOVERY = 2026-01-01〜2026-04-30
  HOLDOUT   = 2026-05-01〜2026-07-31
walk-forward の vintage は元の四半期境界のまま（2026-01〜03 は 2025-12 まで、
2026-04〜06 は 2026-03 まで、2026-07 は 2026-06 までで学習）＝ honest。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_place.py --csv /path/to/wf.csv --stage discovery
  .venv/bin/python scripts/chihou_darkhorse_place.py --csv /path/to/wf.csv --stage holdout
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_place")

RNG = np.random.default_rng(0)
N_BOOT = 4000

PLACE_DISCOVERY = ("20260101", "20260430")
PLACE_HOLDOUT = ("20260501", "20260731")

PAYOUT_QUERY = """
SELECT p.race_id, p.combination, p.payout
FROM chihou.race_payouts p
JOIN chihou.races r ON r.id = p.race_id
WHERE p.bet_type = 'place' AND r.date BETWEEN %(start)s AND %(end)s
"""


def attach_place_payout(df: pd.DataFrame) -> pd.DataFrame:
    """race_payouts から複勝払戻を結合する。払戻が無い馬は 0（＝外れ）。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(PAYOUT_QUERY, {"start": df["date"].min(), "end": df["date"].max()})
    pay = pd.DataFrame(cur.fetchall(), columns=["race_id", "combination", "payout"])
    cur.close()
    conn.close()
    pay["horse_number"] = pd.to_numeric(pay["combination"], errors="coerce")
    pay = pay.dropna(subset=["horse_number"])
    pay["horse_number"] = pay["horse_number"].astype(int)
    pay["place_ret"] = pd.to_numeric(pay["payout"], errors="coerce") / 100.0
    pay = pay[["race_id", "horse_number", "place_ret"]].drop_duplicates(
        subset=["race_id", "horse_number"]
    )

    df = df.merge(pay, on=["race_id", "horse_number"], how="left")
    # 払戻レコードのあるレースだけを対象にする（払戻未取込のレースを
    # 「全馬外れ」と誤読しないため）。行は絞らない、レースを絞る。
    races_with = set(pay["race_id"].unique())
    before = df["race_id"].nunique()
    df = df[df["race_id"].isin(races_with)].copy()
    logger.info("複勝払戻あり: %d / %d レース", df["race_id"].nunique(), before)
    df["place_ret"] = df["place_ret"].fillna(0.0)
    df["place_hit"] = (df["place_ret"] > 0).astype(int)
    return df


def add_structure(df: pd.DataFrame) -> pd.DataFrame:
    """レース構造の軸（人気の集中度）を付与する。"""
    df = df.copy()
    inv = 1.0 / df["win_odds"]
    tot = inv.groupby(df["race_id"]).transform("sum")
    df["p_mkt"] = inv / tot
    g = df.groupby("race_id")
    df["fav_odds"] = g["win_odds"].transform("min")
    df["n_strong"] = g["win_odds"].transform(lambda s: (s < 5.0).sum())
    df["mkt_top3_share"] = g["p_mkt"].transform(
        lambda s: s.nlargest(min(3, len(s))).sum()
    )
    df["mkt_hhi"] = g["p_mkt"].transform(lambda s: (s ** 2).sum())
    df["slots"] = np.where(df["head_count"] >= 8, 3, 2)
    return df


def _ci(vals: np.ndarray) -> tuple[float, float, float]:
    if len(vals) == 0:
        return 0.0, 0.0, 0.0
    boot = RNG.choice(vals, size=(N_BOOT, len(vals)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(vals.mean()), float(lo), float(hi)


def _row(label: str, sub: pd.DataFrame, months: float) -> None:
    if len(sub) < 30:
        print(f"{label:>42} {len(sub):>7,}  — 標本不足")
        return
    r, lo, hi = _ci(sub["place_ret"].values)
    print(f"{label:>42} {len(sub):>7,} {len(sub) / months * 12:>7,.0f} "
          f"{sub['place_hit'].mean():>8.4f} {sub['place_ret'][sub['place_hit'] == 1].mean():>8.2f} "
          f"{r:>7.3f} {f'[{lo:.3f}, {hi:.3f}]':>18} {'○' if lo > 1.0 else '×':>8}")


HEADER = (f"{'条件':>42} {'n':>7} {'年間':>7} {'複勝率':>8} {'平均払戻':>8} "
          f"{'ROI':>7} {'95%CI':>18} {'CI下限>1':>8}")


def stage_discovery(df: pd.DataFrame, months: float) -> None:
    print(f"\n{'=' * 118}")
    print("  [0] サニティ: 全馬を機械的に複勝で買った場合（控除率の確認）")
    print(f"{'=' * 118}")
    print(HEADER)
    _row("全馬", df, months)
    _row("8頭以上（3枠）", df[df["slots"] == 3], months)
    _row("7頭以下（2枠）", df[df["slots"] == 2], months)

    print(f"\n{'=' * 118}")
    print("  [1] オッズ帯別（穴馬の定義を決める）× 枠数")
    print(f"{'=' * 118}")
    print(HEADER)
    for lab, lo, hi in [("5-10", 5, 10), ("10-15", 10, 15), ("15-20", 15, 20),
                        ("20-30", 20, 30), ("30-50", 30, 50), ("50+", 50, 10 ** 6)]:
        b = df[(df["win_odds"] >= lo) & (df["win_odds"] < hi)]
        _row(f"単勝{lab} / 3枠", b[b["slots"] == 3], months)
        _row(f"単勝{lab} / 2枠", b[b["slots"] == 2], months)

    print(f"\n{'=' * 118}")
    print("  [2] レース構造の効き方（穴馬 単勝10-30倍・8頭以上に固定して構造だけ動かす）")
    print(f"{'=' * 118}")
    dark = df[(df["win_odds"] >= 10) & (df["win_odds"] < 30) & (df["slots"] == 3)]
    print(HEADER)
    for lab, m in [
        ("断然人気あり (fav<1.5)", dark["fav_odds"] < 1.5),
        ("準断然 (1.5<=fav<2.0)", (dark["fav_odds"] >= 1.5) & (dark["fav_odds"] < 2.0)),
        ("混戦 (fav>=2.0)", dark["fav_odds"] >= 2.0),
    ]:
        _row(lab, dark[m], months)
    print()
    for n in (1, 2, 3, 4):
        lab = f"実力馬(単勝<5)が{n}頭" if n < 4 else "実力馬(単勝<5)が4頭以上"
        m = (dark["n_strong"] == n) if n < 4 else (dark["n_strong"] >= 4)
        _row(lab, dark[m], months)
    print()
    dk = dark.copy()
    dk["share_q"] = pd.qcut(dk["mkt_top3_share"], 4, labels=False, duplicates="drop")
    for q, g in dk.groupby("share_q"):
        _row(f"市場上位3頭シェア Q{int(q) + 1} ({g['mkt_top3_share'].min():.2f}-"
             f"{g['mkt_top3_share'].max():.2f})", g, months)

    print(f"\n{'=' * 118}")
    print("  [3] モデル指数順位の効き方（同上・穴馬 10-30倍・3枠）")
    print(f"{'=' * 118}")
    print(HEADER)
    for rmax in (1, 2, 3, 5, 8):
        _row(f"指数{rmax}位以内", dark[dark["idx_rank_wf"] <= rmax], months)
    _row("指数6位以下", dark[dark["idx_rank_wf"] >= 6], months)

    print(f"\n{'=' * 118}")
    print("  [4] 事前登録した組み合わせ（構造 × 順位 × オッズ帯・3枠限定）")
    print(f"{'=' * 118}")
    print(HEADER)
    base = df[df["slots"] == 3]
    for o_lab, o_lo, o_hi in [("10-20", 10, 20), ("10-30", 10, 30), ("15-30", 15, 30)]:
        for f_lab, f_max in [("fav<1.5", 1.5), ("fav<2.0", 2.0)]:
            for rmax in (3, 5):
                sub = base[(base["win_odds"] >= o_lo) & (base["win_odds"] < o_hi)
                           & (base["fav_odds"] < f_max) & (base["idx_rank_wf"] <= rmax)]
                _row(f"{o_lab} & {f_lab} & 指数{rmax}位内", sub, months)

    # ── [5] 3軸が指す方向を合成する ──
    # [2][3] はいずれも「開いたレース(混戦・上位シェア低)」「モデルが低く見る馬」を指した。
    # 既存 place_bet の前提（断然人気レース × 指数上位）とは正反対。合成して確かめる。
    print(f"\n{'=' * 118}")
    print("  [5] 探索の方向を合成（開いたレース × モデル非推奨 × オッズ帯）")
    print(f"{'=' * 118}")
    share_q1 = base["mkt_top3_share"].quantile(0.25)
    print(f"  上位3頭シェア Q1 閾値 = {share_q1:.3f}（これ未満を『開いたレース』とする）")
    print(HEADER)
    for o_lab, o_lo, o_hi in [("10-20", 10, 20), ("10-30", 10, 30),
                              ("20-50", 20, 50), ("10-50", 10, 50)]:
        b = base[(base["win_odds"] >= o_lo) & (base["win_odds"] < o_hi)]
        _row(f"{o_lab} & 混戦(fav>=2.0)", b[b["fav_odds"] >= 2.0], months)
        _row(f"{o_lab} & 上位3頭シェア<Q1", b[b["mkt_top3_share"] < share_q1], months)
        _row(f"{o_lab} & シェア<Q1 & 指数6位以下",
             b[(b["mkt_top3_share"] < share_q1) & (b["idx_rank_wf"] >= 6)], months)
        _row(f"{o_lab} & シェア<Q1 & fav>=2.0 & 指数6位以下",
             b[(b["mkt_top3_share"] < share_q1) & (b["fav_odds"] >= 2.0)
               & (b["idx_rank_wf"] >= 6)], months)
        print()

    # 開いたレースの中でさらにシェアが低い層（十分位で切る）
    print("  ── 開いたレース度合いを細かく（単勝10-50倍・3枠）──")
    print(HEADER)
    b = base[(base["win_odds"] >= 10) & (base["win_odds"] < 50)].copy()
    b["sq"] = pd.qcut(b["mkt_top3_share"], 10, labels=False, duplicates="drop")
    for q, g in b.groupby("sq"):
        _row(f"シェア十分位 {int(q) + 1} ({g['mkt_top3_share'].min():.2f}-"
             f"{g['mkt_top3_share'].max():.2f})", g, months)


def FROZEN(b: pd.DataFrame) -> list[tuple[str, pd.Series]]:  # noqa: N802
    """DISCOVERY(2026-01〜04) の探索結果から凍結した条件。

    探索で見えたのは「開いたレース（市場上位3頭シェアが低い）ほど穴馬の複勝 ROI が高い」
    という単調な効果で、**人気順位を固定しても残る**（7-8番人気で Q1 0.953 → Q5 0.509）。
    閾値 0.60 / 0.63 / 0.65 は DISCOVERY を見て決めたので、ここで凍結する。

    既存 `chihou_is_place_bet()`（断然人気レース × 指数上位）は探索では
    ROI 0.68〜0.70 と最下層だったため、比較参照として入れる。
    """
    o, f, r, s = b["win_odds"], b["fav_odds"], b["idx_rank_wf"], b["mkt_top3_share"]
    dark = (o >= 10) & (o < 50)
    return [
        ("★ シェア<0.60 & 10-50", dark & (s < 0.60)),
        ("★ シェア<0.63 & 10-50", dark & (s < 0.63)),
        ("★ シェア<0.65 & 10-50", dark & (s < 0.65)),
        ("シェア<0.63 & 30-50（探索最高値）", (o >= 30) & (o < 50) & (s < 0.63)),
        ("シェア<0.70 & 10-50 & 指数6位以下", dark & (s < 0.70) & (r >= 6)),
        ("シェア<0.70 & 10-50 & fav>=2.0", dark & (s < 0.70) & (f >= 2.0)),
        ("既存 place_bet 相当 (fav<2.0 & 指数3位内 & >=10倍)",
         (o >= 10) & (f < 2.0) & (r <= 3)),
    ]


def stage_holdout(df: pd.DataFrame, months: float) -> None:
    """DISCOVERY で凍結した条件のみを一度きり評価する。"""
    print(f"\n{'=' * 118}")
    print("  HOLDOUT 一度きり確認（2026-05-01〜2026-07-31）")
    print(f"{'=' * 118}")
    print(HEADER)
    base = df[df["slots"] == 3]
    _row("参照: 全馬", df, months)
    _row("参照: 単勝10-50 / 3枠（無条件）", base[(base["win_odds"] >= 10) & (base["win_odds"] < 50)], months)
    print()
    for name, mask in FROZEN(base):
        _row(name, base[mask], months)

    # 検出力: この条件で ROI>1.0 を主張するのに必要な n
    print(f"\n{'=' * 118}")
    print("  検出力（複勝は単勝より分散が小さく、少ない n で判定できる）")
    print(f"{'=' * 118}")
    main = base[(base["win_odds"] >= 10) & (base["win_odds"] < 50) & (base["mkt_top3_share"] < 0.63)]
    if len(main) > 30:
        sd = main["place_ret"].std(ddof=1)
        n_year = len(main) / months * 12
        print(f"  シェア<0.63 & 10-50: 1ベット払戻sd={sd:.3f}  年間ベット数={n_year:,.0f}本")
        print(f"\n{'真のROI':>10} {'必要n(片側a=.05,検出力80%)':>28} {'所要年数':>10}")
        for true_roi in (1.05, 1.10, 1.15, 1.20):
            n_req = ((1.645 + 0.8416) ** 2) * sd ** 2 / ((true_roi - 1.0) ** 2)
            print(f"{true_roi:>10.2f} {n_req:>28,.0f} {n_req / n_year:>10.1f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--stage", choices=["discovery", "holdout"], required=True)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    df["date"] = df["date"].astype(str)
    start, end = PLACE_DISCOVERY if args.stage == "discovery" else PLACE_HOLDOUT
    months = 4.0 if args.stage == "discovery" else 3.0
    df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    print(f"{args.stage}: {start}〜{end}  {len(df):,}行 / {df['race_id'].nunique():,}レース")

    df = attach_place_payout(df)
    df = add_structure(df)
    if args.stage == "discovery":
        stage_discovery(df, months)
    else:
        stage_holdout(df, months)


if __name__ == "__main__":
    main()
