"""人気（発走前単勝）と指数のギャップから「6番人気以下の複勝圏」を絞り込めるか検証する。

## 前回の失敗を繰り返さないための設計

`chihou_darkhorse_place.py` は**確定オッズ**で条件を判定していた。賭け時に確定
オッズは分からないため、あれは look-ahead だった（発走前オッズで測り直すと
複勝ROI 1.22 → 0.85）。本スクリプトは最初から次を守る:

  1. **人気・オッズは全て発走 N 分前のスナップショットから作る**（既定 5 分前）
  2. 指数は walk-forward honest 予測（その四半期より前のデータだけで学習）
  3. 払戻は `chihou.race_payouts`（的中馬のみ保持）。無い馬は payout 0 として
     **行を絞らない**
  4. 探索期間と確認期間を分け、確認は一度だけ

## 使える期間

`odds_history` が 2026-04-07 開始、`race_payouts` の複勝が 2026 年のみ、
walk-forward 予測が 2026-07-31 までのため:

  DISCOVERY : 2026-04-07 〜 2026-06-30
  HOLDOUT   : 2026-07-01 〜 2026-07-31

⚠️ 2026-05〜07 は前回の（失敗した）オッズ帯×シェア条件の評価に一度使っている。
   完全に処女の期間ではないことを承知の上で使う。

## 仮説

「6番人気以下（発走前）× 指数が高く評価 × その枠が空きそうなレース」に絞れば
複勝圏率が上がるか。レース単位のフィルタも併用する。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_popgap_place.py --wf /path/to/chihou_wf_honest.csv --stage discovery
"""
from __future__ import annotations

import argparse
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

from src.indices.buy_signal import chihou_market_top3_share  # noqa: E402

RNG = np.random.default_rng(0)
N_BOOT = 4000

DISCOVERY = ("20260407", "20260630")
HOLDOUT = ("20260701", "20260731")

QUERY = """
WITH r AS (
  SELECT id, date, course_name, race_number, head_count, distance,
         (to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours') AS post_utc
  FROM chihou.races
  WHERE date BETWEEN %(start)s AND %(end)s AND course <> '83'
    AND post_time ~ '^[0-9]{4}$'
),
snap AS (
  SELECT DISTINCT ON (o.race_id, o.combination)
         o.race_id, o.combination::int AS hn, o.odds AS pre_odds
  FROM r
  JOIN chihou.odds_history o
    ON o.race_id = r.id AND o.bet_type = 'win'
   AND o.fetched_at <= r.post_utc - (%(lead)s || ' minutes')::interval
  ORDER BY o.race_id, o.combination, o.fetched_at DESC
),
pay AS (
  SELECT p.race_id, p.combination::int AS hn, p.payout / 100.0 AS place_ret
  FROM chihou.race_payouts p JOIN r ON r.id = p.race_id
  WHERE p.bet_type = 'place' AND p.combination ~ '^[0-9]+$'
)
SELECT r.id AS race_id, r.date, r.course_name, r.head_count, r.distance,
       rr.horse_number AS hn, snap.pre_odds, rr.finish_position,
       COALESCE(pay.place_ret, 0.0) AS place_ret,
       (SELECT count(*) FROM chihou.race_payouts p2
         WHERE p2.race_id = r.id AND p2.bet_type = 'place') AS n_pay
FROM r
JOIN chihou.race_results rr ON rr.race_id = r.id
LEFT JOIN snap ON snap.race_id = r.id AND snap.hn = rr.horse_number
LEFT JOIN pay ON pay.race_id = r.id AND pay.hn = rr.horse_number
WHERE rr.finish_position IS NOT NULL AND COALESCE(rr.abnormality_code, 0) = 0
"""


def load(start: str, end: str, lead: int, wf_csv: str) -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(QUERY, {"start": start, "end": end, "lead": str(lead)})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()
    for c in ("pre_odds", "place_ret", "head_count", "finish_position", "n_pay", "hn", "distance"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 複勝払戻レコードのあるレースのみ（未取込を「全馬外れ」と誤読しない）
    df = df[df["n_pay"] > 0]
    # 発走前オッズが全馬ぶん取れているレースのみ（人気順位が歪むため）
    ok = df.groupby("race_id")["pre_odds"].transform(lambda s: s.notna().all())
    df = df[ok].copy()

    # walk-forward honest 指数を結合
    wf = pd.read_csv(wf_csv, usecols=["race_id", "horse_number", "composite_wf", "idx_rank_wf"])
    wf = wf.rename(columns={"horse_number": "hn"})
    df = df.merge(wf, on=["race_id", "hn"], how="inner")

    # ── 発走前オッズから人気順位（1 = 最も買われている）──
    df["pop_rank"] = df.groupby("race_id")["pre_odds"].rank(method="first").astype(int)
    # 指数順位はレース内で再計算（WF の順位は取消馬含む母集団のため）
    df["idx_rank"] = (
        df.groupby("race_id")["composite_wf"].rank(method="first", ascending=False).astype(int)
    )
    # ギャップ: 正 = 市場より指数の方が高く評価している＝過小評価
    df["gap"] = df["pop_rank"] - df["idx_rank"]

    # 複勝圏（7頭以下は2着まで）
    slots = np.where(df["head_count"] >= 8, 3, 2)
    df["in_place"] = (df["finish_position"] <= slots).astype(int)
    df["slots"] = slots

    # ── レース単位の特徴（すべて発走前オッズから）──
    g = df.groupby("race_id")
    df["top3_share"] = df["race_id"].map(g["pre_odds"].apply(chihou_market_top3_share))
    df["fav_odds"] = g["pre_odds"].transform("min")
    # 市場と指数の一致度: レース内 Spearman（低いほど食い違う）
    df["mkt_idx_corr"] = df["race_id"].map(
        g.apply(lambda x: x["pop_rank"].corr(x["idx_rank"], method="spearman"), include_groups=False)
    )
    return df


def _ci(v: np.ndarray) -> tuple[float, float, float]:
    if len(v) == 0:
        return 0.0, 0.0, 0.0
    b = RNG.choice(v, size=(N_BOOT, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), *np.percentile(b, [2.5, 97.5])


def _row(label: str, sub: pd.DataFrame, months: float, base_hit: float) -> None:
    if len(sub) < 25:
        print(f"  {label:<40} n={len(sub):>5}  — 標本不足")
        return
    hit = sub["in_place"].mean()
    roi, lo, hi = _ci(sub["place_ret"].values)
    lift = hit / base_hit if base_hit else 0.0
    print(f"  {label:<40} n={len(sub):>5} ({len(sub) / months * 12:>5,.0f}/年) "
          f"複勝率={hit:.3f} (x{lift:.2f}) ROI={roi:.3f} CI[{lo:.3f},{hi:.3f}]")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--wf", required=True, help="chihou_darkhorse_wf_build.py の出力 CSV")
    p.add_argument("--stage", choices=["discovery", "holdout"], required=True)
    p.add_argument("--lead", type=int, default=5)
    args = p.parse_args()

    start, end = DISCOVERY if args.stage == "discovery" else HOLDOUT
    months = max((pd.to_datetime(end) - pd.to_datetime(start)).days / 30.4, 0.5)
    df = load(start, end, args.lead, args.wf)
    print(f"{args.stage}: {start}〜{end} (発走{args.lead}分前) "
          f"{len(df):,}行 / {df['race_id'].nunique():,}レース")

    # 母集団 = 発走前 6番人気以下
    pop6 = df[df["pop_rank"] >= 6].copy()
    base_hit = pop6["in_place"].mean()
    base_roi, blo, bhi = _ci(pop6["place_ret"].values)
    print(f"\n{'=' * 100}")
    print("  [0] 母集団: 発走前6番人気以下（無条件）")
    print(f"{'=' * 100}")
    print(f"  n={len(pop6):,}  複勝圏率={base_hit:.4f}  複勝ROI={base_roi:.3f} "
          f"CI[{blo:.3f},{bhi:.3f}]")
    print(f"  参考: 5番人気以内 複勝圏率="
          f"{df[df['pop_rank'] <= 5]['in_place'].mean():.4f}")
    hit_any = df[df["pop_rank"] >= 6].groupby("race_id")["in_place"].max()
    print(f"  6番人気以下が1頭でも複勝圏に入るレース: "
          f"{hit_any.mean() * 100:.1f}%（{int(hit_any.sum()):,}/{len(hit_any):,}）")

    if args.stage == "holdout":
        print(f"\n{'=' * 100}")
        print("  HOLDOUT: 凍結条件のみ")
        print(f"{'=' * 100}")
        for name, mask in FROZEN(pop6):
            _row(name, pop6[mask], months, base_hit)
        return

    print(f"\n{'=' * 100}")
    print("  [1] 馬側: 指数順位（発走前6番人気以下のみ）")
    print(f"{'=' * 100}")
    for rmax in (1, 2, 3, 5):
        _row(f"指数{rmax}位以内", pop6[pop6["idx_rank"] <= rmax], months, base_hit)
    _row("指数6位以下", pop6[pop6["idx_rank"] >= 6], months, base_hit)

    print(f"\n{'=' * 100}")
    print("  [2] 馬側: ギャップ = 人気順位 − 指数順位（正=指数が市場より高評価）")
    print(f"{'=' * 100}")
    for g0 in (2, 3, 4, 5, 6, 8):
        _row(f"gap>={g0}", pop6[pop6["gap"] >= g0], months, base_hit)

    print(f"\n{'=' * 100}")
    print("  [3] レース側: どんなレースで6番人気以下が複勝圏に入るか")
    print(f"{'=' * 100}")
    for lab, m in [
        ("開いた (シェア<0.63)", pop6["top3_share"] < 0.63),
        ("やや開いた (0.63-0.72)", pop6["top3_share"].between(0.63, 0.72)),
        ("固い (シェア>=0.72)", pop6["top3_share"] >= 0.72),
        ("断然人気 (fav<1.5)", pop6["fav_odds"] < 1.5),
        ("混戦 (fav>=3.0)", pop6["fav_odds"] >= 3.0),
        ("市場と指数が一致 (corr>=0.6)", pop6["mkt_idx_corr"] >= 0.6),
        ("市場と指数が食い違う (corr<0.2)", pop6["mkt_idx_corr"] < 0.2),
        ("多頭数 (12頭以上)", pop6["head_count"] >= 12),
        ("少頭数 (8-9頭)", pop6["head_count"].between(8, 9)),
    ]:
        _row(lab, pop6[m], months, base_hit)

    print(f"\n{'=' * 100}")
    print("  [4] 事前登録した組み合わせ（レース条件 × 馬条件）")
    print(f"{'=' * 100}")
    for r_lab, r_mask in [
        ("全レース", pd.Series(True, index=pop6.index)),
        ("シェア<0.63", pop6["top3_share"] < 0.63),
        ("corr<0.2", pop6["mkt_idx_corr"] < 0.2),
        ("シェア<0.70 & corr<0.4", (pop6["top3_share"] < 0.70) & (pop6["mkt_idx_corr"] < 0.4)),
    ]:
        for h_lab, h_mask in [
            ("指数3位内", pop6["idx_rank"] <= 3),
            ("指数5位内", pop6["idx_rank"] <= 5),
            ("gap>=4", pop6["gap"] >= 4),
            ("指数3位内 & gap>=4", (pop6["idx_rank"] <= 3) & (pop6["gap"] >= 4)),
        ]:
            _row(f"{r_lab} × {h_lab}", pop6[r_mask & h_mask], months, base_hit)


def FROZEN(b: pd.DataFrame) -> list[tuple[str, pd.Series]]:  # noqa: N802
    """DISCOVERY を見てから確定させる凍結条件（holdout 実行前に埋める）。"""
    return [
        ("指数3位内", b["idx_rank"] <= 3),
        ("指数3位内 & シェア<0.63", (b["idx_rank"] <= 3) & (b["top3_share"] < 0.63)),
        ("gap>=4", b["gap"] >= 4),
        ("指数3位内 & gap>=4", (b["idx_rank"] <= 3) & (b["gap"] >= 4)),
    ]


if __name__ == "__main__":
    main()
