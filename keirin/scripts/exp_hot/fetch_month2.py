"""好調予想家の一覧APIを期間×予想家で収集する（fetch_month.py の可変版）。

usage: python3 fetch_month2.py <from> <to> <out.jsonl> [yid,yid,...]
"""
from __future__ import annotations
import datetime as dt, json, sys
from pathlib import Path
from fetch_goods import fetch_list
from fetch_month import days, parse_list

DEFAULT = [583, 424, 410, 585, 401, 350, 546, 345, 428, 465, 614, 506, 354, 482]


def main() -> None:
    a, b, out = sys.argv[1], sys.argv[2], sys.argv[3]
    yids = [int(x) for x in sys.argv[4].split(",")] if len(sys.argv) > 4 else DEFAULT
    rows = []
    for yid in yids:
        n0 = len(rows)
        for d in days(a, b):
            try:
                rows += parse_list(yid, d, json.loads(fetch_list(yid, d)))
            except Exception as exc:                       # noqa: BLE001
                print(f"!! {yid} {d}: {exc}", flush=True)
        print(f"{yid}: {len(rows)-n0}件", flush=True)
    Path(out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                         encoding="utf-8")
    print("total", len(rows))


if __name__ == "__main__":
    main()
