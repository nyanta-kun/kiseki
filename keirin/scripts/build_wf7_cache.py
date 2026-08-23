#!/usr/bin/env python3
"""7車の **vintage walk-forward 予測**から §24 用キャッシュを作る（2026-08-23・§31）。

## なぜ要るのか

§24〜§29 が使っている `data/exp/trio_rank_cache.jsonl` / `tf_shape_cache4.jsonl` の
`p3` は **`wt_entries.pred_top3_pct` と 100% 一致**する（実測 28,000行）。この列は
**2026-07-19 に追加され過去分は後から backfill された**もので、そのレースより未来を
知っているモデルの出力＝ **model-vintage look-ahead**（`open_tasks_register` R-6）。
vintage 予測 `data/exp_cache/wf_preds_*.pkl` とは **6.2% しか一致しない**。

両腕が同じ p3 を見るので比較の対称性は保たれるが、**同時確率モデルは p3 を
特徴量として使う**ので、漏れた情報を学習しうる。§24 を本番へ入れる前に
**出所だけを差し替えて**測り直すために、同じ jsonl 形式で vintage 版を作る。

🔴 **ハーネス（`exp_trio_joint_partner.py`）は一切変えない。** 変えるのは入力だけ。

出力（`load_any` が読める形式）:
    data/exp/trio7_cache_wf_train.jsonl   … 2024-07-01 〜 2025-12-31
    data/exp/trio7_cache_wf_test.jsonl    … 2026-01-01 〜 2026-08-04
"""
from __future__ import annotations

import argparse
import glob
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402

WF_GLOB = "data/exp_cache/wf_preds_*.pkl"
SPLIT = "2026-01-01"
NE = 7


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-out", default="data/exp/trio7_cache_wf_train.jsonl")
    ap.add_argument("--test-out", default="data/exp/trio7_cache_wf_test.jsonl")
    ap.add_argument("--split", default=SPLIT)
    args = ap.parse_args()

    p3_by: dict[str, dict[int, float]] = defaultdict(dict)
    files = sorted(glob.glob(WF_GLOB))
    if not files:
        print(f"[error] {WF_GLOB} が見つからない（worktree なら data/exp_cache の "
              f"symlink が要る）", file=sys.stderr)
        return 1
    for f in files:
        d = pickle.load(open(f, "rb"))
        for rk, fn, pp3 in zip(d["race_key"], d["frame_no"], d["pp3"]):
            p3_by[rk][int(fn)] = float(pp3)
    print(f"[wf] {len(files)} ファイル / {len(p3_by):,} レースを読んだ")

    keys = list(p3_by)
    meta: dict[str, str] = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, race_date FROM keirin.wt_races "
                 "WHERE n_entries = ? AND race_key IN (%s)" % ",".join("?" * len(ch)))
            for x in c.execute(q, [NE, *ch]).fetchall():
                meta[x["race_key"]] = str(x["race_date"])

    n = {"train": 0, "test": 0}
    with open(args.train_out, "w") as ftr, open(args.test_out, "w") as fte:
        for rk, p3 in p3_by.items():
            date = meta.get(rk)
            # 🔴 7車ちょうどのレースだけ。`build_A` が len(order) < 7 を弾くので
            #    欠車で7車未満になった盤面を混ぜると黙って落ちる。
            if date is None or len(p3) != NE:
                continue
            rec = dict(race_key=rk, race_date=date, p3=p3,
                       order=sorted(p3, key=lambda k: (-p3[k], k)))
            which = "test" if date >= args.split else "train"
            (fte if which == "test" else ftr).write(json.dumps(rec) + "\n")
            n[which] += 1
    print(f"[out] 学習 {n['train']:,}R → {args.train_out}")
    print(f"[out] 検定 {n['test']:,}R → {args.test_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
