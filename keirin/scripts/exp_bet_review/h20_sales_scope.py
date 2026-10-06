#!/usr/bin/env python3
"""H20 追補: 種別「特一般」「特予選」の商品を売らなかった場合の売上への影響（実売・記述のみ）。

2026-08-27〜の公開済み商品について、1本あたり販売有償pt と日売上に占める割合を、
対象種別とそれ以外で比べる。同日・同時間帯の他商品との差（開催日ブートストラップ）も出す。
DB 読み取りのみ。
"""
import collections
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_bet_review._db import q  # noqa: E402

JST = timezone(timedelta(hours=9))
TARGET = {"特一般", "特予選"}


def band(h):
    return "朝" if h < 12 else "昼" if h < 15 else "夕" if h < 18 else "夜"


cols, rows = q("""select s.race_key, s.race_date, s.sold_paid_points, w.race_type, w.start_at, b.rank_key, b.origin
                  from keirin.netkeirin_sales_race s
                  join keirin.wt_races w on w.race_key=s.race_key
                  join keirin.netkeirin_submissions b on b.race_key=s.race_key and b.deleted_at is null
                       and b.status='published'
                  where s.race_date >= '20260827'""")
recs = []
for rk, d, pt, rt, st, rank, origin in rows:
    h = datetime.fromtimestamp(int(st), JST).hour if st else None
    recs.append(dict(k=rk, day=str(d), pt=float(pt or 0), rt=(rt or "").strip(), rank=rank,
                     b=band(h) if h is not None else "?", tgt=(rt or "").strip() in TARGET))

days = sorted({r["day"] for r in recs})
print(f"期間 {days[0]}〜{days[-1]}  {len(days)}日  公開商品 {len(recs)}本")
print("種別の値（対象を含むもの）:", sorted({r['rt'] for r in recs if '特' in r['rt']}))
for name, f in [("対象（特一般・特予選）", lambda r: r["tgt"]), ("それ以外", lambda r: not r["tgt"])]:
    xs = [r for r in recs if f(r)]
    pts = np.array([r["pt"] for r in xs])
    print(f"{name}: {len(xs)}本 = {len(xs)/len(days):.2f}本/日  1本平均 {pts.mean():.0f}pt  中央 {np.median(pts):.0f}pt"
          f"  販売0 {np.mean(pts == 0):.1%}  売上合計に占める割合 {pts.sum()/sum(r['pt'] for r in recs):.1%}")
for t in sorted(TARGET):
    xs = [r["pt"] for r in recs if r["rt"] == t]
    if xs:
        print(f"  {t}: {len(xs)}本  1本平均 {np.mean(xs):.0f}pt  中央 {np.median(xs):.0f}pt")

# 時間帯の内訳
print("時間帯の内訳（対象 / それ以外の本数・1本平均pt）")
for b in "朝昼夕夜":
    a = [r["pt"] for r in recs if r["tgt"] and r["b"] == b]
    o = [r["pt"] for r in recs if not r["tgt"] and r["b"] == b]
    print(f"  {b}: 対象 {len(a)}本 {np.mean(a) if a else float('nan'):.0f}pt / それ以外 {len(o)}本 {np.mean(o) if o else float('nan'):.0f}pt")

# 同日・同時間帯の他商品との差（対象1本ごと）
cell = collections.defaultdict(list)
for r in recs:
    if not r["tgt"]:
        cell[(r["day"], r["b"])].append(r["pt"])
items = [(r["day"], r["pt"] - np.mean(cell[(r["day"], r["b"])]))
         for r in recs if r["tgt"] and cell.get((r["day"], r["b"]))]
byd = collections.defaultdict(list)
for d, x in items:
    byd[d].append(x)
ds = sorted(byd)
rng = np.random.default_rng(0)
boot = []
for _ in range(2000):
    s = rng.choice(len(ds), len(ds))
    v = [x for i in s for x in byd[ds[i]]]
    boot.append(np.mean(v))
print(f"同日・同時間帯の他商品との差: {np.mean([x for _, x in items]):+.0f}pt/本 "
      f"[{np.percentile(boot, 2.5):+.0f}, {np.percentile(boot, 97.5):+.0f}]  （{len(items)}本・{len(ds)}日）")

# 日売上: 対象を除いた場合の機械的な減り（置き換えなしの上限）
tot = collections.defaultdict(float); tg = collections.defaultdict(float)
for r in recs:
    tot[r["day"]] += r["pt"]
    if r["tgt"]:
        tg[r["day"]] += r["pt"]
share = [tg[d] / tot[d] for d in days if tot[d] > 0]
print(f"日売上に占める対象の割合: 平均 {np.mean(share):.1%}  中央 {np.median(share):.1%}  最大 {max(share):.1%}")
print(f"対象の売上（手取り換算 0.21円/pt）: 1日平均 {np.mean([tg[d] for d in days]):.0f}pt ≈ {np.mean([tg[d] for d in days])*0.21:.0f}円")
