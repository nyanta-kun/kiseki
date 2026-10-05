#!/usr/bin/env python3
"""H15 売上側（2026-08-27〜最新・記述）。高額枠（F_sign / {B,C,D}_sign|_big）↔ 同日・同種別の hit 商品。
読み取りのみ。1本あたり販売有償pt = netkeirin_sales_race.sold_paid_points。開催日ブートストラップ 2,000回。"""
from __future__ import annotations
import sys, collections
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import q

JST = timezone(timedelta(hours=9))
HIT = {f"{t}_hit" for t in "ABCDEF"} | {"A_trio"}


def rtg(rt):
    rt = rt or ""
    if "準決勝" in rt: return "準決勝系"
    if "決勝" in rt: return "決勝系"
    if "特選" in rt or "選抜" in rt or "特秀" in rt or "優秀" in rt: return "特選系"
    if "予選" in rt: return "予選"
    if "一般" in rt: return "一般"
    return "他"


def band(h):
    return "朝" if h < 12 else "昼" if h < 15 else "夕" if h < 18 else "夜"


def load():
    c, rows = q("""select s.race_key, s.race_date, s.sold_paid_points, s.n_sold, w.race_type, w.n_entries, w.start_at,
                   b.rank_key, b.origin,
                   (select t.type_label from keirin.type_lab_picks t where t.race_key=s.race_key limit 1) as tl
                   from keirin.netkeirin_sales_race s
                   join keirin.wt_races w on w.race_key=s.race_key
                   join keirin.netkeirin_submissions b on b.race_key=s.race_key and b.deleted_at is null and b.status='published'
                   where s.race_date >= '20260827'""")
    out = []
    for rk, d, pt, ns, rt, ne, st, rank, origin, tl in rows:
        h = datetime.fromtimestamp(int(st), JST).hour if st else None
        out.append(dict(k=rk, day=d, pt=float(pt or 0), rt=rt, g=rtg(rt), ne=ne, rank=rank, origin=origin, tl=tl,
                        b=band(h) if h is not None else "?"))
    return out


def paired(rows, sign_f, ctrl_f, cellkey, label, rng, nboot=2000):
    sg = [r for r in rows if sign_f(r)]
    ct = [r for r in rows if ctrl_f(r)]
    cm = collections.defaultdict(list)
    for r in ct:
        cm[cellkey(r)].append(r["pt"])
    items = []   # (day, diff)
    for r in sg:
        v = cm.get(cellkey(r))
        if v:
            items.append((r["day"], r["pt"] - float(np.mean(v)), r["pt"], float(np.mean(v))))
    days = sorted({it[0] for it in items})
    byd = collections.defaultdict(list)
    for it in items:
        byd[it[0]].append(it[1])
    est = float(np.mean([it[1] for it in items])) if items else float("nan")
    bs = []
    for _ in range(nboot):
        pick = rng.integers(0, len(days), len(days))
        v = [x for j in pick for x in byd[days[j]]]
        bs.append(np.mean(v))
    lo, hi = np.percentile(bs, [2.5, 97.5]) if bs else (float("nan"),) * 2
    # 日ペア（日ごとの平均差の符号）
    dd = [np.mean(byd[d]) for d in days]
    print(f"| {label} | sign {len(sg)}本 → 対照あり {len(items)}本・{len(days)}日 | sign 平均 {np.mean([it[2] for it in items]):,.0f} / 対照 平均 {np.mean([it[3] for it in items]):,.0f} | 差 **{est:+,.0f}pt** [{lo:+,.0f}, {hi:+,.0f}] | 日ごと差>0: {sum(1 for x in dd if x > 0)}/{len(days)}日 |")
    return dict(label=label, n_sign=len(sg), n_pair=len(items), days=len(days), est=est, lo=float(lo), hi=float(hi))


def main():
    rows = load()
    rng = np.random.default_rng(20261008)
    print(f"対象 {len(rows)} 本（2026-08-27〜・published・sales あり）  日数 {len({r['day'] for r in rows})}")
    cnt = collections.Counter(r["rank"] for r in rows)
    print("rank_key 内訳:", dict(cnt.most_common()))
    # 水準
    def lvl(name, f):
        v = np.array([r["pt"] for r in rows if f(r)])
        if len(v):
            print(f"| {name} | {len(v)} | {v.mean():,.0f} | {np.median(v):,.0f} | {np.mean(v == 0) * 100:.1f}% |")
    print("\n| 区分 | 本数 | 平均pt/本 | 中央 | 販売0の割合 |\n|---|---|---|---|---|")
    lvl("F_sign（看板枠）", lambda r: r["rank"] == "F_sign")
    lvl("F_sign 決勝系", lambda r: r["rank"] == "F_sign" and r["g"] == "決勝系")
    lvl("F_sign 準決勝系", lambda r: r["rank"] == "F_sign" and r["g"] == "準決勝系")
    is_hp = lambda r: (r["tl"] in ("B", "C", "D") and r["ne"] == 7 and (r["rank"].endswith(("_sign", "_big")) or r["origin"] == "highpay_fill"))
    lvl("B/C/D 高額枠（_sign|_big|highpay_fill・7車）", is_hp)
    for g in ("決勝系", "準決勝系", "特選系", "予選", "一般", "他"):
        lvl(f"　うち {g}", lambda r, g=g: is_hp(r) and r["g"] == g)
    lvl("hit 商品（A〜F_hit, A_trio）全体", lambda r: r["rank"] in HIT)
    lvl("B/C/D の hit 商品", lambda r: r["rank"] in {"B_hit", "C_hit", "D_hit"})
    lvl("決勝系の hit 商品", lambda r: r["rank"] in HIT and r["g"] == "決勝系")
    lvl("準決勝系の hit 商品", lambda r: r["rank"] in HIT and r["g"] == "準決勝系")
    print(f"\n高額枠 アイコン（ACT_TYPE_BY_PLAN）: *_sign / *_big は穴狙い、*_hit / A_trio は既定。タイトルも異なる（例 F_sign「高額狙いの三連単｜大混戦」↔ F_hit「押さえの三連単｜大混戦」）。")
    print("\n## 同日ペア（高額枠の各本 − 同日・同種別の hit 商品の平均）\n")
    print("| 比較 | 本数 | 水準 | 差 [95%CI 開催日ブートストラップ] | 日ごとの符号 |\n|---|---|---|---|---|")
    ck_g = lambda r: (r["day"], r["g"])
    ck_gb = lambda r: (r["day"], r["g"], r["b"])
    res = {}
    res["a"] = paired(rows, lambda r: r["rank"] == "F_sign",
                     lambda r: r["rank"] in HIT, ck_g, "(a) F_sign ↔ 同日・同種別の hit（A〜E_hit・A_trio）", rng)
    res["a_b"] = paired(rows, lambda r: r["rank"] == "F_sign",
                       lambda r: r["rank"] in HIT, ck_gb, "(a') 同＋発走時間帯", rng)
    res["a_all"] = paired(rows, lambda r: r["rank"] == "F_sign",
                         lambda r: r["rank"] in HIT or r["rank"] in {"A_ana", "T_firm", "T_mid", "T_axis"}, ck_g, "(a'') 対照を非高額の全商品に", rng) if False else None
    res["b"] = paired(rows, is_hp,
                     lambda r: r["rank"] in {"B_hit", "C_hit", "D_hit"}, ck_g, "(b) B/C/D 高額枠 ↔ 同日・同種別の B/C/D_hit", rng)
    res["b_b"] = paired(rows, is_hp,
                       lambda r: r["rank"] in {"B_hit", "C_hit", "D_hit"}, ck_gb, "(b') 同＋発走時間帯", rng)
    res["b_any"] = paired(rows, is_hp, lambda r: r["rank"] in HIT, ck_g, "(b'') 対照を A〜F の hit 全体に", rng)
    res["b_sign_only"] = paired(rows, lambda r: is_hp(r) and r["rank"].endswith("_sign"),
                               lambda r: r["rank"] in {"B_hit", "C_hit", "D_hit"}, ck_g, "(b''') _sign のみ（_big を除く）", rng)
    # 期間別（高額枠 = 9/22 以降は _sign のみ・9/6〜9/21 は _sign+_big）
    for lab, lo_, hi_ in (("〜2026-09-21", "20260827", "20260921"), ("2026-09-22〜", "20260922", "20261004")):
        sub = [r for r in rows if lo_ <= r["day"] <= hi_]
        paired(sub, lambda r: r["rank"] == "F_sign", lambda r: r["rank"] in HIT, ck_g, f"(a) {lab}", rng)
        paired(sub, is_hp, lambda r: r["rank"] in {"B_hit", "C_hit", "D_hit"}, ck_g, f"(b) {lab}", rng)
    import json
    print("\nJSON", json.dumps({k: v for k, v in res.items() if v}, ensure_ascii=False))


main()
