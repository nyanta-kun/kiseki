"""本番の入稿ループ（軸信頼ゲート → 入稿ゲート → 波ごとの日次上限 → 高額枠）を再現する。

🔴 再現しているもの（`scripts/netkeirin_submit_type_lab.py::run` と
   `backend/src/services/keirin_type_lab_gate.py`）:

  1. 並び順 = `cap_priority(plan, axis_sum, rp_sd)` 降順（枠外と9車は 1.0）。
     同点は 会場→レース番号（本番と同じ deterministic な tie-break）。
  2. 棄却の順序 = 軸信頼ゲート → 入稿ゲート。**どちらも「判定した」に数える**。
  3. 上限 = `max(1, int(判定対象 × DAILY_CAP_RACE_FRACTION))`。
     分母は**枠外を除いた判定対象**、単位は **(日, 波)**。
  4. 枠外（決勝/準決勝/グレード3以上）は落とさず、上限も消費しない。
  5. 高額枠は **日単位のカウンタ**（波を跨いで消費）。`highpay_plan_for` が
     何本目を `_sign`(15万) / `_big`(40万・軸1外し) にするかを決める。
     狙いの行が無ければもう片方で出す。入稿ゲートに落ちたら**枠は消費しない**。

⚠️ 再現できていないもの（限界）:
  - 並び未公開（`missing_lineup`）の見送り。台に情報が無い。判定対象の分母を
    わずかに過大にする（本番は後の波へ回す）。
  - 9車・6車。台は7車のみ。9車は枠外なので上限の分母に入らず影響は小さい。
  - 締切超過（`closed`）・他ランク取得。全面置換後はほぼ空。
"""
from __future__ import annotations
import pickle
from collections import defaultdict
from statistics import median
import numpy as np

ROWS = None
THR = None
META = None

#: 高額枠の商品（`arms` のキー）。`S:all@150000` ≡ `{型}_sign`（signboard・計画15万）/
#: `S:bust@400000` ≡ `{型}_big`（軸1外し・計画40万）/ `S:bust@150000` ≡ 提案（軸1外し・計画15万）。
A_SIGN = "S:all@150000"
A_BIG = "S:bust@400000"
A_BUST15 = "S:bust@150000"

FRAC = 0.5
BIG_SLOTS = frozenset({2, 4})


def load():
    global ROWS, THR, META
    if ROWS is None:
        rows = pickle.load(open("/tmp/11_rows.pkl", "rb"))
        THR = pickle.load(open("/tmp/11_thr.pkl", "rb"))
        META = pickle.load(open("/tmp/12_meta.pkl", "rb"))
        for r in rows:
            m = META[r["key"]]
            r["race_key"] = m["race_key"]; r["venue"] = m["venue"]
            r["wave"] = m["wave"]; r["exempt"] = m["exempt"]
            r["race_no"] = int(m["race_key"].split("_")[2])
            r["type"] = str(r["type"])
            b = r["base"]
            r["axis_ok"] = bool(b["axis_ok"]); r["egate"] = bool(b["gate"])
            r["prio"] = 1.0 if r["exempt"] else float(b["capp"])
        ROWS = rows
    return ROWS


def ndays(win: str) -> int:
    return len({r["date"] for r in load() if r["win"] == win})


def inlay(r, nm) -> bool:
    load()
    if nm in (None, "all", "(全体)"):
        return True
    q, side, t = THR[nm]
    return (r[q] <= t) if side == "lo" else (r[q] >= t)


def _rec(r, arm_key, origin):
    a = r["base"] if arm_key is None else r["arms"][arm_key]
    return dict(date=r["date"], type=r["type"], wave=r["wave"], origin=origin,
                plan=(r["base"]["plan"] if arm_key is None else arm_key),
                inv=float(a["inv"]), pay=float(a["pay"]), k=int(a["k"]),
                mean=float(a.get("mean") or 0.0), race_key=r["race_key"])


def _hp_pick(r, n_done, product):
    """高額枠として出す腕のキー。出せなければ None（枠は消費しない）。"""
    arms = r["arms"]
    if product == "alt":                       # 本番: 2/4本目が `_big`
        want = A_BIG if (n_done + 1) in BIG_SLOTS else A_SIGN
        other = A_SIGN if want is A_BIG else A_BIG
    elif product == "alt10":                   # 参考: 偶数本目を `_big`
        want = A_BIG if (n_done + 1) % 2 == 0 else A_SIGN
        other = A_SIGN if want is A_BIG else A_BIG
    elif product == "sign":
        want, other = A_SIGN, A_BIG
    elif product == "big":
        want, other = A_BIG, A_SIGN
    elif product == "bust15":
        want, other = A_BUST15, A_SIGN
    else:
        raise KeyError(product)
    for k in (want, other):
        a = arms.get(k)
        if a is not None:
            return k if a["gate"] else None    # 本番も `_gate_reason` で落ちれば出さない
    return None


def simulate(win: str, *, hp_slots: int = 5, hp_types=("B", "C", "D"),
             supply=("cap",), product: str = "alt", layer=None,
             rng: np.random.Generator | None = None, rand_frac: float | None = None,
             frac: float = FRAC):
    """本番の入稿ループを回して、実際に出る商品の一覧を返す。

    supply: 高額枠の供給源。"cap"=日次上限落ち / "axis"=軸信頼ゲート落ち。
    layer:  高額枠の対象を層に限る（`THR` のキー）。
    rng/rand_frac: 無作為対照。層の代わりに確率 `rand_frac` で資格を与える。
    """
    rows = [r for r in load() if r["win"] == win]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    out = []
    for date in sorted(by_day):
        n_hp = 0
        for wave in ("morning", "noon", "night"):
            wr = [r for r in by_day[date] if r["wave"] == wave]
            if not wr:
                continue
            n_judged = sum(1 for r in wr if not r["exempt"])
            cap = max(1, int(n_judged * frac)) if (frac and n_judged > 0) else None
            wr.sort(key=lambda r: (-r["prio"], r["venue"], r["race_no"]))
            n_capped = 0
            for r in wr:
                drop = None
                if not r["axis_ok"]:
                    drop = "axis"
                elif not r["egate"]:
                    drop = "egate"
                elif r["exempt"]:
                    out.append(_rec(r, None, "exempt")); continue
                elif cap is not None and n_capped >= cap:
                    drop = "cap"
                if drop is None:
                    out.append(_rec(r, None, "normal")); n_capped += 1
                    continue
                if drop == "egate" or drop not in supply:
                    continue
                if n_hp >= hp_slots or r["type"] not in hp_types:
                    continue
                if rand_frac is not None:
                    if rng.random() >= rand_frac:
                        continue
                elif not inlay(r, layer):
                    continue
                k = _hp_pick(r, n_hp, product)
                if k is None:
                    continue
                out.append(_rec(r, k, "hp:" + drop)); n_hp += 1
    return out


def inventory(win: str, *, frac: float = FRAC):
    """供給在庫の実測 — 日次上限で捨てた行・軸信頼ゲートで落ちた行。"""
    rows = [r for r in load() if r["win"] == win]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    inv = []
    for date in sorted(by_day):
        for wave in ("morning", "noon", "night"):
            wr = [r for r in by_day[date] if r["wave"] == wave]
            if not wr:
                continue
            n_judged = sum(1 for r in wr if not r["exempt"])
            cap = max(1, int(n_judged * frac)) if (frac and n_judged > 0) else None
            wr.sort(key=lambda r: (-r["prio"], r["venue"], r["race_no"]))
            n_capped = 0
            for r in wr:
                if not r["axis_ok"]:
                    inv.append((r, "axis", wave)); continue
                if not r["egate"]:
                    inv.append((r, "egate", wave)); continue
                if r["exempt"]:
                    continue
                if cap is not None and n_capped >= cap:
                    inv.append((r, "cap", wave)); continue
                n_capped += 1
    return inv


def kpi(recs, nd: int) -> dict:
    if not recs:
        return dict(n=0)
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(n=len(recs), perday=len(recs) / nd,
                k=sum(r["k"] for r in recs) / len(recs),
                hit=len(hits) / len(recs) * 100,
                gami=len(gami) / len(hits) * 100 if hits else 0.0,
                shown=(len(hits) - len(gami)) / len(recs) * 100,
                med_pay=median(pays) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd,
                b30=sum(1 for p in pays if p >= 300_000) / nd,
                inv_day=inv / nd, roi=pay / inv * 100 if inv else 0.0)


HEAD = ("  {:30s} {:>6s} {:>5s} {:>6s} {:>6s} {:>8s} {:>9s} {:>7s} {:>7s} {:>9s} {:>6s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中", "払戻中央",
                "10万+/日", "30万+/日", "投資/日", "ROI"))


def line(nm, s) -> str:
    if not s.get("n"):
        return f"  {nm:30s}  (該当なし)"
    return (f"  {nm:30s} {s['perday']:6.2f} {s['k']:5.2f} {s['hit']:6.2f} {s['gami']:6.2f}"
            f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {s['big']:7.3f} {s['b30']:7.3f}"
            f" {s['inv_day']:9,.0f} {s['roi']:6.1f}")


def daily_arr(recs, days):
    """日ごとの (件数, 表示的中件数, 10万+件, 30万+件, 投資, 払戻)。"""
    d = {x: [0, 0, 0, 0, 0.0, 0.0] for x in days}
    for r in recs:
        a = d[r["date"]]
        a[0] += 1
        if r["pay"] > 0 and r["pay"] >= r["inv"]:
            a[1] += 1
        if r["pay"] >= 100_000:
            a[2] += 1
        if r["pay"] >= 300_000:
            a[3] += 1
        a[4] += r["inv"]; a[5] += r["pay"]
    return np.array([d[x] for x in days], dtype=float)


def dboot(base, arm, days, seeds: int = 2000):
    """日を resample する paired bootstrap。

    🔴 純増の腕はレース単位で対応が取れない（増えた行は base に無い）ので、
       **同じ日を共有する日単位**で組む。返り値は主要4指標の (Δ, lo, hi)。
    """
    B = daily_arr(base, days); A = daily_arr(arm, days)
    n = len(days)

    def stat(M):
        return dict(perday=M[:, 0].sum() / n,
                    shown=M[:, 1].sum() / max(M[:, 0].sum(), 1) * 100,
                    big=M[:, 2].sum() / n, b30=M[:, 3].sum() / n,
                    roi=M[:, 5].sum() / max(M[:, 4].sum(), 1) * 100)

    d0 = {k: stat(A)[k] - stat(B)[k] for k in ("perday", "shown", "big", "b30", "roi")}
    rng = np.random.default_rng(12345)
    acc = {k: np.empty(seeds) for k in d0}
    for s in range(seeds):
        j = rng.integers(0, n, n)
        sa, sb = stat(A[j]), stat(B[j])
        for k in d0:
            acc[k][s] = sa[k] - sb[k]
    return {k: (d0[k], float(np.percentile(acc[k], 2.5)),
                float(np.percentile(acc[k], 97.5))) for k in d0}


def ci(t) -> str:
    return f"{t[0]:+.3f} [{t[1]:+.3f},{t[2]:+.3f}]"
