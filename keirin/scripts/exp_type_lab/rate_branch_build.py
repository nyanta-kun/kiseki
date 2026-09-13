#!/usr/bin/env python3
"""「二軸そろいなのに外した」回を、ライン・連対率・3着内率で条件分岐できるかの台
（2026-09-13・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- 売る1商品は `src.type_lab.sell_plans_for`、買い目は `build_legs`+`allocate`、
  入稿ゲートは 平均想定払戻 > 20,000円 かつ 全点の予測オッズ >= 2.0倍。
- **軸2車は `shape.order[0..1]` ＝ モデル P3 の上位2車**（選手の 3着内率 そのものではない）。
- 確率は `rank_7t3_blend_probs`（PL × 同ライン隣接ボーナス λ/μ）。板の PROB ではない。
- 🔴 `third_rate_norm` / `first_rate_norm` と そのレース内順位は **既に
  `FEATURE_COLS_WT` に入っている**（＝P3 モデルは 3着内率・1着率を見ている）。
  **`second_rate`（連対率）だけが未使用。**

## リーク検査（済・2026-09-13）

`wt_entries` は `INSERT OR REPLACE` で上書きされるため、`ex_spurt_pct` は
2026-07-31 に train/serve skew を理由に特徴から外されている。同じ検査
（同一開催・同一選手の Day1 行 vs 最終日行・n=42,367）を率3本に掛けた結果:

    first_rate / second_rate / third_rate   変化 0.00%
    ex_spurt_pct（対照・既知の汚染列）       変化 5.49%

＝ **率は開催中に動かない**（月をまたぐと 91.6% 動く＝期間成績として更新される）。
point-in-time 安全として扱ってよい。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

PERMS, C3 = C.CANON, C.CANON3
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/ratebranch/rows.pkl")


def load_rates(keys: list[str]) -> dict:
    """race_key -> (7,3) の [1着率, 連対率, 3着内率]。車番 = frame_no。"""
    out: dict[str, np.ndarray] = {}
    with get_connection() as c:
        for i0 in range(0, len(keys), 3000):
            ch = keys[i0:i0 + 3000]
            rows = c.execute(
                "SELECT race_key, frame_no, first_rate, second_rate, third_rate "
                "FROM keirin.wt_entries WHERE race_key = ANY(?)", (ch,)).fetchall()
            for r in rows:
                fn = int(r['frame_no'])
                if not (1 <= fn <= 7):
                    continue
                a = out.setdefault(str(r['race_key']), np.full((7, 3), np.nan, np.float32))
                a[fn - 1] = [r['first_rate'] if r['first_rate'] is not None else np.nan,
                             r['second_rate'] if r['second_rate'] is not None else np.nan,
                             r['third_rate'] if r['third_rate'] is not None else np.nan]
            print(f"  rates {min(i0+3000, len(keys)):,}/{len(keys):,}", flush=True)
    return out


def build_one(x, key: str):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(plan=key, trio=trio, k=len(st), inv=float(sum(st.values())),
                pay=pay, mean=float(mean), legs=list(st), stakes=dict(st))


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    keys = np.array([str(v) for v in z["KEY"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    LG, LPOS, LSZ, LLEAD, NL = (z["LG"], z["A_line_pos"], z["A_line_size"],
                                z["A_is_line_leader"], z["A_n_lines"])
    RP, ST = z["A_race_point"].astype(float), z["ST"]

    idx_all = []
    for win in ("explore", "confirm"):
        idx_all += [(win, int(i)) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
    rates = load_rates(sorted({keys[i] for _, i in idx_all}))
    print(f"率が取れたレース: {len(rates):,} / {len({keys[i] for _, i in idx_all}):,}")

    rows = []
    for n, (win, i) in enumerate(idx_all):
        if n % 5000 == 0:
            print(f"  {n:,}/{len(idx_all):,}", flush=True)
        rk = keys[i]
        R = rates.get(rk)
        if R is None or not np.isfinite(R).all():
            continue
        x = ctx(i)
        if x is None:
            continue
        trio_ok = build_one(x, "A_trio") is not None if tp[i] == "A" else False
        key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
        r = build_one(x, key)
        if r is None:
            continue

        wt = x.win_tf                      # 確定 1-2-3（車番タプル）
        a1, a2 = x.shape.order[0], x.shape.order[1]
        top3 = set(wt)
        legs_set = set(r["legs"])
        if r["trio"]:
            set_hit = x.win_t3 in legs_set
            in_legs = set_hit
        else:
            in_legs = wt in legs_set
            set_hit = frozenset(wt) in {frozenset(c) for c in legs_set}
        legs_cars: set[int] = set()
        for cb in r["legs"]:
            legs_cars |= set(cb if not r["trio"] else tuple(cb))

        both = (a1 in top3) and (a2 in top3)
        third_car = next((c for c in wt if c not in (a1, a2)), None)

        rows.append(dict(
            i=i, win=win, race_key=str(rk), date=x.date, type=tp[i], rtype=str(rt[i]),
            plan=r["plan"], trio=r["trio"], k=r["k"], inv=r["inv"], pay=r["pay"],
            mean=r["mean"], hit=r["pay"] > 0, shown=r["pay"] >= r["inv"],
            in_legs=in_legs, set_hit=set_hit,
            a1=a1, a2=a2, both_in3=both, third_car=third_car,
            third_in_legs_cars=(third_car in legs_cars) if third_car else None,
            fin=wt, axis=float(z["AXIS_SUM"][i]), gap=float(z["GAP"][i]),
            arare=int(z["ARARE"][i]), agree=bool(z["AGREE"][i]),
            pw_ent=float(x.shape.pw_ent), rp_sd=float(RP[i].std()),
            p3=np.array([x.shape.order.index(c) for c in range(1, 8)], np.int8),
            P3=np.array([x.pr_tf and 0] * 0),   # placeholder（未使用）
            rate=R.copy(),                      # (7,3) 1着率/連対率/3着内率
            lg=np.array([str(LG[i][c]) for c in range(7)]),
            lpos=LPOS[i].astype(np.float32).copy(),
            lsize=LSZ[i].astype(np.float32).copy(),
            llead=LLEAD[i].astype(np.float32).copy(),
            nlines=float(NL[i][0]), style=np.array([str(ST[i][c]) for c in range(7)]),
            rp=RP[i].copy(),
            po_tf={c: v for c, v in x.po_tf.items()},
            pr_tf={c: v for c, v in x.pr_tf.items()},
            po_t3=dict(x.po_t3), pr_t3=dict(x.pr_t3),
            win_t3=x.win_t3, odds_t3=float(x.odds_t3), pay_tf=float(x.pay_tf),
            p3vec=z["P3"][i].astype(np.float32).copy(),
            pwvec=z["PW"][i].astype(np.float32).copy(),
        ))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as f:
        pickle.dump(rows, f)
    print(f"保存 {OUT}  {len(rows):,}行")


if __name__ == "__main__":
    main()
