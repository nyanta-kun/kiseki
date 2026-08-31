"""advancementConditionText が race_type に対して何を足すかを測る（調査専用）。"""
from __future__ import annotations

import collections
import glob
import json
import math
import re
from pathlib import Path

CACHE = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
             "19c049e5-ea85-4b67-af6f-4efddbeea937/scratchpad/wt_state")


def norm(s: str) -> str:
    return (s or "").replace("～", "〜").replace("~", "〜").replace("　", " ").strip()


def entropy(counter) -> float:
    n = sum(counter.values())
    return -sum(v / n * math.log2(v / n) for v in counter.values() if v)


def cond_entropy(rows, keyf) -> float:
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[keyf(r)][r["text"]] += 1
    n = sum(sum(c.values()) for c in by.values())
    return sum(sum(c.values()) / n * entropy(c) for c in by.values())


def top_slots(t: str):
    """最上位の行き先へ何名上がるかを粗く数える（「1〜3着と4着2名は…へ」→ 3+2）。"""
    head = t.split("、")[0]
    m = re.match(r"(\d+)着?〜(\d+)着", head)
    base = int(m.group(2)) if m else (1 if head.startswith(("1着", "１着")) else None)
    add = 0
    m2 = re.search(r"(\d+)着(\d+)名", head)
    if m2:
        add = int(m2.group(2))
    if "全員" in head:
        return "ALL"
    if base is None:
        return None
    return base + add


def main() -> None:
    rows = []
    seen = set()
    for p in sorted(glob.glob(str(CACHE / "*.json"))):
        st = json.load(open(p))
        cr = None
        for x in st.get("tanStackQuery", {}).get("queries", []):
            d = x.get("state", {}).get("data")
            if isinstance(d, dict) and d.get("schedules") is not None and d.get("races") is not None:
                cr = d
        if not cr:
            continue
        cid = str((cr.get("cup") or {}).get("id"))
        if cid in seen:
            continue
        seen.add(cid)
        cup = cr.get("cup") or {}
        races = cr.get("races") or []
        n_days = len(cr.get("schedules") or [])
        for r in races:
            t = norm(r.get("advancementConditionText"))
            if not t:
                continue
            rows.append({"text": t, "race_type": r.get("raceType"),
                         "cls": r.get("class"), "grade": cup.get("grade"),
                         "n_races": len(races), "n_days": n_days,
                         "slots": top_slots(t)})

    print(f"n={len(rows)}  開催={len(seen)}  ユニークtext={len({r['text'] for r in rows})}")
    h = entropy(collections.Counter(r["text"] for r in rows))
    print(f"H(text)                              = {h:.3f} bit")
    for name, f in [
        ("race_type", lambda r: r["race_type"]),
        ("race_type+cup_grade", lambda r: (r["race_type"], r["grade"])),
        ("race_type+cup_grade+級班", lambda r: (r["race_type"], r["grade"], r["cls"])),
        ("+開催規模(レース数,日数)",
         lambda r: (r["race_type"], r["grade"], r["cls"], r["n_races"], r["n_days"])),
    ]:
        hc = cond_entropy(rows, f)
        print(f"H(text | {name:28s}) = {hc:.3f} bit   （残る不確実性）")

    print("\n最上位の行き先への通過枠（slots）× race_type")
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r["race_type"]][r["slots"]] += 1
    for rt, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
        print(f"  {rt:16s} n={sum(c.values()):4d}  " +
              "  ".join(f"{k}名:{v}" for k, v in sorted(c.items(), key=lambda x: str(x[0]))))


if __name__ == "__main__":
    main()
