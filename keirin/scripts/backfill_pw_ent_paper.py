#!/usr/bin/env python3
"""ペーパー行の `pw_ent` を埋め直す（2026-09-05 の実バグの後始末）。

`run_paper` が `cars` に `pw` を入れ忘れていたため、**盤面（`/tmp/race_type_board.npz`）
経由で作った行は `pw_ent` が全部 0** になっていた（実測 2026-01〜08 の 2,431行）。
`pw_ent` は型A の売り分け（`A_ana`）の唯一の入力なので、そのままだと
**確認窓で A_ana の採否を検証できない**。

🔴 **買い目は作り直さない。** `type_lab_picks` は1レースにつき全プランの行を持ち、
   「どれを売るか」は分析時に `sell_plans_for` で決める。だから `pw_ent` の列だけ
   直せば検証は成立する。買い目を作り直すと母集団が動いてしまう。

🔴 **元の行を作ったのと同じ盤面の `PW` を使う。** `wt_entries.pred_win_pct` は
   四半期 walk-forward のバックフィル由来で vintage が違い、混ぜると
   「同じ mode に別のモデルの pw_ent が入る」ことになる。

使い方（**VPS で実行する**。盤面は VPS の /tmp にある）:
    python scripts/backfill_pw_ent_paper.py --from 2026-01-01 --to 2026-08-31 --dry-run
    python scripts/backfill_pw_ent_paper.py --from 2026-01-01 --to 2026-08-31
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import get_connection            # noqa: E402
from src.type_lab import win_entropy               # noqa: E402

BOARD = Path("/tmp/race_type_board.npz")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not BOARD.exists():
        raise SystemExit(f"[pw_ent] {BOARD} がありません（VPS で実行すること）")
    zf = np.load(BOARD, allow_pickle=True)
    z = {k: zf[k] for k in ("KEY", "DATE", "PW")}
    keys = [str(k) for k in z["KEY"]]
    dates = [str(d) for d in z["DATE"]]

    ent: dict[str, float] = {}
    for i, d in enumerate(dates):
        if not (a.date_from <= d <= a.date_to):
            continue
        pw = z["PW"][i]
        vals = {c + 1: float(pw[c]) for c in range(len(pw))
                if np.isfinite(pw[c]) and pw[c] > 0}
        if len(vals) < 2:
            continue
        ent[keys[i]] = round(win_entropy(vals), 6)
    print(f"[pw_ent] 盤面から {len(ent):,} レースぶんを計算した")

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, count(*) FROM type_lab_picks "
            " WHERE mode = ? AND race_date BETWEEN ? AND ? AND pw_ent = 0 "
            " GROUP BY race_key",
            (a.mode, a.date_from, a.date_to)).fetchall()
        target = {str(r[0]): int(r[1]) for r in rows}
        hit = {k: v for k, v in target.items() if k in ent}
        print(f"[pw_ent] pw_ent=0 の対象 {len(target):,}レース / {sum(target.values()):,}行")
        print(f"[pw_ent] 盤面と突合できた {len(hit):,}レース / {sum(hit.values()):,}行")
        if a.dry_run:
            for k in list(hit)[:5]:
                print(f"   例 {k}: pw_ent 0 → {ent[k]}")
            print("[pw_ent] dry-run のため書き込まない")
            return
        n = 0
        for k, cnt in hit.items():
            conn.execute(
                "UPDATE type_lab_picks SET pw_ent = ? "
                " WHERE race_key = ? AND mode = ? AND pw_ent = 0",
                (ent[k], k, a.mode))
            n += cnt
        print(f"[pw_ent] {n:,} 行を更新した")


if __name__ == "__main__":
    main()
