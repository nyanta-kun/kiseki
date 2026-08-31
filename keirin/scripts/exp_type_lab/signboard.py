#!/usr/bin/env python3
"""看板枠（高額払戻を作る買い方）の実測（2026-08-31）。**読み取りのみ**。

正本のまとめ: `keirin/docs/type_lab/signboard_slot_2026_08_31.md`

台   /tmp/tf20_board_vint.npz  vintage walk-forward・7車 36,427R・2024-07-01〜2026-08-04
     PROB(N,210) モデル確率 / PO(N,210) 予測オッズ / WIN(N) 的中index / PAY(N) 確定払戻(円/100円)
現行 keirin.type_lab_picks (mode='paper') を race_key で結合して比較台にする。
窓   探索 2024-07〜2025-12 / 確認 2026-01〜08-04

🔴 **買い目は本番の `build_legs` + `allocate` を import して組む**
   （CLAUDE.md「買い目を検証で組み直さない」。2026-08-10 に丸1本の結論が反転した型）。
   §2 の「選び方の比較」だけは本番に無い腕を測るのでスクリプト内で組む。

⚠️ **全節で20分ほどかかる**（1腕あたり 28,063レース × 210点）。節を選ぶか
   `--limit` で間引いて動作確認すること。

使い方:
    python scripts/exp_type_lab/signboard.py               # 全節（20分ほど）
    python scripts/exp_type_lab/signboard.py --only 4      # 節を選ぶ
    python scripts/exp_type_lab/signboard.py --limit 2000  # 動作確認（数字は読めない）
"""
from __future__ import annotations

import argparse
import collections
import itertools
import os
import random
import statistics as st
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
from src.type_lab import (BUDGET, PLANS, RaceShape, SIGNBOARD_TARGET,  # noqa: E402
                          allocate, build_legs)

BOARD = os.environ.get("TYPE_LAB_BOARD", "/tmp/tf20_board_vint.npz")
CANON = list(itertools.permutations(range(1, 8), 3))
#: 現行の売り物（`SELL_PLANS` の 7車ぶん。看板枠を入れる**前**の姿を比較台にする）。
BASE_PLAN_BY_TYPE = {"A": "A_hit", "B": "B_hit", "C": "C_hit",
                     "D": "D_hit", "E": "E_hit", "F": "F_pay"}
WINDOWS = (("探索", "2024-07-01", "2025-12-31"), ("確認", "2026-01-01", "2026-12-31"))


def _dsn() -> dict:
    env = {}
    for line in (REPO.parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return dict(host=env["DB_HOST"], port=env["DB_PORT"], dbname=env["DB_NAME"],
                user=env["DB_USER"], password=env["DB_PASSWORD"])


def load():
    # 🔴🔴 **`NpzFile` の添字アクセスは毎回まるごと展開する。** `z["PO"][i]` を
    #    レースごとに呼ぶと 56MB の解凍が数万回走り、数分で終わる測定が終わらない
    #    （型ラボで一度踏んでいる。`keirin_type_lab_shipped_2026_08_27`）。
    #    ここで**一度だけ**素の ndarray へ移す。
    with np.load(BOARD, allow_pickle=True) as f:
        z = {k: f[k] for k in ("PROB", "PO", "WIN", "PAY", "KEY", "DATE")}
    c = psycopg2.connect(**_dsn())
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT race_key, type_label, plan_key, payout
                   FROM keirin.type_lab_picks
                   WHERE mode='paper' AND settled_at IS NOT NULL
                     AND race_date >= '2024-07-01'""")
    ty, base = {}, {}
    for r in cur.fetchall():
        ty[r["race_key"]] = r["type_label"]
        if r["plan_key"] == BASE_PLAN_BY_TYPE.get(r["type_label"]):
            base[r["race_key"]] = int(r["payout"] or 0)
    keep = np.array([z["KEY"][i] in base and z["WIN"][i] >= 0
                     and np.isfinite(z["PAY"][i]) and z["PAY"][i] > 0
                     for i in range(len(z["KEY"]))])
    return z, ty, base, np.where(keep)[0]


def _maps(z, i):
    po, pr = z["PO"][i], z["PROB"][i]
    pred = {CANON[k]: float(po[k]) for k in range(210)
            if np.isfinite(po[k]) and po[k] > 0}
    prob = {CANON[k]: float(pr[k]) for k in range(210) if np.isfinite(pr[k])}
    return pred, prob


_SIGN_CACHE: dict[int, object] = {}


def production_signboard(z, i, type_label):
    """**本番関数**で看板枠を組んで確定払戻で採点する。戻り: (払戻, 点数, 計画最低)"""
    if i in _SIGN_CACHE:                       # 1レース1型なので使い回せる
        return _SIGN_CACHE[i]
    pred, prob = _maps(z, i)
    plan = PLANS[f"{type_label}_sign"]
    # 看板枠は確率から選ぶので order は使わない。型だけが意味を持つ。
    shape = RaceShape(type_label, 1.0, 0, 0.0, False, tuple(range(1, 8)))
    legs = build_legs(shape, plan, pred, prob)
    stakes = allocate(legs, pred, prob, plan) if legs else None
    if not stakes:
        _SIGN_CACHE[i] = None
        return None
    got = stakes.get(CANON[int(z["WIN"][i])], 0) * (float(z["PAY"][i]) / 100.0)
    _SIGN_CACHE[i] = (got, len(stakes), min(stakes[c] * pred[c] for c in stakes))
    return _SIGN_CACHE[i]


def greedy(z, i, target, rule, omax=None, topk=None, budget=BUDGET):
    """§2・§5 用。**本番に無い腕**（並べ方・予算違い）を測るときだけ使う。"""
    po, pr = z["PO"][i], z["PROB"][i]
    g = np.where(np.isfinite(po) & (po > 0) & np.isfinite(pr))[0]
    if omax is not None:
        g = g[po[g] <= omax]
    if topk is not None:
        g = g[np.argsort(-pr[g])][:topk]
    if g.size == 0:
        return None
    key = {"ev": -(pr[g] * po[g]), "prob": -pr[g], "odds": po[g]}[rule]
    sel, share = [], 0.0
    for k in g[np.argsort(key)]:
        if share + 1.0 / po[k] <= budget / target:
            sel.append(int(k))
            share += 1.0 / po[k]
    if not sel:
        return None
    unit = budget / share
    got = next((unit / po[k] * (float(z["PAY"][i]) / 100.0)
                for k in sel if k == z["WIN"][i]), 0.0)
    return got, len(sel), unit


def run(z, ty, base, idx, pick, label, budget=BUDGET, thr=100_000):
    """`pick(i, type_label) -> (払戻, 点数, 計画) or None`。None なら現行のまま。"""
    out = {}
    for wl, lo, hi in WINDOWS:
        byday = collections.defaultdict(list)
        pts, plans = [], []
        for i in idx:
            d = str(z["DATE"][i])
            if not (lo <= d <= hi):
                continue
            rk = z["KEY"][i]
            b = base[rk]
            r = pick(i, ty.get(rk))
            if r is None:
                byday[d].append((b, b, BUDGET))
            else:
                byday[d].append((b, r[0], budget))
                pts.append(r[1])
                plans.append(r[2])
        days = list(byday)
        nd, n = len(days), sum(len(v) for v in byday.values())
        if not n:
            continue
        pay = sum(y for v in byday.values() for _, y, _ in v)
        inv = sum(k for v in byday.values() for _, _, k in v)
        disp = sum(1 for v in byday.values() for _, y, k in v if y > k)
        b10 = sum(1 for v in byday.values() for _, y, _ in v if y >= thr)
        b30 = sum(1 for v in byday.values() for _, y, _ in v if y >= 300_000)
        # 🔴 **日ブロックで対応をとる**（同じ日の全レースをまとめて再標本する）。
        #    レース単位で回すと 1000×全レースで現実的な時間に終わらない。
        agg = {d: (sum(x for x, _, _ in v), sum(y for _, y, _ in v),
                   BUDGET * len(v), sum(k for _, _, k in v))
               for d, v in byday.items()}
        rnd, dd = random.Random(5), []
        for _ in range(1000):
            bb = nn = kb = kn = 0.0
            for _ in days:
                x, y, k1, k2 = agg[rnd.choice(days)]
                bb += x
                nn += y
                kb += k1
                kn += k2
            dd.append(100 * nn / kn - 100 * bb / kb)
        dd.sort()
        out[wl] = (len(pts) / nd, 100.0 * disp / n, 100.0 * pay / inv, b10 / nd,
                   b30 / nd, inv / nd, st.median(pts) if pts else 0,
                   st.median(plans) if plans else 0, dd[25], dd[974])
    if len(out) < 2:                       # --limit で片方の窓が空になったとき
        print(f"{label:26} | 窓が足りない（--limit 中？）: {sorted(out)}", flush=True)
        return
    a, b = out["探索"], out["確認"]
    print(f"{label:26} | {a[0]:5.1f}枠 表示的中{a[1]:6.2f}% ROI{a[2]:5.1f}% "
          f"10万+{a[3]:5.2f} 30万+{a[4]:5.2f} 投資{a[5]/10000:5.1f}万 {a[6]:3.0f}点 "
          f"ΔROI[{a[8]:+5.1f},{a[9]:+5.1f}]"
          f" || {b[1]:6.2f}% {b[2]:5.1f}% {b[3]:5.2f} {b[4]:5.2f} "
          f"{b[5]/10000:5.1f}万 {b[6]:3.0f}点 [{b[8]:+5.1f},{b[9]:+5.1f}]", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=0, help="節番号（0=全部）")
    ap.add_argument("--limit", type=int, default=0,
                    help="レース数の上限（動作確認用。数字は読めない）")
    args = ap.parse_args()
    z, ty, base, idx = load()
    if args.limit:
        # 🔴 先頭から取ると**確認窓が空になる**（台は日付順）。等間隔で間引く。
        idx = idx[:: max(1, len(idx) // args.limit)]
        print("⚠️ --limit 付き＝動作確認用。数字を結論に使わないこと。")
    print(f"台 {BOARD} / 現行商品と結合できたレース {len(idx)}\n", flush=True)
    head = (f"{'':26} | {'───────────── 探索 2024-07〜2025-12 ─────────────':^82}"
            f" || {'──────── 確認 2026 ────────':^54}")

    if args.only in (0, 2):
        print("§2 点の選び方（全型を看板枠に・計画15万）— **本数もROIも動かない**")
        print(head)
        run(z, ty, base, idx, lambda i, t: None, "なし（現行）")
        for rule, omax, topk, lb in (("ev", None, None, "EV順"),
                                     ("prob", None, None, "確率順"),
                                     ("odds", None, None, "予測オッズ昇順"),
                                     ("prob", 300, None, "確率順・<=300倍"),
                                     ("ev", 300, None, "EV順・<=300倍"),
                                     ("prob", 600, None, "確率順・<=600倍"),
                                     ("ev", None, 30, "確率上位30点→EV順")):
            run(z, ty, base, idx,
                lambda i, t, r=rule, o=omax, k=topk:
                    greedy(z, i, SIGNBOARD_TARGET, r, o, k), f"  {lb}")
        print()

    if args.only in (0, 3):
        print("§3 計画払戻 T の掃引（全型・確率順・<=600倍）")
        print(head)
        for T in (80_000, 100_000, 120_000, 150_000, 200_000):
            run(z, ty, base, idx,
                lambda i, t, T=T: greedy(z, i, T, "prob", 600),
                f"  計画{T//10000}万")
        print()

    if args.only in (0, 4):
        print("§4 ダイヤル（**本番の build_legs + allocate**・計画"
              f"{SIGNBOARD_TARGET//10000}万）")
        print(head)
        run(z, ty, base, idx, lambda i, t: None, "なし（現行）")
        for types in (("F",), ("F", "C"), ("F", "C", "E"),
                      ("F", "C", "E", "B"), tuple("ABCDEF")):
            s = set(types)
            run(z, ty, base, idx,
                lambda i, t, s=s: production_signboard(z, i, t) if t in s else None,
                f"  看板枠: {'+'.join(types)}")
        print()

    if args.only in (0, 5):
        print("§5 予算を上げる（全型・確率順・<=600倍・計画15万）— 本数は予算に比例")
        print(head)
        for bud in (10_000, 15_000, 20_000, 30_000):
            run(z, ty, base, idx,
                lambda i, t, b=bud: greedy(z, i, SIGNBOARD_TARGET, "prob", 600,
                                           budget=b),
                f"  1レース{bud//10000}.{(bud//1000) % 10}万円", budget=bud)


if __name__ == "__main__":
    main()
