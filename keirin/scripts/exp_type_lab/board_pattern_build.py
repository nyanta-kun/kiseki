#!/usr/bin/env python3
"""発走前に確定する「盤面の組み合わせ」を1レース1行で焼き付ける（2026-09-10）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- ユーザーが挙げた4つ（WT印 / 脚質 / ライン / 番手）は **すべて `FEATURE_COLS_WT`（70列）に
  単体として入っている**（`prediction_mark` / `style_enc`・`n_senko` / `line_*` 12列 /
  `line_leader_*`・`formation_*`）。したがって**単体の量を条件にする腕は二重計上**になる。
  実証: `docs/type_lab/partner_select_2026_09_10.md`（5腕すべて一律版に負けた）。
- よってここで作るのは **単体ではなく組み合わせ（レース単位の配置）** だけ。
- `race_shape()` の `arare` が実際に見ているのは5つだけ:
  ①軸1のライン人数 ②先頭の遅れ率 ③先頭の脚質が「追」④開催日目 ⑤番手の得点>先頭の得点。
  ライン構成そのもの（本数×人数の組み）・印の散らばり・脚質の配置・軸2車の位置関係は
  **どこにも入っていない**。

## 出力

/tmp/board_pattern.pkl … {name: np.ndarray}（36,427R ぶん・7車のみ）
"""
from __future__ import annotations

import itertools
import pickle
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CANON = list(itertools.permutations(range(1, 8), 3))
OUT = Path("/tmp/board_pattern.pkl")


def main() -> None:
    z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
    d = {k: z[k] for k in z.files}          # NpzFile の添字は毎回全展開するので実体化
    n = len(d["KEY"])
    P3, PW = d["P3"], d["PW"]
    LG, ST, LP = d["LG"], d["ST"], d["A_line_pos"]
    SZ, MK, RP = d["A_line_size"], d["A_prediction_mark"], d["A_race_point"]
    BH, WIN = d["BEHIND"], d["WIN"]

    out: dict[str, list] = {k: [] for k in (
        "key", "date", "win", "n_lines", "line_sig", "n_solo", "max_line",
        "ax_same", "ax_rel", "a1_pos", "a1_size", "a2_pos", "a2_size",
        "m1_pos", "m1_size", "mk_same12", "mk_conc", "mk_nlines", "mk_top_is_a1",
        "n_nige", "n_oi", "n_ryo", "a1lead_style", "n_nige_lead", "n_nige_solo",
        "n_oi_deputy", "n_dep_stronger", "a1_dep_stronger", "a1lead_behind",
        "nl_top3", "nl_top5", "n35_in_axisline",
        "axis_sum", "gap", "arare", "type", "rtype", "venue", "dayi", "agree",
        "pw_ent", "rp_sd", "both_in3", "a1_in3", "a2_in3", "third_rank",
        "pay_tf", "ok")}

    for i in range(n):
        p3 = P3[i]
        order = list(np.argsort(-p3) + 1)          # 車番 1..7 の p3 降順
        rank = {c: r for r, c in enumerate(order, 1)}
        lg = {c: str(LG[i][c - 1]) for c in range(1, 8)}
        lp = {c: int(LP[i][c - 1]) for c in range(1, 8)}
        sz = {c: int(SZ[i][c - 1]) for c in range(1, 8)}
        st = {c: str(ST[i][c - 1]) for c in range(1, 8)}
        rp = {c: float(RP[i][c - 1]) for c in range(1, 8)}
        mk = {c: int(MK[i][c - 1]) for c in range(1, 8)}
        groups: dict[str, list[int]] = {}
        for c in range(1, 8):
            groups.setdefault(lg[c], []).append(c)
        sizes = sorted((len(v) for v in groups.values()), reverse=True)

        a1, a2 = order[0], order[1]
        same = lg[a1] == lg[a2]
        if same:
            rel = f"same_{min(lp[a1],3)}-{min(lp[a2],3)}"
        else:
            def _tag(c):
                return "S" if sz[c] == 1 else str(min(lp[c], 3))
            rel = f"diff_{_tag(a1)}-{_tag(a2)}"

        def leader_of(car):
            mem = groups[lg[car]]
            if len(mem) == 1:
                return car
            ld = [c for c in mem if lp[c] == 1]
            return ld[0] if ld else min(mem, key=lambda c: lp[c])

        m1 = next((c for c in range(1, 8) if mk[c] == 1), None)
        m2 = next((c for c in range(1, 8) if mk[c] == 2), None)
        m3 = next((c for c in range(1, 8) if mk[c] == 3), None)
        marks = [c for c in (m1, m2, m3) if c is not None]
        mk_conc = sum(1 for c in marks if m1 is not None and lg[c] == lg[m1])
        mk_nlines = len({lg[c] for c in marks}) if marks else 0

        dep_str = 0
        for g, mem in groups.items():
            if len(mem) < 2:
                continue
            ld = [c for c in mem if lp[c] == 1]
            dp = [c for c in mem if lp[c] == 2]
            if ld and dp and rp[dp[0]] > rp[ld[0]]:
                dep_str += 1
        a1ld = leader_of(a1)
        a1dp = [c for c in groups[lg[a1]] if lp[c] == 2]
        nl3 = len({lg[c] for c in order[:3]})
        nl5 = len({lg[c] for c in order[:5]})
        axis_lines = {lg[a1], lg[a2]}
        n35 = sum(1 for c in order[2:5] if lg[c] in axis_lines)

        fin = CANON[int(WIN[i])]
        in3 = set(fin)
        both = (a1 in in3) and (a2 in in3)
        third = -1
        if both:
            oth = [c for c in fin if c not in (a1, a2)]
            third = rank[oth[0]] if oth else -1

        rec = dict(
            key=str(d["KEY"][i]), date=str(d["DATE"][i]),
            win=("explore" if str(d["DATE"][i]) <= "2025-12-31" else "confirm"),
            n_lines=len(groups), line_sig="-".join(str(x) for x in sizes),
            n_solo=sum(1 for x in sizes if x == 1), max_line=sizes[0],
            ax_same=bool(same), ax_rel=rel,
            a1_pos=min(lp[a1], 3), a1_size=min(sz[a1], 4),
            a2_pos=min(lp[a2], 3), a2_size=min(sz[a2], 4),
            m1_pos=(min(lp[m1], 3) if m1 else 0),
            m1_size=(min(sz[m1], 4) if m1 else 0),
            mk_same12=bool(m1 and m2 and lg[m1] == lg[m2]),
            mk_conc=mk_conc, mk_nlines=mk_nlines,
            mk_top_is_a1=bool(m1 == a1),
            n_nige=sum(1 for c in range(1, 8) if st[c] == "逃"),
            n_oi=sum(1 for c in range(1, 8) if st[c] == "追"),
            n_ryo=sum(1 for c in range(1, 8) if st[c] == "両"),
            a1lead_style=st[a1ld],
            n_nige_lead=sum(1 for g, m in groups.items() if len(m) >= 2
                            and any(lp[c] == 1 and st[c] == "逃" for c in m)),
            n_nige_solo=sum(1 for g, m in groups.items() if len(m) == 1
                            and st[m[0]] == "逃"),
            n_oi_deputy=sum(1 for c in range(1, 8) if lp[c] == 2 and sz[c] >= 2
                            and st[c] == "追"),
            n_dep_stronger=dep_str,
            a1_dep_stronger=bool(a1dp and rp[a1dp[0]] > rp[a1ld]),
            a1lead_behind=float(BH[i][a1ld - 1]),
            nl_top3=nl3, nl_top5=nl5, n35_in_axisline=n35,
            axis_sum=float(d["AXIS_SUM"][i]), gap=float(d["GAP"][i]),
            arare=int(d["ARARE"][i]), type=str(d["TYPE"][i]),
            rtype=str(d["RTYPE"][i]), venue=str(d["VENUE"][i]),
            dayi=int(d["DAYI"][i]), agree=bool(d["AGREE"][i]),
            pw_ent=float(-(PW[i][PW[i] > 0] * np.log(PW[i][PW[i] > 0])).sum()),
            rp_sd=float(np.std(RP[i])),
            both_in3=bool(both), a1_in3=bool(a1 in in3), a2_in3=bool(a2 in in3),
            third_rank=int(third), pay_tf=float(d["PAY"][i]) / 100.0,
            ok=bool(d["TYPE"][i] != "" and d["TRIO_WIN"][i] >= 0 and d["OKPRED"][i]),
        )
        for k, v in rec.items():
            out[k].append(v)

    arr = {k: np.array(v) for k, v in out.items()}
    with OUT.open("wb") as f:
        pickle.dump(arr, f)
    print("rows", n, "->", OUT)
    for k in ("line_sig", "ax_rel", "a1lead_style", "mk_conc", "n_nige"):
        print(k, Counter(arr[k].tolist()).most_common(12))


if __name__ == "__main__":
    main()
