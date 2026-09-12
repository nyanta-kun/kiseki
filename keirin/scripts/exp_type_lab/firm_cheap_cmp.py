#!/usr/bin/env python3
"""「順当かつ三連単が安いレースは丸ごと見送る」の比較（2026-09-11）。

台: `firm_cheap_build.py` → /tmp/firm_cheap_rows.pkl
計画: `docs/type_lab/PLAN_firm_cheap_skip_2026_09_11.md`

    python firm_cheap_cmp.py auc      §3.5 「順当に決まりそう」を朝の量で当てられるか
    python firm_cheap_cmp.py arms     §3 ①〜④（丸ごと見送り）
    python firm_cheap_cmp.py split    §3.5 ⑤〜⑦（安い層を2分する）
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

ROWS = pickle.load(open("/tmp/firm_cheap_rows.pkl", "rb"))
WINS = ("explore", "confirm")
RNG_SEEDS = range(20)


def sell(win: str) -> list[dict]:
    """入稿までの本番経路を通った商品（軸信頼ゲート込み）。"""
    return [r for r in ROWS if r["win"] == win and r["gate"] and r["axis_ok"]]


def ndays(win: str) -> int:
    return len({r["date"] for r in ROWS if r["win"] == win})


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """ROC AUC（同値は 0.5 で数える）。"""
    ok = np.isfinite(s)
    y, s = y[ok], s[ok]
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    r = np.argsort(np.argsort(s, kind="mergesort"), kind="mergesort") + 1.0
    # 同値の順位を平均へ
    o = np.argsort(s, kind="mergesort")
    ss = s[o]
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        if j > i:
            r[o[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    n1 = y.sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))


# ═══════════════════════════════ auc ═══════════════════════════════

def cmd_auc(thr: float = 3.0) -> None:
    print(f"\n### 「安い層（cheapA < {thr}倍）の中で順当に決まるか」を朝の量で当てられるか\n")
    print("  目的変数 exact123 = 決着が指数1・2・3位そのまま / jundo = 上位4車で決着かつ1,2位を含む\n")
    for win in WINS:
        rs = [r for r in sell(win) if np.isfinite(r["cheapA"]) and r["cheapA"] < thr]
        print(f"  -- {win}  n={len(rs):,} "
              f"(exact123 {np.mean([r['exact123'] for r in rs])*100:.2f}% / "
              f"jundo {np.mean([r['jundo'] for r in rs])*100:.2f}%) --")
        print(f"     {'量':10s} {'AUC(exact123)':>14s} {'AUC(jundo)':>11s}")
        for v in ("sig14", "sig12", "q_m3", "hs_trio", "sp5", "sp14",
                  "cheapA", "axis", "gap", "pw_ent"):
            s = np.array([float(r[v]) for r in rs])
            a1 = auc(np.array([r["exact123"] for r in rs], dtype=int), s)
            a2 = auc(np.array([r["jundo"] for r in rs], dtype=int), s)
            print(f"     {v:10s} {a1:14.3f} {a2:11.3f}")
    print("\n  🔴 AUC<0.5 は「小さいほど順当」の向き。見送る側の符号に注意すること。")


# ═══════════════════════════════ 集計 ═══════════════════════════════

def rep(name: str, rs: list[dict], nd: int, base: dict | None = None) -> dict:
    s = C.summarize(rs, nd)
    inv = sum(r["inv"] for r in rs) / nd
    d = ""
    if base:
        d = (f" | Δ表示 {s['shown']-base['shown']:+5.2f}pt"
             f"  Δ10万+ {s['big_per_day']-base['big']:+.3f}")
    print(f"  {name:30s} {s['perday']:6.2f}件/日 表示的中 {s['shown']:6.2f}%"
          f" 10万+/日 {s['big_per_day']:.3f} 払戻中央 {s['med_pay']:8,.0f}"
          f" 投資 {inv:8,.0f}円/日 ROI {s['roi']:5.1f}{d}")
    return dict(shown=s["shown"], big=s["big_per_day"], perday=s["perday"],
                roi=s["roi"], inv=inv, n=len(rs))


def boot_delta(a: list[dict], b: list[dict], nboot: int = 2000,
               seed: int = 0) -> tuple[float, float, float]:
    """Δ表示的中(b−a) のレース単位ブートストラップ 95%CI。

    🔴 **同じレース集合から再標本する**（腕ごとに別々に取ると対応が切れる）。
    ここでは a ⊇ b（b は a から落としたもの）なので、a の index で再標本して
    両腕を同時に作り直す。
    """
    ids = {r["i"] for r in b}
    arr = np.array([[float((r["pay"] > r["inv"])), float(r["i"] in ids),
                     float((r["pay"] > r["inv"]) and r["i"] in ids)] for r in a])
    rng = np.random.default_rng(seed)
    out = []
    n = len(arr)
    for _ in range(nboot):
        k = rng.integers(0, n, n)
        x = arr[k]
        na, nb = n, x[:, 1].sum()
        if nb < 1:
            continue
        out.append(x[:, 2].sum() / nb * 100 - x[:, 0].sum() / na * 100)
    o = np.percentile(out, [2.5, 97.5])
    return float(np.mean(out)), float(o[0]), float(o[1])


# ═══════════════════════════════ arms ═══════════════════════════════

def cmd_arms() -> None:
    for win in WINS:
        nd = ndays(win)
        rs = sell(win)
        print(f"\n════ {win}  入稿 {len(rs):,}件 / {nd}日 ════")
        base = rep("⓪ 現行", rs, nd)
        for thr in (2.5, 3.0, 3.5, 4.0):
            cheap = [r for r in rs if np.isfinite(r["cheapA"]) and r["cheapA"] < thr]
            print(f"\n  -- 安い層 cheapA<{thr}倍: {len(cheap):,}件 "
                  f"({len(cheap)/len(rs)*100:.2f}% / うち一撃 "
                  f"{sum(r['ichigeki'] for r in cheap)}件) --")
            drop1 = {r["i"] for r in cheap if not r["ichigeki"]}
            drop2 = {r["i"] for r in cheap}
            drop3 = {r["i"] for r in cheap if r["ichigeki"]}
            a1 = rep("① 当てにいく商品だけ見送り", [r for r in rs if r["i"] not in drop1], nd, base)
            a2 = rep("② 全部見送り（提案）", [r for r in rs if r["i"] not in drop2], nd, base)
            a3 = rep("③ 一撃商品だけ見送り", [r for r in rs if r["i"] not in drop3], nd, base)
            m, lo, hi = boot_delta(rs, [r for r in rs if r["i"] not in drop2])
            print(f"     ② Δ表示的中 95%CI [{lo:+.2f}, {hi:+.2f}] pt (平均 {m:+.2f})")
            # ④ 無作為対照
            wins_ = 0
            sh, bg = [], []
            for sd in RNG_SEEDS:
                rng = np.random.default_rng(1000 + sd)
                keep = rs[:]
                k = rng.choice(len(rs), len(rs) - len(drop2), replace=False)
                arm = [rs[j] for j in k]
                s = C.summarize(arm, nd)
                sh.append(s["shown"]); bg.append(s["big_per_day"])
                wins_ += (a2["shown"] > s["shown"])
            print(f"  ④ 無作為対照20seed 表示的中 中央 {np.median(sh):6.2f}%"
                  f" (範囲 {min(sh):.2f}〜{max(sh):.2f}) 10万+ 中央 {np.median(bg):.3f}"
                  f"  →  ②の勝敗 **{wins_}/20**")


# ═══════════════════════════════ split ═══════════════════════════════

def cmd_split(var: str = "sig14", thr: float = 3.0, frac: float = 0.5) -> None:
    print(f"\n### ⑤ 安い層（cheapA<{thr}倍）を `{var}` で2分し、"
          f"上位 {frac:.0%}（＝順当に決まりそう）だけ見送る\n")
    for win in WINS:
        nd = ndays(win)
        rs = sell(win)
        cheap = [r for r in rs if np.isfinite(r["cheapA"]) and r["cheapA"] < thr]
        v = np.array([float(r[var]) for r in cheap])
        ok = np.isfinite(v)
        cut = float(np.quantile(v[ok], 1 - frac))
        drop = {r["i"] for r, val in zip(cheap, v) if np.isfinite(val) and val >= cut}
        print(f"\n════ {win}  安い層 {len(cheap):,}件 / 見送り {len(drop):,}件"
              f"  ({var} >= {cut:.4f}) ════")
        base = rep("⓪ 現行", rs, nd)
        arm = [r for r in rs if r["i"] not in drop]
        a5 = rep(f"⑤ {var} 上位を見送り", arm, nd, base)
        m, lo, hi = boot_delta(rs, arm)
        print(f"     ⑤ Δ表示的中 95%CI [{lo:+.2f}, {hi:+.2f}] pt (平均 {m:+.2f})")
        # ⑦ 安い層から無作為に同数
        sh, bg = [], []
        w = 0
        for sd in RNG_SEEDS:
            rng = np.random.default_rng(2000 + sd)
            k = set(rng.choice([r["i"] for r in cheap], len(drop), replace=False).tolist())
            s = C.summarize([r for r in rs if r["i"] not in k], nd)
            sh.append(s["shown"]); bg.append(s["big_per_day"])
            w += (a5["shown"] > s["shown"])
        print(f"  ⑦ 無作為対照20seed(安い層から同数) 表示的中 中央 {np.median(sh):6.2f}%"
              f" (範囲 {min(sh):.2f}〜{max(sh):.2f}) 10万+ 中央 {np.median(bg):.3f}"
              f"  →  ⑤の勝敗 **{w}/20**")
        # 下位を見送る裏向き
        drop_lo = {r["i"] for r, val in zip(cheap, v)
                   if np.isfinite(val) and val < float(np.quantile(v[ok], frac))}
        rep(f"（裏）{var} 下位を見送り", [r for r in rs if r["i"] not in drop_lo], nd, base)


# ═══════════════════════════════ seg ═══════════════════════════════

def cmd_seg() -> None:
    """安い層は本当に「当てても安い」のか。帯別に商品の実績を出す。"""
    print("\n### 安い層は何を出しているか（cheapA の帯別・入稿後）\n")
    bins = [(0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 10.0),
            (10.0, 20.0), (20.0, 1e9)]
    for win in WINS:
        nd = ndays(win)
        rs = sell(win)
        print(f"\n  ════ {win} ════")
        print(f"    {'cheapA帯':>12s} {'件':>6s} {'件/日':>6s} {'的中%':>6s} {'ガミ%':>6s}"
              f" {'表示的中%':>8s} {'払戻中央':>9s} {'ROI%':>6s} {'10万+/日':>8s}"
              f" {'順当%':>6s} {'点数':>5s}")
        for lo, hi in bins:
            g = [r for r in rs if np.isfinite(r["cheapA"]) and lo <= r["cheapA"] < hi]
            if not g:
                continue
            s = C.summarize(g, nd)
            print(f"    {lo:5.1f}〜{hi if hi < 1e8 else 999:5.1f} {len(g):6,}"
                  f" {s['perday']:6.2f} {s['hit']:6.2f} {s['gami']:6.2f}"
                  f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {s['roi']:6.1f}"
                  f" {s['big_per_day']:8.3f}"
                  f" {np.mean([r['exact123'] for r in g])*100:6.2f} {s['k']:5.2f}")
        # 当たったときの払戻の分布
        print(f"\n    -- 的中した商品の払戻（cheapA<3.0 ↔ それ以外）--")
        for nm, g in (("安い(<3.0)", [r for r in rs if r["cheapA"] < 3.0]),
                      ("それ以外", [r for r in rs if not (r["cheapA"] < 3.0)])):
            h = sorted(r["pay"] for r in g if r["pay"] > 0)
            inv = np.mean([r["inv"] for r in g])
            print(f"       {nm:10s} n={len(g):5,} 的中{len(h):4,}件"
                  f"  払戻 25%={np.percentile(h,25):7,.0f} 中央={np.percentile(h,50):7,.0f}"
                  f" 75%={np.percentile(h,75):8,.0f}  平均投資={inv:6,.0f}円")


# ═══════════════════════════════ sweep ═══════════════════════════════

def cmd_sweep() -> None:
    """⑤ の総当たり。各セル = ⑦（安い層から同数を無作為）20seed に対する勝敗。

    🔴 セルは 9量 × 2閾値 × 2割合 = 36 ある。**いくつか勝つのは当たり前**なので、
       「両窓◯」が出た量については機序（向き）まで確かめること。
    """
    print("### ⑤ 総当たり: 安い層を朝の量で2分し、上位（順当そう）を見送る")
    print("### 判定は ⑦（安い層から同数を無作為に落とす20seed）に対する勝敗\n")
    print(f"  {'量':9s} {'閾値':>5s} {'割合':>5s} | {'探索':>7s} {'Δ表示':>7s}"
          f" | {'確認':>7s} {'Δ表示':>7s}  判定")
    for var in ("sig14", "sig12", "q_m3", "hs_trio", "sp5", "sp14",
                "axis", "gap", "pw_ent"):
        for thr in (3.0, 4.0):
            for frac in (0.33, 0.5):
                out = {}
                for win in WINS:
                    nd, rs = ndays(win), sell(win)
                    cheap = [r for r in rs
                             if np.isfinite(r["cheapA"]) and r["cheapA"] < thr]
                    v = np.array([float(r[var]) for r in cheap])
                    ok = np.isfinite(v)
                    cut = float(np.quantile(v[ok], 1 - frac))
                    drop = {r["i"] for r, x in zip(cheap, v)
                            if np.isfinite(x) and x >= cut}
                    arm = C.summarize([r for r in rs if r["i"] not in drop], nd)
                    base = C.summarize(rs, nd)
                    w = 0
                    for sd in RNG_SEEDS:
                        rng = np.random.default_rng(2000 + sd)
                        k = set(rng.choice([r["i"] for r in cheap], len(drop),
                                           replace=False).tolist())
                        w += (arm["shown"] > C.summarize(
                            [r for r in rs if r["i"] not in k], nd)["shown"])
                    out[win] = (w, arm["shown"] - base["shown"])
                we, de = out["explore"]
                wc, dc = out["confirm"]
                v1, v2 = we >= 15, wc >= 15
                tag = "両窓◯" if v1 and v2 else ("**符号反転**" if v1 != v2 else "両窓✗")
                print(f"  {var:9s} {thr:5.1f} {frac:5.2f} | {we:4d}/20 {de:+7.2f}"
                      f" | {wc:4d}/20 {dc:+7.2f}  {tag}")


# ═══════════════════════════════ rev ═══════════════════════════════

def cmd_rev(thr: float = 4.0, frac: float = 0.33) -> None:
    """⑤の**裏向き**＝安い層のうち「荒れそうな側」を見送る。

    🔴 `pw_ent` は安い層の中で **低いほど順当**（AUC 0.450/0.413）。
       上位を落とすのは**ユーザー提案とは逆向き**の操作である点に注意。
    """
    print(f"\n### 安い層（cheapA<{thr}）の pw_ent 上位 {frac:.0%}"
          f"（＝1着が読めない側）を見送る — ⑤の裏向き\n")
    for win in WINS:
        rs = [r for r in sell(win) if r["cheapA"] < thr]
        v = np.array([r["pw_ent"] for r in rs])
        q = np.quantile(v, [1 / 3, 2 / 3])
        for nm, m in (("下位1/3(読める)", v < q[0]),
                      ("中", (v >= q[0]) & (v < q[1])),
                      ("上位1/3(読めない)", v >= q[1])):
            g = [r for r, k in zip(rs, m) if k]
            s = C.summarize(g, ndays(win))
            print(f"  {win:8s} {nm:18s} n={len(g):4,}"
                  f" 順当 {np.mean([r['exact123'] for r in g])*100:5.2f}%"
                  f" 表示的中 {s['shown']:5.2f}% 払戻中央 {s['med_pay']:7,.0f}"
                  f" 10万+/日 {s['big_per_day']:.3f}"
                  f" 一撃 {sum(r['ichigeki'] for r in g):3d}件")
    print()
    for win in WINS:
        nd, rs = ndays(win), sell(win)
        cheap = [r for r in rs if r["cheapA"] < thr]
        v = np.array([r["pw_ent"] for r in cheap])
        cut = float(np.quantile(v, 1 - frac))
        drop = {r["i"] for r, x in zip(cheap, v) if x >= cut}
        arm = [r for r in rs if r["i"] not in drop]
        b = rep(f"{win} ⓪現行", rs, nd)
        rep(f"{win} 荒れ側 {len(drop)}件を見送り", arm, nd, b)
        m, lo, hi = boot_delta(rs, arm)
        print(f"     Δ表示的中 95%CI [{lo:+.2f}, {hi:+.2f}] pt")


# ═══════════════════════════════ ichi ═══════════════════════════════

def cmd_ichi() -> None:
    """一撃商品は安いレースでも一撃になるか（提案の後半の検算）。"""
    print("\n### 一撃商品（A_ana / F_sign）は安いレースでも一撃になるか\n")
    for win in WINS:
        nd = ndays(win)
        rs = [r for r in sell(win) if r["ichigeki"]]
        print(f"  ════ {win}  一撃商品 {len(rs):,}件 ════")
        print(f"    {'cheapA帯':>12s} {'件':>5s} {'表示的中%':>8s} {'払戻中央':>9s}"
              f" {'払戻最大':>10s} {'10万+':>6s} {'10万+/日':>8s} {'ROI%':>6s}")
        for lo, hi in ((0, 4.0), (4.0, 8.0), (8.0, 15.0), (15.0, 30.0), (30.0, 1e9)):
            g = [r for r in rs if lo <= r["cheapA"] < hi]
            if not g:
                continue
            s = C.summarize(g, nd)
            pays = [r["pay"] for r in g]
            print(f"    {lo:5.1f}〜{hi if hi < 1e8 else 999:5.1f} {len(g):5,}"
                  f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {max(pays):10,.0f}"
                  f" {sum(1 for p in pays if p >= 100_000):6d}"
                  f" {s['big_per_day']:8.3f} {s['roi']:6.1f}")
    print("\n  🔴 n が 47〜72 しかなく**両窓で符号が反転する**（探索 0件 ↔ 確認 3件）。"
          "\n     ここは測定では決まらない。")


# ═══════════════════════════════ defb ═══════════════════════════════

def cmd_defb() -> None:
    """定義A（指数1-2-3）と定義B（印◎○△）で ② の結論が変わるか。"""
    print("\n### 定義A / B の比較（§2）\n")
    for win in WINS:
        nd, rs = ndays(win), sell(win)
        base = C.summarize(rs, nd)
        print(f"  ════ {win} ════ ⓪現行 {base['perday']:.2f}件/日"
              f" 表示的中 {base['shown']:.2f}% 10万+ {base['big_per_day']:.3f}"
              f" ROI {base['roi']:.1f}")
        for key in ("cheapA", "cheapB"):
            for thr in (3.0, 4.0):
                drop = {r["i"] for r in rs
                        if np.isfinite(r[key]) and r[key] < thr}
                s = C.summarize([r for r in rs if r["i"] not in drop], nd)
                w = 0
                for sd in RNG_SEEDS:
                    rng = np.random.default_rng(1000 + sd)
                    k = rng.choice(len(rs), len(rs) - len(drop), replace=False)
                    w += (s["shown"] > C.summarize([rs[j] for j in k], nd)["shown"])
                print(f"    {key} <{thr}: 見送り {len(drop):5,}件 →"
                      f" {s['perday']:5.2f}件/日 表示的中 {s['shown']:5.2f}%"
                      f" (Δ{s['shown']-base['shown']:+.2f}) 10万+ {s['big_per_day']:.3f}"
                      f" ROI {s['roi']:5.1f}  対照 {w}/20")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "arms"
    if cmd == "auc":
        cmd_auc(float(sys.argv[2]) if len(sys.argv) > 2 else 3.0)
    elif cmd == "arms":
        cmd_arms()
    elif cmd == "sweep":
        cmd_sweep()
    elif cmd == "rev":
        cmd_rev(*(float(a) for a in sys.argv[2:4]))
    elif cmd == "ichi":
        cmd_ichi()
    elif cmd == "defb":
        cmd_defb()
    elif cmd == "seg":
        cmd_seg()
    elif cmd == "split":
        cmd_split(sys.argv[2] if len(sys.argv) > 2 else "sig14",
                  float(sys.argv[3]) if len(sys.argv) > 3 else 3.0,
                  float(sys.argv[4]) if len(sys.argv) > 4 else 0.5)