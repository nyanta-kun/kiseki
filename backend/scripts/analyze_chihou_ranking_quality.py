"""地方競馬 指数ランキング品質 Phase1 定量分析

目標指数の現状を「学習/検証/テスト」の時系列分割で汚染なく評価する。

■ 評価期間（データ汚染防止）
  - 学習期間（参照のみ）: 20230101 〜 20250630  ← 現行モデルの学習範囲
  - 検証期間 (OOS-val)  : 20250701 〜 20251231
  - テスト期間 (OOS-test): 20260101 〜 today     ← 純フォワード

  モデルは学習期間データを使って学習済み。検証・テスト期間のみで評価することで
  インサンプル楽観バイアスを排除する。

■ 評価指標
  M0: 指数1位が1着になる率（現行 top1_win_rate）
  M1: 指数1位が1・2着になる率 [目標ゴール主指標]
  M2: 指数top3のうち2頭以上が着順top3に入る率
  M3: 指数top3が着順top3を完全カバーする率（3連複完全的中相当）
  MK: レース内 Spearman 順位相関（全頭）

  各指標を競馬場グループ別・個別場別・優先度別に集計する。

■ ベースライン比較
  市場（単勝人気順）でも同じ指標を計算し、「指数 vs 市場」の差を提示する。

■ 失敗パターン分析
  指数1位が1・2着を外した(M1失敗)レースを層別分析し、要因を特定する。

使い方:
  cd backend
  .venv/bin/python scripts/analyze_chihou_ranking_quality.py
  .venv/bin/python scripts/analyze_chihou_ranking_quality.py --venue 大井
  .venv/bin/python scripts/analyze_chihou_ranking_quality.py --period test
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_ranking_quality")

# ─────────────────────────────────────────────
# 期間定義（汚染防止）
# ─────────────────────────────────────────────
TRAIN_END   = "20250630"   # 現行モデルの学習終了日（この日までがインサンプル）
VAL_START   = "20250701"   # 検証期間開始（OOS-val）
VAL_END     = "20251231"   # 検証期間終了
TEST_START  = "20260101"   # テスト期間開始（純フォワード OOS）
TODAY       = "20260702"   # 今日の日付

PERIODS = {
    "train": ("20230101", TRAIN_END),    # 参照用（楽観バイアスあり）
    "val":   (VAL_START,  VAL_END),      # OOS-val
    "test":  (TEST_START, TODAY),        # 純フォワード OOS
}

# ─────────────────────────────────────────────
# 競馬場グループ
# ─────────────────────────────────────────────
VENUE_GROUP_A = {"大井", "川崎", "船橋", "浦和"}     # 南関東（最優先）
VENUE_GROUP_B = {"高知", "佐賀"}                       # 第2優先
# Group C = その他全て

CHIHOU_V10_VERSION = 10

# ─────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────
BASE_QUERY = """
SELECT
    ci.race_id,
    r.date,
    r.course_name,
    r.distance,
    r.head_count,
    r.surface,
    r.condition,
    re.horse_id,
    re.frame_number,
    ci.composite_index,
    ci.speed_index,
    ci.last3f_index,
    ci.jockey_index,
    ci.rotation_index,
    ci.last_margin_index,
    rr.finish_position,
    rr.win_popularity,
    rr.win_odds,
    rr.place_odds
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %(ver)s
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""


def fetch(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V10_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for col in ["composite_index", "win_popularity", "finish_position",
                "win_odds", "place_odds", "head_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ─────────────────────────────────────────────
# レース内ランキング付与
# ─────────────────────────────────────────────

def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """各レース内で指数順位・人気順位・着順を付与する。"""
    df = df.copy()
    g = df.groupby("race_id")
    # 指数ランク (1=最高指数, 降順) — NaN は最下位扱い (na_option='bottom')
    df["idx_rank"] = g["composite_index"].rank(ascending=False, method="min",
                                                na_option="bottom").astype(int)
    # 人気ランク (1=1番人気, 昇順) — popularity が NULL の馬は最下位
    df["pop_rank"] = g["win_popularity"].rank(ascending=True, method="min",
                                               na_option="bottom").astype(int)
    # 着順
    df["fp"] = df["finish_position"].astype(int)
    # 競馬場グループ
    df["venue_group"] = df["course_name"].apply(
        lambda v: "A_南関東" if v in VENUE_GROUP_A
        else ("B_九四州" if v in VENUE_GROUP_B else "C_その他")
    )
    return df


# ─────────────────────────────────────────────
# 指標計算
# ─────────────────────────────────────────────

def spearman_rho(race_df: pd.DataFrame, rank_col: str = "idx_rank") -> float:
    """1レースのスピアマン順位相関（指数順位 vs 着順）。"""
    if len(race_df) < 3:
        return np.nan
    n = len(race_df)
    d2 = ((race_df[rank_col] - race_df["fp"]) ** 2).sum()
    return 1.0 - 6.0 * d2 / (n * (n ** 2 - 1))


def top3_coverage_by_col(race_df: pd.DataFrame, rank_col: str) -> dict[str, int | float]:
    """1レースのtop3カバレッジ指標を返す。rank_col で指定した列を使う。"""
    idx_top3 = set(race_df.nsmallest(3, rank_col)["horse_id"])
    act_top3 = set(race_df.nsmallest(3, "fp")["horse_id"])
    covered = len(idx_top3 & act_top3)
    return {
        "cover3": int(covered == 3),
        "cover2": int(covered >= 2),
        "cover1": int(covered >= 1),
        "covered_n": covered,
    }


def spearman_rho_by_col(race_df: pd.DataFrame, rank_col: str) -> float:
    """1レースのスピアマン順位相関（指定ランク列 vs 着順）。"""
    if len(race_df) < 3:
        return np.nan
    n = len(race_df)
    d2 = ((race_df[rank_col] - race_df["fp"]) ** 2).sum()
    return 1.0 - 6.0 * d2 / (n * (n ** 2 - 1))


def compute_metrics(df: pd.DataFrame, ranker: str = "idx_rank",
                    label: str = "index") -> pd.DataFrame:
    """全レース × 全指標を計算して race-level DataFrame を返す。

    Args:
        df: add_ranks 済みのデータフレーム
        ranker: ランク列名。"idx_rank" or "pop_rank"
        label: 結果 DataFrame のプレフィックス
    """
    rows = []
    for race_id, g in df.groupby("race_id"):
        g = g.copy()

        top1 = g[g[ranker] == 1]
        if top1.empty:
            continue
        fp1 = int(top1.iloc[0]["fp"])

        cov = top3_coverage_by_col(g, rank_col=ranker)
        rho = spearman_rho_by_col(g, rank_col=ranker)

        rows.append({
            "race_id":    race_id,
            "date":       g.iloc[0]["date"],
            "course_name": g.iloc[0]["course_name"],
            "venue_group": g.iloc[0]["venue_group"],
            "head_count": g.iloc[0]["head_count"],
            "m0_top1win":  int(fp1 == 1),
            "m1_top1in2":  int(fp1 <= 2),
            "m1_top1in3":  int(fp1 <= 3),
            **{f"{label}_{k}": v for k, v in cov.items()},
            f"{label}_spearman": rho,
            "n_horses":   len(g),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# Bootstrap CI
# ─────────────────────────────────────────────

def bootstrap_ci(arr: np.ndarray, n_boot: int = 2000, ci: float = 0.95,
                 rng_seed: int = 42) -> tuple[float, float]:
    """Bootstrap 信頼区間 (lower, upper)。"""
    if len(arr) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(rng_seed)
    means = [rng.choice(arr, size=len(arr), replace=True).mean() for _ in range(n_boot)]
    lo = (1 - ci) / 2
    return (float(np.percentile(means, lo * 100)), float(np.percentile(means, (1 - lo) * 100)))


# ─────────────────────────────────────────────
# 集計テーブル作成
# ─────────────────────────────────────────────

def summarize(race_df: pd.DataFrame, group_col: str = "venue_group") -> pd.DataFrame:
    """グループ別に指標を集計する。"""
    metric_cols = [
        ("m0_top1win",      "指数1位勝率",       "index"),
        ("m1_top1in2",      "指数1位→1・2着率",  "index"),
        ("m1_top1in3",      "指数1位→複勝率",    "index"),
        ("index_cover3",    "top3完全一致率(M3)", "index"),
        ("index_cover2",    "top3_2頭一致率(M2)", "index"),
        ("index_spearman",  "Spearman相関",        "index"),
    ]
    rows = []
    for grp, g in race_df.groupby(group_col):
        row = {"グループ": grp, "レース数": len(g)}
        for col, name, _ in metric_cols:
            if col not in g.columns:
                continue
            vals = g[col].dropna().values
            mu = float(np.mean(vals)) * 100 if col != "index_spearman" else float(np.mean(vals))
            lo, hi = bootstrap_ci(vals)
            lo_pct = lo * 100 if col != "index_spearman" else lo
            hi_pct = hi * 100 if col != "index_spearman" else hi
            row[f"{name}(%)"] = f"{mu:.1f}"
            row[f"{name}_CI95"] = f"[{lo_pct:.1f},{hi_pct:.1f}]"
        rows.append(row)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# 失敗パターン分析
# ─────────────────────────────────────────────

def failure_analysis(df: pd.DataFrame, venue_filter: str | None = None) -> pd.DataFrame:
    """M1 失敗（指数1位が1・2着を外した）レースの要因分析。

    サブ指数の分布・人気・オッズを層別に比較する。
    指数期間: テスト期間(OOS-test)のみで実施する。
    """
    if venue_filter:
        df = df[df["course_name"] == venue_filter]

    top1 = df[df["idx_rank"] == 1].copy()
    top1["m1_fail"] = (top1["fp"] > 2).astype(int)

    success = top1[top1["m1_fail"] == 0]
    failure = top1[top1["m1_fail"] == 1]

    sub_cols = ["speed_index", "last3f_index", "jockey_index",
                "rotation_index", "last_margin_index", "composite_index",
                "win_popularity", "win_odds"]

    rows = []
    for col in sub_cols:
        if col not in top1.columns:
            continue
        s = success[col].dropna()
        f = failure[col].dropna()
        rows.append({
            "指標": col,
            "成功_平均": f"{s.mean():.2f}" if len(s) else "-",
            "失敗_平均": f"{f.mean():.2f}" if len(f) else "-",
            "差(成功-失敗)": f"{s.mean() - f.mean():.2f}" if len(s) and len(f) else "-",
        })

    row_total = {
        "指標": "件数",
        "成功_平均": str(len(success)),
        "失敗_平均": str(len(failure)),
        "差(成功-失敗)": f"成功率={(len(success)/(len(success)+len(failure))*100):.1f}%" if (len(success)+len(failure)) > 0 else "-",
    }
    return pd.DataFrame([row_total] + rows)


def failure_by_subindex_quartile(df: pd.DataFrame) -> pd.DataFrame:
    """各サブ指数の四分位別に M1 失敗率を集計する（指数1位の馬のみ）。

    指数が高いのに1・2着を外すパターン = 「何が足りないか」の診断。
    """
    top1 = df[df["idx_rank"] == 1].copy()
    top1["m1_fail"] = (top1["fp"] > 2).astype(int)

    sub_cols = ["speed_index", "last3f_index", "jockey_index",
                "rotation_index", "last_margin_index"]
    rows = []
    for col in sub_cols:
        if col not in top1.columns:
            continue
        top1[f"q_{col}"] = pd.qcut(top1[col], q=4, labels=["Q1低", "Q2", "Q3", "Q4高"],
                                    duplicates="drop")
        for q, g in top1.groupby(f"q_{col}", observed=True):
            m1_fail_rate = g["m1_fail"].mean() * 100
            rows.append({
                "サブ指数": col,
                "四分位": q,
                "n": len(g),
                "M1失敗率(%)": f"{m1_fail_rate:.1f}",
            })
    return pd.DataFrame(rows)


def failure_by_head_count(df: pd.DataFrame) -> pd.DataFrame:
    """頭数帯別の M1 率を集計する（地方は小頭数が多い）。"""
    top1 = df[df["idx_rank"] == 1].copy()
    top1["m1_fail"] = (top1["fp"] > 2).astype(int)
    top1["head_band"] = pd.cut(top1["head_count"],
                                bins=[5, 7, 9, 11, 13, 99],
                                labels=["6-7頭", "8-9頭", "10-11頭", "12-13頭", "14頭+"])
    rows = []
    for band, g in top1.groupby("head_band", observed=True):
        rows.append({
            "頭数帯": band,
            "n": len(g),
            "M1成功率(%)": f"{(1 - g['m1_fail'].mean()) * 100:.1f}",
            "M0勝率(%)":   f"{(g['fp'] == 1).mean() * 100:.1f}",
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="地方競馬 指数ランキング品質 Phase1 分析")
    p.add_argument("--period", choices=["train", "val", "test", "all"], default="all",
                   help="評価期間 (default: all で全期間を比較表示)")
    p.add_argument("--venue", default=None, help="特定競馬場のみ分析 (例: 大井)")
    p.add_argument("--n-boot", type=int, default=2000, help="Bootstrap サンプル数")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    # 評価対象期間を決定
    if args.period == "all":
        target_periods = ["val", "test"]  # train は楽観バイアスがあるため参考扱い
    else:
        target_periods = [args.period]

    # ─── 全期間のデータを取得してキャッシュ ───
    period_dfs: dict[str, pd.DataFrame] = {}
    for pname in (["train", "val", "test"] if args.period == "all" else [args.period]):
        start, end = PERIODS[pname]
        logger.info("データ取得: %s (%s 〜 %s)", pname, start, end)
        raw = fetch(conn, start, end)
        if raw.empty:
            logger.warning("%s: データなし", pname)
            continue
        ranked = add_ranks(raw)
        if args.venue:
            ranked = ranked[ranked["course_name"] == args.venue]
        period_dfs[pname] = ranked
        logger.info("  → %d 馬行 / %d レース", len(ranked), ranked["race_id"].nunique())
    conn.close()

    sep = "=" * 72

    # ═══════════════════════════════════════════════════════
    # 1. 全期間 × 競馬場グループ別サマリー
    # ═══════════════════════════════════════════════════════
    print(f"\n{sep}")
    print("■ 1. 競馬場グループ別 指標サマリー（期間別）")
    print(sep)
    print("  ※ train は学習期間（インサンプル参照のみ）。val/test が真のOOS評価。\n")

    for pname, df in period_dfs.items():
        if pname == "train":
            bias_note = "⚠️ 学習期間(インサンプル参照)"
        elif pname == "val":
            bias_note = "✅ OOS-val (汚染なし)"
        else:
            bias_note = "✅ OOS-test 純フォワード"

        n_races = df["race_id"].nunique()
        start, end = PERIODS[pname]
        print(f"【{pname}】{bias_note}  {start}〜{end}  ({n_races:,}レース)")

        race_df = compute_metrics(df, ranker="idx_rank", label="index")
        tbl = summarize(race_df, group_col="venue_group")
        print(tbl.to_string(index=False))
        print()

    # ═══════════════════════════════════════════════════════
    # 2. 個別競馬場別サマリー（OOS-test のみ）
    # ═══════════════════════════════════════════════════════
    if "test" in period_dfs:
        df_test = period_dfs["test"]
        print(f"\n{sep}")
        print("■ 2. 個別競馬場別 指標（OOS-test 純フォワード）")
        print(sep)

        race_df_t = compute_metrics(df_test, ranker="idx_rank", label="index")
        tbl_venue = summarize(race_df_t, group_col="course_name")
        # 優先度グループ付き
        tbl_venue = tbl_venue.sort_values("グループ")
        print(tbl_venue.to_string(index=False))

    # ═══════════════════════════════════════════════════════
    # 3. 指数 vs 市場（人気）比較（OOS-test）
    # ═══════════════════════════════════════════════════════
    if "test" in period_dfs:
        df_test = period_dfs["test"]
        print(f"\n{sep}")
        print("■ 3. 指数 vs 市場（人気）比較（OOS-test）")
        print(sep)

        idx_metrics  = compute_metrics(df_test, ranker="idx_rank", label="index")
        mkt_metrics  = compute_metrics(df_test, ranker="pop_rank",  label="market")

        compare_rows = []
        for vg, ig in idx_metrics.groupby("venue_group"):
            mg = mkt_metrics[mkt_metrics["venue_group"] == vg]
            n = len(ig)
            compare_rows.append({
                "グループ":            vg,
                "n(レース)":           n,
                # M0
                "指数M0(勝率%)":       f"{ig['m0_top1win'].mean()*100:.1f}",
                "市場M0(勝率%)":       f"{mg['m0_top1win'].mean()*100:.1f}" if not mg.empty else "-",
                "差M0":                f"{(ig['m0_top1win'].mean()-mg['m0_top1win'].mean())*100:.1f}" if not mg.empty else "-",
                # M1
                "指数M1(1・2着%)":     f"{ig['m1_top1in2'].mean()*100:.1f}",
                "市場M1(1・2着%)":     f"{mg['m1_top1in2'].mean()*100:.1f}" if not mg.empty else "-",
                "差M1":                f"{(ig['m1_top1in2'].mean()-mg['m1_top1in2'].mean())*100:.1f}" if not mg.empty else "-",
                # M2
                "指数M2(2頭一致%)":    f"{ig['index_cover2'].mean()*100:.1f}",
                "市場M2(2頭一致%)":    f"{mg['market_cover2'].mean()*100:.1f}" if (not mg.empty and 'market_cover2' in mg.columns) else "-",
                # M3
                "指数M3(完全一致%)":   f"{ig['index_cover3'].mean()*100:.1f}",
                "市場M3(完全一致%)":   f"{mg['market_cover3'].mean()*100:.1f}" if (not mg.empty and 'market_cover3' in mg.columns) else "-",
            })
        df_compare = pd.DataFrame(compare_rows)
        print(df_compare.to_string(index=False))

    # ═══════════════════════════════════════════════════════
    # 4. 失敗パターン分析（OOS-test）
    # ═══════════════════════════════════════════════════════
    if "test" in period_dfs:
        df_test = period_dfs["test"]
        print(f"\n{sep}")
        print("■ 4. M1失敗パターン分析（指数1位が1・2着を外した要因 / OOS-test）")
        print(sep)

        venue_targets = [None] + sorted(VENUE_GROUP_A | VENUE_GROUP_B)
        for vt in venue_targets:
            label = vt if vt else "全場"
            df_v = df_test[df_test["course_name"] == vt] if vt else df_test
            if df_v["race_id"].nunique() < 20:
                continue
            fail_df = failure_analysis(df_v)
            print(f"\n  [{label}] 指数1位サブ指数比較（成功=1・2着 vs 失敗=3着以下）")
            print(fail_df.to_string(index=False))

        print(f"\n{sep}")
        print("■ 4b. サブ指数四分位別 M1失敗率（OOS-test 全場）")
        print(sep)
        q_df = failure_by_subindex_quartile(df_test)
        for sub, g in q_df.groupby("サブ指数"):
            print(f"\n  {sub}:")
            print(g[["四分位", "n", "M1失敗率(%)"]].to_string(index=False))

        print(f"\n{sep}")
        print("■ 4c. 頭数帯別 M1成功率・勝率（OOS-test 全場）")
        print(sep)
        hc_df = failure_by_head_count(df_test)
        print(hc_df.to_string(index=False))

    # ═══════════════════════════════════════════════════════
    # 5. OOS-val / OOS-test の指標推移（月次）
    # ═══════════════════════════════════════════════════════
    for pname in ["val", "test"]:
        if pname not in period_dfs:
            continue
        df_p = period_dfs[pname]
        df_p = df_p.copy()
        df_p["ym"] = df_p["date"].astype(str).str[:6]
        race_df_p = compute_metrics(df_p, ranker="idx_rank", label="index")
        race_df_p["ym"] = race_df_p["date"].astype(str).str[:6]

        print(f"\n{sep}")
        print(f"■ 5. 月次推移（{pname}）— 指数M0/M1/M2/M3")
        print(sep)
        rows = []
        for ym, g in race_df_p.groupby("ym"):
            rows.append({
                "年月":        ym,
                "n":           len(g),
                "M0勝率%":     f"{g['m0_top1win'].mean()*100:.1f}",
                "M1(1・2着%)": f"{g['m1_top1in2'].mean()*100:.1f}",
                "M1複勝%":     f"{g['m1_top1in3'].mean()*100:.1f}",
                "M2(2頭%)":    f"{g['index_cover2'].mean()*100:.1f}",
                "M3(完全%)":   f"{g['index_cover3'].mean()*100:.1f}",
                "Spearman":    f"{g['index_spearman'].mean():.3f}",
            })
        print(pd.DataFrame(rows).to_string(index=False))

    # ═══════════════════════════════════════════════════════
    # 6. 優先場（グループA）の詳細（OOS-test）
    # ═══════════════════════════════════════════════════════
    if "test" in period_dfs:
        df_test = period_dfs["test"]
        print(f"\n{sep}")
        print("■ 6. グループA（南関東4場）個別詳細（OOS-test）")
        print(sep)
        for venue in sorted(VENUE_GROUP_A):
            df_v = df_test[df_test["course_name"] == venue]
            n_races = df_v["race_id"].nunique()
            if n_races < 10:
                print(f"  {venue}: レース数不足 ({n_races})")
                continue
            race_dv = compute_metrics(df_v, ranker="idx_rank", label="index")
            mkt_dv  = compute_metrics(df_v, ranker="pop_rank",  label="market")
            m0_i = race_dv["m0_top1win"].mean() * 100
            m1_i = race_dv["m1_top1in2"].mean() * 100
            m2_i = race_dv["index_cover2"].mean() * 100
            m3_i = race_dv["index_cover3"].mean() * 100
            rho_i = race_dv["index_spearman"].mean()
            m1_m = mkt_dv["m1_top1in2"].mean() * 100
            m1_ci = bootstrap_ci(race_dv["m1_top1in2"].dropna().values, n_boot=args.n_boot)
            print(
                f"  {venue}  n={n_races:,}レース\n"
                f"    M0(勝率)  指数={m0_i:.1f}%\n"
                f"    M1(1・2着) 指数={m1_i:.1f}% CI95=[{m1_ci[0]*100:.1f},{m1_ci[1]*100:.1f}]  市場={m1_m:.1f}%  差={m1_i-m1_m:.1f}pt\n"
                f"    M2(2頭一致)={m2_i:.1f}%  M3(完全)={m3_i:.1f}%  Spearman={rho_i:.3f}\n"
            )

    print(f"\n{sep}")
    print("■ 分析完了")
    print(f"  ・評価基準: val(OOS)={PERIODS['val'][0]}〜{PERIODS['val'][1]}  "
          f"test(純FW)={PERIODS['test'][0]}〜{PERIODS['test'][1]}")
    print(f"  ・インサンプル学習期間: 〜{TRAIN_END}（train期指標はバイアスあり・参照のみ）")
    print(sep)


if __name__ == "__main__":
    main()
