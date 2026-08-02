"""地方競馬（chihou）モデル特徴量の死活監視。

背景:
  JRA 側の `check_feature_health.py` は `keiba.calculated_indices` の列を直接見れば
  済んだが、地方の 44 特徴は **大半が DB 列ではなく算出時に生成される**
  （履歴系・外部指数・馬場・コーナー/調教師・市場乖離）。したがって
  「モデルが実際に見ている値」を検査するには学習パイプラインと同じ前処理を
  通してから測るしかない。本スクリプトは `train_chihou_market_lgb.prep` を
  そのまま再利用して 44 特徴の行列を組み、月次で健全性を検査する。

  JRA では `paddock_index` が学習期間を通して sd=0（モデル寄与ゼロ）だったことが
  v27 調査の副産物で発覚した。地方でも `sekito.netkeiba` のスクレイプが
  2026-05-10 に停止している（memory: netkeiba_scrape_stopped_2026_05）ため、
  `nk_idx_z` / `nk_rank_n` / `ext_missing` が同型の状態にある可能性が高い。

検出する異常:
  DEAD       : 直近 N ヶ月すべて sd < DEAD_SD_THRESHOLD（実質定数・モデルに寄与しない）
  SHIFT      : 直近月の sd が過去中央値から SHIFT_RATIO 倍以上乖離（分布レジーム変化）
  DEGENERATE : 欠損フォールバック値（0.5 / -1.0 / 0.0 等）が占める割合が高い
               ＝ 値は入っているが実質「情報なし」で埋まっている状態
  DECLINE    : フォールバック占有率が過去中央値から大きく増えた（上流の供給劣化）
               ※ 完全停止（DEGENERATE）の前段階を捕まえるためのもの。実際 kichiuma は
                  2026-06 に 95%→76% へ落ちたが占有率の絶対値では閾値に掛からなかった
  CONSTANT_1 : 0/1 フラグ特徴が片側に振り切っている

使い方:
    cd backend
    .venv/bin/python scripts/check_chihou_feature_health.py
    .venv/bin/python scripts/check_chihou_feature_health.py --start 20250101 --months 3
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
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

from scripts.train_chihou_market_lgb import ALL_FEATURES, fetch, prep  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_feature_health")

# 実質定数とみなす標準偏差の閾値
DEAD_SD_THRESHOLD = 0.01
# 直近月の sd が過去中央値の何倍/何分の一を超えたらレジーム変化とみなすか
SHIFT_RATIO = 3.0
# フォールバック値の占有率がこれを超えたら DEGENERATE
DEGENERATE_RATE = 0.90
# フォールバック占有率が基準より何ポイント増えたら DECLINE とみなすか
DECLINE_DELTA = 0.15
# 直近月として扱うのに必要な最低行数。これ未満は「まだ月の途中」とみなし判定から外す
MIN_ROWS_PER_MONTH = 3000
# 0/1 フラグが片側に振り切っているとみなす割合
FLAG_CONSTANT_RATE = 0.99

# 各特徴の「欠損フォールバック値」。prep 側の fillna と一致させること。
# None = フォールバック概念なし（実測値そのもの）
FALLBACK: dict[str, float | None] = {
    # 履歴系: add_historical_features 後に -1.0 で埋める（train_chihou_prod_lgb.prep）
    "improving_form": -1.0,
    "track_win_rate": -1.0,
    "class_drop_ratio": -1.0,
    "prev_pace_ratio": -1.0,
    # 外部指数: z は 0.0 / 順位は 0.5
    "kc_sp_z": 0.0,
    "nk_idx_z": 0.0,
    "kc_rank_n": 0.5,
    "nk_rank_n": 0.5,
    # 馬場・コーナー系
    "horse_wet_apt": 0.0,
    "horse_wet_apt_active": 0.0,
    "c_early_n": 0.5,
    "c_late_gain_n": 0.0,
    "c_makuri_n": 0.0,
    # 市場乖離
    "odds_rank_n": 0.5,
}

# 0/1 フラグ特徴（片側振り切りを検査する）
FLAG_FEATURES = {
    "is_turf", "is_dirt", "is_good", "is_heavy", "is_bad",
    "ext_missing", "jk_change", "is_heavy_fav", "is_dark_horse",
}

# 既知の問題（新規アラートではなく既知として報告する）
KNOWN_ISSUES: dict[str, str] = {
    "nk_idx_z": "上流 sekito.netkeiba スクレイプが 2026-05-10 に停止（memory: netkeiba_scrape_stopped_2026_05）",
    "nk_rank_n": "同上",
    # 障害ではなく地方競馬の構造。芝コースを持つのは盛岡だけで、開催の 99% 以上がダート。
    # 片側に振り切っているのが正常なので、CONSTANT_1 で騒がせない。
    "is_turf": "地方は芝コースが盛岡のみで開催の99%以上がダート。片側に寄るのが正常",
    "is_dirt": "同上",
}


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def build_matrix(conn, start: str, end: str) -> pd.DataFrame:
    """学習と同一の前処理で 44 特徴の行列を組む。"""
    logger.info(f"レース取得 {start}〜{end}")
    df_raw = fetch(conn, start, end)
    if df_raw.empty:
        return df_raw
    logger.info(f"  {len(df_raw):,} 行 / {df_raw['race_id'].nunique():,} レース")
    logger.info("履歴取得（全期間・特徴量算出に必要）")
    df_hist = fetch_hist(conn)
    logger.info("前処理（prep: 履歴→外部→馬場→コーナー/調教師→市場）")
    df = prep(conn, df_raw, df_hist)
    keep = ["race_id", "date", *ALL_FEATURES]
    df = df[[c for c in keep if c in df.columns]].copy()
    for c in ALL_FEATURES:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    df["ym"] = df["date"].astype(str).str.slice(0, 6)
    return df


def analyse(df: pd.DataFrame, months: int) -> tuple[pd.DataFrame, list[str], list[str]]:
    """月次 sd / フォールバック占有率を集計し、異常を判定する。"""
    yms = sorted(df["ym"].unique())
    rows = []
    for ym in yms:
        sub = df[df["ym"] == ym]
        rec: dict[str, float | str | int] = {"ym": ym, "n": len(sub)}
        for c in ALL_FEATURES:
            if c not in sub.columns:
                continue
            s = sub[c]
            rec[f"sd_{c}"] = float(s.std()) if len(s) > 1 else 0.0
            fb = FALLBACK.get(c)
            if fb is not None:
                rec[f"fb_{c}"] = float(np.isclose(s.to_numpy(), fb).mean())
            if c in FLAG_FEATURES:
                m = float(s.mean())
                rec[f"flag_{c}"] = max(m, 1.0 - m)
        rows.append(rec)
    stat = pd.DataFrame(rows)

    # 月の途中（行数が極端に少ない月）は判定から外す。
    # そのまま入れると「まだ数日分しかない月」の値で誤検知する。
    stat = stat[stat["n"] >= MIN_ROWS_PER_MONTH].reset_index(drop=True)
    if stat.empty:
        return stat, [], []

    alerts: list[str] = []
    known: list[str] = []
    tail = stat.tail(months)
    past = stat.iloc[:-months] if len(stat) > months else stat

    def baseline(col: str) -> float | None:
        """比較基準。12ヶ月以上あれば**前年同月**、無ければ過去中央値。

        `prev_pace_ratio` のように夏に上がり冬に下がる季節性を持つ特徴があり
        （2025年も2026年も同じ形）、過去中央値と比べると毎年夏に誤検知する。
        """
        cur_ym = str(stat["ym"].iloc[-1])
        prev_ym = f"{int(cur_ym[:4]) - 1}{cur_ym[4:]}"
        row = stat[stat["ym"] == prev_ym]
        if not row.empty:
            return float(row[col].iloc[0])
        return float(past[col].median()) if len(past) >= 3 else None

    for c in ALL_FEATURES:
        key_sd = f"sd_{c}"
        if key_sd not in stat.columns:
            continue
        msgs: list[str] = []
        recent_sd = tail[key_sd]

        if (recent_sd < DEAD_SD_THRESHOLD).all():
            msgs.append(f"DEAD       直近{months}ヶ月すべて sd<{DEAD_SD_THRESHOLD}（実質定数）")
        elif len(past) >= 3:
            med = float(past[key_sd].median())
            cur = float(recent_sd.iloc[-1])
            if med > DEAD_SD_THRESHOLD and (cur > med * SHIFT_RATIO or cur < med / SHIFT_RATIO):
                msgs.append(f"SHIFT      直近月 sd={cur:.4f} vs 過去中央値 {med:.4f}")

        key_fb = f"fb_{c}"
        if key_fb in stat.columns:
            fb_rate = float(tail[key_fb].max())
            if fb_rate > DEGENERATE_RATE:
                msgs.append(f"DEGENERATE 欠損フォールバック占有率 {fb_rate:.1%}（実質情報なし）")
            else:
                base = baseline(key_fb)
                fb_cur = float(tail[key_fb].iloc[-1])
                if base is not None and fb_cur - base > DECLINE_DELTA:
                    msgs.append(f"DECLINE    フォールバック占有率 {base:.1%}→{fb_cur:.1%}"
                                f"（前年同月比・上流の供給劣化）")

        key_flag = f"flag_{c}"
        if key_flag in stat.columns:
            fr = float(tail[key_flag].min())
            if fr > FLAG_CONSTANT_RATE:
                msgs.append(f"CONSTANT_1 フラグが片側に {fr:.1%} 振り切り")

        for m in msgs:
            line = f"{c:<24}{m}"
            (known if c in KNOWN_ISSUES else alerts).append(line)

    return stat, alerts, known


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None, help="検査開始日 YYYYMMDD（既定: 15ヶ月前）")
    p.add_argument("--end", default=None, help="検査終了日 YYYYMMDD（既定: 今日）")
    p.add_argument("--months", type=int, default=3, help="DEAD/DEGENERATE 判定に使う直近月数")
    p.add_argument("--show-monthly", action="store_true",
                   help="フォールバック占有率の月次推移を表示する（供給劣化の追跡用）")
    args = p.parse_args()

    today = date.today()
    end = args.end or today.strftime("%Y%m%d")
    if args.start:
        start = args.start
    else:
        y, m = today.year, today.month - 15
        while m <= 0:
            m += 12
            y -= 1
        start = f"{y}{m:02d}01"

    conn = connect()
    try:
        df = build_matrix(conn, start, end)
    finally:
        conn.close()

    if df.empty:
        logger.error("対象行がありません")
        return 1

    stat, alerts, known = analyse(df, args.months)

    print("\n" + "=" * 100)
    print(f"地方 特徴量ヘルスチェック  期間 {start}〜{end} / {len(ALL_FEATURES)}特徴 / 直近{args.months}ヶ月で判定")
    print("=" * 100)

    if known:
        print("\n[既知の問題]")
        for line in known:
            print("  " + line)
            name = line.split()[0]
            if name in KNOWN_ISSUES:
                print(f"      → {KNOWN_ISSUES[name]}")
    if alerts:
        print("\n[要対応]")
        for line in alerts:
            print("  " + line)
    else:
        print("\n[要対応] なし（既知の問題を除き全特徴量が健全）")

    if args.show_monthly:
        fb_cols = [c for c in ALL_FEATURES if f"fb_{c}" in stat.columns]
        print("\nフォールバック占有率の月次推移（上流供給の劣化追跡）:")
        print(f"  {'ym':<8}{'n':>7}" + "".join(f"{c[:11]:>13}" for c in fb_cols))
        for _, row in stat.iterrows():
            print(f"  {row['ym']:<8}{int(row['n']):>7}"
                  + "".join(f"{float(row[f'fb_{c}']):>12.1%} " for c in fb_cols))

    last = stat.iloc[-1]
    print(f"\n直近月 {last['ym']} (n={int(last['n']):,}) の特徴量サマリ:")
    print(f"  {'feature':<24}{'sd':>10}{'fallback%':>12}")
    for c in ALL_FEATURES:
        if f"sd_{c}" not in stat.columns:
            continue
        sd = float(last[f"sd_{c}"])
        fb = last.get(f"fb_{c}")
        fb_s = f"{float(fb):>11.1%}" if fb is not None and not pd.isna(fb) else " " * 12
        flag = "  ← DEAD" if sd < DEAD_SD_THRESHOLD else ""
        print(f"  {c:<24}{sd:>10.4f}{fb_s}{flag}")

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
