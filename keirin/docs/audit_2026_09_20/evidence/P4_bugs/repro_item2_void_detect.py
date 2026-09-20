"""item2 再現: type_lab_picks(mode=live) の買い目に欠車の車番が混ざっている行を検出する。

事前に以下を実行して CSV を作る（DB へは SELECT のみ）:

    psql "$KEIRIN_DB_URL" -c "COPY (
      SELECT id, race_key, race_date, plan_key, bet_type, budget, legs, hit, payout, settled_at
      FROM keirin.type_lab_picks
      WHERE mode='live' AND race_date BETWEEN '2026-08-27' AND '2026-09-19'
        AND settled_at IS NOT NULL
    ) TO STDOUT WITH CSV HEADER" > tlp_live_settled.csv

    psql "$KEIRIN_DB_URL" -c "COPY (
      SELECT race_key, frame_no, finish_order FROM keirin.wt_entries
      WHERE race_key IN (SELECT DISTINCT race_key FROM keirin.type_lab_picks
                          WHERE mode='live' AND race_date BETWEEN '2026-08-27' AND '2026-09-19'
                            AND settled_at IS NOT NULL)
    ) TO STDOUT WITH CSV HEADER" > wt_entries_window.csv

実行結果（2026-09-20 実測）: flagged rows=9 / distinct races=6 / sum(stake)=25,400円
（20260830_13_06 B_hit の 9,900円を含む）。sub_settle/REPORT_settle.md の S1 と一致。
"""
import csv
import json
from collections import defaultdict


def cars(combo: str) -> list[int]:
    sep = "=" if "=" in combo else "-"
    return [int(x) for x in combo.split(sep) if x.strip().isdigit()]


def main() -> None:
    entries: dict[str, set[int]] = defaultdict(set)
    with open("wt_entries_window.csv") as f:
        for row in csv.DictReader(f):
            entries[row["race_key"]].add(int(row["frame_no"]))

    flagged = []
    with open("tlp_live_settled.csv") as f:
        for row in csv.DictReader(f):
            rk = row["race_key"]
            present = entries.get(rk, set())
            legs = json.loads(row["legs"])
            bad_legs = []
            for leg in legs:
                missing = [c for c in cars(leg["combo"]) if c not in present]
                if missing:
                    bad_legs.append((leg["combo"], leg["stake"], missing))
            if bad_legs:
                flagged.append({
                    "id": row["id"], "race_key": rk, "plan_key": row["plan_key"],
                    "hit": row["hit"], "payout": row["payout"],
                    "bad_legs": bad_legs,
                    "total_stake_bad": sum(b[1] for b in bad_legs),
                })

    print(f"flagged rows: {len(flagged)}")
    print(f"distinct races: {len(set(x['race_key'] for x in flagged))}")
    print(f"sum total_stake_bad: {sum(x['total_stake_bad'] for x in flagged)}")
    for x in flagged:
        print(x)


if __name__ == "__main__":
    main()
