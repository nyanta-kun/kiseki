#!/usr/bin/env python3
"""共通候補プールを **vintage で生成**して現行体系の精度を測り直す（2026-08-23・§36）。

**本番 DB へは一切書かない。** 結果はローカルの pkl に貯める。

## 方針（§35 の結論を受けたユーザー判断・2026-08-23）

事前登録 Phase 0 の完了条件「現行実績と一致」は**原理的に達成できない**
（ゲートが見た p3 はどこにも残っておらず、本番モデルは 2026-08-21 に再学習済み）。
→ **プールも「現行相当」も同じ vintage 再構築で作り、同一の土俵に載せる。**
🔴 **したがってここで出る「現行体系」の数字は、実際に売った商品の実績ではない。**
比較できるのは**買い方ルールの優劣**であって、現行商品の公表実績ではない。

## 作り方

月次 vintage モデル（`lgbm_wt_eval_mYYMM` 等）で各ランクの `build_rows()` を呼ぶ。
`rebuild_*_walkforward_pg.py` と同じ計算だが **wipe→insert をしない**ので、
`picks_history` を壊さずに 7H2 / 7T1 のように過去分が無いランクも作れる
（§33 で「破壊的な rebuild が要る」と書いたが、**プールを持つだけなら不要**）。

    プール      = 優先順位をかけない全ランクの買い目
    現行相当    = enabled フィルタ → RANK_ORDER で1レース1商品へ畳む

⚠️ 日次上限は各ランクの `build_rows()` の中にあるものをそのまま使う
（事前登録は「評価時に外す」としているが、本節は**現状の再測定**なので現状のまま）。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/phase0_vintage_pool.py \
        --from 2026-01 --to 2026-06            # 生成（キャッシュ済みは飛ばす）
    PYTHONPATH=. .venv/bin/python scripts/phase0_vintage_pool.py \
        --from 2026-01 --to 2026-06 --report   # 集計だけ
"""
from __future__ import annotations

import argparse
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.phase0_pool_audit import enabled_ranks, load_rank_order  # noqa: E402
from src.database import get_connection  # noqa: E402

CACHE = Path("data/exp/vpool")


def _v(tag: str, role: str) -> str:
    return f"lgbm_wt_{role}_{tag}"


def _pos(mod, tag, d1, d2):
    return mod.build_rows(_v(tag, "eval"), d1, d2, _v(tag, "win"), _v(tag, "bad"))


def _kw(mod, tag, d1, d2):
    return mod.build_rows(d1, d2, eval_model=_v(tag, "eval"),
                          win_model=_v(tag, "win"))


def _h1(mod, tag, d1, d2):
    return mod.build_rows(d1, d2, eval_model=_v(tag, "eval"),
                          win_model=_v(tag, "win"), bad_model=_v(tag, "bad"),
                          favbust_model=_v(tag, "favbust"))


BUILDERS = {
    "7H2": ("backfill_7h2_rank_wt", _kw),
    "7S":  ("backfill_7s_merged_rank_wt", _pos),
    "7B":  ("backfill_7b_rank_wt", _pos),
    "7C":  ("backfill_7c_rank_wt", _pos),
    "7T1": ("backfill_7t1_rank_wt", _kw),
    "7H1": ("backfill_7h1_rank_wt", _h1),
    "7M1": ("backfill_7m1_rank_wt", _pos),
}


def months(a: str, b: str):
    y, m = int(a[:4]), int(a[5:7])
    ey, em = int(b[:4]), int(b[5:7])
    while (y, m) <= (ey, em):
        import calendar
        last = calendar.monthrange(y, m)[1]
        yield (f"m{y % 100:02d}{m:02d}", f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last}")
        m += 1
        if m > 12:
            m, y = 1, y + 1


def build(a: str, b: str) -> None:
    import importlib
    CACHE.mkdir(parents=True, exist_ok=True)
    for tag, d1, d2 in months(a, b):
        for rank, (mod_name, call) in BUILDERS.items():
            out = CACHE / f"{rank}_{tag}.pkl"
            if out.exists():
                continue
            t0 = time.time()
            try:
                mod = importlib.import_module(f"scripts.{mod_name}")
                rows = call(mod, tag, d1, d2)
            except Exception as e:                       # noqa: BLE001
                print(f"  {tag} {rank:>4}: 🔴 {type(e).__name__}: {e}", flush=True)
                # 🔴 失敗を空で保存しない。空とエラーを区別できなくなる。
                continue
            keep = [{k: r.get(k) for k in
                     ("race_key", "race_date", "hit", "payout", "bet_amount",
                      "n_combos")} for r in rows]
            pickle.dump(keep, open(out, "wb"))
            print(f"  {tag} {rank:>4}: {len(keep):>4} 行 ({time.time() - t0:.0f}s)",
                  flush=True)


def load(a: str, b: str):
    pool = defaultdict(dict)          # {レースキー: {ランク: 行}}
    have = defaultdict(list)          # {ランク: [tag]}
    for tag, _d1, _d2 in months(a, b):
        for rank in BUILDERS:
            f = CACHE / f"{rank}_{tag}.pkl"
            if not f.exists():
                continue
            have[rank].append(tag)
            for r in pickle.load(open(f, "rb")):
                pool[str(r["race_key"]).split("#")[0]][rank] = r
    return pool, have


def agg(rows, n_days):
    if not rows:
        return None
    bet = sum(float(r["bet_amount"] or 0) for r in rows)
    pay = sum(float(r["payout"] or 0) for r in rows)
    buys = [r for r in rows if float(r["bet_amount"] or 0) > 0]
    hits = sum(1 for r in buys if r["hit"])
    return dict(n=len(buys), per_day=len(buys) / max(n_days, 1),
                hit=hits / max(len(buys), 1), roi=pay / max(bet, 1),
                inv_day=bet / max(n_days, 1))


def report(a: str, b: str) -> None:
    pool, have = load(a, b)
    if not pool:
        print("キャッシュが無い。--report を付けずに先に生成すること。")
        return
    with get_connection() as conn:
        off = enabled_ranks(conn)
    order = [r for r in load_rank_order() if r in BUILDERS]
    en = [r for r in order if r not in off]
    days = {r["race_date"] for v in pool.values() for r in v.values()}
    nd = len(days)
    print(f"\nプール {len(pool):,} レース / {nd} 日")
    print(f"実効の優先順位: {' > '.join(en)}   （無効: {sorted(off & set(order))}）")
    print("\n🔴 これは **vintage 再構築**であって実際に売った商品の実績ではない。\n")

    print("===== 1. プール（優先順位をかけない・各ランク単独）=====")
    print(f"{'ランク':>6}{'月数':>5}{'件数':>7}{'件/日':>7}{'投資/日':>10}"
          f"{'的中%':>8}{'ROI':>8}")
    for r in order:
        rows = [v[r] for v in pool.values() if r in v]
        s = agg(rows, nd)
        if not s:
            print(f"{r:>6}{len(have.get(r, [])):>5}{'—':>7}")
            continue
        mark = "" if r in en else "  (無効)"
        print(f"{r:>6}{len(have.get(r, [])):>5}{s['n']:>7}{s['per_day']:>7.2f}"
              f"{s['inv_day']:>10,.0f}{s['hit']:>8.1%}{s['roi']:>8.1%}{mark}")

    print("\n===== 2. 現行相当（enabled → RANK_ORDER で1レース1商品）=====")
    taken = defaultdict(list)
    for _k, v in pool.items():
        pick = next((r for r in en if r in v), None)
        if pick:
            taken[pick].append(v[pick])
    print(f"{'ランク':>6}{'件数':>7}{'件/日':>7}{'投資/日':>10}{'的中%':>8}{'ROI':>8}")
    allrows = []
    for r in en:
        s = agg(taken.get(r, []), nd)
        if not s:
            continue
        allrows += taken[r]
        print(f"{r:>6}{s['n']:>7}{s['per_day']:>7.2f}{s['inv_day']:>10,.0f}"
              f"{s['hit']:>8.1%}{s['roi']:>8.1%}")
    s = agg(allrows, nd)
    if s:
        print(f"\n  **体系合計**: {s['n']:,}件 ・ {s['per_day']:.2f}件/日 ・ "
              f"投資 {s['inv_day']:,.0f}円/日 ・ 的中 {s['hit']:.2%} ・ ROI {s['roi']:.1%}")
        print(f"  （払戻率の壁 74.85% との差: {(s['roi'] - 0.7485) * 100:+.1f}pt）")


def day_ci(days, B=3000, seed=7):
    """days={日: [bet_a, pay_a, bet_b, pay_b]} → ROI差(B−A)の95%CI。"""
    import numpy as np
    v = np.array([[d[0], d[1], d[2], d[3]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    a = v[idx, 1].sum(1) / np.maximum(v[idx, 0].sum(1), 1)
    b = v[idx, 3].sum(1) / np.maximum(v[idx, 2].sum(1), 1)
    d = np.sort(b - a)
    return d[int(B * .025)], d[int(B * .975)]


def head_to_head(a: str, b: str) -> None:
    """重複レースの直接対決。**同じレースの上で**上位ランクと下位ランクを比べる。

    🔴 プール単独の ROI を並べても優劣は言えない（母集団が違う）。
       優先順位が正しいかは「**両方が取れるレース**でどちらが上か」でしか測れない。
    """
    from collections import defaultdict as dd
    pool, _have = load(a, b)
    with get_connection() as conn:
        off = enabled_ranks(conn)
    order = [r for r in load_rank_order() if r in BUILDERS]
    en = [r for r in order if r not in off]
    print("\n===== 3. 重複レースの直接対決（同じレース上での比較）=====")
    print("  🔴 プール単独の ROI は母集団が違うので優劣を語れない。ここが本番。")
    print(f"  {'上位':>5}{'下位':>5}{'重複R':>7}{'上位ROI':>9}{'下位ROI':>9}"
          f"{'差(下位−上位)':>26}")
    for i, hi in enumerate(en):
        for lo in en[i + 1:]:
            days = dd(lambda: [0.0, 0.0, 0.0, 0.0])
            n = 0
            for v in pool.values():
                if hi not in v or lo not in v:
                    continue
                rh, rl = v[hi], v[lo]
                bh = float(rh["bet_amount"] or 0)
                bl = float(rl["bet_amount"] or 0)
                if bh <= 0 or bl <= 0:
                    continue
                n += 1
                z = days[rh["race_date"]]
                z[0] += bh; z[1] += float(rh["payout"] or 0)
                z[2] += bl; z[3] += float(rl["payout"] or 0)
            if n < 200:
                continue
            rh_ = sum(z[1] for z in days.values()) / sum(z[0] for z in days.values())
            rl_ = sum(z[3] for z in days.values()) / sum(z[2] for z in days.values())
            lo_, hi_ = day_ci(days)
            flag = "🟢下位が上" if lo_ > 0 else ("🔴上位が上" if hi_ < 0 else "")
            diff = f"{(rl_ - rh_) * 100:+.1f}pt[{lo_ * 100:+.1f},{hi_ * 100:+.1f}]{flag}"
            print(f"  {hi:>5}{lo:>5}{n:>7}{rh_:>9.1%}{rl_:>9.1%}{diff:>26}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="a", default="2026-01")
    ap.add_argument("--to", dest="b", default="2026-06")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not args.report:
        print(f"===== vintage プール生成 {args.a}〜{args.b} =====", flush=True)
        build(args.a, args.b)
    report(args.a, args.b)
    head_to_head(args.a, args.b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
