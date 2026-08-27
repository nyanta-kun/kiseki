"""型ラボの「分割の答え合わせ」— 事前の型分けが決着と合っていたかを表にする。

2026-08-27 新設。DB にも FastAPI にも依存しない**純関数だけ**（`keirin_sales_analysis.py`
と同じ方針）。API は `keirin_type_lab_router` から呼ぶ。

## 何を検証するか

型ラボは1レースを **① 軸の堅さ（`axis_sum`）× ②〜⑥ 荒れ度（`arare`）** で6型に分ける
（`keirin/src/type_lab.py`）。その主張は3層に分かれていて、層ごとに支配するものが違う:

| 層 | 事前の量 | 支配するもの | ここで見る表 |
|---|---|---|---|
| ① 軸の堅さ | `axis_sum`（境界 1.44） | **的中率** | 型 × 決着クラス |
| ② 相手の開き | `gap` | **3着の出どころ** | 相手の開き × 決着クラス |
| ③〜⑥ 荒れ度 | `arare` | **配当** | 型 × 確定三連単オッズ帯 |

🔴 **「型が当たっている」＝「儲かる」ではない。** 型は edge を作らず、決めるのは
   「同じ買い方でどの帯へ落ちるか」と「どのレースを拾えるか」だけ
   （`docs/type_lab/SUMMARY.md` 2.6）。ここで見るのは**分割の再現性**であって収支ではない。

## 決着クラス（列）

指数（3着内率）順位で、1〜3着に入った3車がどこから来たかを5つに分ける。

| key | 意味 |
|---|---|
| `firm34`   | **順当** — 指数1位・2位がそろい、3着も指数3〜4位 |
| `firm_ana` | 指数1位・2位はそろったが、もう1車が**指数5〜7位** |
| `half34`   | 軸は1車だけ。残り2車は指数3〜4位 |
| `half_ana` | 軸は1車だけで、**指数5〜7位**を含む |
| `broken`   | **軸崩壊** — 指数1位も2位も3着以内に入らなかった |

🔴 `p3_order`（行を作った時点の並び）が無い行は**分類しない**。後から `wt_entries` を
   引き直して並べ直すと、モデルの再学習ぶんだけ当時と違う並びになる。
   分類できなかった件数は必ず返して画面に出す（黙って落とすと母集団が縮む）。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import median
from typing import Any

#: 決着クラス（列）の定義。並び順がそのまま表の列順になる。
FINISH_CLASSES: list[dict[str, str]] = [
    {"key": "firm34", "label": "順当", "note": "軸2車 + 指数3〜4位"},
    {"key": "firm_ana", "label": "軸2+穴", "note": "軸2車 + 指数5〜7位"},
    {"key": "half34", "label": "片軸+中位", "note": "軸1車 + 指数3〜4位ばかり"},
    {"key": "half_ana", "label": "片軸+穴", "note": "軸1車 + 指数5〜7位を含む"},
    {"key": "broken", "label": "軸崩壊", "note": "指数1位も2位も3着外"},
]

#: 確定三連単オッズの帯。境界は 2026-01〜08 の実測分布から
#: （四分位が 13.2 / 38.1 / 108.6 倍・上位1割が 284.6 倍）。
PAYOUT_BANDS: list[dict[str, Any]] = [
    {"key": "lt10", "label": "〜10倍", "lo": 0.0, "hi": 10.0},
    {"key": "10_30", "label": "10〜30倍", "lo": 10.0, "hi": 30.0},
    {"key": "30_100", "label": "30〜100倍", "lo": 30.0, "hi": 100.0},
    {"key": "100_300", "label": "100〜300倍", "lo": 100.0, "hi": 300.0},
    {"key": "ge300", "label": "300倍〜", "lo": 300.0, "hi": None},
]

TYPE_ORDER = ["A", "B", "C", "D", "E", "F"]
TYPE_NAME = {"A": "鉄板", "B": "堅い・中", "C": "堅いが崩れ筋",
             "D": "混戦・軸あり", "E": "混戦・中", "F": "大混戦"}


# ───────────────────────────── 1行ぶんの分類 ─────────────────────────────

def finishers(win_combo: str | None) -> tuple[int, ...]:
    """`win_combo` から1〜3着の車番。三連単 "1-2-3" と三連複 "1=2=3" の両方を受ける。

    分類に使うのは**集合**（誰が来たか）だけなので、順序の有無は問わない。
    """
    if not win_combo:
        return ()
    parts = str(win_combo).replace("=", "-").replace("→", "-").split("-")
    out = tuple(int(p) for p in parts if p.strip().isdigit())
    return out if len(out) == 3 else ()


def index_ranks(p3_order: str | None) -> dict[int, int]:
    """`p3_order`（"3-1-5-…"）→ {車番: 指数順位（1始まり）}。"""
    if not p3_order:
        return {}
    cars = [int(x) for x in str(p3_order).split("-") if x.strip().isdigit()]
    return {c: i + 1 for i, c in enumerate(cars)}


def finish_class(win_combo: str | None, p3_order: str | None) -> str | None:
    """決着クラス（`FINISH_CLASSES` の key）。分類できなければ None。

    🔴 `axis1`/`axis2` ではなく `p3_order` の先頭2つを軸とみなす（同じ値だが、
       並び全体が無いと3着の出どころが分からないので、どのみち `p3_order` が要る）。
    """
    fin = finishers(win_combo)
    rank = index_ranks(p3_order)
    if not fin or not rank:
        return None
    try:
        ranks = sorted(rank[c] for c in fin)
    except KeyError:
        return None                      # 出走表に無い車番（欠場の繰り上がり等）
    n_axis = sum(1 for r in ranks if r <= 2)
    others = [r for r in ranks if r > 2]
    if n_axis >= 2:
        return "firm34" if others and others[0] <= 4 else "firm_ana"
    if n_axis == 1:
        return "half34" if all(r <= 4 for r in others) else "half_ana"
    return "broken"


def payout_band(win_tf_odds: float | None) -> str | None:
    """確定三連単オッズ → 帯の key。"""
    if win_tf_odds is None:
        return None
    try:
        v = float(win_tf_odds)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    for b in PAYOUT_BANDS:
        if b["hi"] is None or v < b["hi"]:
            return str(b["key"])
    return str(PAYOUT_BANDS[-1]["key"])


# ───────────────────────────── 集計 ─────────────────────────────

def _cells(counts: dict[str, int], columns: Sequence[dict], n: int,
           hits: dict[str, int] | None = None) -> list[dict[str, Any]]:
    out = []
    for col in columns:
        k = str(col["key"])
        c = counts.get(k, 0)
        cell: dict[str, Any] = {"key": k, "n": c,
                                "pct": round(c / n * 100, 1) if n else 0.0}
        if hits is not None:
            h = hits.get(k, 0)
            cell["n_hit"] = h
            cell["hit_rate"] = round(h / c * 100, 1) if c else 0.0
        out.append(cell)
    return out


def _row(key: str, label: str, items: Sequence[dict[str, Any]],
         field: str, columns: Sequence[dict],
         hit_field: str | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    hits: dict[str, int] = {}
    for it in items:
        k = it.get(field)
        if k is None:
            continue
        counts[k] = counts.get(k, 0) + 1
        if hit_field and it.get(hit_field):
            hits[k] = hits.get(k, 0) + 1
    n = sum(counts.values())
    odds = [float(it["win_tf_odds"]) for it in items if it.get("win_tf_odds")]
    return {
        "key": key, "label": label, "n": n,
        "median_tf_odds": round(median(odds), 1) if odds else None,
        "cells": _cells(counts, columns, n, hits if hit_field else None),
    }


def _tertiles(values: Sequence[float]) -> tuple[float, float] | None:
    """3分位の境界。値が足りなければ None（＝その表は出さない）。"""
    v = sorted(float(x) for x in values)
    if len(v) < 30:
        return None
    return v[len(v) // 3], v[len(v) * 2 // 3]


def build_outcome(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """`type_lab_picks` の行から答え合わせの表を作る。

    `rows` は `race_key` / `plan_key` / `type_label` / `gap` / `settled_at` /
    `hit` / `win_combo` / `p3_order` / `win_tf_odds` を持つ辞書の並び。

    🔴 **レース単位の表は race_key で重複を落とす。** 1レースに2プラン当たる型
       （A・F）があるので、落とさないとその型だけ2倍に数えられる。
    """
    settled = [dict(r) for r in rows if r.get("settled_at") is not None]
    for r in settled:
        r["finish_class"] = finish_class(r.get("win_combo"), r.get("p3_order"))
        r["payout_band"] = payout_band(r.get("win_tf_odds"))
        r["is_hit"] = bool(r.get("hit"))

    by_race: dict[str, dict[str, Any]] = {}
    for r in settled:
        by_race.setdefault(str(r["race_key"]), r)
    races = list(by_race.values())
    classified = [r for r in races if r["finish_class"]]

    matrices: list[dict[str, Any]] = []

    # ── ① 型 × 決着クラス ──
    rows_type = [
        _row(t, f"{t} {TYPE_NAME.get(t, '')}",
             [r for r in classified if r.get("type_label") == t],
             "finish_class", FINISH_CLASSES)
        for t in TYPE_ORDER
    ]
    matrices.append({
        "key": "type_finish",
        "title": "① 型 × 決着の中身",
        "note": "堅い型ほど「順当」が多く「軸崩壊」が少なければ、軸の堅さの分割は効いている",
        "columns": FINISH_CLASSES,
        "rows": [r for r in rows_type if r["n"]],
        "total": _row("ALL", "合計", classified, "finish_class", FINISH_CLASSES),
    })

    # ── ③ 型 × 確定三連単オッズ帯 ──
    banded = [r for r in races if r["payout_band"]]
    rows_pay = [
        _row(t, f"{t} {TYPE_NAME.get(t, '')}",
             [r for r in banded if r.get("type_label") == t],
             "payout_band", PAYOUT_BANDS)
        for t in TYPE_ORDER
    ]
    matrices.append({
        "key": "type_payout",
        "title": "③ 型 × 決着の配当（三連単の確定オッズ）",
        "note": "荒れ度の分割が効いていれば A→F で中央倍率が単調に上がる",
        "columns": [{"key": b["key"], "label": str(b["label"]), "note": ""}
                    for b in PAYOUT_BANDS],
        "rows": [r for r in rows_pay if r["n"]],
        "total": _row("ALL", "合計", banded, "payout_band", PAYOUT_BANDS),
    })

    # ── ② 相手の開き（gap）× 決着クラス ──
    gaps = [float(r["gap"]) for r in classified if r.get("gap") is not None]
    cut = _tertiles(gaps)
    if cut:
        lo, hi = cut
        def _band(r: dict) -> str:
            g = float(r["gap"])
            return "low" if g < lo else ("mid" if g < hi else "high")
        gap_rows = []
        for k, label in (("low", f"開き 小（〜{lo:.2f}）"),
                         ("mid", f"開き 中（{lo:.2f}〜{hi:.2f}）"),
                         ("high", f"開き 大（{hi:.2f}〜）")):
            items = [r for r in classified
                     if r.get("gap") is not None and _band(r) == k]
            gap_rows.append(_row(k, label, items, "finish_class", FINISH_CLASSES))
        matrices.append({
            "key": "gap_finish",
            "title": "② 相手の開き × 決着の中身",
            "note": "開きが大きいほど3着が指数3〜4位から出る（＝穴が減る）はず",
            "columns": FINISH_CLASSES,
            "rows": [r for r in gap_rows if r["n"]],
            "total": None,
        })

    # ── プラン × 決着クラス（どこで外したか）──
    plans = sorted({str(r["plan_key"]) for r in settled if r.get("finish_class")})
    plan_rows = [
        _row(p, p, [r for r in settled
                    if r["finish_class"] and str(r["plan_key"]) == p],
             "finish_class", FINISH_CLASSES, hit_field="is_hit")
        for p in plans
    ]
    matrices.append({
        "key": "plan_finish",
        "title": "プラン × 決着の中身（各セルは レース数 / 的中率）",
        "note": "そのプランがどの決着で取れて、どの決着で落としているか",
        "columns": FINISH_CLASSES,
        "rows": [r for r in plan_rows if r["n"]],
        "total": None,
    })

    return {
        "n_races": len(classified),
        "n_races_settled": len(races),
        "n_unclassified": len(races) - len(classified),
        "n_no_payout": len(races) - len(banded),
        "matrices": matrices,
    }
