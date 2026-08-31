"""キャッシュ済み state から advancementConditionText を集計する（調査専用）。"""
from __future__ import annotations

import collections
import glob
import json
import re
from pathlib import Path

CACHE = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
             "19c049e5-ea85-4b67-af6f-4efddbeea937/scratchpad/wt_state")


def cup_races(st: dict):
    for x in st.get("tanStackQuery", {}).get("queries", []):
        d = x.get("state", {}).get("data")
        if isinstance(d, dict) and d.get("schedules") is not None and d.get("races") is not None:
            return d
    return None


def norm(s: str) -> str:
    """全角/半角チルダ・空白の揺れを吸収する。"""
    return (s or "").replace("～", "〜").replace("　", " ").strip()


def main() -> None:
    texts = collections.Counter()
    by_type = collections.defaultdict(collections.Counter)
    cups = set()
    for p in sorted(glob.glob(str(CACHE / "*.json"))):
        st = json.load(open(p))
        cr = cup_races(st)
        if not cr:
            continue
        cid = str((cr.get("cup") or {}).get("id"))
        if cid in cups:
            continue
        cups.add(cid)
        for r in cr.get("races") or []:
            t = norm(r.get("advancementConditionText"))
            if not t:
                continue
            texts[t] += 1
            by_type[r.get("raceType")][t] += 1

    print(f"# 開催数 {len(cups)} / 非空テキストのレース数 {sum(texts.values())} / "
          f"ユニーク {len(texts)}")
    print("\n## テキスト頻度")
    for t, n in texts.most_common():
        print(f"{n:5d}  {t}")

    print("\n## raceType × テキスト（上位）")
    for rt, c in sorted(by_type.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"\n--- {rt}  (n={sum(c.values())}, unique={len(c)})")
        for t, n in c.most_common(6):
            print(f"   {n:4d}  {t}")

    # 構造化できるか: 「N着まで/N名は X へ」の抽出
    print("\n## 構造抽出の試み（行き先の集合と、上位何名が上へ行くか）")
    dest = collections.Counter()
    for t, n in texts.items():
        for m in re.finditer(r"([^、。]+?)へ", t):
            dest[m.group(1).split("は")[-1].split("まで")[-1].strip()] += n
    for d, n in dest.most_common(25):
        print(f"{n:5d}  -> {d}")


if __name__ == "__main__":
    main()
