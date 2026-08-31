"""WINTICKET 出走表ページの __PRELOADED_STATE__ を取得してローカルへキャッシュする。

調査専用（本番経路は触らない）。既にキャッシュがあれば再取得しない。
使い方: PYTHONPATH=. .venv/bin/python scripts/exp_accident_advancement/fetch_state.py \
          <venue_id> <cup_id> <day_index> <race_no> [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.scraper.winticket import VENUE_SLUGS, WinticketScraper, _BASE, _extract_state

CACHE = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
             "19c049e5-ea85-4b67-af6f-4efddbeea937/scratchpad/wt_state")


def fetch(sc: WinticketScraper, venue_id: str, cup_id: str, day: int, race_no: int) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{cup_id}_{day}_{race_no}.json"
    if path.exists():
        return json.loads(path.read_text())
    slug = VENUE_SLUGS.get(venue_id)
    if not slug:
        print(f"no slug for {venue_id}")
        return None
    url = f"{_BASE}/keirin/{slug}/racecard/{cup_id}/{day}/{race_no}"
    resp = sc._get(url)
    if resp is None or resp.status_code != 200:
        print(f"FAIL {url} {getattr(resp, 'status_code', None)}")
        return None
    st = _extract_state(resp.text)
    path.write_text(json.dumps(st, ensure_ascii=False))
    print(f"saved {path.name} ({len(resp.text):,} bytes)")
    return st


def main() -> None:
    args = sys.argv[1:]
    sc = WinticketScraper(request_interval=2.0)
    for i in range(0, len(args), 4):
        venue_id, cup_id, day, race_no = args[i], args[i + 1], int(args[i + 2]), int(args[i + 3])
        fetch(sc, venue_id, cup_id, day, race_no)


if __name__ == "__main__":
    main()
