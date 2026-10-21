#!/usr/bin/env python3
"""H22 目視確認: 型 A〜F 各1件（キー順で決定的に選ぶ・結果では選ばない）＋特殊ケースで、
二軸・頭・相手（3通り）・各形 F1〜F6 の買い目・配分・合成オッズ・着順・④ を出す。"""
import pickle
import numpy as np
from h01_common import *   # noqa
import h22_build as B

z = pickle.load(open(D / "h22" / "h22_arms.pkl", "rb"))
arms, cur, races = z["arms"], z["cur"], z["races"]
keys = sorted(arms)


def fmt_leg(k):
    return "-".join(map(str, k))


def show(tag, r, T=None):
    if r is None:
        print(f"      {tag:6s}: 組めない"); return
    st = sorted(r["stakes"].items(), key=lambda kv: -kv[1])
    flag = "" if r["gate"] else " [入稿ゲート落ち]"
    if T is not None and r["comp"] * 1e4 < T:
        flag += f" [見送り(合成{r['comp']*1e4:,.0f}円<{T:,}円)]"
    print(f"      {tag:6s}: {r['n']:2d}点 投資{r['inv']:,.0f} 払戻{r['pay']:,.0f} 合成{r['comp']:.2f}倍{flag} "
          + " ".join(f"{fmt_leg(k)}:{v}" for k, v in st[:12]))


def detail(key, tag):
    a, rc = arms[key], races[key]
    f = a["info"]
    print(f"\n==== [{tag}] {key} 型{rc['tl']} {rc['rtype']}  着={f['win_tf']} 確定オッズ={f['pay_tf']:.1f}倍")
    print("   車: p3/pw/ライン.位置/脚質 = " + "  ".join(
        f"{c}:{f['p3'][c]:.2f}/{f['pw'][c]:.2f}/{f['lg'][c]}.{int(f['lp'][c])}/{f['st'][c]}" for c in range(1, 8)))
    print(f"   二軸 A={f['A']} B={f['B']} 頭H={f['H']} O={f['O']}  二軸同ライン={f['axes_same_line']} 単騎数={f['n_single']}  相手: {f['partners']}")
    c = cur.get(key)
    if c:
        st = sorted(c["stakes"].items(), key=lambda kv: -kv[1])
        print(f"   ① {c['plan']} slot={c['slot']}: {c['n']}点 投資{c['inv']:,.0f} 払戻{c['pay']:,.0f} " + " ".join(f"{fmt_leg(k)}:{v}" for k, v in st[:8]))
    else:
        print("   ① 売っていない（軸信頼ゲート／日次上限など）")
    seen = {}
    for pk in B.PKEYS:
        P = a["info"]["partners"][pk]
        if P in seen:
            print(f"   [{pk}] 相手 {P} = {seen[P]} と同じ"); continue
        seen[P] = pk
        print(f"   [{pk}] 相手 {P}")
        r = a["rows"][pk]
        for fm in ("F1", "F2", "F3", "F4", "F5"):
            show(fm, r[fm], 30000)
        show("F6@3", r["F6@3"], 30000); show("F6@5", r["F6@5"], 50000); show("F6@2", r["F6@2"])
        n3 = r["F6@3"]["n"] if r["F6@3"] else None
        if n3 and a["ctrl"].get(n3):
            show(f"④n={n3}", a["ctrl"][n3], 30000)


picked = []
for t in "ABCDEF":
    for k in keys:
        if races[k]["tl"] == t and k not in picked:
            picked.append(k); detail(k, f"型{t}"); break
# 特殊ケース（キー順で最初に当たるもの）
conds = [
    ("二軸が同ライン", lambda f, a: f["axes_same_line"] and f["partners"]["Pp3"] != f["partners"]["Pline"]),
    ("二軸が別ライン", lambda f, a: not f["axes_same_line"] and f["partners"]["Pp3"] != f["partners"]["Pline"]),
    ("Pline≠Pline_t（単騎が相手候補）", lambda f, a: f["partners"]["Pline"] != f["partners"]["Pline_t"]),
    ("F6@3 が見送り（先頭1点で合成3倍未満）", lambda f, a: a["rows"]["Pp3"]["F6@3"] is not None and a["rows"]["Pp3"]["F6@3"]["comp"] < 3.0),
    ("F6@3 が多点（6点以上）", lambda f, a: a["rows"]["Pp3"]["F6@3"] is not None and a["rows"]["Pp3"]["F6@3"]["n"] >= 6),
]
for nm, fn in conds:
    for k in keys:
        if k in picked:
            continue
        if fn(arms[k]["info"], arms[k]):
            picked.append(k); detail(k, nm); break
