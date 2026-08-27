#!/usr/bin/env python3
"""型F「大混戦」に買える形があるかを網羅的に探す（2026-08-27）。

型F = 混戦(axis_sum < 1.44) ∧ 荒れ度 >= 1 。全7車の 19.3件/日（6型で最大）。
既知: 二軸そろい 39.1% / 確定三連複オッズ中央 12.3倍 / 決まり手 逃17 捲30 差53。

使い方:
    .venv/bin/python scripts/exp_type_lab/type_f.py            # 全部
    .venv/bin/python scripts/exp_type_lab/type_f.py trio       # 三連複だけ
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TYPE = "F"
WINDOWS = ("explore", "confirm")

# 🔴 npz は毎回の [] アクセスで解凍が走る（実測 0.1秒/回）。必ずメモリへ載せてから使う。
C._Z = {k: v for k, v in ((k, C.board()[k]) for k in C.board().files)}
Z = C._Z
_P3_ORDER = np.argsort(-Z["P3"], axis=1) + 1     # レース x 7（3着内率の降順）


# ───────────────────────── 下ごしらえ ─────────────────────────

_TRIO_PROB_CACHE: dict[int, np.ndarray] = {}


def trio_prob(i: int) -> np.ndarray:
    """三連単確率 210 を三連複 35 へ畳む（＝三者同時確率）。"""
    v = _TRIO_PROB_CACHE.get(i)
    if v is None:
        p = Z["PROB"][i]
        v = np.zeros(35)
        for j, c in enumerate(C.CANON):
            v[C.C3IDX[frozenset(c)]] += p[j]
        _TRIO_PROB_CACHE[i] = v
    return v


def p3_order(i: int) -> list[int]:
    return _P3_ORDER[i].tolist()


def axes(i: int) -> tuple[int, int]:
    o = _P3_ORDER[i]
    return int(o[0]), int(o[1])


def days(window: str) -> int:
    return C.days_of(C.select(None, window))


def idx(window: str, agree: bool | None = None) -> np.ndarray:
    return C.select(TYPE, window, agree=agree)


# ───────────────────────── 相手の並べ方 ─────────────────────────

def relay_orders(i: int, a1: int, a2: int) -> dict[str, list[int]]:
    """軸2車を除く5車を、いろいろな基準で並べる。"""
    z = Z
    rest = [c for c in p3_order(i) if c not in (a1, a2)]      # p3 降順（全体3〜7番手）
    tp = trio_prob(i)
    po = z["TRIO_PO"][i]

    def key_joint(c):
        return -tp[C.C3IDX[frozenset((a1, a2, c))]]

    def key_ev(c):
        j = C.C3IDX[frozenset((a1, a2, c))]
        return -(tp[j] * po[j] if np.isfinite(po[j]) and po[j] > 0 else 0.0)

    def key_po(c):
        j = C.C3IDX[frozenset((a1, a2, c))]
        return -(po[j] if np.isfinite(po[j]) else 0.0)

    return {
        "p3降順": rest,                                   # 3,4,5,6,7番手
        "p3昇順": rest[::-1],                             # 7,6,5,4,3番手
        "5番手から": [rest[2], rest[3], rest[4], rest[0], rest[1]],
        "同時確率順": sorted(rest, key=key_joint),
        "EV順": sorted(rest, key=key_ev),
        "予測オッズ順": sorted(rest, key=key_po),
    }


# ───────────────────────── 腕: 三連複 ─────────────────────────

def run_trio(window: str, k: int, order: str, tilt: bool = True,
             agree: bool | None = None) -> dict:
    z = Z
    recs = []
    for i in idx(window, agree):
        a1, a2 = axes(int(i))
        rest = relay_orders(int(i), a1, a2)[order][:k]
        combos = [frozenset((a1, a2, c)) for c in rest]
        st = C.trio_stakes(int(i), combos, tilt=tilt)
        if st is None or not C.trio_gate(int(i), st):
            continue
        inv, pay, mean = C.trio_result(int(i), st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


def run_trio_free(window: str, k: int, tilt: bool = True, order: str = "prob",
                  agree: bool | None = None, min_po: float = 0.0) -> dict:
    """軸2車固定をやめ、三連複35点から直接 k 点選ぶ。"""
    z = Z
    recs = []
    for i in idx(window, agree):
        i = int(i)
        tp, po = trio_prob(i), z["TRIO_PO"][i]
        ok = [j for j in range(35) if np.isfinite(po[j]) and po[j] >= max(min_po, 0.01)]
        if len(ok) < k:
            continue
        if order == "prob":
            ok.sort(key=lambda j: -tp[j])
        elif order == "ev":
            ok.sort(key=lambda j: -tp[j] * po[j])
        combos = [frozenset(C.CANON3[j]) for j in ok[:k]]
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


def run_trio_axis_band(window: str, k: int, min_po: float = 10.0,
                       tilt: bool = True, agree: bool | None = None) -> list[dict]:
    """軸2車固定のまま、相手を「予測 min_po 倍以上」に絞って同時確率順に k 点。
    自由選択（run_trio_free）と点数・帯を揃えた直接対決用。"""
    z = Z
    recs = []
    for i in idx(window, agree):
        i = int(i)
        a1, a2 = axes(i)
        tp, po = trio_prob(i), z["TRIO_PO"][i]
        cand = []
        for c in p3_order(i):
            if c in (a1, a2):
                continue
            j = C.C3IDX[frozenset((a1, a2, c))]
            if np.isfinite(po[j]) and po[j] >= min_po:
                cand.append((tp[j], c))
        if len(cand) < k:
            continue
        cand.sort(key=lambda t: -t[0])
        combos = [frozenset((a1, a2, c)) for _, c in cand[:k]]
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


# ───────────────────────── 腕: 三連単 ─────────────────────────

def run_tf(window: str, k: int, order: str = "prob", lo: float = 0.0, hi: float = 1e9,
           agree: bool | None = None, axis1_fix: bool = False) -> dict:
    """三連単 k 点。lo/hi は**予測**オッズの帯。"""
    z = Z
    recs = []
    for i in idx(window, agree):
        i = int(i)
        p, po = z["PROB"][i], z["PO"][i]
        a1, _ = axes(i)
        cand = []
        for j, c in enumerate(C.CANON):
            v = po[j]
            if not np.isfinite(v) or v <= 0 or not (lo <= v <= hi):
                continue
            if axis1_fix and a1 not in c:
                continue
            cand.append(j)
        if len(cand) < k:
            continue
        if order == "prob":
            cand.sort(key=lambda j: -p[j])
        elif order == "ev":
            cand.sort(key=lambda j: -p[j] * po[j])
        combos = [C.CANON[j] for j in cand[:k]]
        st = C.tf_stakes(i, combos)
        if not C.tf_gate(i, st):
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] * float(po[C.CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


def run_tf_form(window: str, n2: int = 2, n3: int = 5, tilt: bool = False,
                agree: bool | None = None, lo: float = 0.0) -> dict:
    """1着=軸1固定 × 2着=p3の2〜(1+n2)位 × 3着=残り総流し(n3点まで)。"""
    z = Z
    recs = []
    for i in idx(window, agree):
        i = int(i)
        o = p3_order(i)
        a1, seconds = o[0], o[1:1 + n2]
        p, po = z["PROB"][i], z["PO"][i]
        combos = []
        for b in seconds:
            thirds = [c for c in o if c not in (a1, b)][:n3]
            for t in thirds:
                combos.append((a1, b, t))
        combos = [c for c in combos if np.isfinite(po[C.CIDX[c]]) and po[C.CIDX[c]] >= lo]
        if not combos:
            continue
        if tilt:
            w = [p[C.CIDX[c]] for c in combos]
            tot = sum(w)
            n_units = C.BUDGET // C.UNIT
            if tot <= 0 or n_units < len(combos):
                continue
            units = [1] * len(combos)
            rest = n_units - len(combos)
            for j, x in enumerate(w):
                units[j] += int(rest * x / tot)
            st = {c: u * C.UNIT for c, u in zip(combos, units)}
        else:
            st = C.tf_stakes(i, combos)
        if not C.tf_gate(i, st):
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] * float(po[C.CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


# ───────────────────────── 出力 ─────────────────────────

WALL = 74.85   # 控除率の壁（三連複・三連単の払戻率 75% 相当）


def table(title: str, arms: list[tuple[str, dict, dict]]) -> None:
    print(f"\n### {title}")
    print(C.HEAD)
    for name, se, sc in arms:
        print(C.line(f"探 {name}", se))
        print(C.line(f"確 {name}", sc))
        print()


def both(fn, name, **kw):
    return (name, C.summarize(fn("explore", **kw), days("explore")),
            C.summarize(fn("confirm", **kw), days("confirm")))


# ───────────────────────── 区間推定（レース単位ブートストラップ） ─────────────────────────

def boot(recs: list[dict], n_boot: int = 2000) -> dict:
    """ROI と表示的中の 95% 区間。レース単位で復元抽出する。"""
    if not recs:
        return {}
    rng = np.random.default_rng(20260827)
    inv = np.array([r["inv"] for r in recs])
    pay = np.array([r["pay"] for r in recs])
    shown = np.array([1.0 if (r["pay"] > r["inv"]) else 0.0 for r in recs])
    n = len(recs)
    ii = rng.integers(0, n, size=(n_boot, n))
    roi = pay[ii].sum(1) / inv[ii].sum(1) * 100
    sh = shown[ii].mean(1) * 100
    return dict(roi_lo=np.percentile(roi, 2.5), roi_hi=np.percentile(roi, 97.5),
                sh_lo=np.percentile(sh, 2.5), sh_hi=np.percentile(sh, 97.5))


# ───────────────────────── 実行 ─────────────────────────

def section_baseline():
    print("\n" + "=" * 100)
    print("§0 型Fの姿と現行相当のベースライン")
    z = Z
    for w in WINDOWS:
        ii = idx(w)
        n2 = 0
        for i in ii:
            a1, a2 = axes(int(i))
            wn = set(C.CANON3[int(z["TRIO_WIN"][i])])
            n2 += ({a1, a2} <= wn)
        print(f"  {w}: {len(ii)}R / {days(w)}日 / {len(ii)/days(w):.2f}件日"
              f" 二軸そろい {n2/len(ii)*100:.1f}%"
              f" 確定三連複中央 {np.median(z['TRIO_PAY'][ii]):.1f}倍"
              f" 印一致 {len(idx(w, True))/len(ii)*100:.1f}%")

    arms = [
        both(run_trio, "7S相当 軸2+総流し5点", k=5, order="p3降順"),
        both(run_trio, "7M1相当 軸2+下位3点", k=3, order="p3昇順"),
        both(run_trio, "7S相当 印不一致のみ", k=5, order="p3降順", agree=False),
        both(run_trio, "7M1相当 印不一致のみ", k=3, order="p3昇順", agree=False),
    ]
    table("現行が型Fで売っている形（本台での再現）", arms)


def section_trio():
    print("\n" + "=" * 100)
    print("§1 三連複 軸2車 + 相手k点（相手の並べ方 × 点数）")
    for order in ("p3降順", "p3昇順", "5番手から", "同時確率順", "EV順", "予測オッズ順"):
        arms = [both(run_trio, f"{order} k={k}", k=k, order=order) for k in (1, 2, 3, 5)]
        table(f"相手={order}", arms)

    print("\n" + "=" * 100)
    print("§2 賭け金 ダッチ vs 均等")
    arms = []
    for k in (2, 3, 5):
        arms.append(both(run_trio, f"同時確率順 k={k} ダッチ", k=k, order="同時確率順", tilt=True))
        arms.append(both(run_trio, f"同時確率順 k={k} 均等", k=k, order="同時確率順", tilt=False))
    table("配分の比較", arms)

    print("\n" + "=" * 100)
    print("§3 軸2車固定をやめる（三連複35点から直接選ぶ）")
    arms = []
    for k in (1, 2, 3, 5):
        arms.append(both(run_trio_free, f"確率上位 k={k}", k=k, order="prob"))
    for k in (2, 3, 5):
        arms.append(both(run_trio_free, f"EV上位 k={k}", k=k, order="ev"))
    for k in (2, 3):
        arms.append(both(run_trio_free, f"確率上位 k={k} 予測20倍+", k=k, order="prob", min_po=20))
    table("軸固定なし", arms)

    print("\n" + "=" * 100)
    print("§4 印一致 / 不一致で割る（現行は不一致しか売っていない）")
    arms = []
    for ag, lb in ((True, "印一致"), (False, "印不一致")):
        arms.append(both(run_trio, f"{lb} 同時確率順 k=1", k=1, order="同時確率順", agree=ag))
        arms.append(both(run_trio, f"{lb} 同時確率順 k=2", k=2, order="同時確率順", agree=ag))
        arms.append(both(run_trio, f"{lb} 同時確率順 k=3", k=3, order="同時確率順", agree=ag))
    table("印での分割", arms)


def section_tf():
    print("\n" + "=" * 100)
    print("§5 三連単 確率上位k点（帯なし）")
    arms = [both(run_tf, f"確率上位 k={k}", k=k) for k in (1, 2, 3, 5, 10)]
    table("PROB上位", arms)

    print("\n" + "=" * 100)
    print("§6 三連単 予測オッズ帯 × 確率上位k点")
    for lo, hi, lb in ((20, 1e9, "20倍+"), (30, 1e9, "30倍+"), (50, 1e9, "50倍+"),
                       (100, 1e9, "100倍+"), (30, 100, "30-100倍"), (50, 200, "50-200倍")):
        arms = [both(run_tf, f"{lb} k={k}", k=k, lo=lo, hi=hi) for k in (2, 3, 5, 10)]
        table(f"帯={lb}", arms)

    print("\n" + "=" * 100)
    print("§7 三連単 EV順 / 軸1を含む目に限定")
    arms = []
    for k in (3, 5, 10):
        arms.append(both(run_tf, f"EV順 k={k}", k=k, order="ev"))
    for k in (3, 5, 10):
        arms.append(both(run_tf, f"軸1絡み 確率上位 k={k}", k=k, axis1_fix=True))
    for k in (5, 10):
        arms.append(both(run_tf, f"軸1絡み 30倍+ k={k}", k=k, lo=30, axis1_fix=True))
    table("EV順・軸1絡み", arms)

    print("\n" + "=" * 100)
    print("§8 三連単 1軸固定 + 2軸目n点 + 3着流し")
    arms = []
    for n2 in (1, 2, 3):
        for n3 in (3, 5):
            arms.append(both(run_tf_form, f"1着軸1 2着{n2}車 3着{n3}車 均等",
                             n2=n2, n3=n3, tilt=False))
    for n2 in (2, 3):
        arms.append(both(run_tf_form, f"1着軸1 2着{n2}車 3着5車 強弱",
                         n2=n2, n3=5, tilt=True))
    for n2 in (2,):
        arms.append(both(run_tf_form, f"1着軸1 2着{n2}車 3着5車 30倍+",
                         n2=n2, n3=5, tilt=False, lo=30))
    table("フォーメーション", arms)


# ───────────────────────── §9 以降 ─────────────────────────

FINALISTS = [
    ("三連複 10倍+ 3点", run_trio_free, dict(k=3, order="prob", min_po=10)),
    ("三連複 10倍+ 4点", run_trio_free, dict(k=4, order="prob", min_po=10)),
    ("三連複 10倍+ 5点", run_trio_free, dict(k=5, order="prob", min_po=10)),
    ("三連複 15倍+ 5点", run_trio_free, dict(k=5, order="prob", min_po=15)),
    ("三連複 20倍+ 5点", run_trio_free, dict(k=5, order="prob", min_po=20)),
    ("TF 100倍+ 3点", run_tf, dict(k=3, lo=100)),
    ("TF 100倍+ 5点", run_tf, dict(k=5, lo=100)),
    ("TF 50倍+ 5点", run_tf, dict(k=5, lo=50)),
    ("TF 50倍+ 10点", run_tf, dict(k=10, lo=50)),
    ("TF 30倍+ 10点", run_tf, dict(k=10, lo=30)),
    ("TF 確率上位10点", run_tf, dict(k=10)),
    ("三連複 同時確率2点", run_trio, dict(k=2, order="同時確率順")),
    ("三連複 p3降順3点", run_trio, dict(k=3, order="p3降順")),
    ("三連複 20倍+3点", run_trio_free, dict(k=3, order="prob", min_po=20)),
    ("三連複 EV順3点(軸2)", run_trio, dict(k=3, order="EV順")),
    ("現行7S相当 5点", run_trio, dict(k=5, order="p3降順")),
    ("現行7M1相当 3点", run_trio, dict(k=3, order="p3昇順")),
]


def section_ci():
    print("\n" + "=" * 100)
    print("§9 有力腕の区間推定（レース単位ブートストラップ・壁 74.85%）")
    print(f"  {'腕':26s} {'窓':4s} {'件/日':>6s} {'表示的中%':>9s} {'[95%CI]':>16s}"
          f" {'ROI%':>7s} {'[95%CI]':>18s} {'10万+/日':>8s} {'平均払戻中央':>11s}")
    for name, fn, kw in FINALISTS:
        for w, lb in (("explore", "探"), ("confirm", "確")):
            recs = fn(w, **kw)
            s_ = C.summarize(recs, days(w))
            b = boot(recs)
            if not s_.get("n"):
                continue
            print(f"  {name:26s} {lb:4s} {s_['perday']:6.2f} {s_['shown']:9.2f}"
                  f" [{b['sh_lo']:6.2f},{b['sh_hi']:6.2f}]"
                  f" {s_['roi']:7.1f} [{b['roi_lo']:7.1f},{b['roi_hi']:7.1f}]"
                  f" {s_['big_per_day']:8.2f} {s_['med_mean']:11,.0f}")
        print()


def section_tf_fine():
    print("\n" + "=" * 100)
    print("§10 三連単 帯 × 点数の細掃引（点数を減らして1点あたりを増やす方向）")
    for lo in (30, 50, 80, 100, 150, 200):
        arms = [both(run_tf, f"{lo}倍+ k={k}", k=k, lo=lo) for k in (1, 2, 3, 5, 7, 10, 15)]
        table(f"帯={lo}倍+", arms)


def run_tf_sel(window: str, k: int, lo: float, sel: str) -> list[dict]:
    """三連単 帯×k点 に、レース選別 sel を掛ける。"""
    z = Z
    recs = []
    for i in idx(window):
        i = int(i)
        if not _sel_ok(i, sel):
            continue
        p, po = z["PROB"][i], z["PO"][i]
        cand = [j for j in range(210)
                if np.isfinite(po[j]) and po[j] >= lo]
        if len(cand) < k:
            continue
        cand.sort(key=lambda j: -p[j])
        combos = [C.CANON[j] for j in cand[:k]]
        st = C.tf_stakes(i, combos)
        if not C.tf_gate(i, st):
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] * float(po[C.CIDX[c]]) for c in st) / len(st)
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return recs


def _sel_ok(i: int, sel: str) -> bool:
    z = Z
    if sel == "all":
        return True
    if sel == "印一致":
        return bool(z["AGREE"][i])
    if sel == "印不一致":
        return not bool(z["AGREE"][i])
    if sel.startswith("荒れ"):
        return int(z["ARARE"][i]) >= int(sel[2:])
    if sel.startswith("軸<"):
        return float(z["AXIS_SUM"][i]) < float(sel[2:])
    if sel.startswith("軸>="):
        return float(z["AXIS_SUM"][i]) >= float(sel[3:])
    if sel.startswith("種別:"):
        return str(z["RTYPE"][i]) in sel[3:].split("|")
    if sel.startswith("級:"):
        return str(z["GRADE"][i]) == sel[2:]
    if sel.startswith("日目"):
        return int(z["DAYI"][i]) == int(sel[2:])
    if sel == "本命薄":
        return float(z["PW"][i].max()) < 0.30
    if sel == "本命厚":
        return float(z["PW"][i].max()) >= 0.30
    raise ValueError(sel)


def section_trio_band():
    print("\n" + "=" * 100)
    print("§12 三連複 予測オッズ帯 × 点数（軸固定なし・確率上位）")
    for mp in (10, 15, 20, 30, 50):
        arms = [both(run_trio_free, f"{mp}倍+ k={k}", k=k, order="prob", min_po=mp)
                for k in (1, 2, 3, 4, 5)]
        table(f"三連複 帯={mp}倍+", arms)


def section_select():
    print("\n" + "=" * 100)
    print("§11 レース選別を掛ける（土台は 三連単 50倍+ 5点 と 30倍+ 10点）")
    sels = ["all", "印一致", "印不一致", "荒れ2", "荒れ3", "軸<1.25", "軸>=1.25",
            "種別:予選|一般", "種別:決勝|準決勝|特選|選抜", "級:S級", "級:A級",
            "日目1", "日目2", "日目3", "本命薄", "本命厚"]
    for lo, k in ((50, 5), (30, 10), (100, 3)):
        arms = []
        for sel in sels:
            arms.append((f"{sel}",
                         C.summarize(run_tf_sel("explore", k, lo, sel), days("explore")),
                         C.summarize(run_tf_sel("confirm", k, lo, sel), days("confirm"))))
        table(f"三連単 {lo}倍+ {k}点 × レース選別", arms)


def section_headtohead():
    print("\n" + "=" * 100)
    print("§14 同じ帯・同じ点数での直接対決（軸2車固定 ↔ 自由選択）")
    for mp in (10, 20):
        arms = []
        for k in (2, 3, 4, 5):
            arms.append(both(run_trio_axis_band, f"{mp}倍+ 軸2固定 k={k}", k=k, min_po=mp))
            arms.append(both(run_trio_free, f"{mp}倍+ 自由 k={k}", k=k, order="prob", min_po=mp))
        table(f"帯={mp}倍+", arms)


def section_stability():
    print("\n" + "=" * 100)
    print("§15 採用候補の安定性（四半期別・閾値の感度）")
    print("\n  ── 三連複 10倍+ 4点 の四半期別（全期間・確認窓は2026Q1以降）")
    recs = run_trio_free("explore", k=4, order="prob", min_po=10) + \
        run_trio_free("confirm", k=4, order="prob", min_po=10)
    q: dict[str, list[dict]] = {}
    for r in recs:
        y, m = r["date"][:4], int(r["date"][5:7])
        q.setdefault(f"{y}Q{(m - 1) // 3 + 1}", []).append(r)
    print(f"  {'四半期':8s} {'件':>6s} {'表示的中%':>9s} {'払戻中央':>9s} {'2倍+件':>7s} {'ROI%':>7s}")
    for k_ in sorted(q):
        s_ = C.summarize(q[k_])
        print(f"  {k_:8s} {s_['n']:6d} {s_['shown']:9.2f} {s_['med_pay']:9,.0f}"
              f" {s_['two_per_day'] * len({r['date'] for r in q[k_]}):7.0f} {s_['roi']:7.1f}")

    print("\n  ── 帯の感度（点数=4・確率上位）")
    arms = [both(run_trio_free, f"{mp}倍+ 4点", k=4, order="prob", min_po=mp)
            for mp in (6, 8, 10, 12, 15)]
    table("下限の掃引", arms)

    print("\n  ── 配分（ダッチ ↔ 均等）")
    arms = [both(run_trio_free, "10倍+ 4点 ダッチ", k=4, order="prob", min_po=10, tilt=True),
            both(run_trio_free, "10倍+ 4点 均等", k=4, order="prob", min_po=10, tilt=False),
            both(run_trio_free, "10倍+ 5点 ダッチ", k=5, order="prob", min_po=10, tilt=True),
            both(run_trio_free, "10倍+ 5点 均等", k=5, order="prob", min_po=10, tilt=False)]
    table("配分", arms)


def section_cap():
    print("\n" + "=" * 100)
    print("§16 件数を絞れるか（日次上限・順序を変えて）")

    def run(window, cap, key):
        base = {}
        for i in idx(window):
            i = int(i)
            tp, po = trio_prob(i), Z["TRIO_PO"][i]
            ok = [j for j in range(35) if np.isfinite(po[j]) and po[j] >= 10]
            if len(ok) < 4:
                continue
            ok.sort(key=lambda j: -tp[j])
            combos = [frozenset(C.CANON3[j]) for j in ok[:4]]
            st = C.trio_stakes(i, combos, tilt=True)
            if st is None or not C.trio_gate(i, st):
                continue
            inv, pay, mean = C.trio_result(i, st)
            sc = (sum(tp[C.C3IDX[c]] for c in combos) if key == "確率"
                  else mean if key == "想定払戻"
                  else sum(tp[C.C3IDX[c]] * float(po[C.C3IDX[c]]) for c in combos))
            base.setdefault(str(Z["DATE"][i]), []).append(
                (sc, dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=4)))
        recs = []
        for d, rows in base.items():
            rows.sort(key=lambda t: -t[0])
            recs += [r for _, r in rows[:cap]]
        return recs

    arms = []
    for key in ("確率", "想定払戻", "EV"):
        for cap in (3, 5, 8):
            arms.append((f"{key}上位 {cap}件/日",
                         C.summarize(run("explore", cap, key), days("explore")),
                         C.summarize(run("confirm", cap, key), days("confirm"))))
    arms.append(("上限なし",
                 C.summarize(run("explore", 999, "確率"), days("explore")),
                 C.summarize(run("confirm", 999, "確率"), days("confirm"))))
    table("三連複 10倍+ 4点 に日次上限", arms)


def section_diag():
    """🔴 型Fの中心的な問い: 軸2車固定は本当に足枷か（ゲートを外した素の比較）。"""
    print("\n" + "=" * 100)
    print("§13 軸2車固定の天井（入稿ゲートを外した素の的中率）")
    print(f"  {'窓':4s} {'二軸そろい':>9s} {'軸2固定5点':>10s} {'自由5点':>8s} {'自由10点':>9s}"
          f" {'自由5点に軸2両方':>13s} {'自由5点の平均軸数':>15s}")
    for w in WINDOWS:
        ii = idx(w)
        n = len(ii)
        both2 = fix5 = free5 = free10 = has2 = 0
        naxis = 0.0
        for i in ii:
            i = int(i)
            a1, a2 = axes(i)
            wn = frozenset(C.CANON3[int(Z["TRIO_WIN"][i])])
            both2 += ({a1, a2} <= set(wn))
            rest = [c for c in p3_order(i) if c not in (a1, a2)]
            fix = [frozenset((a1, a2, c)) for c in rest]
            fix5 += (wn in fix)
            tp = trio_prob(i)
            top = np.argsort(-tp)
            f5 = [frozenset(C.CANON3[j]) for j in top[:5]]
            f10 = [frozenset(C.CANON3[j]) for j in top[:10]]
            free5 += (wn in f5)
            free10 += (wn in f10)
            has2 += sum(1 for c in f5 if {a1, a2} <= set(c)) == 5
            naxis += sum(len({a1, a2} & set(c)) for c in f5) / 5
        print(f"  {w:4s} {both2/n*100:9.2f} {fix5/n*100:10.2f} {free5/n*100:8.2f}"
              f" {free10/n*100:9.2f} {has2/n*100:13.2f} {naxis/n:15.2f}")

    print("\n  ── 10倍+ 帯を掛けたときの買い目の中身（確認窓）")
    ii = idx("confirm")
    cnt = {0: 0, 1: 0, 2: 0}
    tot = 0
    for i in ii:
        i = int(i)
        a1, a2 = axes(i)
        tp, po = trio_prob(i), Z["TRIO_PO"][i]
        ok = [j for j in range(35) if np.isfinite(po[j]) and po[j] >= 10]
        ok.sort(key=lambda j: -tp[j])
        for j in ok[:4]:
            cnt[len({a1, a2} & set(C.CANON3[j]))] += 1
            tot += 1
    print(f"    買い目 {tot} 点のうち 軸2車を 0車含む {cnt[0]/tot*100:.1f}% /"
          f" 1車 {cnt[1]/tot*100:.1f}% / 2車 {cnt[2]/tot*100:.1f}%")


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "base"):
        section_baseline()
    if what in ("all", "trio"):
        section_trio()
    if what in ("all", "tf"):
        section_tf()
    if what in ("all", "ci"):
        section_ci()
    if what in ("all", "fine"):
        section_tf_fine()
    if what in ("all", "cap"):
        section_cap()
    if what in ("all", "stab"):
        section_stability()
    if what in ("all", "h2h"):
        section_headtohead()
    if what in ("all", "diag"):
        section_diag()
    if what in ("all", "tband"):
        section_trio_band()
    if what in ("all", "sel"):
        section_select()


if __name__ == "__main__":
    main()
