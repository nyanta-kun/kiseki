#!/usr/bin/env python3
"""型ラボの買い目を採点して `keirin.type_lab_picks` を埋める（2026-08-27 新設）。

    python scripts/settle_type_lab_picks.py --from 2026-01-01 --to 2026-08-26
    python scripts/settle_type_lab_picks.py --date 2026-08-27          # 当日ぶん

🔴 **確定オッズで採点する**（`wt_odds` の trio / trifecta）。予測オッズは使わない。
   的中目の 確定/予測 は中央 0.87 に下振れる（勝者の呪い）ので、想定払戻で採点すると
   実績を上振れさせる。
🔴 **同着の当たり目は複数ある**（2026-08-28 是正）。判定の正本は `src/result_top3.py`。
   3着が2車同着なら三連複の当たりは2通り、三連単は着順の入れ替えぶん増える。
   旧実装は `{着順: 車番}` の辞書で持っていたため
   - 同着で**後勝ちして片方が消える**（しかも SQL に ORDER BY が無く非決定的）
   - 1着/2着同着だと 1・2・3 がそろわず**永久に未採点**
   になっていた。実測で **14行が当たり目を買っているのに hit=false**、
   **339行が永久保留**（2025-10-27 岸和田6R では同じレースの A_hit だけが的中を落とし、
   A_pay は的中していた）。他ランクの `backfill_7*_rank_wt.py` は全部 `result_top3` を
   使っており、**型ラボの採点だけが例外**だった。
🔴 **払戻は「自分が買った当たり目」で引く**（同着では目ごとに払戻が違う）。
🔴 **3着までが確定しないレースは採点しない**（`settled_at` を空のまま残す）。
   ここで 0 を書くと外れと区別できなくなる（2026-08-21 立川11R で踏んだ型）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402
from src.result_top3 import (  # noqa: E402
    representative, winning_trifectas, winning_trios,
)
from src.type_lab import (  # noqa: E402
    axis_hit_of, classify_miss, lookup_prob_ranked,
)


def _load_targets(where: str, params: tuple, redo: bool = False) -> list[dict]:
    """採点対象の行。`redo=True` なら**採点済みも含めて**採り直す。

    ⚠️ `--redo` は採点ロジックを直したあとに過去分を揃え直すためのもの。
       既定（False）は未採点だけを見るので、何度流しても害がない。
    """
    cond = where if redo else f"settled_at IS NULL AND {where}"
    # 🔴 **外れの5分類（`DESIGN.md` 4.3）に要る列も一緒に引く。** 判定の入力は
    #    生成時に焼き付けた `band_min_odds` / `prob_ranked` / `axis1` / `axis2` /
    #    `n_legs` だけで、ここでモデルを引き直さない（引き直すと再学習後は別物）。
    cols = ("id", "race_key", "bet_type", "legs",
            "axis1", "axis2", "n_legs", "band_min_odds", "prob_ranked")
    with get_connection() as c:
        rows = c.execute(
            f"SELECT {', '.join(cols)} FROM type_lab_picks WHERE {cond}",
            params).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _finish(keys: list[str]) -> dict:
    """{race_key: [(着順, 車番), ...]}。**3着以内が3車そろったレースだけ**返す。

    🔴 `{着順: 車番}` の辞書にしないこと。同着では2車が同じ着順を持つので
       後勝ちで片方が消える。並びと当たり目の生成は `src/result_top3.py` が正本で、
       `TOP3_SQL` と同じ `ORDER BY finish_order, frame_no` のタイブレークを使う
       （無いと同じレースを組み直すたびに正解が入れ替わる）。
    """
    out = defaultdict(list)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, finish_order, frame_no FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order BETWEEN 1 AND 3 "
                 "ORDER BY race_key, finish_order, frame_no")
            for rk, fo, fn in c.execute(q, ch).fetchall():
                out[rk].append((int(fo), int(fn)))
    # 当たり目が作れないレース（3着までそろっていない）は落とす
    return {k: v for k, v in out.items() if winning_trifectas(v)}


def _odds(keys: list[str]) -> dict:
    """{race_key: {('trio'|'trifecta', 正規化した組み合わせ): 確定オッズ}}"""
    out = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, bet_type, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type IN ('trio','trifecta')")
            for rk, bt, comb, od in c.execute(q, ch).fetchall():
                try:
                    v = float(od)
                except (TypeError, ValueError):
                    continue
                if not (0 < v < 99999):
                    continue
                nums = [int(x) for x in re.split(r"[-=→]", str(comb)) if x.strip().isdigit()]
                if len(nums) != 3:
                    continue
                key = ("=".join(str(x) for x in sorted(nums)) if bt == "trio"
                       else "-".join(str(x) for x in nums))
                out[rk][(bt, key)] = v
    return dict(out)


def _cars(combo: str) -> list[int]:
    """`"1-4-5"`（三連単）/ `"2=5=7"`（三連複）の車番。"""
    sep = "=" if "=" in combo else "-"
    return [int(x) for x in combo.split(sep) if x.strip().isdigit()]


def _combo_str(win, bet_type: str) -> str:
    """当たり目 → `type_lab_picks.legs` と同じ表記。"""
    if win is None:
        return ""
    return ("=".join(str(x) for x in sorted(win)) if bet_type == "trio"
            else "-".join(str(x) for x in win))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--date")
    ap.add_argument("--redo", action="store_true",
                    help="採点済みの行も採り直す（採点ロジックを直したあとの是正用）")
    a = ap.parse_args()
    if a.date:
        where, params = "race_date = ?", (a.date,)
    elif a.date_from and a.date_to:
        where, params = "race_date BETWEEN ? AND ?", (a.date_from, a.date_to)
    else:
        where, params = "race_date = ?", (date.today().isoformat(),)

    targets = _load_targets(where, params, redo=a.redo)
    if not targets:
        print("採点対象なし")
        return
    keys = sorted({t["race_key"] for t in targets})
    fin, odds = _finish(keys), _odds(keys)
    print(f"対象 {len(targets)} 行 / {len(keys)} レース  着順確定 {len(fin)}")

    n_ok = n_wait = 0
    updates: list[tuple] = []
    with get_connection() as c:
        for t in targets:
            f = fin.get(t["race_key"])
            if not f:
                n_wait += 1
                continue
            legs = json.loads(t["legs"]) if isinstance(t["legs"], str) else t["legs"]
            # 🔴 当たり目は**複数ありうる**（同着）。正本 `src/result_top3.py` で作る。
            wins = (winning_trios(f) if t["bet_type"] == "trio"
                    else winning_trifectas(f))
            win_strs = [_combo_str(w, t["bet_type"]) for w in wins]
            # 🔴🔴 **当たった目を全部払う**（2026-08-28）。同着では当たり目が2通りあり、
            #    その両方を買っていれば**両方とも払い戻される**（確定オッズも目ごとに
            #    別々に公表される。実測 2025-10-27 岸和田6R: 1-4-2 = 28.0倍 /
            #    1-4-6 = 170.7倍 が両方とも板にある）。
            #    ⚠️ `result_top3.hit_trifecta` / `hit_trio` は**1つしか返さない**ので
            #       そのまま使うと払戻を取りこぼす。あちらは他ランクが共有する正本
            #       なので API は変えず、ここで全当たり目を舐める。
            won_legs = [l for l in legs if l["combo"] in win_strs]
            missing = [l for l in won_legs
                       if odds.get(t["race_key"], {}).get((t["bet_type"], l["combo"])) is None]
            if missing:
                # 当たっているのに確定オッズが引けない。0 を書くと外れと区別できないので待つ
                n_wait += 1
                continue
            payout = sum(int(round(l["stake"]
                                   * odds[t["race_key"]][(t["bet_type"], l["combo"])]))
                         for l in won_legs)
            hit = won_legs[0] if won_legs else None
            # 🔴 記録する `win_combo` は「**自分が買った当たり目**」。外れたときだけ
            #    代表を入れる（`representative` は決定的に1つ選ぶだけで、
            #    「その値がレースの払戻だ」という意味ではない）。
            #    ⚠️ 同着で2目とも買っていた場合は先頭だけが残る。`payout` は
            #       両方を足した額なので、**`final_odds × stake` と一致しない**。
            win = (hit["combo"] if hit is not None
                   else _combo_str(representative(wins), t["bet_type"]))
            o = odds.get(t["race_key"], {}).get((t["bet_type"], win))
            # 🔴 **決着の三連単オッズは券種と的中に関係なく入れる**（答え合わせ用）。
            #    `final_odds` は「買った目」の確定オッズで的中時しか入らないため、
            #    外れたレースの荒れ具合が測れず「arare が配当を当てているか」を
            #    検証できない。三連複プラン(D_hit)の行にも三連単の値を入れることで
            #    型どうしを同じ物差しで比べられる。
            # ⚠️ 同着では三連単の当たり目も複数ある。ここは**レース単位の荒れ具合**が
            #    要るだけなので代表を1つ選ぶ（同着は全体の 0.2% 程度）。
            tf_rep = representative(winning_trifectas(f))
            tf = "-".join(str(x) for x in tf_rep) if tf_rep else ""
            tf_odds = odds.get(t["race_key"], {}).get(("trifecta", tf))
            # ── 外れの5分類（`DESIGN.md` 4.3・正本は `src.type_lab.classify_miss`）──
            # 🔴 **決着の目は「買った当たり目」ではなく実際の決着**を使う。`win` は
            #    的中時は買った目（＝決着と同じ）だが、外れたときは代表の決着なので
            #    どちらでも決着を指す。ここを `legs` から取ると外れた行が全部
            #    引けなくなる。
            prob_ranked = t.get("prob_ranked")
            if isinstance(prob_ranked, str):
                prob_ranked = json.loads(prob_ranked) if prob_ranked else None
            # 🔴🔴 **焼き付けが無い行には分類を書かない。** `prob_ranked` が NULL
            #    （= migration `202609111730_keirin` より前に生成された行）だと
            #    決着の確率順位が引けず、`classify_miss` は必ず `legs_model` を返す。
            #    それは「モデルの限界だった」という**確信のある誤った札**で、
            #    ③帯下決着 と ④a予算 を丸ごと飲み込む。分からないものは
            #    NULL のまま残し、レポート側で「未分類」として別に数える。
            if prob_ranked is None:
                win_po = win_rank = miss_class = None
            else:
                win_po, win_rank = lookup_prob_ranked(prob_ranked, win)
                miss_class = classify_miss(
                    hit=bool(hit),
                    axis_hit=axis_hit_of(t.get("axis1"), t.get("axis2"), win),
                    band_min_odds=t.get("band_min_odds"),
                    win_pred_odds=win_po, win_prob_rank=win_rank,
                    n_legs=t.get("n_legs"))
            # 🔴 `hit` は PostgreSQL では boolean。1/0 を渡すと
            #    DatatypeMismatch で落ちる（SQLite では通るので気づきにくい）。
            updates.append((win, bool(hit), payout,
                            float(o) if (hit and o) else None,
                            float(tf_odds) if tf_odds else None,
                            win_po, win_rank, miss_class, t["id"]))
            n_ok += 1
        # 1行ずつ UPDATE すると 16,000 行で数分かかる（VPS への往復）。まとめて送る。
        if updates:
            c.executemany(
                "UPDATE type_lab_picks SET settled_at = NOW(), win_combo = ?, "
                "hit = ?, payout = ?, final_odds = ?, win_tf_odds = ?, "
                "win_pred_odds = ?, win_prob_rank = ?, miss_class = ? "
                "WHERE id = ?", updates)
        c.commit()
    print(f"採点 {n_ok} 行 / 保留 {n_wait} 行（着順または確定オッズ待ち）")


if __name__ == "__main__":
    main()
