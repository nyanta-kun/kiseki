#!/usr/bin/env python3
"""制約の再現に要るメタを揃える（波・cup_grade・race_key）。

🔴 `/tmp/11_rows.pkl` は本番経路で 1レース1商品を組んで **入稿ゲートの可否 (base.gate)・
   軸信頼ゲートの可否 (base.axis_ok)・日次上限の優先度 (base.capp)** まで持っている。
   足りないのは **波**（開催の第1R発走時刻）と **cup_grade**（日次上限の枠外判定）だけ。

出力: /tmp/12_meta.pkl  {board_index: dict(race_key, venue, wave, cupg, exempt)}
"""
from __future__ import annotations
import os, pickle, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import importlib.util
import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from src.meeting_wave import wave_of_first_hour

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s); _s.loader.exec_module(_G)

JST = timezone(timedelta(hours=9))


def main() -> None:
    rows = pickle.load(open("/tmp/11_rows.pkl", "rb"))
    z = C.board()
    KEY = np.array([str(v) for v in z["KEY"]])
    VEN = np.array([str(v) for v in z["VENUE"]])
    RT = np.array([str(v) for v in z["RTYPE"]])
    keys = sorted({KEY[r["key"]] for r in rows})
    print(f"race_key {len(keys):,} 件を DB へ問い合わせ", flush=True)

    import psycopg2
    c = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = c.cursor()
    # 🔴 第1R発走は **開催（日×会場）ぜんぶ**から取る（型ラボの対象レースだけでなく）。
    cur.execute("""select race_date, venue_id, min(start_at::bigint)
                   from keirin.wt_races
                   where race_date >= '2024-07-01' and start_at is not null
                     and start_at <> '' group by 1,2""")
    first = {}
    for rd, v, sa in cur.fetchall():
        first[(str(rd), str(v))] = datetime.fromtimestamp(int(sa), JST).hour
    cur.execute("""select race_key, cup_grade from keirin.wt_races
                   where race_date >= '2024-07-01'""")
    cupg = {str(k): (int(g) if g is not None else None) for k, g in cur.fetchall()}
    c.close()

    meta = {}
    miss_h = miss_g = 0
    for r in rows:
        i = r["key"]; rk = KEY[i]; ven = VEN[i]; dt = r["date"]
        h = first.get((dt, ven))
        if h is None:
            miss_h += 1
        g = cupg.get(rk)
        if g is None:
            miss_g += 1
        meta[i] = dict(race_key=rk, venue=ven, hour=h,
                       wave=wave_of_first_hour(h), cupg=g,
                       exempt=bool(_G.daily_cap_exempt(str(RT[i]), g)))
    pickle.dump(meta, open("/tmp/12_meta.pkl", "wb"))
    from collections import Counter
    print(f"保存 /tmp/12_meta.pkl  {len(meta):,} 行  発走不明 {miss_h}  grade不明 {miss_g}")
    print("波", Counter(m["wave"] for m in meta.values()))
    print("枠外", Counter(m["exempt"] for m in meta.values()))
    print("時", Counter(m["hour"] for m in meta.values()))


if __name__ == "__main__":
    main()
