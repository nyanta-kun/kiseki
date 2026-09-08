#!/usr/bin/env python3
"""型F（F_hit）にも「帯の下から1点」は要るか（2026-09-08・ユーザー質問）。

## 発端

型C で帯15倍の下から最人気の1点を買い足す形を採用した（PR #508 /
`docs/type_lab/type_c.md` 11章）。

> F_hit も同じ問題がありますか？ それとも F は波乱想定のため対応しない方がいいですか？

## 構造は型Cと**逆**（先に読むこと）

| | 帯 | ゲート（平均想定払戻2万）に落ちるか |
|---|---|---|
| `C_hit` | **常に15倍** | 差込を入れたときだけ落ちる（`GATE_FALLBACK` で差込なしへ） |
| `F_hit` | **なし**（本命は素通し） | **本命が 24% 落ちる**（`GATE_FALLBACK` で帯15倍12点へ） |

つまり F_hit は **76% のレースでそもそも帯を持たない**（安い目も買っている）。
帯が効くのはゲートに落ちた 24% の側だけ。「帯が順当な目を切っている」問題が
起こりうるのはそこだけなので、**まず branch 別に切り分けてから測る**。

## 作法

- 母集団は 型F × 7車 × 軸信頼ゲート（`AXIS_GATE_MIN["F_hit"]=1.230`）。
  `typef_band.py` と同じ。実際に売る母集団は看板枠（決勝・準決勝系）が抜けるので
  `--no-sign` 相当の分割も併記する
- 本番の入口（`build_with_gate_fallback`）で組む
- 判断は表示的中。ROI では決めない。件数を動かす案には無作為対照を置く

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_f_underband.py diag
      diag   branch 別の姿と「帯で切った目が決着した」割合
      arms   差込の腕（対照つき）
      conc   帯が無い本命側の賭け金の集中（逆向きの問い）
      floor  本命側に安すぎる目の下限（min_odds）を入れる掃引
      combo  ①代替への差込 × ②本命の下限 の組み合わせ
      verify 本番の PLANS / GATE_FALLBACK で採用値が再現するか
"""
from __future__ import annotations

import random
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import common as C  # noqa: E402
from typef_racetype import ctx, AXIS_GATE_MIN  # noqa: E402
from src.type_lab import (  # noqa: E402
    GATE_FALLBACK, PLANS, SIGNBOARD_RACE_TYPES, build_with_gate_fallback,
    mean_expected_payout,
)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
WIN = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))
#: 🔴 **この台の「現行」は 2026-09-08 の実装*前*の型F**（本命＝帯なし12点 /
#:    代替＝帯15倍12点の1段）。出荷後に再実行しても記録した数字が出るよう、
#:    本番の `PLANS` を読まずに明示的に組み直している。
#:    本番の定義そのままで測るのは `verify` セクション。
CUR = replace(PLANS["F_hit"], min_odds=0.0, underband_min=0.0,
              note="実装前の本命（帯なし12点）")
FB = replace(GATE_FALLBACK["F_hit"][-1], underband_min=0.0,
             note="実装前の代替（帯15倍12点・差込なし）")


@contextmanager
def fallback(fbs):
    """`GATE_FALLBACK["F_hit"]` を差し替える。`None` なら本番のまま。"""
    import src.type_lab as T
    orig = T.GATE_FALLBACK["F_hit"]
    if fbs is not None:
        T.GATE_FALLBACK["F_hit"] = tuple(fbs)
    try:
        yield
    finally:
        T.GATE_FALLBACK["F_hit"] = orig


def pop(win: str, sold_only: bool = False) -> list:
    """型F・7車・軸信頼ゲート通過。`sold_only` なら看板枠の種別を外す。"""
    z = C.board()
    idx = C.select("F", win)
    idx = idx[np.array([float(z["AXIS_SUM"][i]) >= AXIS_GATE_MIN["F_hit"] for i in idx])]
    if sold_only:
        rt = np.array([str(z["RTYPE"][i]) for i in idx])
        idx = idx[~np.isin(rt, list(SIGNBOARD_RACE_TYPES))]
    return [x for i in idx if (x := ctx(int(i))) is not None]


def sold(x, plan, ins: float = 0.0, fbs=(None,)):
    """本番の入口で組み、入稿ゲートを通ったものだけ返す。

    `fbs` の既定 `(None,)` は「呼び出し側が `fallback()` で決める」の意味。
    """
    pl = replace(plan, underband_min=ins) if ins else plan
    got = build_with_gate_fallback(x.shape, pl, x.po_tf, x.pr_tf, n_entries=7,
                                   min_mean_payout=MIN_MEAN_PAYOUT)
    if not got:
        return None
    legs, st, used = got
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    return legs, st, used


def rec_of(x, st) -> dict:
    pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, x.po_tf))


# ───────────────────────── 診断 ─────────────────────────

def diag() -> None:
    for label, win in WIN:
        xs = pop(win)
        nd = C.days_of(C.select(None, win))
        rows = {"本命（帯なし12点）": [], "代替（帯15倍12点）": []}
        cut, cut_odds, miss = 0, [], 0
        n_fb = 0
        for x in xs:
            got = sold(x, CUR)
            if not got:
                continue
            legs, st, used = got
            fb = used is FB
            n_fb += fb
            rows["代替（帯15倍12点）" if fb else "本命（帯なし12点）"].append(rec_of(x, st))
            if x.win_tf in st:
                continue
            miss += 1
            o = x.po_tf.get(x.win_tf)
            if fb and o is not None and float(o) < FB.min_odds:
                cut += 1
                cut_odds.append(float(o))
        tot = sum(len(v) for v in rows.values())
        print(f"\n===== 型F {label}  入稿ゲート通過 {tot:,}R / {nd}日 =====")
        print(C.HEAD)
        for nm, recs in rows.items():
            print(C.line(nm, C.summarize(recs, nd)))
        print(f"  代替（帯15倍）へ落ちた割合      {n_fb/tot*100:5.1f}%  ({n_fb:,}R)")
        print(f"  外した（買い目に無かった）      {miss/tot*100:5.1f}%  ({miss:,}R)")
        print(f"  🔴 うち帯15倍未満で切っていた   {cut/tot*100:5.2f}%  ({cut:,}R)"
              f"  ← 型Cは 29.7 / 26.9%")
        if cut_odds:
            print(f"     切った目の予測オッズ 中央 {median(cut_odds):.1f}倍")


# ───────────────────────── 腕 ─────────────────────────

def _fb_arm(x, how: str, rnd=None, lo: float = 5.0):
    """本命がゲートに落ちた（＝代替＝帯15倍）レースだけ、帯下の1点を差し込む。

    🔴 **本命（帯なし12点）は触らない。** そちらに帯は無いので「帯が切った」問題が
       そもそも無く、差し込む対象も定義できない。
    🔴 差込の実装は本番と同じ `_insert_underband` を通す（`how="cheap"` のとき）。
       対照（無作為）だけ選び方を差し替える。
    """
    from src.type_lab import allocate, alloc_fallback, build_legs, _insert_underband
    with fallback((FB,)):
        got = sold(x, CUR)
    if not got:
        return None
    legs, st, used = got
    if used is not FB:
        return rec_of(x, st)                       # 本命側はそのまま
    base = build_legs(x.shape, FB, x.po_tf, x.pr_tf)
    if not base:
        return rec_of(x, st)
    base = list(base)
    if how == "cheap":
        legs2 = _insert_underband(base, replace(FB, underband_min=lo), x.po_tf)
    else:
        cand = [k for k, v in x.po_tf.items()
                if v and len(set(k)) == 3 and lo <= float(v) < FB.min_odds
                and k not in base]
        legs2 = ([rnd.choice(cand)] + base[:len(base) - 1]) if cand else base
    if list(legs2) == base:
        return rec_of(x, st)
    st2 = allocate(legs2, x.po_tf, x.pr_tf, FB)
    if not st2:
        f2 = alloc_fallback(FB)
        st2 = allocate(legs2, x.po_tf, x.pr_tf, f2) if f2 else None
    # 🔴 差し込んで入稿ゲートを割るなら差込なしへ戻す（型C と同じ形）
    if not st2 or mean_expected_payout(st2, x.po_tf) <= MIN_MEAN_PAYOUT:
        return rec_of(x, st)
    if min(float(x.po_tf[c]) for c in st2) < MIN_POINT_ODDS:
        return rec_of(x, st)
    return rec_of(x, st2)


def arms() -> None:
    for sold_only in (False, True):
        tag = "売る母集団（看板枠の種別を除く）" if sold_only else "型F 全体（看板枠込み）"
        for label, win in WIN:
            xs = pop(win, sold_only)
            nd = C.days_of(C.select(None, win))
            print(f"\n===== {tag} / {label}  {len(xs):,}R / {nd}日 =====")
            print(C.HEAD + "   10万+/日")
            cur, ins = {}, {}
            for x in xs:
                g = sold(x, CUR)
                cur[id(x)] = rec_of(x, g[1]) if g else None
                ins[id(x)] = _fb_arm(x, "cheap")
            sa = C.summarize([r for r in cur.values() if r], nd)
            sb = C.summarize([r for r in ins.values() if r], nd)
            print(C.line("現行 F_hit", sa) + f" {sa.get('big_per_day', 0):9.3f}")
            print(C.line("差込 市場1位≧5倍（代替側のみ）", sb)
                  + f" {sb.get('big_per_day', 0):9.3f}")
            pairs = [(1.0 if cur[k]["pay"] > cur[k]["inv"] else 0.0,
                      1.0 if ins[k]["pay"] > ins[k]["inv"] else 0.0)
                     for k in cur if cur.get(k) and ins.get(k)]
            fired = sum(1 for k in cur if cur.get(k) and ins.get(k)
                        and cur[k]["mean"] != ins[k]["mean"])
            a = sum(p[0] for p in pairs) / len(pairs) * 100
            b = sum(p[1] for p in pairs) / len(pairs) * 100
            l2, h2 = _boot(pairs)
            print(f"      Δ表示的中 {a:5.2f} → {b:5.2f}  {b-a:+5.2f}pt"
                  f"  95%CI [{l2:+5.2f}, {h2:+5.2f}]  対応 {len(pairs):,}R"
                  f"  発火 {fired/len(pairs)*100:.1f}%")
            # 🔴 無作為対照（同じ経路・帯下から無作為1点・20 seed）
            shown = []
            for sd in range(20):
                rnd = random.Random(sd)
                recs = [r for x in xs if (r := _fb_arm(x, "rand", rnd))]
                shown.append(C.summarize(recs, nd)["shown"])
            shown.sort()
            print(f"  {'対照 無作為≧5倍(20seed)':26s} 表示的中 中央 {shown[10]:.2f}%"
                  f"  範囲 {shown[0]:.2f}〜{shown[-1]:.2f}%"
                  f"   （市場1位 {b:.2f}% が範囲の外なら本物）")


def conc() -> None:
    """🔴 逆向きの問い: 帯が無い本命側は、安い点に寄りすぎていないか。"""
    for label, win in WIN:
        xs = pop(win)
        sh, lows, mins = [], [], []
        for x in xs:
            got = sold(x, CUR)
            if not got or got[2] is FB:
                continue
            _, st, _ = got
            tot = sum(st.values())
            sh.append(max(st.values()) / tot)
            o = min(float(x.po_tf[c]) for c in st)
            lows.append(o)
            mins.append(min(st[c] * float(x.po_tf[c]) for c in st))
        q = lambda a, p: sorted(a)[min(int(len(a) * p), len(a) - 1)]
        print(f"\n=== 型F 本命（帯なし）{label}  {len(sh):,}R ===")
        print(f"  1点に乗る最大割合   中央 {q(sh,.5)*100:.1f}% / p95 {q(sh,.95)*100:.1f}%"
              f" / 最大 {max(sh)*100:.1f}%   （型C 差込点は 26 / 37 / 43%）")
        print(f"  買い目の最安 予測オッズ 中央 {q(lows,.5):.1f}倍 / p05 {q(lows,.05):.1f}"
              f" / 最小 {min(lows):.1f}倍")
        print(f"  商品内の最低想定払戻 中央 {q(mins,.5):,.0f}円 / p05 {q(mins,.05):,.0f}"
              f" / 最小 {min(mins):,.0f}円")


def _boot(pairs, n=2000, seed=0):
    rnd = random.Random(seed)
    m = len(pairs)
    out = []
    for _ in range(n):
        s = [pairs[rnd.randrange(m)] for _ in range(m)]
        out.append((sum(p[1] for p in s) - sum(p[0] for p in s)) / m * 100)
    out.sort()
    return out[int(n * 0.025)], out[int(n * 0.975)]


# ═══════════════════════════════════════════════════════════════════════════
# ② 本命（帯なし）側に**安すぎる目の下限**を入れるか（2026-09-08）
#
# `conc` の実測: F_hit は帯が無いので最安 3.5倍 の目まで買い、1点に最大 49% が乗る。
# 型C で `underband_min=5.0` を置いた懸念そのものが、こちらには下限なしで存在する。
#
# 🔴 **これは `min_odds`（買う目の下限）であって差込ではない。** 帯15倍への丸ごと
#    置換は 2026-09-03 に測って不採用（表示的中 −0.8pt）だが、3〜6倍の弱い下限は
#    別物なので測り直す。
# 🔴 下限を上げると平均想定払戻が上がり **ゲートを通りやすくなる**＝代替へ落ちる
#    割合が減る。母集団が動くので `fb%` を必ず併記する。
# ═══════════════════════════════════════════════════════════════════════════

PRIMARY_FLOORS = (0.0, 3.0, 4.0, 5.0, 6.0, 8.0)


def floor() -> None:
    for label, win in WIN:
        xs = pop(win, sold_only=True)
        nd = C.days_of(C.select(None, win))
        print(f"\n===== 型F 売る母集団 / {label}  {len(xs):,}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日   代替%  1点最大%  最安倍率")
        cur_pairs = None
        for lo in PRIMARY_FLOORS:
            pl = replace(CUR, min_odds=lo)
            recs, shares, lows, fb_n, seen = [], [], [], 0, 0
            hit = {}
            for x in xs:
                got = sold(x, pl)
                if not got:
                    continue
                legs, st, used = got
                seen += 1
                fb_n += used.min_odds == FB.min_odds and used is not pl
                tot = sum(st.values())
                shares.append(max(st.values()) / tot)
                lows.append(min(float(x.po_tf[c]) for c in st))
                r = rec_of(x, st)
                recs.append(r)
                hit[id(x)] = 1.0 if r["pay"] > r["inv"] else 0.0
            s = C.summarize(recs, nd)
            sh, lw = sorted(shares), sorted(lows)
            nm = "現行（下限なし）" if lo == 0 else f"下限{lo:.0f}倍"
            print(C.line(nm, s) + f" {s.get('big_per_day', 0):9.3f} {fb_n/max(seen,1)*100:6.1f}"
                  f" {sh[int(len(sh)*.95)]*100:8.0f}/{sh[-1]*100:.0f} {lw[0]:8.1f}")
            if lo == 0:
                cur_pairs = hit
            else:
                pr = [(cur_pairs[k], hit[k]) for k in hit if k in cur_pairs]
                a = sum(p[0] for p in pr) / len(pr) * 100
                b = sum(p[1] for p in pr) / len(pr) * 100
                l2, h2 = _boot(pr)
                print(f"      Δ表示的中 {a:5.2f} → {b:5.2f}  {b-a:+5.2f}pt"
                      f"  95%CI [{l2:+5.2f}, {h2:+5.2f}]  対応 {len(pr):,}R")


# ═══════════════════════════════════════════════════════════════════════════
# ①+② 組み合わせ（2026-09-08）
#
# 🔴 **足し算にならない。** ②（本命に下限5倍）は平均想定払戻を上げて
#    **代替へ落ちる割合を 23〜24% → 16〜17% に減らす**ので、①（代替側の差込）の
#    働く場所がそのぶん縮む。必ず4腕を同じ母集団で並べる。
# ═══════════════════════════════════════════════════════════════════════════

def combo() -> None:
    P5 = replace(CUR, min_odds=5.0)                    # ② 本命に下限5倍
    FB5 = replace(FB, underband_min=5.0)               # ① 代替に帯下1点の差込

    def run(x, primary, fbs):
        with fallback(fbs):
            return sold(x, primary)

    def run2(x, primary):
        """三段の連鎖: 本命 → 代替(帯15+差込) → 代替(帯15)。

        🔴 二段のままだと、差込を入れた代替がゲートを割ったとき三段目が無く
           **商品が丸ごと消える**（実測 件/日 −1.3〜2%）。型C が「在庫を1件も
           減らさない」ために `GATE_FALLBACK` を置いたのと同じ理由。
        """
        return run(x, primary, (FB5, FB))

    ARM = (("現行", CUR, (FB,)),
           ("① 代替に差込のみ", CUR, (FB5,)),
           ("② 本命に下限5倍のみ", P5, (FB,)),
           ("①+② 両方", P5, (FB5,)),
           ("① 三段連鎖", CUR, None),
           ("①+② 三段連鎖", P5, None))
    for label, win in WIN:
        xs = pop(win, sold_only=True)
        nd = C.days_of(C.select(None, win))
        print(f"\n===== 型F 売る母集団 / {label}  {len(xs):,}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日   代替%")
        hits = {}
        for nm, pl, fb in ARM:
            recs, h, fb_n, seen = [], {}, 0, 0
            for x in xs:
                got = run2(x, pl) if fb is None else run(x, pl, fb)
                if not got:
                    continue
                legs, st, used = got
                seen += 1
                fb_n += used.key == "F_hit" and used.min_odds == FB.min_odds and used is not pl
                r = rec_of(x, st)
                recs.append(r)
                h[id(x)] = 1.0 if r["pay"] > r["inv"] else 0.0
            hits[nm] = h
            s = C.summarize(recs, nd)
            print(C.line(nm, s) + f" {s.get('big_per_day', 0):9.3f} {fb_n/max(seen,1)*100:6.1f}")
        base = hits["現行"]
        for nm, _, _ in ARM[1:]:
            pr = [(base[k], hits[nm][k]) for k in hits[nm] if k in base]
            a = sum(p[0] for p in pr) / len(pr) * 100
            b = sum(p[1] for p in pr) / len(pr) * 100
            l2, h2 = _boot(pr)
            print(f"      {nm:20s} Δ表示的中 {b-a:+5.2f}pt  95%CI [{l2:+5.2f}, {h2:+5.2f}]"
                  f"  対応 {len(pr):,}R")


# ═══════════════════════════════════════════════════════════════════════════
# 出荷後の突き合わせ — **本番の PLANS / GATE_FALLBACK** で同じ数字が出るか
#
# 🔴 diag〜combo は `replace` で腕を組み立てている。実装したら本番の定義そのままで
#    測り直し、採用した数字が再現することを確かめる（型C の `verify` と同じ）。
# ═══════════════════════════════════════════════════════════════════════════

def verify() -> None:
    import src.type_lab as T
    BEFORE, FB_BEFORE = CUR, (FB,)                          # 実装前（この台の「現行」）
    for label, win in WIN:
        xs = pop(win, sold_only=True)
        nd = C.days_of(C.select(None, win))
        rows, hits = {}, {}
        # 🔴 **本番の腕は `PLANS["F_hit"]` を読む。** この module の `CUR` は
        #    「実装前」なので、そこを渡すと②（本命の下限5倍）が効かないまま
        #    「①だけ」を測ってしまう（2026-09-08 に実際に踏んだ）。
        for nm, pl, fbs in (("実装前", BEFORE, FB_BEFORE),
                            ("本番 PLANS/GATE_FALLBACK", PLANS["F_hit"], None)):
            orig = T.GATE_FALLBACK["F_hit"]
            if fbs is not None:
                T.GATE_FALLBACK["F_hit"] = fbs
            try:
                recs, h, step = [], {}, {0: 0, 1: 0, 2: 0}
                for x in xs:
                    got = sold(x, pl)
                    if not got:
                        continue
                    legs, st, used = got
                    step[0 if used is pl else
                         (1 if used is T.GATE_FALLBACK["F_hit"][0] else 2)] += 1
                    r = rec_of(x, st)
                    recs.append(r)
                    h[id(x)] = 1.0 if r["pay"] > r["inv"] else 0.0
            finally:
                T.GATE_FALLBACK["F_hit"] = orig
            rows[nm], hits[nm] = recs, h
            s = C.summarize(recs, nd)
            tot = sum(step.values())
            print("" if nm != "実装前" else
                  f"\n===== 型F 売る母集団 / {label}  {len(xs):,}R / {nd}日 =====\n" + C.HEAD,
                  end="" if nm != "実装前" else "\n")
            print(C.line(nm, s) + f"   本命 {step[0]/tot*100:.1f}%"
                  f" / 代替1段 {step[1]/tot*100:.1f}% / 代替2段 {step[2]/tot*100:.1f}%")
        pr = [(hits["実装前"][k], hits["本番 PLANS/GATE_FALLBACK"][k])
              for k in hits["実装前"] if k in hits["本番 PLANS/GATE_FALLBACK"]]
        a = sum(x[0] for x in pr) / len(pr) * 100
        b = sum(x[1] for x in pr) / len(pr) * 100
        l2, h2 = _boot(pr)
        print(f"      Δ表示的中 {a:5.2f} → {b:5.2f}  {b-a:+5.2f}pt"
              f"  95%CI [{l2:+5.2f}, {h2:+5.2f}]  対応 {len(pr):,}R")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diag"
    fn = {"diag": diag, "arms": arms, "conc": conc, "floor": floor,
          "combo": combo, "verify": verify}[cmd]
    # 🔴 **探索の腕は「実装前」の型F で回す。** 出荷後に再実行しても記録した数字が
    #    出るようにするため。`combo` は自前で並びを差し替え、`verify` は本番のまま測る。
    if cmd in ("diag", "arms", "conc", "floor"):
        with fallback((FB,)):
            fn()
    else:
        fn()
