#!/usr/bin/env python3
"""H21 配管の検証（走らせる前に1回）。

 (1) ≤08-04: 台の PO ↔ predict_board(台の P3/PW + DB の出走表) が一致するか（①を作る経路の検証）
 (2) ≤08-04: 結果（WIN/PAY）が台と一致するか
 (3) ①で組んだ買い目 ↔ type_lab_picks（paper ≤08-26 / live 08-27〜）の legs の一致率（組・賭け金）
"""
from __future__ import annotations
import json, os, sys, collections
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import h21_core as K                                       # noqa: E402
from _db import q                                           # noqa: E402
S, PLANS = K.S, K.PLANS


def main():
    recs, skip = K.load_window()
    print("races", len(recs), "skip", dict(collections.Counter(skip.values())))
    # (1) PO
    from src import odds_prediction_tf as OT
    brd = [r for r in recs if r["src"] == "board"][::40][:70]
    rel = []
    for r in brd:
        ent = r["ent"]; cars = list(range(1, 8))
        m = {c: dict(race_point=float(ent[c]["race_point"]), mark=ent[c]["mark"], player_class=ent[c]["player_class"],
                     style=ent[c]["style"], line_group=ent[c]["line_group"], line_size=ent[c]["line_size"],
                     line_pos=ent[c]["line_pos"], is_line_leader=ent[c]["is_line_leader"],
                     first_rate=ent[c]["first_rate"], second_rate=ent[c]["second_rate"], third_rate=ent[c]["third_rate"])
             for c in cars}
        bd = OT.predict_board(cars, {c: r["P3"][c - 1] for c in cars}, {c: r["PW"][c - 1] for c in cars}, m)
        po = np.array([bd[p] for p in K.PERMS])
        rel.append(np.abs(po / r["PO1"] - 1))
    rel = np.concatenate(rel)
    print(f"(1) 台PO vs predict_board(台P3/PW): n_races={len(brd)} 相対差 median {np.median(rel):.2e} p99 {np.percentile(rel,99):.2e} max {rel.max():.2e}")
    # (2) 結果
    b = [r for r in recs if r["src"] == "board"]
    nw = sum(1 for r in b if K.PIDX[r["win"]] != r["board_win"])
    pay_rel = np.array([abs(r["pay_tf"] * 100 / r["board_pay"] - 1) for r in b if K.PIDX[r["win"]] == r["board_win"]])
    print(f"(2) 結果: 台 {len(b)}R 当たり目の不一致 {nw}  払戻の相対差>1e-3: {(pay_rel>1e-3).sum()} / {len(pay_rel)}  同着(代表1通り)のレース {sum(1 for r in recs if r['win_n']>1)}")
    # (3) 買い目の一致率
    cols, rows = q("""select race_key, plan_key, mode, legs, rule_version, generated_at from keirin.type_lab_picks
                      where n_entries=7 and mode in ('paper','live') and race_date between '2026-06-08' and '2026-10-05'""")
    picks = collections.defaultdict(list)
    for rk, pk, mode, legs, rv, ga in rows:
        picks[rk].append((pk, mode, legs if isinstance(legs, list) else json.loads(legs), rv))
    idx = {r["key"]: i for i, r in enumerate(recs)}
    PO = np.array([r["PO1"] for r in recs])
    S._Z = K.board_dict(recs, PO)
    K._ARM["TRIO"] = None
    res = collections.defaultdict(lambda: collections.Counter())
    shown = 0
    for rk, lst in picks.items():
        i = idx.get(rk)
        if i is None:
            continue
        r = recs[i]
        seg = "paper<=08-04(board)" if r["date"] <= K.BOARD_END else ("paper08-05..26(DB)" if r["date"] <= "2026-08-26" else "live08-27..(DB)")
        x = S.ctx(i)
        if x is None:
            continue
        for pk, mode, legs, rv in lst:
            if pk not in PLANS or pk.startswith(("T_", "L_flat")):
                continue
            if (mode == "paper") != (r["date"] <= "2026-08-26"):
                continue
            got = S.build(x, PLANS[pk])
            c = res[seg]
            c["rows"] += 1
            res[seg + "|" + pk]["rows"] += 1
            if not got:
                c["mine_none"] += 1
                continue
            stakes, odds, used, mean = got
            trio = used.bet_type == "trio"
            mine = {(tuple(sorted(k)) if trio else tuple(k)): int(v) for k, v in stakes.items()}
            theirs = {}
            for l in legs:
                t = tuple(int(y) for y in str(l["combo"]).replace("=", "-").split("-"))
                theirs[tuple(sorted(t)) if trio else t] = int(l["stake"])
            res[seg + "|" + pk]["same_combos_stakes"] += mine == theirs
            c["same_combos"] += set(mine) == set(theirs)
            c["same_combos_stakes"] += mine == theirs
            c["plan_used_same"] += used.key == pk
            if seg.startswith("paper<=") and mine != theirs and shown < 3:
                shown += 1
                print("   例 不一致", rk, pk, "mine", dict(list(mine.items())[:4]), "theirs", dict(list(theirs.items())[:4]), "rv", rv)
    for seg, c in res.items():
        n = c["rows"]
        print(f"(3) {seg}: 行 {n}  買う目の集合が一致 {c['same_combos']/n*100:.1f}%  賭け金まで一致 {c['same_combos_stakes']/n*100:.1f}%  組めなかった {c['mine_none']}")


if __name__ == "__main__":
    main()
