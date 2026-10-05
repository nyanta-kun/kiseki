#!/usr/bin/env python3
"""H13 抽出: 2023-10〜2025-12 の wt_entries + wt_races を月単位で読み取り専用に取得してキャッシュする。
    set -a; source ~/.config/kiseki/env; set +a; .venv/bin/python scripts/exp_bet_review/h13_extract.py
"""
import sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import connect

OUT = Path(__file__).resolve().parents[2] / "data/exp_bet_review/h13"
COLS = """e.race_key, r.race_date, r.venue_id, r.grade, r.race_type, r.distance, r.start_at, r.n_entries,
 r.status, r.cancel, e.frame_no, e.player_id, e.style, e.race_point, e.prediction_mark, e.s_count, e.h_count,
 e.b_count, e.front_runner, e.stalker, e.deep_closer, e.marker, e.first_rate, e.second_rate, e.third_rate,
 e.line_group, e.line_size, e.line_pos, e.is_line_leader, e.n_lines, e.finish_order, e.res_standing, e.res_back, e.final_half"""

def months():
    y, m = 2023, 10
    while (y, m) <= (2025, 12):
        yield y, m
        m += 1
        if m == 13: y, m = y + 1, 1

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for y, m in months():
        f = OUT / f"ent_{y}-{m:02d}.pkl"
        if f.exists(): continue
        lo, hi = f"{y}-{m:02d}-01", f"{y}-{m:02d}-32"
        with connect() as c:
            df = pd.read_sql_query(f"SELECT {COLS} FROM keirin.wt_entries e JOIN keirin.wt_races r ON r.race_key=e.race_key "
                                   f"WHERE r.race_date >= %s AND r.race_date < %s ORDER BY r.race_date, e.race_key, e.frame_no", c, params=(lo, hi))
        df.to_pickle(f); print(y, m, len(df), flush=True); time.sleep(1)
