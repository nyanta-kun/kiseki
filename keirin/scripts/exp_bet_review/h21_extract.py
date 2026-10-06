#!/usr/bin/env python3
"""H21 抽出: 窓 2026-06-08〜10-05 の7車レースについて、入力・結果・最終オッズ・発走前スナップショットを集める（読み取り専用）。

    set -a; source ~/.config/kiseki/env >/dev/null 2>&1; set +a
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h21_cover.py      # 先に被覆（cover.pkl）
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h21_extract.py

出力: data/exp_bet_review/h21/ex_YYYY-MM-DD.pkl（日ごと） と start_all.json（全レースの venue/start_at）。
スナップショット = 各レースで「発走の120分前／60分前以前の最後の1枚」（事前登録・全 snapshot_type・snapshot_at は JST の naive）。
オッズ辞書は 0 < odds < 9999 の組だけ（9999 = 売れていない組。数値としては入れない）。無効組数は n_invalid に残す。
"""
from __future__ import annotations
import json, pickle, re, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from _db import connect

JST = timezone(timedelta(hours=9))
OUT = REPO / "data/exp_bet_review/h21"
SENT = 9999.0
CUTS = (120, 60)


def parse_perm(s):
    try:
        t = tuple(int(x) for x in re.split(r"[-=→]", str(s)))
    except ValueError:
        return None
    return t if len(t) == 3 else None


def pick_snap(snaps, start_epoch, lead):
    """(type, at) = 発走の lead 分前以前の最後の1枚。無ければ None。"""
    stt = datetime.fromtimestamp(start_epoch, JST).replace(tzinfo=None)
    cut = stt - timedelta(minutes=lead)
    c = [s for s in snaps if s[1] <= cut]
    if not c:
        return None
    s = max(c, key=lambda s: s[1])
    return s[0], s[1], (stt - s[1]).total_seconds() / 60.0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cov = pickle.load(open(OUT / "cover.pkl", "rb"))
    races, snaps = cov["races"], cov["snaps"]
    days = sorted({v[0] for v in races.values()})
    lo = sys.argv[1] if len(sys.argv) > 1 else "0000"
    hi = sys.argv[2] if len(sys.argv) > 2 else "9999"
    days = [d for d in days if lo <= d <= hi]
    with connect() as c, c.cursor() as cur:
        # 全レースの venue / start_at（モーニング開催の判定用・車数を問わない）
        cur.execute("""SELECT race_key, venue_id, start_at FROM keirin.wt_races
                       WHERE race_date BETWEEN '2026-06-08' AND '2026-10-05' AND start_at IS NOT NULL""")
        if not (OUT / "start_all.json").exists():
            json.dump({r[0]: [str(r[1]), str(r[2])] for r in cur.fetchall()}, open(OUT / "start_all.json", "w"))
        for d in days:
            if (OUT / f"ex_{d}.pkl").exists():
                continue
            keys = sorted(k for k, v in races.items() if v[0] == d)
            cur.execute("""SELECT race_key, race_type, cup_grade, day_index, venue_id, race_no
                           FROM keirin.wt_races WHERE race_key = ANY(%s)""", (keys,))
            meta = {r[0]: dict(race_type=r[1], cup_grade=r[2], day_index=r[3], venue_id=str(r[4]), race_no=r[5],
                               start=races[r[0]][1]) for r in cur.fetchall()}
            cur.execute("""SELECT race_key, frame_no, line_group, line_pos, style, race_point, ex_left_behind_pct,
                                  line_size, is_line_leader, player_class, first_rate, second_rate, third_rate,
                                  prediction_mark, pred_top3_pct, pred_win_pct, finish_order
                           FROM keirin.wt_entries WHERE race_key = ANY(%s)""", (keys,))
            ent = defaultdict(dict)
            for r in cur.fetchall():
                ent[r[0]][int(r[1])] = dict(line_group=r[2], line_pos=r[3], style=r[4], race_point=r[5],
                                            behind=r[6], line_size=r[7], is_line_leader=r[8], player_class=r[9],
                                            first_rate=r[10], second_rate=r[11], third_rate=r[12], mark=r[13],
                                            p3pct=None if r[14] is None else float(r[14]),
                                            pwpct=None if r[15] is None else float(r[15]),
                                            finish=r[16])
            # 最終オッズ（wt_odds）
            fin = {k: dict(tf={}, trio={}, n_tf_invalid=0) for k in keys}
            cur.execute("""SELECT DISTINCT ON (race_key, bet_type, combination) race_key, bet_type, combination, odds_value
                           FROM keirin.wt_odds WHERE race_key = ANY(%s) AND bet_type IN ('trifecta','trio')
                           ORDER BY race_key, bet_type, combination, collected_at DESC""", (keys,))
            for rk, bt, comb, v in cur.fetchall():
                p = parse_perm(comb)
                if p is None:
                    continue
                if v is None or not (0 < float(v) < SENT):
                    if bt == "trifecta":
                        fin[rk]["n_tf_invalid"] += 1
                    continue
                if bt == "trifecta":
                    fin[rk]["tf"][p] = float(v)
                else:
                    fin[rk]["trio"][frozenset(p)] = float(v)
            # スナップショット
            chosen = {k: {} for k in keys}
            need = defaultdict(list)      # snapshot_type -> [race_key]
            for k in keys:
                for lead in CUTS:
                    ps = pick_snap(snaps.get(k, []), races[k][1], lead)
                    chosen[k][lead] = ps
                    if ps:
                        need[ps[0]].append(k)
            snapv = {k: {} for k in keys}
            for st_type, ks in need.items():
                ks = sorted(set(ks))
                cur.execute("""SELECT race_key, bet_type, combination, odds_value, snapshot_at
                               FROM keirin.wt_odds_snapshot
                               WHERE race_key = ANY(%s) AND snapshot_type = %s AND bet_type IN ('trifecta','trio')""",
                            (ks, st_type))
                for rk, bt, comb, v, at in cur.fetchall():
                    p = parse_perm(comb)
                    if p is None:
                        continue
                    slot = snapv[rk].setdefault((st_type, at), dict(tf={}, trio={}, n_tf=0, n_tf_invalid=0))
                    if bt == "trifecta":
                        slot["n_tf"] += 1
                    if v is None or not (0 < float(v) < SENT):
                        if bt == "trifecta":
                            slot["n_tf_invalid"] += 1
                        continue
                    if bt == "trifecta":
                        slot["tf"][p] = float(v)
                    else:
                        slot["trio"][frozenset(p)] = float(v)
            out = {}
            for k in keys:
                sn = {}
                for lead in CUTS:
                    ps = chosen[k][lead]
                    if ps is None:
                        sn[lead] = None
                        continue
                    slot = snapv[k].get((ps[0], ps[1]))
                    sn[lead] = None if slot is None else dict(type=ps[0], at=ps[1], lead_min=ps[2], **slot)
                out[k] = dict(meta=meta.get(k), ent=dict(ent.get(k, {})), final=fin[k], snap=sn)
            pickle.dump(out, open(OUT / f"ex_{d}.pkl", "wb"))
            print(d, len(keys), flush=True)


if __name__ == "__main__":
    main()
