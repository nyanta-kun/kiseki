"""H21 共通: 窓 2026-06-08〜10-05 の7車台を作り、腕ごとに「オッズだけ差し替えて」本番の規則で日次ラインナップを組む。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h21_core.py build      # window.pkl（入力・結果・①の予測板）
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h21_core.py arms       # 腕ごとの日次ラインナップ（arm_*.pkl）

台: ≤2026-08-04 は race_type_board.npz（P3/PW/PO）、2026-08-05〜 は wt_entries.pred_*_pct/100 と
    odds_prediction_tf.predict_board（本番モデル data/models/odds_tf_n7.txt）。
オッズが入る場所は `src/type_lab.py` の関数がすべて `pred_odds` 引数で受ける（DB を読む経路は無い）ので、
`lineup_sim.Ctx.po_tf` / `po_t3` の差し替え1点で、買い目の選択・配分・ゲート・上帯・ライン差し替え・並べ替え・
追加買い目のすべてに効く。
"""
from __future__ import annotations
import itertools, json, pickle, sys, time
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import alloc_common as C                                   # noqa: E402
S, TL, _G = C.S, C.TL, C._G
from src.type_lab import PLANS, sell_plans_for             # noqa: E402

D = REPO / "data/exp_bet_review"
H = D / "h21"
PERMS = S.PERMS
PIDX = {p: i for i, p in enumerate(PERMS)}
C3 = S.C3
C3IDX = {frozenset(c): i for i, c in enumerate(C3)}
BOARD_END = "2026-08-04"
W0, W1 = "2026-06-08", "2026-10-05"
SENT = 9999.0


# ───────────────────────── 窓の台を作る ─────────────────────────
def build_window():
    from src.result_top3 import winning_trifectas, representative
    from src.entry_health import missing_market_inputs
    from src import odds_prediction_tf as OT

    cov = pickle.load(open(H / "cover.pkl", "rb"))
    days = sorted({v[0] for v in cov["races"].values()})
    bz = np.load(D / "race_type_board.npz", allow_pickle=True)
    keys_b = [str(k) for k in bz["KEY"]]
    bidx = {k: i for i, k in enumerate(keys_b) if str(bz["DATE"][i]) >= W0}
    B = {k: bz[k] for k in ("DATE", "RTYPE", "CUPG", "DAYI", "P3", "PW", "PO", "LG", "ST", "A_line_pos",
                            "A_race_point", "BEHIND", "OKPRED", "WIN", "PAY")}
    skip = {}
    recs = []
    for d in days:
        ex = pickle.load(open(H / f"ex_{d}.pkl", "rb"))
        for rk in sorted(ex):
            e = ex[rk]
            meta, ent, fin = e["meta"], e["ent"], e["final"]
            if meta is None or len(ent) != 7 or set(ent) != set(range(1, 8)):
                skip[rk] = "no_entries"; continue
            # 結果と最終オッズ
            fo = [(v["finish"], c) for c, v in ent.items() if v["finish"] is not None]
            wins = winning_trifectas([(int(a), int(b)) for a, b in fo])
            rep = representative(wins)
            if not rep:
                skip[rk] = "no_result"; continue
            ftf, ftr = fin["tf"], fin["trio"]
            if rep not in ftf or frozenset(rep) not in ftr:
                skip[rk] = "no_final_odds"; continue
            r = dict(key=rk, date=d, src="board" if d <= BOARD_END else "db", win=rep, win_n=len(wins),
                     pay_tf=ftf[rep], odds_t3=ftr[frozenset(rep)], meta=meta, start=meta["start"],
                     ent=ent, final=fin, snap=e["snap"])
            if d <= BOARD_END:
                j = bidx.get(rk)
                if j is None or not bool(B["OKPRED"][j]):
                    skip[rk] = "no_board"; continue
                r.update(rtype=str(B["RTYPE"][j]), cupg=str(B["CUPG"][j]), dayi=int(B["DAYI"][j]),
                         P3=B["P3"][j].astype(float), PW=B["PW"][j].astype(float), PO1=B["PO"][j].astype(np.float64),
                         LG=[str(x) for x in B["LG"][j]], ST=[str(x) for x in B["ST"][j]],
                         LPOS=B["A_line_pos"][j].astype(float), RP=B["A_race_point"][j].astype(float),
                         BEH=B["BEHIND"][j].astype(float), board_win=int(B["WIN"][j]), board_pay=float(B["PAY"][j]))
            else:
                cars = list(range(1, 8))
                if any(ent[c]["p3pct"] is None or ent[c]["pwpct"] is None or ent[c]["race_point"] is None for c in cars):
                    skip[rk] = "no_pred"; continue
                if missing_market_inputs({"prediction_mark": ent[c]["mark"], "line_group": ent[c]["line_group"]}
                                         for c in cars):
                    skip[rk] = "lineup_issue"; continue
                p3 = np.array([ent[c]["p3pct"] / 100.0 for c in cars]); pw = np.array([ent[c]["pwpct"] / 100.0 for c in cars])
                r.update(rtype=str(meta["race_type"]), cupg=str(meta["cup_grade"]), dayi=int(meta["day_index"] or 0),
                         P3=p3, PW=pw,
                         LG=[str(int(ent[c]["line_group"])) for c in cars], ST=[str(ent[c]["style"] or "") for c in cars],
                         LPOS=np.array([float(ent[c]["line_pos"] or 0) for c in cars]),
                         RP=np.array([float(ent[c]["race_point"]) for c in cars]),
                         BEH=np.array([float(ent[c]["behind"] or 0) for c in cars]))
                m = {c: dict(race_point=float(ent[c]["race_point"]), mark=ent[c]["mark"],
                             player_class=ent[c]["player_class"], style=ent[c]["style"],
                             line_group=ent[c]["line_group"], line_size=ent[c]["line_size"],
                             line_pos=ent[c]["line_pos"], is_line_leader=ent[c]["is_line_leader"],
                             first_rate=ent[c]["first_rate"], second_rate=ent[c]["second_rate"],
                             third_rate=ent[c]["third_rate"]) for c in cars}
                try:
                    bd = OT.predict_board(cars, {c: p3[c - 1] for c in cars}, {c: pw[c - 1] for c in cars}, m)
                except Exception:                                         # noqa: BLE001
                    skip[rk] = "no_po"; continue
                po = np.full(210, np.nan)
                for t, v in bd.items():
                    po[PIDX[tuple(t)]] = v
                r["PO1"] = po
            if not np.isfinite(r["PO1"]).all():
                skip[rk] = "po_nan"; continue
            recs.append(r)
        print(d, len(recs), flush=True)
    import collections
    print("skip", dict(collections.Counter(skip.values())))
    pickle.dump(dict(recs=recs, skip=skip), open(H / "window.pkl", "wb"), protocol=4)
    return recs


def load_window():
    z = pickle.load(open(H / "window.pkl", "rb"))
    return z["recs"], z["skip"]


# ───────────────────────── 腕ごとの板 ─────────────────────────
def arm_boards(recs, lead, min_valid=60, min_share=None, use_trio=True):
    """lead(分前) のスナップショットで PO を作る。使えない/足りない race は ① のまま。

    返り値: PO (N,210) / TRIO (N 個の dict|None) / info (N 個の dict)
    min_valid: 有効な三連単の組がこれ未満なら使わない（race 台の ctx が組めない下限・60 は lineup_sim.ctx と同じ）。
    min_share: 指定時は 有効組/210 がこれ未満なら使わない（感度用）。
    """
    N = len(recs)
    PO = np.zeros((N, 210))
    TRIO = [None] * N
    info = []
    for i, r in enumerate(recs):
        PO[i] = r["PO1"]
        sn = r["snap"].get(lead)
        inf = dict(used=False, why="no_snapshot", type=None, lead_min=None, n_valid=0, share=0.0)
        if sn is not None:
            n = len(sn["tf"])
            inf.update(type=sn["type"], lead_min=sn["lead_min"], n_valid=n, share=n / 210.0)
            if n < min_valid:
                inf["why"] = "thin"
            elif min_share is not None and n / 210.0 < min_share:
                inf["why"] = "thin90"
            else:
                row = np.full(210, np.nan)
                for t, v in sn["tf"].items():
                    if len(t) == 3 and t in PIDX:
                        row[PIDX[t]] = v
                PO[i] = row
                TRIO[i] = dict(sn["trio"]) if (use_trio and len(sn["trio"]) >= 5) else None
                inf.update(used=True, why="ok")
        info.append(inf)
    return PO, TRIO, info


def board_dict(recs, PO):
    """lineup_sim が読む形の台（S._NEED のキー）。"""
    N = len(recs)
    z = {}
    z["KEY"] = np.array([r["key"] for r in recs]); z["DATE"] = np.array([r["date"] for r in recs])
    z["RTYPE"] = np.array([r["rtype"] for r in recs]); z["CUPG"] = np.array([r["cupg"] for r in recs])
    z["DAYI"] = np.array([r["dayi"] for r in recs], dtype=np.int16)
    z["P3"] = np.array([r["P3"] for r in recs]); z["PW"] = np.array([r["PW"] for r in recs])
    z["LG"] = np.array([r["LG"] for r in recs]); z["ST"] = np.array([r["ST"] for r in recs])
    z["A_line_pos"] = np.array([r["LPOS"] for r in recs]); z["A_race_point"] = np.array([r["RP"] for r in recs])
    z["BEHIND"] = np.array([r["BEH"] for r in recs])
    z["PO"] = PO
    z["WIN"] = np.array([PIDX[r["win"]] for r in recs]); z["PAY"] = np.array([r["pay_tf"] * 100.0 for r in recs])
    z["TRIO_WIN"] = np.array([C3IDX[frozenset(r["win"])] for r in recs])
    z["TRIO_PAY"] = np.array([r["odds_t3"] for r in recs])
    for k in ("TRIO_PO", "TYPE", "AGREE", "AXIS_SUM", "GAP", "OKPRED", "GRADE"):
        z[k] = np.zeros(N)
    return z


# ───────────────────────── 1レースの組み立て（alloc_common.prep_race の写し＋買い目の保持） ─────────────────────────
_ARM = dict(TRIO=None)
_orig_ctx = S.ctx


def _ctx_arm(i):
    x = _orig_ctx(i)
    if x is None:
        return None
    tr = _ARM["TRIO"][i] if _ARM["TRIO"] is not None else None
    if tr is not None:
        x.po_t3 = tr           # 三連複は実スナップショット（pr_t3 は確率なので不変）
    return x


S.ctx = _ctx_arm


def _built2(x, plan_key):
    plan = PLANS[plan_key]
    got = S.build(x, plan)
    if not got:
        return None
    stakes, odds, used, mean = got
    trio = used.bet_type == "trio"
    inv, pay = S.settle(x, stakes, trio)
    return dict(plan=used.key, inv=inv, pay=pay, mean=float(mean), n=len(stakes),
                gate=bool(S.gate_ok(stakes, odds, mean)), trio=trio,
                legs=[(tuple(sorted(c)) if trio else tuple(c), int(stakes[c]), float(odds[c])) for c in stakes])


def prep_race2(i):
    x = S.ctx(i)
    if x is None:
        return None
    tl = x.shape.type_label
    rec = dict(i=i, key=x.key, day=x.date, rtype=x.rtype, cupg=x.cupg, tl=tl,
               axis=float(x.shape.axis_sum), ent=float(x.shape.pw_ent),
               rp_sd=x.rp_sd, lead_legs=bool(x.shape.lead_legs))
    trio_ok = None
    if tl == "A":
        g = S.build(x, PLANS["A_trio"])
        trio_ok = bool(g and S.gate_ok(g[0], g[1], g[3]))
    sell = sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent, trio_ok=trio_ok)
    rec["main"] = _built2(x, sell[0].key) if sell else None
    if rec["main"]:
        rec["main"]["axis_ok"] = bool(_G.passes_axis_gate(rec["main"]["plan"], rec["axis"], 7))
    rec["hit"] = _built2(x, f"{tl}_hit")
    rec["sign"] = _built2(x, f"{tl}_sign")
    rec["lead"] = _built2(x, "L_lead")
    rec["flat"] = _built2(x, "L_flat")
    return rec


def _work(chunk):
    return [prep_race2(i) for i in chunk]


def run_arm(recs, PO, TRIO, workers=8):
    """腕1本ぶん: 全レースの商品を組み → 日次ラインナップ。返り値 (recs_by_i, lineup{day:(sold,status)})。"""
    import multiprocessing as mp
    S._Z = board_dict(recs, PO)
    _ARM["TRIO"] = TRIO
    ids = list(range(len(recs)))
    chunks = [ids[j:j + 100] for j in range(0, len(ids), 100)]
    t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        out = []
        for n, r in enumerate(pool.imap(_work, chunks)):
            out += r
            if n % 10 == 0:
                print("  prep", len(out), f"{time.time()-t0:.0f}s", flush=True)
    by_i = {r["i"]: r for r in out if r is not None}
    start = {k: tuple(v) for k, v in json.load(open(H / "start_all.json")).items()}
    morning = C.morning_set(start)
    lineup = C.lineup(by_i, morning, legacy=False, with_lead=True)
    return by_i, lineup


def main_arms():
    recs, _ = load_window()
    print("races", len(recs), flush=True)
    specs = {
        "A1": None,
        "A2": dict(lead=120), "A3": dict(lead=60),
        "A2s90": dict(lead=120, min_share=0.9), "A3s90": dict(lead=60, min_share=0.9),
        # 三連複を実三連複スナップショットではなく、実三連単スナップショットを畳んで作る（本番の導出と同じ）
        "A2f": dict(lead=120, use_trio=False), "A3f": dict(lead=60, use_trio=False),
    }
    import sys as _s
    want = _s.argv[2:] or list(specs)
    for name in want:
        sp = specs[name]
        if sp is None:
            PO = np.array([r["PO1"] for r in recs]); TRIO = None
            info = [dict(used=False, why="arm1", type=None, lead_min=None, n_valid=210, share=1.0) for _ in recs]
        else:
            PO, TRIO, info = arm_boards(recs, sp["lead"], min_share=sp.get("min_share"), use_trio=sp.get("use_trio", True))
        t0 = time.time()
        by_i, lineup = run_arm(recs, PO, TRIO)
        pickle.dump(dict(name=name, info=info, by_i=by_i, lineup=lineup, keys=[r["key"] for r in recs]),
                    open(H / f"arm_{name}.pkl", "wb"), protocol=4)
        n_sold = sum(len(v[0]) for v in lineup.values())
        print(name, "done", f"{time.time()-t0:.0f}s", "sold", n_sold, "used", sum(1 for x in info if x["used"]), flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build_window()
    elif cmd == "arms":
        main_arms()
