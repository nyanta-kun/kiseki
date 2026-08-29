#!/usr/bin/env python3
"""型ラボの成績が「発走時刻／開催種別」で変わるかを測る（2026-08-29・ユーザー観察）。

## 発端

2026-08-29（型ラボ全面移行の初日）に、午前 5/8 的中 ↔ 午後 2/23 的中
（Fisher 両側 p=0.0056）。**ただし「15時」という切れ目は結果を見てから選んでいる**
ので、これ自体は証拠にならない（1日を切る方法は何通りもある）。

## 測り方

- 母集団: `type_lab_picks` の **paper** 行のうち `SELL_PLANS` の6プラン、採点済み。
  **本番と同じ軸信頼ゲートを掛ける**（掛けないと売っていないレースが混ざる）
- 窓を分ける: **探索 2025 / 確認 2026-01〜08-26**。
  片方だけで良く見える切り方はいくらでも作れるので、**両窓で向きが一致するか**だけを見る
- 量: 表示的中（払戻 >= 賭け金）と ROI。件数も必ず出す

🔴 **時間帯と開催種別と車数と場は絡んでいる**（ミッドナイトは7車・ガールズが多い等）。
   単独の表で差が出ても「時間帯が原因」とは言えない。だから最後に
   **開催種別の中で時間帯を割る**表も出す。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/time_of_day.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402
from src.type_lab import SELL_PLANS                # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)

JST = dt.timezone(dt.timedelta(hours=9))
WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}

# 開催種別の境界は `backend/src/api/keirin_meeting.py` と同じ値
# （そちらは stdlib のみだが FastAPI 経由なのでここでは値を合わせるにとどめ、
#  ずれたら test で気づけるよう定数名も揃えてある）。
DAY_FROM_HOUR, NIGHTER_FROM_HOUR, MIDNIGHT_FROM_HOUR = 9, 12, 18


def meeting_type(first_hour: float | None) -> str:
    if first_hour is None:
        return "unknown"
    if first_hour >= MIDNIGHT_FROM_HOUR:
        return "ミッドナイト"
    if first_hour >= NIGHTER_FROM_HOUR:
        return "ナイター"
    if first_hour >= DAY_FROM_HOUR:
        return "デイ"
    return "モーニング"


def band(h: int | None) -> str:
    if h is None:
        return "unknown"
    for lo, hi, label in ((0, 11, "〜10時"), (11, 15, "11〜14時"),
                          (15, 18, "15〜17時"), (18, 21, "18〜20時"),
                          (21, 24, "21時〜")):
        if lo <= h < hi:
            return label
    return "unknown"


def load() -> list[dict]:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.race_key, t.race_date, t.plan_key, t.axis_sum, t.n_entries, "
            "       t.race_type, t.type_label, t.budget, t.payout, "
            "       r.start_at, r.venue_id, r.cup_grade "
            "FROM type_lab_picks t JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode = ? AND t.race_date BETWEEN ? AND ? "
            "  AND t.settled_at IS NOT NULL AND t.budget > 0",
            ("paper", "2025-01-01", "2026-08-26"))]
    out = []
    first: dict[tuple, int] = {}
    for d in rows:
        if d["plan_key"] not in SELL_PLANS:
            continue
        # 本番と同じゲート（看板は素通し）
        if not is_fill_target(d.get("race_type"), d.get("cup_grade")):
            if not _GATE.passes_axis_gate(
                    str(d["plan_key"]),
                    float(d["axis_sum"]) if d["axis_sum"] is not None else None,
                    int(d["n_entries"]) if d["n_entries"] is not None else None):
                continue
        # 🔴 `race_date` の型はテーブルごとに違う（type_lab_picks は DATE・
        #    wt_races は VARCHAR）。文字列に揃えてから窓で切る。
        d["race_date"] = str(d["race_date"])
        sa = d.get("start_at")
        d["hour"] = (dt.datetime.fromtimestamp(int(sa), JST).hour if sa else None)
        key = (d["venue_id"], d["race_date"])
        if sa and (key not in first or int(sa) < first[key]):
            first[key] = int(sa)
        out.append(d)
    for d in out:
        f = first.get((d["venue_id"], d["race_date"]))
        d["meeting"] = meeting_type(
            dt.datetime.fromtimestamp(f, JST).hour if f else None)
    return out


def tally(rows: list[dict]) -> tuple[int, float, float]:
    n = len(rows)
    bet = sum(int(d["budget"]) for d in rows)
    pay = sum(int(d["payout"] or 0) for d in rows)
    hits = sum(1 for d in rows if int(d["payout"] or 0) >= int(d["budget"]))
    return n, (hits / n if n else 0.0), (pay / bet if bet else 0.0)


def table(rows: list[dict], keyfn, title: str, order=None) -> dict[str, dict]:
    print(f"\n=== {title} ===")
    print(f"{'':<14}" + "".join(f"{w:>28}" for w in WINDOWS))
    got: dict[str, dict] = {}
    keys = sorted({keyfn(d) for d in rows}, key=lambda k: (order.index(k) if order and k in order else 99, k))
    for k in keys:
        line = f"{k:<14}"
        got[k] = {}
        for w, (lo, hi) in WINDOWS.items():
            sub = [d for d in rows if keyfn(d) == k and lo <= d["race_date"] <= hi]
            n, hit, roi = tally(sub)
            got[k][w] = (n, hit, roi)
            line += f"{n:>8,}件 {hit:>6.1%} {roi:>7.1%}" if n else f"{'—':>28}"
        print(line)
    return got


def main() -> int:
    rows = load()
    print(f"母集団: {len(rows):,}行（paper・SELL_PLANS・ゲート適用済み）")
    bands = ["〜10時", "11〜14時", "15〜17時", "18〜20時", "21時〜"]
    t1 = table(rows, lambda d: band(d["hour"]), "① レース自身の発走時刻帯", bands)
    meets = ["モーニング", "デイ", "ナイター", "ミッドナイト"]
    t2 = table(rows, lambda d: d["meeting"], "② 開催種別（その開催の第1R発走）", meets)

    print("\n=== ③ 開催種別の中で発走時刻帯を割る（交絡の切り分け） ===")
    for m in meets:
        sub = [d for d in rows if d["meeting"] == m]
        if len(sub) < 200:
            continue
        table(sub, lambda d: band(d["hour"]), f"開催種別 = {m}", bands)

    print("\n=== 向きの一致（探索と確認で同じ順位か） ===")
    for name, t in (("発走時刻帯", t1), ("開催種別", t2)):
        pairs = [(k, v["探索 2025"], v["確認 2026"]) for k, v in t.items()
                 if v.get("探索 2025", (0,))[0] > 200 and v.get("確認 2026", (0,))[0] > 200]
        if len(pairs) < 3:
            continue
        a = sorted(pairs, key=lambda x: -x[1][1])
        b = sorted(pairs, key=lambda x: -x[2][1])
        print(f"  {name}  表示的中の順位  探索 {[p[0] for p in a]}")
        print(f"  {'':<{len(name)}}                確認 {[p[0] for p in b]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
