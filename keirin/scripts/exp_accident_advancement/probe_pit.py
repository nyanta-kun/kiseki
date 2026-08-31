"""キャッシュ済み state の日付項目を対象レース日と突き合わせ、point-in-time かを実測する。"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

CACHE = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
             "19c049e5-ea85-4b67-af6f-4efddbeea937/scratchpad/wt_state")


def get_query(st: dict, frag: str):
    for x in st.get("tanStackQuery", {}).get("queries", []):
        if frag in str(x.get("queryKey")):
            return x.get("state", {}).get("data")
    return None


def rid_date(rid) -> str:
    return str(rid)[-8:]


def main() -> None:
    for path in sorted(glob.glob(str(CACHE / "*.json"))):
        st = json.load(open(path))
        d = get_query(st, "FETCH_KEIRIN_RACE")
        if not d:
            continue
        race = d.get("race", {})
        # 対象レース日は schedule から
        sched = d.get("schedule") or {}
        rdate = str(sched.get("date", "")).replace("-", "")
        res = d.get("results") or []
        if res and not rdate:
            rdate = rid_date(res[0]["raceId"])
        print(f"== {Path(path).name}  race_date={rdate}  status={race.get('status')}")
        for key in ("currentCupResults", "previousCupResults", "latestCupResults",
                    "latestVenueResults"):
            ds = [rid_date(it["raceId"]) for r in d.get("records", [])
                  for it in (r.get(key) or []) if isinstance(it, dict) and it.get("raceId")]
            fut = sorted({x for x in ds if x > rdate})
            print(f"   {key:22s} n={len(ds):4d} max={max(ds) if ds else '-':>8s} "
                  f"future={len(fut)} {fut[:5]}")
        ds = [rid_date(it["raceId"]) for c in (d.get("competitionRecords") or [])
              for it in (c.get("races") or []) if it.get("raceId")]
        fut = sorted({x for x in ds if x > rdate})
        print(f"   {'competitionRecords':22s} n={len(ds):4d} max={max(ds) if ds else '-':>8s} "
              f"future={len(fut)} {fut[:5]}")
        # 成績率（開催中に動くことが既知の項目）
        r0 = (d.get("records") or [{}])[0]
        print(f"   sample rates: firstRate={r0.get('firstRate')} "
              f"exSpurt={(r0.get('exSpurt') or {}).get('percentage')}")


if __name__ == "__main__":
    main()
