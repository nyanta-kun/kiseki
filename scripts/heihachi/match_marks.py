#!/usr/bin/env python3
"""馬印テーブル画像を OCR し、DB の出走表と突き合わせて馬番を確定させる。

平八の表は画像なので、OCR の数字は当てにならない（読み違えが実際に起きる）。
一方で**馬名はほぼ正確に読める**ので、馬名を DB の出走表と照合して馬番は
DB 側から取る。これで OCR の桁誤りが自動的に補正される。

前提:
  1. `fetch_notes.py` で画像を集めてあること
  2. macOS の Vision OCR バイナリをビルドしてあること
       swiftc -O -o scripts/heihachi/ocr scripts/heihachi/ocr.swift

使い方:
  python3 scripts/heihachi/match_marks.py \
      --imgs data/heihachi/raw/imgs \
      --out  data/heihachi/marks.tsv

精度: 2026-09-05 の表で手作業の正解と突き合わせ、119/119印・30/30レースが一致。
全体では 218枚中198枚が照合成立（未照合20枚はタイトル日付の誤り、または
レイアウト差による OCR 不良）。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from extract_marks import load_ocr, match, rows_from  # noqa: E402

_ENTRIES_SQL = """
    SELECT r.date, r.course_name, r.race_number, re.horse_number, h.name
    FROM keiba.races r
    JOIN keiba.race_entries re ON re.race_id = r.id
    JOIN keiba.horses h ON h.id = re.horse_id
    WHERE r.date = ANY(:dates)
"""


async def load_entries(dates: list[str]) -> dict[str, dict]:
    """対象日の出走表を {date: {(course, race): [(馬番, 馬名)]}} で返す。"""
    from sqlalchemy import text

    from src.db.session import AsyncSessionLocal  # type: ignore[import-not-found]

    out: dict[str, dict] = collections.defaultdict(lambda: collections.defaultdict(list))
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(_ENTRIES_SQL), {"dates": dates})).all()
    for date, course, race_no, horse_no, name in rows:
        out[date][(course, int(race_no))].append((int(horse_no), name))
    return out


def run_ocr(ocr_bin: Path, img: Path, cache: Path) -> Path:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists() or cache.stat().st_size == 0:
        res = subprocess.run([str(ocr_bin), str(img)], capture_output=True, timeout=180)
        if res.returncode != 0:
            raise RuntimeError(f"OCR failed: {img}")
        cache.write_bytes(res.stdout)
    return cache


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--imgs", default="data/heihachi/raw/imgs")
    ap.add_argument("--out", default="data/heihachi/marks.tsv")
    ap.add_argument("--ocr-cache", default="data/heihachi/raw/ocr")
    ap.add_argument("--ocr-bin", default=str(Path(__file__).with_name("ocr")))
    args = ap.parse_args()

    imgs = sorted(Path(args.imgs).glob("*.png"))
    if not imgs:
        sys.exit(f"画像が見つかりません: {args.imgs}")
    ocr_bin = Path(args.ocr_bin)
    if not ocr_bin.exists():
        sys.exit(f"OCR バイナリがありません。先にビルドしてください:\n"
                 f"  swiftc -O -o {ocr_bin} {ocr_bin.with_suffix('.swift')}")

    dates = sorted({p.name.split('_')[0] for p in imgs})
    entries = asyncio.run(load_entries(dates))

    stats: collections.Counter[str] = collections.Counter()
    out_rows = []
    for img in imgs:
        date = img.name.split("_")[0]
        races = entries.get(date)
        if not races:
            stats["DBに開催なし"] += 1
            continue
        ocr = run_ocr(ocr_bin, img, Path(args.ocr_cache) / f"{img.stem}.tsv")
        matched = match(rows_from(load_ocr(str(ocr))), races)
        if not matched:
            stats["照合不成立"] += 1
            continue
        stats["OK"] += 1
        stats["レース"] += len(matched)
        for r in matched:
            for mark, v in r["marks"].items():
                out_rows.append([date, r["course"], r["race"], mark, v["no"],
                                 v["name"], v["sim"], r["score"]])

    out_rows.sort(key=lambda r: (r[0], r[1], r[2],
                                 "◎○▲☆".index(r[3]) if r[3] in "◎○▲☆" else 9))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["date", "course", "race", "mark", "horse_number", "horse_name",
                    "name_similarity", "race_match_score"])
        w.writerows(out_rows)
    print(dict(stats), f"→ {out} ({len(out_rows)} 印)")


if __name__ == "__main__":
    main()
