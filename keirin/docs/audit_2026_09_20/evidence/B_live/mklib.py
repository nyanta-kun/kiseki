"""独立再実装（本番 keirin_settlement.py を import しない）。"""
import csv, json, sys
from itertools import permutations
csv.field_size_limit(10**9)

def load_subs(path="subs.csv"):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

def load_finishers(path="finishers.csv"):
    fin = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            fo = r["finish_order"]
            if fo in ("", None): continue
            fo = int(fo)
            if 1 <= fo <= 3:
                fin.setdefault(r["race_key"], set()).add((fo, int(r["frame_no"])))
    return {k: sorted(v) for k, v in fin.items()}

def win_trifectas(rows):
    groups = []
    for fo, no in sorted(rows or []):
        if groups and groups[-1][0] == fo: groups[-1][1].append(no)
        else: groups.append((fo, [no]))
    if sum(len(g) for _, g in groups) < 3: return []
    opts = [()]; rem = 3
    for _fo, frames in groups:
        if rem <= 0: break
        take = min(len(frames), rem)
        opts = [p + q for p in opts for q in permutations(frames, take)]
        rem -= take
    return sorted(opts)

def win_labels(rows):
    tfs = win_trifectas(rows)
    trios, seen = [], set()
    for t in tfs:
        k = frozenset(t)
        if k not in seen:
            seen.add(k); trios.append("=".join(str(x) for x in sorted(k)))
    return trios + ["-".join(map(str, t)) for t in tfs]

def parse_bd(raw):
    if not raw: return None
    try: d = json.loads(raw)
    except Exception: return None
    return d if isinstance(d, dict) else None

ORDERED = {"3連複": False, "3連単": True}
def combo_label(combo, ordered):
    try:
        cars = [int(x) for x in str(combo).strip().replace("=", "-").split("-") if x != ""]
    except (TypeError, ValueError):
        return None
    if len(cars) != 3: return None
    return "-".join(map(str, cars)) if ordered else "=".join(map(str, sorted(cars)))

def pay100(odds):
    if odds is None: return None
    try: return int(round(float(odds) * 100)) // 10 * 10
    except (TypeError, ValueError): return None
