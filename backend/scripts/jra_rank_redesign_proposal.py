"""JRA 指数リデザイン案の定量検証（上位・下位を分離して並べる合成 + 再学習頻度）

`jra_rank_quality_review.py` の結果を受けた 2 つの検証:

【検証1】上位/下位を分離した合成スコアの比較
  base(全体順位) を土台に、上位は 3着内ヘッド、下位は着外率ヘッドで補正する:
      score = z(base) + a * z(p_top3) - b * z(p_out)
  base は reg_rank（レース内正規化着順の回帰）と lambdarank の 2 系統で試す。

【検証2】モデル vintage（再学習頻度）の効果
  同じ test 窓（2026-03〜）に対して
    - 旧 vintage: train ≤2025-06
    - 新 vintage: train ≤2025-12
  の 2 本を当て、直近データを取り込むことでランキング品質が戻るかを見る。
  （本番 v26 は 2025-06 までで学習されたまま。2026-06/07 に品質劣化が観測されている）

使い方:
    cd backend
    .venv/bin/python scripts/jra_rank_redesign_proposal.py
"""

from __future__ import annotations

import json
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.jra_rank_quality_review import (  # noqa: E402
    FETCH_SQL,
    evaluate,
    featurize,
    train_binary,
    train_lambdarank,
    train_reg_rank,
    zscore_in_race,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rank_redesign")

MODELS_DIR = _root / "models"
SEEDS = [42, 123, 456]
KEYS = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3",
        "TAIL_bot3_out_rate", "TAIL_placer_in_bot30pct", "ALL_spearman"]


def load_all() -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL)
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()
    df = featurize(df)
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df[df["composite_index"].notna()]
    return df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)


def fit_scores(df: pd.DataFrame, train_end: str, valid_end: str,
               test_start: str) -> pd.DataFrame:
    """指定 vintage で 4 スコアを学習し test に付与して返す。"""
    tr = df[df["date"] <= train_end]
    va = df[(df["date"] > train_end) & (df["date"] <= valid_end)]
    te = df[df["date"] > test_start].copy().reset_index(drop=True)
    logger.info(f"vintage(train<={train_end}) train={len(tr):,} valid={len(va):,} "
                f"test={len(te):,}/{te.race_id.nunique():,}R ({te.date.min()}〜{te.date.max()})")
    te["p_top3"] = train_binary(tr, va, te, (tr.finish_position <= 3).astype(int),
                                (va.finish_position <= 3).astype(int), SEEDS)
    te["p_out"] = train_binary(tr, va, te, (tr.finish_position >= 6).astype(int),
                               (va.finish_position >= 6).astype(int), SEEDS)
    te["reg_rank"] = train_reg_rank(tr, va, te, SEEDS)
    te["lambdarank"] = train_lambdarank(tr, va, te, SEEDS)
    for c in ("p_top3", "p_out", "reg_rank", "lambdarank"):
        te[f"z_{c}"] = zscore_in_race(te, c)
    return te


def table(title: str, rows: dict[str, dict]) -> None:
    print("\n" + "=" * 122)
    print(title)
    print("=" * 122)
    print(f"{'候補':<34}" + "".join(f"{k.split('_', 1)[1][:17]:>17}" for k in KEYS))
    for name, m in rows.items():
        print(f"{name:<34}" + "".join(f"{m[k]:>17.4f}" for k in KEYS))


def main() -> None:
    df = load_all()
    report: dict = {}

    # ---------------- 検証1: 合成スコア ----------------
    te = fit_scores(df, "20250630", "20251231", "20251231")
    rows: dict[str, dict] = {"prod_composite (現行)": evaluate(te, "composite_index")}
    for base in ("reg_rank", "lambdarank"):
        rows[f"{base} 単体"] = evaluate(te, base)
        for a in (0.0, 0.3, 0.5):
            for b in (0.3, 0.5, 0.8):
                te["_s"] = te[f"z_{base}"] + a * te["z_p_top3"] - b * te["z_p_out"]
                rows[f"{base} +{a:.1f}*top3 -{b:.1f}*out"] = evaluate(te, "_s")
    table("【検証1】上位/下位を分離した合成  test = 2026-01〜（honest・train≤2025-06）", rows)
    report["blend_2026"] = rows

    # 上位3件を要約
    best_head = max(rows.items(), key=lambda kv: kv[1]["HEAD_ndcg3"])
    best_tail = min(rows.items(), key=lambda kv: kv[1]["TAIL_placer_in_bot30pct"])
    best_all = max(rows.items(), key=lambda kv: kv[1]["ALL_spearman"])
    print(f"\n  HEAD最良(ndcg3): {best_head[0]} = {best_head[1]['HEAD_ndcg3']:.4f}"
          f"   （現行 {rows['prod_composite (現行)']['HEAD_ndcg3']:.4f}）")
    print(f"  TAIL最良(placer_in_bot30): {best_tail[0]} = {best_tail[1]['TAIL_placer_in_bot30pct']:.4f}"
          f"   （現行 {rows['prod_composite (現行)']['TAIL_placer_in_bot30pct']:.4f}）")
    print(f"  ALL最良(spearman): {best_all[0]} = {best_all[1]['ALL_spearman']:.4f}"
          f"   （現行 {rows['prod_composite (現行)']['ALL_spearman']:.4f}）")

    # ---------------- 検証2: vintage ----------------
    # 同一 test 窓（2026-03〜）で 旧/新 vintage を比較する
    old = fit_scores(df, "20250630", "20251231", "20260228")
    new = fit_scores(df, "20251231", "20260228", "20260228")
    # 新 vintage は valid に 2026-01〜02 を使うため、test は 2026-03 以降のみ
    v_rows = {
        "prod_composite (train≤2025-06)": evaluate(old, "composite_index"),
        "reg_rank 旧vintage(≤2025-06)": evaluate(old, "reg_rank"),
        "reg_rank 新vintage(≤2025-12)": evaluate(new, "reg_rank"),
        "blend 旧vintage": evaluate(
            old.assign(_s=old.z_reg_rank + 0.3 * old.z_p_top3 - 0.5 * old.z_p_out), "_s"),
        "blend 新vintage": evaluate(
            new.assign(_s=new.z_reg_rank + 0.3 * new.z_p_top3 - 0.5 * new.z_p_out), "_s"),
    }
    table("【検証2】モデル vintage の効果  test = 2026-03〜（同一窓で比較）", v_rows)
    report["vintage_2026H2"] = v_rows

    out = MODELS_DIR / "jra_rank_redesign_proposal.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
