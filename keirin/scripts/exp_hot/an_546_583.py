#!/usr/bin/env python3
"""546 / 583 の商品構成を実測する。fetch_546_583.py が集めた詳細HTMLだけを使う。"""
from __future__ import annotations
import json, re, statistics as st, collections
from pathlib import Path
from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
DET = HERE / "detail"  # placeholder
DET = HERE / "raw" / "detail"
WIN = (20260820, 20260905)
NAMES = {546: "iAI -居合-", 583: "LONELYWOLF"}
MARK = {"Icon_Honmei": "◎", "Icon_Taikou": "○", "Icon_Kurosan": "▲",
        "Icon_Chuui": "△", "Icon_Renka": "×", "Icon_Osae": "注"}

rows = [json.loads(l) for l in (HERE / "month2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def marks(gid: str) -> dict[int, str]:
    p = DET / f"{gid}.html"
    t = p.read_text(encoding="utf-8")
    m = re.search(r'<table class="YosoShirushiTable01">(.*?)</table>', t, re.S)
    if not m:
        return {}
    out = {}
    for tr in re.split(r"<tr[ >]", m.group(1))[1:]:
        ic = re.search(r"Icon_Shirushi (Icon_\w+)", tr)
        num = re.search(r'<span class="Num Waku\d+">(\d+)</span>', tr)
        if ic and num:
            out[int(num.group(1))] = MARK.get(ic.group(1), ic.group(1))
    return out


def kenkai(gid: str) -> str:
    t = (DET / f"{gid}.html").read_text(encoding="utf-8")
    m = re.search(r'YosoKenkaiTxt">(.*?)</div>', t, re.S)
    if not m:
        return ""
    s = re.sub(r"<table.*?</table>", "", m.group(1), flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load(yid: int) -> list[dict]:
    out = []
    for r in rows:
        if r["yid"] != yid:
            continue
        p = DET / f"{r['gid']}.html"
        if not p.exists():
            continue
        try:
            d = parse_bets(p)
        except Exception:  # noqa: BLE001
            continue
        if not d["rows"]:
            continue
        r = dict(r)
        r["d"] = d
        r["in_win"] = WIN[0] <= int(r["date"]) <= WIN[1]
        r["hi"] = (r.get("payout") or 0) >= 100000
        out.append(r)
    return out


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(p * (len(xs) - 1))))
    return xs[i]


def desc(name, xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return f"{name}: n=0"
    return (f"{name}: n={len(xs)} min={min(xs)} p10={q(xs,.10)} 中央={st.median(xs)} "
            f"p90={q(xs,.90)} max={max(xs)}")


def report(yid):
    data = load(yid)
    smp = [r for r in data if r["in_win"] and not r["hi"]]
    allsmp = [r for r in data if r["in_win"]]
    hi = [r for r in data if r["hi"]]
    print("#" * 60)
    print(f"# {yid} {NAMES[yid]}  詳細取得 {len(data)}件 (窓内 {len(allsmp)} / 10万+ {len(hi)})")

    def block(tag, rs):
        if not rs:
            print(f"-- {tag}: 0件")
            return
        print(f"-- {tag}  n={len(rs)}")
        bts = collections.Counter(t for r in rs for t in r["d"]["bet_types"])
        nbt = collections.Counter(tuple(r["d"]["bet_types"]) for r in rs)
        print("   券種(商品数)", nbt.most_common())
        print("  ", desc("点数", [r["d"]["n_points_total"] for r in rs]))
        print("  ", desc("total_bet", [r["d"]["total_bet"] for r in rs]))
        eq = sum(1 for r in rs if r["d"]["unit_min"] == r["d"]["unit_max"])
        print(f"   均等配分(unit_min==max) {eq}/{len(rs)} = {eq/len(rs)*100:.1f}%")
        ratio = [r["d"]["unit_max"] / r["d"]["unit_min"] for r in rs
                 if r["d"]["unit_min"] and r["d"]["unit_max"] != r["d"]["unit_min"]]
        if ratio:
            print("  ", desc("傾斜比(max/min)", [round(x, 2) for x in ratio]))
        print("  ", desc("unit_min", [r["d"]["unit_min"] for r in rs]))
        print("  ", desc("unit_max", [r["d"]["unit_max"] for r in rs]))
        md = collections.Counter(m for r in rs for m in {x["mode"] for x in r["d"]["rows"]})
        print("   mode(商品数・重複可)", md.most_common())
        # cols パターン
        shp = collections.Counter()
        for r in rs:
            for x in r["d"]["rows"]:
                if x["cols"]:
                    shp[tuple(len(c) for c in x["cols"])] += 1
        print("   cols形(行数)", shp.most_common(8))
        # 1着候補の車数（行単位）
        c1 = collections.Counter()
        for r in rs:
            for x in r["d"]["rows"]:
                if x["cols"] and len(x["cols"]) >= 1:
                    c1[len(x["cols"][0])] += 1
        print("   1着候補の車数(行)", sorted(c1.items()))
        # 商品ごとの1着候補ユニーク車
        u1 = collections.Counter()
        for r in rs:
            s = set()
            for x in r["d"]["rows"]:
                if x["cols"]:
                    s |= set(x["cols"][0])
            if s:
                u1[len(s)] += 1
        print("   商品内の1着候補ユニーク車数", sorted(u1.items()))
        print("  ", desc("1行あたり点数", [x["n_points"] for r in rs for x in r["d"]["rows"]]))
        print("  ", desc("行数/商品", [len(r["d"]["rows"]) for r in rs]))

    block("窓内サンプル(外れ含む・10万+除く)", smp)
    block("窓内サンプル(全)", allsmp)
    block("10万+的中", hi)

    # 高額分解
    print("-- 10万+の分解")
    st_, od_ = [], []
    for r in sorted(hi, key=lambda r: -(r["payout"] or 0)):
        h = r["d"]["hit"]
        if not h:
            print(f"   {r['date']} {r['venue']}{r['race_no']}R {r['race_name']}"
                  f" payout={r['payout']} 的中行なし n_pts={r['d']['n_points_total']}")
            continue
        st_.append(h["hit_stake"]); od_.append(h["hit_odds"])
        print(f"   {r['date']} {r['venue']}{r['race_no']}R {r['race_name'][:12]:14s}"
              f" 払戻{r['payout']:>7} = 賭金{h['hit_stake']:>5} x {h['hit_odds']:>7.1f}倍"
              f"  券種{h['bet_type']} 点数{r['d']['n_points_total']:>3}"
              f" unit{r['d']['unit_min']}-{r['d']['unit_max']}")
    print("  ", desc("的中倍率", od_))
    print("  ", desc("的中点の賭金", st_))
    conc = sum(1 for s in st_ if s >= 3000)
    mult = sum(1 for o in od_ if o >= 200)
    print(f"   集中型(賭金>=3000) {conc}/{len(st_)}   倍率型(倍率>=200) {mult}/{len(od_)}")

    # 印と的中の関係
    print("-- 的中目の印構成（10万+）")
    cnt = collections.Counter()
    for r in hi:
        h = r["d"]["hit"]
        if not h or not h["hit_combo"]:
            continue
        mk = marks(r["gid"])
        combo = re.split(r"[-=]", h["hit_combo"])
        cnt[tuple(mk.get(int(c), "無") for c in combo)] += 1
    for k, v in cnt.most_common(12):
        print("   ", "-".join(k), v)
    return data


d546 = report(546)
d583 = report(583)

print("\n===== 見解テキスト（例） =====")
for yid, data in ((546, d546), (583, d583)):
    print(f"--- {yid} {NAMES[yid]}")
    seen = set()
    for r in data[:400]:
        k = kenkai(r["gid"])
        if k and k[:40] not in seen:
            seen.add(k[:40])
            print(f"   [{r['date']} {r['venue']}{r['race_no']}R] {k[:300]}")
        if len(seen) >= 6:
            break


print("\n\n########## 追加分析 ##########")
for yid, data in ((546, d546), (583, d583)):
    print("=" * 55)
    print(f"# {yid} {NAMES[yid]}")
    smp = [r for r in data if r["in_win"]]
    hi = [r for r in data if r["hi"]]
    if not smp:
        continue

    def bucket(n):
        for lo, hi_ in ((1, 1), (2, 3), (4, 6), (7, 10), (11, 15), (16, 25), (26, 999)):
            if lo <= n <= hi_:
                return f"{lo}-{hi_}" if lo != hi_ else "1"
        return "?"
    print("-- 点数バケツ（窓内サンプル / 10万+）")
    bs = collections.Counter(bucket(r["d"]["n_points_total"]) for r in smp)
    bh = collections.Counter(bucket(r["d"]["n_points_total"]) for r in hi)
    order = ["1", "2-3", "4-6", "7-10", "11-15", "16-25", "26-999"]
    for k in order:
        print(f"   {k:>7}  サンプル {bs.get(k,0):>3} ({bs.get(k,0)/len(smp)*100:>5.1f}%)"
              f"   10万+ {bh.get(k,0):>3}")
    print("-- 1点あたり賭金バケツ（窓内サンプル / 10万+）")
    def ub(u):
        for lo, hi_ in ((0, 499), (500, 999), (1000, 1999), (2000, 2999), (3000, 4999), (5000, 9999), (10000, 10**9)):
            if lo <= u <= hi_:
                return f"{lo}-{hi_}"
        return "?"
    us = collections.Counter(ub(r["d"]["unit_min"]) for r in smp if r["d"]["unit_min"])
    uh = collections.Counter(ub(r["d"]["unit_min"]) for r in hi if r["d"]["unit_min"])
    for k in ["0-499", "500-999", "1000-1999", "2000-2999", "3000-4999", "5000-9999", "10000-1000000000"]:
        print(f"   {k:>18}  サンプル {us.get(k,0):>3}   10万+ {uh.get(k,0):>3}")

    def cls(s):
        for k in ("準決勝", "決勝", "特選", "選抜", "予選", "一般"):
            if k in s:
                return k
        return "他"
    print("-- レース種別（窓内サンプル / 10万+）")
    cs = collections.Counter(cls(r["race_name"]) for r in smp)
    ch = collections.Counter(cls(r["race_name"]) for r in hi)
    for k in ("予選", "一般", "準決勝", "選抜", "特選", "決勝", "他"):
        print(f"   {k:>4}  サンプル {cs.get(k,0):>3} ({cs.get(k,0)/len(smp)*100:>5.1f}%)   10万+ {ch.get(k,0):>3}")

    print("-- 公開時刻帯 × 点数中央（窓内サンプル）")
    byh = collections.defaultdict(list)
    for r in smp:
        byh[(r["published_at"] or "")[11:13]].append(r["d"]["n_points_total"])
    for h in sorted(byh):
        print(f"   {h}時  n={len(byh[h]):>3}  点数中央 {st.median(byh[h])}")

    print("-- 買い目の車数構成（窓内サンプル・商品ごとのユニーク車）")
    for pos, nm in ((0, "1着"), (1, "2着"), (2, "3着")):
        cc = collections.Counter()
        for r in smp:
            s = set()
            for x in r["d"]["rows"]:
                if x["cols"] and len(x["cols"]) > pos:
                    s |= set(x["cols"][pos])
            if s:
                cc[len(s)] += 1
        print(f"   {nm}候補ユニーク車数 {sorted(cc.items())}")

    print("-- 印の使われ方（窓内サンプル）")
    m1 = collections.Counter(); mall = collections.Counter()
    for r in smp:
        mk = marks(r["gid"])
        if not mk:
            continue
        mall[len(mk)] += 1
        s = set()
        for x in r["d"]["rows"]:
            if x["cols"]:
                s |= set(x["cols"][0])
        m1["+".join(sorted(mk.get(c, "無") for c in s))] += 1
    print("   印の個数分布", sorted(mall.items()))
    print("   1着に置いた印の組合せ", m1.most_common(8))

    print("-- 的中(窓内サンプル・一覧payout>0)の内訳")
    hs = [r for r in smp if (r["payout"] or 0) > 0]
    print(f"   的中 {len(hs)}/{len(smp)}")
    for r in sorted(hs, key=lambda r: -(r["payout"] or 0))[:12]:
        h = r["d"]["hit"]
        print(f"   {r['date']} {r['venue']}{r['race_no']}R 払戻{r['payout']:>7}"
              f" 点数{r['d']['n_points_total']:>3} unit{r['d']['unit_min']}"
              + (f" 倍率{h['hit_odds']}" if h else " 倍率?"))


print("\n\n########## comment別の商品設計（詳細ベース・窓内サンプル＋10万+） ##########")
for yid, data in ((546, d546), (583, d583)):
    print("=" * 60); print(f"# {yid} {NAMES[yid]}")
    g = collections.defaultdict(list)
    for r in data:
        g[(r["comment"] or "").strip()].append(r)
    hdr = f"{'comment':30s} {'n':>4} {'点数中央':>6} {'点数p10':>6} {'点数p90':>6} {'unit中央':>7} {'unit範囲':>13} {'均等%':>6}"
    print(hdr)
    for k, rs in sorted(g.items(), key=lambda kv: -len(kv[1])):
        pts = [r["d"]["n_points_total"] for r in rs]
        un = [r["d"]["unit_min"] for r in rs if r["d"]["unit_min"]]
        eq = sum(1 for r in rs if r["d"]["unit_min"] == r["d"]["unit_max"])
        print(f"{k[:28]:30s} {len(rs):>4} {st.median(pts):>6.1f} {q(pts,.1):>6} {q(pts,.9):>6} "
              f"{(st.median(un) if un else 0):>7.0f} {min(un) if un else 0:>6}-{max(un) if un else 0:<6} "
              f"{eq/len(rs)*100:>5.0f}%")
