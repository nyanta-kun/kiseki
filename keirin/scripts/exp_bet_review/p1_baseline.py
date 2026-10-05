#!/usr/bin/env python3
"""P1': 型ラボ買い目の「入稿時点オッズ」での比較（現状測定のみ・読み取り専用）。

    # 1. 抽出（VPS の Postgres を読む。日別キャッシュ -> data/exp_bet_review/）
    set -a; source ~/.config/kiseki/env; set +a
    .venv/bin/python scripts/exp_bet_review/p1_baseline.py extract --from 2026-08-27 --to 2026-10-04

    # 2. 集計（キャッシュだけを読む。DB 不要）-> docs/bet_review/reports/baseline.md
    .venv/bin/python scripts/exp_bet_review/p1_baseline.py report

腕（すべて同じレース・同じ券種・同じ点数 k・同じ予算 1万円。採点は **最終オッズ×賭け金**）
  B  型ラボの実際の買い目と配分（type_lab_picks）
  A  市場のみ（朝）: 朝スナップショット（wt_odds_snapshot morning）の低オッズ順に上位 k 点、朝オッズでダッチ
  C  予測オッズのみ: 予測オッズ板（odds_tf・DB の pred_* から再構成）の低オッズ順に上位 k 点、予測オッズでダッチ
  F  市場のみ（最終・参考）: 最終オッズの低オッズ順に上位 k 点、最終オッズでダッチ（直前購入の上限値）
  B' 参考: B と同じ買い目を、予測オッズでダッチに配り直したもの（配分の違いだけを切り出す）

🔴 払戻 = 最終オッズ x 賭け金 は「締切直前に買えた」場合の上限値。朝・入稿時点では買えない。
🔴 本番コード（src/・netkeirin_*・cron）は import するだけで変更しない。DB は読み取り専用。
🔴 採否・改善提案は書かない（2026-07-16 以降は現状測定のみ。監査の取り決め）。
"""
from __future__ import annotations

import argparse
import collections
import json
import pickle
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

CACHE = REPO / "data" / "exp_bet_review"
REPORT = REPO / "docs" / "bet_review" / "reports" / "baseline.md"
BUDGET = 10000
INVALID_ODDS = 9999.0          # 9999.9 は「板が立っていない/打ち切り」の置き値
N_BOOT = 2000
SEED = 20261005


# ───────────────────────────── 抽出 ─────────────────────────────

def _norm_combo(bt: str, comb: str) -> str | None:
    import re
    nums = [int(x) for x in re.split(r"[-=→]", str(comb)) if x.strip().isdigit()]
    if len(nums) != 3:
        return None
    return "=".join(str(x) for x in sorted(nums)) if bt == "trio" else "-".join(map(str, nums))


def extract_day(day: str) -> dict:
    """1開催日ぶんを辞書にする（読み取りのみ）。"""
    from _db import connect
    from src import odds_prediction_tf as tf
    from src.result_top3 import winning_trifectas, winning_trios

    out: dict = {"day": day, "races": {}}
    with connect() as c, c.cursor() as cur:
        cur.execute(
            "SELECT race_key, plan_key, mode, bet_type, n_legs, budget, legs, generated_at, "
            "settled_at, hit, payout, void_refund, race_type, n_entries, type_label "
            "FROM keirin.type_lab_picks WHERE race_date=%s AND mode IN ('live','live9')", (day,))
        picks = cur.fetchall()
        if not picks:
            return out
        keys = sorted({p[0] for p in picks})
        cur.execute("SELECT race_key, rank_key, status, origin, is_confident, submitted_at, session, "
                    "bet_detail FROM keirin.netkeirin_submissions WHERE race_key = ANY(%s)", (keys,))
        subs = {(r[0], r[1]): r for r in cur.fetchall()}
        cur.execute("SELECT race_key, frame_no, finish_order FROM keirin.wt_entries "
                    "WHERE race_key = ANY(%s) ORDER BY race_key, frame_no", (keys,))
        ent: dict = defaultdict(list)
        for rk, fn, fo in cur.fetchall():
            ent[rk].append((int(fn), fo))
        final: dict = defaultdict(dict)
        cur.execute("SELECT race_key, bet_type, combination, odds_value FROM keirin.wt_odds "
                    "WHERE race_key = ANY(%s) AND bet_type IN ('trio','trifecta')", (keys,))
        for rk, bt, comb, v in cur.fetchall():
            k = _norm_combo(bt, comb)
            if k is not None and v is not None and 0 < v < 99999:
                final[rk][(bt, k)] = float(v)
        # 板のスナップショット: snapshot_type ごと（morning / h10 / h12 / ... / evening）
        snaps: dict = defaultdict(lambda: defaultdict(dict))     # rk -> type -> {bt|combo: odds}
        snap_t: dict = defaultdict(dict)                         # rk -> type -> (min_at, max_at)
        cur.execute("SELECT race_key, snapshot_type, bet_type, combination, odds_value, snapshot_at "
                    "FROM keirin.wt_odds_snapshot WHERE race_key = ANY(%s) "
                    "AND bet_type IN ('trio','trifecta')", (keys,))
        for rk, st, bt, comb, v, at in cur.fetchall():
            k = _norm_combo(bt, comb)
            if k is not None and v is not None and v > 0:
                snaps[rk][st][f"{bt}|{k}"] = float(v)
                lo, hi = snap_t[rk].get(st, (at, at))
                snap_t[rk][st] = (min(lo, at), max(hi, at))
        cur.execute("SELECT race_key, race_date, n_entries FROM keirin.wt_races WHERE race_key = ANY(%s)", (keys,))
        rmeta = {r[0]: r for r in cur.fetchall()}
        cur.execute("SELECT race_key, plan_key, mode, pred_mean_payout, pred_min_payout "
                    "FROM keirin.type_lab_picks WHERE race_date=%s AND mode IN ('live','live9')", (day,))
        pm = {(r[0], r[1], r[2]): (r[3], r[4]) for r in cur.fetchall()}

    for rk in keys:
        fin = [(int(fo), fn) for fn, fo in ent[rk] if fo is not None and 1 <= int(fo) <= 3]
        fin.sort()
        wins_tf = ["-".join(map(str, w)) for w in winning_trifectas(fin)] if fin else []
        wins_trio = ["=".join(map(str, sorted(w))) for w in winning_trios(fin)] if fin else []
        # 予測板（DB の pred_* から再構成）。作れなければ None
        board = None
        try:
            b = tf.predicted_trifecta_board(rk)
            board = {"-".join(map(str, k)): float(v) for k, v in b.items() if v and v > 0}
        except Exception as e:  # noqa: BLE001
            board = None
            out.setdefault("board_err", {})[rk] = str(e)[:120]
        out["races"][rk] = {
            "entrants": sorted(fn for fn, _ in ent[rk]),
            "n_finish": len(fin), "wins_tf": wins_tf, "wins_trio": wins_trio,
            "final": {f"{bt}|{k}": v for (bt, k), v in final.get(rk, {}).items()},
            "snaps": {t: dict(d) for t, d in snaps.get(rk, {}).items()},
            "snap_t": dict(snap_t.get(rk, {})),
            "morning": dict(snaps.get(rk, {}).get("morning", {})),
            "snap_at": snap_t.get(rk, {}).get("morning", (None, None))[0],
            "n_entries_row": (rmeta.get(rk) or (None, None, None))[2],
            "race_date": str((rmeta.get(rk) or (None, None, None))[1]),
            "board": board,
            "picks": [],
        }
    for (rk, pk, mode, bt, nl, bud, legs, gen, settled, hit, pay, vr, rtype, nent, tl) in picks:
        s = subs.get((rk, pk))
        out["races"][rk]["picks"].append({
            "plan_key": pk, "mode": mode, "bet_type": bt, "n_legs": nl, "budget": bud,
            "legs": legs if not isinstance(legs, str) else json.loads(legs),
            "generated_at": gen, "settled_at": settled, "hit": hit, "payout": pay,
            "void_refund": vr, "race_type": rtype, "n_entries": nent, "type_label": tl,
            "pred_mean_payout": (pm.get((rk, pk, mode)) or (None, None))[0],
            "pred_min_payout": (pm.get((rk, pk, mode)) or (None, None))[1],
            "sub_status": s[2] if s else None, "sub_origin": s[3] if s else None,
            "sub_confident": s[4] if s else None, "sub_at": s[5] if s else None,
            "sub_session": s[6] if s else None,
            "sub_lines": ((s[7] if not isinstance(s[7], str) else json.loads(s[7])) if s and s[7] else None),
        })
    return out


def cmd_extract(a) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    d0, d1 = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    d = d0
    while d <= d1:
        f = CACHE / f"day_{d}.pkl"
        if f.exists() and not a.force:
            d += timedelta(days=1)
            continue
        r = extract_day(d.isoformat())
        with open(f, "wb") as fh:
            pickle.dump(r, fh)
        print(f"{d} races={len(r['races'])}", flush=True)
        d += timedelta(days=1)


# ───────────────────────────── 集計（キャッシュのみ） ─────────────────────────────

def _cars(combo: str) -> list[int]:
    return [int(x) for x in combo.replace("=", "-").split("-")]


def _fold_trio(board: dict[str, float]) -> dict[str, float]:
    """三連単の予測板 -> 三連複: 1 / Σ_perm (1/PO)。build_type_lab_picks._fold_to_trio と同じ式。"""
    q: dict[str, float] = defaultdict(float)
    for k, o in board.items():
        q["=".join(map(str, sorted(_cars(k))))] += 1.0 / o
    return {k: 1.0 / v for k, v in q.items() if v > 0}


def _topk(odds: dict[str, float], k: int, allowed_valid: bool = True) -> list[str]:
    items = [(o, c) for c, o in odds.items() if o < INVALID_ODDS]
    items.sort()
    return [c for _, c in items[:k]]


def _score(stakes: dict[str, int], bt: str, race: dict, entrants: set[int]) -> dict | None:
    """最終オッズ x 賭け金で採点。勝ち目の最終オッズが引けなければ None（保留）。"""
    wins = race["wins_trio"] if bt == "trio" else race["wins_tf"]
    pay = 0
    for c, s in stakes.items():
        if c in wins:
            o = race["final"].get(f"{bt}|{c}")
            if o is None:
                return "nofinal"
            pay += int(round(s * o))
    void = sum(s for c, s in stakes.items() if any(x not in entrants for x in _cars(c)))
    cost = sum(stakes.values())
    return {"pay": pay, "cost": cost, "void": void, "net_cost": cost - void,
            "hit": pay > 0, "n": len(stakes)}


def build_rows() -> tuple[list[dict], dict]:
    """売った/候補の各 pick について 5 つの腕の結果を作る。"""
    from src.type_lab import PLANS, allocate

    rows: list[dict] = []
    meta: dict = defaultdict(int)
    sa_f = CACHE / "start_at.json"
    start_at = json.load(open(sa_f)) if sa_f.exists() else {}
    for f in sorted(CACHE.glob("day_*.pkl")):
        day = pickle.load(open(f, "rb"))
        for rk, race in day["races"].items():
            for p in race["picks"]:
                meta["picks_total"] += 1
                if p["settled_at"] is None or not race["wins_tf"]:
                    meta["unsettled"] += 1
                    continue
                bt = p["bet_type"]
                legs = p["legs"]
                k = len(legs)
                plan = PLANS.get(p["plan_key"])
                if plan is None:
                    meta["no_plan"] += 1
                    continue
                ent = set(race["entrants"])
                b_st = {l["combo"]: int(l["stake"]) for l in legs}
                B = _score(b_st, bt, race, ent)
                if B == "nofinal":
                    meta["B_no_final_odds"] += 1
                    continue
                # 整合性: 自前の採点と DB の採点が一致するか
                meta["B_pay_mismatch"] += int(B["pay"] != (p["payout"] or 0))
                meta["B_void_mismatch"] += int(B["void"] != (p["void_refund"] or 0))
                dutch = replace(plan, alloc="dutch")
                mrn = {c.split("|", 1)[1]: v for c, v in race["morning"].items()
                       if c.startswith(bt + "|")}
                fin = {c.split("|", 1)[1]: v for c, v in race["final"].items()
                       if c.startswith(bt + "|")}
                board = race["board"]
                pred = None
                if board:
                    pred = _fold_trio(board) if bt == "trio" else board
                row = {
                    "race_key": rk, "day": day["day"], "plan": p["plan_key"], "mode": p["mode"],
                    "bt": bt, "k": k, "race_type": p["race_type"], "n_entries": p["n_entries"],
                    "sold": p["sub_status"] == "published", "conf": bool(p["sub_confident"]),
                    "origin": p["sub_origin"], "gen_hour": p["generated_at"].hour,
                    "sub_at": p["sub_at"], "gen_at": p["generated_at"],
                    "snap_at": race["snap_at"],
                    "alloc": plan.alloc, "B": B,
                }
                # 朝板の有効率
                n_exp = len(fin) or None
                valid_m = sum(1 for v in mrn.values() if v < INVALID_ODDS)
                row["morning_valid_frac"] = valid_m / max(len(mrn), 1) if mrn else 0.0
                row["morning_n"] = len(mrn)
                # 売った買い目（入稿の bet_detail）と type_lab_picks.legs の一致検査
                if p["sub_lines"] and row["sold"]:
                    sl = {}
                    for ln in (p["sub_lines"].get("lines") or []):
                        sl[_norm_combo(bt, ln["combo"])] = int(ln["stake"])
                    meta["sold_checked"] += 1
                    if sl != b_st:
                        meta["sold_legs_differ"] += 1
                        meta.setdefault("sold_legs_differ_list", []).append(
                            (rk, p["plan_key"], str(p["sub_at"])[:19], str(p["generated_at"])[:19],
                             sorted(set(sl.items()) ^ set(b_st.items()))[:4]))
                # 基準時刻: 売った pick は入稿時刻、それ以外は生成時刻。
                # 🔴 type_lab_picks は (race_key, plan_key, mode) 一意で昼・夕の波が同じ行を再生成すると
                #    generated_at が上書きされる。売った pick は入稿時刻の方が正しい。
                ref_at = p["sub_at"] if (p["sub_status"] == "published" and p["sub_at"]) else p["generated_at"]
                asof_t, asof_odds = None, None
                best = None
                for t, (lo, hi) in race["snap_t"].items():
                    if lo is not None and lo <= ref_at and (best is None or lo > best[0]):
                        best = (lo, t)
                if best:
                    asof_t = best[1]
                    asof_odds = {c.split("|", 1)[1]: v for c, v in race["snaps"][asof_t].items()
                                 if c.startswith(bt + "|")}
                row["asof_type"] = asof_t
                row["asof_valid_frac"] = (sum(1 for v in asof_odds.values() if v < INVALID_ODDS)
                                          / max(len(asof_odds), 1)) if asof_odds else 0.0
                row["why"] = {}
                sels = {}
                # 腕 A(朝) / Ag(生成時点の板) / C(予測) / F(最終)
                for name, odds in (("A", mrn), ("Ag", asof_odds), ("C", pred), ("F", fin)):
                    if not odds:
                        row[name] = None
                        row["why"][name] = "板なし"
                        continue
                    sel = _topk(odds, k)
                    sels[name] = sel
                    if len(sel) < k:
                        row[name] = None
                        row["why"][name] = "有効オッズがk点に満たない"
                        continue
                    st = allocate(sel, odds, {}, dutch, BUDGET)
                    if not st:
                        row[name] = None
                        row["why"][name] = "配分不能"
                        continue
                    sc = _score(st, bt, race, ent)
                    if sc == "nofinal":
                        row[name] = None
                        row["why"][name] = "当たり目の最終オッズ欠損"
                    else:
                        row[name] = sc
                row["a_f_overlap"] = (len(set(sels["A"]) & set(sels["F"])) / k
                                      if sels.get("A") and sels.get("F") and len(sels["A"]) == k
                                      and len(sels["F"]) == k else None)
                # B' : B の買い目を予測オッズでダッチに配り直す
                if pred:
                    odds_b = {c: pred.get(c) for c in b_st}
                    if all(v for v in odds_b.values()):
                        st = allocate(list(b_st), odds_b, {}, dutch, BUDGET)
                        sc = _score(st, bt, race, ent) if st else None
                        row["Bp"] = None if sc == "nofinal" else sc
                    else:
                        row["Bp"] = None
                else:
                    row["Bp"] = None
                # 予測板の再構成の忠実度（B の保存済み pred_odds との比）
                if pred:
                    r = [pred[l["combo"]] / l["pred_odds"] for l in legs
                         if l["combo"] in pred and l.get("pred_odds")]
                    row["pred_fid"] = float(np.median(r)) if r else None
                else:
                    row["pred_fid"] = None
                # オッズの動き（買った組）
                mv = []
                for l in legs:
                    c = l["combo"]
                    po, mo, fo = l.get("pred_odds"), mrn.get(c), fin.get(c)
                    mv.append({"combo": c, "pred": po, "morn": mo if (mo and mo < INVALID_ODDS) else None,
                               "final": fo, "stake": int(l["stake"]),
                               "won": c in (race["wins_trio"] if bt == "trio" else race["wins_tf"])})
                row["moves"] = mv
                row["pred_mean_payout"] = (float(p["pred_mean_payout"])
                                           if p.get("pred_mean_payout") is not None else None)
                row["ref_at"] = ref_at
                sa = start_at.get(rk)
                row["start_at"] = datetime.fromisoformat(sa) if sa else None
                row["post_start_gen"] = bool(sa and p["generated_at"] > datetime.fromisoformat(sa))
                row["start_band"] = (None if not sa else "デイ(<15時)" if row["start_at"].hour < 15
                                     else "ナイター(15-20時)" if row["start_at"].hour < 20 else "ミッド(>=20時)")
                row["sess"] = "朝便" if ref_at.hour < 9 else "昼夜便"
                row["gen_lt_sub"] = bool(p["sub_status"] == "published" and p["sub_at"]
                                         and p["sub_at"] < p["generated_at"])
                row["board_err"] = (day.get("board_err") or {}).get(rk)
                row["ent_mismatch"] = (race.get("n_entries_row") is not None
                                       and len(race["entrants"]) != race["n_entries_row"])
                st_ = race["snap_t"].get("morning")
                row["morning_spread_min"] = ((st_[1] - st_[0]).total_seconds() / 60) if st_ else None
                row["morning_date_ok"] = bool(st_ and str(st_[0].date()) == race.get("race_date"))
                rows.append(row)
    return rows, dict(meta)


# ───────────────────────────── 統計 ─────────────────────────────

def _agg(rows: list[dict], arm: str):
    """日別の (payout, net_cost) 配列などを返す。"""
    byday: dict[str, list] = defaultdict(lambda: [0, 0])
    pays = []
    for r in rows:
        x = r[arm]
        byday[r["day"]][0] += x["pay"]
        byday[r["day"]][1] += x["net_cost"]
        pays.append(x["pay"])
    return byday, pays


def _roi(rows, arm):
    pay = sum(r[arm]["pay"] for r in rows)
    cost = sum(r[arm]["net_cost"] for r in rows)
    return pay / cost if cost else float("nan")


def boot(rows: list[dict], arms: list[str], seed=SEED):
    """開催日リサンプルのブートストラップ。arms の ROI の分布を (n_boot, len(arms)) で返す。"""
    days = sorted({r["day"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    P = np.zeros((len(days), len(arms)))
    C = np.zeros((len(days), len(arms)))
    for r in rows:
        for j, a in enumerate(arms):
            P[idx[r["day"]], j] += r[a]["pay"]
            C[idx[r["day"]], j] += r[a]["net_cost"]
    rng = np.random.default_rng(seed)
    out = np.empty((N_BOOT, len(arms)))
    for b in range(N_BOOT):
        s = rng.integers(0, len(days), len(days))
        out[b] = P[s].sum(0) / np.maximum(C[s].sum(0), 1)
    return out


def ci(x):
    lo, hi = np.percentile(x, [2.5, 97.5])
    return lo, hi


def summarize(rows: list[dict], arms=("B", "A", "C", "F")) -> dict:
    arms = list(arms)
    bt = boot(rows, arms)
    res = {"n": len(rows), "days": len({r["day"] for r in rows})}
    for j, a in enumerate(arms):
        pays = [r[a]["pay"] for r in rows]
        cost = sum(r[a]["net_cost"] for r in rows)
        hit = np.mean([r[a]["hit"] for r in rows])
        hit_disp = np.mean([r[a]["pay"] > r[a]["net_cost"] for r in rows])
        top = max(pays) if pays else 0
        big = sum(1 for p in pays if p >= 100000)
        hp = [p for p in pays if p > 0]
        res[a] = {
            "roi": sum(pays) / cost if cost else float("nan"),
            "hit": float(hit), "hit_disp": float(hit_disp),
            "roi_ex_top": (sum(pays) - top) / cost if cost else float("nan"),
            "ci": ci(bt[:, j]),
            "big": big, "big_per_day": big / res["days"],
            "med_hit_pay": float(np.median(hp)) if hp else float("nan"),
            "cost": cost,
        }
    for a in arms[1:]:
        ja, jb = arms.index(a), arms.index("B")
        if a == "B":
            continue
        d = bt[:, jb] - bt[:, ja]
        res[f"B-{a}"] = (float(_roi(rows, "B") - _roi(rows, a)), ci(d))
    return res


def monthly(rows, arm):
    m: dict[str, list] = defaultdict(lambda: [0, 0])
    for r in rows:
        m[r["day"][:7]][0] += r[arm]["pay"]
        m[r["day"][:7]][1] += r[arm]["net_cost"]
    return {k: (v[0] / v[1] if v[1] else float("nan")) for k, v in sorted(m.items())}


def pct(x, d=1):
    return "nan" if x != x else f"{x * 100:.{d}f}%"


def pp(x, d=1):
    return "nan" if x != x else f"{x * 100:+.{d}f}pt"


ARM_NAME = {"B": "B 型ラボ", "A": "A 朝の市場", "Ag": "Ag 生成時点の市場", "C": "C 予測オッズ",
            "F": "F 最終の市場(参考)", "Bp": "B′ Bの目を予測ダッチ"}
ARMS_MAIN = ("B", "A", "C", "F")
ARMS_WAVE = ("B", "A", "Ag", "C", "F")


def family(plan: str) -> str:
    if plan.startswith("T_"):
        return "段(T_*)"
    if plan.startswith("L_"):
        return "逃げ先頭(L_*)"
    if plan.endswith("_sign") or plan.endswith("_big"):
        return "高額・看板枠(_sign/_big)"
    return "本線(_hit/_pay/_trio/_ana/F_line)"


# ───────────────────────────── 前提（DB を読む） ─────────────────────────────

def cmd_startat(a) -> None:
    """全 race_key の発走時刻(JST・naive)を引いて start_at.json に保存する（読み取りのみ）。"""
    from _db import q
    keys = sorted({k for f in CACHE.glob("day_*.pkl")
                   for k in pickle.load(open(f, "rb"))["races"]})
    out = {}
    for i in range(0, len(keys), 500):
        c, r = q("SELECT race_key, to_timestamp(start_at::bigint) at time zone 'Asia/Tokyo' "
                 "FROM keirin.wt_races WHERE race_key = ANY(%s) AND start_at ~ '^[0-9]+$'", (keys[i:i + 500],))
        for rk, t in r:
            out[rk] = str(t)[:19]
    json.dump(out, open(CACHE / "start_at.json", "w"))
    print(f"start_at {len(out)}/{len(keys)}")


def cmd_premise(a) -> None:
    from _db import q
    out = {}
    c, r = q("""SELECT snapshot_type, count(*), count(DISTINCT race_key), min(snapshot_at),
                max(snapshot_at), min(snapshot_at::date), max(snapshot_at::date)
                FROM keirin.wt_odds_snapshot GROUP BY 1 ORDER BY 1""")
    out["snap_types"] = [[str(x) for x in row] for row in r]
    c, r = q("""SELECT extract(hour FROM snapshot_at)::int h, count(DISTINCT race_key), min(snapshot_at::date),
                max(snapshot_at::date) FROM keirin.wt_odds_snapshot WHERE snapshot_type='morning'
                AND snapshot_at::date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""", (a.date_from, a.date_to))
    out["morning_hour"] = [[str(x) for x in row] for row in r]
    c, r = q("""SELECT min(snapshot_at::time), max(snapshot_at::time), count(DISTINCT snapshot_at::date)
                FROM keirin.wt_odds_snapshot WHERE snapshot_type='morning'
                AND snapshot_at::date BETWEEN %s AND %s""", (a.date_from, a.date_to))
    out["morning_time_range"] = [str(x) for x in r[0]]
    c, r = q("""SELECT min(submitted_at::time), max(submitted_at::time), count(*) FROM keirin.netkeirin_submissions
                WHERE session='morning' AND status='published' AND submitted_at::date BETWEEN %s AND %s""",
             (a.date_from, a.date_to))
    out["sub_morning_time_range"] = [str(x) for x in r[0]]
    c, r = q("""SELECT session, count(*), min(submitted_at::time), max(submitted_at::time)
                FROM keirin.netkeirin_submissions WHERE status='published'
                AND submitted_at::date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""", (a.date_from, a.date_to))
    out["sub_sessions"] = [[str(x) for x in row] for row in r]
    c, r = q("""SELECT mode, count(*), count(DISTINCT race_key), sum((settled_at IS NULL)::int)
                FROM keirin.type_lab_picks WHERE race_date BETWEEN %s AND %s AND mode IN ('live','live9')
                GROUP BY 1""", (a.date_from, a.date_to))
    out["picks_modes"] = [[str(x) for x in row] for row in r]
    json.dump(out, open(CACHE / "premise.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ───────────────────────────── 報告 ─────────────────────────────

def md_table(head: list[str], body: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return out + [""]


def qs(xs, ps=(10, 25, 50, 75, 90)):
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return "-"
    v = np.percentile(xs, ps)
    return " / ".join(f"{x:.2f}" for x in v) + f"  (n={len(xs)})"


def roi_cells(s: dict, arms) -> list[str]:
    return [f"{pct(s[a]['roi'])} [{pct(s[a]['ci'][0], 0)}, {pct(s[a]['ci'][1], 0)}]" for a in arms]


def diff_cells(s: dict, arms) -> list[str]:
    out = []
    for a in arms:
        if a == "B":
            continue
        d, (lo, hi) = s[f"B-{a}"]
        out.append(f"{pp(d)} [{lo * 100:+.0f}, {hi * 100:+.0f}]")
    return out


def hit_cells(s: dict, arms) -> list[str]:
    return [f"{pct(s[a]['hit'])} / {pct(s[a]['hit_disp'])} / {pct(s[a]['roi_ex_top'])}" for a in arms]


def big_cells(s: dict, arms) -> list[str]:
    return [f"{s[a]['big']} ({s[a]['big_per_day']:.2f}/日) / {s[a]['med_hit_pay']:,.0f}" for a in arms]


def cmd_report(a) -> None:
    rows, meta = build_rows()
    pickle.dump((rows, meta), open(CACHE / "rows.pkl", "wb"))
    write_report(rows, meta)


def common(rows: list[dict], arms) -> list[dict]:
    return [r for r in rows if all(r.get(x) for x in arms)]


def drop_table(rows: list[dict], arms) -> list[str]:
    """腕ごとに、組めなかった pick の理由を数える。"""
    reasons = ["板なし", "有効オッズがk点に満たない", "配分不能", "当たり目の最終オッズ欠損"]
    body = []
    for x in arms:
        if x == "B":
            continue
        c = defaultdict(int)
        for r in rows:
            if not r.get(x):
                c[r["why"].get(x, "?")] += 1
        body.append([ARM_NAME[x]] + [str(c.get(k, 0)) for k in reasons])
    return md_table(["腕"] + reasons, body)


def pop_tables(W, title: str, rows: list[dict], arms, detail_plans: bool = True) -> None:
    """母集団 1 つぶんの表（全体・本線/高額/…・プラン別）。"""
    allrows = rows
    rows = common(rows, arms)
    W(f"#### {title}\n")
    W(f"pick {len(allrows)} 件のうち全腕とも組めた {len(rows)} 件を比較に使う"
      f"（外れた {len(allrows) - len(rows)} 件の理由は下表。腕別・重複あり）。\n")
    W("\n".join(drop_table(allrows, arms)))
    groups = [("全体", rows)]
    for fam in ("本線(_hit/_pay/_trio/_ana/F_line)", "高額・看板枠(_sign/_big)", "逃げ先頭(L_*)", "段(T_*)"):
        g = [r for r in rows if family(r["plan"]) == fam]
        if g:
            groups.append((fam, g))
    if detail_plans:
        for p in sorted({r["plan"] for r in rows}):
            groups.append((f"　{p}", [r for r in rows if r["plan"] == p]))
    summ = [(lab, g, summarize(g, arms)) for lab, g in groups]
    W("**回収率（最終オッズ×賭け金・欠車返還控除後）[開催日ブートストラップ 95%CI]**\n")
    W("\n".join(md_table(["商品", "R数", "日数"] + [ARM_NAME[x] for x in arms],
                        [[lab, str(s["n"]), str(s["days"])] + roi_cells(s, arms) for lab, g, s in summ])))
    W("**差（B − 各腕。同じ開催日リサンプルで対にした 95%CI。単位 pt）**\n")
    W("\n".join(md_table(["商品", "R数"] + [f"B−{x}" for x in arms if x != "B"],
                        [[lab, str(s["n"])] + diff_cells(s, arms) for lab, g, s in summ])))
    W("**的中率(ガミ含む = 払戻>0) / 表示的中(払戻>賭け金・netkeirin の表示的中に相当) / 最高配当1件を除いた回収率**\n")
    W("\n".join(md_table(["商品", "R数"] + [ARM_NAME[x] for x in arms],
                        [[lab, str(s["n"])] + hit_cells(s, arms) for lab, g, s in summ])))
    W("**10万円以上の的中 件数(1日あたり) / 的中時払戻の中央値(円)**\n")
    W("\n".join(md_table(["商品", "R数"] + [ARM_NAME[x] for x in arms],
                        [[lab, str(s["n"])] + big_cells(s, arms) for lab, g, s in summ])))
    W("**月別回収率**\n")
    months = sorted({r["day"][:7] for r in rows})
    body = []
    for arm in arms:
        m = monthly(rows, arm)
        body.append([ARM_NAME[arm]] + [pct(m.get(mm, float("nan"))) for mm in months])
    W("\n".join(md_table(["腕"] + months, body)))


TOPNS = (0, 1, 3, 5)


def topn_stats(rows: list[dict], arms, ns=TOPNS, seed=SEED) -> dict:
    """各腕から「その腕自身の払戻の上位 N 件(レース単位)」を除いた回収率と、B−各腕の差。

    分母（賭け金−返還）は除かない。CI は開催日リサンプルのたびに、リサンプル後のレースの中で
    上位 N 件を除き直す（点推定と同じ手続き）。重複して引かれたレースは別件として数える。
    """
    arms = list(arms)
    days = sorted({r["day"] for r in rows})
    didx = {d: i for i, d in enumerate(days)}
    dr = np.array([didx[r["day"]] for r in rows])
    pay = {a: np.array([r[a]["pay"] for r in rows], dtype=float) for a in arms}
    cost = {a: np.array([r[a]["net_cost"] for r in rows], dtype=float) for a in arms}
    rng = np.random.default_rng(seed)
    D = len(days)
    cnt = np.zeros((N_BOOT, D))
    draws = rng.integers(0, D, (N_BOOT, D))
    for b in range(N_BOOT):
        cnt[b] = np.bincount(draws[b], minlength=D)
    Wb = cnt[:, dr]                       # N_BOOT x R
    W1 = np.ones((1, len(rows)))

    def roi_for(a, W):
        order = np.argsort(-pay[a], kind="stable")
        ps, Ws = pay[a][order], W[:, order]
        prev = np.cumsum(Ws, axis=1) - Ws
        tot = Ws @ ps
        c = W @ cost[a]
        out = {}
        for n in ns:
            removed = (np.clip(n - prev, 0, Ws) * ps).sum(axis=1)
            out[n] = (tot - removed) / np.maximum(c, 1)
        return out

    pt = {a: roi_for(a, W1) for a in arms}
    bt = {a: roi_for(a, Wb) for a in arms}
    res: dict = {}
    for n in ns:
        d: dict = {}
        for a in arms:
            lo, hi = np.percentile(bt[a][n], [2.5, 97.5])
            d[a] = (float(pt[a][n][0]), float(lo), float(hi))
        for a in arms:
            if a == "B":
                continue
            lo, hi = np.percentile(bt["B"][n] - bt[a][n], [2.5, 97.5])
            d["B-" + a] = (float(pt["B"][n][0] - pt[a][n][0]), float(lo), float(hi))
        res[n] = d
    return res


def topn_table(W, rows: list[dict], arms, with_plans: bool = False) -> None:
    rows = common(rows, arms)
    groups = [("全体", rows)]
    for fam in ("本線(_hit/_pay/_trio/_ana/F_line)", "高額・看板枠(_sign/_big)", "逃げ先頭(L_*)", "段(T_*)"):
        g = [r for r in rows if family(r["plan"]) == fam]
        if g:
            groups.append((fam, g))
    if with_plans:
        for p in sorted({r["plan"] for r in rows}):
            groups.append((f"　{p}", [r for r in rows if r["plan"] == p]))
    body = []
    for lab, g in groups:
        st = topn_stats(g, arms)
        for n in TOPNS:
            d = st[n]
            body.append([lab if n == 0 else "", f"{len(g)}" if n == 0 else "", "なし" if n == 0 else f"上位{n}件"]
                        + [f"{pct(d[a][0])} [{pct(d[a][1], 0)}, {pct(d[a][2], 0)}]" for a in arms]
                        + [f"{pp(d['B-' + a][0])} [{d['B-' + a][1] * 100:+.0f}, {d['B-' + a][2] * 100:+.0f}]"
                           for a in arms if a != "B"])
    W("\n".join(md_table(["商品", "R数", "除外"] + [ARM_NAME[a] for a in arms]
                        + [f"B−{a}" for a in arms if a != "B"], body)))


def _roi_of(g, a):
    c = sum(r[a]["net_cost"] for r in g)
    return sum(r[a]["pay"] for r in g) / c if c else float("nan")


def strata_section(W, rows: list[dict]) -> None:
    """R1: B−A の層別と、層内で符号が揃う比較の整理。rows = 売った × 朝便（共通母集団）。"""
    arms = ARMS_MAIN
    rows = [r for r in common(rows, arms) if r.get("start_band")]
    for r in rows:
        r["_thick"] = "厚(>=0.9)" if r["morning_valid_frac"] >= 0.9 else "薄(<0.9)"
        r["_week"] = datetime.fromisoformat(r["day"]).isocalendar()[1]
    W("##### 主表 B−A の層別（時間帯 × 朝板の厚さ・ISO 週）\n")
    W("層は事前に定義したものではなく、事後の分割（§3.5 と同じ）。件数が少ない層は参考値。"
      "`A∩F` は A の選ぶ k 点と最終市場の低オッズ k 点（F）の一致率の中央値（1 に近いほど A が最終の市場に近い）。\n")
    strata: list[tuple[str, list[dict]]] = []
    for band in ("デイ(<15時)", "ナイター(15-20時)", "ミッド(>=20時)"):
        for th in ("厚(>=0.9)", "薄(<0.9)"):
            g = [r for r in rows if r["start_band"] == band and r["_thick"] == th]
            if g:
                strata.append((f"{band} × {th}", g))
    wk = [(f"ISO{w}週", [r for r in rows if r["_week"] == w]) for w in sorted({r["_week"] for r in rows})]
    body = []
    for lab, g in strata + wk:
        if len(g) < 5:
            continue
        s_ = summarize(g, arms)
        d, (lo, hi) = s_["B-A"]
        ov = [r["a_f_overlap"] for r in g if r.get("a_f_overlap") is not None]
        body.append([lab, str(s_["n"]), str(s_["days"])] + roi_cells(s_, arms)
                    + [f"{pp(d)} [{lo * 100:+.0f}, {hi * 100:+.0f}]", f"{np.median(ov):.2f}" if ov else "-"])
    W("\n".join(md_table(["層", "R数", "日数"] + [ARM_NAME[x] for x in arms] + ["B−A", "A∩F"], body)))
    allov_thin = [r["a_f_overlap"] for r in rows if r["_thick"] == "薄(<0.9)" and r.get("a_f_overlap") is not None]
    allov_thick = [r["a_f_overlap"] for r in rows if r["_thick"] == "厚(>=0.9)" and r.get("a_f_overlap") is not None]
    W(f"薄い朝板は主表の {sum(1 for r in rows if r['_thick'] == '薄(<0.9)')}/{len(rows)} 件。"
      f"A の選ぶ k 点と最終市場の低オッズ k 点の一致率の中央値は、薄い板 {np.median(allov_thin):.2f}・"
      f"厚い板 {np.median(allov_thick):.2f}。**薄い板では A は「市場」の粗い近似にすぎない**"
      "（この粗さが B−A をどちらへ偏らせるかは測れていない）。\n")
    # 層内の符号
    pairs = [("B", "A"), ("B", "C"), ("B", "F"), ("C", "A"), ("F", "A"), ("F", "C")]
    use = [(lab, g) for lab, g in strata if len(g) >= 50] + [(lab, g) for lab, g in wk if len(g) >= 50]
    body = []
    consist = []
    for x, y in pairs:
        pos = neg = 0
        for lab, g in use:
            d = _roi_of(g, x) - _roi_of(g, y)
            pos += d > 0
            neg += d < 0
        body.append([f"{ARM_NAME[x]} − {ARM_NAME[y]}", str(len(use)), str(pos), str(neg)])
        if pos == 0 or neg == 0:
            consist.append(f"{x}−{y}（{'すべて正' if neg == 0 else 'すべて負'}）")
    W("**層内で符号が揃うか**（上の層のうち 50 件以上の層 × 週。回収率の点推定の差の符号を数えた）\n")
    W("\n".join(md_table(["比較", "層の数", "差>0 の層", "差<0 の層"], body)))
    _rg = {x: [_roi_of(g, x) for _, g in use] for x in ("B", "A")}
    _f = dict(bmin=pct(min(_rg["B"]), 0), bmax=pct(max(_rg["B"]), 0), amin=pct(min(_rg["A"]), 0), amax=pct(max(_rg["A"]), 0))
    W(("🔴 **主表の「B 67.8% ↔ A 70.8%（B−A −3.0pt）」は解釈できない。** B−A は層の切り方で符号が反転し"
      "（上表）。50 件以上の層での回収率の範囲は B {bmin}〜{bmax}・A {amin}〜{amax}（B の振れの方が大きい）。主表の B−A を「市場に負けている」とも「並んでいる」とも読まないこと。"
      + ("層・週のすべてで符号が揃った比較は " + "、".join(consist) + "。" if consist else
         "層・週のすべてで符号が揃った比較は無い。")
      + "\n").replace("{bmin}", _f["bmin"]).replace("{bmax}", _f["bmax"]).replace("{amin}", _f["amin"]).replace("{amax}", _f["amax"]))


def write_report(rows, meta) -> None:  # noqa: C901
    from src.type_lab import PLANS
    prem = json.load(open(CACHE / "premise.json")) if (CACHE / "premise.json").exists() else {}
    L: list[str] = []
    W = L.append
    days = sorted({r["day"] for r in rows})
    morning_pk = [r for r in rows if r["sess"] == "朝便"]
    wave_pk = [r for r in rows if r["sess"] == "昼夜便"]
    allsold_ = [r for r in rows if r["sold"]]
    sold_m = [r for r in morning_pk if r["sold"]]
    sold_w = [r for r in wave_pk if r["sold"]]
    cand_m = [r for r in morning_pk if not r["plan"].startswith("T_") and r["plan"] != "L_flat"]

    W("# P1' ベースライン: 型ラボの買い目を「入稿時点のオッズ」で測る（現状測定のみ）\n")
    W("> 採否・改善提案は書かない。2026-07-16 以降は監査（`AUDIT_2026_09_20.md` §0）の取り決めで現状測定のみ。"
      "数値だけを報告する。期間が約 5 週間と短く、CI は広い。")
    W("> 生成: `keirin/scripts/exp_bet_review/p1_baseline.py`（`extract` -> `premise` -> `report`）。\n")
    W("🔴 **払戻 = 最終オッズ x 賭け金は「締切直前に買えた場合」の上限値。** 朝・入稿時点では買えない。"
      "A / Ag / C の回収率は、その価格で実際に買えたことを意味しない。"
      "F（最終の市場）は買えない価格での参考値。\n")

    # ── 0 データの前提
    W("## 0. データの前提（朝スナップショットの時刻と被覆）\n")
    W("`keirin.wt_odds_snapshot`（`UNIQUE(race_key, bet_type, combination, snapshot_type)`・`INSERT OR IGNORE` = "
      "**各 snapshot_type の初回値を保持**）。`wt_odds` は最終オッズで上書きされ、`collected_at` は初回時刻のままなので"
      "時刻判断には使っていない。\n")
    if prem:
        W("\n".join(md_table(["snapshot_type", "行数", "レース数", "最初の snapshot_at", "最後の snapshot_at"],
                            [[x[0], f"{int(x[1]):,}", f"{int(x[2]):,}", x[3], x[4]] for x in prem["snap_types"]])))
        W(f"**分析期間 {days[0]}〜{days[-1]} の morning の取得時刻**: 日内の最早〜最遅 = "
          f"{prem['morning_time_range'][0]}〜{prem['morning_time_range'][1]}（{prem['morning_time_range'][2]} 開催日）。"
          "時台別レース数: " + "、".join(f"{x[0]}時台 {x[1]}R" for x in prem["morning_hour"]) + "。")
        W(f"**朝の入稿**（`netkeirin_submissions` session=morning・published）の時刻範囲 = "
          f"{prem['sub_morning_time_range'][0]}〜{prem['sub_morning_time_range'][1]}"
          f"（{prem['sub_morning_time_range'][2]}件）。セッション別: "
          + "、".join(f"{x[0]} {x[1]}件({x[2]}〜{x[3]})" for x in prem["sub_sessions"]) + "。\n")
    W("朝バッチの順序: `daily_picks_wt.sh`（07:00・collect-wt が morning を採る）-> `type_lab_daily.sh`"
      "（買い目生成 -> 入稿 07:09〜07:31）。**morning は買い目の生成・入稿より前に取れている。**\n")
    W(f"- 分析対象 pick（live+live9・期間 {days[0]}〜{days[-1]}・{len(days)} 開催日・採点済み）= "
      f"**{len(rows):,}**（`type_lab_picks` の live は 2026-08-27 から、live9 は 08-28 から。それ以前は paper のみで、"
      f"実入稿も予測板の再構成もできない）。未採点で除外 = {meta.get('unsettled', 0)}。")
    pre = [r for r in rows if r["snap_at"] and r["snap_at"] <= r["gen_at"]]
    W(f"- morning の snapshot_at が **買い目の生成時刻より前**: {len(pre):,}/{len(rows):,}。")
    sd = [r for r in rows if r["sold"] and r["snap_at"] and r["sub_at"]]
    W(f"- 売った pick で morning が **入稿時刻より前**: {sum(1 for r in sd if r['snap_at'] <= r['sub_at']):,}/{len(sd):,}"
      f"（入稿 − morning の中央値 {np.median([(r['sub_at'] - r['snap_at']).total_seconds() / 60 for r in sd]):.1f} 分、"
      f"朝便のみ {np.median([(r['sub_at'] - r['snap_at']).total_seconds() / 60 for r in sd if r['sess'] == '朝便']):.1f} 分）。")
    gl = [r for r in rows if r["gen_lt_sub"]]
    W(f"- 🔴 **便の分け方**: `type_lab_picks` は (race_key, plan_key, mode) 一意で、昼・夕の波が同じ行を再生成すると "
      f"`generated_at` が上書きされる。売った pick のうち入稿時刻 < generated_at のものが **{len(gl):,}** 件あった。"
      "そこで **基準時刻 = 売った pick は入稿時刻、それ以外は生成時刻** とし、基準時刻が 09 時前を朝便、"
      "それ以外（10〜19 時台）を昼夜便とした。"
      f"朝便 {len(morning_pk):,}（うち売った {len(sold_m):,}）／昼夜便 {len(wave_pk):,}（うち売った {len(sold_w):,}）。"
      "昼夜便は 07:00 台の朝板では基準時点の板にならない（数時間古い）ので、**主表は朝便に絞り、昼夜便は別表**"
      "（A=朝板に加え Ag=基準時刻以前に取れた最新の snapshot_type の板）で出す。")
    W(f"- 朝板の取得時刻の幅（レース内の最大−最小 snapshot_at・分）: 中央値 "
      f"{np.median([r['morning_spread_min'] for r in rows if r['morning_spread_min'] is not None]):.1f}・"
      f"p99 {np.percentile([r['morning_spread_min'] for r in rows if r['morning_spread_min'] is not None], 99):.1f}・"
      f"最大 {max(r['morning_spread_min'] for r in rows if r['morning_spread_min'] is not None):.1f}。"
      f"morning の日付が開催日と一致 = {sum(1 for r in rows if r['morning_date_ok']):,}/{len(rows):,}。")
    mv = [r["morning_valid_frac"] for r in morning_pk]
    W(f"- 朝便の朝板の有効オッズ比率(9999 未満)の分位 10/50/90% = "
      f"{np.percentile(mv, 10):.2f} / {np.percentile(mv, 50):.2f} / {np.percentile(mv, 90):.2f}"
      "（9999.9 は置き値で、選抜は低オッズ側なので置き値は選ばれない）。"
      f"昼夜便では、朝板 {np.median([r['morning_valid_frac'] for r in wave_pk]):.2f} に対し"
      f"生成時点の板 {np.median([r['asof_valid_frac'] for r in wave_pk]):.2f}（中央値）。")
    byd: dict = defaultdict(list)
    for r in morning_pk:
        byd[r["day"]].append(r["morning_valid_frac"])
    lowd = [(d, np.median(v)) for d, v in sorted(byd.items()) if np.median(v) < 0.4]
    if lowd:
        W("- 朝板の有効比率の日別中央値が 0.4 未満の日: "
          + "、".join(f"{d}({v:.2f}・朝板取得 {min(r['snap_at'] for r in morning_pk if r['day'] == d):%H:%M})"
                     for d, v in lowd)
          + "。（朝板を 06:05 に採った 2026-09-15 のように、板が未公開のうちに採った日。）")
    W(f"- 自前の採点が DB（`type_lab_picks.payout` / `void_refund`）と食い違った pick = "
      f"payout {meta.get('B_pay_mismatch', 0)} / void {meta.get('B_void_mismatch', 0)}（B の採点ロジックは完全再現）。")
    W(f"- 売った pick の bet_detail の買い目・金額が `type_lab_picks.legs` と食い違った = "
      f"{meta.get('sold_legs_differ', 0)}/{meta.get('sold_checked', 0)}"
      + ("。内訳: " + "；".join(f"{a} {b}（入稿 {c}・生成 {d}・差分 {e}）"
                              for a, b, c, d, e in meta.get("sold_legs_differ_list", []))
         if meta.get("sold_legs_differ_list") else "") + "。"
      f" 採点不能（当たり目の最終オッズ欠損）で B が組めず除外した pick = {meta.get('B_no_final_odds', 0)}。")
    be = sum(1 for r in rows if r["board_err"])
    em = sum(1 for r in rows if r["ent_mismatch"])
    both = sum(1 for r in rows if r["board_err"] and r["ent_mismatch"])
    W(f"- 予測板(C 腕)を再構成できなかった pick = {be}。出走表の行数が `wt_races.n_entries` と違う（欠車で行が消えた）"
      f"pick = {em}（うち予測板の失敗と重なる = {both}）。欠車レースでは「今の出走表」で板を作り直すため、"
      "C は欠車を知っている板で選ぶ一方、B は欠車の目も買っている（返還）点に注意。")
    psg = [r for r in rows if r.get("post_start_gen")]
    rows_f = [r for r in rows if not r.get("post_start_gen")]
    W(f"- 🔴 **発走後に生成された行（`generated_at` > 発走時刻）= {len(psg)} 件**"
      f"（日付: {', '.join(f'{d} {n}' for d, n in sorted(collections.Counter(r['day'] for r in psg).items()))}。"
      f"うち売った朝便 {sum(1 for r in psg if r['sold'] and r['sess'] == '朝便')}・昼夜便の候補 "
      f"{sum(1 for r in psg if not r['sold'] and r['sess'] == '昼夜便')}）。"
      "これらの `legs[].pred_odds` は組み直し時点の値なので、**下の予測板の忠実度と §4 の予測オッズ比はこの行を除いて**出した。"
      f"（入稿時刻より後に再生成された売った行は別に {sum(1 for r in rows if r['gen_lt_sub'])} 件。"
      "§4 に除いた版を併記した。回収率の B はどちらも `type_lab_picks.legs` で採点しているが、入稿の買い目と一致する"
      "ことは確認済み。）")
    fids = [r["pred_fid"] for r in rows_f if r.get("pred_fid")]
    W("- **予測板の再構成の忠実度**（発走後生成行を除く）（B の保存済み予測オッズに対する再構成板の比・pick ごとの中央値）。"
      "`wt_entries.pred_*_pct` は 0.1% 刻みに丸められ、本番が生成時に使った未丸めの p3/pw とは僅かに違う。月別:\n")
    body = []
    for mo in sorted({r["day"][:7] for r in rows}):
        for sess in ("朝便", "昼夜便"):
            f_ = [r["pred_fid"] for r in rows_f if r["day"][:7] == mo and r["sess"] == sess and r.get("pred_fid")]
            if f_:
                body.append([mo, sess, str(len(f_))] + [f"{x:.3f}" for x in np.percentile(f_, [1, 5, 50, 95, 99])])
    W("\n".join(md_table(["月", "便", "n", "p1", "p5", "中央", "p95", "p99"], body)))
    W(f"全期間の中央値 {np.median(fids):.3f}。\n")

    # ── 1 仕様
    W("## 1. 本番コードから書き出した各商品の仕様\n")
    W("（`src/type_lab.py` の `PLANS` と `type_lab_picks` の実データから。配分 `alloc`: "
      "dutch = 賭け金 ∝ 1/予測オッズ（払戻を全点で揃える）／ conf = 床 + 確率比例 ／ equal = 均等。）\n")
    body = []
    for p in sorted({r["plan"] for r in rows}):
        g = [r for r in rows if r["plan"] == p]
        pl = PLANS.get(p)
        ks = [r["k"] for r in g]
        body.append([p, g[0]["bt"], pl.alloc if pl else "-",
                     f"{np.median(ks):.0f} ({min(ks)}〜{max(ks)})", f"{np.median([r['B']['cost'] for r in g]):,.0f}",
                     str(len(g)), str(sum(r['sold'] for r in g))])
    W("\n".join(md_table(["plan_key", "券種", "配分", "点数 k 中央値(範囲)", "賭け金合計 中央値(円)",
                        "live pick 数", "うち売った"], body)))
    W("- 予算は 1 レース 10,000 円（`BUDGET`）・100 円単位。欠車を含む買い目は返還（`void_refund`）で、"
      "回収率の分母から控除した。")
    W("- **A / Ag / C / F の組み方**: B と同じ券種・同じ点数 k で、各腕の板のオッズが**低い順に上位 k 点**を選び、"
      "その板のオッズで dutch 配分（`type_lab.allocate`・予算 10,000 円・plan の alloc を dutch に差し替え。"
      "賭け金 0 円になる点は `allocate` が落とす＝本番と同じ処理）。"
      "**B の配分が dutch でない商品（conf / equal）では、各腕との差に配分の違いも含まれる**（B′ の表を参照）。")
    W("- ゲート・例外（高額枠 `_sign/_big`・「自信あり」・`F_pay`/`F_line`・`L_lead` 等）は、"
      "**B は本番が決めた買い目をそのまま使う**ので再現していない（母集団＝本番が売った/売る判定したレース）。"
      "A/Ag/C/F は同じレースに対する比較用の買い方で、ゲートは掛けていない。"
      "高額・看板枠(_sign/_big)は計画払戻 15万円前後を狙う高オッズ帯なので、"
      "同じ点数の「低オッズ上位 k 点」（A/Ag/C/F）とは**狙う帯が違う**。\n")

    W("**ゲートと例外（本番コード `netkeirin_submit_type_lab.py` / `type_lab.py`・`AUDIT_2026_09_20.md` §2.1・`RECOMMENDATION.md` §3.3〜3.6 から）**\n")
    W("\n".join(md_table(["入稿経路 (`origin`)", "対象の plan", "掛かるゲート", "売った件数"], [
        ["rank", "`*_hit` `*_pay` `*_trio` `A_ana` `F_line` `F_sign` `T_*` ほか",
         "並び・印の欠測 -> 軸信頼ゲート（`_passes_axis_gate`・7車のみ・プラン内 p30。`cap_free_plans()` は免除）"
         " -> 入稿ゲート（平均想定払戻 > 2万円 ∧ 全点 2.0 倍以上）-> 日次上限（判定対象×`DAILY_CAP_RACE_FRACTION`）",
         str(sum(1 for r in allsold_ if r["origin"] == "rank"))],
        ["highpay_fill", "`{B,C,D}_sign` `{B,C,D}_big`（高額枠・1日 `HIGHPAY_SLOTS_PER_DAY` 本）",
         "軸信頼ゲートは掛けない・日次上限の枠を消費しない・入稿ゲート（想定払戻・1点オッズ）は掛ける",
         str(sum(1 for r in allsold_ if r["origin"] == "highpay_fill"))],
        ["line_lead", "`L_lead`（逃げ先頭ライン・均等配分）", "独自の選抜（`line_lead_legs`）。L_flat は紙上のみ",
         str(sum(1 for r in allsold_ if r["origin"] == "line_lead"))],
    ])))
    W(f"「自信あり」（`is_confident`・18時前 × 合成 2.5 倍以上 × Σp 最大）で売った pick = "
      f"{sum(1 for r in allsold_ if r['conf'])}。段 `T_*` は `TIER_SELL_ENABLED=False` で、実売は 2026-09-15 の 1 日だけ。"
      "B は上記の経路を通って本番が実際に売った（または候補とした）買い目そのもので、"
      "A/Ag/C/F は同じレースへゲートを掛けずに当てた比較用の買い方。\n")

    # ── 2 目視確認
    W("## 2. 目視確認した 1 件（実際に売った・的中した入稿）\n")
    pool = [r for r in sold_m if r["plan"] == "C_hit" and r["B"]["hit"] and all(r.get(x) for x in ARMS_MAIN)]
    pool = pool or [r for r in sold_m if r["B"]["hit"] and all(r.get(x) for x in ARMS_MAIN)]
    ex = sorted(pool, key=lambda r: (abs(r["k"] - 5), r["day"]))[0]
    W(f"レース `{ex['race_key']}` ／ plan `{ex['plan']}`（{ex['bt']}・k={ex['k']}・{ex['race_type']}・"
      f"{ex['n_entries']}車）／ 朝板取得 {ex['snap_at']:%H:%M:%S}・生成 {ex['gen_at']:%H:%M:%S}・"
      f"入稿 {ex['sub_at']:%H:%M:%S}。\n")
    W("B（実際に売った買い目。予測/朝/最終オッズは買った組の値）:\n")
    W("\n".join(md_table(["組", "賭け金(円)", "予測オッズ", "朝オッズ", "最終オッズ", "当たり"],
                        [[l["combo"], f"{l['stake']:,}",
                          f"{l['pred']}" if l['pred'] else "-", f"{l['morn']}" if l['morn'] else "-",
                          f"{l['final']}" if l['final'] else "-", "○" if l["won"] else ""]
                         for l in ex["moves"]])))
    W(f"B: 賭け金 {ex['B']['cost']:,}円・払戻 {ex['B']['pay']:,}円"
      f"（回収 {pct(ex['B']['pay'] / ex['B']['net_cost'])}）。計画（`pred_mean_payout`）= "
      f"{ex['pred_mean_payout']:,.0f}円。")
    for arm in ("A", "C", "F"):
        x = ex[arm]
        W(f"{ARM_NAME[arm]}: {x['n']}点・賭け金 {x['cost']:,}円・払戻 {x['pay']:,}円。")
    W("")

    # ── 3 主要表
    W("## 3. 回収率と差\n")
    W(f"回収率 = Σ払戻 ÷ Σ(賭け金 − 欠車返還)。CI は開催日単位ブートストラップ {N_BOOT} 回"
      "（日を復元抽出し、同じ抽出で B−各腕を対にする）。\n")
    W("> 🔴 **先に読む（レッドチーム R1）: 主表の B−A は解釈できない。** B−A は層（朝板の厚さ × 発走時間帯、週）で符号が反転し、"
      "A は朝板が薄いとき最終市場の粗い近似にすぎない（A と F の選ぶ組の一致率の中央値 薄い板 0.50 付近）。"
      "層別の表と、層内で符号が揃う比較の整理は 3.1 の末尾。さらに回収率の差は上位数件の高額的中に支配される（3.6 に上位 k 件除外後を出した）。\n")
    pop_tables(W, "3.1 【主表】売った買い目 × 朝便（基準時刻 09 時前・A=07:00 台の朝板 = 入稿時点の板）",
               sold_m, ARMS_MAIN)
    strata_section(W, sold_m)
    pop_tables(W, "3.2 売った買い目 × 昼夜便（基準時刻 09 時以降。A=朝板（古い）／Ag=基準時刻以前の最新の板）",
               sold_w, ARMS_WAVE, detail_plans=False)
    pop_tables(W, "3.3 live 候補（段 T_* と紙上のみの L_flat を除く）× 朝便"
                  "【1 レースに複数 plan の行が並ぶ候補集合であり、ポートフォリオではない】",
               cand_m, ARMS_MAIN)
    allsold = [r for r in rows if r["sold"]]
    W("#### 3.4 参考: 売った買い目の全体（朝便 + 昼夜便・A=朝板固定）\n")
    s_all = common(allsold, ARMS_MAIN)
    sm = summarize(s_all, ARMS_MAIN)
    W("\n".join(md_table(["R数", "日数"] + [ARM_NAME[x] for x in ARMS_MAIN] + ["B−A", "B−C", "B−F"],
                        [[str(sm["n"]), str(sm["days"])] + roi_cells(sm, ARMS_MAIN) + diff_cells(sm, ARMS_MAIN)])))

    # 共通母集団から外れた pick の B
    drop_m = [r for r in sold_m if not all(r.get(x) for x in ARMS_MAIN)]
    keep_m = common(sold_m, ARMS_MAIN)

    def _b_roi(g):
        return (sum(r["B"]["pay"] for r in g) / max(sum(r["B"]["net_cost"] for r in g), 1),
                sum(r["B"]["void"] for r in g), len(g))
    bk, bd = _b_roi(keep_m), _b_roi(drop_m)
    ball = summarize(allsold_, ("B",))
    W("**共通母集団から外れた pick の B（売った × 朝便）**: "
      f"残した {bk[2]} 件は B 回収 {pct(bk[0])}・返還計 {bk[1]:,} 円／外れた {bd[2]} 件は B 回収 {pct(bd[0])}・返還計 {bd[1]:,} 円。"
      f"（外れた主因は A の「有効オッズが k 点に満たない」= 板が薄い夜開催レース。）"
      f"売った全体（朝便+昼夜便・全 {ball['n']:,} 件・{ball['days']} 日）の B 回収は {pct(ball['B']['roi'])} "
      f"[{pct(ball['B']['ci'][0], 0)}, {pct(ball['B']['ci'][1], 0)}]。"
      "AUDIT §4.1 の型ラボ期（8/29〜9/19・1,240 件）75.3% [58.7, 97.9] とは窓が違うが CI は重なる。\n")

    W("#### 3.5 感度: 朝板の有効オッズ比率で分けた（売った買い目 × 朝便・全体）\n")
    W("07:00 台は夜開催レースの板がまだ立っておらず、9999.9 の置き値が多い。"
      "有効比率 0.9 以上 = 板がほぼ揃っていたレース、未満 = 板が薄かったレース。"
      "⚠️ 板の厚さは発走時間帯（種別・開催）と交絡する（薄い板 = 遅い発走）ので、群間の差を板の厚さだけの効果とは読めない。\n")
    body = []
    for lab, g in (("朝板の有効比率 >= 0.9", [r for r in sold_m if r["morning_valid_frac"] >= 0.9]),
                   ("朝板の有効比率 < 0.9", [r for r in sold_m if r["morning_valid_frac"] < 0.9])):
        g = common(g, ARMS_MAIN)
        if len(g) < 5:
            body.append([lab, str(len(g))] + ["-"] * 7)
            continue
        sm_ = summarize(g, ARMS_MAIN)
        body.append([lab, str(sm_["n"]), str(sm_["days"])] + roi_cells(sm_, ARMS_MAIN) + diff_cells(sm_, ARMS_MAIN)[:0])
    W("\n".join(md_table(["群", "R数", "日数"] + [ARM_NAME[x] for x in ARMS_MAIN], [b[:3 + len(ARMS_MAIN)] for b in body])))

    W("#### 3.6 全腕から同じ件数の上位払戻を除いた回収率（レッドチーム R2）\n")
    W("各腕が自分の払戻の上位 N 件（レース単位）を除いた回収率（分母は除かない）。N=0 は除外なし。"
      "CI と B−各腕の差の CI は、日リサンプルのたびにリサンプル後のレースの中で上位 N 件を除き直したもの。"
      "従来の「最高配当 1 件除外」は B にだけ効くように読めたので、**全腕に同じ手続き**を当てる。\n")
    W("##### 3.6.1 売った × 朝便（共通母集団・商品別を含む）\n")
    topn_table(W, sold_m, ARMS_MAIN, with_plans=True)
    W("##### 3.6.2 売った × 昼夜便\n")
    topn_table(W, sold_w, ARMS_WAVE)
    W("##### 3.6.3 live 候補 × 朝便（1 レースに複数 plan の行が並ぶ候補集合）\n")
    topn_table(W, cand_m, ARMS_MAIN)

    # ── 4 オッズの動き
    W("## 4. 朝 -> 最終 のオッズの動き（B の買った組・売った買い目・朝便）\n")
    W("比 = 分子 / 分母。分位は 10 / 25 / 50 / 75 / 90%（脚単位・賭け金で重み付けしない）。"
      "朝板・最終オッズが引けない脚は除く。昼夜便は朝板が古いので含めない。"
      f"**発走後に生成された行 {sum(1 for r in sold_m if r['post_start_gen'])} 件（売った朝便）は除いてある**"
      "（その行の予測オッズは組み直し時点の値で、入稿時点の予測ではない）。\n")
    W("⚠️ **朝 -> 最終 の動きを「B を公開したことによる目減り（追随買い）」と読まないこと**（レッドチーム R9）。"
      "朝板は締切までに予測オッズへ半分ほど寄っていく（回帰係数 0.54）粗い板で、公開ダミーの係数は 0 を跨ぐ"
      "（+0.037 [−0.030, +0.098]）。朝板の値で買えたことも意味しない。\n")
    sold4 = [r for r in sold_m if not r["post_start_gen"]]
    groups = [("全体", sold4), ("全体（入稿後に再生成された行も除く）", [r for r in sold4 if not r["gen_lt_sub"]])]
    groups += [(f, [r for r in sold4 if family(r["plan"]) == f])
               for f in sorted({family(r["plan"]) for r in sold4})]
    groups += [(p, [r for r in sold4 if r["plan"] == p]) for p in sorted({r["plan"] for r in sold4})]
    body = []
    seen = set()
    for lab, g in groups:
        if not g or lab in seen:
            continue
        seen.add(lab)
        mvv = [m for r in g for m in r["moves"]]
        pm = [m["pred"] / m["morn"] for m in mvv if m["pred"] and m["morn"]]
        mf = [m["morn"] / m["final"] for m in mvv if m["morn"] and m["final"]]
        pf = [m["pred"] / m["final"] for m in mvv if m["pred"] and m["final"]]
        body.append([lab, str(len(g)), qs(pm), qs(mf), qs(pf)])
    W("\n".join(md_table(["商品", "R数", "予測/朝", "朝/最終", "予測/最終"], body)))
    W("**的中した脚だけ**（勝者の呪いの確認）と、計画払戻に対する実払戻の比。計画払戻は 2 通り: "
      "(a) 的中脚の 賭け金×予測オッズ、(b) `pred_mean_payout`（入稿ゲートの入力＝上帯を重ねる前の 全点の想定払戻の平均）:\n")
    body = []
    for lab, g in groups:
        hl, ra, rb = [], [], []
        for r in g:
            if not r["B"]["hit"]:
                continue
            for m in r["moves"]:
                if m["won"] and m["pred"] and m["final"]:
                    hl.append(m)
                    ra.append(r["B"]["pay"] / (m["stake"] * m["pred"]))
                    if r["pred_mean_payout"]:
                        rb.append(r["B"]["pay"] / r["pred_mean_payout"])
        if hl:
            body.append([lab, str(len(hl)), qs([m["pred"] / m["final"] for m in hl]),
                         qs([m["morn"] / m["final"] for m in hl if m["morn"]]), qs(ra), qs(rb)])
    W("\n".join(md_table(["商品", "的中脚数", "予測/最終", "朝/最終", "実払戻/(a)", "実払戻/(b)"], body)))

    # ── 5 B′
    W("## 5. 参考: B′（B と同じ買い目を予測オッズで dutch に配り直す）\n")
    W("配分の違いだけを切り出す参考。売った買い目・朝便。plan の配分が dutch でないもの（conf / equal）と dutch のものに分けた。\n")
    bp = []
    for lab, g in (("配分が dutch の商品", [r for r in sold_m if r["alloc"] == "dutch"]),
                   ("配分が conf/equal の商品", [r for r in sold_m if r["alloc"] != "dutch"])):
        g = [r for r in g if all(r.get(x) for x in ("B", "Bp", "A", "C", "F"))]
        if not g:
            continue
        d = sorted({r["day"] for r in g})
        P = {x: sum(r[x]["pay"] for r in g) / sum(r[x]["net_cost"] for r in g) for x in ("B", "Bp", "A", "C", "F")}
        bp.append([lab, str(len(g)), str(len(d))] + [pct(P[x]) for x in ("B", "Bp", "A", "C", "F")])
    W("\n".join(md_table(["群", "R数", "日数", "B", "B′", "A", "C", "F"], bp)))

    W("## 6. 読むときの注意（数値の限界）\n")
    W("- 期間は約 5 週間。高額枠の的中率は 3〜7%、10万円以上の的中は数件で、CI は広く、差の符号が決まらない項目が多い。")
    W("- A/Ag/C/F は「同じ k の低オッズ上位 k 点」で、B の選び方（モデル確率・型ごとの帯・ライン構造）とは別の買い方。"
      "高額・看板枠では狙う帯が違うため、的中率と回収率の分布が B と大きく異なる。")
    W("- plan 別の n<20 の行（例: C_big・D_big・B_big・C_sign）は CI が `[+0, +965]` のように端が 0 に張り付く。参考値として読む。")
    W("- 的中率は「払戻>0（ガミ含む）」と「払戻>賭け金（表示的中）」を併記した。AUDIT §4.1 の表示的中は後者。")
    W("- §0 の入稿セッション別時刻（morning / noon / evening）は netkeirin への入稿バッチの区分で、本表の朝便・昼夜便"
      "（基準時刻 09 時前/以降）とは別物。")
    W("- 払戻はすべて最終オッズ。入稿時点で A/Ag/C を実際に買えば、締切までに動くぶん（§4）だけ払戻が変わる。")
    # ── 7 レッドチーム対応
    cm = common(sold_m, ARMS_MAIN)
    nz = tot = 0
    for pk in sorted({r["plan"] for r in cm}):
        g = [r for r in cm if r["plan"] == pk]
        s_ = summarize(g, ARMS_MAIN)
        for x in ("A", "C", "F"):
            tot += 1
            d, (lo, hi) = s_[f"B-{x}"]
            nz += (lo > 0 or hi < 0)
    b_all = _roi_of(sold_m, "B")
    b_cm = _roi_of(cm, "B")
    mo_cnt = collections.Counter(r["day"][:7] for r in cm)
    mo_days = {m: len({r["day"] for r in cm if r["day"][:7] == m}) for m in mo_cnt}
    W("## 7. レッドチーム対応（`reports/redteam_20261005.md` R1〜R17）\n")
    W("レッドチーム報告本体は書き換えていない。状態: **対応済** = この報告に反映した、**対象外** = baseline の記述に影響しない"
      "（H2・feature_audit の指摘）、**未** = 測れていない/実施していない。\n")
    resp_rows = [
        ["R1 重大", "対応済", "3.1 の冒頭に「B−A は解釈できない」を明記。3.1 末尾に 時間帯×朝板の厚さ・ISO 週の層別表（B−A の符号が行き来する）、"
         "A∩F 一致率（薄い板で低い）、層内で符号が揃うかの数え上げ表を追加。"],
        ["R2 重大", "対応済", "3.6 に全腕から同じ件数（上位 0/1/3/5 件）を除いた回収率と B−A/B−C/B−F（CI は除き直し）を、"
         "売った×朝便（商品別・高額枠を含む）・昼夜便・候補で並べた。従来の「最高配当1件除外」列は残してある。"],
        ["R3 中", "対象外", "H2（波乱指数）の最終オッズ使用の指摘。baseline の記述に影響しない。"],
        ["R4 軽", f"対応済", f"発走後に生成された {len(psg)} 件を §0 の予測板の忠実度と §4 の予測オッズ比から除いた値に差し替え。"
         "入稿後に再生成された売った行も除いた版を §4 に併記。回収率の B は legs=入稿の買い目で影響なし（食い違い 2 件は払戻 0 円）。"],
        ["R5 中", "対応済（注記）", "C の予測板は抽出時点の `wt_entries` からの再構成で、本番が見た板ではない。昼夜便は忠実度が崩れる（§0）。"
         "C 腕は参考値扱い。C が選んだ組の忠実度は本番が保存していないため測れない（未）。欠車 36 件は全腕共通の母集団から外れている。"],
        ["R6 軽", "対象外", "H2 の `mdl_ent` の in-sample 疑い。"],
        ["R7 中", "対象外", "H2 の `mdl_ent` と `mkt_p100` の独立性。"],
        ["R7b 中", "対応済（注記）", "C は最終三連単オッズを目的変数に学習した予測オッズで選ぶため、C ≈ F の縮小版（最終市場の近似）。"
         "主表で C が A と F の間にあるのはこの位置関係で、「モデルの予測だけで市場に近づく」証拠ではない。"],
        ["R8 否定", "対象外", "H2 の板内順位バイアス。"],
        ["R9 中", "対応済（注記）", "§4 の朝->最終のドリフトを「公開による目減り」と読まない旨を §4 に明記（公開ダミーの係数は 0 を跨ぐ）。"],
        ["R10 中", "対応済（注記）", f"plan 別の差の表は多重比較と退化した CI（B が的中 0 件の plan 等）が混ざる。本版の主表では plan×{3} 腕の差の区間 "
         f"{tot} 本のうち 0 を跨がないものが {nz} 本（偶然の期待は約 {tot * 0.05:.1f} 本）。plan 別の行で「X は朝の市場に勝つ」等と読まないこと。"],
        ["R11 軽", "対象外", "H2 の探索窓/確認窓の構成数。"],
        ["R12 軽", "未", "開催日ブートストラップの被覆。週単位は 6 クラスタで比較にならないため未実施。CI は名目より狭い可能性がある"
         "（払戻が 10 万円超の数件に支配される母集団）。"],
        ["R13 中", "対応済（注記）",
         f"主表（共通母集団）は {len({r['day'] for r in cm})} 開催日・ISO {len({datetime.fromisoformat(r['day']).isocalendar()[1] for r in cm})} 週。"
         "月別の日数: " + "、".join(f"{m} {mo_days[m]}日/{mo_cnt[m]}件" for m in sorted(mo_cnt)) + "。"
         "段 T_* の実売は 2026-09-15 の 1 日だけで、その日は朝板 06:05 取得（有効比率 0.00）。グレード別の層別は未。"],
        ["R14 中", "対応済（注記）",
         f"帯が揃っていない（高額枠は A/C/F と狙う帯が別物）。共通母集団の取り方は B に有利側: 全 {len(sold_m)} 件の B {pct(b_all)} に対し"
         f"共通母集団 {len(cm)} 件は {pct(b_cm)}（外れた 92 件ほどは主に薄い板）。配分は差を作っていない（§5 の B′）。"
         "F は選定に最終オッズを使う腕なので、B−F を「市場に有意に負ける」と読まないこと（§3.6 の除き直し後も CI を併記）。"],
        ["R15 軽", "未（一部対応）", "公開の影響は R9 の回帰で検出されず。自分の賭け金によるオッズ低下は組ごとの売上データが無く未測定。"
         "冒頭の「払戻は上限値」の根拠は、締切までの動きと自分の賭け金による低下の両方で、後者の大きさは不明。"],
        ["R16 軽", "対象外", "H2 の母集団から落ちたレース。"],
        ["R17 軽", "対象外", "feature_audit の未検証 17 列・朝値の直接比較。"],
    ]
    W("\n".join(md_table(["指摘", "状態", "反映内容"], resp_rows)))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", REPORT)


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("extract")
    e.add_argument("--from", dest="date_from", required=True)
    e.add_argument("--to", dest="date_to", required=True)
    e.add_argument("--force", action="store_true")
    pr = sp.add_parser("premise")
    pr.add_argument("--from", dest="date_from", required=True)
    pr.add_argument("--to", dest="date_to", required=True)
    sp.add_parser("startat")
    sp.add_parser("report")
    a = ap.parse_args()
    {"extract": cmd_extract, "premise": cmd_premise, "startat": cmd_startat,
     "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
