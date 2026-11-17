"""v29 の指数を全期間バックフィルする（`version = 29` の行として保存）。

v29（2026-11-07）: 単勝ヘッド・複勝の独立ヘッドを 38列 → **45列**（ラップ7列を追加）。
`composite_index` / `out_probability` は v27・v28 と同じ。それ以外の構造・注意点は
`inference_v28.py`（このファイルの元）と同一なので、そちらの docstring も読むこと。

    composite_index   = blend_v27(reg_rank, out_prob)   … 🔴 変更なし
    out_probability   = 着外率ヘッド（34列）            … 🔴 変更なし
    win_probability   = v29 単勝ヘッド（45列）のレース内正規化（Σ=1）
    place_probability = v29 独立 is_placed ヘッド（45列）→ Σp = place_slots

冪等: 対象期間の `version = 29` 行を削除してから挿入する。
🔴 `--limit-races` を指定すると DB へ書き込まない（`inference_v28.py` と同じ安全策）。

## 🔴 本番 `composite.py` と同じ道を通す

- 34列は `train_jra_out_rate.featurize`
- 過去走4列は `train_jra_iswin_head.attach_past_form`（`past_form` の一括入口）
- ラップ7列は `train_jra_iswin_head.attach_lap_form`（`lap_form.build_lap_context` →
  `lap_features_for_horses`。配信の `build_lap_form_features_for_race` と同じ組み立て）
- 正規化は `composite.normalize_place_to_slots`、`place_slots` はフィールドの馬数から

⚠️ 本番モデルは `TRAIN_DATA_END` まで学習しているので、それ以前のレースの値は in-sample。
過去の ROI・的中率の評価に使わないこと。

使い方:
    cd backend
    .venv/bin/python scripts/inference_v29.py --dry-run --limit-races 3
    .venv/bin/python scripts/inference_v29.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from scripts.jra_prob_scoring import JRA_COURSES  # noqa: E402
from scripts.train_jra_iswin_head import attach_lap_form, attach_past_form, dsn  # noqa: E402
from scripts.train_jra_out_rate import featurize as prod_featurize  # noqa: E402
from src.indices.composite import (  # noqa: E402
    COMPOSITE_VERSION,
    OUT_PROB_FEATURE_NAMES,
    SUBINDEX_SOURCE_SQL,
    V29_FEATURE_NAMES,
    CompositeIndexCalculator,
    blend_v27,
    normalize_place_to_slots,
    place_slots_for_field,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inference_v29")

MODELS_DIR = _root / "models"
REG_RANK_MODEL = MODELS_DIR / "jra_reg_rank_lgb.txt"  # v27 から変更なし
OUT_RATE_MODEL = MODELS_DIR / "jra_out_rate_lgb.txt"  # v27 から変更なし
ISWIN_MODEL = MODELS_DIR / "v29_iswin_calib.txt"  # v29（45列）
PLACED_MODEL = MODELS_DIR / "v29_placed_head.txt"  # v29（45列・独立ヘッド）

# v29 行にコピーするサブ指数（DB 列名）
SUBINDEX_COLUMNS = [
    "speed_index",
    "last_3f_index",
    "course_aptitude",
    "position_advantage",
    "rotation_index",
    "jockey_index",
    "pace_index",
    "pedigree_index",
    "training_index",
    "anagusa_index",
    "paddock_index",
    "rebound_index",
    "rivals_growth_index",
    "career_phase_index",
    "distance_change_index",
    "jockey_trainer_combo_index",
    "going_pedigree_index",
]

FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, ci.race_id, ci.horse_id, re.horse_number,
    {", ".join("ci." + c for c in SUBINDEX_COLUMNS)},
    r.distance, r.head_count, r.surface, r.condition, r.grade, r.course,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN {JRA_COURSES}
ORDER BY ci.race_id, ci.horse_id
"""

INSERT_SQL = f"""
INSERT INTO keiba.calculated_indices
  (race_id, horse_id, version, {", ".join(SUBINDEX_COLUMNS)},
   composite_index, win_probability, place_probability, out_probability, calculated_at)
VALUES %s
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--sleep", type=float, default=0.2, help="バッチ間スリープ秒（VPS DB 負荷対策）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--limit-races",
        type=int,
        default=0,
        help="先頭 N レースだけ処理する（目視確認用・0 で無制限）。"
        "🔴 指定すると DB 書き込みを行わない（--dry-run 相当）",
    )
    args = p.parse_args()

    # 🔴 DELETE は --start/--end の全範囲を対象にするので、--limit-races と
    #    書き込みを併用すると v29 世代がほぼ全消しになる。書き込ませない（docstring 参照）
    if args.limit_races and not args.dry_run:
        logger.warning(
            "--limit-races は目視確認用なので DB 書き込みを行わない（--dry-run を強制）。"
            "部分バックフィルは --start / --end で期間を切ること"
        )
        args.dry_run = True

    for path in (REG_RANK_MODEL, OUT_RATE_MODEL, ISWIN_MODEL, PLACED_MODEL):
        if not path.exists():
            raise SystemExit(f"モデルが見つかりません: {path}")
    reg_model = lgb.Booster(model_file=str(REG_RANK_MODEL))
    out_model = lgb.Booster(model_file=str(OUT_RATE_MODEL))
    iswin_model = lgb.Booster(model_file=str(ISWIN_MODEL))
    placed_model = lgb.Booster(model_file=str(PLACED_MODEL))

    conn = psycopg2.connect(dsn())
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    logger.info(f"対象: {len(df):,}行 / {df['race_id'].nunique():,}レース")
    if df.empty:
        return
    df["date"] = df["date"].astype(str)
    if args.limit_races:
        keep = df["race_id"].drop_duplicates().head(args.limit_races)
        df = df[df["race_id"].isin(keep)].reset_index(drop=True)
        logger.info(f"--limit-races={args.limit_races} → {len(df):,}行")

    # 🔴 母集団のフィルタ（build_population）は掛けない。ここは**配信の再現**であり、
    #    本番 `calculate_and_save` は race_entries の全馬に対して行を書くため。
    df = prod_featurize(df)
    df = attach_past_form(df, conn, end=args.end)
    df = attach_lap_form(df, conn, end=args.end)  # 🔴 v29 のラップ7列

    X34 = df[OUT_PROB_FEATURE_NAMES].to_numpy(dtype=float)
    X45 = df[V29_FEATURE_NAMES].to_numpy(dtype=float)
    df["_reg"] = reg_model.predict(X34)
    df["_out"] = np.clip(out_model.predict(X34), 0.0, 1.0)
    df["_win_raw"] = np.clip(iswin_model.predict(X45), 1e-9, 1.0)
    df["_placed_raw"] = np.clip(placed_model.predict(X45), 1e-9, 1.0 - 1e-9)

    # レース単位で合成・確率化（本番 composite.py と同じ処理）
    records: list[tuple] = []
    n_harville_fallback = 0
    for _, g in df.groupby("race_id", sort=False):
        comps = blend_v27(g["_reg"].to_numpy(), g["_out"].to_numpy())
        raw_w = g["_win_raw"].to_numpy(dtype=float)
        total = float(raw_w.sum())
        win_p = (
            [float(x) for x in raw_w / total]
            if total > 0
            else CompositeIndexCalculator._softmax([float(c) for c in comps])
        )
        # 🔴 place_slots は **そのレースのフィールドの馬数**から。head_count は使わない
        slots = place_slots_for_field(len(g))
        place_p = normalize_place_to_slots(g["_placed_raw"].to_numpy(dtype=float), slots)
        if not place_p:  # n < 5（複勝の発売なし）→ 本番と同じく Harville へ落とす
            place_p = CompositeIndexCalculator._harville_place_probs(win_p)
            n_harville_fallback += 1
        for i, (_, row) in enumerate(g.iterrows()):
            sub = [None if pd.isna(row[c]) else float(row[c]) for c in SUBINDEX_COLUMNS]
            records.append(
                (
                    int(row["race_id"]),
                    int(row["horse_id"]),
                    COMPOSITE_VERSION,
                    *sub,
                    float(comps[i]),
                    round(float(win_p[i]), 4),
                    round(float(place_p[i]), 4),
                    round(float(row["_out"]), 4),
                )
            )

    logger.info(
        f"算出完了: {len(records):,}行  "
        f"composite平均={np.mean([r[3 + len(SUBINDEX_COLUMNS)] for r in records]):.2f}  "
        f"Harville フォールバック(5頭未満)={n_harville_fallback}レース"
    )
    _visual_check(df)

    if args.dry_run:
        logger.info("dry-run のため DB 更新はスキップ")
        return

    # 冪等性のため対象期間の v29 行を先に削除
    cur.execute(
        """
        DELETE FROM keiba.calculated_indices ci
        USING keiba.races r
        WHERE r.id = ci.race_id AND ci.version = %(ver)s
          AND r.date >= %(start)s AND r.date <= %(end)s
        """,
        {"ver": COMPOSITE_VERSION, "start": args.start, "end": args.end},
    )
    logger.info(f"既存 v{COMPOSITE_VERSION} 行を削除: {cur.rowcount:,}行")
    conn.commit()

    template = "(" + ",".join(["%s"] * (3 + len(SUBINDEX_COLUMNS) + 4)) + ", NOW())"
    total_ins = 0
    for i in range(0, len(records), args.batch_size):
        batch = records[i : i + args.batch_size]
        execute_values(cur, INSERT_SQL, batch, template=template)
        conn.commit()
        total_ins += len(batch)
        if (i // args.batch_size) % 5 == 0:
            logger.info(f"  挿入 {total_ins:,}/{len(records):,}")
        if args.sleep:
            time.sleep(args.sleep)
    logger.info(f"挿入完了: {total_ins:,}行 (version={COMPOSITE_VERSION})")
    cur.close()
    conn.close()


def _visual_check(df: pd.DataFrame) -> None:
    """🔴 実データを1レース表示して目視確認する（`CLAUDE.md` 検証の作法）。

    ⚠️ 本ループ（`main`）と**同じ Harville フォールバック**を持つこと。
    5頭未満（`slots == 0`）のレースが範囲の先頭に来ると
    `normalize_place_to_slots` が `[]` を返し、`place_p[i]` で IndexError になる。
    """
    g = df[df["race_id"] == df["race_id"].iloc[0]].sort_values("horse_number")
    slots = place_slots_for_field(len(g))
    raw_w = g["_win_raw"].to_numpy(dtype=float)
    total_w = float(raw_w.sum())
    win_p = raw_w / total_w if total_w > 0 else np.full(len(g), 1.0 / max(1, len(g)))
    place_p = normalize_place_to_slots(g["_placed_raw"].to_numpy(dtype=float), slots)
    if not place_p:  # n < 5（複勝の発売なし）→ 本ループと同じく Harville へ落とす
        place_p = CompositeIndexCalculator._harville_place_probs([float(x) for x in win_p])
    print("\n" + "=" * 112)
    print(f"🔴 目視確認 race_id={int(g.iloc[0]['race_id'])} {g.iloc[0]['date']} " f"n={len(g)} place_slots={slots}")
    print("=" * 112)
    print(
        f"{'馬番':>4}{'脚質ord':>9}{'着順分散5':>11}{'勝複比5':>10}{'pace_pit':>10}"
        f"{'p_win':>11}{'raw_placed':>12}{'p_place':>11}{'out_prob':>10}"
    )

    def _f(v: object, w: int, dg: int = 3) -> str:
        fv = float(v)  # type: ignore[arg-type]
        return f"{'NaN':>{w}}" if np.isnan(fv) else f"{fv:>{w}.{dg}f}"

    for i, (_, r) in enumerate(g.iterrows()):
        print(
            f"{int(r['horse_number']):>4}{_f(r['runner_type_ord'], 9)}"
            f"{_f(r['finish_var5'], 11)}{_f(r['win_place_ratio5'], 10)}"
            f"{_f(r['pace_handicap_pit'], 10)}{win_p[i]:>11.5f}"
            f"{float(r['_placed_raw']):>12.5f}{place_p[i]:>11.5f}"
            f"{float(r['_out']):>10.4f}"
        )
    print(
        f"{'Σ':>4}{'':>9}{'':>11}{'':>10}{'':>10}{win_p.sum():>11.5f}"
        f"{g['_placed_raw'].sum():>12.5f}{sum(place_p):>11.5f}"
    )
    if slots > 0:
        print(f"（期待: Σp_win=1.00000 / Σp_place={slots}.00000 / raw_placed だけはずれてよい）")
    else:
        print(
            f"（🔴 n={len(g)} < 5 で複勝の発売なし ⇒ p_place は Harville フォールバック"
            f"（本ループと同じ）。期待: Σp_win=1.00000）"
        )


if __name__ == "__main__":
    main()
