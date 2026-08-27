#!/usr/bin/env python3
"""型B「堅い・中」に対する購入可能な買い方の網羅探索（2026-08-27）。

型B = axis_sum(vintage p3 上位2車の合計) >= 1.44 ∧ 荒れ度 == 0
探索窓 2024-07〜2025-12 / 確認窓 2026-01〜2026-08（**確認窓で判断**）
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# 🔴 NpzFile は key を引くたび解凍する（`common` の trio_* も毎回引く）。
#    一度だけ実体化して dict へ差し替える。
C._Z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
Z = C.board()
CANON3 = C.CANON3
C3IDX = C.C3IDX
CANON = C.CANON
CIDX = C.CIDX


# ── 派生量 ────────────────────────────────────────────────
_M = np.zeros((210, 35), np.float32)
for _t, _c in enumerate(CANON):
    _M[_t, C3IDX[frozenset(_c)]] = 1.0
_JOINT = None


def joint3(i: int) -> np.ndarray:
    """三連複35点の同時確率（三連単210点の合成PLを組み合わせへ集約）。"""
    global _JOINT
    if _JOINT is None:
        _JOINT = Z["PROB"].astype(np.float32) @ _M
    return _JOINT[i]


_ORD = None


def p3ord(i: int) -> list[int]:
    global _ORD
    if _ORD is None:
        _ORD = (np.argsort(-Z["P3"], axis=1) + 1).astype(np.int8)
    return [int(x) for x in _ORD[i]]


def days_all(window: str) -> int:
    return C.days_of(C.select(None, window))


# ── 三連複の腕 ────────────────────────────────────────────
def axis_pair(i: int) -> tuple[int, int]:
    o = p3ord(i)
    return o[0], o[1]


def partners_by(i: int, how: str, k: int) -> list[int]:
    """軸2車以外の5車から相手をk車選ぶ。"""
    a, b = axis_pair(i)
    rest = [c for c in p3ord(i) if c not in (a, b)]     # p3降順（=全体3〜7番手）
    if how == "p3desc":
        return rest[:k]
    if how == "p3asc":
        return rest[::-1][:k]
    if how.startswith("rank"):                                # 単独の順位
        r = int(how[4:])
        return [rest[r - 3]] if r - 3 < len(rest) else []
    j = joint3(i)
    if how == "joint":
        sc = {c: j[C3IDX[frozenset((a, b, c))]] for c in rest}
        return sorted(rest, key=lambda c: -sc[c])[:k]
    if how == "ev":
        po = Z["TRIO_PO"][i]
        sc = {}
        for c in rest:
            t = C3IDX[frozenset((a, b, c))]
            sc[c] = j[t] * (po[t] if np.isfinite(po[t]) else 0.0)
        return sorted(rest, key=lambda c: -sc[c])[:k]
    if how == "po_desc":                                      # 予測オッズが高い順（穴寄り）
        po = Z["TRIO_PO"][i]
        sc = {c: (po[C3IDX[frozenset((a, b, c))]] if np.isfinite(
            po[C3IDX[frozenset((a, b, c))]]) else -1) for c in rest}
        return sorted(rest, key=lambda c: -sc[c])[:k]
    raise ValueError(how)


def run_trio(idx, build, tilt=True, nd=None, gate=True):
    recs = []
    for i in idx:
        combos = build(int(i))
        if not combos:
            continue
        combos = list(dict.fromkeys(combos))
        st = C.trio_stakes(int(i), combos, tilt=tilt)
        if st is None:
            continue
        if gate and not C.trio_gate(int(i), st):
            continue
        inv, pay, mean = C.trio_result(int(i), st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


def tf_tilt_stakes(i: int, combos: list[tuple]) -> dict | None:
    """三連単のダッチング（∝1/予測オッズ）。三連複 `trio_stakes` と同じ規則。"""
    po = [float(Z["PO"][i][CIDX[c]]) for c in combos]
    if any((not np.isfinite(x)) or x <= 0 for x in po):
        return None
    w = [1.0 / x for x in po]
    n_units = C.BUDGET // C.UNIT
    if n_units < len(combos):
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


def run_tf(idx, build, nd=None, tilt=False, mean_gate=False, min_po=None):
    recs = []
    for i in idx:
        combos = build(int(i))
        if not combos:
            continue
        combos = list(dict.fromkeys(combos))
        if len(combos) > C.BUDGET // C.UNIT:
            continue
        st = tf_tilt_stakes(int(i), combos) if tilt else C.tf_stakes(int(i), combos)
        if st is None:
            continue
        if not C.tf_gate(int(i), st):
            continue
        po_ = {c: float(Z["PO"][i][CIDX[c]]) for c in st}
        if min_po is not None and min(po_.values()) < min_po:
            continue
        if mean_gate and sum(st[c] * po_[c] for c in st) / len(st) <= C.MIN_MEAN_PAYOUT:
            continue
        inv, pay = C.tf_result(int(i), st)
        po = Z["PO"][i]
        mean = sum(st[c] * float(po[CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


# ── 実行 ──────────────────────────────────────────────────
def table(title: str, arms: list[tuple[str, object]], kind="trio", agree=None):
    print(f"\n### {title}")
    for w in ("explore", "confirm"):
        idx = C.select("B", w, agree=agree)
        nd = days_all(w)
        print(f"\n[{w}] 型B {len(idx):,}R / 窓{nd}日")
        print(C.HEAD)
        for name, fn in arms:
            if kind == "call":
                s = fn(idx, nd)
            elif kind == "trio":
                s = run_trio(idx, fn[0], tilt=fn[1], nd=nd)
            elif isinstance(fn, tuple):
                s = run_tf(idx, fn[0], nd=nd, **fn[1])
            else:
                s = run_tf(idx, fn, nd=nd)
            print(C.line(name, s))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"

    if which in ("all", "1"):
        arms = []
        for how in ("p3desc", "joint", "ev", "p3asc", "po_desc"):
            for k in (1, 2, 3, 4, 5):
                arms.append((f"{how} k={k}", (lambda i, h=how, kk=k:
                                              [frozenset(axis_pair(i) + (p,))
                                               for p in partners_by(i, h, kk)], True)))
        table("1. 三連複 軸2車+相手k点（ダッチング）", arms)

    if which in ("all", "2"):
        arms = []
        for r in (3, 4, 5, 6, 7):
            arms.append((f"単独 全体{r}番手", (lambda i, rr=r:
                                              [frozenset(axis_pair(i) + (p,))
                                               for p in partners_by(i, f"rank{rr}", 1)], True)))
        arms.append(("3,4番手のみ", (lambda i: [frozenset(axis_pair(i) + (p,))
                                                for p in partners_by(i, "p3desc", 2)], True)))
        table("2. 三連複 相手を単独順位で固定", arms)

    # ── 3. ゲート回避（2倍未満の点を飛ばして次点へ）─────────────
    def elig(i, order):
        """予測三連複オッズ >= MIN_POINT_ODDS の相手だけを order の順に返す。"""
        a, b = axis_pair(i)
        po = Z["TRIO_PO"][i]
        out = []
        for c in order:
            t = C3IDX[frozenset((a, b, c))]
            if np.isfinite(po[t]) and po[t] >= C.MIN_POINT_ODDS:
                out.append(c)
        return out

    def build_fallback(i, how, k):
        a, b = axis_pair(i)
        rest = [c for c in p3ord(i) if c not in (a, b)]
        if how == "joint":
            j = joint3(i)
            rest = sorted(rest, key=lambda c: -j[C3IDX[frozenset((a, b, c))]])
        ok = elig(i, rest)[:k]
        return [frozenset((a, b, c)) for c in ok]

    if which in ("all", "3"):
        arms = []
        for how in ("p3desc", "joint"):
            for k in (1, 2, 3):
                arms.append((f"落ちたら次点 {how} k={k}",
                             (lambda i, h=how, kk=k: build_fallback(i, h, kk), True)))
        table("3. 三連複 2倍未満の相手を飛ばして次点へ（件数を落とさない）", arms)

    # ── 4. 賭け金 均等 vs ダッチング ────────────────────────────
    if which in ("all", "4"):
        arms = []
        for k in (2, 3):
            for tilt in (True, False):
                nm = "ダッチ" if tilt else "均等"
                arms.append((f"次点補完 k={k} {nm}",
                             (lambda i, kk=k: build_fallback(i, "p3desc", kk), tilt)))
                arms.append((f"p3desc k={k} {nm}",
                             (lambda i, kk=k: [frozenset(axis_pair(i) + (p,))
                                               for p in partners_by(i, "p3desc", kk)], tilt)))
        table("4. 賭け金 均等 vs ダッチング", arms)

    # ── 5. 三連単 ───────────────────────────────────────────────
    def tf_axis_flow(i, m, order="joint", both=True):
        """軸2車を1-2着（both=True なら両順）・3着へ m 車流す。"""
        a, b = axis_pair(i)
        rest = [c for c in p3ord(i) if c not in (a, b)]
        if order == "joint":
            pr = Z["PROB"][i]
            rest = sorted(rest, key=lambda c: -(pr[CIDX[(a, b, c)]] + pr[CIDX[(b, a, c)]]))
        out = []
        for c in rest[:m]:
            out.append((a, b, c))
            if both:
                out.append((b, a, c))
        return out

    def tf_top(i, k):
        """合成PL確率の上位k点（軸制約なし）。"""
        pr = Z["PROB"][i]
        return [CANON[t] for t in np.argsort(-pr)[:k]]

    def tf_axis_any(i, k):
        """軸2車が3着以内に入る目のうち確率上位k点。"""
        a, b = axis_pair(i)
        pr = Z["PROB"][i]
        cand = [t for t in range(210) if a in CANON[t] and b in CANON[t]]
        cand.sort(key=lambda t: -pr[t])
        return [CANON[t] for t in cand[:k]]

    if which in ("all", "5"):
        arms = []
        for m in (1, 2, 3):
            arms.append((f"軸1-2着(両順)x3着{m}車", lambda i, mm=m: tf_axis_flow(i, mm)))
        for k in (2, 3, 5, 8):
            arms.append((f"軸2車在中 確率上位{k}点", lambda i, kk=k: tf_axis_any(i, kk)))
        for k in (2, 3, 5, 10):
            arms.append((f"確率上位{k}点(無制約)", lambda i, kk=k: tf_top(i, kk)))
        table("5. 三連単", arms, kind="tf")

    # ── 6. 1軸固定 + 2軸目2点 + 3着流し ─────────────────────────
    def two_second(i, m, mode="trio"):
        """軸1 = p3 1位固定。2軸目候補は p3 2位・3位。3着へ m 車流す。"""
        o = p3ord(i)
        a, cands = o[0], o[1:3]
        rest = [c for c in o if c not in (a, *cands)]
        if mode == "trio":
            out = []
            for b in cands:
                for c in rest[:m]:
                    out.append(frozenset((a, b, c)))
            return out
        out = []
        for b in cands:
            for c in rest[:m]:
                out.append((a, b, c))
        return out

    if which in ("all", "6"):
        arms = []
        for m in (1, 2):
            arms.append((f"軸1+2軸目2点+3着{m}車 ダッチ",
                         (lambda i, mm=m: two_second(i, mm), True)))
            arms.append((f"軸1+2軸目2点+3着{m}車 均等",
                         (lambda i, mm=m: two_second(i, mm), False)))
        table("6. 三連複 1軸固定+2軸目2点+3着流し", arms)

    # ── 8. 三連単 ダッチング・下限オッズ ────────────────────────
    if which in ("all", "8"):
        arms = []
        for k in (2, 3, 4, 5):
            arms.append((f"確率上位{k}点 均等",
                         (lambda i, kk=k: tf_axis_any(i, kk), dict())))
            arms.append((f"確率上位{k}点 ダッチ",
                         (lambda i, kk=k: tf_axis_any(i, kk), dict(tilt=True))))
            arms.append((f"確率上位{k}点 ダッチ+平均2万",
                         (lambda i, kk=k: tf_axis_any(i, kk),
                          dict(tilt=True, mean_gate=True))))
            arms.append((f"確率上位{k}点 均等+各点{k}倍以上",
                         (lambda i, kk=k: tf_axis_any(i, kk), dict(min_po=float(k)))))
        table("8. 三連単 賭け金と下限", arms, kind="tf")

    # ── 9. 三連単 1軸固定+2軸目2点+3着流し ──────────────────────
    def tf_two_second(i, m):
        o = p3ord(i)
        a, cands = o[0], o[1:3]
        pr = Z["PROB"][i]
        out = []
        for b in cands:
            rest = [c for c in o if c not in (a, b)]
            rest = sorted(rest, key=lambda c: -pr[CIDX[(a, b, c)]])
            out += [(a, b, c) for c in rest[:m]]
        return out

    if which in ("all", "9"):
        arms = []
        for m in (1, 2, 3):
            arms.append((f"1着軸1・2着2車・3着{m}車 均等",
                         (lambda i, mm=m: tf_two_second(i, mm), dict())))
            arms.append((f"1着軸1・2着2車・3着{m}車 ダッチ",
                         (lambda i, mm=m: tf_two_second(i, mm), dict(tilt=True))))
        table("9. 三連単 1軸固定+2軸目2点+3着流し", arms, kind="tf")

    # ── 12. 合成オッズを固定して点数を可変にする（定義は上）──────
    # ── 7・10・11. 決勝候補の細部 ────────────────────────────────
    def tf_adaptive_min(i, floor, kmin, kmax=8):
        """floor を守れる最大点数。ただし最低 kmin 点は必ず買う（floor を割ってもよい）。"""
        a, b = axis_pair(i)
        pr, po = Z["PROB"][i], Z["PO"][i]
        cand = sorted([t for t in range(210) if a in CANON[t] and b in CANON[t]],
                      key=lambda t: -pr[t])
        acc, best = 0.0, []
        for t in cand[:kmax]:
            v = float(po[t])
            if not np.isfinite(v) or v <= 0:
                return []
            acc2 = acc + 1.0 / v
            if len(best) >= kmin and C.BUDGET / acc2 < floor:
                break
            acc = acc2
            best.append(CANON[t])
        return best

    def tf_adaptive(i, floor, kmax=8):
        """軸2車在中の確率上位から積み、想定平均払戻（=BUDGET/Σ(1/予測オッズ)）が
        floor を下回らない最大点数で止める。ダッチングだと mean は点数に単調減少。"""
        a, b = axis_pair(i)
        pr = Z["PROB"][i]
        po = Z["PO"][i]
        cand = [t for t in range(210) if a in CANON[t] and b in CANON[t]]
        cand.sort(key=lambda t: -pr[t])
        acc, best = 0.0, []
        for t in cand[:kmax]:
            v = float(po[t])
            if not np.isfinite(v) or v <= 0:
                return []
            acc2 = acc + 1.0 / v
            if C.BUDGET / acc2 < floor:
                break
            acc = acc2
            best.append(CANON[t])
        return best

    FINAL = [
        ("★30k可変 最低2点+2万ゲート", "tf",
         (lambda i: tf_adaptive_min(i, 30_000, 2), dict(tilt=True, mean_gate=True))),
        ("TF 30k可変 最低2点 ダッチ", "tf",
         (lambda i: tf_adaptive_min(i, 30_000, 2), dict(tilt=True))),
        ("TF 30k可変 最低3点 ダッチ", "tf",
         (lambda i: tf_adaptive_min(i, 30_000, 3), dict(tilt=True))),
        ("TF 合成>=30k 可変 ダッチ", "tf",
         (lambda i: tf_adaptive(i, 30_000), dict(tilt=True))),
        ("TF 合成>=25k 可変 ダッチ", "tf",
         (lambda i: tf_adaptive(i, 25_000), dict(tilt=True))),
        ("TF 確率上位2点 ダッチ", "tf", (lambda i: tf_axis_any(i, 2), dict(tilt=True))),
        ("TF 確率上位3点 ダッチ", "tf", (lambda i: tf_axis_any(i, 3), dict(tilt=True))),
        ("TF 確率上位4点 ダッチ", "tf", (lambda i: tf_axis_any(i, 4), dict(tilt=True))),
        ("TF 1着軸1・2着2・3着2 ダッチ", "tf",
         (lambda i: tf_two_second(i, 2), dict(tilt=True))),
        ("三連複 単独 全体3番手", "trio",
         (lambda i: [frozenset(axis_pair(i) + (p,))
                     for p in partners_by(i, "p3desc", 1)], True)),
        ("三連複 次点補完 k=2", "trio", (lambda i: build_fallback(i, "p3desc", 2), True)),
    ]

    def run_final(idx, nd):
        out = []
        for nm, kd, fn in FINAL:
            s_ = (run_trio(idx, fn[0], tilt=fn[1], nd=nd) if kd == "trio"
                  else run_tf(idx, fn[0], nd=nd, **fn[1]))
            out.append((nm, s_))
        return out

    if which in ("all", "7"):
        for ag in (True, False):
            for w in ("explore", "confirm"):
                idx = C.select("B", w, agree=ag)
                nd = days_all(w)
                print(f"\n### 7. 印一致={ag} [{w}] {len(idx):,}R / {nd}日")
                print(C.HEAD)
                for nm, s_ in run_final(idx, nd):
                    print(C.line(nm, s_))

    if which in ("all", "12"):
        arms = []
        for fl in (25_000, 30_000, 35_000, 40_000, 50_000):
            arms.append((f"合成払戻>={fl//1000}k で点数可変",
                         (lambda i, f=fl: tf_adaptive(i, f), dict(tilt=True))))
        table("12. 三連単 想定平均払戻を固定して点数可変（ダッチ）", arms, kind="tf")

    def tf_adaptive_gen(i, floor, order="prob", axis=True, kmax=8):
        a, b = axis_pair(i)
        pr = Z["PROB"][i]
        po = Z["PO"][i]
        cand = [t for t in range(210) if (not axis) or (a in CANON[t] and b in CANON[t])]
        if order == "prob":
            cand.sort(key=lambda t: -pr[t])
        elif order == "ev":
            cand.sort(key=lambda t: -(pr[t] * (po[t] if np.isfinite(po[t]) else 0)))
        elif order == "axis_first":       # 軸2車が1-2着の目を先に
            cand.sort(key=lambda t: (not (set(CANON[t][:2]) == {a, b}), -pr[t]))
        acc, best = 0.0, []
        for t in cand[:kmax]:
            v = float(po[t])
            if not np.isfinite(v) or v <= 0:
                return []
            acc2 = acc + 1.0 / v
            if C.BUDGET / acc2 < floor:
                break
            acc = acc2
            best.append(CANON[t])
        return best

    if which in ("all", "14"):
        arms = []
        for fl in (28_000, 30_000, 32_000):
            arms.append((f"軸2車在中・確率順 {fl//1000}k",
                         (lambda i, f=fl: tf_adaptive_gen(i, f), dict(tilt=True))))
        arms.append(("無制約・確率順 30k",
                     (lambda i: tf_adaptive_gen(i, 30_000, axis=False), dict(tilt=True))))
        arms.append(("軸2車在中・EV順 30k",
                     (lambda i: tf_adaptive_gen(i, 30_000, order="ev"), dict(tilt=True))))
        arms.append(("軸2車が1-2着優先 30k",
                     (lambda i: tf_adaptive_gen(i, 30_000, order="axis_first"), dict(tilt=True))))
        arms.append(("軸2車在中・確率順 30k 上限5点",
                     (lambda i: tf_adaptive_gen(i, 30_000, kmax=5), dict(tilt=True))))
        arms.append(("軸2車在中・確率順 30k 上限3点",
                     (lambda i: tf_adaptive_gen(i, 30_000, kmax=3), dict(tilt=True))))
        table("14. 合成払戻固定の変種", arms, kind="tf")

    if which in ("all", "15"):
        # 🔴 作法: baseline は実データを1件表示して目視確認する
        idx = C.select("B", "confirm")
        print("\n### 15. 採用候補の入稿を実データで目視（確認窓の先頭5R）")
        shown = 0
        ks = []
        for i in idx:
            cb = tf_adaptive_min(int(i), 30_000, 2)
            if not cb:
                continue
            st = tf_tilt_stakes(int(i), cb)
            if st is None:
                continue
            if sum(st[c] * float(Z["PO"][i][CIDX[c]]) for c in st) / len(st) <= C.MIN_MEAN_PAYOUT:
                continue
            ks.append(len(cb))
            if shown < 5:
                inv, pay = C.tf_result(int(i), st)
                mean = sum(st[c] * float(Z["PO"][i][CIDX[c]]) for c in st) / len(st)
                win = CANON[int(Z["WIN"][i])]
                print(f"  {Z['KEY'][i]} {Z['DATE'][i]} {Z['RTYPE'][i]} "
                      f"軸={axis_pair(int(i))} 的中={'-'.join(map(str, win))} "
                      f"払戻={pay:,.0f}円 想定平均={mean:,.0f}円")
                for c in cb:
                    print(f"      {'-'.join(map(str, c))}  {st[c]:>5,}円 "
                          f"予測{float(Z['PO'][i][CIDX[c]]):7.1f}倍 "
                          f"想定{st[c]*float(Z['PO'][i][CIDX[c]]):>8,.0f}円"
                          f"{'  ★的中' if c == win else ''}")
                shown += 1
        ks = np.array(ks)
        print("\n  点数の分布:", {int(k): int((ks == k).sum()) for k in sorted(set(ks.tolist()))},
              f"  平均 {ks.mean():.2f}点")
        for w in ("explore", "confirm"):
            ii = C.select("B", w)
            nd = days_all(w)
            s_ = run_tf(ii, lambda i: tf_adaptive_min(i, 30_000, 2), nd=nd, tilt=True,
                        mean_gate=True)
            print(f"  [{w}] 10万円+の的中 {s_['big_per_day']:.3f}件/日 "
                  f"（{s_['big_per_day']*nd:.0f}件 / {nd}日）")

    if which in ("all", "16"):
        # 点数別の内訳（合成>=30k 可変）と 最低点数の変種
        for w in ("explore", "confirm"):
            ii = C.select("B", w)
            nd = days_all(w)
            print(f"\n### 16. 点数別の内訳 [{w}]")
            print(C.HEAD)
            for lo, hi, nm in ((1, 1, "1点のみ"), (2, 2, "2点"), (3, 4, "3-4点"),
                               (5, 8, "5-8点")):
                def bld(i, lo=lo, hi=hi):
                    cb = tf_adaptive(i, 30_000)
                    return cb if cb and lo <= len(cb) <= hi else []
                print(C.line(nm, run_tf(ii, bld, nd=nd, tilt=True)))

        arms = [
            ("30k 可変（最低1点）", (lambda i: tf_adaptive(i, 30_000), dict(tilt=True))),
            ("30k 可変 最低2点", (lambda i: tf_adaptive_min(i, 30_000, 2), dict(tilt=True))),
            ("30k 可変 最低3点", (lambda i: tf_adaptive_min(i, 30_000, 3), dict(tilt=True))),
            ("30k 可変 2点未満は見送り",
             (lambda i: (lambda c: c if len(c) >= 2 else [])(tf_adaptive(i, 30_000)),
              dict(tilt=True))),
            ("25k 可変 最低2点", (lambda i: tf_adaptive_min(i, 25_000, 2), dict(tilt=True))),
        ]
        table("16b. 最低点数の変種", arms, kind="tf")

    if which in ("all", "17"):
        arms = [
            ("30k可変 最低1点", (lambda i: tf_adaptive(i, 30_000), dict(tilt=True))),
            ("30k可変 最低2点", (lambda i: tf_adaptive_min(i, 30_000, 2), dict(tilt=True))),
            ("30k可変 最低2点 かつ平均2万+",
             (lambda i: tf_adaptive_min(i, 30_000, 2), dict(tilt=True, mean_gate=True))),
            ("30k可変 最低3点 かつ平均2万+",
             (lambda i: tf_adaptive_min(i, 30_000, 3), dict(tilt=True, mean_gate=True))),
            ("35k可変 最低2点 かつ平均2万+",
             (lambda i: tf_adaptive_min(i, 35_000, 2), dict(tilt=True, mean_gate=True))),
        ]
        table("17. 最終候補の詰め", arms, kind="tf")
        for w in ("explore", "confirm"):
            idx = C.select("B", w)
            print(f"\n  想定平均払戻の帯 [{w}]")
            for nm, fn, mg in (("30k可変最低2点+2万ゲート",
                                lambda i: tf_adaptive_min(i, 30_000, 2), True),
                               ("30k可変最低3点+2万ゲート",
                                lambda i: tf_adaptive_min(i, 30_000, 3), True)):
                ms, ks = [], []
                for i in idx:
                    cb = fn(int(i))
                    if not cb:
                        continue
                    st = tf_tilt_stakes(int(i), cb)
                    if st is None:
                        continue
                    m = sum(st[c] * float(Z["PO"][i][CIDX[c]]) for c in st) / len(st)
                    if mg and m <= C.MIN_MEAN_PAYOUT:
                        continue
                    ms.append(m); ks.append(len(cb))
                ms = np.array(ms)
                b = [(ms < 20_000), (ms >= 20_000) & (ms < 30_000),
                     (ms >= 30_000) & (ms < 50_000), (ms >= 50_000)]
                print(f"    {nm:24s} n={len(ms):5d} 平均{np.mean(ks):4.2f}点"
                      f"  <2万 {b[0].mean()*100:4.1f}%  2-3万 {b[1].mean()*100:5.1f}%"
                      f"  3-5万 {b[2].mean()*100:5.1f}%  5万+ {b[3].mean()*100:4.1f}%")

    if which in ("all", "13"):
        # 平均想定払戻の帯シェア（売上KPIの帯）
        for w in ("explore", "confirm"):
            idx = C.select("B", w)
            print(f"\n### 13. 想定平均払戻の帯シェア [{w}] {len(idx):,}R")
            for nm, fn in (("TF上位2点", lambda i: tf_axis_any(i, 2)),
                           ("TF上位3点", lambda i: tf_axis_any(i, 3)),
                           ("TF上位4点", lambda i: tf_axis_any(i, 4)),
                           ("合成>=30k可変", lambda i: tf_adaptive(i, 30_000)),
                           ("30k可変最低2点", lambda i: tf_adaptive_min(i, 30_000, 2)),
                           ("30k可変最低3点", lambda i: tf_adaptive_min(i, 30_000, 3)),
                           ("25k可変最低2点", lambda i: tf_adaptive_min(i, 25_000, 2)),
                           ("合成>=35k可変", lambda i: tf_adaptive(i, 35_000))):
                ms, ks = [], []
                for i in idx:
                    cb = fn(int(i))
                    if not cb:
                        continue
                    st = tf_tilt_stakes(int(i), cb)
                    if st is None:
                        continue
                    ms.append(sum(st[c] * float(Z["PO"][i][CIDX[c]]) for c in st) / len(st))
                    ks.append(len(cb))
                ms = np.array(ms)
                b = [(ms < 20_000), (ms >= 20_000) & (ms < 30_000),
                     (ms >= 30_000) & (ms < 50_000), (ms >= 50_000)]
                print(f"  {nm:14s} n={len(ms):5d} 平均点数{np.mean(ks):4.2f}"
                      f"  <2万 {b[0].mean()*100:5.1f}%  2-3万 {b[1].mean()*100:5.1f}%"
                      f"  3-5万 {b[2].mean()*100:5.1f}%  5万+ {b[3].mean()*100:5.1f}%")

    if which in ("all", "10"):
        # 四半期ごとの安定性（確認窓の内訳）
        d = Z["DATE"]
        for w in ("explore", "confirm"):
            base = C.select("B", w)
            qs = sorted({f"{x[:4]}Q{(int(x[5:7])-1)//3+1}" for x in d[base]})
            for q in qs:
                m = np.array([f"{x[:4]}Q{(int(x[5:7])-1)//3+1}" == q for x in d[base]])
                idx = base[m]
                nd = len(set(d[idx]))
                print(f"\n### 10. {q} 型B {len(idx):,}R / 型Bが出た{nd}日")
                print(C.HEAD)
                for nm, s_ in run_final(idx, nd):
                    print(C.line(nm, s_))

    if which in ("all", "11"):
        # 確認窓のブートストラップCI（レース単位・2000回）
        rng = np.random.default_rng(0)
        idx = C.select("B", "confirm")
        nd = days_all("confirm")
        print(f"\n### 11. 確認窓 ブートストラップ95%CI（{len(idx):,}R / {nd}日）")
        for nm, kd, fn in FINAL:
            recs = []
            for i in idx:
                combos = fn[0](int(i)) if kd == "tf" else fn[0](int(i))
                if not combos:
                    continue
                combos = list(dict.fromkeys(combos))
                if kd == "trio":
                    st = C.trio_stakes(int(i), combos, tilt=fn[1])
                    if st is None or not C.trio_gate(int(i), st):
                        continue
                    inv, pay, mean = C.trio_result(int(i), st)
                else:
                    kw = fn[1]
                    st = (tf_tilt_stakes(int(i), combos) if kw.get("tilt")
                          else C.tf_stakes(int(i), combos))
                    if st is None or not C.tf_gate(int(i), st):
                        continue
                    mean = sum(st[c] * float(Z["PO"][i][CIDX[c]]) for c in st) / len(st)
                    if kw.get("mean_gate") and mean <= C.MIN_MEAN_PAYOUT:
                        continue
                    inv, pay = C.tf_result(int(i), st)
                recs.append((pay > inv, pay, inv))
            a = np.array([r[0] for r in recs]); pv = np.array([r[1] for r in recs])
            iv = np.array([r[2] for r in recs])
            n = len(recs)
            sh, roi, tp = [], [], []
            for _ in range(2000):
                s2 = rng.integers(0, n, n)
                sh.append(a[s2].mean() * 100)
                roi.append(pv[s2].sum() / iv[s2].sum() * 100)
                tp.append((pv[s2] >= 2 * iv[s2]).sum() / nd)
            f = lambda v: (np.percentile(v, 2.5), np.percentile(v, 97.5))
            print(f"  {nm:26s} n={n:4d} 表示的中 {a.mean()*100:5.2f}% CI[{f(sh)[0]:.2f},{f(sh)[1]:.2f}]"
                  f"  2倍+/日 {(pv>=2*iv).sum()/nd:.2f} CI[{f(tp)[0]:.2f},{f(tp)[1]:.2f}]"
                  f"  ROI {pv.sum()/iv.sum()*100:.1f}% CI[{f(roi)[0]:.1f},{f(roi)[1]:.1f}]")
