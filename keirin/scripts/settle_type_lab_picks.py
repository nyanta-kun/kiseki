#!/usr/bin/env python3
"""型ラボの買い目を採点して `keirin.type_lab_picks` を埋める（2026-08-27 新設）。

    python scripts/settle_type_lab_picks.py --from 2026-01-01 --to 2026-08-26
    python scripts/settle_type_lab_picks.py --date 2026-08-27          # 当日ぶん

🔴 **確定オッズで採点する**（`wt_odds` の trio / trifecta）。予測オッズは使わない。
   的中目の 確定/予測 は中央 0.87 に下振れる（勝者の呪い）ので、想定払戻で採点すると
   実績を上振れさせる。
🔴 **同着・失格で3着までが確定しないレースは採点しない**（`settled_at` を空のまま残す）。
   ここで 0 を書くと外れと区別できなくなる（2026-08-21 立川11R で踏んだ型）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402


def _load_targets(where: str, params: tuple) -> list[dict]:
    with get_connection() as c:
        rows = c.execute(
            "SELECT id, race_key, bet_type, legs FROM type_lab_picks "
            f"WHERE settled_at IS NULL AND {where}", params).fetchall()
    return [dict(zip(("id", "race_key", "bet_type", "legs"), r)) for r in rows]


def _finish(keys: list[str]) -> dict:
    """{race_key: {着順: 車番}}。1〜3着がそろったレースだけ返す。"""
    out = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order IS NOT NULL")
            for rk, fn, fo in c.execute(q, ch).fetchall():
                out[rk][int(fo)] = int(fn)
    return {k: v for k, v in out.items() if all(i in v for i in (1, 2, 3))}


def _odds(keys: list[str]) -> dict:
    """{race_key: {('trio'|'trifecta', 正規化した組み合わせ): 確定オッズ}}"""
    out = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, bet_type, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type IN ('trio','trifecta')")
            for rk, bt, comb, od in c.execute(q, ch).fetchall():
                try:
                    v = float(od)
                except (TypeError, ValueError):
                    continue
                if not (0 < v < 99999):
                    continue
                nums = [int(x) for x in re.split(r"[-=→]", str(comb)) if x.strip().isdigit()]
                if len(nums) != 3:
                    continue
                key = ("=".join(str(x) for x in sorted(nums)) if bt == "trio"
                       else "-".join(str(x) for x in nums))
                out[rk][(bt, key)] = v
    return dict(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--date")
    a = ap.parse_args()
    if a.date:
        where, params = "race_date = ?", (a.date,)
    elif a.date_from and a.date_to:
        where, params = "race_date BETWEEN ? AND ?", (a.date_from, a.date_to)
    else:
        where, params = "race_date = ?", (date.today().isoformat(),)

    targets = _load_targets(where, params)
    if not targets:
        print("採点対象なし")
        return
    keys = sorted({t["race_key"] for t in targets})
    fin, odds = _finish(keys), _odds(keys)
    print(f"対象 {len(targets)} 行 / {len(keys)} レース  着順確定 {len(fin)}")

    n_ok = n_wait = 0
    updates: list[tuple] = []
    with get_connection() as c:
        for t in targets:
            f = fin.get(t["race_key"])
            if not f:
                n_wait += 1
                continue
            trio = "=".join(str(x) for x in sorted((f[1], f[2], f[3])))
            tf = f"{f[1]}-{f[2]}-{f[3]}"
            win = trio if t["bet_type"] == "trio" else tf
            legs = json.loads(t["legs"]) if isinstance(t["legs"], str) else t["legs"]
            hit = next((l for l in legs if l["combo"] == win), None)
            o = odds.get(t["race_key"], {}).get((t["bet_type"], win))
            if hit and o is None:
                # 当たっているのに確定オッズが引けない。0 を書くと外れと区別できないので待つ
                n_wait += 1
                continue
            payout = int(round(hit["stake"] * o)) if hit else 0
            # 🔴 **決着の三連単オッズは券種と的中に関係なく入れる**（答え合わせ用）。
            #    `final_odds` は「買った目」の確定オッズで的中時しか入らないため、
            #    外れたレースの荒れ具合が測れず「arare が配当を当てているか」を
            #    検証できない。三連複プラン(D_hit)の行にも三連単の値を入れることで
            #    型どうしを同じ物差しで比べられる。
            tf_odds = odds.get(t["race_key"], {}).get(("trifecta", tf))
            # 🔴 `hit` は PostgreSQL では boolean。1/0 を渡すと
            #    DatatypeMismatch で落ちる（SQLite では通るので気づきにくい）。
            updates.append((win, bool(hit), payout,
                            float(o) if (hit and o) else None,
                            float(tf_odds) if tf_odds else None, t["id"]))
            n_ok += 1
        # 1行ずつ UPDATE すると 16,000 行で数分かかる（VPS への往復）。まとめて送る。
        if updates:
            c.executemany(
                "UPDATE type_lab_picks SET settled_at = NOW(), win_combo = ?, "
                "hit = ?, payout = ?, final_odds = ?, win_tf_odds = ? "
                "WHERE id = ?", updates)
        c.commit()
    print(f"採点 {n_ok} 行 / 保留 {n_wait} 行（着順または確定オッズ待ち）")


if __name__ == "__main__":
    main()
