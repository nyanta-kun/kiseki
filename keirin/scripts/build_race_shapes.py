#!/usr/bin/env python3
"""レースの型（A〜F）を**全車数**ぶん求めて `keirin.race_shapes` へ書く。

    python scripts/build_race_shapes.py --date 2026-09-12

## なぜ別のスクリプトなのか

型判定（`src.type_lab.race_shape`）は 3着内率と並びしか見ないので**車数に
依存しない**。ところが実際に走っていたのは `build_type_lab_picks.py` の中だけで、
あちらは**売る商品を組む**スクリプトなので 7車と9車しか回さない
（`type_lab_daily.sh` が `--n-entries 7` と `9` の2回だけ）。
そのため 5車・6車・8車のレースには型が1件も無く、`/keirin` の一覧では
**同じ推奨外なのに型が出る行と出ない行が混ざっていた**
（2026-09-12 実測: 直近1週間で 31レース＝5車2・6車25・8車4）。

🔴 **ここは商品を作らない。** 買い目も予測オッズも一切触らない（5車・6車は
   予測オッズモデルがそもそも無い）。売るのは従来どおり 7車・9車だけで、
   このスクリプトが書くのは**一覧に出す型**だけ。
🔴 **型判定の正本は `src/type_lab.py`。** ここには規則を書かない
   （`build_type_lab_picks.py` と同じ思想。二重管理になった瞬間に
   「一覧に出ている型」と「売っている商品の型」が食い違う）。

⚠️ 何度流しても害がない（UPSERT・表示専用なので焼き付けない）。朝に並び予想が
   未公開だったレースは昼・夕の波で正しい型へ上書きされる。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.type_lab import race_shape                # noqa: E402

# 🔴 予測と DB の読み出しは `build_type_lab_picks` から借りる。同じ関数を通すので
#    「行を作ったときの並び」と「商品を組んだときの並び」がずれない。
from scripts.build_type_lab_picks import (         # noqa: E402
    _load_entries, _load_race_meta, predict_p3_pw,
)

COLS = ("race_key race_date n_entries type_label axis_sum arare gap pw_ent "
        "axis1 axis2 p3_order").split()

#: 型判定に要る最低車数（`race_shape` が 3着内率3車未満で None を返す）。
MIN_CARS = 3


def _keys_of_date(day: str) -> list[str]:
    """その日の**全レース**（車数で絞らない）。"""
    with get_connection() as c:
        return [r[0] for r in c.execute(
            "SELECT race_key FROM wt_races WHERE race_date = ? ORDER BY race_key",
            (day,)).fetchall()]


def build(day: str) -> list[dict]:
    keys = _keys_of_date(day)
    if not keys:
        print(f"[shape] {day}: レースがありません")
        return []
    meta_all = _load_race_meta(keys)
    ent_all = _load_entries(keys)
    p3, pw = predict_p3_pw(day)
    if not p3:
        print("[shape] 特徴量が作れませんでした")
        return []

    out: list[dict] = []
    for rk in keys:
        ent = ent_all.get(rk)
        probs = p3.get(rk)
        if not ent or not probs or len(probs) < MIN_CARS:
            continue
        cars = {c: ent[c] for c in ent if c in probs}
        if len(cars) < MIN_CARS:
            continue
        m = meta_all.get(rk)
        if not m:
            continue
        shape = race_shape(
            {c: probs[c] for c in cars},
            {c: v["line_group"] for c, v in cars.items()},
            {c: v["line_pos"] for c, v in cars.items()},
            {c: v["style"] for c, v in cars.items()},
            {c: v["race_point"] for c, v in cars.items()},
            {c: v["behind"] for c, v in cars.items()},
            m.get("day_index") or 0,
            {c: (pw.get(rk) or {}).get(c) for c in cars
             if (pw.get(rk) or {}).get(c) is not None} or None,
        )
        if shape is None:
            continue
        out.append(dict(
            race_key=rk, race_date=str(m["race_date"]), n_entries=len(cars),
            type_label=shape.type_label, axis_sum=round(shape.axis_sum, 4),
            arare=shape.arare, gap=round(shape.gap, 4),
            pw_ent=round(shape.pw_ent, 6),
            axis1=shape.order[0], axis2=shape.order[1],
            p3_order="-".join(str(c) for c in shape.order),
        ))
    return out


def save(rows: list[dict]) -> int:
    if not rows:
        return 0
    ph = ",".join("?" * len(COLS))
    upd = ", ".join(f"{c}=excluded.{c}" for c in COLS if c != "race_key")
    sql = (f"INSERT INTO race_shapes ({', '.join(COLS)}) VALUES ({ph}) "
           f"ON CONFLICT (race_key) DO UPDATE SET {upd}, computed_at = CURRENT_TIMESTAMP")
    with get_connection() as c:
        for r in rows:
            c.execute(sql, tuple(r[k] for k in COLS))
        c.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = build(a.date)
    by_car: dict[int, int] = {}
    for r in rows:
        by_car[r["n_entries"]] = by_car.get(r["n_entries"], 0) + 1
    detail = " ".join(f"{k}車{v}R" for k, v in sorted(by_car.items()))
    if a.dry_run:
        print(f"[shape] {a.date}: {len(rows)}R（{detail}）dry-run")
        return
    print(f"[shape] {a.date}: {save(rows)}R を保存（{detail}）")


if __name__ == "__main__":
    main()
