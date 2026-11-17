"""着順分布（予想フロー再設計 Phase 2）の入力となる walk-forward 予測を VAL の終わりまで伸ばす。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Phase 2 / §5.4。

Wave 0c の予測（`jra_place_head_ab.py --pred-cache`）は 2024Q1〜2026Q2 で止まっている。
あのスクリプトは事前登録（`jra_winplace_structure_plan_2026_09_04.md` §12.2）で
`TEST_START=20260701` 以降を使わないガードを持ち、**そのガードは緩めない**。
本計画は VAL（`jra_protocol.VAL_END` まで）で判定するので、足りない四半期だけを
同じ手順（`jra_place_head_ab.run_walk_forward` をそのまま import）で作って継ぎ足す。

## 再現の確認

walk-forward の各四半期は「その四半期より前のデータ」だけで決まる。よって既存の
キャッシュと同じ四半期（既定 2026Q2）をもう一度作り、`p_win` / `p_top3_raw` が
一致することを確かめてから継ぎ足す。一致しなければ（DB が当時から変わった等）
継ぎ足しはせず、全四半期を作り直すよう促して止まる。

## 2026Q3 について

2026Q3 は v28 の一度きり評価（CLAUDE.md・計画書 §18・648R）に使った四半期で、
四半期ローリングの現在は VAL に入っている。出力 JSON に必ずその旨を残す
（未使用の窓と誤解されないように）。

## 使い方

    ~/bin/kiseki-ro -C backend .venv/bin/python scripts/jra_finish_dist_preds.py \\
        --base-preds <dir>/place_head_preds.pkl --cache <dir>/finish_dist_ds.pkl \\
        --out <dir>/finish_dist_preds.pkl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.jra_place_head_ab import FEAT_COLS, run_walk_forward  # noqa: E402
from scripts.jra_winplace_feature_ab import build_dataset, parse_quarters  # noqa: E402
from src import jra_protocol  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("finish_dist_preds")

KEY = ["race_id", "horse_id"]
PRED_COLS = ["p_win", "p_top3_raw"]
REPRO_TOL = 1e-9
BURNED_NOTE = (
    "2026Q3 は v28 の一度きり評価（648R・CLAUDE.md / jra_winplace_structure_plan §18）に使った。"
    "四半期ローリングで現在は VAL に入っているが、未使用の窓ではない"
)


def compare_preds(ref: pd.DataFrame, new: pd.DataFrame) -> dict:
    """同じ四半期の予測が一致するかを見る（行の集合と値の両方）。"""
    a = ref.set_index(KEY)[PRED_COLS]
    b = new.set_index(KEY)[PRED_COLS]
    common = a.index.intersection(b.index)
    out: dict = {
        "n_ref": int(len(a)),
        "n_new": int(len(b)),
        "n_common": int(len(common)),
    }
    for c in PRED_COLS:
        out[f"max_abs_dev_{c}"] = float(np.abs(a.loc[common, c].to_numpy() - b.loc[common, c].to_numpy()).max())
    out["match"] = bool(
        len(common) == len(a) == len(b) and all(out[f"max_abs_dev_{c}"] <= REPRO_TOL for c in PRED_COLS)
    )
    return out


def main() -> None:
    """足りない四半期の予測を作り、既存のキャッシュへ継ぎ足して保存する。"""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-preds", required=True, help="既存の予測（jra_place_head_ab の --pred-cache）")
    p.add_argument("--quarters", default="2026Q3..2026Q3", help="継ぎ足す四半期")
    p.add_argument("--repro-quarter", default="2026Q2", help="再現を確かめる既存の四半期")
    p.add_argument("--seeds", default="42,123,456", help="jra_place_head_ab の既定と同じにする")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--cache", default=None, help="データセット pickle")
    p.add_argument("--out", required=True, help="継ぎ足した予測の pickle")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    new_qs = parse_quarters(args.quarters)
    repro_qs = parse_quarters(f"{args.repro_quarter}..{args.repro_quarter}")

    # 🔴 本計画の判定は VAL の中だけ。TEST（jra_protocol.TEST_START 以降）は使わない
    eval_end = max(q[2] for q in new_qs)
    if eval_end > jra_protocol.VAL_END:
        raise SystemExit(f"🔴 評価終端 {eval_end} が VAL_END={jra_protocol.VAL_END} を越える")

    base = pd.read_pickle(args.base_preds)
    have = set(base["quarter"].unique())
    dup = [q[0] for q in new_qs if q[0] in have]
    if dup:
        raise SystemExit(f"既存の予測に同じ四半期がある: {dup}")
    if repro_qs[0][0] not in have:
        raise SystemExit(f"再現確認の四半期 {repro_qs[0][0]} が既存の予測に無い")

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = build_dataset(args.data_start, eval_end)
        if cache:
            df.to_pickle(cache)
    missing = [c for c in FEAT_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"データセットに `feat` 特徴が足りない: {missing}")
    # 🔴 2重ガード: VAL の外の行を落とす
    n0 = len(df)
    df = df[df["date"] <= jra_protocol.VAL_END].reset_index(drop=True)
    logger.info("VAL_END=%s より後を %d行 除外 / 母集団 %d行", jra_protocol.VAL_END, n0 - len(df), len(df))

    t0 = time.time()
    rep = run_walk_forward(df, repro_qs, seeds, args.valid_days)
    repro = compare_preds(base[base["quarter"] == repro_qs[0][0]], rep)
    logger.info("再現の確認 %s: %s (%.0fs)", repro_qs[0][0], repro, time.time() - t0)
    if not repro["match"]:
        raise SystemExit(
            "🔴 既存の予測を再現できない（DB か手順が当時と違う）。継ぎ足さずに止める。"
            "全四半期を同じデータセットで作り直すこと"
        )

    ev_new = run_walk_forward(df, new_qs, seeds, args.valid_days)
    out = pd.concat([base, ev_new.reindex(columns=base.columns)], ignore_index=True)
    pd.to_pickle(out, args.out)
    meta = {
        "base_preds": str(args.base_preds),
        "added_quarters": [q[0] for q in new_qs],
        "repro_check": {"quarter": repro_qs[0][0], **repro},
        "val_end": jra_protocol.VAL_END,
        "test_start": jra_protocol.TEST_START,
        "note_2026q3": BURNED_NOTE,
        "n_rows_by_quarter": {str(k): int(v) for k, v in out["quarter"].value_counts().sort_index().items()},
    }
    Path(args.out).with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    logger.info("保存: %s (%d行) / %s", args.out, len(out), meta["n_rows_by_quarter"])


if __name__ == "__main__":
    main()
