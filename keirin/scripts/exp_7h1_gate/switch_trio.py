#!/usr/bin/env python3
"""7H1 に「本命が生きていると読んだら三連複・二軸総流しへ切り替える」案を測る。

## 背景（2026-08-25 ユーザー提案）

> 本命が勝つレースとして判定し、7H1 からは溢れるのが理想。高知7Rは取れるのであれば
> 2,5 の二軸となるモデルで三連複的中が理想。相手も混戦模様のため総流し。

見送って下位ランク（7M1）へ渡す形は **受け皿が 26〜32% しかない**（残りは無商品）。
そこで**同じランクの中で券種を切り替える**形を測る。7C が
`RANK_7C_TRIFECTA_PW_MIN` で三連複⇄三連単を切り替えているのと同じ構造。

    切替なし … 三連単F8点（現行）
    切替あり … 三連複 軸2車（本命 + 3着内率で本命の次点）総流し5点

⚠️ 賭け金は **1レース1万円・5点均等 2,000円** で計算する。本番の三連複ランクは
   `tilt_stakes`（予測オッズによるダッチング）なので、実装するなら数字は変わる。
   ここでは券種切替の可否だけを見るため均等で揃えている。

⚠️ 軸2は `others[0]`（本命を除く3着内率トップ）で近似している。本番の
   `axis1_7c`/`axis2_7c` は全7車の pred_top3 上位2車なので、本命が pred_top3 でも
   1位ならこの2つは一致する。一致率は `--check-axes` で 7M1 の記録と突き合わせる。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/switch_trio.py [--check-axes]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.backfill_7h1_rank_wt import _combo_key, _load_boards, _load_finishes  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.result_top3 import hit_trifecta, winning_trifectas  # noqa: E402
from src.strategy_wt import RACE_BUDGET, STAKE_UNIT, rank_7h1_stakes, unit_stake  # noqa: E402

CACHE = REPO / "data" / "exp" / "7h1_gate_cache.jsonl"
WINDOWS = (("探索 2024-04〜2025-12", "2024-04-01", "2025-12-31"),
           ("確認 2026-01〜", "2026-01-01", "2026-12-31"))


def load():
    rows = []
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("selected") and r.get("scored"):
                rows.append(r)
    return rows


def trio_legs(r) -> tuple[list[frozenset], int, int]:
    """(三連複の目, 軸1, 軸2)。軸=本命 + 本命を除く3着内率トップ、相手=残り5車の総流し。"""
    fav = r["fav"]
    others = list(r["others"])
    a2 = others[0]
    partners = [x for x in others[1:]]
    return [frozenset((fav, a2, p)) for p in partners], fav, a2


def score(rows):
    """各レースについて (三連単現行, 三連複切替) 両方の結果を返す。"""
    keys = [r["race_key"] for r in rows]
    trio_bd, tf_bd = _load_boards(keys)
    fins = _load_finishes(keys)
    pm = _load_payouts_wt(keys)
    out = []
    for r in rows:
        rk = r["race_key"]
        order = fins.get(rk)
        if not order:
            continue
        # --- 現行（三連単8点） ---
        tf_lookup = tf_bd.get(rk) or {}
        legs_tf = [t for t in r["legs_tf"] if _combo_key(t, True) in tf_lookup]
        head_ok = any(k[0] == int(str(r["legs_tf"][0]).split("-")[0]) for k in tf_lookup)
        tf = None
        if tf_lookup and head_ok and legs_tf:
            u, bet = rank_7h1_stakes(len(legs_tf))
            wins = winning_trifectas(order)
            w = hit_trifecta([tuple(int(x) for x in t.split("-")) for t in legs_tf], wins)
            odds = pm.get(rk, {}).get(("trifecta", w or wins[0]), 0)
            tf = dict(hit=int(w is not None), n=len(legs_tf), bet=bet,
                      payout=int(odds * u // 100 if w is not None else 0))
        # --- 切替（三連複 軸2総流し） ---
        trio_lookup = trio_bd.get(rk) or {}
        legs, a1, a2 = trio_legs(r)
        legs = [s for s in legs if s in trio_lookup]
        tr = None
        if legs:
            u = unit_stake(len(legs), RACE_BUDGET, STAKE_UNIT)
            win_set = frozenset(f for _o, f in order[:3])
            hit = win_set in legs
            odds = pm.get(rk, {}).get(("trio", win_set), 0)
            tr = dict(hit=int(hit), n=len(legs), bet=u * len(legs),
                      payout=int(odds * u // 100 if hit else 0), a1=a1, a2=a2)
        if tf and tr:
            out.append(dict(r, _tf=tf, _tr=tr))
    return out


def stat(items, key):
    n = len(items)
    if not n:
        return "n=0"
    hit = sum(x[key]["hit"] for x in items)
    real = sum(1 for x in items if x[key]["hit"] and x[key]["payout"] > x[key]["bet"])
    pay = sum(x[key]["payout"] for x in items)
    bet = sum(x[key]["bet"] for x in items)
    pays = sorted(x[key]["payout"] for x in items if x[key]["hit"])
    med = pays[len(pays) // 2] if pays else 0
    return (f"n={n:5d} 的中{hit:4d}({hit / n * 100:5.2f}%) 表示{real / n * 100:5.2f}% "
            f"ROI={pay / bet * 100:6.1f}% 払戻中央={med:,}円 "
            f"2万+={sum(1 for x in pays if x >= 20000)}")


def check_axes(rows) -> None:
    """軸の近似（本命 + others[0]）が 7M1/7C の記録した軸と一致するかを実測する。"""
    keys = {r["race_key"] for r in rows}
    got = defaultdict(dict)
    with get_connection() as c:
        ks = sorted(keys)
        for i in range(0, len(ks), 700):
            ch = ks[i:i + 700]
            q = ("select race_key, rank, pred_combo from picks_history where rank in "
                 "('RANK_7M1','RANK_7C') and race_key like '%%' and " +
                 "substr(race_key,1,%d) in (%s)" % (len(ch[0]), ",".join("?" * len(ch))))
            try:
                for r in c.execute(q, ch):
                    got[r["race_key"].split("#")[0]][r["rank"]] = r["pred_combo"]
            except Exception as e:                       # SQL 方言差は致命的でない
                print(f"  [warn] 軸突き合わせをスキップ: {e}")
                return
    n = ok = 0
    for r in rows:
        combo = got.get(r["race_key"], {}).get("RANK_7M1") or \
                got.get(r["race_key"], {}).get("RANK_7C")
        if not combo:
            continue
        m = re.match(r"^(\d+)=(\d+)-", str(combo))
        if not m:
            continue
        n += 1
        if {int(m.group(1)), int(m.group(2))} == {r["fav"], r["others"][0]}:
            ok += 1
    if n:
        print(f"  軸の近似 一致率: {ok}/{n} = {ok / n * 100:.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-axes", action="store_true")
    args = ap.parse_args()
    rows = load()
    for label, a, b in WINDOWS:
        sub = [r for r in rows if a <= r["race_date"] <= b]
        sc = score(sub)
        print(f"\n== {label} （両券種とも採点できた {len(sc)}件）==")
        print(f"  現行 三連単8点     {stat(sc, '_tf')}")
        print(f"  常に三連複5点      {stat(sc, '_tr')}")
        for name, cond in (("本命が1着だった", lambda x: x["fav_win"] == 1),
                           ("本命が2-3着だった",
                            lambda x: x["fav_win"] == 0 and x["fav_bust"] == 0),
                           ("本命がバストした", lambda x: x["fav_bust"] == 1)):
            part = [x for x in sc if cond(x)]
            print(f"   -- {name} ({len(part)}件 {len(part) / len(sc) * 100:.1f}%)")
            print(f"      三連単 {stat(part, '_tf')}")
            print(f"      三連複 {stat(part, '_tr')}")
        if args.check_axes:
            check_axes(sc)


if __name__ == "__main__":
    main()
