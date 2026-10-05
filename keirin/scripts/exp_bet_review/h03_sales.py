#!/usr/bin/env python3
"""H03 売上側（弱い証拠・記述のみ）: 2026-08-01〜 の netkeirin_sales_race × wt_races × netkeirin_submissions。
読み取りのみ。決勝(完全一致)/準決勝系/ガールズ決勝/その他 × 発走時間帯 の 1本あたり販売有償pt。"""
from __future__ import annotations
import sys, collections
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import q

JST = timezone(timedelta(hours=9))


def grp(rt):
    rt = rt or ""
    if rt in ("決勝", "チャレンジ決勝"):
        return "決勝(完全一致)"
    if "準決勝" in rt:
        return "準決勝系"
    if "決勝" in rt:
        return "他の決勝(ガールズ等)"
    return "その他"


def band(h):
    return "朝(~11時)" if h < 12 else "昼(12-14時)" if h < 15 else "夕(15-17時)" if h < 18 else "夜(18時~)"


def main():
    c, rows = q("""select s.race_key, s.race_date, s.sold_paid_points, s.n_sold, w.race_type, w.start_at, w.n_entries
                   from keirin.netkeirin_sales_race s join keirin.wt_races w on w.race_key=s.race_key""")
    c2, sub = q("""select netkeirin_race_id, race_key, rank_key, origin from keirin.netkeirin_submissions
                   where deleted_at is null and status in ('published','submitted','proposed')""")
    sm = {}
    for rid, rk, rank, origin in sub:
        sm[rk] = (rank, origin)
    data = []
    for rk, d, pt, ns, rt, st, ne in rows:
        h = datetime.fromtimestamp(int(st), JST).hour if st else None
        if h is None:
            continue
        data.append(dict(k=rk, day=d, pt=pt or 0, ns=ns or 0, g=grp(rt), b=band(h), ne=ne, rt=rt, sub=sm.get(rk)))
    print(f"対象 {len(data)} 本（2026-08-01〜10-04・商品が売られたレース）  日数 {len({x['day'] for x in data})}")
    # 日平均（日固定効果の近似: log(1+pt) − その日の全レース平均）
    byday = collections.defaultdict(list)
    for x in data: byday[x["day"]].append(np.log1p(x["pt"]))
    dmean = {d: np.mean(v) for d, v in byday.items()}
    for x in data: x["dl"] = np.log1p(x["pt"]) - dmean[x["day"]]

    def show(title, items, keyf):
        print(f"\n## {title}")
        print("| 区分 | 本数 | 平均pt/本 | 中央pt/本 | 販売0の割合 | 日固定効果後 log差(平均) |")
        print("|---|---|---|---|---|---|")
        g = collections.defaultdict(list)
        for x in items: g[keyf(x)].append(x)
        for k in sorted(g):
            v = g[k]; pts = np.array([x["pt"] for x in v])
            print(f"| {k} | {len(v)} | {pts.mean():,.0f} | {np.median(pts):,.0f} | {np.mean(pts==0)*100:.1f}% | {np.mean([x['dl'] for x in v]):+.2f} |")
    show("種別別（全時間帯・7車9車込み）", data, lambda x: x["g"])
    show("7車のみ・種別別", [x for x in data if x["ne"] == 7], lambda x: x["g"])
    show("種別 × 時間帯", data, lambda x: f"{x['g']} / {x['b']}")
    show("決勝(完全一致)・7車 × 商品の出どころ", [x for x in data if x["g"].startswith("決勝(完全") and x["ne"] == 7],
         lambda x: (x["sub"][1] if x["sub"] else "不明"))
    show("決勝(完全一致)・7車 × プラン(rank_key)", [x for x in data if x["g"].startswith("決勝(完全") and x["ne"] == 7],
         lambda x: (x["sub"][0] if x["sub"] else "不明"))
    show("非決勝・7車 × プラン(rank_key)（比較用）", [x for x in data if not x["g"].startswith("決勝(完全") and x["ne"] == 7],
         lambda x: (x["sub"][0] if x["sub"] else "不明"))
    # 1日あたり決勝本数
    fd = collections.Counter(x["day"] for x in data if x["g"].startswith("決勝(完全"))
    print(f"\n決勝(完全一致)の売った本数/日: 平均 {np.mean(list(fd.values())):.2f}（決勝のある日 {len(fd)} 日）  合計 {sum(fd.values())} 本  決勝からの販売有償pt 合計 {sum(x['pt'] for x in data if x['g'].startswith('決勝(完全')):,}  全体の {sum(x['pt'] for x in data if x['g'].startswith('決勝(完全'))/sum(x['pt'] for x in data)*100:.1f}%")
    print(f"全種別の平均 pt/本 {np.mean([x['pt'] for x in data]):,.0f}（全 {len(data)} 本）")

main()
