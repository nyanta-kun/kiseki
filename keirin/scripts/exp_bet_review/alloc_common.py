"""H08 / H09 共通: 2025 の7車台で「現行ラインナップ（①）」を日次に再現し、レース別の候補商品を一度だけ組む。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/alloc_common.py build     # alloc_base.pkl を作る
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/alloc_common.py validate  # legacy 設定で lineup_arms.run と照合

- 台: data/exp_bet_review/race_type_board.npz（7車・vintage p3/pw）、予測オッズは探索用 vintage（h06_pop.pkl の V）。
  lineup_sim の /tmp ハードコードは S._Z の差し替えで避ける（lineup_sim.py 等は編集しない）。
- 本番の入稿経路（netkeirin_submit_type_lab.run）の順序を写す:
    軸信頼ゲート（先）→ 入稿ゲート → 日次上限（枠外を除く判定対象 × 0.5）→ 高額枠（B/C/D の _sign・5本）
    → L_lead 段（何も出していない・準決勝系/型E/モーニング開催を除く・上限なし）。
- 🔴 `lineup_arms.run` と違う点（本番に合わせた）:
    (a) A_ana を日次上限の枠外にしない（`cap_free_plans()` は TIER_SELL_ENABLED=False の間は空）
    (b) 日次上限の分母に入稿ゲートで落ちる行も数える（`_reject` の rj[4] が True）
    (c) 軸信頼ゲートを入稿ゲートより先に見て、入稿ゲートに落ちる行でも軸ゲート落ちなら高額枠へ回す
    (d) L_lead の段を持つ
  legacy=True で (a)(b)(c) を lineup_arms.run と同じにできる（validate で一致を確認する）。
"""
from __future__ import annotations
import importlib.util, json, pickle, sys, time
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
D = REPO / "data" / "exp_bet_review"

import lineup_sim as S                                   # noqa: E402
import lineup_arms as R                                  # noqa: E402
import src.type_lab as TL                                # noqa: E402
from src.type_lab import PLANS, sell_plans_for, highpay_plan_for, HIGHPAY_SLOTS_PER_DAY  # noqa: E402

_G = S._G
FRACTION = float(_G.DAILY_CAP_RACE_FRACTION)
H1_END = "2025-06-30"
ANA_SUFFIX = ("_sign", "_big")
ANA_KEYS = frozenset({"A_ana", "F_pay", "L_lead"})
MORNING_BEFORE_HOUR = 9
LEAD_EXCLUDED_TYPES = frozenset({"E"})


def is_ana(plan: str) -> bool:
    """穴商品（設計上の表示的中 10% 未満・穴狙いアイコン付き）か。"""
    return plan in ANA_KEYS or plan.endswith(ANA_SUFFIX)


# ───────────────────────────── 台 ─────────────────────────────
def load_board():
    pop = pickle.load(open(D / "h06_pop.pkl", "rb"))
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    b = {k: z[k] for k in S._NEED}
    po = b["PO"].copy(); po[:] = np.nan
    for i, v in pop["V"].items():
        po[i] = v.astype(po.dtype)
    b["PO"] = po
    S._Z = b
    return b, pop


def _built(x, plan_key):
    """(inv, pay, mean, n, gate_ok, used_key) or None。"""
    plan = PLANS[plan_key]
    got = S.build(x, plan)
    if not got:
        return None
    stakes, odds, used, mean = got
    inv, pay = S.settle(x, stakes, used.bet_type == "trio")
    return dict(plan=used.key, inv=inv, pay=pay, mean=float(mean), n=len(stakes),
                gate=bool(S.gate_ok(stakes, odds, mean)))


def prep_race(i: int):
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
    rec["main"] = _built(x, sell[0].key) if sell else None
    if rec["main"]:
        rec["main"]["axis_ok"] = bool(_G.passes_axis_gate(rec["main"]["plan"], rec["axis"], 7))
    rec["hit"] = _built(x, f"{tl}_hit")
    rec["sign"] = _built(x, f"{tl}_sign")
    rec["lead"] = _built(x, "L_lead")
    rec["flat"] = _built(x, "L_flat")        # 紙上の参考（H08 の表の候補には入れない）
    return rec


def _work(chunk):
    return [prep_race(i) for i in chunk]


def build_base():
    import multiprocessing as mp
    b, pop = load_board()
    ids = list(pop["pop"])
    t0 = time.time()
    chunks = [ids[j:j + 200] for j in range(0, len(ids), 200)]
    with mp.get_context("fork").Pool(8) as pool:
        out = []
        for n, r in enumerate(pool.imap(_work, chunks)):
            out += r
            if n % 10 == 0:
                print("prep", len(out), f"{time.time()-t0:.0f}s", flush=True)
    recs = {r["i"]: r for r in out if r is not None}
    print(f"races {len(ids)} ctx ok {len(recs)}  {time.time()-t0:.0f}s", flush=True)
    start = _load_start_at()
    pickle.dump(dict(recs=recs, start=start), open(D / "alloc_base.pkl", "wb"))
    return recs


def _load_start_at():
    """2025 の全レースの (race_key -> (venue_id, start_at epoch))。モーニング開催の判定用。"""
    p = D / "start_at_2025.json"
    if p.exists():
        return json.load(open(p))
    from _db import q
    _, rows = q("select race_key, venue_id, start_at from keirin.wt_races "
                "where race_date between '2025-01-01' and '2025-12-31'")
    d = {r[0]: [str(r[1]), str(r[2])] for r in rows}
    json.dump(d, open(p, "w"))
    return d


def load_base():
    z = pickle.load(open(D / "alloc_base.pkl", "rb"))
    return z["recs"], z["start"]


def morning_set(start):
    """本番 `morning_meeting_races` と同じ規則（開催=日付×場の第1R 発走が JST 9:00 前）。"""
    from datetime import datetime, timedelta, timezone
    jst = timezone(timedelta(hours=9))
    first = {}
    for rk, (venue, st) in start.items():
        try:
            ts = int(str(st))
        except (TypeError, ValueError):
            continue
        h = datetime.fromtimestamp(ts, jst)
        hour = h.hour + h.minute / 60
        k = (rk[:8], str(venue))
        first[k] = min(first.get(k, 99.0), hour)
    out = set()
    for rk, (venue, st) in start.items():
        if first.get((rk[:8], str(venue)), 99.0) < MORNING_BEFORE_HOUR:
            out.add(rk)
    return out


def lead_excluded(rec, morning):
    """L_lead を出さない理由（本番 `line_lead_excluded` + モーニング）。出してよければ None。"""
    if "準決勝" in str(rec["rtype"]):
        return "準決勝系"
    if rec["tl"] in LEAD_EXCLUDED_TYPES:
        return f"型{rec['tl']}"
    if rec["key"] in morning:
        return "モーニング"
    return None


def lead_avail(rec, morning):
    ld = rec["lead"]
    return bool(ld and ld["gate"] and lead_excluded(rec, morning) is None)


# ───────────────────────────── 日次ラインナップ（①） ─────────────────────────────
def exempt_race(rec):
    return bool(_G.daily_cap_exempt(rec["rtype"], rec["cupg"]))


def run_day(rows, morning, *, legacy=False, with_lead=True, forced=None):
    """1日ぶんの①。rows = その日の rec のリスト。

    返り値: sold = list of dict(key, plan, slot, inv, pay, mean, n, exempt, cap_used, src)
            status = {race_key: 売られなかった理由 'axis'|'cap'|'gate'|'nomain'|'sold'}
    forced: {race_key: 'skip'|'lead'|'sign'} … H08 で使う事前割り当て（該当レースは通常経路に載せない）
    """
    forced = forced or {}
    status, sold = {}, []
    cand = []                       # (rec, main, passes_axis)
    for r in rows:
        if r["key"] in forced:
            continue
        m = r["main"]
        if m is None:
            status[r["key"]] = "nomain"
            continue
        if legacy and not m["gate"]:
            status[r["key"]] = "gate"
            continue
        cand.append(r)

    def ex(r):
        e = exempt_race(r)
        if legacy and _G.daily_cap_exempt_plan(r["main"]["plan"]):
            e = True
        return e

    judged = [r for r in cand if not ex(r)]
    budget = max(1, int(len(judged) * FRACTION)) if judged else None

    def pri(r):
        if ex(r) and not legacy:
            return 1.0
        return _G.cap_priority(r["main"]["plan"], r["axis"], r["rp_sd"])

    order = sorted(cand, key=lambda r: (-pri(r), r["key"]))
    n_capped, dropped = 0, []
    for r in order:
        m = r["main"]
        if not m["axis_ok"]:
            dropped.append((r, "axis")); continue
        if not m["gate"]:
            status[r["key"]] = "gate"; continue
        if budget is not None and not ex(r) and n_capped >= budget:
            dropped.append((r, "cap")); continue
        e = ex(r)
        if not e:
            n_capped += 1
        sold.append(dict(key=r["key"], plan=m["plan"], slot="main", inv=m["inv"], pay=m["pay"],
                         mean=m["mean"], n=m["n"], exempt=e, cap_used=not e, src="main"))
        status[r["key"]] = "sold"
    n_done = 0
    for r, why in dropped:
        status[r["key"]] = why
        if n_done >= HIGHPAY_SLOTS_PER_DAY:
            continue
        want = highpay_plan_for(r["tl"], 7, n_done)
        if not want:
            continue
        sg = r["sign"]
        if not sg or not sg["gate"]:
            continue
        sold.append(dict(key=r["key"], plan=sg["plan"], slot="highpay", inv=sg["inv"], pay=sg["pay"],
                         mean=sg["mean"], n=sg["n"], exempt=True, cap_used=False, src=why))
        status[r["key"]] = "sold"
        n_done += 1
    if with_lead:
        taken = {s["key"] for s in sold} | set(forced)
        leads = [r for r in rows if r["key"] not in taken and lead_avail(r, morning)]
        for r in leads:
            ld = r["lead"]
            sold.append(dict(key=r["key"], plan="L_lead", slot="lead", inv=ld["inv"], pay=ld["pay"],
                             mean=ld["mean"], n=ld["n"], exempt=True, cap_used=False,
                             src=status.get(r["key"], "unsold")))
            status[r["key"]] = "sold"
    return sold, status


def by_day(recs):
    d = defaultdict(list)
    for r in recs.values():
        d[r["day"]].append(r)
    for v in d.values():
        v.sort(key=lambda r: r["key"])
    return dict(sorted(d.items()))


def lineup(recs, morning, *, legacy=False, with_lead=True):
    """全日。返り値: {day: (sold, status)}"""
    return {day: run_day(rows, morning, legacy=legacy, with_lead=with_lead)
            for day, rows in by_day(recs).items()}


# ───────────────────────────── 集計 ─────────────────────────────
class DayArr:
    """日配列（開催日の全体を軸にする）。"""

    def __init__(self, days):
        self.days = list(days)
        self.pos = {d: i for i, d in enumerate(self.days)}
        n = len(self.days)
        self.inv = np.zeros(n); self.pay = np.zeros(n); self.n = np.zeros(n)
        self.hit = np.zeros(n); self.big = np.zeros(n); self.ana = np.zeros(n)

    def add(self, day, inv, pay, plan):
        i = self.pos[day]
        self.inv[i] += inv; self.pay[i] += pay; self.n[i] += 1
        self.hit[i] += pay > inv
        self.big[i] += pay >= 100_000
        self.ana[i] += is_ana(plan)


def daily_roi(inv, pay):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(inv > 0, pay / inv * 100, np.nan)


def metrics(A, ix=None):
    ix = np.arange(len(A.days)) if ix is None else ix
    inv, pay = A.inv[ix], A.pay[ix]
    dr = daily_roi(inv, pay)
    ok = np.isfinite(dr)
    nd = len(ix)
    return dict(
        days_sold=int(ok.sum()), n_day=A.n[ix].sum() / nd,
        roi=pay.sum() / inv.sum() * 100 if inv.sum() else float("nan"),
        mean_daily=float(np.nanmean(dr)) if ok.any() else float("nan"),
        p10=float(np.nanpercentile(dr, 10)) if ok.any() else float("nan"),
        lt50=float((dr[ok] < 50).mean() * 100) if ok.any() else float("nan"),
        shown=A.hit[ix].sum() / max(A.n[ix].sum(), 1) * 100,
        big=A.big[ix].sum() / nd, ana=A.ana[ix].sum() / nd,
        ana_share=A.ana[ix].sum() / max(A.n[ix].sum(), 1) * 100)


def boot_idx(nd, rng, n):
    return rng.integers(0, nd, size=(n, nd))


def p10_of(inv, pay, B):
    """B: (n_boot, nd) の添字。各回の日次回収率 p10。"""
    i = inv[B]; p = pay[B]
    with np.errstate(invalid="ignore", divide="ignore"):
        dr = np.where(i > 0, p / i * 100, np.nan)
    return np.nanpercentile(dr, 10, axis=1)


def mean_daily_of(inv, pay, B):
    i = inv[B]; p = pay[B]
    with np.errstate(invalid="ignore", divide="ignore"):
        dr = np.where(i > 0, p / i * 100, np.nan)
    return np.nanmean(dr, axis=1)


def pooled_of(inv, pay, B):
    return pay[B].sum(1) / inv[B].sum(1) * 100


def lt50_of(inv, pay, B):
    i = inv[B]; p = pay[B]
    with np.errstate(invalid="ignore", divide="ignore"):
        dr = np.where(i > 0, p / i * 100, np.nan)
    ok = np.isfinite(dr)
    return ((dr < 50) & ok).sum(1) / ok.sum(1) * 100


def ci(a):
    a = np.asarray(a)
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def validate():
    recs, start = load_base()
    morning = morning_set(start)
    ids = sorted(recs)
    b, pop = load_board()
    # lineup_arms.run 用の ctx キャッシュ
    cache = {i: S.ctx(i) for i in ids}
    R.AXIS_GATE = True
    ref = R.run("current", {}, ids, cache)
    mine = lineup(recs, morning, legacy=True, with_lead=False)
    sold = [(s["key"], s["plan"], s["slot"], round(s["inv"]), round(s["pay"])) for d, (ss, _) in mine.items() for s in ss]
    refs = [(r["race_key"], r["plan"], r["slot"], round(r["inv"]), round(r["pay"])) for r in ref]
    print("lineup_arms.run 行数", len(refs), " legacy 再実装", len(sold))
    sa, sb = set(sold), set(refs)
    print("一致", len(sa & sb), " 片側のみ(再実装)", len(sa - sb), " 片側のみ(ref)", len(sb - sa))
    for x in list(sa - sb)[:5]: print("  mine only", x)
    for x in list(sb - sa)[:5]: print("  ref only", x)
    prod = lineup(recs, morning, legacy=False, with_lead=False)
    ps = [(s["key"], s["plan"], s["slot"]) for d, (ss, _) in prod.items() for s in ss]
    print("本番準拠(L_lead なし) 行数", len(ps), " 差 vs legacy", len(set(ps) ^ set((k, p, s) for k, p, s, _, _ in sold)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build_base()
    elif cmd == "validate":
        validate()
