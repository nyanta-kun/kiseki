#!/usr/bin/env python3
"""H22 台側の組み立て: 二軸＋相手2車・合成オッズで見送り（事前登録どおり・README「ユーザー決定（2026-10-21）と事前登録 — H22」）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h22_build.py

- 台・① は h19_build と同じ（lineup_arms.run と全行一致を assert。① = h15_run.run15・L_lead なし）。
  ただし ② は H19 と違い「① が売るレース」に絞らず、**台の 7車 全レース（ctx が組めたもの）に当てる**（事前登録: 単独プラン）。
- 二軸 A,B = x.shape.order[0], order[1]（本番 race_shape と同じ）。配分は TL.allocate（本番 dutch・1万円）。
  入稿ゲートは S.gate_ok（平均想定払戻 > 2万 ∧ 全点 ≥ 2.0倍）を全腕に掛ける。
- 出力: data/exp_bet_review/h22/h22_arms.pkl

🔴 解釈（README に無く、結果を見る前にここで固定したもの）
  1. 頭 H = A,B のうち pw の高い方。pw が同値なら order[0]（p3 上位）。O = もう一方。
  2. 台の単騎の表現: LG に '' / '0' は無く、単騎は「1人だけのグループ」。単騎 = そのグループの人数が 1。
     二軸の片方が単騎なら、その軸からは同ライン候補は出ない。
  3. Pline: 同ライン候補 = 二軸のどちらかと同じ（2人以上の）ライン内の非軸の車。距離 = 同ラインにいる軸との
     |ライン内位置の差| の最小（二軸が同ライン・別ラインのどちらでも同じ式）。距離の昇順→p3 降順→車番で並べ、
     先に採る。足りなければ残りの車を p3 降順（単騎も他のライン外の車と同列）で補う。
  4. Pline_t: 同ライン候補は Pline と同じ。補う段で、単騎は脚質 逃/両 のものだけを p3 順に残し、
     追の単騎は「同ライン候補＋残した車」が 2 車に満たないときだけ末尾から補う。
     全員単騎のレース（7グループ・ライン情報が欠測の型）は Pline_t = Pline とする（件数は run で併記）。
  5. 候補集合 U は po_tf に無い目を除く（vintage 予測オッズは全 210 目そろうので実質影響なし）。
  6. F6: U を x.pr_tf（rank_7t3_blend_probs）の降順（同値は目の辞書順）に並べ、Σ(1/予測オッズ) が 1/(T/1万円) を
     超える手前（break）まで積む。**先頭1点だけで超える場合も 1点は持つ**（最低1点）。そのとき合成 < T なので見送りになる。
  7. 見送りは「実際に買う stakes（TL.allocate が 0円の点を落とした後）の合成 = 1/Σ(1/予測オッズ) × 1万円 < T」。
     入稿ゲート落ち（平均想定払戻 ≤ 2万 または 予測 2.0 倍未満の点を含む）は見送りとは別に数える。
  8. F6 の T=3万/5万 は積み上げの基準 T と見送りの T の両方（同じ値）。**F6 × T なし** は F6 の積み上げに T が要るので
     「T=3万で積んだ買い目を、見送りだけ外して（ゲートのみで）買う」と固定する（③無作為見送り対照の母集団もこれ。
     ②−③ は『同じ形・同じ点数ルールのまま、見送りの有無だけ』を比べる）。参考に F6@2 =「T=2万（入稿ゲートの床）で積み、
     見送りなし」も作る（判定外・記述のみ）。
  9. ③ の無作為見送り: ②の T なし版（上記）が買うレース（ゲート通過）から、②と**同じ件数**を全期間から無作為に残す
     （日ごとではなく全体・20 seed）。
 10. ④: ②が買ったレースで、②と同じ点数 n を 210目から（予測 2.0 倍未満を除き）確率降順に取り、dutch・入稿ゲート・
     同じ T を掛ける。落ちたレースは ④ は買わない（④ ⊆ ②）。n ごとに全レース分を作っておき run で引く。
 11. 合成オッズは予測オッズ（選択専用）。払戻は台の確定払戻（S.settle）。
"""
from __future__ import annotations
import itertools, pickle, time
from collections import Counter
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL
from src.type_lab import PLANS
import h15_run as H

DUTCH = PLANS["T_firm"]                       # alloc='dutch'
H22_DIR = D / "h22"
PKEYS = ("Pp3", "Pline", "Pline_t")
FORMS = ("F1", "F2", "F3", "F4", "F5", "F6@3", "F6@5", "F6@2")
F6_T = {"F6@3": 3.0, "F6@5": 5.0, "F6@2": 2.0}      # 合成オッズ（倍）= T/1万円


def partners(order, p3, lg, lp, st, kind):
    """相手2車（解釈 3・4）。"""
    A, B = order[0], order[1]
    size = Counter(lg.values())
    rest = list(order[2:])                       # p3 降順（race_shape の order）
    dist = {}
    for c in rest:
        for ax in (A, B):
            if size[lg[ax]] >= 2 and lg[c] == lg[ax]:
                d = abs(lp[c] - lp[ax])
                dist[c] = min(dist.get(c, 99.0), d)
    same = sorted(dist, key=lambda c: (dist[c], -p3[c], c))
    pool = [c for c in rest if c not in dist]
    if kind == "Pp3":
        return rest[:2]
    if kind == "Pline":
        return (same + pool)[:2]
    if len(size) == 7:                           # 全員単騎＝ライン欠測
        return (same + pool)[:2]
    single_late = [c for c in pool if size[lg[c]] == 1 and st[c] == "追"]
    keep = [c for c in pool if c not in single_late]
    return (same + keep + single_late)[:2]


def alloc(x, legs):
    """dutch 配分（本番 allocate）→ (stakes, gate_ok) or None。"""
    if not legs:
        return None
    stakes = TL.allocate(legs, x.po_tf, x.pr_tf, DUTCH)
    if not stakes:
        return None
    mean = TL.mean_expected_payout(stakes, x.po_tf)
    return stakes, bool(S.gate_ok(stakes, x.po_tf, mean))


def _row(x, stakes, gate):
    inv, pay = S.settle(x, stakes, False)
    comp = 1.0 / sum(1.0 / float(x.po_tf[c]) for c in stakes)
    return dict(inv=inv, pay=pay, n=len(stakes), comp=float(comp), gate=bool(gate), stakes=dict(stakes))


def forms_legs(A, B, H_, O, P2):
    """F1〜F5（U の部分集合・事前登録の定義）。"""
    f1 = [t for p in P2 for t in ((H_, O, p), (H_, p, O))]
    f2 = [t for p in P2 for t in ((A, B, p), (B, A, p))]
    f3 = [t for p in P2 for t in ((A, p, B), (B, p, A))]
    f4 = list(dict.fromkeys(f1 + f2))
    f5 = [t for p in P2 for t in itertools.permutations((A, B, p))]
    return dict(F1=f1, F2=f2, F3=f3, F4=f4, F5=f5)


def build_f6(x, U, tmult):
    """U を確率降順に積み、合成が tmult を割る手前で止める（最低1点）。"""
    cand = sorted(U, key=lambda q: (-float(x.pr_tf.get(q, 0.0)), q))
    cap = 1.0 / tmult
    out, s = [], 0.0
    for q in cand:
        inv_o = 1.0 / float(x.po_tf[q])
        if s + inv_o > cap + 1e-12 and out:
            break
        out.append(q); s += inv_o
        if s > cap + 1e-12:                      # 最低1点（先頭だけで超える）→ 1点で止める
            break
    return out


def ctrl_rows(x):
    """④: n=1..12 → 210目から（予測 2.0 倍未満を除き）確率降順の先頭 n 点・dutch。"""
    cand = [q for q, v in x.po_tf.items() if v >= TL.MIN_POINT_ODDS]
    cand.sort(key=lambda q: (-float(x.pr_tf.get(q, 0.0)), q))
    out = {}
    for n in range(1, 13):
        g = alloc(x, cand[:n])
        out[n] = _row(x, g[0], g[1]) if g else None
    return out


def h22_for(x, order, p3, pw, lg, lp, st):
    A, B = order[0], order[1]
    H_, O = (A, B) if pw[A] >= pw[B] else (B, A)      # 同値は order[0]
    size = Counter(lg.values())
    info = dict(A=A, B=B, H=H_, O=O, order=tuple(order), p3=p3, pw=pw, lg=lg, lp=lp, st=st,
                axes_same_line=bool(lg[A] == lg[B] and size[lg[A]] >= 2),
                all_single=bool(len(size) == 7), n_single=int(sum(1 for c in lg if size[lg[c]] == 1)))
    rows, pdict, cache = {}, {}, {}
    for pk in PKEYS:
        P2 = partners(order, p3, lg, lp, st, pk)
        pdict[pk] = tuple(P2)
        if tuple(P2) in cache:                         # 相手が同じなら買い目も同じ
            rows[pk] = cache[tuple(P2)]
            continue
        fl = forms_legs(A, B, H_, O, P2)
        r = {}
        for f, legs in fl.items():
            legs = [q for q in legs if q in x.po_tf]
            g = alloc(x, legs)
            r[f] = _row(x, g[0], g[1]) if g else None
        U = [q for q in fl["F5"] if q in x.po_tf]
        for f6, tm in F6_T.items():
            g = alloc(x, build_f6(x, U, tm))
            r[f6] = _row(x, g[0], g[1]) if g else None
        cache[tuple(P2)] = r
        rows[pk] = r
    info["partners"] = pdict
    info["win_tf"] = x.win_tf
    info["pay_tf"] = x.pay_tf
    return dict(info=info, rows=rows, ctrl=ctrl_rows(x))


def main():
    t0 = time.time()
    H22_DIR.mkdir(parents=True, exist_ok=True)
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
         & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    print(f"[母集団] {len(idx)}R → ctx {len(ok)}R  {time.time()-t0:.0f}s", flush=True)
    R.AXIS_GATE = True
    ref = R.run("current", {}, ok, cache)
    recs = H.run15(ok, cache)
    assert len(ref) == len(recs) and abs(sum(r["pay"] for r in ref) - sum(r["pay"] for r in recs)) < 1e-6 \
        and abs(sum(r["inv"] for r in ref) - sum(r["inv"] for r in recs)) < 1e-6, "lineup_arms.run と一致しない"
    cur = {r["race_key"]: r for r in recs}
    assert len(cur) == len(recs)
    print(f"[検証] lineup_arms.run と一致 {len(recs)}行 (main {sum(r['slot']=='main' for r in recs)} 高額枠 {sum(r['slot']=='highpay' for r in recs)})  {time.time()-t0:.0f}s", flush=True)
    races, arms = {}, {}
    cars = range(1, 8)
    for n_done, i in enumerate(ok):
        x = cache[i]
        p3 = {c: float(b["P3"][i][c - 1]) for c in cars}
        pw = {c: float(b["PW"][i][c - 1]) for c in cars}
        lg = {c: str(b["LG"][i][c - 1]) for c in cars}
        lp = {c: float(b["A_line_pos"][i][c - 1]) for c in cars}
        st = {c: str(b["ST"][i][c - 1]) for c in cars}
        races[x.key] = dict(day=x.date, rtype=x.rtype, tl=x.shape.type_label, i=i)
        arms[x.key] = h22_for(x, list(x.shape.order), p3, pw, lg, lp, st)
        if (n_done + 1) % 3000 == 0:
            print(f"  {n_done+1}/{len(ok)}  {time.time()-t0:.0f}s", flush=True)
    pickle.dump(dict(arms=arms, cur=cur, races=races), open(H22_DIR / "h22_arms.pkl", "wb"))
    print(f"[done] {len(arms)}R  {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
