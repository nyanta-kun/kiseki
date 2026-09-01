"""指数（モデル特徴量）の死活監視。

背景（2026-08-02 のランキング品質レビューで発覚）:
  `paddock_index` は **v26 の学習期間 2023-05〜2025-06 の全月で sd=0（全馬 50 の定数）**
  だった。つまり 34 特徴のうち 1 つが死んだまま学習・運用されていたのに、
  誰も気付ける仕組みが無かった。さらに 2026-05 以降は上流の netkeiba スクレイプ自体が
  停止し、`sekito.netkeiba.is_paddock` が 0 件になっている。
  `anagusa_index` も 2023年は sd 0.7〜0.9 とほぼ定数で、2024-01 から 4〜5 に急変している。

本スクリプトは各特徴量の月次ばらつき・欠損率を集計し、
  - DEAD  : 直近 N ヶ月すべて sd < DEAD_SD_THRESHOLD（＝実質定数・モデルに寄与しない）
  - SHIFT : 直近月の sd が過去中央値から大きく外れた（分布レジーム変化）
  - SPARSE: 欠損率が高い
を検出して非ゼロ終了する。日次バッチ・CI から呼ぶことを想定。

使い方:
    cd backend
    .venv/bin/python scripts/check_feature_health.py
    .venv/bin/python scripts/check_feature_health.py --months 6 --version 27
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("feature_health")

# 監視対象（calculated_indices の列名）
SUBINDEX_COLUMNS = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]

# 実質定数とみなす標準偏差の閾値
DEAD_SD_THRESHOLD = 0.05
# 直近月の sd が過去中央値の何倍/何分の一を超えたらレジーム変化とみなすか
SHIFT_RATIO = 3.0
# 欠損率がこれを超えたら SPARSE
SPARSE_NULL_RATE = 0.20

# 既知の劣化（新規アラートとして扱わず、既知として報告する）
# 🔴 ここは「無視してよい」ではなく **「原因が分かっていて、除去しないと決めた」** 一覧。
#    自動監視（scripts/feature_health_weekly.sh）は [要対応] があるときだけ exit 1 を返す。
#    既知の問題を [要対応] に残したままにすると監視が常時赤になり、
#    **本当に新しい異常が出たときに気づけなくなる**（2026-09-01 に自動化した際、
#    検出3件すべてが既知で毎回赤になることが分かったため整理した）。
#    ⚠️ 逆に、原因未特定のものをここへ入れて黙らせないこと。
KNOWN_ISSUES = {
    "paddock_index": (
        "上流 sekito.netkeiba のスクレイプが 2026-05 に停止し is_paddock が 0 件。"
        " v26 学習期間中も全月 sd=0 だったためモデル寄与は元々ゼロ。"
        " memory: netkeiba_scrape_stopped_2026_05 / jra_rank_quality_redesign_2026_08_02"
    ),
    "going_pedigree_index": (
        "**レース当日に算出すると必ず全馬 50** になる（算出時点で races.condition が"
        " 未確定＝重/不でないため早期 return する）。値が入るのは後日バックフィルした"
        " レースだけなので、当日ぶんが定数なのは構造上の正常。"
        " 除去しても改善しないことを VAL 3,450R の paired bootstrap で確認済み"
        "（有意差なし・docs/jra_rebuild_2026_08.md 15.1）。"
    ),
    "rebound_index": (
        "2026-04 以降ばらつきが単調に減少している（sd 2.79→0.57）。"
        " going_pedigree_index と同じ検定で、除去しても改善しないことを確認済み"
        "（docs/jra_rebuild_2026_08.md 15.1）。"
        " 🔴 「配信時に定数だから外すべき」は成り立たない——LightGBM は定数入力を単に無視する。"
    ),
}


def fetch(version: int, months: int) -> pd.DataFrame:
    cols = ",\n".join(
        f"  stddev(ci.{c})::numeric(10,4) AS sd_{c},\n"
        f"  avg(CASE WHEN ci.{c} IS NULL THEN 1.0 ELSE 0.0 END)::numeric(6,4) AS null_{c}"
        for c in SUBINDEX_COLUMNS
    )
    sql = f"""
    SELECT substr(r.date, 1, 6) AS ym, count(*) AS n,
{cols}
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    WHERE ci.version = %(ver)s
    GROUP BY 1 ORDER BY 1
    """
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(sql, {"ver": version})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()
    return df.tail(months + 12).reset_index(drop=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, default=None,
                   help="calculated_indices.version（既定: COMPOSITE_VERSION）")
    p.add_argument("--months", type=int, default=3, help="DEAD 判定に使う直近月数")
    args = p.parse_args()

    if args.version is None:
        from src.indices.composite import COMPOSITE_VERSION
        args.version = COMPOSITE_VERSION

    df = fetch(args.version, args.months)
    if df.empty:
        logger.error(f"version={args.version} の行がありません")
        return 1
    logger.info(f"version={args.version} / {df['ym'].iloc[0]}〜{df['ym'].iloc[-1]} を検査")

    alerts: list[str] = []
    known: list[str] = []
    for c in SUBINDEX_COLUMNS:
        sd = pd.to_numeric(df[f"sd_{c}"], errors="coerce")
        nullrate = pd.to_numeric(df[f"null_{c}"], errors="coerce")
        recent = sd.tail(args.months)
        past = sd.iloc[:-args.months] if len(sd) > args.months else sd

        msgs: list[str] = []
        if recent.notna().all() and (recent < DEAD_SD_THRESHOLD).all():
            msgs.append(f"DEAD  直近{args.months}ヶ月すべて sd<{DEAD_SD_THRESHOLD}（実質定数）")
        elif len(past.dropna()) >= 3 and recent.notna().any():
            med = float(past.median())
            cur_sd = float(recent.iloc[-1])
            if med > DEAD_SD_THRESHOLD and (
                cur_sd > med * SHIFT_RATIO or cur_sd < med / SHIFT_RATIO
            ):
                msgs.append(f"SHIFT 直近月 sd={cur_sd:.3f} vs 過去中央値 {med:.3f}")
        if nullrate.tail(args.months).max() is not None and \
                float(nullrate.tail(args.months).max() or 0) > SPARSE_NULL_RATE:
            msgs.append(f"SPARSE 欠損率 {float(nullrate.tail(args.months).max()):.1%}")

        for m in msgs:
            line = f"{c:<32}{m}"
            (known if c in KNOWN_ISSUES else alerts).append(line)

    print("\n" + "=" * 100)
    print(f"特徴量ヘルスチェック (version={args.version}, 直近{args.months}ヶ月)")
    print("=" * 100)
    if known:
        print("\n[既知の問題]")
        for line in known:
            print("  " + line)
            key = line.split()[0]
            if key in KNOWN_ISSUES:
                print(f"      → {KNOWN_ISSUES[key]}")
    if alerts:
        print("\n[要対応]")
        for line in alerts:
            print("  " + line)
    else:
        print("\n[要対応] なし（既知の問題を除き全特徴量が健全）")

    # 直近月の sd 一覧（目視確認用）
    last = df.iloc[-1]
    print(f"\n直近月 {last['ym']} の sd:")
    for c in SUBINDEX_COLUMNS:
        v = last[f"sd_{c}"]
        flag = " ← DEAD" if v is not None and float(v) < DEAD_SD_THRESHOLD else ""
        print(f"  {c:<32}{float(v or 0):>8.3f}{flag}")

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
