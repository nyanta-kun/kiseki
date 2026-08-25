"""予測オッズと確定オッズの食い違いを測るための共通部品（2026-08-26）。

🔴 **honest を壊さないこと。** 本番モデルの学習終端は `odds_trio_meta.json` の
   `train_end`。それ以前の期間を本番モデルで採点すると in-sample になる。
   過去窓を測るときは `KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816`
   （学習終端 2025-12-31）を指すこと。`src.odds_prediction.assert_model_is_honest`
   と同じ趣旨。
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

SEP_RE = re.compile(r"[-=]")
CACHE = REPO / "data" / "exp_cache"


def connect():
    import psycopg2
    url = os.environ.get("KEIRIN_DB_URL")
    if not url:
        raise SystemExit("KEIRIN_DB_URL が未設定です")
    return psycopg2.connect(url, connect_timeout=60)


def q(sql, params=None):
    import psycopg2.extras
    with connect() as c:
        with c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def combo(text) -> frozenset:
    return frozenset(int(x) for x in SEP_RE.split(str(text)) if x.strip().isdigit())


def final_boards(date_from: str) -> dict[str, dict[frozenset, float]]:
    """確定三連複オッズ。`wt_odds` は払戻金と 100% 一致することを確認済み。"""
    out: dict[str, dict[frozenset, float]] = defaultdict(dict)
    for r in q("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
               "WHERE bet_type='trio' AND race_key >= %s", (date_from,)):
        c = combo(r["combination"])
        if len(c) == 3 and r["odds_value"]:
            out[r["race_key"]][c] = float(r["odds_value"])
    return out


def race_inputs(race_keys) -> dict:
    """`odds_prediction.load_race_inputs` と同じ組み立てを一括で行う（1件ずつ引くと遅い）。"""
    keys = list(race_keys)
    rows = []
    for i in range(0, len(keys), 500):
        rows += q("""SELECT race_key, frame_no, race_point, prediction_mark, player_class, style,
                     line_group, line_size, line_pos, is_line_leader,
                     first_rate, second_rate, third_rate, pred_win_pct, pred_top3_pct
                     FROM keirin.wt_entries WHERE race_key = ANY(%s)""", (keys[i:i + 500],))
    by = defaultdict(list)
    for r in rows:
        by[r["race_key"]].append(r)
    out = {}
    for rk, rs in by.items():
        cars, p3, pw, meta = [], {}, {}, {}
        ok = True
        for d in rs:
            car = int(d["frame_no"])
            if d["pred_top3_pct"] is None or d["pred_win_pct"] is None or d["race_point"] is None:
                ok = False
                break
            cars.append(car)
            p3[car] = float(d["pred_top3_pct"]) / 100.0
            pw[car] = float(d["pred_win_pct"]) / 100.0
            meta[car] = {k: d[k] for k in ("prediction_mark", "player_class", "style",
                                           "line_group", "line_size", "line_pos",
                                           "is_line_leader", "first_rate", "second_rate",
                                           "third_rate")}
            meta[car]["mark"] = meta[car].pop("prediction_mark")
            meta[car]["race_point"] = float(d["race_point"])
        if ok and len(cars) in (7, 9):
            out[rk] = (sorted(cars), p3, pw, meta)
    return out
