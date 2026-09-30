#!/usr/bin/env python3
"""取得済みの選手コメントと分類（2025-12〜2026-09）を `rider_interviews` へ一度だけ入れる（2026-10-01）。

入力（2026-09-30〜10-01 に作ったもの・git 管理外）:
  data/interviews/{post,pre}/{race_key}.json   … ページの interview データ（backfill）
  data/interviews/tags/*.jsonl                 … API Batch の分類（id = "kind|race_key|playerId"）

    PYTHONPATH=. .venv/bin/python3 scripts/load_rider_interviews.py [--dry-run]

何度流しても行は増えない（`upsert` は同じ本文なら何もしない）。分類は未分類の行にだけ書く。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.rider_interviews import TAG_MODEL, apply_tags, rows_from_data, upsert, valid_tag  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "data" / "interviews"


def main() -> None:
    """CLI エントリ。"""
    ap = argparse.ArgumentParser(description="取得済みの選手コメントを DB へ入れる")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    tags: dict[str, dict] = {}
    for f in sorted((root / "tags").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            t = json.loads(line)
            if valid_tag(t):
                tags.setdefault(t["id"], t)      # 先勝ち（パイロットの重複を捨てる）

    with get_connection() as c:
        cup_of = {r[0]: r[1] for r in c.execute(
            "SELECT race_key, cup_id FROM wt_races WHERE race_date >= '2025-12-01'")}
    rows: list[dict] = []
    for kind in ("pre", "post"):
        for f in sorted((root / kind).glob("*.json")):
            d = json.loads(f.read_text())
            rk = d["race_key"]
            rows += rows_from_data(kind, rk, cup_of.get(rk), d["race_date"], d.get("data") or {})
    n_tag = sum(1 for r in rows if f"{r['kind']}|{r['race_key']}|{str(r['player_id']).zfill(6)}" in tags)
    print(f"コメント {len(rows):,} 行 / 分類あり {n_tag:,} / 分類の総数 {len(tags):,}")
    if args.dry_run:
        return

    ins = upd = 0
    for i in range(0, len(rows), 2000):
        with get_connection() as c:
            a, b = upsert(c, rows[i:i + 2000])
        ins += a
        upd += b
    print(f"追加 {ins:,} / 本文更新 {upd:,}")

    with get_connection() as c:
        ids = {(r[1], r[2], int(r[3])): r[0] for r in c.execute(
            "SELECT id, kind, race_key, player_id FROM rider_interviews WHERE tagged_at IS NULL")}
    todo = []
    for key, t in tags.items():
        kind, rk, pid = key.split("|")
        rid = ids.get((kind, rk, int(pid)))
        if rid is not None:
            todo.append((rid, t))
    for i in range(0, len(todo), 2000):
        with get_connection() as c:
            apply_tags(c, todo[i:i + 2000], model=f"{TAG_MODEL}(batch)")
    print(f"分類を書いた行 {len(todo):,}")


if __name__ == "__main__":
    main()
