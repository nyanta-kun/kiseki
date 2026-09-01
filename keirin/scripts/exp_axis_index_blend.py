#!/usr/bin/env python3
"""軸選定に「1着率・2着内率・3着内率」の合成指数を使えるか（2026-08-26・ユーザー案）。

> 1着率がたいして離しておらず、3着内率が他と逆転している。1着・2着内・3着内の
> 平均や傾斜で作った新たな指数を軸選定に使うアプローチもあるかと思います。

## 本番の軸選定（`rank_7s_select_axis` の3ヘッド版）
    軸1 = 1着率(pw) の最上位
    軸2 = z(3着内率) − RANK_AXIS2_BAD_WEIGHT × z(大敗確率) の最上位
🔴 **2着内率(`pred_top2_pct`)はどこにも使われていない。**

## この台
`wt_entries.pred_{win,top2,top3}_pct`（**backfill 値**）で全7車レースを走らせ、
合成指数で軸を選び直したときの **二軸そろい率**（軸2車がともに3着以内）を比べる。
⚠️ backfill なので絶対水準は上振れするが、**同一入力どうしの Δ は使える**
   （[[keirin_s24_vintage_reverify_2026_08_23]]）。大敗確率は列が無いので
   現行アームは「badなし近似」＝ z(p3) の最上位（本番と実質同等・
   [[keirin_handoff_2026_08_23_pm]]）。
ここで差が出なければ vintage を作る意味が無い、というスクリーニング。

## 実行される検査
    main()       9通りの合成 × 二軸そろい率（通算 53.29〜53.98% ＝ 全部同じ）
    disagree()   軸が入れ替わるレースだけの直接対決 + ユーザーの見立ての層
    konsen()     **混戦に限れば効くのでは**を層別 + ブートストラップ CI で潰す
                 （2026-08-27 追加・宇都宮4R の追検証）
    axis1_only() 軸1 の規則だけを 1着率 / 3着以内率 で比べる
    with_line()  指数ではなく **ライン構造** で軸を取ったらどうか
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

WINDOWS = [("2024後半", "2024-07-01", "2024-12-31"),
           ("2025前半", "2025-01-01", "2025-06-30"),
           ("2025後半", "2025-07-01", "2025-12-31"),
           ("2026前半", "2026-01-01", "2026-06-30"),
           ("2026後半", "2026-07-01", "2026-12-31")]


def load(n_entries: int = 7):
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, r.race_date::text, e.frame_no,
               e.pred_win_pct, e.pred_top2_pct, e.pred_top3_pct, e.finish_order
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = %s AND r.race_date >= '2024-07-01'
        """, (n_entries,))
    races = defaultdict(dict)
    date = {}
    for rk, d, fn, pw, p2, p3, fo in cur.fetchall():
        if pw is None or p2 is None or p3 is None:
            continue
        races[rk][int(fn)] = (float(pw), float(p2), float(p3),
                              int(fo) if fo else 0)
        date[rk] = d
    con.close()
    out = []
    for rk, cars in races.items():
        if len(cars) != n_entries:
            continue
        top3 = {f for f, v in cars.items() if 1 <= v[3] <= 3}
        if len(top3) != 3:
            continue
        out.append((rk, date[rk], cars, top3))
    return out


def z(vals: dict[int, float]) -> dict[int, float]:
    n = len(vals)
    m = sum(vals.values()) / n
    sd = (sum((v - m) ** 2 for v in vals.values()) / n) ** 0.5
    return {k: (v - m) / sd if sd > 0 else 0.0 for k, v in vals.items()}


def axes(cars: dict, w: tuple[float, float, float], axis1_by_pw: bool):
    pw = {f: v[0] for f, v in cars.items()}
    p2 = {f: v[1] for f, v in cars.items()}
    p3 = {f: v[2] for f, v in cars.items()}
    zw, z2, z3 = z(pw), z(p2), z(p3)
    sc = {f: w[0] * zw[f] + w[1] * z2[f] + w[2] * z3[f] for f in cars}
    order = sorted(cars, key=lambda f: (-sc[f], f))
    if axis1_by_pw:
        a1 = max(pw, key=lambda f: (pw[f], -f))
        a2 = max((f for f in order if f != a1), key=lambda f: (sc[f], -f))
        return a1, a2
    return order[0], order[1]


def main() -> None:
    rows = load(7)
    print(f"7車 {len(rows):,}R  {min(r[1] for r in rows)}〜{max(r[1] for r in rows)}")
    arms = [
        ("現行近似 軸1=pw / 軸2=p3", (0, 0, 1), True),
        ("軸1=pw / 軸2=p2", (0, 1, 0), True),
        ("軸1=pw / 軸2=(p2+p3)/2", (0, .5, .5), True),
        ("軸1=pw / 軸2=(pw+p2+p3)/3", (1/3, 1/3, 1/3), True),
        ("両軸=合成(1,1,1)", (1/3, 1/3, 1/3), False),
        ("両軸=合成(3,2,1)傾斜", (.5, 1/3, 1/6), False),
        ("両軸=合成(1,2,3)傾斜", (1/6, 1/3, .5), False),
        ("両軸=p3のみ", (0, 0, 1), False),
        ("両軸=p2のみ", (0, 1, 0), False),
    ]
    print("\n[二軸そろい率＝軸2車がともに3着以内]")
    print(f"  {'腕':30s}" + "".join(f"{w:>10s}" for w, _, _ in WINDOWS) + f"{'通算':>10s}")
    for nm, w, by_pw in arms:
        line = f"  {nm:30s}"
        tot_n = tot_h = 0
        for _, lo, hi in WINDOWS:
            sub = [r for r in rows if lo <= r[1] <= hi]
            h = sum(1 for _, _, cars, top3 in sub
                    if set(axes(cars, w, by_pw)) <= top3)
            line += f"{h/len(sub)*100:9.2f}%"
            tot_n += len(sub); tot_h += h
        line += f"{tot_h/tot_n*100:9.2f}%"
        print(line)




def disagree() -> None:
    """軸が実際に入れ替わるレースだけで比べる（集計に埋もれていないか）。

    さらにユーザーの見立て「1着率が離れていない ∧ 3着内率が逆転している」レースへ
    層別する。
    """
    rows = load(7)
    base_w, base_pw = (0, 0, 1), True          # 現行近似
    cands = [("軸2=p2", (0, 1, 0), True),
             ("軸2=(p2+p3)/2", (0, .5, .5), True),
             ("両軸=p2", (0, 1, 0), False),
             ("両軸=合成(1,2,3)", (1/6, 1/3, .5), False)]

    def pwgap(cars):
        v = sorted((c[0] for c in cars.values()), reverse=True)
        return v[0] - v[1]

    def inverted(cars):
        """1着率の順位と3着内率の順位が上位2枚で入れ替わっているか。"""
        pw_o = sorted(cars, key=lambda f: -cars[f][0])[:2]
        p3_o = sorted(cars, key=lambda f: -cars[f][2])[:2]
        return set(pw_o) != set(p3_o)

    gaps = sorted(pwgap(c) for _, _, c, _ in rows)
    q33 = gaps[len(gaps) // 3]

    for nm, w, by_pw in cands:
        print(f"\n[{nm}] 現行近似との直接対決")
        print("  窓        入替率   入替レースでの二軸そろい: 現行 / 新   差")
        for wname, lo, hi in WINDOWS:
            sub = [r for r in rows if lo <= r[1] <= hi]
            ch = [r for r in sub
                  if set(axes(r[2], base_w, base_pw)) != set(axes(r[2], w, by_pw))]
            if not ch:
                continue
            a = sum(1 for _, _, c, t in ch if set(axes(c, base_w, base_pw)) <= t) / len(ch) * 100
            b = sum(1 for _, _, c, t in ch if set(axes(c, w, by_pw)) <= t) / len(ch) * 100
            print(f"  {wname:8s} {len(ch)/len(sub)*100:6.1f}%      {a:6.2f}% / {b:6.2f}%  {b-a:+6.2f}pt")

    print("\n[ユーザーの見立ての層] 1着率の差が小さい(下位1/3) ∧ 上位2枚が p3 と逆転")
    print("  窓        n     現行 / 軸2=p2 / 両軸=p2 / 合成(1,2,3)")
    for wname, lo, hi in WINDOWS:
        sub = [r for r in rows if lo <= r[1] <= hi
               and pwgap(r[2]) <= q33 and inverted(r[2])]
        if not sub:
            continue
        def rate(w, by_pw):
            return sum(1 for _, _, c, t in sub if set(axes(c, w, by_pw)) <= t) / len(sub) * 100
        print(f"  {wname:8s} {len(sub):5d}   {rate(base_w, base_pw):6.2f}% / {rate((0,1,0),True):6.2f}%"
              f" / {rate((0,1,0),False):6.2f}% / {rate((1/6,1/3,.5),False):6.2f}%")




def konsen() -> None:
    """**混戦に限れば効くのでは**を層別で潰す（2026-08-27・宇都宮4R の追検証）。

    ユーザーの見立ては「1着率がたいして離れておらず、3着内率が逆転している」。
    その層＝**pw トップが下位1/3 ∧ pw 上位2枚と p3 上位2枚が食い違う**を作り、
    現行（軸1=pw / 軸2=p3）と **両軸=p3**（＝3着内率トップを軸にする案）を
    直接対決させる。`disagree()` は軸2/合成の4腕しか見ていないので、
    「両軸=p3」だけを取り出してブートストラップ CI まで出すのがこの関数。

    ## 結果（7車 49,968R・2024-07〜2026-08）: 🔴 効かない

        層                        n       現行 / 両軸=p3      差
        全レース               49,968   53.76% / 53.83%   +0.07pt
        混戦(pwトップ<=38.7%)   16,732   41.67% / 41.86%   +0.19pt
        上位2枚が逆転           13,193   44.55% / 44.81%   +0.27pt
        混戦 ∧ 逆転（4R型）      5,977   37.63% / 38.15%   +0.52pt

    宇都宮4R型の層でも **Δ +0.52pt / 95%CI[-0.22,+1.24]**、2026年だけなら
    **+0.00pt / CI[-1.21,+1.26]**。窓ごとの符号も反転する
    （2024後半 -0.33 / 2025前半 +1.51 / 2025後半 +1.22 / 2026前半 +0.08 /
    2026後半 -0.19）。**採用の根拠にならない。**

    ⚠️ 2026-08-26 宇都宮4R の pw トップ 21.6% は 7車全体の**下から1.6%点**＝
       層の代表ではなく極端に混戦な側。見立て自体は正確だが、それでも動かない。
    """
    rows = load(7)
    tops = sorted(max(v[0] for v in c.values()) for _, _, c, _ in rows)
    q33 = tops[len(tops) // 3]

    def cur_ax(c):
        a1 = max(c, key=lambda f: (c[f][0], -f))
        a2 = max((f for f in c if f != a1), key=lambda f: (c[f][2], -f))
        return {a1, a2}

    def p3_ax(c):
        o = sorted(c, key=lambda f: (-c[f][2], f))
        return {o[0], o[1]}

    def inverted(c):
        return (set(sorted(c, key=lambda f: -c[f][0])[:2])
                != set(sorted(c, key=lambda f: -c[f][2])[:2]))

    def konsen_race(c):
        return max(v[0] for v in c.values()) <= q33

    strata = [("全レース", lambda c: True),
              (f"混戦(pwトップ<={q33:.1f}%)", konsen_race),
              ("上位2枚が逆転", inverted),
              ("混戦 ∧ 逆転（宇都宮4R型）",
               lambda c: konsen_race(c) and inverted(c))]

    print(f"\n[混戦層で「3着内率トップを軸」は効くか] 7車 {len(rows):,}R")
    print("  二軸そろい率＝軸2車がともに3着以内   現行(pw+p3) / 両軸=p3 / 差")
    for nm, f in strata:
        print(f"\n  [{nm}]")
        tn = ta = tb = 0
        for wname, lo, hi in WINDOWS:
            sub = [r for r in rows if lo <= r[1] <= hi and f(r[2])]
            if not sub:
                continue
            n = len(sub)
            a = sum(1 for _, _, c, t in sub if cur_ax(c) <= t)
            b = sum(1 for _, _, c, t in sub if p3_ax(c) <= t)
            tn += n; ta += a; tb += b
            print(f"    {wname:8s} n={n:6d}  {a/n*100:6.2f}% / {b/n*100:6.2f}%"
                  f"  {(b - a) / n * 100:+6.2f}pt")
        print(f"    {'通算':8s} n={tn:6d}  {ta/tn*100:6.2f}% / {tb/tn*100:6.2f}%"
              f"  {(tb - ta) / tn * 100:+6.2f}pt")

    # 宇都宮4R型の層だけ、対応のあるブートストラップで CI を出す
    rng = random.Random(0)
    print("\n  [宇都宮4R型（混戦 ∧ 逆転）の対応ありブートストラップ]")
    for label, lo, hi in [("通算", "2024-07-01", "2026-12-31"),
                          ("2026年", "2026-01-01", "2026-12-31")]:
        sub = [r for r in rows if lo <= r[1] <= hi
               and konsen_race(r[2]) and inverted(r[2])]
        d = [(1 if p3_ax(c) <= t else 0) - (1 if cur_ax(c) <= t else 0)
             for _, _, c, t in sub]
        n = len(d)
        bs = sorted(sum(rng.choices(d, k=n)) / n * 100 for _ in range(2000))
        print(f"    {label:6s} n={n:5d}  Δ={sum(d)/n*100:+.2f}pt"
              f"  95%CI[{bs[50]:+.2f},{bs[1949]:+.2f}]")
        ch = [(c, t) for _, _, c, t in sub if cur_ax(c) != p3_ax(c)]
        a = sum(1 for c, t in ch if cur_ax(c) <= t)
        b = sum(1 for c, t in ch if p3_ax(c) <= t)
        print(f"           軸が実際に入れ替わる {len(ch)}R ({len(ch)/n*100:.1f}%): "
              f"現行 {a/len(ch)*100:.2f}% / 両軸=p3 {b/len(ch)*100:.2f}%")


def axis1_only() -> None:
    """軸1（1着に置く車／三連複では最重視する車）だけを見る。"""
    rows = load(7)
    rules = [("pw 最上位（現行）", lambda c: max(c, key=lambda f: (c[f][0], -f))),
             ("p2 最上位", lambda c: max(c, key=lambda f: (c[f][1], -f))),
             ("p3 最上位", lambda c: max(c, key=lambda f: (c[f][2], -f))),
             ("合成(1,1,1)", None), ("合成(3,2,1)", None), ("合成(1,2,3)", None)]
    ws = {"合成(1,1,1)": (1/3, 1/3, 1/3), "合成(3,2,1)": (.5, 1/3, 1/6),
          "合成(1,2,3)": (1/6, 1/3, .5)}
    print("\n[軸1の質] 1着になる率 / 3着以内に入る率")
    print(f"  {'規則':18s}" + "".join(f"{w:>16s}" for w, _, _ in WINDOWS))
    for nm, f in rules:
        line = f"  {nm:18s}"
        for _, lo, hi in WINDOWS:
            sub = [r for r in rows if lo <= r[1] <= hi]
            n1 = n3 = 0
            for _, _, cars, top3 in sub:
                if f is None:
                    w = ws[nm]
                    zw, z2, z3 = (z({k: v[0] for k, v in cars.items()}),
                                  z({k: v[1] for k, v in cars.items()}),
                                  z({k: v[2] for k, v in cars.items()}))
                    a1 = max(cars, key=lambda k: (w[0]*zw[k] + w[1]*z2[k] + w[2]*z3[k], -k))
                else:
                    a1 = f(cars)
                if cars[a1][3] == 1:
                    n1 += 1
                if a1 in top3:
                    n3 += 1
            line += f"  {n1/len(sub)*100:5.2f}/{n3/len(sub)*100:5.2f}%"
        print(line)




def with_line() -> None:
    """周辺確率の混ぜ方ではなく **ライン構造** で軸を取るとどうなるか。

    2026-08-26 宇都宮4R（1-5-7）は L1(5-1-2-7) の同ライン3車決着で、本番の
    軸1（1着率最上位の4番＝別ラインの先頭）は6着だった。3着の7番は
    1着率 0.3% / 3着内率 10.6% ＝ モデルはほぼゼロと見ていた車。
    """
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, r.race_date::text, e.frame_no,
               e.pred_win_pct, e.pred_top2_pct, e.pred_top3_pct, e.finish_order,
               e.line_group, e.line_pos
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date >= '2024-07-01'
        """)
    races = defaultdict(dict); date = {}
    for rk, d, fn, pw, p2, p3, fo, lg, lp in cur.fetchall():
        if pw is None or p3 is None:
            continue
        races[rk][int(fn)] = dict(pw=float(pw), p2=float(p2 or 0), p3=float(p3),
                                  fo=int(fo) if fo else 0, lg=lg, lp=lp)
        date[rk] = d
    con.close()
    rows = []
    for rk, cars in races.items():
        if len(cars) != 7:
            continue
        top3 = {f for f, v in cars.items() if 1 <= v["fo"] <= 3}
        if len(top3) == 3:
            rows.append((rk, date[rk], cars, top3))
    print(f"\n[ライン構造で軸を取る] 7車 {len(rows):,}R")

    def cur_axes(c):
        a1 = max(c, key=lambda f: (c[f]["pw"], -f))
        a2 = max((f for f in c if f != a1), key=lambda f: (c[f]["p3"], -f))
        return {a1, a2}

    def strong_line(c):
        best = None
        for g in {v["lg"] for v in c.values() if v["lg"] not in (None, "", "0")}:
            mem = [f for f in c if c[f]["lg"] == g]
            lead = [f for f in mem if str(c[f]["lp"]) == "1"]
            sec = [f for f in mem if str(c[f]["lp"]) == "2"]
            if not lead or not sec:
                continue
            s = sum(c[f]["pw"] for f in mem)
            if best is None or s > best[0]:
                best = (s, lead[0], sec[0])
        return None if best is None else {best[1], best[2]}

    def pw_plus_next(c):
        a1 = max(c, key=lambda f: (c[f]["pw"], -f))
        g, lp = c[a1]["lg"], c[a1]["lp"]
        nxt = [f for f in c if f != a1 and c[f]["lg"] == g and g not in (None, "", "0")
               and str(lp).isdigit() and str(c[f]["lp"]).isdigit()
               and int(c[f]["lp"]) == int(lp) + 1]
        if nxt:
            return {a1, nxt[0]}
        return cur_axes(c)

    arms = [("現行近似 pw+p3", cur_axes),
            ("最強ライン 先頭+番手", strong_line),
            ("pw最上位+その直後", pw_plus_next)]
    print(f"  {'腕':22s}" + "".join(f"{w:>10s}" for w, _, _ in WINDOWS) + f"{'通算':>10s}")
    for nm, f in arms:
        line = f"  {nm:22s}"; tn = th = 0
        for _, lo, hi in WINDOWS:
            sub = [r for r in rows if lo <= r[1] <= hi]
            ok = n = 0
            for _, _, c, t in sub:
                ax = f(c)
                if ax is None:
                    continue
                n += 1
                if ax <= t:
                    ok += 1
            line += f"{ok/n*100:9.2f}%"; tn += n; th += ok
        line += f"{th/tn*100:9.2f}%"
        print(line)


if __name__ == "__main__":
    main()
    disagree()
    konsen()
    axis1_only()
    with_line()
