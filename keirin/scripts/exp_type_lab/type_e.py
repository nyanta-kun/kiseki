#!/usr/bin/env python3
"""型E「混戦・中」に載る買い方を網羅的に探す（2026-08-27）。

型E = axis_sum(vintage p3 上位2車の合計) < 1.44 ∧ 荒れ度 == 0
  探索窓 1,941R / 確認窓 946R。二軸そろい 40.2%・確定三連複オッズ中央 11.3倍。

使い方:  .venv/bin/python scripts/exp_type_lab/type_e.py <section> [<section>...]
  base     型Eの素の姿（二軸そろい・相手の出どころ・払戻分布）
  example  ベースラインを1件、実データで目視確認する（作法）
  cover    軸の構造（1軸固定+2軸目2点 が三連複で意味を持つか）
  overlap  既存商品との重なり（決勝系率・印一致率）
  trio     三連複 軸2車 + 相手k点（相手の選び方・配分）
  trio2    三連複 追加（均等の相手組み合わせ / 予測オッズ下限）
  axis2    1軸固定 + 2軸目2点 + 3着流し（総当たり）
  tf       三連単（点数・下限・並べ替え・フォーメーション）
  tf2      三連単 追加（下限 × 点数）
  fine     三連単の微調整（下限 25/28/30/32/35 × 点数 12〜20）
  agree    印一致/不一致での分割（三連複）
  agree2   印一致/不一致（採用候補）
  robust   4分割窓での頑健性
  sel      型E内のレース選別
  xtype    採用候補の腕を全型に当てる
  xtype2   三連複の腕を全型に当てる
  final    採用候補と主要な棄却腕（一覧）
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# 🔴 NpzFile は添字アクセスのたびに解凍する（1腕で数分かかった）。
#    全配列を dict へ展開して common 側の参照ごと差し替える。
C._Z = {k: v for k, v in C.board().items()}
Z = C.board()
DATE = Z["DATE"]
WINDOWS = ("explore", "confirm")
NDAYS = {w: C.days_of(C.select(None, w)) for w in WINDOWS}


# ───────────────────────── 道具 ─────────────────────────

_MAP210 = np.array([C.C3IDX[frozenset(c)] for c in C.CANON])
_TPC: dict[int, np.ndarray] = {}
_ORD: dict[int, list] = {}


def trio_prob(i: int) -> np.ndarray:
    """位置別合成PLの三連単確率(210)を三連複(35)へ畳む。"""
    v = _TPC.get(i)
    if v is None:
        v = _TPC[i] = np.bincount(_MAP210, weights=Z["PROB"][i].astype(np.float64),
                                  minlength=35)
    return v


def p3_order(i: int) -> list:
    v = _ORD.get(i)
    if v is None:
        v = _ORD[i] = [int(x) for x in np.argsort(-Z["P3"][i]) + 1]
    return v


def run_trio(build, window: str, tilt: bool = True) -> dict:
    """build(i) -> 買い目(list[frozenset]) or None。ゲートを通してから集計。"""
    recs = []
    for i in C.select("E", window):
        combos = build(int(i))
        if not combos:
            continue
        st = C.trio_stakes(int(i), combos, tilt=tilt)
        if st is None or not C.trio_gate(int(i), st):
            continue
        inv, pay, mean = C.trio_result(int(i), st)
        recs.append(dict(date=DATE[i], inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, n_days_all=NDAYS[window])


def run_tf(build, window: str) -> dict:
    recs = []
    for i in C.select("E", window):
        combos = build(int(i))
        if not combos:
            continue
        st = C.tf_stakes(int(i), combos)
        if not C.tf_gate(int(i), st):
            continue
        inv, pay = C.tf_result(int(i), st)
        mean = sum(st[c] * float(Z["PO"][int(i)][C.CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=DATE[i], inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, n_days_all=NDAYS[window])


def show(name: str, build, tilt: bool = True, kind: str = "trio"):
    for w in WINDOWS:
        s = run_trio(build, w, tilt) if kind == "trio" else run_tf(build, w)
        print(C.line(f"{name} [{w[:3]}]", s))


# ───────────────────────── 買い目の作り方 ─────────────────────────

def legs_by_p3(i, ranks):
    """軸=p3上位2車、相手=p3順位 ranks（1-indexed の全体順位）。"""
    o = p3_order(i)
    a1, a2 = o[0], o[1]
    return [frozenset((a1, a2, o[r - 1])) for r in ranks if r <= 7]


def legs_by_joint(i, k, desc=True):
    """軸=p3上位2車固定。相手は「三者同時確率」の高い順(desc)/低い順 に k 点。"""
    o = p3_order(i)
    a1, a2 = o[0], o[1]
    tp = trio_prob(i)
    cand = [(tp[C.C3IDX[frozenset((a1, a2, x))]], x) for x in o[2:]]
    cand.sort(key=lambda t: -t[0] if desc else t[0])
    return [frozenset((a1, a2, x)) for _, x in cand[:k]]


def legs_by_po(i, k, cheap=True):
    """軸=p3上位2車固定。相手は予測オッズの安い順(cheap)/高い順 に k 点。"""
    o = p3_order(i)
    a1, a2 = o[0], o[1]
    po = Z["TRIO_PO"][i]
    cand = [(float(po[C.C3IDX[frozenset((a1, a2, x))]]), x) for x in o[2:]]
    cand = [c for c in cand if np.isfinite(c[0]) and c[0] > 0]
    cand.sort(key=lambda t: t[0] if cheap else -t[0])
    return [frozenset((a1, a2, x)) for _, x in cand[:k]]


# ── 1軸固定 + 2軸目2点 ──

def axis2_cands(i, how):
    """軸1(=p3 1位) と 2軸目候補2車 を返す。"""
    o = p3_order(i)
    a1 = o[0]
    rest = o[1:]
    if how == "p3":                       # p3 の 2位・3位
        return a1, [rest[0], rest[1]]
    if how == "p3_23_24":                 # p3 の 2位・4位
        return a1, [rest[0], rest[2]]
    if how == "joint":                    # 軸1との同時確率（3着目で周辺化）
        tp = trio_prob(i)
        sc = []
        for x in rest:
            s = sum(tp[C.C3IDX[frozenset((a1, x, y))]] for y in rest if y != x)
            sc.append((s, x))
        sc.sort(key=lambda t: -t[0])
        return a1, [sc[0][1], sc[1][1]]
    if how == "line":                     # 軸1と同ラインを優先、足りなければ p3 順
        lg = Z["LG"][i]
        same = [x for x in rest if lg[x - 1] == lg[a1 - 1] and lg[a1 - 1] not in ("", "0")]
        pick = same[:2] + [x for x in rest if x not in same]
        return a1, pick[:2]
    if how == "pw":                       # 1着率の上位2（軸1除く）
        pw = C.pw_order(i)
        pick = [x for x in pw if x != a1][:2]
        return a1, pick
    raise ValueError(how)


def legs_axis2(i, how, m, third="p3"):
    """{a1, b, x} を 2軸目候補 b ごとに 3着流し m 点。"""
    a1, bs = axis2_cands(i, how)
    tp = trio_prob(i) if third == "joint" else None
    out = []
    for b in bs:
        rest = [x for x in p3_order(i) if x not in (a1, b)]
        if third == "joint":
            rest.sort(key=lambda x: -tp[C.C3IDX[frozenset((a1, b, x))]])
        for x in rest[:m]:
            f = frozenset((a1, b, x))
            if f not in out:
                out.append(f)
    return out


def stakes_axis2(i, combos, how, w_first=2.0):
    """2軸目の第1候補に w_first 倍の重みを置く（1軸固定+2軸目2点 の強弱）。"""
    a1, bs = axis2_cands(i, how)
    w = []
    for c in combos:
        b = bs[0] if bs[0] in c and a1 in c else None
        w.append(w_first if b is not None else 1.0)
    n_units = C.BUDGET // C.UNIT
    if n_units < len(combos):
        return None
    tot = sum(w)
    units = [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / w[k])
        units[j] += 1
    return {c: u * C.UNIT for c, u in zip(combos, units)}


def run_axis2(how, m, w_first, window, third="p3"):
    recs = []
    for i in C.select("E", window):
        i = int(i)
        combos = legs_axis2(i, how, m, third)
        if not combos:
            continue
        st = (C.trio_stakes(i, combos, tilt=True) if w_first == "tilt" else
              C.trio_stakes(i, combos, tilt=False) if w_first == "eq" else
              stakes_axis2(i, combos, how, w_first))
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=DATE[i], inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, n_days_all=NDAYS[window])


# ── 三連単 ──

def tf_top(i, k, po_min=0.0):
    p = Z["PROB"][i]
    po = Z["PO"][i]
    ok = [t for t in range(210) if np.isfinite(po[t]) and po[t] >= max(po_min, 1.0)]
    ok.sort(key=lambda t: -p[t])
    return [C.CANON[t] for t in ok[:k]]


def tf_ev(i, k, po_min=0.0):
    p = Z["PROB"][i]
    po = Z["PO"][i]
    ok = [t for t in range(210) if np.isfinite(po[t]) and po[t] >= max(po_min, 1.0)]
    ok.sort(key=lambda t: -(p[t] * po[t]))
    return [C.CANON[t] for t in ok[:k]]


def tf_form(i, firsts, seconds, thirds, k=None):
    """1着=firsts / 2着=seconds / 3着=thirds（車番リスト）から重複なしの順序対。"""
    out = []
    for a in firsts:
        for b in seconds:
            if b == a:
                continue
            for c in thirds:
                if c in (a, b):
                    continue
                if (a, b, c) not in out:
                    out.append((a, b, c))
    if k:
        p = Z["PROB"][i]
        out.sort(key=lambda t: -p[C.CIDX[t]])
        out = out[:k]
    return out


# ───────────────────────── セクション ─────────────────────────

def sec_base():
    print("■ 型E の素の姿（買い方に依らない）")
    for w in WINDOWS:
        idx = C.select("E", w)
        n = len(idx)
        both = third_pos = 0
        pays, dist3 = [], Counter()
        for i in idx:
            i = int(i)
            o = p3_order(i)
            win = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            if {o[0], o[1]} <= win:
                both += 1
                (third,) = win - {o[0], o[1]}
                r = o.index(third) + 1
                dist3[r] += 1
                if r in (3, 4):
                    third_pos += 1
            pays.append(float(Z["TRIO_PAY"][i]))
        print(f"  [{w}] n={n}  日={C.days_of(idx)}  件/日={n/NDAYS[w]:.2f}")
        print(f"     二軸そろい {both/n*100:.1f}%   3着が全体3,4番手 "
              f"{third_pos/max(both,1)*100:.1f}%")
        print("     二軸そろい時の3着の全体順位: " +
              " ".join(f"{r}位{dist3[r]/max(both,1)*100:.0f}%" for r in range(3, 8)))
        ps = sorted(pays)
        print(f"     確定三連複オッズ  p25 {ps[len(ps)//4]:.1f} / 中央 "
              f"{median(ps):.1f} / p75 {ps[len(ps)*3//4]:.1f} / p95 "
              f"{ps[int(len(ps)*.95)]:.1f} 倍")
        # 相手位置ごとの単独の当たりやすさ（軸2車そろい前提でない生の率）
        hit = Counter()
        for i in idx:
            i = int(i)
            o = p3_order(i)
            win = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            for r in range(1, 8):
                if o[r - 1] in win:
                    hit[r] += 1
        print("     各順位の3着内率: " +
              " ".join(f"{r}位{hit[r]/n*100:.0f}%" for r in range(1, 8)))


def sec_example():
    print("■ ベースラインを1件、実データで目視確認する（作法）")
    i = int(C.select("E", "confirm")[0])
    o = p3_order(i)
    print(f"  {Z['KEY'][i]} {Z['DATE'][i]} {Z['RTYPE'][i]}  p3順={o}")
    print(f"  p3={np.round(Z['P3'][i], 3).tolist()}  axis_sum={Z['AXIS_SUM'][i]:.3f}"
          f"  arare={Z['ARARE'][i]}  agree={Z['AGREE'][i]}")
    for name, combos in (("総流し5点", legs_by_p3(i, [3, 4, 5, 6, 7])),
                         ("p3順3点", legs_by_p3(i, [3, 4, 5])),
                         ("同時確率2点", legs_by_joint(i, 2))):
        for tilt in (True, False):
            st = C.trio_stakes(i, combos, tilt=tilt)
            if st is None:
                continue
            po = {c: float(Z["TRIO_PO"][i][C.C3IDX[c]]) for c in st}
            g = C.trio_gate(i, st)
            print(f"  {name} tilt={tilt} gate={'通' if g else '×'}  " +
                  " / ".join(f"{sorted(c)}:{st[c]:,}円x{po[c]:.1f}={st[c]*po[c]:,.0f}"
                             for c in st))


def sec_trio():
    print("■ 三連複 軸2車（p3上位2）+ 相手k点")
    print(C.HEAD)
    for k in range(1, 6):
        show(f"p3順 上から{k}点", lambda i, k=k: legs_by_p3(i, list(range(3, 3 + k))))
    print("  ── 相手1点の位置ごと（どの順位を買うのが良いか）──")
    for r in range(3, 8):
        show(f"相手=全体{r}番手のみ", lambda i, r=r: legs_by_p3(i, [r]))
    print("  ── 相手の選び方を変える ──")
    for k in (1, 2, 3):
        show(f"同時確率 上位{k}点", lambda i, k=k: legs_by_joint(i, k))
    for k in (1, 2, 3):
        show(f"同時確率 下位{k}点", lambda i, k=k: legs_by_joint(i, k, desc=False))
    for k in (1, 2, 3):
        show(f"予測オッズ高い順{k}点", lambda i, k=k: legs_by_po(i, k, cheap=False))
    print("  ── 配分（均等 vs ダッチング）──")
    for k in (2, 3, 5):
        show(f"p3順{k}点 均等", lambda i, k=k: legs_by_p3(i, list(range(3, 3 + k))),
             tilt=False)
    for r in (5, 6):
        show(f"相手={r}番手+{r+1}番手 均等",
             lambda i, r=r: legs_by_p3(i, [r, r + 1]), tilt=False)


def sec_axis2():
    print("■ 1軸固定 + 2軸目2点 + 3着流し")
    print(C.HEAD)
    for how in ("p3", "joint", "line", "pw", "p3_23_24"):
        for m in (1, 2):
            for w in ("eq", "tilt", 2.0, 3.0):
                lab = {"eq": "均等", "tilt": "ダッチ"}.get(w, f"第1軸2×{w}")
                for win in WINDOWS:
                    s = run_axis2(how, m, w, win)
                    print(C.line(f"{how} m={m} {lab} [{win[:3]}]", s))
    print("  ── 3着を同時確率で選ぶ（m=2 のみ）──")
    for how in ("p3", "joint"):
        for w in ("eq", "tilt", 2.0):
            lab = {"eq": "均等", "tilt": "ダッチ"}.get(w, f"第1軸2×{w}")
            for win in WINDOWS:
                s = run_axis2(how, 2, w, win, third="joint")
                print(C.line(f"{how} m=2 3着joint {lab} [{win[:3]}]", s))


def sec_tf():
    print("■ 三連単")
    print(C.HEAD)
    for k in (1, 2, 3, 5, 8, 10):
        show(f"確率上位{k}点", lambda i, k=k: tf_top(i, k), kind="tf")
    for k in (3, 5, 10):
        show(f"EV上位{k}点", lambda i, k=k: tf_ev(i, k), kind="tf")
    for po_min in (20, 30, 50, 100):
        for k in (3, 5, 10):
            show(f"予測{po_min}倍+ 確率上位{k}点",
                 lambda i, k=k, p=po_min: tf_top(i, k, p), kind="tf")
    print("  ── フォーメーション ──")
    show("1着=p3_1 x 2着=p3_2,3 x 3着流し",
         lambda i: tf_form(i, [p3_order(i)[0]], p3_order(i)[1:3], p3_order(i)[1:]),
         kind="tf")
    show("1,2着=p3_1,2の順不同 x 3着流し",
         lambda i: tf_form(i, p3_order(i)[:2], p3_order(i)[:2], p3_order(i)[2:]),
         kind="tf")
    show("1着=pw_1 x 2着=p3上位2 x 3着流し",
         lambda i: tf_form(i, [C.pw_order(i)[0]], p3_order(i)[:2], p3_order(i)),
         kind="tf")


def sec_agree():
    print("■ 印一致 / 不一致 での分割（代表腕のみ）")
    arms = {
        "総流し5点": lambda i: legs_by_p3(i, [3, 4, 5, 6, 7]),
        "p3順3点": lambda i: legs_by_p3(i, [3, 4, 5]),
        "同時確率2点": lambda i: legs_by_joint(i, 2),
        "相手=5番手1点": lambda i: legs_by_p3(i, [5]),
    }
    print(C.HEAD)
    for name, build in arms.items():
        for w in WINDOWS:
            for ag in (True, False):
                recs = []
                for i in C.select("E", w, agree=ag):
                    i = int(i)
                    combos = build(i)
                    st = C.trio_stakes(i, combos, tilt=True)
                    if st is None or not C.trio_gate(i, st):
                        continue
                    inv, pay, mean = C.trio_result(i, st)
                    recs.append(dict(date=DATE[i], inv=inv, pay=pay, mean=mean,
                                     k=len(combos)))
                s = C.summarize(recs, n_days_all=NDAYS[w])
                print(C.line(f"{name} 印{'一致' if ag else '不一致'}[{w[:3]}]", s))


SECTIONS = dict(base=sec_base, example=sec_example, trio=sec_trio,
                axis2=sec_axis2, tf=sec_tf, agree=sec_agree)



# ───────────────────────── 追加スキャン ─────────────────────────

def legs_p3_floor(i, k, floor):
    """軸=p3上位2。相手は p3 順だが**予測三連複オッズ >= floor** の車だけ採る。"""
    o = p3_order(i)
    a1, a2 = o[0], o[1]
    po = Z["TRIO_PO"][i]
    out = []
    for x in o[2:]:
        v = float(po[C.C3IDX[frozenset((a1, a2, x))]])
        if np.isfinite(v) and v >= floor:
            out.append(frozenset((a1, a2, x)))
        if len(out) >= k:
            break
    return out


def sec_cover():
    """軸の構造（1軸固定+2軸目2点 が三連複で意味を持つか）。"""
    print("■ 軸の構造")
    for w in WINDOWS:
        idx = C.select("E", w)
        n = len(idx)
        c = Counter()
        for i in idx:
            i = int(i)
            o = p3_order(i)
            win = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            c["a1"] += o[0] in win
            c["a1a2"] += {o[0], o[1]} <= win
            c["a1_and_23"] += o[0] in win and (o[1] in win or o[2] in win)
            c["a1_and_234"] += o[0] in win and len({o[1], o[2], o[3]} & win) >= 1
            c["2of_top3"] += len({o[0], o[1], o[2]} & win) >= 2
            c["2of_top4"] += len({o[0], o[1], o[2], o[3]} & win) >= 2
        print(f"  [{w}] n={n}  軸1が3着内 {c['a1']/n*100:.1f}% / 二軸そろい "
              f"{c['a1a2']/n*100:.1f}% / 軸1∧(2位or3位) {c['a1_and_23']/n*100:.1f}%"
              f" / 軸1∧(2〜4位のどれか) {c['a1_and_234']/n*100:.1f}%")
        print(f"        上位3車から2車 {c['2of_top3']/n*100:.1f}% / "
              f"上位4車から2車 {c['2of_top4']/n*100:.1f}%")
    print("  レース種別（既存 7T1/7T3 は決勝系のみなので重なりを見る）")
    for w in WINDOWS:
        idx = C.select("E", w)
        rt = Counter(str(Z["RTYPE"][i]) for i in idx)
        tot = len(idx)
        print(f"  [{w}] " + " ".join(f"{k}:{v/tot*100:.0f}%"
                                    for k, v in rt.most_common(8)))


def sec_trio2():
    print("■ 三連複 追加スキャン（均等・相手の組み合わせ / オッズ下限）")
    print(C.HEAD)
    print("  ── 2点 均等（ガミは構造的に出ない: 均等k点のガミ ⟺ 確定 < k 倍）──")
    for pair in ((3, 4), (3, 5), (3, 6), (3, 7), (4, 5), (4, 6), (5, 7)):
        show(f"相手={pair[0]},{pair[1]}番手 均等",
             lambda i, p=pair: legs_by_p3(i, list(p)), tilt=False)
    print("  ── 3点 均等 + 予測オッズ下限（下限で ガミ を潰す）──")
    for fl in (3.0, 4.0, 5.0):
        for k in (2, 3):
            show(f"p3順{k}点 均等 予測>={fl}倍",
                 lambda i, k=k, f=fl: legs_p3_floor(i, k, f), tilt=False)
    print("  ── 1軸固定+2軸目2点 を均等で（点数を揃えた直接対決）──")
    for how in ("p3", "line", "joint"):
        for win in WINDOWS:
            print(C.line(f"{how} m=2 均等 [{win[:3]}]", run_axis2(how, 2, "eq", win)))
    print("  ── 4点・5点 均等（点数を増やす側）──")
    for k in (4, 5):
        show(f"p3順{k}点 均等", lambda i, k=k: legs_by_p3(i, list(range(3, 3 + k))),
             tilt=False)
    for k in (4, 5):
        show(f"p3順{k}点 均等 予測>={k}倍",
             lambda i, k=k: legs_p3_floor(i, k, float(k)), tilt=False)


def sec_tf2():
    print("■ 三連単 追加スキャン（予測オッズ下限 × 点数）")
    print(C.HEAD)
    for fl in (15, 20, 25, 30, 40, 60):
        for k in (5, 8, 10, 12, 15):
            show(f"予測{fl}倍+ 確率上位{k}点",
                 lambda i, k=k, f=fl: tf_top(i, k, f), kind="tf")
    print("  ── 並べ替えを EV にすると（同じ母集団・同じ点数）──")
    for fl in (20, 30):
        for k in (10,):
            show(f"予測{fl}倍+ EV上位{k}点",
                 lambda i, k=k, f=fl: tf_ev(i, k, f), kind="tf")
    print("  ── 1着=軸1 × 2着=2軸目2点 × 3着流し（予測下限つき）──")
    for fl in (20, 30):
        for k in (8, 10):
            show(f"1着=p3_1x2着=p3_2,3 予測{fl}倍+ 上位{k}点",
                 lambda i, k=k, f=fl: [c for c in tf_top(i, 210, f)
                                       if c[0] == p3_order(i)[0]
                                       and c[1] in p3_order(i)[1:3]][:k], kind="tf")
    print("  ── 1,2着が p3 上位3車のどれか（順不同）×予測下限 ──")
    for fl in (20, 30):
        for k in (8, 10, 12):
            show(f"1,2着=p3上位3 予測{fl}倍+ 上位{k}点",
                 lambda i, k=k, f=fl: [c for c in tf_top(i, 210, f)
                                       if c[0] in p3_order(i)[:3]
                                       and c[1] in p3_order(i)[:3]][:k], kind="tf")


SECTIONS.update(cover=sec_cover, trio2=sec_trio2, tf2=sec_tf2)




# ───────────────────────── 微調整・頑健性 ─────────────────────────

SUBW = {"2024H2": ("2024-07-01", "2024-12-31"), "2025H1": ("2025-01-01", "2025-06-30"),
        "2025H2": ("2025-07-01", "2025-12-31"), "2026(確認)": ("2026-01-01", "2029-12-31")}


def sub_idx(lo, hi, agree=None):
    idx = C.select("E", "all", agree=agree)
    return np.array([i for i in idx if lo <= DATE[i] <= hi])


def run_sub(build, idx, kind="trio", tilt=True, lo=None, hi=None):
    recs = []
    ad = DATE[C.select(None, "all")]
    lo = lo or min(DATE[idx]); hi = hi or max(DATE[idx])
    allday = len({d for d in ad if lo <= d <= hi})
    for i in idx:
        i = int(i)
        combos = build(i)
        if not combos:
            continue
        if kind == "trio":
            st = C.trio_stakes(i, combos, tilt=tilt)
            if st is None or not C.trio_gate(i, st):
                continue
            inv, pay, mean = C.trio_result(i, st)
        else:
            st = C.tf_stakes(i, combos)
            if not C.tf_gate(i, st):
                continue
            inv, pay = C.tf_result(i, st)
            mean = sum(st[c] * float(Z["PO"][i][C.CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=DATE[i], inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, n_days_all=allday)


def sec_fine():
    print("■ 三連単の微調整（下限 × 点数）")
    print(C.HEAD)
    for fl in (25, 28, 30, 32, 35):
        for k in (12, 14, 15, 16, 18, 20):
            show(f"予測{fl}倍+ 上位{k}点", lambda i, k=k, f=fl: tf_top(i, k, f), kind="tf")


def sec_robust():
    print("■ 頑健性（4つの窓）— 採用候補の腕だけ")
    arms = [
        ("三連単 予測30倍+ 10点", lambda i: tf_top(i, 10, 30), "tf", True),
        ("三連単 予測30倍+ 14点", lambda i: tf_top(i, 14, 30), "tf", True),
        ("三連単 予測30倍+ 20点", lambda i: tf_top(i, 20, 30), "tf", True),
        ("三連複 相手3,5番手 均等", lambda i: legs_by_p3(i, [3, 5]), "trio", False),
        ("三連複 相手4,5番手 均等", lambda i: legs_by_p3(i, [4, 5]), "trio", False),
        ("三連複 p3順1点", lambda i: legs_by_p3(i, [3]), "trio", True),
        ("三連複 p3順3点 ダッチ", lambda i: legs_by_p3(i, [3, 4, 5]), "trio", True),
        ("三連複 p3順2点 均等 予測>=4倍", lambda i: legs_p3_floor(i, 2, 4.0), "trio", False),
        ("三連複 p3順3点 均等 予測>=5倍", lambda i: legs_p3_floor(i, 3, 5.0), "trio", False),
    ]
    for name, build, kind, tilt in arms:
        print(f"  {name}")
        print(C.HEAD)
        for wn, (lo, hi) in SUBW.items():
            idx = sub_idx(lo, hi)
            if len(idx) == 0:
                continue
            print(C.line(f"  {wn} (n={len(idx)})", run_sub(build, idx, kind, tilt, lo, hi)))


def sec_agree2():
    print("■ 印一致/不一致（採用候補の腕）")
    arms = [("三連単 予測30倍+ 14点", lambda i: tf_top(i, 14, 30), "tf", True),
            ("三連単 予測30倍+ 10点", lambda i: tf_top(i, 10, 30), "tf", True),
            ("三連複 相手3,5番手 均等", lambda i: legs_by_p3(i, [3, 5]), "trio", False)]
    print(C.HEAD)
    for name, build, kind, tilt in arms:
        for wn, (lo, hi) in (("explore", ("2024-07-01", "2025-12-31")),
                             ("confirm", ("2026-01-01", "2029-12-31"))):
            for ag in (True, False):
                idx = sub_idx(lo, hi, agree=ag)
                s = run_sub(build, idx, kind, tilt, lo, hi)
                print(C.line(f"{name[:14]} {'一致' if ag else '不一致'}[{wn[:3]}]", s))


def sec_overlap():
    print("■ 既存商品との重なり（型E の中身）")
    for w in WINDOWS:
        idx = C.select("E", w)
        n = len(idx)
        fin = sum(1 for i in idx if str(Z["RTYPE"][i]) in ("決勝", "チャレンジ決勝"))
        ag = sum(1 for i in idx if Z["AGREE"][i])
        print(f"  [{w}] n={n}  決勝系 {fin/n*100:.1f}%（7T1/7T3 の母集団）"
              f" / 印一致 {ag/n*100:.1f}%（7C/7S/7M1 の印不一致ゲートが落とす側）")


SECTIONS.update(fine=sec_fine, robust=sec_robust, agree2=sec_agree2, overlap=sec_overlap)




def sec_xtype():
    """同じ腕を他の型にも当てる（型Eに固有か・広い商品にすべきかの判別）。"""
    print("■ 三連単 予測30倍+ 14点 を型別に当てる")
    print(C.HEAD)
    for t in ("A", "B", "C", "D", "E", "F", None):
        for w in WINDOWS:
            idx = C.select(t, w)
            s = run_sub(lambda i: tf_top(i, 14, 30), idx, "tf", True,
                        *(("2024-07-01", "2025-12-31") if w == "explore"
                          else ("2026-01-01", "2029-12-31")))
            print(C.line(f"型{t or '全'} [{w[:3]}] n={len(idx)}", s))


SECTIONS.update(xtype=sec_xtype)




def sec_xtype2():
    print("■ 三連複の腕を型別に当てる（型Eが固有かどうか）")
    arms = [("p3順1点", lambda i: legs_by_p3(i, [3]), True),
            ("相手3,5番手 均等", lambda i: legs_by_p3(i, [3, 5]), False),
            ("p3順3点 ダッチ", lambda i: legs_by_p3(i, [3, 4, 5]), True),
            ("p3順2点 均等 予測>=4倍", lambda i: legs_p3_floor(i, 2, 4.0), False)]
    for name, build, tilt in arms:
        print(f"  {name}")
        print(C.HEAD)
        for t in ("A", "C", "D", "E", "F"):
            for w in WINDOWS:
                idx = C.select(t, w)
                s = run_sub(build, idx, "trio", tilt,
                            *(("2024-07-01", "2025-12-31") if w == "explore"
                              else ("2026-01-01", "2029-12-31")))
                print(C.line(f"  型{t} [{w[:3]}] n={len(idx)}", s))


SECTIONS.update(xtype2=sec_xtype2)




def sec_sel():
    """型Eの中をさらに選別できるか（採用候補の腕を固定して母集団だけ動かす）。"""
    print("■ 型E内のレース選別（三連単 予測30倍+ 14点 を固定）")
    build = lambda i: tf_top(i, 14, 30)  # noqa: E731
    print(C.HEAD)
    cuts = {}
    for w in WINDOWS:
        idx = C.select("E", w)
        lo, hi = (("2024-07-01", "2025-12-31") if w == "explore"
                  else ("2026-01-01", "2029-12-31"))
        # AXIS_SUM の中央で二分
        med = float(np.median(Z["AXIS_SUM"][idx]))
        cuts[w] = (lo, hi, med)
        groups = {
            f"axis_sum<{med:.2f}(より混戦)": [i for i in idx if Z["AXIS_SUM"][i] < med],
            f"axis_sum>={med:.2f}": [i for i in idx if Z["AXIS_SUM"][i] >= med],
            "ライン数<=3": [i for i in idx if Z["A_n_lines"][i][0] <= 3],
            "ライン数>=4": [i for i in idx if Z["A_n_lines"][i][0] >= 4],
            "決勝系": [i for i in idx if str(Z["RTYPE"][i]) in ("決勝", "チャレンジ決勝",
                                                            "準決勝", "特選", "選抜")],
            "非決勝系": [i for i in idx if str(Z["RTYPE"][i]) not in
                      ("決勝", "チャレンジ決勝", "準決勝", "特選", "選抜")],
            "GAP上位半分": [i for i in idx if Z["GAP"][i] >= float(np.median(Z["GAP"][idx]))],
            "GAP下位半分": [i for i in idx if Z["GAP"][i] < float(np.median(Z["GAP"][idx]))],
        }
        for g, sub in groups.items():
            if len(sub) < 100:
                continue
            print(C.line(f"{g} [{w[:3]}] n={len(sub)}",
                         run_sub(build, np.array(sub), "tf", True, lo, hi)))


SECTIONS.update(sel=sec_sel)




def sec_final():
    print("■ 採用候補と主要な棄却腕（両窓・入稿ゲート通過後）")
    arms = [
        ("★三連単 予測30倍+14点", lambda i: tf_top(i, 14, 30), "tf", True),
        ("三連単 予測30倍+10点", lambda i: tf_top(i, 10, 30), "tf", True),
        ("三連単 予測30倍+20点", lambda i: tf_top(i, 20, 30), "tf", True),
        ("三連単 確率上位5点(下限なし)", lambda i: tf_top(i, 5), "tf", True),
        ("三連単 EV上位10点", lambda i: tf_ev(i, 10), "tf", True),
        ("三連複 総流し5点ダッチ", lambda i: legs_by_p3(i, [3, 4, 5, 6, 7]), "trio", True),
        ("三連複 p3順3点ダッチ", lambda i: legs_by_p3(i, [3, 4, 5]), "trio", True),
        ("三連複 p3順1点", lambda i: legs_by_p3(i, [3]), "trio", True),
        ("三連複 2点均等 予測>=4倍", lambda i: legs_p3_floor(i, 2, 4.0), "trio", False),
        ("三連複 3点均等 予測>=5倍", lambda i: legs_p3_floor(i, 3, 5.0), "trio", False),
        ("三連複 相手3,5番手均等", lambda i: legs_by_p3(i, [3, 5]), "trio", False),
        ("三連複 相手4,5番手均等", lambda i: legs_by_p3(i, [4, 5]), "trio", False),
        ("三連複 5点均等", lambda i: legs_by_p3(i, [3, 4, 5, 6, 7]), "trio", False),
        ("三連複 同時確率2点ダッチ", lambda i: legs_by_joint(i, 2), "trio", True),
    ]
    print(C.HEAD)
    for name, build, kind, tilt in arms:
        for w in WINDOWS:
            idx = C.select("E", w)
            lo, hi = (("2024-07-01", "2025-12-31") if w == "explore"
                      else ("2026-01-01", "2029-12-31"))
            print(C.line(f"{name}[{w[:3]}]", run_sub(build, idx, kind, tilt, lo, hi)))


SECTIONS.update(final=sec_final)


if __name__ == "__main__":
    for s in sys.argv[1:] or ["base"]:
        SECTIONS[s]()
        print()
