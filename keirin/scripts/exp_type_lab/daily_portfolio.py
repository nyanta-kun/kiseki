#!/usr/bin/env python3
"""1日単位の商品ポートフォリオ設計（2026-09-10・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- 売る1商品は `src/type_lab.sell_plans_for`（看板枠 → 型Aの3分割 → 型F の種別）。
- 入稿の順序は `scripts/netkeirin_submit_type_lab.py::run`:
    並び未公開 → **軸信頼ゲート**（`AXIS_GATE_MIN`・A/D/E/F_hit の4プラン p20）
    → 入稿ゲート（平均想定払戻 2万円超・1点オッズ 2.0以上）
    → **日次上限**（枠外を除いた判定対象 × `DAILY_CAP_RACE_FRACTION=0.5`）
- 上限に当たったときの残す順は `cap_priority = (2*axis_priority + rp_sd_priority)/3`。
  🔴 **`2×axis + rp_sd` は 2026-09-01 に実装済み**（`RP_SD_PRIORITY_AXIS_WEIGHT=2.0`）。
- 上限は**波ごと**（morning/noon/night = 開催の第1R発走時刻で決まる）に掛かる。
- 上限で捨てたレースのうち型B/C/D には**高額枠**（`{型}_sign` / `{型}_big`・
  `HIGHPAY_SLOTS_PER_DAY=5`・2/4本目が `_big`）を置く。既存商品は減らさない。

## 台

`/tmp/daily_portfolio_rows.pkl`（`daily_portfolio_build.py`）＝ 板 36,427R から
本番の生成器で1レース1商品を組み、**入稿ゲートまで通した** 27,799件。
探索 2024-07〜2025-12 / 確認 2026-01〜08。

🔴 判断指標は 件/日・表示的中(payout>投資)・払戻中央・10万+/日 と**日次分布**。
🔴 件数を動かす腕には無作為対照20seed・中央値で比較。
"""
from __future__ import annotations
import csv, pickle, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import importlib.util
_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s); _s.loader.exec_module(_G)
from src.meeting_wave import wave_of_first_hour

JST = timezone(timedelta(hours=9))
ROWS = pickle.load(open("/tmp/daily_portfolio_rows.pkl", "rb"))

# ── メタ（発走時刻→波・cup_id＝節・cup_grade）────────────────────────────
META: dict[str, dict] = {}
with open("/tmp/dp_races_meta.csv") as f:
    for rk, sa, cg, cup in csv.reader(f):
        META[rk] = dict(start=int(sa) if sa else None,
                        cupg=int(cg) if cg else None, cup=cup)
first_hour: dict[tuple[str, str], int] = {}
for r in ROWS:
    m = META.get(r["race_key"])
    if not m or m["start"] is None:
        continue
    k = (r["date"], r["venue"])
    h = datetime.fromtimestamp(m["start"], JST).hour
    if k not in first_hour or h < first_hour[k]:
        first_hour[k] = h
for r in ROWS:
    m = META.get(r["race_key"]) or {}
    r["cup"] = m.get("cup") or f'{r["date"]}{r["venue"]}'
    r["cupg_db"] = m.get("cupg")
    r["wave"] = wave_of_first_hour(first_hour.get((r["date"], r["venue"])))
    r["hour"] = (datetime.fromtimestamp(m["start"], JST).hour
                 if m.get("start") else None)
    r["shown"] = 1 if r["pay"] > r["inv"] else 0
    r["exempt"] = _G.daily_cap_exempt(r["rtype"], r["cupg_db"])
    r["prio"] = _G.cap_priority(r["plan"], r["axis"], r["rp_sd"])
    r["gate"] = _G.passes_axis_gate(r["plan"], r["axis"], 7)

EX = [r for r in ROWS if r["win"] == "explore"]
CF = [r for r in ROWS if r["win"] == "confirm"]


# ── 本番と同じ選抜（軸ゲート → 波ごとの日次上限）────────────────────────
def produce(rows, frac=_G.DAILY_CAP_RACE_FRACTION, prio_key="prio",
            rng: np.random.Generator | None = None, keep_exempt=True):
    """本番の入稿ループを再現して、実際に出る商品だけを返す。"""
    ok = [r for r in rows if r["gate"]]
    out = []
    by = defaultdict(list)
    for r in ok:
        by[(r["date"], r["wave"])].append(r)
    for _k, grp in by.items():
        ex = [r for r in grp if keep_exempt and r["exempt"]]
        rest = [r for r in grp if not (keep_exempt and r["exempt"])]
        out.extend(ex)
        if not frac:
            out.extend(rest); continue
        n = len(rest)
        if n == 0:
            continue
        cap = max(1, int(n * frac))
        if rng is not None:
            keys = rng.random(n)
        else:
            keys = np.array([r[prio_key] for r in rest])
        order = np.argsort(-keys, kind="stable")
        out.extend(rest[j] for j in order[:cap])
    return out


def daily(rows):
    """日ごとの (件数, 表示的中数, 投資, 払戻, 10万+件)。"""
    d = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    for r in rows:
        a = d[r["date"]]
        a[0] += 1; a[1] += r["shown"]; a[2] += r["inv"]; a[3] += r["pay"]
        a[4] += 1 if r["pay"] >= 100_000 else 0
    return d


def kpi(rows) -> dict:
    if not rows:
        return dict(n=0)
    d = daily(rows)
    nd = len(d)
    sh = [a[1] / a[0] * 100 for a in d.values()]
    roi = [a[3] / a[2] * 100 for a in d.values()]
    pays = sorted(r["pay"] for r in rows if r["pay"] > 0)
    return dict(
        n=len(rows), nd=nd, perday=len(rows) / nd,
        shown=sum(r["shown"] for r in rows) / len(rows) * 100,
        roi=sum(r["pay"] for r in rows) / sum(r["inv"] for r in rows) * 100,
        big=sum(1 for r in rows if r["pay"] >= 100_000) / nd,
        med_pay=float(np.median(pays)) if pays else 0.0,
        d_sh_med=float(np.median(sh)), d_sh_q1=float(np.percentile(sh, 25)),
        d_sh_q3=float(np.percentile(sh, 75)),
        d_zero=sum(1 for x in sh if x == 0) / nd * 100,
        d_sub10=sum(1 for x in sh if x < 10) / nd * 100,
        d_roi_med=float(np.median(roi)),
        d_roi_sub50=sum(1 for x in roi if x < 50) / nd * 100,
        d_roi_over100=sum(1 for x in roi if x >= 100) / nd * 100,
    )


HDR = ("{:34s} {:>6s} {:>7s} {:>6s} {:>6s} {:>7s}|{:>7s} {:>6s} {:>6s} {:>6s} {:>6s} {:>6s}"
       .format("腕", "件/日", "表示的中", "ROI", "10万+", "日中央", "日Q1", "日Q3",
               "0件%", "<10%", "日ROI中", "<50%"))


def line(name, s):
    if not s.get("n"):
        return f"{name:34s}  (該当なし)"
    return ("{:34s} {perday:6.2f} {shown:7.2f} {roi:6.1f} {big:6.3f} {d_sh_med:7.2f}|"
            "{d_sh_q1:7.2f} {d_sh_q3:6.2f} {d_zero:6.1f} {d_sub10:6.1f} "
            "{d_roi_med:6.1f} {d_roi_sub50:6.1f}").format(name, **s)
