#!/usr/bin/env python3
"""鈴木誠(350) を深掘りする。1日5本の選び方・組み方・見解の書き方。"""
from __future__ import annotations
import collections, json, re, statistics as st
from pathlib import Path
from an_465_expand import product_stakes

HERE = Path(__file__).resolve().parent
rs = [json.loads(l) for l in (HERE / "an465" / "350.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
rs.sort(key=lambda r: (r["date"], r["race_no"]))


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


print(f"n={len(rs)}")

# 1) 開催の選び方
by = collections.defaultdict(list)
for r in rs:
    by[r["date"]].append(r)
print("\n### 日別（場・R番号・種別）")
for d in sorted(by):
    xs = sorted(by[d], key=lambda x: x["race_no"])
    print(f"  {d} {xs[0]['venue']:5s} R={[x['race_no'] for x in xs]} "
          f"{[re.sub('^Ｓ級 ','',x['race_name'])[:8] for x in xs]}")

# 2) 買い目の構造
nrows = [len(r["rows"]) for r in rs]
print(f"\n### 買い目ブロック数(=フォーメーション行数): {sorted(collections.Counter(nrows).items())}")
shapes = collections.Counter()
for r in rs:
    for row in r["rows"]:
        c = row["cols"]
        shapes["-".join(str(len(x)) for x in c) if c else "点買い"] += 1
print("  ブロックの形(1着-2着-3着の候補数) 上位15:", shapes.most_common(15))

# 1着1車固定 / 2車の入れ替え
kinds = collections.Counter()
for r in rs:
    for row in r["rows"]:
        c = row["cols"]
        if not c:
            kinds["点買い"] += 1
        elif len(c[0]) == 1:
            kinds["1着1車固定"] += 1
        elif len(c) >= 2 and sorted(c[0]) == sorted(c[1]):
            kinds[f"1・2着同一{len(c[0])}車(入替)"] += 1
        else:
            kinds[f"1着{len(c[0])}車"] += 1
print("  ブロック種別:", kinds.most_common())

# 3) 印と買い目の関係
hon_in_1st, hon_any = 0, 0
mark_first = collections.Counter()
for r in rs:
    m = {x["mark"]: x["num"] for x in r["marks"]}
    firsts = set()
    allnums = set()
    for row in r["rows"]:
        c = row["cols"]
        if c:
            firsts |= set(c[0])
            for col in c:
                allnums |= set(col)
    if "◎" in m:
        hon_in_1st += m["◎"] in firsts
        hon_any += m["◎"] in allnums
    for k, v in m.items():
        if v in firsts:
            mark_first[k] += 1
print(f"\n### 印: ◎が1着候補に入る {hon_in_1st}/{len(rs)} / 買い目のどこかに入る {hon_any}/{len(rs)}")
print("  1着候補に入る印の回数:", mark_first.most_common())
print("  印の数:", sorted(collections.Counter(len(r["marks"]) for r in rs).items()))

# 4) 配分
allv = [v for r in rs for v in product_stakes(r).values()]
print(f"\n### 1点賭け金: {sorted(collections.Counter(allv).items())}")
ratio = []
for r in rs:
    s = product_stakes(r)
    if s:
        ratio.append(max(s.values()) / min(s.values()))
print(f"  傾斜比 max/min: {sorted(collections.Counter(round(x,2) for x in ratio).items())}")

# 5) 見解
lens = [len(r.get("kenkai") or "") for r in rs]
clauses = [len(re.split(r"[、。]", (r.get("kenkai") or "").strip("。")))for r in rs]
print(f"\n### 見解: 文字数 中央={st.median(lens):.0f} min={min(lens)} max={max(lens)}")
print(f"  読点区切りの節数: {sorted(collections.Counter(clauses).items())}  "
      f"（ブロック数との一致 {sum(1 for a,b in zip(clauses,nrows) if a==b)}/{len(rs)}）")
kw = collections.Counter()
for r in rs:
    for w in ("押さえ", "巻き返", "先行", "番手", "突っ込", "捲り", "追い込", "細切れ",
              "有利", "期待", "怖い", "気配", "地元", "別線", "自力", "днем"):
        if w in (r.get("kenkai") or ""):
            kw[w] += 1
print("  頻出語:", kw.most_command() if hasattr(kw, "most_command") else kw.most_common())
print("  タイトル:", collections.Counter(r.get("kenkai_title") for r in rs).most_common())

# 6) 的中の中身
hits = [r for r in rs if r.get("hit_row")]
print(f"\n### 的中 {len(hits)}/{len(rs)}")
print(f"  的中したブロックの形:")
cc = collections.Counter()
for r in hits:
    for row in r["rows"]:
        if row["hit"] and row["cols"]:
            cc["-".join(str(len(x)) for x in row["cols"])] += 1
print("   ", cc.most_common())
# 種別別
gr = collections.Counter(); grh = collections.Counter()
for r in rs:
    g = re.sub("^[ＳＬ]級 ", "", r["race_name"])[:6]
    gr[g] += 1
    if r.get("hit_row"):
        grh[g] += 1
print("  種別別 的中/本数:", [(g, f"{grh[g]}/{gr[g]}") for g, _ in gr.most_common()])
# R番号別
rn = collections.Counter(); rnh = collections.Counter()
for r in rs:
    rn[r["race_no"]] += 1
    if r.get("hit_row"):
        rnh[r["race_no"]] += 1
print("  R別 的中/本数:", [(k, f"{rnh[k]}/{rn[k]}") for k in sorted(rn)])
