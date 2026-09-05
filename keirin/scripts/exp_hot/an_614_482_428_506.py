#!/usr/bin/env python3
"""担当4予想家(614/482/428/506)の商品構成を実測する。

prof/*.jsonl（サンプル）と raw/detail/*.html（見解タイトル）を突き合わせる。
"""
from __future__ import annotations
import json, re, statistics as st, sys, collections
from pathlib import Path
from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw" / "detail"
TITLE = re.compile(r'<h2 class="YosoKenkaiTitle">(.*?)</h2>', re.S)


def kenkai_title(gid: str) -> str | None:
    p = RAW / f"{gid}.html"
    if not p.exists():
        return None
    m = TITLE.search(p.read_text(encoding="utf-8"))
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else None


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
    return xs[i]


NAMES = {614: "厳選AIマスター", 482: "シュウのAI指数極", 428: "シュウのAI指数", 506: "しんちゃん"}
SAMPLE = {614: "prof/614_20260820_20260905.jsonl",
          482: "prof/482_20260820_20260905.jsonl",
          428: "prof/428_20260820_20260905.jsonl",
          506: "prof/506_20260820_20260905.jsonl"}


def detail_stats(gids: list[str]) -> dict:
    """gid リストの詳細HTMLから商品構成を実測する。"""
    prods = []
    for g in gids:
        p = RAW / f"{g}.html"
        if not p.exists():
            continue
        d = parse_bets(p)
        if not d["rows"]:
            continue
        d["gid"] = g
        d["title"] = kenkai_title(g)
        prods.append(d)
    return prods


def summarize(tag: str, prods: list[dict]) -> None:
    n = len(prods)
    if not n:
        print(f"{tag}: データなし")
        return
    bt = collections.Counter()
    md = collections.Counter()
    colpat = collections.Counter()
    for d in prods:
        bts = tuple(sorted({r["bet_type"] for r in d["rows"]}))
        bt[bts] += 1
        for r in d["rows"]:
            md[r["mode"] or "通常"] += 1
            if r["cols"]:
                colpat[tuple(len(c) for c in r["cols"])] += 1
    npts = [d["n_points_total"] for d in prods]
    eq = sum(1 for d in prods if d["unit_min"] == d["unit_max"])
    ratios = [d["unit_max"] / d["unit_min"] for d in prods
              if d["unit_min"] and d["unit_max"] and d["unit_min"] != d["unit_max"]]
    print(f"\n### {tag}  n={n}")
    print(f"  券種構成: " + ", ".join(f"{'+'.join(k)} {v}({v/n*100:.1f}%)" for k, v in bt.most_common()))
    print(f"  mode(行ベース): {dict(md.most_common())}")
    print(f"  点数: 中央{med(npts)} p10={pct(npts,.1)} p90={pct(npts,.9)} min={min(npts)} max={max(npts)}")
    print(f"  1点賭金: 均等{eq}/{n} ({eq/n*100:.1f}%)  "
          f"傾斜の最大/最小 中央={med(ratios) if ratios else '-'}")
    print(f"  cols(1着,2着,3着の候補車数) top: {colpat.most_common(10)}")
    print(f"  総購入額 中央: {med([d['total_bet'] for d in prods])}")
    ti = collections.Counter(d["title"] for d in prods)
    if len(ti) <= 8:
        print(f"  見解タイトル: {dict(ti.most_common())}")
