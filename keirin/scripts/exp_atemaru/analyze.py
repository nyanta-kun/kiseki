"""アテマルの ◎ と 相手3車 の選び方を自社の指標と突き合わせる。"""
from __future__ import annotations
import json, itertools
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent / "atemaru"
import sys
R = [json.loads(l) for l in (ROOT / "joined.jsonl").open(encoding="utf-8")]
R = [r for r in R if len(r["marks"]) == 4 and r["result"]]
if len(sys.argv) >= 3:
    lo, hi = sys.argv[1], sys.argv[2]
    R = [r for r in R if lo <= r["date"] <= hi]
print(f"対象 {len(R)} レース  ({min(r['date'] for r in R)}〜{max(r['date'] for r in R)})")


def i(d):  # json は int キーを str にする
    return {int(k): v for k, v in d.items()}


def fin(r):
    """着順 -> 車番（失格等は除く）"""
    out = {}
    for rk, fn in r["result"]:
        if rk.isdigit():
            out[int(rk)] = fn
    return out


def pct(n, d):
    return f"{100*n/d:5.2f}%" if d else "  n/a"


rows = []
skipped = []
for r in R:
    e = i(r["entries"]); mkt = i(r["mkt_rank"]); p3 = i(r["p3_rank"]); pw = i(r["pw_rank"])
    pt = i(r["pt_rank"]); wp = i(r["mkt_win_p"])
    m = r["marks"]
    ax = m.get("◎")
    part = [m.get("○"), m.get("▲"), m.get("△")]
    if ax is None or any(p is None for p in part):
        continue
    if ax not in e or any(p not in e for p in part):
        skipped.append(r["race_key"])
        continue
    f = fin(r)
    order = [f.get(k) for k in (1, 2, 3)]
    rest = [x for x in e if x != ax]
    cut = [x for x in rest if x not in part]
    lsz = Counter(v["line_group"] for v in e.values())
    rows.append(dict(r=r, e=e, lsz=lsz, ax=ax, part=part, cut=cut, mkt=mkt, p3=p3, pw=pw, pt=pt,
                     wp=wp, f=f, order=order, n=len(e)))

print(f"うち印4つ揃い {len(rows)}  （DBの出走表と車番が合わず除外 {len(skipped)}）")

# ---------- 1. ◎ の素性 ----------
print("\n== 1. ◎（軸1車）は何を選んでいるか ==")
for label, key in [("市場人気(確定オッズ由来)", "mkt"), ("自社 p3 順位", "p3"),
                   ("自社 pw 順位", "pw"), ("競走得点 順位", "pt")]:
    c = Counter(row[key].get(row["ax"]) for row in rows)
    tot = sum(c.values())
    dist = " ".join(f"{k}位{pct(c[k],tot)}" for k in sorted(c) if k <= 5)
    print(f"  ◎の{label:22s}: 平均{mean(row[key][row['ax']] for row in rows):4.2f}位  {dist}")

agree = sum(1 for row in rows if row["p3"][row["ax"]] == 1)
print(f"  ◎ = 自社 p3 1位 と一致: {pct(agree, len(rows))}")
agree_mkt = sum(1 for row in rows if row["mkt"][row["ax"]] == 1)
print(f"  ◎ = 市場1番人気     と一致: {pct(agree_mkt, len(rows))}")
own_mkt = sum(1 for row in rows if row["mkt"][min(row["p3"], key=row["p3"].get)] == 1)
print(f"  自社p3 1位 = 市場1番人気 と一致: {pct(own_mkt, len(rows))}  ← 比較用")

# ◎ の成績
w = sum(1 for row in rows if row["order"][0] == row["ax"])
t3 = sum(1 for row in rows if row["ax"] in row["order"])
print(f"  ◎の1着率 {pct(w,len(rows))} / 3着内率 {pct(t3,len(rows))}")
ow = sum(1 for row in rows if row["order"][0] == min(row["p3"], key=row["p3"].get))
ot3 = sum(1 for row in rows if min(row["p3"], key=row["p3"].get) in row["order"])
print(f"  自社p3 1位の1着率 {pct(ow,len(rows))} / 3着内率 {pct(ot3,len(rows))}  ← 比較用")

# ---------- 2. 相手3車の切り方 ----------
print("\n== 2. 相手3車の選び方（◎以外から3車） ==")
n_rest = Counter(row["n"] - 1 for row in rows)
print("  ◎以外の車数:", dict(n_rest))
# 相手が市場・自社の上位3車と一致するか
def topk_excl(rank: dict, ax: int, k: int):
    return [f for f in sorted(rank, key=rank.get) if f != ax][:k]

same_mkt = Counter(len(set(row["part"]) & set(topk_excl(row["mkt"], row["ax"], 3))) for row in rows)
same_p3 = Counter(len(set(row["part"]) & set(topk_excl(row["p3"], row["ax"], 3))) for row in rows)
tot = len(rows)
print("  相手3車と『市場上位3』の重なり:", {k: pct(v, tot) for k, v in sorted(same_mkt.items())})
print("  相手3車と『自社p3上位3』重なり:", {k: pct(v, tot) for k, v in sorted(same_p3.items())})

# 切られた車の素性
cut_mkt = Counter(); cut_p3 = Counter(); part_mkt = Counter(); part_p3 = Counter()
for row in rows:
    for x in row["cut"]:
        cut_mkt[row["mkt"][x]] += 1; cut_p3[row["p3"][x]] += 1
    for x in row["part"]:
        part_mkt[row["mkt"][x]] += 1; part_p3[row["p3"][x]] += 1
print("\n  【市場人気】相手に入れる/切る の割合（人気順位別）")
for k in sorted(set(cut_mkt) | set(part_mkt)):
    a, b = part_mkt[k], cut_mkt[k]
    print(f"    {k}番人気: 相手 {a:5d} / 切り {b:5d}  → 採用率 {pct(a, a+b)}")
print("  【自社p3】相手に入れる/切る の割合（p3順位別）")
for k in sorted(set(cut_p3) | set(part_p3)):
    a, b = part_p3[k], cut_p3[k]
    print(f"    p3 {k}位: 相手 {a:5d} / 切り {b:5d}  → 採用率 {pct(a, a+b)}")

# 切りの精度: 切られた車 / 相手の車 の実 3着内率
cut_in = sum(1 for row in rows for x in row["cut"] if x in row["order"])
cut_n = sum(len(row["cut"]) for row in rows)
part_in = sum(1 for row in rows for x in row["part"] if x in row["order"])
part_n = sum(len(row["part"]) for row in rows)
print(f"\n  切った車の実3着内率 {pct(cut_in, cut_n)} (n={cut_n})")
print(f"  相手にした車の3着内率 {pct(part_in, part_n)} (n={part_n})")
print(f"  ◎の3着内率        {pct(t3, len(rows))}")

# ---------- 3. ライン ----------
print("\n== 3. ライン構造との関係 ==")
same_line = 0; tot_p = 0; ax_leader = 0
for row in rows:
    e = row["e"]; ax = row["ax"]
    lg = e[ax]["line_group"]
    if e[ax].get("is_line_leader"):
        ax_leader += 1
    for x in row["part"]:
        tot_p += 1
        if e[x]["line_group"] == lg:
            same_line += 1
print(f"  相手3車のうち◎と同ライン: {pct(same_line, tot_p)}")
print(f"  ◎がライン先頭(先行役): {pct(ax_leader, len(rows))}")
cnt = Counter(sum(1 for x in row["part"] if row["e"][x]["line_group"] == row["e"][row["ax"]]["line_group"]) for row in rows)
print("  1レースあたり同ライン相手数:", {k: pct(v, len(rows)) for k, v in sorted(cnt.items())})
sty = Counter(row["e"][row["ax"]]["style"] for row in rows)
print("  ◎の脚質:", {k: pct(v, len(rows)) for k, v in sty.most_common()})

# ---------- 4. 成績 ----------
print("\n== 4. 収支 ==")
hit = sum(1 for row in rows if row["r"].get("payout"))
bet = sum(row["r"]["total_bet"] for row in rows)
pay = sum(row["r"].get("payout") or 0 for row in rows)
print(f"  的中 {pct(hit,len(rows))} ({hit}/{len(rows)})  投資 {bet:,}円  払戻 {pay:,}円  回収率 {100*pay/bet:.1f}%")
gami = sum(1 for row in rows if 0 < (row["r"].get("payout") or 0) < row["r"]["total_bet"])
print(f"  ガミ（的中したが元返し未満）: {pct(gami, hit)} of 的中")
# ◎の着順別
by = defaultdict(lambda: [0, 0, 0])
for row in rows:
    pos = next((k for k in (1, 2, 3) if row["f"].get(k) == row["ax"]), 0)
    b = by[pos]
    b[0] += 1
    b[1] += row["r"].get("payout") or 0
    b[2] += row["r"]["total_bet"]
for k in sorted(by):
    n, p, b = by[k]
    print(f"  ◎が{k or '圏外'}着: {n:5d}R ({pct(n,len(rows))})  回収率 {100*p/b:6.1f}%")

# 相手2車もそろった率
both = sum(1 for row in rows if row["ax"] in row["order"] and
           len([x for x in row["order"] if x in row["part"]]) >= 2)
print(f"  ◎3着内 かつ 相手から2車: {pct(both,len(rows))}  = 的中の定義")


# ---------- 5. 同じ買い方（18点マルチ）で選び方だけ差し替える ----------
print("\n== 5. 同じ18点マルチ・同じ配分で『選び方』だけ入れ替えた反実仮想 ==")
UNIT = {1: 1000, 2: 400, 3: 200}   # ◎が1着/2着/3着のときの1点あたり


def payout_of(row, ax, part):
    """◎+相手3の18点を買ったときの払戻（円）。確定三連単オッズ×100 を使う。"""
    o = row["order"]
    if None in o or len(set(o)) < 3:
        return None
    pos = next((k for k in (1, 2, 3) if o[k - 1] == ax), 0)
    if pos == 0:
        return 0
    others = [x for k, x in enumerate(o, 1) if k != pos]
    if not all(x in part for x in others):
        return 0
    comb = "-".join(str(x) for x in o)
    od = row["r"]["tri_odds"].get(comb)
    return None if od is None else od * 100 * UNIT[pos] / 100


def arm(name, pick):
    bet = hit = pay = 0
    n = 0
    for row in rows:
        sel = pick(row)
        if sel is None:
            continue
        ax, part = sel
        p = payout_of(row, ax, part)
        if p is None:
            continue
        n += 1
        bet += 9600
        pay += p
        hit += 1 if p > 0 else 0
    print(f"  {name:28s} n={n:5d} 的中 {pct(hit,n)} 回収率 {100*pay/bet:6.1f}%  払戻計 {pay:>12,.0f}円")


def by_rank(key, k=4):
    def f(row):
        order = [x for x in sorted(row[key], key=row[key].get)]
        if len(order) < k:
            return None
        return order[0], order[1:k]
    return f


arm("アテマル（実際の◎+相手3）", lambda row: (row["ax"], row["part"]))
arm("自社 p3 上位4（1位軸+2-4位）", by_rank("p3"))
arm("自社 pw 上位4（1位軸+2-4位）", by_rank("pw"))
arm("市場人気 上位4", by_rank("mkt"))
arm("競走得点 上位4", by_rank("pt"))
arm("アテマル◎ + 自社p3で相手3", lambda row: (row["ax"], [f for f in sorted(row["p3"], key=row["p3"].get) if f != row["ax"]][:3]))

# 実払戻との突き合わせ（オッズ代用の検算）
diff = []
for row in rows:
    real = row["r"].get("payout")
    calc = payout_of(row, row["ax"], row["part"])
    if real is not None and calc is not None:
        diff.append((real, calc))
ok = sum(1 for a, b in diff if abs(a - b) <= max(1, 0.02 * max(a, 1)))
print(f"  検算: 実払戻とオッズ代用の一致 {pct(ok, len(diff))} (n={len(diff)})")


# ---------- 6. ◎ の市場乖離 ----------
print("\n== 6. ◎は穴目か（市場との関係） ==")
agree = [row for row in rows if row["mkt"][row["ax"]] == 1]
dis = [row for row in rows if row["mkt"][row["ax"]] != 1]


def perf(name, sub):
    if not sub:
        return
    bet = sum(9600 for _ in sub)
    pay = sum(r["r"].get("payout") or 0 for r in sub)
    hit = sum(1 for r in sub if (r["r"].get("payout") or 0) > 0)
    net = sum(1 for r in sub if (r["r"].get("payout") or 0) > 9600)
    w = sum(1 for r in sub if r["order"][0] == r["ax"])
    print(f"  {name:34s} n={len(sub):5d} ({pct(len(sub),len(rows))})  ◎1着 {pct(w,len(sub))} "
          f"的中 {pct(hit,len(sub))} 表示的中(ガミ除) {pct(net,len(sub))} 回収率 {100*pay/bet:6.1f}%")


perf("◎ = 市場1番人気", agree)
perf("◎ ≠ 市場1番人気（乖離）", dis)
perf("◎ = 自社p3 1位", [row for row in rows if row["p3"][row["ax"]] == 1])
perf("◎ ≠ 自社p3 1位", [row for row in rows if row["p3"][row["ax"]] != 1])
perf("◎=市場1位 かつ =自社p3 1位", [row for row in rows if row["mkt"][row["ax"]] == 1 and row["p3"][row["ax"]] == 1])
c = Counter()
for row in dis:
    c[row["mkt"][row["ax"]]] += 1
print("  乖離時の◎の人気順位:", {k: pct(v, len(dis)) for k, v in sorted(c.items())})
# 乖離時に市場1番人気をどう扱っているか
fav_in = sum(1 for row in dis if min(row["mkt"], key=row["mkt"].get) in row["part"])
print(f"  乖離時に市場1番人気を相手に入れている: {pct(fav_in, len(dis))}"
      f" / 切っている {pct(len(dis)-fav_in, len(dis))}")

# ---------- 7. 相手の中身 ----------
print("\n== 7. 相手3車の中身と切り捨て ==")
lead_part = sum(1 for row in rows for x in row["part"] if row["e"][x].get("is_line_leader"))
print(f"  相手3車のうちライン先頭: {pct(lead_part, 3*len(rows))}")
# ◎と同ラインの番手（line_pos==2）を相手に入れているか
mark_in = mark_ex = 0
for row in rows:
    lg = row["e"][row["ax"]]["line_group"]
    bante = [x for x in row["e"] if x != row["ax"] and row["e"][x]["line_group"] == lg
             and (row["e"][x]["line_pos"] or 9) == (row["e"][row["ax"]]["line_pos"] or 0) + 1]
    for b in bante:
        if b in row["part"]:
            mark_in += 1
        else:
            mark_ex += 1
print(f"  ◎の直後（番手）が存在したレースで相手に採用: {pct(mark_in, mark_in+mark_ex)} (n={mark_in+mark_ex})")
# 切った車のうち実際に3着内に来たもののプロフィール
cut_hit = Counter(); cut_all = Counter()
for row in rows:
    for x in row["cut"]:
        cut_all[row["mkt"][x]] += 1
        if x in row["order"]:
            cut_hit[row["mkt"][x]] += 1
print("  切った車の実3着内率（市場人気別）:")
for k in sorted(cut_all):
    print(f"    {k}番人気: {pct(cut_hit[k], cut_all[k])}  (n={cut_all[k]})")
miss_by_cut = sum(1 for row in rows if row["ax"] in row["order"] and
                  sum(1 for x in row["order"] if x in row["cut"]) >= 1 and
                  sum(1 for x in row["order"] if x in row["part"]) < 2)
print(f"  ◎は3着内なのに切った車に邪魔されて外れた: {pct(miss_by_cut, len(rows))}")

# ---------- 8. AI指数と自社指標 ----------
print("\n== 8. アテマルのAI指数と自社モデル ==")
import math
pairs = []
for row in rows:
    ai = {int(k): v for k, v in row["r"].get("ai_index", {}).items()}
    for f, v in ai.items():
        if f in row["e"]:
            pairs.append((v, float(row["e"][f]["pred_top3_pct"]), float(row["e"][f]["pred_win_pct"]),
                          row["wp"].get(f, 0.0)))
if pairs:
    def corr(a, b):
        n = len(a); ma, mb = mean(a), mean(b)
        va = math.sqrt(sum((x-ma)**2 for x in a)); vb = math.sqrt(sum((x-mb)**2 for x in b))
        return sum((x-ma)*(y-mb) for x, y in zip(a, b))/(va*vb) if va and vb else 0
    A = [p[0] for p in pairs]
    print(f"  印の付いた車 n={len(pairs)}  AI指数 vs 自社p3 r={corr(A,[p[1] for p in pairs]):.3f}"
          f" / 自社pw r={corr(A,[p[2] for p in pairs]):.3f} / 市場1着率 r={corr(A,[p[3] for p in pairs]):.3f}")
    print(f"  AI指数レンジ: min={min(A):.3f} max={max(A):.3f} mean={mean(A):.3f}")


# ---------- 9. 主要な腕のレース単位 paired bootstrap ----------
print("\n== 9. 腕ごとの回収率とレース単位 paired bootstrap（95%CI） ==")
import random
random.seed(20260825)

ARMS = {
    "A_アテマル実物": lambda row: (row["ax"], row["part"]),
    "B_自社p3上位4": by_rank("p3"),
    "C_市場上位4": by_rank("mkt"),
    "D_◎はアテマル/相手は自社p3": lambda row: (row["ax"], [f for f in sorted(row["p3"], key=row["p3"].get) if f != row["ax"]][:3]),
    "E_◎はアテマル/相手は市場上位3": lambda row: (row["ax"], [f for f in sorted(row["mkt"], key=row["mkt"].get) if f != row["ax"]][:3]),
    "F_◎は自社p3/相手はアテマル": None,
}


def arm_payouts(pick):
    out = []
    for row in rows:
        sel = pick(row)
        if sel is None:
            out.append(None); continue
        p = payout_of(row, sel[0], sel[1])
        out.append(p)
    return out


def f_pick(row):
    a = min(row["p3"], key=row["p3"].get)
    if a in row["part"]:
        part = [x for x in row["part"] if x != a]
        extra = [f for f in sorted(row["p3"], key=row["p3"].get) if f != a and f not in part]
        part = part + extra[:1]
    else:
        part = row["part"]
    return a, part[:3]


ARMS["F_◎は自社p3/相手はアテマル"] = f_pick
P = {k: arm_payouts(v) for k, v in ARMS.items()}
valid = [i2 for i2 in range(len(rows)) if all(P[k][i2] is not None for k in ARMS)]
print(f"  有効 {len(valid)} レース")
for k in ARMS:
    pay = sum(P[k][i2] for i2 in valid)
    hit = sum(1 for i2 in valid if P[k][i2] > 0)
    net = sum(1 for i2 in valid if P[k][i2] > 9600)
    print(f"  {k:28s} 的中 {pct(hit,len(valid))} 表示的中 {pct(net,len(valid))} 回収率 {100*pay/(9600*len(valid)):6.1f}%")

B = 2000
for other in ("B_自社p3上位4", "D_◎はアテマル/相手は自社p3", "F_◎は自社p3/相手はアテマル", "C_市場上位4"):
    d = []
    for _ in range(B):
        s = [random.choice(valid) for _ in valid]
        a = sum(P["A_アテマル実物"][i2] for i2 in s)
        b = sum(P[other][i2] for i2 in s)
        d.append(100 * (b - a) / (9600 * len(s)))
    d.sort()
    print(f"  ROI差 {other} − A: {mean(d):+6.2f}pt  CI95 [{d[int(.025*B)]:+6.2f}, {d[int(.975*B)]:+6.2f}]")


# ---------- 10. 軸の思想の違い ----------
print("\n== 10. 軸の思想：アテマルの◎ vs 自社 p3 1位 ==")
def prof(name, getf):
    lead = sum(1 for row in rows if row["e"][getf(row)].get("is_line_leader"))
    sty = Counter(row["e"][getf(row)]["style"] for row in rows)
    w = sum(1 for row in rows if row["order"][0] == getf(row))
    t3 = sum(1 for row in rows if getf(row) in row["order"])
    fav = sum(1 for row in rows if row["mkt"][getf(row)] == 1)
    print(f"  {name:16s} ライン先頭 {pct(lead,len(rows))} 脚質 " +
          " ".join(f"{k}{pct(v,len(rows))}" for k, v in sty.most_common()) +
          f"  市場1番人気 {pct(fav,len(rows))} 1着 {pct(w,len(rows))} 3着内 {pct(t3,len(rows))}")


prof("アテマル ◎", lambda row: row["ax"])
prof("自社 p3 1位", lambda row: min(row["p3"], key=row["p3"].get))
prof("自社 pw 1位", lambda row: min(row["pw"], key=row["pw"].get))
prof("市場 1番人気", lambda row: min(row["mkt"], key=row["mkt"].get))

diff = [row for row in rows if row["ax"] != min(row["p3"], key=row["p3"].get)]
aw = sum(1 for row in diff if row["order"][0] == row["ax"])
ow = sum(1 for row in diff if row["order"][0] == min(row["p3"], key=row["p3"].get))
at = sum(1 for row in diff if row["ax"] in row["order"])
ot = sum(1 for row in diff if min(row["p3"], key=row["p3"].get) in row["order"])
print(f"  ◎と自社p3 1位が食い違うレース {len(diff)}件（{pct(len(diff),len(rows))}）での直接対決:")
print(f"    1着率  アテマル◎ {pct(aw,len(diff))} vs 自社p3 1位 {pct(ow,len(diff))}")
print(f"    3着内率 アテマル◎ {pct(at,len(diff))} vs 自社p3 1位 {pct(ot,len(diff))}")


# ---------- 11. 7車限定（自社の主力母集団に揃える） ----------
print("\n== 11. 7車立てのみに揃えた比較 ==")
seven = [row for row in rows if row["n"] == 7]
if seven:
    w = sum(1 for row in seven if row["order"][0] == row["ax"])
    t3 = sum(1 for row in seven if row["ax"] in row["order"])
    ll = sum(1 for row in seven if row["e"][row["ax"]].get("is_line_leader"))
    fav = sum(1 for row in seven if row["mkt"][row["ax"]] == 1)
    print(f"  アテマル◎（7車 n={len(seven)}）: 1着 {pct(w,len(seven))} 3着内 {pct(t3,len(seven))} "
          f"ライン先頭 {pct(ll,len(seven))} 市場1番人気 {pct(fav,len(seven))}")
    ow = sum(1 for row in seven if row["order"][0] == min(row["p3"], key=row["p3"].get))
    ot = sum(1 for row in seven if min(row["p3"], key=row["p3"].get) in row["order"])
    ol = sum(1 for row in seven if row["e"][min(row["p3"], key=row["p3"].get)].get("is_line_leader"))
    print(f"  自社p3 1位（同一レース）     : 1着 {pct(ow,len(seven))} 3着内 {pct(ot,len(seven))} "
          f"ライン先頭 {pct(ol,len(seven))}")
    mw = sum(1 for row in seven if row["order"][0] == min(row["mkt"], key=row["mkt"].get))
    print(f"  市場1番人気（同一レース）     : 1着 {pct(mw,len(seven))}")
    bet = 9600 * len(seven)
    pay = sum(row["r"].get("payout") or 0 for row in seven)
    hit = sum(1 for row in seven if (row["r"].get("payout") or 0) > 0)
    net = sum(1 for row in seven if (row["r"].get("payout") or 0) > 9600)
    print(f"  7車のみの収支: 的中 {pct(hit,len(seven))} 表示的中 {pct(net,len(seven))} 回収率 {100*pay/bet:.1f}%")


# ---------- 12. 切り捨ての構造（どんな車を捨てるか） ----------
print("\n== 12. 切り捨ての構造 ==")
def profile(cars_fn, label):
    n = 0
    agg = Counter()
    t3 = 0
    for row in rows:
        for x in cars_fn(row):
            n += 1
            e = row["e"][x]
            agg["ライン先頭"] += 1 if e.get("is_line_leader") else 0
            agg["単騎"] += 1 if row["lsz"][e["line_group"]] <= 1 else 0
            agg["◎と同ライン"] += 1 if e["line_group"] == row["e"][row["ax"]]["line_group"] else 0
            agg["脚質:逃"] += 1 if e["style"] == "逃" else 0
            agg["脚質:追"] += 1 if e["style"] == "追" else 0
            agg["脚質:両"] += 1 if e["style"] == "両" else 0
            if x in row["order"]:
                t3 += 1
    print(f"  {label} n={n}  " + " ".join(f"{k} {pct(v,n)}" for k, v in agg.items()) +
          f"  実3着内率 {pct(t3,n)}")


profile(lambda row: row["part"], "相手にした車")
profile(lambda row: row["cut"], "切った車    ")
# 位置づけ別の採用率
print("  ライン内の位置別の採用率（◎以外の全車）:")
pos = defaultdict(lambda: [0, 0, 0])
for row in rows:
    for x in row["e"]:
        if x == row["ax"]:
            continue
        e = row["e"][x]
        key = "単騎" if row["lsz"][e["line_group"]] <= 1 else ("先頭" if e.get("is_line_leader") else f"{e.get('line_pos')}番手")
        d = pos[key]
        d[0] += 1
        d[1] += 1 if x in row["part"] else 0
        d[2] += 1 if x in row["order"] else 0
for k in sorted(pos, key=lambda k: -pos[k][0]):
    n, a, t = pos[k]
    print(f"    {k:8s} 出現 {n:5d}  相手採用 {pct(a,n)}  実3着内 {pct(t,n)}")
