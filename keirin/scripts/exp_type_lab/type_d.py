#!/usr/bin/env python3
"""型D「混戦・軸あり」の商品設計を網羅的に探す（2026-08-27）。

型D = axis_sum(vintage p3 上位2車の合計) < 1.44 ∧ 荒れ度 <= -1
      探索 1,291R/475日 ・ 確認 744R/202日（7車全体で 3.5件/日＝最小の型）

作法（CLAUDE.md「測る前に本番コードを読む」）
  - 入稿ゲート（MIN_POINT_ODDS=2.0 / MIN_MEAN_PAYOUT=20,000）を**通してから**比べる
  - 判断は 件/日・表示的中(ガミ除く)・払戻中央・2倍+/日・ガミ率。ROI は参考のみ
  - 探索窓のオッズは in-sample（odds_tf_n7 の train_end=2025-12-31）→ **確認窓で判断**

使い方: .venv/bin/python scripts/exp_type_lab/type_d.py [section]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# 🔴 NpzFile は要素アクセスのたびに配列全体を伸長する（PROB は 30MB）。
#    必ず一度だけメモリへ展開してから使うこと（放置すると1腕で数分かかる）。
C._Z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
Z = C.board()
TYPE = "D"


# ───────────────────────── 補助 ─────────────────────────

_FOLD = np.zeros((210, 35), np.float64)
for _t, _c in enumerate(C.CANON):
    _FOLD[_t, C.C3IDX[frozenset(_c)]] = 1.0
_TP: dict[int, np.ndarray] = {}
_ORD: dict[int, list[int]] = {}


def trio_prob(i: int) -> np.ndarray:
    """三連単の位置別合成PL確率(210)を三連複35点へ畳む（キャッシュ）。"""
    r = _TP.get(i)
    if r is None:
        r = _TP[i] = Z["PROB"][i].astype(np.float64) @ _FOLD
    return r


def po3(i: int) -> np.ndarray:
    return Z["TRIO_PO"][i].astype(np.float64)


def marks(i: int) -> dict[int, float]:
    return {c: Z["A_prediction_mark"][i][c - 1] for c in range(1, 8)}


def line_of(i: int) -> dict[int, str]:
    return {c: Z["LG"][i][c - 1] for c in range(1, 8)}


def run(idx: np.ndarray, build, tilt: bool = True, kind: str = "trio") -> list[dict]:
    """build(i) -> 買い目list を入稿ゲートに通して recs を作る。"""
    recs = []
    for i in idx:
        combos = build(int(i))
        if not combos:
            continue
        if kind == "trio":
            combos = list(dict.fromkeys(frozenset(c) for c in combos))
            st = C.trio_stakes(int(i), combos, tilt=tilt)
            if st is None or not C.trio_gate(int(i), st):
                continue
            inv, pay, mean = C.trio_result(int(i), st)
            recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(st)))
        else:
            combos = list(dict.fromkeys(tuple(c) for c in combos))
            st = C.tf_stakes(int(i), combos)
            if not C.tf_gate(int(i), st):
                continue
            inv, pay = C.tf_result(int(i), st)
            mean = sum(st[c] * float(Z["PO"][i][C.CIDX[c]]) for c in st) / len(st)
            recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(st)))
    return recs


def show(title: str, arms: list[tuple[str, object, bool, str]], agree=None):
    """arms: [(名前, build, tilt, kind)]"""
    print(f"\n### {title}")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w, agree=agree)
        nd = C.days_of(idx)
        print(f"\n[{w}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for name, build, tilt, kind in arms:
            s = C.summarize(run(idx, build, tilt=tilt, kind=kind), n_days_all=nd)
            print(C.line(name, s))


# ═══════════ 1. 三連複 軸2車（p3上位2）+ 相手k点 ═══════════

def _axes(i):
    o = _ORD.get(i)
    if o is None:
        o = _ORD[i] = C.p3_order(i)
    return o[0], o[1], o[2:]


def partner_order(i, how: str) -> list[int]:
    a1, a2, rest = _axes(i)
    if how == "p3":                      # 全体3番手→7番手
        return rest
    if how == "p3rev":
        return rest[::-1]
    tp, po = trio_prob(i), po3(i)
    if how == "joint":
        return sorted(rest, key=lambda x: -tp[C.C3IDX[frozenset((a1, a2, x))]])
    if how == "ev":
        return sorted(rest, key=lambda x: -(tp[C.C3IDX[frozenset((a1, a2, x))]]
                                            * po[C.C3IDX[frozenset((a1, a2, x))]]))
    if how == "po":                      # 予測オッズ高い順（穴寄り）
        return sorted(rest, key=lambda x: -po[C.C3IDX[frozenset((a1, a2, x))]])
    if how == "nomark":                  # 公式印の付いていない車を先に
        m = marks(i)
        return sorted(rest, key=lambda x: (m[x] if m[x] > 0 else 9))
    raise ValueError(how)


def mk_trio_k(how: str, k: int):
    def build(i):
        a1, a2, _ = _axes(i)
        return [(a1, a2, x) for x in partner_order(i, how)[:k]]
    return build


def mk_trio_rank(r: int):
    """相手を「全体r番手」1点だけ（§29 の順位固定と同じ形）。"""
    def build(i):
        a1, a2, rest = _axes(i)
        return [(a1, a2, rest[r - 3])] if len(rest) >= r - 2 else []
    return build


def section1():
    arms = []
    for how in ("p3", "joint", "ev", "po", "nomark", "p3rev"):
        for k in (1, 2, 3, 4, 5):
            arms.append((f"軸2+{how} k={k}", mk_trio_k(how, k), True, "trio"))
    show("1. 三連複 軸2車 + 相手k点（ダッチング）", arms)


def section1b():
    arms = [(f"相手=全体{r}番手 1点", mk_trio_rank(r), True, "trio") for r in (3, 4, 5, 6, 7)]
    arms += [("軸2+p3 k=3 均等", mk_trio_k("p3", 3), False, "trio"),
             ("軸2+p3 k=3 ダッチ", mk_trio_k("p3", 3), True, "trio"),
             ("軸2+joint k=2 均等", mk_trio_k("joint", 2), False, "trio"),
             ("軸2+joint k=2 ダッチ", mk_trio_k("joint", 2), True, "trio"),
             ("軸2+全流し5点 均等", mk_trio_k("p3", 5), False, "trio"),
             ("軸2+全流し5点 ダッチ", mk_trio_k("p3", 5), True, "trio")]
    show("1b. 相手1点固定 と 均等/ダッチの比較", arms)


# ═══════════ 2. 本命候補: 軸1固定 + 2軸目2点 + 3着流し ═══════════

def stakes_w(i: int, combos: list[frozenset], w: list[float]) -> dict | None:
    """任意の重み w で 10,000円を配分（100円単位）。"""
    po = [Z["TRIO_PO"][i][C.C3IDX[c]] for c in combos]
    if any((not np.isfinite(x)) or x <= 0 for x in po):
        return None
    n_units = C.BUDGET // C.UNIT
    if n_units < len(combos) or sum(w) <= 0:
        return None
    tot = sum(w)
    units = [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    return {c: u * C.UNIT for c, u in zip(combos, units)}


def pair_joint(i: int, a: int, b: int) -> float:
    """a と b が2車とも3着内に入る確率（三連複35点の周辺化）。"""
    tp = trio_prob(i)
    return sum(tp[C.C3IDX[frozenset((a, b, x))]] for x in range(1, 8) if x not in (a, b))


def second_axis_pair(i: int, how: str) -> list[int]:
    """2軸目の候補2車。"""
    a1, a2, rest = _axes(i)
    if how == "p3_23":                   # p3 の2位・3位
        return [a2, rest[0]]
    if how == "joint":                   # 軸1との2車同時3着内が高い2車
        cand = sorted((c for c in range(1, 8) if c != a1),
                      key=lambda x: -pair_joint(i, a1, x))
        return cand[:2]
    if how == "line":                    # p3 2位 + 軸1と同ラインの最上位（無ければ3位）
        lg = line_of(i)
        same = [c for c in [a2] + rest if lg[c] == lg[a1] and lg[a1] not in ("", "0")]
        b2 = next((c for c in same if c != a2), rest[0])
        return [a2, b2]
    if how == "mark":                    # p3 2位 + 公式印◎○のうち軸に居ない車
        m = marks(i)
        cand = [c for c in [a2] + rest if m[c] in (1, 2) and c != a1]
        b2 = next((c for c in cand if c != a2), rest[0])
        return [a2, b2]
    if how == "p3_24":
        return [a2, rest[1]]
    raise ValueError(how)


def third_order(i: int, a1: int, b: int, how: str) -> list[int]:
    tp, po = trio_prob(i), po3(i)
    rest = [c for c in range(1, 8) if c not in (a1, b)]
    if how == "joint":
        return sorted(rest, key=lambda x: -tp[C.C3IDX[frozenset((a1, b, x))]])
    if how == "ev":
        return sorted(rest, key=lambda x: -(tp[C.C3IDX[frozenset((a1, b, x))]]
                                            * po[C.C3IDX[frozenset((a1, b, x))]]))
    if how == "po":
        return sorted(rest, key=lambda x: -po[C.C3IDX[frozenset((a1, b, x))]])
    if how == "p3":
        p3 = Z["P3"][i]
        return sorted(rest, key=lambda x: -p3[x - 1])
    raise ValueError(how)


def build_two_axis(i: int, pair_how: str, third_how: str, k: int):
    """軸1固定 × 2軸目2点 × 3着k点。戻り値 (combos, 2軸目の並び)"""
    a1 = _axes(i)[0]
    b1, b2 = second_axis_pair(i, pair_how)
    combos, grp = [], []
    for gi, b in enumerate((b1, b2)):
        for x in third_order(i, a1, b, third_how)[:k]:
            c = frozenset((a1, b, x))
            if c in combos:
                continue
            combos.append(c)
            grp.append(gi)
    return combos, grp, (b1, b2)


def mk_two_axis(pair_how: str, third_how: str, k: int, mode: str = "dutch"):
    """mode: dutch(1/予測オッズ) / equal / grp(2軸目の確からしさで群を傾ける)"""
    def run_one(i):
        combos, grp, (b1, b2) = build_two_axis(i, pair_how, third_how, k)
        if not combos:
            return None
        po = [float(Z["TRIO_PO"][i][C.C3IDX[c]]) for c in combos]
        if any((not np.isfinite(x)) or x <= 0 for x in po):
            return None
        if mode == "dutch":
            w = [1.0 / x for x in po]
        elif mode == "equal":
            w = [1.0] * len(combos)
        else:                               # grp: 群比 = pair_joint 比 × ダッチ
            j1, j2 = pair_joint(i, _axes(i)[0], b1), pair_joint(i, _axes(i)[0], b2)
            s = j1 + j2
            gw = [j1 / s, j2 / s] if s > 0 else [0.5, 0.5]
            w = [gw[g] / x for g, x in zip(grp, po)]
        return stakes_w(i, combos, w)
    return run_one


def run2(idx, stake_fn):
    recs = []
    for i in idx:
        i = int(i)
        st = stake_fn(i)
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(st)))
    return recs


def show2(title, arms, agree=None):
    print(f"\n### {title}")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w, agree=agree)
        nd = C.days_of(idx)
        print(f"\n[{w}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for name, fn in arms:
            print(C.line(name, C.summarize(run2(idx, fn), n_days_all=nd)))


def section2():
    arms = []
    for ph in ("p3_23", "joint", "line", "mark", "p3_24"):
        for k in (1, 2):
            arms.append((f"軸1×2軸目[{ph}]×3着{k}", mk_two_axis(ph, "joint", k)))
    show2("2. 軸1固定 + 2軸目2点 + 3着流し（3着=joint順・ダッチング）", arms)


def section2b():
    arms = []
    for th in ("joint", "ev", "po", "p3"):
        for k in (1, 2, 3):
            arms.append((f"3着={th} k={k}", mk_two_axis("p3_23", th, k)))
    show2("2b. 3着流しの選び方と点数（2軸目=p3 2位3位・ダッチング）", arms)


def section2c():
    arms = []
    for md in ("dutch", "equal", "grp"):
        for k in (1, 2):
            arms.append((f"[{md}] 3着{k}点", mk_two_axis("p3_23", "joint", k, md)))
    for md in ("dutch", "equal", "grp"):
        arms.append((f"[{md}] joint対×3着2点", mk_two_axis("joint", "joint", 2, md)))
    show2("2c. 賭け金の強弱（均等 / ダッチ / 2軸目の確からしさで傾ける）", arms)


# ═══════════ 3. 三連単 ═══════════

def tf_pool(i: int, how: str, k: int, po_min: float = 0.0, po_max: float = 1e9):
    """三連単210点から k 点。how: prob(確率順) / ev(確率×予測オッズ)"""
    p, po = Z["PROB"][i].astype(np.float64), Z["PO"][i].astype(np.float64)
    ok = np.isfinite(po) & (po >= po_min) & (po <= po_max) & (po > 0)
    cand = np.flatnonzero(ok)
    if len(cand) < k:
        return []
    key = p[cand] if how == "prob" else p[cand] * po[cand]
    top = cand[np.argsort(-key)[:k]]
    return [C.CANON[t] for t in top]


def tf_form(i: int, first: str, k3: int, k2: int = 1):
    """フォーメーション。first: a1(軸1が1着) / both(軸1軸2の順序2通り)"""
    a1, a2, _ = _axes(i)
    p, po = Z["PROB"][i].astype(np.float64), Z["PO"][i].astype(np.float64)
    heads = [(a1, a2)] if first == "a1" else [(a1, a2), (a2, a1)]
    out = []
    for h1, h2 in heads[:k2] if first == "a1" else heads:
        rest = [c for c in range(1, 8) if c not in (h1, h2)]
        rest.sort(key=lambda x: -p[C.CIDX[(h1, h2, x)]])
        out += [(h1, h2, x) for x in rest[:k3]]
    return [c for c in out if np.isfinite(po[C.CIDX[c]]) and po[C.CIDX[c]] > 0]


def mk_tf(fn):
    return lambda i: fn(i)


def section3():
    arms = []
    for k in (2, 3, 5, 8, 10):
        arms.append((f"確率上位{k}点", mk_tf(lambda i, k=k: tf_pool(i, "prob", k)), True, "tf"))
    for k in (3, 5, 10):
        arms.append((f"EV上位{k}点", mk_tf(lambda i, k=k: tf_pool(i, "ev", k)), True, "tf"))
    for lo in (20, 30, 50, 100):
        arms.append((f"予測{lo}倍+ 確率上位5点",
                     mk_tf(lambda i, lo=lo: tf_pool(i, "prob", 5, po_min=lo)), True, "tf"))
    show("3. 三連単（均等・1レース10,000円）", arms)


def section3b():
    arms = []
    for k3 in (1, 2, 3, 5):
        arms.append((f"F 軸1→軸2→流し{k3}",
                     mk_tf(lambda i, k=k3: tf_form(i, "a1", k)), True, "tf"))
        arms.append((f"F 軸2車を順不同→流し{k3}",
                     mk_tf(lambda i, k=k3: tf_form(i, "both", k)), True, "tf"))
    for lo in (30, 50):
        for k in (3, 10):
            arms.append((f"予測{lo}倍+ 確率上位{k}点",
                         mk_tf(lambda i, lo=lo, k=k: tf_pool(i, "prob", k, po_min=lo)), True, "tf"))
    show("3b. 三連単 フォーメーションと高配当帯", arms)


# ═══════════ 4. 印一致/不一致・軸の作り方・レース選別 ═══════════

BEST = [
    ("三連複 軸2+全流し5点", mk_trio_k("p3", 5), True, "trio"),
    ("三連複 軸2+po上位4点", mk_trio_k("po", 4), True, "trio"),
    ("三連複 軸2+ev上位4点", mk_trio_k("ev", 4), True, "trio"),
    ("三連複 軸2+joint2点", mk_trio_k("joint", 2), True, "trio"),
    ("三連複 軸2+p3 1点", mk_trio_k("p3", 1), True, "trio"),
    ("三連単 確率上位5点", mk_tf(lambda i: tf_pool(i, "prob", 5)), True, "tf"),
    ("三連単 軸2車順不同→流し1", mk_tf(lambda i: tf_form(i, "both", 1)), True, "tf"),
]


def section4():
    show("4. 印一致（モデル上位2車==◎○）のとき", BEST, agree=True)
    show("4b. 印不一致のとき（＝7M1 の母集団）", BEST, agree=False)


def mk_axis_variant(kind: str, k: int, how: str = "po"):
    """軸の作り方を替えて 軸2+相手 を組む。"""
    def build(i):
        if kind == "p3":
            o = C.p3_order(i)
        elif kind == "pw":
            o = C.pw_order(i)
        elif kind == "mix":                  # 軸1=pw最上位 / 軸2=p3最上位（軸1を除く）
            pw, p3 = C.pw_order(i), C.p3_order(i)
            a1 = pw[0]
            a2 = next(c for c in p3 if c != a1)
            o = [a1, a2] + [c for c in p3 if c not in (a1, a2)]
        elif kind == "mark":                 # 軸=公式印◎○（無ければ見送り）
            m = marks(i)
            mk = [c for c in range(1, 8) if m[c] in (1, 2)]
            if len(mk) != 2:
                return []
            o = mk + [c for c in C.p3_order(i) if c not in mk]
        else:
            raise ValueError(kind)
        a1, a2, rest = o[0], o[1], o[2:]
        po = po3(i)
        if how == "po":
            rest = sorted(rest, key=lambda x: -po[C.C3IDX[frozenset((a1, a2, x))]])
        return [(a1, a2, x) for x in rest[:k]]
    return build


def section5():
    arms = []
    for kind in ("p3", "pw", "mix", "mark"):
        for k in (4, 5):
            arms.append((f"軸={kind} +po上位{k}点", mk_axis_variant(kind, k), True, "trio"))
    show("5. 軸2車の作り方（三連複・相手は予測オッズ上位）", arms)


def section6():
    """レース選別（見送り）— 型Dの中をさらに切る。"""
    z = Z
    build = mk_trio_k("po", 4)
    print("\n### 6. 型Dの中でのレース選別（三連複 軸2+po上位4点）")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        print(f"\n[{w}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        segs = {
            "全部": np.ones(len(idx), bool),
            "印一致": z["AGREE"][idx],
            "印不一致": ~z["AGREE"][idx],
            "axis_sum<1.30": z["AXIS_SUM"][idx] < 1.30,
            "axis_sum>=1.30": z["AXIS_SUM"][idx] >= 1.30,
            "荒れ度<=-2": z["ARARE"][idx] <= -2,
            "荒れ度==-1": z["ARARE"][idx] == -1,
            "GAP上位半分": z["GAP"][idx] >= np.median(z["GAP"][idx]),
            "GAP下位半分": z["GAP"][idx] < np.median(z["GAP"][idx]),
        }
        rt = np.array([str(x) for x in z["RTYPE"][idx]])
        for nm in ("予選", "一般", "特選", "決勝", "準決勝", "選抜"):
            m = np.char.find(rt, nm) >= 0
            if m.sum() >= 60:
                segs[f"種別:{nm}"] = m
        for nm, m in segs.items():
            sub = idx[m]
            if len(sub) < 40:
                continue
            print(C.line(f"{nm} (n={len(sub)})", C.summarize(run(sub, build), n_days_all=nd)))


# ═══════════ 7. ダッチングでは「集合」しか効かない ═══════════
# 🔴 ∝1/予測オッズ で配分すると **どの点が当たっても払戻は同じ**（=10,000/Σ(1/po)）。
#    したがって並べ替えは無意味で、設計の自由度は「5車の相手からどれを買うか」だけ。

def mk_drop(js: tuple[int, ...], by: str = "po"):
    """相手5車のうち js 番目（by で並べたときの 0-index）を外す。"""
    def build(i):
        a1, a2, rest = _axes(i)
        po = po3(i)
        if by == "po":       # 予測オッズ昇順＝人気順
            order = sorted(rest, key=lambda x: po[C.C3IDX[frozenset((a1, a2, x))]])
        else:                # p3 降順
            order = rest
        keep = [c for n, c in enumerate(order) if n not in js]
        return [(a1, a2, x) for x in keep]
    return build


def section7():
    arms = [("全流し5点", mk_drop(()), True, "trio")]
    for j in range(5):
        arms.append((f"人気{j+1}番目の相手を外す(4点)", mk_drop((j,)), True, "trio"))
    for js in ((0, 1), (0, 4), (3, 4)):
        arms.append((f"人気{js[0]+1},{js[1]+1}番目を外す(3点)", mk_drop(js), True, "trio"))
    show("7. 相手5車のうちどれを外すか（三連複・ダッチング）", arms)


# ═══════════ 8. 絞り込みの組み合わせ ═══════════

def _mask(idx, name):
    z = Z
    if name == "all":
        return np.ones(len(idx), bool)
    if name == "agree":
        return z["AGREE"][idx]
    if name == "as130":
        return z["AXIS_SUM"][idx] >= 1.30
    if name == "agree+as130":
        return z["AGREE"][idx] & (z["AXIS_SUM"][idx] >= 1.30)
    raise ValueError(name)


def section8():
    builds = [("軸2+人気1番目外し4点", mk_drop((0,))),
              ("軸2+人気1,2番目外し3点", mk_drop((0, 1))),
              ("軸2+全流し5点", mk_drop(()))]
    print("\n### 8. 絞り込みの組み合わせ")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        print(f"\n[{w}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for bn, b in builds:
            for mn in ("all", "agree", "as130", "agree+as130"):
                sub = idx[_mask(idx, mn)]
                print(C.line(f"{bn} × {mn}", C.summarize(run(sub, b), n_days_all=nd)))


def section9():
    """三連単の 平均想定払戻 も見る（売れる帯の判断に要る）。"""
    arms = [("三連単 確率上位2点", mk_tf(lambda i: tf_pool(i, "prob", 2)), True, "tf"),
            ("三連単 確率上位3点", mk_tf(lambda i: tf_pool(i, "prob", 3)), True, "tf"),
            ("三連単 確率上位5点", mk_tf(lambda i: tf_pool(i, "prob", 5)), True, "tf"),
            ("三連単 確率上位10点", mk_tf(lambda i: tf_pool(i, "prob", 10)), True, "tf"),
            ("三連単 軸2車順不同→流し1(2点)", mk_tf(lambda i: tf_form(i, "both", 1)), True, "tf"),
            ("三連単 軸2車順不同→流し2(4点)", mk_tf(lambda i: tf_form(i, "both", 2)), True, "tf"),
            ("三連単 軸2車順不同→流し3(6点)", mk_tf(lambda i: tf_form(i, "both", 3)), True, "tf")]
    show("9. 三連単（平均想定払戻つき）", arms)


# ═══════════ 10. 売れる帯（平均想定払戻）と既存商品との重なり ═══════════
# 帯→pt/R は docs/sales_kpi.md §11.2.1 の実測（三連複のみ・観察データで交絡あり）。
BAND = [(12_000, "〜1.2万", 65), (15_000, "1.2〜1.5万", 122), (20_000, "1.5〜2万", 135),
        (30_000, "2〜3万", 171), (50_000, "3〜5万", 309), (10**9, "5万+", 225)]


def band_of(mean: float) -> tuple[str, int]:
    for hi, nm, pt in BAND:
        if mean < hi:
            return nm, pt
    return BAND[-1][1], BAND[-1][2]


def section10():
    cands = [("軸2+人気1番目外し4点", mk_drop((0,))),
             ("軸2+人気1,2番目外し3点", mk_drop((0, 1))),
             ("軸2+全流し5点", mk_drop(())),
             ("軸2+p3 1点", mk_trio_k("p3", 1))]
    print("\n### 10. 平均想定払戻の帯（三連複・§11.2.1 の pt/R を当てた粗い試算）")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        print(f"\n[{w}] {len(idx):,}R / {nd}日")
        for nm, b in cands:
            recs = run(idx, b)
            cnt: dict[str, int] = {}
            pt = 0
            for r in recs:
                bn, p = band_of(r["mean"])
                cnt[bn] = cnt.get(bn, 0) + 1
                pt += p
            share = " ".join(f"{k}:{v/len(recs)*100:.0f}%" for k, v in
                             sorted(cnt.items(), key=lambda kv: -kv[1]))
            print(f"  {nm:22s} {len(recs)/nd:5.2f}件/日  推定 {pt/nd:6.0f}pt/日   {share}")


def section11():
    """既存商品の母集団と重なるか（型Dの中の内訳）。"""
    z = Z
    print("\n### 11. 型D と既存ランクの母集団の重なり")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        agree = z["AGREE"][idx]
        pw_top_is_maru = np.array([
            C.pw_order(int(i))[0] == next((c for c in range(1, 8)
                                           if z["A_prediction_mark"][i][c - 1] == 1), -1)
            for i in idx])
        print(f"\n[{w}] {len(idx):,}R / {nd}日 = {len(idx)/nd:.2f}件/日")
        print(f"  印不一致（7S/7M1 の母集団）      {(~agree).sum():5d}R  {(~agree).sum()/nd:.2f}件/日")
        print(f"  印一致 ∧ pw最上位≠◎（7B 相当）   {(agree & ~pw_top_is_maru).sum():5d}R"
              f"  {(agree & ~pw_top_is_maru).sum()/nd:.2f}件/日")
        print(f"  印一致 ∧ pw最上位==◎（どこにも無い）{(agree & pw_top_is_maru).sum():5d}R"
              f"  {(agree & pw_top_is_maru).sum()/nd:.2f}件/日")
        print("  ※ 7C は axis_sum>=1.44 なので型Dとは定義上ゼロ重なり")


# ═══════════ 12. 採用候補の安定性 ═══════════

def boot_ci(recs, key, n=2000, seed=0):
    """レース単位のブートストラップ（95%CI）。key: shown/roi/med_pay"""
    rng = np.random.default_rng(seed)
    arr = np.array([[r["pay"], r["inv"]] for r in recs], float)
    out = []
    for _ in range(n):
        s = arr[rng.integers(0, len(arr), len(arr))]
        hit = s[:, 0] > 0
        if key == "shown":
            out.append(((s[:, 0] > s[:, 1]).sum()) / len(s) * 100)
        elif key == "roi":
            out.append(s[:, 0].sum() / s[:, 1].sum() * 100)
        else:
            out.append(np.median(s[hit, 0]) if hit.any() else 0.0)
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def section12():
    b = mk_drop((0,))
    print("\n### 12. 採用候補（軸2+人気1番目外し4点）の安定性")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        recs = run(idx, b)
        s = C.summarize(recs, n_days_all=nd)
        lo, hi = boot_ci(recs, "shown")
        rlo, rhi = boot_ci(recs, "roi")
        print(f"\n[{w}] n={s['n']}  表示的中 {s['shown']:.2f}% CI[{lo:.2f},{hi:.2f}]"
              f"  ROI {s['roi']:.1f}% CI[{rlo:.1f},{rhi:.1f}]")
        # 半期ごと
        d = np.array([r["date"] for r in recs])
        half = np.array([x[:4] + ("H1" if x[5:7] <= "06" else "H2") for x in d])
        print(C.HEAD)
        for h in sorted(set(half)):
            sub = [r for r, k in zip(recs, half) if k == h]
            ndh = len({r["date"] for r in sub})
            print(C.line(f"{h} (n={len(sub)})", C.summarize(sub, n_days_all=ndh)))


# ═══════════ 13. 1軸だけ固定（二軸そろいが43%しかないことへの直接の答え） ═══════════

def mk_one_axis(k: int, how: str = "joint", axis: str = "p3"):
    def build(i):
        a1 = C.p3_order(i)[0] if axis == "p3" else C.pw_order(i)[0]
        tp, po = trio_prob(i), po3(i)
        cand = [frozenset((a1, x, y)) for n, x in enumerate(range(1, 8))
                for y in range(x + 1, 8) if x != a1 and y != a1]
        if how == "joint":
            cand.sort(key=lambda c: -tp[C.C3IDX[c]])
        elif how == "ev":
            cand.sort(key=lambda c: -(tp[C.C3IDX[c]] * po[C.C3IDX[c]]))
        elif how == "po":
            cand.sort(key=lambda c: -po[C.C3IDX[c]])
        return [tuple(c) for c in cand[:k]]
    return build


def section13():
    z = Z
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        a1in = np.mean([C.p3_order(int(i))[0] in set(C.CANON3[int(z["TRIO_WIN"][i])]) for i in idx])
        print(f"[{w}] 軸1(p3最上位)の3着内率 {a1in*100:.1f}%")
    arms = []
    for k in (2, 3, 4, 5, 6, 8):
        arms.append((f"1軸 joint上位{k}点", mk_one_axis(k, "joint"), True, "trio"))
    for k in (4, 6, 8):
        arms.append((f"1軸 ev上位{k}点", mk_one_axis(k, "ev"), True, "trio"))
    show("13. 軸1だけ固定して2・3着を組む", arms)


# ═══════════ 14. 上限の確認（軸の縛りを外して、ゲートを通る中で確率最大の集合） ═══════════
# ダッチングでは 平均想定払戻 = 10,000/Σ(1/po) なので、ゲート ⇔ Σ(1/po) < 0.5。
# 「Σ(1/po)<0.5 を満たす k 点で Σ確率 最大」を貪欲に解いて、軸2車固定が何を損しているか見る。

def mk_greedy(k: int):
    def build(i):
        tp, po = trio_prob(i), po3(i)
        ok = np.isfinite(po) & (po > 0)
        order = np.argsort(-tp)
        chosen, s = [], 0.0
        for j in order:
            if not ok[j]:
                continue
            if s + 1.0 / po[j] >= 0.5 - 1e-9:
                continue
            chosen.append(j)
            s += 1.0 / po[j]
            if len(chosen) == k:
                break
        return [C.CANON3[j] for j in chosen] if len(chosen) == k else []
    return build


def section14():
    arms = [(f"貪欲 ゲート内 確率最大{k}点", mk_greedy(k), True, "trio") for k in (2, 3, 4, 5, 6)]
    arms.append(("軸2+人気1番目外し4点", mk_drop((0,)), True, "trio"))
    show("14. 軸の縛りを外した上限（貪欲）", arms)


# ═══════════ 15. 最終比較（売れる帯の試算・軸の含有率つき） ═══════════

FINAL = [("軸2+人気1番目外し4点", mk_drop((0,))),
         ("軸2+joint2点", mk_trio_k("joint", 2)),
         ("貪欲2点", mk_greedy(2)),
         ("貪欲3点", mk_greedy(3)),
         ("貪欲4点", mk_greedy(4)),
         ("軸2+人気1,2番目外し3点", mk_drop((0, 1))),
         ("軸2+p3 1点", mk_trio_k("p3", 1))]


def section15():
    print("\n### 15. 最終比較（推定 pt/日 は §11.2.1 の帯別 pt/R を当てた粗い試算）")
    for w in ("explore", "confirm"):
        idx = C.select(TYPE, w)
        nd = C.days_of(idx)
        print(f"\n[{w}] {len(idx):,}R / {nd}日")
        print("  {:24s} {:>6s} {:>8s} {:>9s} {:>10s} {:>8s} {:>8s} {:>7s} {:>7s}".format(
            "腕", "件/日", "表示的中%", "払戻中央", "平均払戻中央", "2倍+/日", "推定pt/日",
            "軸1含有", "軸2含有"))
        for nm, b in FINAL:
            recs, a1c, a2c, n = [], 0, 0, 0
            pt = 0
            for i in idx:
                i = int(i)
                combos = list(dict.fromkeys(frozenset(c) for c in b(i)))
                if not combos:
                    continue
                st = C.trio_stakes(i, combos)
                if st is None or not C.trio_gate(i, st):
                    continue
                inv, pay, mean = C.trio_result(i, st)
                recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(st)))
                pt += band_of(mean)[1]
                o = C.p3_order(i)
                n += 1
                a1c += sum(1 for c in combos if o[0] in c) / len(combos)
                a2c += sum(1 for c in combos if o[0] in c and o[1] in c) / len(combos)
            s = C.summarize(recs, n_days_all=nd)
            print("  {:24s} {:6.2f} {:8.2f} {:9,.0f} {:10,.0f} {:8.2f} {:8.0f} {:6.0f}% {:6.0f}%"
                  .format(nm, s["perday"], s["shown"], s["med_pay"], s["med_mean"],
                          s["two_per_day"], pt / nd, a1c / n * 100, a2c / n * 100))


SECTIONS = {
    "1": section1, "1b": section1b, "2": section2, "2b": section2b, "2c": section2c,
    "3": section3, "3b": section3b, "4": section4, "5": section5, "6": section6,
    "7": section7, "8": section8, "9": section9, "10": section10, "11": section11,
    "12": section12, "13": section13, "14": section14, "15": section15,
}

if __name__ == "__main__":
    want = sys.argv[1:] or list(SECTIONS)
    for s in want:
        SECTIONS[s]()
