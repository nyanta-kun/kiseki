#!/usr/bin/env python3
"""型A を軸信頼で割り、荒れやすい層を別の買い方で買う（2026-08-31・ユーザー指示）。

## なぜ

型A は買い方を6通り変えても **分岐割れ 72〜88%** が動かなかった。一方で
**型A の 35.2% は 30倍以上で決着**しており（確認窓・中央88倍）、現行の
「◎1着・○2着固定」はそこを1点も買っていない。

事前に選べるかを測ると `axis_sum` が最も効いた（低1/3 で 30倍+ が 45.5% ↔
高1/3 で 27.4%・中央 25.2倍 ↔ 8.1倍）。**型A の中の「一番堅くない層」**が荒れる側。

## 測ること

型A を `axis_sum` の三分位で割り、各層に**別の買い方**を当てて比べる。

    現行A     ◎1着・○2着固定 × 3着3車（3点）
    確率3点   全210点を位置別合成PL の確率降順で上位3点
    C相当     予測20倍以上から確率上位12点（型C の買い方）
    E相当     予測30倍以上から確率上位14点（型E の買い方）
    F相当     軸2車＋相手2車の6順列（12点・型F_hit の買い方）

🔴 **予測オッズは `type_lab_picks.legs` に入っている値しか使えない**（買った目の分だけ）。
   C/E 相当は「予測オッズ20倍以上」の母集団を作れないので、
   **確定オッズで代用せず、確率上位k点＋帯なし**で近似する。近似であることを明記する。
🔴 配分は全案とも均等（配分差で結論が動かないように）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_split.py
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs   # noqa: E402
from src.type_lab import BUDGET, UNIT              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}


def load():
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, race_date, axis_sum, race_type, p3_order "
            "FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
            "  AND plan_key = ? AND n_entries = 7 AND p3_order IS NOT NULL",
            ("paper", "A_hit"))]
        rows = [d for d in rows
                if is_fill_target(d.get("race_type"), None)
                or _GATE.passes_axis_gate(
                    "A_hit",
                    float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
        keys = sorted({d["race_key"] for d in rows})
        fin: dict = defaultdict(dict)
        ent: dict = defaultdict(dict)
        top: dict = defaultdict(list)
        for i in range(0, len(keys), 300):
            ch = keys[i:i + 300]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type = 'trifecta' AND race_key IN ({ph})",
                               tuple(ch)):
                d = dict(r)
                fin[d["race_key"]][d["combination"]] = float(d["odds_value"])
            for r in c.execute(f"SELECT race_key, frame_no, finish_order, pred_win_pct, "
                               f"       pred_top3_pct FROM wt_entries "
                               f"WHERE race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                if d["pred_win_pct"] is not None and d["pred_top3_pct"] is not None:
                    ent[d["race_key"]][int(d["frame_no"])] = (
                        float(d["pred_win_pct"]) / 100.0,
                        float(d["pred_top3_pct"]) / 100.0)
                if d["finish_order"] and 1 <= int(d["finish_order"]) <= 3:
                    top[d["race_key"]].append((int(d["finish_order"]),
                                               int(d["frame_no"])))
    out = []
    for d in rows:
        e = ent.get(d["race_key"], {})
        f = sorted(top.get(d["race_key"], []))
        order = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
        if len(e) != 7 or len(f) < 3 or len(order) < 7:
            continue
        pw = {c: v[0] for c, v in e.items()}
        p3 = {c: v[1] for c, v in e.items()}
        prob = rank_7t3_blend_probs(sorted(p3), pw, p3)
        out.append({
            "date": str(d["race_date"]), "axis_sum": float(d["axis_sum"] or 0),
            "order": order,
            "ranked": ["-".join(str(x) for x in t)
                       for t in sorted(prob, key=lambda t: -float(prob[t]))],
            "act": "-".join(str(c) for _o, c in f[:3]),
            "board": fin.get(d["race_key"], {}),
        })
    return out


def stat(rs, combos_of):
    bet = pay = hit = 0
    pays: list[int] = []
    ks: list[int] = []
    for d in rs:
        cs = combos_of(d)
        if not cs:
            continue
        k = len(cs)
        st = BUDGET // k // UNIT * UNIT
        ks.append(k)
        bet += st * k
        if d["act"] in cs and d["board"].get(d["act"]):
            p = int(st * d["board"][d["act"]])
            pay += p
            hit += 1
            pays.append(p)
    if not bet or not hit:
        return None
    pays.sort()
    n = len(ks)
    hr = hit / n
    need = (bet / n) / hr
    return (n, sum(ks) / n, hr, pay / bet, pays[len(pays) // 2], need,
            sum(1 for x in pays if x < need) / len(pays),
            sum(1 for x in pays if x >= 100_000) / n)


CUR = lambda d: [f"{d['order'][0]}-{d['order'][1]}-{d['order'][p - 1]}"
                 for p in (3, 4, 5)]          # noqa: E731


def main() -> int:
    data = load()
    plans = [("現行A（固定3点）", CUR),
             ("確率上位3点", lambda d: d["ranked"][:3]),
             ("確率上位6点", lambda d: d["ranked"][:6]),
             ("確率上位12点", lambda d: d["ranked"][:12]),
             ("確率4〜15位（12点）", lambda d: d["ranked"][3:15]),
             ("確率7〜18位（12点）", lambda d: d["ranked"][6:18])]
    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        if not rs:
            continue
        srt = sorted(rs, key=lambda d: d["axis_sum"])
        n = len(srt)
        layers = [("axis 低1/3（荒れやすい）", srt[:n // 3]),
                  ("axis 中1/3", srt[n // 3:2 * n // 3]),
                  ("axis 高1/3（最も堅い）", srt[2 * n // 3:])]
        print(f"\n=== {win}（型A {n:,}R・配分は全案とも均等）===")
        for lab, sl in layers:
            print(f"\n  【{lab}】{len(sl):,}R")
            print(f"    {'買い方':<20}{'点':>4}{'素の的中':>9}{'ROI':>8}"
                  f"{'的中中央':>10}{'分岐割れ':>9}{'10万+':>8}")
            for pl, fn in plans:
                s = stat(sl, fn)
                if s:
                    print(f"    {pl:<20}{s[1]:>4.0f}{s[2]:>9.1%}{s[3]:>8.1%}"
                          f"{s[4]:>9,}円{s[6]:>9.0%}{s[7]:>8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
