#!/usr/bin/env python3
"""7H1 の買い目変種を honest vintage キャッシュ上で採点し直す。

比較する2つ:
  現行  1着=別ライン先頭 / 2着=プール上位2 / 3着=**本命を除く**4車      → 8点
  案A   1着=同上         / 2着=同上        / 3着=本命を**足した**5車    → 10点

案A は 2026-08-24 に別データで測って「的中 5.02→6.76% / ROI ほぼ不変」と
出ていたもの（`docs/sales_kpi.md` 打ち手F・未実装）。本スクリプトは
月次凍結 vintage のキャッシュで測り直す。

モデル推論は不要（キャッシュに買い目の素材が入っている）。DB からは
盤面・着順・払戻だけを読む。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/variant_third_col.py
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.backfill_7h1_rank_wt import _combo_key, _load_boards, _load_finishes  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.result_top3 import hit_trifecta, winning_trifectas  # noqa: E402
from src.strategy_wt import RANK_7H1_TF_SECOND_N, rank_7h1_stakes  # noqa: E402

CACHE = REPO / "data" / "exp" / "7h1_gate_cache.jsonl"
WINDOWS = (("探索 2024-04〜2025-12", "2024-04-01", "2025-12-31"),
           ("確認 2026-01〜", "2026-01-01", "2026-12-31"))


def legs_current(r) -> list[str]:
    return list(r["legs_tf"])


def legs_variant(r) -> list[str]:
    """3着列へ本命を足す（`rank_7h1_build_legs` の others に fav を追加した形）。"""
    cur = list(r["legs_tf"])
    lead = int(cur[0].split("-")[0])
    others = list(r["others"])
    pool_rest = []
    seen = set()
    for t in cur:                      # 2着に使われている車を現行の目から復元する
        a = int(t.split("-")[1])
        if a not in seen:
            seen.add(a)
            pool_rest.append(a)
    assert len(pool_rest) <= RANK_7H1_TF_SECOND_N
    third = others + [r["fav"]]
    return [f"{lead}-{a}-{c}" for a in pool_rest for c in third if c not in (lead, a)]


def legs_variant_b(r) -> list[str]:
    """2着列にも本命を足す（本命が2着に来る決着も拾う形）。"""
    cur = list(r["legs_tf"])
    lead = int(cur[0].split("-")[0])
    others = list(r["others"])
    seen, pool_rest = set(), []
    for t in cur:
        a = int(t.split("-")[1])
        if a not in seen:
            seen.add(a)
            pool_rest.append(a)
    second = pool_rest + [r["fav"]]
    third = others + [r["fav"]]
    return [f"{lead}-{a}-{c}" for a in second for c in third if c not in (lead, a)]


def score(rows, legs_fn):
    keys = [r["race_key"] for r in rows]
    _t, tf_bd = _load_boards(keys)
    fins = _load_finishes(keys)
    pm = _load_payouts_wt(keys)
    out = []
    for r in rows:
        rk = r["race_key"]
        lookup, order = tf_bd.get(rk), fins.get(rk)
        if not lookup or not order:
            continue
        all_legs = legs_fn(r)
        head = int(all_legs[0].split("-")[0])
        if not any(k[0] == head for k in lookup):
            continue
        legs = [t for t in all_legs if _combo_key(t, True) in lookup]
        if not legs:
            continue
        u, bet = rank_7h1_stakes(len(legs))
        wins = winning_trifectas(order)
        w = hit_trifecta([tuple(int(x) for x in t.split("-")) for t in legs], wins)
        odds = pm.get(rk, {}).get(("trifecta", w or wins[0]), 0)
        out.append(dict(race_date=r["race_date"], n=len(legs), hit=int(w is not None),
                        payout=int(odds * u // 100 if w is not None else 0),
                        bet=bet, odds=int(odds)))
    return out


def summary(rows, label):
    n = len(rows)
    hit = sum(r["hit"] for r in rows)
    real = sum(1 for r in rows if r["hit"] and r["payout"] > r["bet"])
    pay = sum(r["payout"] for r in rows)
    bet = sum(r["bet"] for r in rows)
    pays = sorted(r["payout"] for r in rows if r["hit"])
    med = pays[len(pays) // 2] if pays else 0
    print(f"  {label:8s} 平均{sum(r['n'] for r in rows) / n:.1f}点 n={n:5d} "
          f"的中{hit:3d} {hit / n * 100:5.2f}% (表示{real / n * 100:5.2f}%) "
          f"ROI={pay / bet * 100:6.1f}%  払戻中央={med:,}円 "
          f"10万+={sum(1 for x in pays if x >= 100000)}")
    return dict(n=n, hit=hit, pay=pay, bet=bet)


def paired_boot(a, b, iters=4000, seed=11):
    """同一レースでの対 (現行, 案A) をレース単位で再標本し、差の95%CIを出す。"""
    rnd = random.Random(seed)
    idx = range(len(a))
    dh, dr = [], []
    for _ in range(iters):
        s = [rnd.randrange(len(a)) for _ in idx]
        ha = sum(a[i]["hit"] for i in s) / len(s) * 100
        hb = sum(b[i]["hit"] for i in s) / len(s) * 100
        ra = sum(a[i]["payout"] for i in s) / sum(a[i]["bet"] for i in s) * 100
        rb = sum(b[i]["payout"] for i in s) / sum(b[i]["bet"] for i in s) * 100
        dh.append(hb - ha)
        dr.append(rb - ra)
    dh.sort(); dr.sort()
    lo = int(iters * 0.025); hi = int(iters * 0.975)
    return (dh[lo], dh[hi], dr[lo], dr[hi])


def main() -> None:
    rows = []
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("selected") and r.get("scored"):
                rows.append(r)
    for label, a, b in WINDOWS:
        sub = [r for r in rows if a <= r["race_date"] <= b]
        cur = score(sub, legs_current)
        var = score(sub, legs_variant)
        vb = score(sub, legs_variant_b)
        # 採点できた集合は同一（同じ盤面・着順条件）はずだが、念のため突き合わせる
        assert len(cur) == len(var) == len(vb), f"{label}: 採点件数が違う"
        print(f"\n== {label} ==")
        summary(cur, "現行8点")
        summary(var, "案A10点")
        summary(vb, "案B15点")
        for name, v in (("案A", var), ("案B", vb)):
            dhl, dhh, drl, drh = paired_boot(cur, v)
            print(f"  差（{name} − 現行）: 的中 {dhl:+.2f}〜{dhh:+.2f}pt / "
                  f"ROI {drl:+.1f}〜{drh:+.1f}pt （95%CI・レース単位対再標本）")


if __name__ == "__main__":
    main()
