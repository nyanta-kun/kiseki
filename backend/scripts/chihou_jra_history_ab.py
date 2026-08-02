"""地方 × JRA戦歴 クロス特徴の A/B（Phase 2）

背景（`docs/chihou_logic_review_2026_08_02.md` C1）:
  `keiba` スキーマは JRA（course 01-10）の全レースを保持しており、
  **地方の出走馬 20,033頭のうち 10,773頭（53.8%）に JRA 出走歴がある**のに、
  地方の44特徴はこれを一切使っていない。地方は場ごとに時計水準もクラス体系も
  違うため、**JRA 戦歴は場をまたぐ共通のものさし**になりうる、というのが仮説。

名寄せ:
  `chihou.horses.name + birthday` ↔ `keiba.horses.name + birthday`。
  keiba 側は同一 (name, birthday) が 1,769 組重複している（同じ馬のマスタ重複）ため、
  **一致した keiba 馬IDを全部まとめて 1 頭として扱う**（レースIDで重複排除）。
  馬名のみの突合は keiba に同名馬が多く危険なので使わない。

point-in-time:
  各地方レース行に対し、**その開催日より前の JRA 走のみ**を集計する。
  `merge_asof(direction="backward", allow_exact_matches=False)` で厳密に担保する。

追加する特徴（8本）:
  has_jra          : JRA 出走歴あり
  jra_runs_n       : JRA 出走数 min(n,20)/20
  jra_best_pos_n   : JRA での最良の正規化着順（0=1着, 1=最下位）
  jra_avg_pos_n    : 同 平均
  jra_days_since   : 最終 JRA 走からの経過日数 log10(1+d)/4（転入からの間隔）
  jra_first_after  : 転入初戦フラグ（この地方走が最終 JRA 走の直後の初戦）
  jra_prize_log    : 出走した JRA レースの最高 1着賞金 log1p（クラス水準の代理）
  jra_last_pos_n   : 最終 JRA 走の正規化着順

Web 調査の裏付け: 中央→地方の転入初戦は場によって大きく差がある
  （金沢 勝率25.1% / 笠松 24.8% / 高知 24.6% ⇔ 大井 11.6% / 園田 13.2%）。
  ただし単勝回収率は全場 100% 未満（最良 85.2）＝**市場は既に織り込んでいる**ため、
  買い目ルールではなく**特徴量**として入れて順位精度が上がるかを見るのが本 A/B の狙い。

honest 分割は `src/chihou_protocol.py` 準拠（train ≤20250630 / valid 〜20251231 /
test 20260101〜20260630）。TEST_START(20260701) 以降は使わない。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_jra_history_ab.py
"""

from __future__ import annotations

import argparse
import json
import logging
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

from scripts.chihou_rank_quality_review import (  # noqa: E402
    DATA_START,
    VALID_END,
    connect,
    evaluate,
    paired_bootstrap,
    per_race_metrics,
    train_binary,
)
from scripts.train_chihou_market_lgb import (  # noqa: E402
    ALL_FEATURES,
    MARKET_FEATURES,
    fetch,
    prep,
)
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TRAIN_END  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_jra_hist")

MODELS_DIR = _root / "models"

JRA_FEATURES = [
    "has_jra", "jra_runs_n", "jra_best_pos_n", "jra_avg_pos_n",
    "jra_days_since", "jra_first_after", "jra_prize_log", "jra_last_pos_n",
]

# 地方の出走馬に紐づく JRA 走をすべて取得する。
# 同一 (name, birthday) に複数の keiba 馬IDがぶら下がる（マスタ重複）ので、
# chihou 側の horse_id に畳んでから DISTINCT で JRA レースの重複を潰す。
JRA_RUNS_SQL = """
SELECT DISTINCT
    ch.id                AS horse_id,
    rc.date              AS jra_date,
    rr.finish_position   AS fp,
    COALESCE(rc.head_count, 16) AS hc,
    rc.prize_1st         AS prize,
    rc.id                AS jra_race_id
FROM chihou.horses ch
JOIN keiba.horses kh
  ON kh.name = ch.name AND kh.birthday = ch.birthday
JOIN keiba.race_results rr ON rr.horse_id = kh.id
JOIN keiba.races rc        ON rc.id = rr.race_id
WHERE rc.course BETWEEN '01' AND '10'
  AND rr.finish_position IS NOT NULL
  AND rr.finish_position > 0
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rc.date < %(end)s
"""


def _to_days(s: pd.Series) -> pd.Series:
    return (pd.to_datetime(s, format="%Y%m%d", errors="coerce")
            - pd.Timestamp("2000-01-01")).dt.days


def build_jra_state(conn, end: str) -> pd.DataFrame:
    """JRA 走ごとの「その走を終えた時点までの累積状態」を作る。

    merge_asof で地方レース日より前の直近状態を引くための土台。
    """
    cur = conn.cursor()
    cur.execute(JRA_RUNS_SQL, {"end": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    if df.empty:
        return df
    logger.info(f"JRA走 {len(df):,} 件 / {df['horse_id'].nunique():,} 頭")

    df["fp"] = pd.to_numeric(df["fp"], errors="coerce")
    df["hc"] = pd.to_numeric(df["hc"], errors="coerce").clip(lower=2)
    df["prize"] = pd.to_numeric(df["prize"], errors="coerce").fillna(0.0)
    # 正規化着順（0=1着, 1=最下位）
    df["pos_n"] = ((df["fp"] - 1) / (df["hc"] - 1)).clip(0.0, 1.0)
    df["jra_days"] = _to_days(df["jra_date"])
    df = df.sort_values(["horse_id", "jra_days"]).reset_index(drop=True)

    g = df.groupby("horse_id", sort=False)
    df["_jh_c_runs"] = g.cumcount() + 1
    df["_jh_c_best"] = g["pos_n"].cummin()
    df["_jh_c_sum"] = g["pos_n"].cumsum()
    df["_jh_c_prize"] = g["prize"].cummax()
    df["_jh_c_avg"] = df["_jh_c_sum"] / df["_jh_c_runs"]
    df["_jh_c_last_pos"] = df["pos_n"]
    df["_jh_c_last_days"] = df["jra_days"]
    return df[["horse_id", "jra_days", "_jh_c_runs", "_jh_c_best", "_jh_c_avg",
               "_jh_c_prize", "_jh_c_last_pos", "_jh_c_last_days"]]


def add_jra_features(df: pd.DataFrame, state: pd.DataFrame) -> pd.DataFrame:
    """地方レース行に point-in-time な JRA 戦歴特徴を付与する。"""
    left = df.copy()
    left["_days"] = _to_days(left["date"])
    left = left.sort_values("_days", kind="stable").reset_index()
    right = state.sort_values("jra_days", kind="stable")

    merged = pd.merge_asof(
        left, right,
        left_on="_days", right_on="jra_days", by="horse_id",
        direction="backward", allow_exact_matches=False,   # 当日の JRA 走は使わない
    )
    merged = merged.set_index("index").sort_index()

    has = merged["_jh_c_runs"].notna()
    out = df.copy()
    out["has_jra"] = has.astype(int).to_numpy()
    out["jra_runs_n"] = (merged["_jh_c_runs"].fillna(0).clip(upper=20) / 20.0).to_numpy()
    # 未経験は「中位」で埋める（0 だと 1着相当になってしまう）
    out["jra_best_pos_n"] = merged["_jh_c_best"].fillna(0.5).to_numpy()
    out["jra_avg_pos_n"] = merged["_jh_c_avg"].fillna(0.5).to_numpy()
    out["jra_last_pos_n"] = merged["_jh_c_last_pos"].fillna(0.5).to_numpy()
    days_since = (merged["_days"] - merged["_jh_c_last_days"]).clip(lower=0)
    out["jra_days_since"] = (np.log10(1.0 + days_since) / 4.0).fillna(1.0).to_numpy()
    out["jra_prize_log"] = np.log1p(merged["_jh_c_prize"].fillna(0.0)).to_numpy()

    # 転入初戦: JRA 経験があり、かつ「この馬のこれまでの地方走の中で
    # 最終 JRA 走より後の初めての1走」であるか
    tmp = out[["horse_id", "date"]].copy()
    tmp["_days"] = _to_days(tmp["date"])
    tmp["_last_jra"] = merged["_jh_c_last_days"].to_numpy()
    tmp["_after"] = tmp["_days"] > tmp["_last_jra"]
    tmp = tmp.sort_values(["horse_id", "_days"], kind="stable")
    # 同一馬で「最終JRA走の日付」が切り替わった直後の1走 = 転入初戦
    first = (tmp.groupby(["horse_id", "_last_jra"], sort=False).cumcount() == 0)
    out["jra_first_after"] = (first & tmp["_after"] & tmp["_last_jra"].notna()) \
        .reindex(out.index).fillna(False).astype(int).to_numpy()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--out-threshold", type=int, default=5)
    p.add_argument("--json-out", default=str(MODELS_DIR / "chihou_jra_history_ab.json"))
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    test_start, test_end = "20260101", "20260630"

    conn = connect()
    try:
        logger.info(f"データ取得 {DATA_START}〜{test_end}")
        df_raw = fetch(conn, DATA_START, test_end)
        df_hist = fetch_hist(conn)
        logger.info("前処理（prep）")
        df = prep(conn, df_raw, df_hist)
        logger.info("JRA戦歴の取得・累積状態の構築")
        state = build_jra_state(conn, test_end)
    finally:
        conn.close()

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    logger.info("JRA戦歴特徴の付与（point-in-time）")
    df = add_jra_features(df, state)
    cov = float(df["has_jra"].mean())
    logger.info(f"JRA戦歴のカバレッジ: {cov:.1%} / 転入初戦フラグ {df['jra_first_after'].mean():.2%}")

    tr = df[df["date"] <= TRAIN_END].copy()
    va = df[(df["date"] > TRAIN_END) & (df["date"] <= VALID_END)].copy()
    te = df[(df["date"] >= test_start) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info(f"train {len(tr):,} / valid {len(va):,} / test {len(te):,}"
                f"（{te['race_id'].nunique():,}R）")

    y_tr = (tr["finish_position"] <= 3).astype(int).values
    y_va = (va["finish_position"] <= 3).astype(int).values

    # 市場（オッズ）特徴を外した対でも測る。
    # JRA戦歴が「情報としては効くが市場に既に織り込まれている」のか、
    # 「そもそも情報が無い」のかを切り分けるため（前者なら no-market 側では効くはず）。
    nomkt = [f for f in ALL_FEATURES if f not in MARKET_FEATURES]
    variants = {
        "base44(現行)": list(ALL_FEATURES),
        "base44 +JRA戦歴8": list(ALL_FEATURES) + JRA_FEATURES,
        "base39(市場なし)": nomkt,
        "base39 +JRA戦歴8": nomkt + JRA_FEATURES,
    }
    scores = {}
    for name, feats in variants.items():
        logger.info(f"学習 {name}（{len(feats)}特徴）")
        scores[name] = train_binary(tr, va, te, y_tr, y_va, feats, seeds)

    results = {n: evaluate(te, s, args.out_threshold) for n, s in scores.items()}
    metrics = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3",
               "TAIL_bot3_out_rate", "TAIL_placer_in_bot30pct", "ALL_spearman"]

    print("\n" + "=" * 112)
    print(f"地方 × JRA戦歴 A/B  test {test_start}〜{test_end} "
          f"({te['race_id'].nunique():,}R) / JRA戦歴カバレッジ {cov:.1%}")
    print("=" * 112)
    hdr = f"{'variant':<20}" + "".join(f"{m.split('_', 1)[1]:>17}" for m in metrics)
    print(hdr)
    print("-" * len(hdr))
    for n, r in results.items():
        print(f"{n:<20}" + "".join(f"{r[m]:>17.4f}" for m in metrics))

    pr = {n: per_race_metrics(te, s) for n, s in scores.items()}
    boot = {}
    # 市場あり/なしはそれぞれの土台を基準にペア比較する
    pairs = [("base44(現行)", "base44 +JRA戦歴8"), ("base39(市場なし)", "base39 +JRA戦歴8")]
    print("\npaired bootstrap（各土台を基準 / 95%CI）")
    for base, n in pairs:
        cells, rec = "", {}
        for m in ["top1_win", "top1_place", "spearman"]:
            d, lo, hi = paired_bootstrap(pr[base], pr[n], m)
            sig = "*" if (lo > 0 or hi < 0) else " "
            cells += f"  {m}: {d:>+8.4f} [{lo:>+7.4f},{hi:>+7.4f}]{sig}"
            rec[m] = [round(d, 5), round(lo, 5), round(hi, 5)]
        boot[n] = rec
        print(f"{n:<20}{cells}")
    print("* = 95%CI が 0 を跨がない")

    # JRA戦歴を持つ馬だけに絞った効果（薄まりを排除して見る）
    sub = te["has_jra"] == 1
    if sub.any():
        print(f"\n【JRA戦歴を持つ馬のみ】 n={int(sub.sum()):,}頭 "
              f"({sub.mean():.1%}) の 3着内率:")
        for n, s in scores.items():
            d = te[sub].copy()
            d["_s"] = s[sub.to_numpy()]
            d["rk"] = d.groupby("race_id")["_s"].rank(ascending=False, method="first")
            print(f"  {n:<20} 指数1位時の複勝率="
                  f"{(d[d['rk'] == 1]['finish_position'] <= 3).mean():.4f}")

    Path(args.json_out).write_text(json.dumps(
        {"test_start": test_start, "test_end": test_end, "seeds": seeds,
         "jra_coverage": round(cov, 4), "n_races": int(te["race_id"].nunique()),
         "results": results, "paired_bootstrap_vs_base": boot},
        ensure_ascii=False, indent=2))
    logger.info(f"保存: {args.json_out}")


if __name__ == "__main__":
    main()
