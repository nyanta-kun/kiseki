#!/usr/bin/env python3
"""424 LONEFOX / 345 Aiライン極 / 410 倉本匠馬 の商品構成を実測で分解する。

prof/<yid>_20260820_20260905.jsonl（サンプル）と prof/hi_<yid>.jsonl（10万+全件）を読み、
券種・点数・1点賭け金・買い目の形・高額的中の作られ方を集計して標準出力へ出す。

usage: python3 an_424_345_410.py [--kenkai]
"""
from __future__ import annotations

import json
import re
import statistics as st
import sys
from collections import Counter
from pathlib import Path

from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
PROF = HERE / "prof"
RAW = HERE / "raw"
NAMES = {424: "LONEFOX", 345: "Aiライン極", 410: "倉本匠馬"}


def q(xs: list, p: float) -> float | None:
    """百分位（線形補間なしの単純順位）を返す。"""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[i]


def load(p: Path) -> list[dict]:
    """JSONL を読む（無ければ空）。"""
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]


def racecat(name: str) -> str:
    """レース名から種別を粗く分類する。"""
    for k in ("決勝", "準決勝", "特選", "選抜", "予選", "一般"):
        if k in name:
            return k
    return "その他"


def kenkai(gid: str) -> str:
    """詳細HTMLから見解テキストを取り出す。"""
    p = RAW / "detail" / f"{gid}.html"
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8")
    m = re.search(r'<div class="YosoKenkaiTxt">(.*?)</div>', t, re.S)
    h = re.search(r'<h2 class="YosoKenkaiTitle">(.*?)</h2>', t, re.S)
    body = re.sub(r"<[^>]+>", "\n", m.group(1)) if m else ""
    return ((h.group(1).strip() + " / ") if h else "") + \
        re.sub(r"\n{2,}", "\n", body).strip()


def sample_report(yid: int) -> None:
    """サンプル（全商品を間引いたもの）の構成を出す。"""
    rows = load(PROF / f"{yid}_20260820_20260905.jsonl")
    print(f"\n===== {yid} {NAMES[yid]} サンプル n={len(rows)} =====")
    if not rows:
        return
    print("券種:", Counter(tuple(r["bet_types"] or []) for r in rows).most_common())
    print("形式:", Counter(tuple(r["modes"] or []) for r in rows).most_common())
    npt = [r["n_points_total"] for r in rows if r.get("n_points_total")]
    print(f"点数 中央{st.median(npt)} min{min(npt)} p10 {q(npt,.1)} "
          f"p90 {q(npt,.9)} max{max(npt)}")
    print("点数分布:", sorted(Counter(npt).items())[:20])
    flat = sum(1 for r in rows if r["unit_min"] == r["unit_max"])
    print(f"均等配分 {flat}/{len(rows)} = {flat/len(rows)*100:.1f}%")
    ratio = [r["unit_max"] / r["unit_min"] for r in rows
             if r["unit_min"] and r["unit_max"] and r["unit_max"] != r["unit_min"]]
    if ratio:
        print(f"傾斜 max/min 中央{st.median(ratio):.2f} p90 {q(ratio,.9):.2f} "
              f"max {max(ratio):.2f}")
    umin = [r["unit_min"] for r in rows if r["unit_min"]]
    umax = [r["unit_max"] for r in rows if r["unit_max"]]
    print(f"1点賭金 min中央{st.median(umin)} (最小{min(umin)}) / "
          f"max中央{st.median(umax)} (最大{max(umax)})")
    print("行数(=買い目ブロック数):", Counter(r["n_rows"] for r in rows).most_common(5))
    # cols の形（1着/2着/3着の候補数）
    shapes = Counter()
    for r in rows:
        for c in (r.get("cols") or []):
            if c and all(c):
                shapes[tuple(len(x) for x in c)] += 1
    print("cols形(1着,2着,3着の候補数) 上位:", shapes.most_common(10))
    inv = sum(r.get("bet") or 0 for r in rows)
    pay = sum(r.get("payout") or 0 for r in rows)
    nh = sum(1 for r in rows if (r.get("payout") or 0) > 0)
    print(f"サンプル的中 {nh}/{len(rows)} = {nh/len(rows)*100:.1f}%  "
          f"ROI {pay/inv*100:.1f}%")


def hi_report(yid: int) -> None:
    """10万+的中の作られ方を1件ずつ分解して集計する。"""
    rows = load(PROF / f"hi_{yid}.jsonl")
    print(f"\n----- {yid} {NAMES[yid]} 10万+ n={len(rows)} -----")
    if not rows:
        return
    recs = []
    for r in rows:
        h = r.get("hit") or {}
        recs.append({
            "date": r["date"], "venue": r["venue"], "race": r["race_name"],
            "payout": r["payout"], "bt": h.get("bet_type"), "mode": h.get("mode"),
            "stake": h.get("hit_stake"), "odds": h.get("hit_odds"),
            "npts": r.get("n_points_total"), "umin": r.get("unit_min"),
            "umax": r.get("unit_max"), "types": r.get("bet_types"),
        })
    ok = [x for x in recs if x["odds"]]
    print(f"的中1点を分解できた {len(ok)}/{len(recs)}")
    print("的中券種:", Counter(x["bt"] for x in ok).most_common())
    print("的中形式:", Counter(x["mode"] for x in ok).most_common())
    od = [x["odds"] for x in ok]
    stk = [x["stake"] for x in ok]
    print(f"的中倍率 中央{st.median(od):.1f} min{min(od)} p10 {q(od,.1)} "
          f"p90 {q(od,.9)} max{max(od)}")
    print(f"的中1点の賭金 中央{st.median(stk)} min{min(stk)} max{max(stk)}")
    hi_odds = sum(1 for x in ok if x["odds"] >= 200)
    conc = sum(1 for x in ok if x["stake"] >= 3000)
    print(f"倍率型(200倍+) {hi_odds}/{len(ok)} = {hi_odds/len(ok)*100:.0f}%  |  "
          f"集中型(1点3000円+) {conc}/{len(ok)} = {conc/len(ok)*100:.0f}%")
    print("  倍率帯:", Counter(
        ("<50" if x["odds"] < 50 else "50-100" if x["odds"] < 100 else
         "100-200" if x["odds"] < 200 else "200-500" if x["odds"] < 500 else "500+")
        for x in ok).most_common())
    print("  賭金帯:", Counter(
        ("<500" if x["stake"] < 500 else "500-999" if x["stake"] < 1000 else
         "1000-2999" if x["stake"] < 3000 else "3000-5999" if x["stake"] < 6000
         else "6000+") for x in ok).most_common())
    npt = [x["npts"] for x in recs if x["npts"]]
    print(f"10万+商品の点数 中央{st.median(npt)} 範囲{min(npt)}〜{max(npt)}")
    print("10万+商品の券種:", Counter(tuple(x["types"] or []) for x in recs).most_common())
    print("種別:", Counter(racecat(x["race"]) for x in recs).most_common())
    for x in sorted(ok, key=lambda z: -z["payout"])[:8]:
        print(f"   {x['date']} {x['venue']}{x['race']:<12} 払戻{x['payout']:>9,} "
              f"= {x['stake']:>5,}円 x {x['odds']:>7.1f}倍  "
              f"({x['bt']}/{x['mode'] or '通常'}/{x['npts']}点)")


def block_report(yid: int) -> None:
    """商品の「ブロック構成」（券種/形式/点数/1点賭金の並び）を数える。"""
    rows = load(PROF / f"{yid}_20260820_20260905.jsonl")
    hi = load(PROF / f"hi_{yid}.jsonl")
    for label, rs in (("サンプル", rows), ("10万+", hi)):
        if not rs:
            continue
        pat = Counter()
        for r in rs:
            det = r.get("rows_detail")
            if det is None:                              # sample は再パースする
                p = RAW / "detail" / f"{r['gid']}.html"
                if not p.exists():
                    continue
                det = parse_bets(p)["rows"]
            pat[tuple((x["bet_type"], x["mode"], x["n_points"], x["unit"])
                      for x in det)] += 1
        if pat:
            print(f"[{yid} {label}] ブロック構成 上位:")
            for k, v in pat.most_common(5):
                print(f"   {v:>3}  {k}")


def conf_report(yid: int) -> None:
    """見解タイトルの分布（倉本匠馬の自信度など）を数える。"""
    gids = [r["gid"] for r in load(PROF / f"{yid}_20260820_20260905.jsonl")]
    hig = [r["gid"] for r in load(PROF / f"hi_{yid}.jsonl")]
    for label, gs in (("サンプル", gids), ("10万+", hig)):
        c = Counter()
        for g in gs:
            p = RAW / "detail" / f"{g}.html"
            if not p.exists():
                continue
            m = re.search(r'<h2 class="YosoKenkaiTitle">(.*?)</h2>',
                          p.read_text(encoding="utf-8"), re.S)
            c[(m.group(1).strip() if m else "")[:40]] += 1
        if c:
            print(f"[{yid} {label}] 見解タイトル 上位:", c.most_common(8))


def main() -> None:
    """全レポートを出力する。"""
    for yid in (424, 345, 410):
        sample_report(yid)
        hi_report(yid)
        block_report(yid)
        conf_report(yid)
    if "--kenkai" in sys.argv:
        for yid in (424, 345, 410):
            rows = load(PROF / f"{yid}_20260820_20260905.jsonl")[:3]
            print(f"\n##### {yid} {NAMES[yid]} 見解サンプル")
            for r in rows:
                print("---", r["date"], r["venue"], r["race_name"])
                print(kenkai(r["gid"])[:1200])


if __name__ == "__main__":
    main()
