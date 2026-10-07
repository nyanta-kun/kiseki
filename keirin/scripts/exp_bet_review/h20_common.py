"""H20 共通: レース単位の層ラベル・層の回収率・除外つきラインナップ。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h20_common.py labels    # ラベルの分布を表示

事前登録 (README「事前登録 — H20」) の層の次元だけを使う。次元は足さない。
- レース次元（全レースに付く）: 場・周長・級班・種別・開催日目・時間帯・型・axis_sum 五分位・ライン数・単騎の数・wt_overlap_n・月
- 商品次元（①で売った行の商品で決まる）: 型×プラン・予測合成オッズ五分位・平均想定払戻五分位
解釈の固定は reports/exp_H20.md §0 に書いた（走らせる前）。
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C   # noqa: E402

D = C.D
H1_END = C.H1_END
JST = timezone(timedelta(hours=9))
# backend/src/api/keirin_meeting.py と同じ境界（第1R 発走の「時」）
DAY_FROM_HOUR, NIGHTER_FROM_HOUR, MIDNIGHT_FROM_HOUR = 9, 12, 18
RACE_DIMS = ["場", "周長", "級班", "種別", "開催日目", "時間帯", "型", "axis_sum五分位", "ライン数", "単騎の数", "wt_overlap_n", "月"]
PROD_DIMS = ["型×プラン", "予測合成オッズ五分位", "平均想定払戻五分位"]
ALL_DIMS = RACE_DIMS + PROD_DIMS


def meeting_band(h):
    if h is None:
        return "不明"
    if h >= MIDNIGHT_FROM_HOUR:
        return "ミッドナイト"
    if h >= NIGHTER_FROM_HOUR:
        return "ナイター"
    if h >= DAY_FROM_HOUR:
        return "デイ"
    return "モーニング"


def quint_cuts(v):
    return np.percentile(np.asarray(v, float), [20, 40, 60, 80])


def quint(x, cuts):
    return "Q%d" % (1 + int(np.searchsorted(cuts, x, side="right")))


def race_labels(recs, start):
    """rec['i'] -> {次元: 値}（レース次元）と、五分位の切点（上期の全レース）を返す。"""
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    keyidx = {str(k): j for j, k in enumerate(z["KEY"])}
    bank = json.load(open(D / "h18" / "bank_length.json"))
    first = {}
    for rk, (venue, st) in start.items():
        try:
            ts = int(str(st))
        except (TypeError, ValueError):
            continue
        h = datetime.fromtimestamp(ts, JST)
        k = (rk[:8], str(venue))
        first[k] = min(first.get(k, 99.0), h.hour + h.minute / 60)
    axis_h1 = [r["axis"] for r in recs.values() if r["day"] <= H1_END]
    cut_axis = quint_cuts(axis_h1)
    out = {}
    for rec in recs.values():
        j = keyidx[rec["key"]]
        ven = str(z["VENUE"][j])
        rt = str(rec["rtype"])
        challenge = rt.startswith("チャレンジ")
        core = rt[len("チャレンジ"):] if challenge else rt
        grade = "チャレンジ" if challenge else ("S" if str(z["GRADE"][j]) == "S級" else "A")
        LG = z["LG"][j]; MK = z["A_prediction_mark"][j]; P3 = z["P3"][j]
        groups = defaultdict(list)
        for c in range(1, 8):
            groups[str(LG[c - 1])].append(c)
        order = sorted(range(1, 8), key=lambda c: (-float(P3[c - 1]), c))
        wt = {c for c in range(1, 8) if float(MK[c - 1]) in (1.0, 2.0)}
        ov = len(set(order[:2]) & wt) if wt else -1       # -1 = ◎○ の印が無い
        fh = first.get((rec["day"].replace("-", ""), ven))
        out[rec["i"]] = {
            "場": ven, "周長": str(int(bank.get(ven, 0))), "級班": grade, "種別": core,
            "開催日目": str(int(z["DAYI"][j])), "時間帯": meeting_band(fh), "型": rec["tl"],
            "axis_sum五分位": quint(rec["axis"], cut_axis),
            "ライン数": str(len(groups)), "単騎の数": str(sum(1 for v in groups.values() if len(v) == 1)),
            "wt_overlap_n": ("印なし" if ov < 0 else str(ov)), "月": rec["day"][5:7],
        }
    return out, cut_axis


def prod_cuts(recs, lineup_days):
    """商品次元の五分位切点。上期に売った行（その版のラインナップ）で1回だけ引く。"""
    syn, mean = [], []
    for day, (sold, _) in lineup_days.items():
        if day > H1_END:
            continue
        for s in sold:
            syn.append(s["mean"] / s["inv"]); mean.append(s["mean"])
    return quint_cuts(syn), quint_cuts(mean)


def sold_labels(s, rec_labels, rec, cuts):
    """売った行 s の全次元ラベル。"""
    d = dict(rec_labels)
    d["型×プラン"] = f"{rec['tl']}×{s['plan']}"
    d["予測合成オッズ五分位"] = quint(s["mean"] / s["inv"], cuts[0])
    d["平均想定払戻五分位"] = quint(s["mean"], cuts[1])
    return d


# ───────────────────────────── 日配列（層別・除外後の集計） ─────────────────────────────
def day_index(days):
    return {d: i for i, d in enumerate(days)}


def stratum_day_sums(sold_all, days, labels_by_row):
    """{(次元, 値): (inv[日], pay[日], hit[日], n[日])}。labels_by_row[i] = 売った行 i の全次元ラベル。"""
    pos = day_index(days)
    acc = {}
    for (day, s), lab in zip(sold_all, labels_by_row):
        for dim, val in lab.items():
            key = (dim, val)
            if key not in acc:
                acc[key] = np.zeros((4, len(days)))
            a = acc[key]
            p = pos[day]
            a[0, p] += s["inv"]; a[1, p] += s["pay"]; a[2, p] += s["pay"] > s["inv"]; a[3, p] += 1
    return acc


def lineup_with_skip(recs, morning, *, with_lead, skip_keys_by_day):
    """除外レースを forced='skip' にして日次ラインナップを組む。"""
    out = {}
    for day, rows in C.by_day(recs).items():
        forced = {k: "skip" for k in skip_keys_by_day.get(day, ())}
        out[day] = C.run_day(rows, morning, legacy=False, with_lead=with_lead, forced=forced)
    return out


def arr_from_lineup(L, days):
    A = C.DayArr(days)
    for day, (sold, _) in L.items():
        for s in sold:
            A.add(day, s["inv"], s["pay"], s["plan"])
    return A


def highpay_per_day(L, days):
    return np.array([sum(1 for s in L[d][0] if s["slot"] == "highpay") for d in days], float)


def lead_per_day(L, days):
    return np.array([sum(1 for s in L[d][0] if s["slot"] == "lead") for d in days], float)


if __name__ == "__main__":
    recs, start = C.load_base()
    lab, cut = race_labels(recs, start)
    import collections
    print("axis 五分位の切点", cut)
    for dim in RACE_DIMS:
        print(dim, sorted(collections.Counter(v[dim] for v in lab.values()).items()))
