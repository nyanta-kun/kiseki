#!/usr/bin/env python3
"""型ラボの6型分割を **9車・6車** でも測り、車数をまたいで1つに束ねられるかを見る。

🔴 **本番の依存を先に読んだ**（CLAUDE.md「検証の作法」#1）:
   - `race_shape()` の論理自体は車数非依存。`build_legs()` も `shape.order` から
     実際の車番を引くので車数に依存しない。`PERMS3` は**未使用の残骸**
   - 車数に効くのは3つだけ:
       AXIS_SUM_FIRM = 1.44   （7C の定数。9C は 1.30）
       BEHIND_MID    = 11.0   （7車の実測中央値）
       N_ENTRIES     = 7      （build_type_lab_picks の抽出条件）
   - 🔴 **予測オッズは 三連単=7車のみ / 三連複=7車と9車**。6車は両方無い
     ＝ 6車では帯ゲート（20倍/30倍）も配分も作れない

したがってここで測るのは **買い目の成績ではなく型分割そのもの**（確定オッズと着順だけ）。
    層① 軸の堅さ → 二軸そろい率（＝的中率を支配する量）
    層②〜⑥ 荒れ度 → 確定配当（三連複・三連単）

出力は3本立て:
    1) 車数別の axis_sum 分布（絶対閾値 1.44 が車数で何を意味するか）
    2) 車数ごとの型別の分離（自車数の中央値で切った場合と 1.44 で切った場合）
    3) 車数横断で束ねられるか（正規化した堅さ指標）
"""
from __future__ import annotations

import argparse
import glob
import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402
from src.type_lab import AXIS_SUM_FIRM, race_shape  # noqa: E402

WALL = 74.85


# ─────────────────────────── 読み込み ───────────────────────────

def load_wf_preds(n_car: int, d1: str, d2: str) -> dict[str, dict[int, dict]]:
    """walk-forward の p3/pw。9車は専用キャッシュ、7車は f60 キャッシュ。"""
    pat = "wf_preds9_*.pkl" if n_car == 9 else "wf_preds_*_f60_*.pkl"
    frames = []
    for f in sorted(glob.glob(str(REPO / "data" / "exp_cache" / pat))):
        frames.append(pd.read_pickle(f))
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for rk, fn, p3, pw in zip(df["race_key"], df["frame_no"], df["pp3"], df["ppw"]):
        rk = str(rk)
        if not (d1.replace("-", "") <= rk[:8] <= d2.replace("-", "")):
            continue
        out[rk][int(fn)] = {"p3": float(p3), "pw": float(pw)}
    return dict(out)


def load_races(keys: list[str]) -> dict[str, dict]:
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, race_date, race_type, day_index, n_entries "
                 f"FROM wt_races WHERE race_key IN ({','.join('?' * len(ch))})")
            for r in c.execute(q, ch).fetchall():
                d = dict(zip(("race_key", "race_date", "race_type",
                              "day_index", "n_entries"), tuple(r)))
                out[str(d["race_key"])] = d
    return out


def load_entries(keys: list[str]) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, line_group, line_pos, style, race_point, "
                 "       ex_left_behind_pct, finish_order "
                 f"FROM wt_entries WHERE race_key IN ({','.join('?' * len(ch))})")
            for r in c.execute(q, ch).fetchall():
                d = dict(zip(("race_key", "frame_no", "line_group", "line_pos",
                              "style", "race_point", "behind", "finish"), tuple(r)))
                out[str(d["race_key"])][int(d["frame_no"])] = d
    return dict(out)


def load_final_odds(keys: list[str]) -> dict[str, dict[str, float]]:
    """確定オッズ。{race_key: {'trio': 的中組のオッズ, 'trifecta': 同}}。"""
    fin: dict[str, list[tuple[int, int]]] = {}
    ent = load_entries(keys)
    for rk, cars in ent.items():
        got = sorted(((int(v["finish"] or 0), c) for c, v in cars.items()
                      if v["finish"] and int(v["finish"]) >= 1))
        if len(got) >= 3 and [g[0] for g in got[:3]] == [1, 2, 3]:
            fin[rk] = got[:3]
    out: dict[str, dict[str, float]] = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = [k for k in keys[i:i + 900] if k in fin]
            if not ch:
                continue
            q = ("SELECT race_key, bet_type, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type IN ('trio', 'trifecta')")
            board: dict[tuple, float] = {}
            for rk, bt, combo, od in (tuple(r) for r in c.execute(q, ch).fetchall()):
                board[(str(rk), str(bt), str(combo))] = float(od or 0)
            for rk in ch:
                cars = [g[1] for g in fin[rk]]
                trio = "=".join(str(x) for x in sorted(cars))
                tf = "-".join(str(x) for x in cars)
                for bt, key in (("trio", trio), ("trifecta", tf)):
                    v = board.get((rk, bt, key))
                    if v:
                        out[rk][bt] = v
    return dict(out)


# ─────────────────────────── 型を作る ───────────────────────────

def shapes_for(n_car: int, d1: str, d2: str, firm_cut: float | None):
    preds = load_wf_preds(n_car, d1, d2)
    if not preds:
        return []
    keys = [k for k, v in preds.items() if len(v) == n_car]
    races = load_races(keys)
    keys = [k for k in keys
            if races.get(k) and int(races[k].get("n_entries") or 0) == n_car]
    ent = load_entries(keys)
    odds = load_final_odds(keys)
    rows = []
    for rk in keys:
        cars = ent.get(rk) or {}
        if len(cars) != n_car:
            continue
        p3 = {c: preds[rk][c]["p3"] for c in preds[rk]}
        sh = race_shape(
            p3,
            {c: v["line_group"] for c, v in cars.items()},
            {c: v["line_pos"] for c, v in cars.items()},
            {c: str(v["style"] or "") for c, v in cars.items()},
            {c: float(v["race_point"] or 0) for c, v in cars.items()},
            {c: float(v["behind"] or 0) for c, v in cars.items()},
            int(races[rk].get("day_index") or 0),
        )
        if sh is None:
            continue
        top3 = {c for c, v in cars.items()
                if v["finish"] and 1 <= int(v["finish"]) <= 3}
        rows.append({
            "race_key": rk, "n_car": n_car,
            "date": str(races[rk]["race_date"]),
            "race_type": races[rk].get("race_type"),
            "axis_sum": sh.axis_sum, "arare": sh.arare,
            "label7": sh.type_label,            # 1.44 で切った本番どおりの型
            "label": _relabel(sh, firm_cut),    # 指定の境界で切り直した型
            "both_axis": int(sh.order[0] in top3 and sh.order[1] in top3),
            "trio_odds": (odds.get(rk) or {}).get("trio"),
            "tf_odds": (odds.get(rk) or {}).get("trifecta"),
        })
    return rows


def _relabel(sh, cut: float | None) -> str:
    if cut is None:
        return sh.type_label
    firm = sh.axis_sum >= cut
    s = sh.arare
    if firm:
        return "A" if s <= -1 else ("B" if s == 0 else "C")
    return "D" if s <= -1 else ("E" if s == 0 else "F")


# ─────────────────────────── 表示 ───────────────────────────

def _med(v):
    v = [x for x in v if x]
    return stx.median(v) if v else 0.0


def show(rows: list[dict], title: str) -> None:
    print(f"\n== {title}  n={len(rows)}")
    if not rows:
        return
    g = defaultdict(list)
    for r in rows:
        g[r["label"]].append(r)
    print(f"{'型':4}{'n':>7}{'割合':>8}{'二軸そろい':>11}"
          f"{'三連複 中央':>12}{'三連単 中央':>12}{'万車券率':>9}")
    for k in "ABCDEF":
        v = g.get(k) or []
        if not v:
            continue
        tf = [r["tf_odds"] for r in v if r["tf_odds"]]
        man = sum(1 for o in tf if o >= 100) / len(tf) * 100 if tf else 0
        print(f"{k:4}{len(v):7d}{len(v) / len(rows) * 100:7.1f}%"
              f"{sum(r['both_axis'] for r in v) / len(v) * 100:10.2f}%"
              f"{_med([r['trio_odds'] for r in v]):12.1f}"
              f"{_med(tf):12.1f}{man:8.2f}%")


def _pat(n_car: int) -> str:
    return {9: "wf_preds9_*.pkl", 6: "wf_preds6_*.pkl"}.get(n_car, "wf_preds_*_f60_*.pkl")


def load_wf_any(n_car: int, d1: str, d2: str):
    import glob as _g
    frames = [pd.read_pickle(f) for f in
              sorted(_g.glob(str(REPO / "data" / "exp_cache" / _pat(n_car))))]
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    a, b = d1.replace("-", ""), d2.replace("-", "")
    for rk, fn, p3, pw in zip(df["race_key"], df["frame_no"], df["pp3"], df["ppw"]):
        rk = str(rk)
        if a <= rk[:8] <= b:
            out[rk][int(fn)] = {"p3": float(p3), "pw": float(pw)}
    return dict(out)


def cross(rows: list[dict], title: str) -> None:
    """型 × 車数。**同じ型なら車数が違っても同じ性質か**を見る。"""
    print(f"\n== {title}")
    g = defaultdict(list)
    for r in rows:
        g[(r["label"], r["n_car"])].append(r)
    cars = sorted({r["n_car"] for r in rows})
    print(f"{'型':4}" + "".join(f"{str(c) + '車 n/そろい/三連単中央':>26}" for c in cars))
    for k in "ABCDEF":
        line = f"{k:4}"
        for c in cars:
            v = g.get((k, c)) or []
            if not v:
                line += f"{'—':>26}"
                continue
            sor = sum(x["both_axis"] for x in v) / len(v) * 100
            line += f"{len(v):8d}{sor:8.1f}%{_med([x['tf_odds'] for x in v]):9.1f}"
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2024-07-01")
    ap.add_argument("--to", dest="d2", default="2026-08-04")
    ap.add_argument("--split", default="2025-07-01",
                    help="この日から後を確認窓にする")
    a = ap.parse_args()

    print(f"窓 {a.d1}〜{a.d2}   本番の境界 AXIS_SUM_FIRM={AXIS_SUM_FIRM}")
    print(f"探索 {a.d1}〜{a.split} 未満 / 確認 {a.split}〜{a.d2}")

    allrows = []
    for n in (7, 9, 6):
        rows = shapes_for_n(n, a.d1, a.d2)
        if not rows:
            print(f"\n🔴 {n}車: walk-forward 予測が無く測れない")
            continue
        allrows.extend(rows)
        s = sorted(r["axis_sum"] for r in rows)
        firm = sum(1 for x in s if x >= AXIS_SUM_FIRM) / len(s) * 100
        print(f"\n-- {n}車 axis_sum  n={len(s)}  p25={s[len(s)//4]:.3f} "
              f"中央={s[len(s)//2]:.3f} p75={s[len(s)*3//4]:.3f}  "
              f"→ 1.44 以上は {firm:.1f}%")

    for n in sorted({r["n_car"] for r in allrows}):
        sub = [r for r in allrows if r["n_car"] == n]
        show([r for r in sub if r["date"] < a.split], f"{n}車・探索窓（1.44）")
        show([r for r in sub if r["date"] >= a.split], f"{n}車・確認窓（1.44）")

    print("\n" + "=" * 78)
    cross([r for r in allrows if r["date"] < a.split], "型 × 車数（探索窓・境界1.44）")
    cross([r for r in allrows if r["date"] >= a.split], "型 × 車数（確認窓・境界1.44）")

    print("\n" + "=" * 78)
    show(allrows, "全車数をまとめて1つの体系にした場合（境界1.44・全窓）")
    show([r for r in allrows if r["date"] >= a.split],
         "同・確認窓だけ")


def shapes_for_n(n_car: int, d1: str, d2: str):
    """shapes_for と同じだが、車数ごとのキャッシュを自動で選ぶ。"""
    global load_wf_preds
    orig = load_wf_preds
    load_wf_preds = lambda nc, x, y: load_wf_any(nc, x, y)  # noqa: E731
    try:
        return shapes_for(n_car, d1, d2, None)
    finally:
        load_wf_preds = orig


if __name__ == "__main__":
    main()
