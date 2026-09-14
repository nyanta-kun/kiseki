#!/usr/bin/env python3
"""売れる予想と当たる予想の配分を**毎週測り直す**レポート（2026-09-14 新設）。

背景と計画は `docs/sales_kpi.md` §12、停止条件は
`docs/PREREG_SALES_ALLOCATION_2026_09_14.md`。この1本で次の5つを出す:

1. 期間サマリ（売上・個/R・表示的中・ガミ率・的中中央払戻・無売上率・10万円以上）
2. 週次表（`sales_kpi.md` §8 に貼る行）
3. 2枠分解と設計式（高額産出枠 H と本線の的中率から、フロアごとに置ける H）
4. 予備在庫（1商品も出していないレースで売れる本線の表示的中率）
5. 自信ありの置き場所（レース種別ごとの販売件数）

    .venv/bin/python scripts/sales_allocation_report.py
    .venv/bin/python scripts/sales_allocation_report.py --start 20260913 --end 20260926

## 設計式

    表示的中率 = (H × h_H + (N − H) × h_B) ÷ N
    10万円以上 / 週 = H × 7 × p10

N=1日の総商品数、H=高額産出枠の本数、h_H / h_B=それぞれの表示的中率、
p10=高額産出枠1本あたりの10万円以上の率。フロア F を満たす最大の H は
`(h_B − F) × N ÷ (h_B − h_H)`。

## 🔴 読むときの注意

- **表示的中は `payout > 賭け金`**（netkeirin の公表値・ガミを除く）
- **p10 は件数が少なく CI が広い**（9/1〜9/12 は n=129 で 95%CI [1.3%, 8.8%]）。
  10万円以上/週の予測は ±2倍で読むこと。レポートは CI も併記する
- **予備在庫の「売れ行き」は出せない**（売っていないので実測が無い）。
  出せるのは表示的中率だけ
- **本数の弾力性（本数を増やして売上が増えるか）はここでは測らない**。
  n=日数の回帰で、トレンド・直前の払戻との同時推定が要る（§12 の数字は
  scratchpad の分析で出したもの）。週次で追うのは停止条件に要る量だけ

DB は読み取りのみ。keirin の venv（psycopg2 あり・pandas 無し）で動く。
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]          # keirin/
KISEKI = REPO.parent

#: 売上金額 = 販売有償pt × この率。正本は backend の `keirin_sales_report.REVENUE_RATE`。
#: 🔴 ここは読み取り専用レポートなので、正本をファイルから読んで食い違いを防ぐ。
sys.path.insert(0, str(KISEKI / "backend"))
from src.services.keirin_sales_report import (  # noqa: E402
    BIG_PAYOUT_YEN, HIT_RATE_FLOOR_PCT, REVENUE_RATE,
)

#: 1レースの賭け金（netkeirin のルールで固定）。表示的中は払戻がこれを超えたもの。
STAKE_YEN = 10_000

#: 高額産出枠。**10万円以上を実際に作っている枠**（9/1〜9/12 で本線495本は0件）。
#: 🔴 `type_lab.HIGHPAY_PLAN_KEYS` とは別物（あちらは高額枠 B/C/D の _sign/_big だけ）。
#:    ここでは看板枠 F_sign と穴・払戻狙い A_ana / F_pay も含める。
PAYOUT_PLANS = frozenset({"A_ana", "F_pay"})


def is_payout_slot(rank_key: str | None) -> bool:
    """高額産出枠か。`_sign` / `_big` の全型と A_ana / F_pay。"""
    k = rank_key or ""
    return k.endswith("_sign") or k.endswith("_big") or k in PAYOUT_PLANS


def race_class(label: str | None) -> str:
    """レース名からレース種別の大分類を返す。

    🔴 **「準決勝」は「決勝」を部分一致で拾う**ので先に除外する。
    """
    s = label or ""
    if "準決勝" in s:
        return "準決勝"
    if "決勝" in s:
        return "決勝"
    if "予選" in s:
        return "予選"
    if "選抜" in s or "特選" in s:
        return "選抜/特選"
    return "その他"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二項比率の Wilson 95%CI。"""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def max_payout_slots(n: float, h_hit: float, h_pay: float, floor: float) -> float:
    """表示的中率 `floor`（%）を満たす最大の高額産出枠本数。"""
    if h_hit <= h_pay:
        return math.nan
    return max(0.0, (h_hit - floor) * n / (h_hit - h_pay))


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """kiseki/.env の DB_* を読む（python-dotenv が無い venv でも動くよう手で読む）。"""
    env = KISEKI / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _connect():
    _load_env()
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"])


def fetch(cur, start: str, end: str) -> dict:
    cur.execute(
        "SELECT sale_date, n_predictions, n_sold, sold_paid_points, "
        "       n_hits_excl_garami, n_hits_incl_garami "
        "FROM keirin.netkeirin_sales_daily WHERE sale_date BETWEEN %s AND %s "
        "ORDER BY sale_date", (start, end))
    daily = cur.fetchall()
    # 🔴 1レースに提出が2本ある古い行（1レース1商品ガード以前）で売上を二重に
    #    数えないよう、提出側は DISTINCT ON で1本に畳む。
    cur.execute(
        "SELECT r.race_date, r.race_key, r.race_label, r.n_sold, r.sold_paid_points, "
        "       r.n_hits_excl_garami, r.n_hits_incl_garami, r.payout_amount, "
        "       s.rank_key, COALESCE(s.is_confident, false) "
        "FROM keirin.netkeirin_sales_race r "
        "LEFT JOIN LATERAL ("
        "  SELECT rank_key, is_confident FROM keirin.netkeirin_submissions x "
        "  WHERE x.netkeirin_race_id = r.race_id AND x.deleted_at IS NULL "
        "  ORDER BY x.is_confident DESC, x.submitted_at LIMIT 1) s ON true "
        "WHERE r.race_date BETWEEN %s AND %s ORDER BY r.race_date", (start, end))
    races = cur.fetchall()
    d0 = datetime.strptime(start, "%Y%m%d").date()
    d1 = datetime.strptime(end, "%Y%m%d").date()
    # 予備在庫: 型ラボが生成して採点済みなのに、1商品も出していないレース。
    # 「本線として足したら何を売るか」を1レース1行に畳む。
    # 🔴 本番の `type_lab.sell_plans_for` に合わせる: **9車の型F は `F_line`**、
    #    それ以外（7車の型F を含む）は `{型}_hit`。`F_line` の行は7車にも
    #    生成されている（ペーパー）ので、車数で絞らないと7車の型F を
    #    売らない商品で数えてしまう（2026-09-14 に実際に取り違えた）。
    # 🔴 期間の上限を必ず付ける。付けないと採点が進んだ翌日以降の行が混ざる。
    cur.execute(
        "WITH sold AS (SELECT DISTINCT race_key FROM keirin.netkeirin_submissions "
        "              WHERE deleted_at IS NULL) "
        "SELECT DISTINCT ON (t.race_key) t.race_key, t.plan_key, t.payout, t.budget "
        "FROM keirin.type_lab_picks t LEFT JOIN sold s ON s.race_key = t.race_key "
        "WHERE t.mode IN ('live', 'live9') AND t.race_date BETWEEN %s AND %s "
        "  AND t.settled_at IS NOT NULL AND s.race_key IS NULL "
        "  AND ((t.plan_key LIKE '%%\\_hit' AND NOT (t.plan_key = 'F_hit' AND t.n_entries = 9)) "
        "       OR (t.plan_key = 'F_line' AND t.n_entries = 9)) "
        "ORDER BY t.race_key, t.plan_key",
        (d0, d1))
    spare = cur.fetchall()
    return {"daily": daily, "races": races, "spare": spare,
            "n_cal_days": (d1 - d0).days + 1}


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------

def _pct(k: float, n: float) -> str:
    return f"{100 * k / n:.1f}%" if n else "—"


def section_summary(data: dict) -> list[str]:
    daily, races = data["daily"], data["races"]
    days = len(daily)
    if not days:
        return ["（日別の行がありません）"]
    n_pred = sum(r[1] or 0 for r in daily)
    n_sold = sum(r[2] or 0 for r in daily)
    paid = sum(r[3] or 0 for r in daily)
    hit_x = sum(r[4] or 0 for r in daily)
    hit_g = sum(r[5] or 0 for r in daily)
    pays = [r[7] for r in races if (r[6] or 0) > 0 and r[7]]
    n_big = sum(1 for r in races if (r[7] or 0) >= BIG_PAYOUT_YEN)
    race_days = len({r[0] for r in races})
    zero = sum(1 for r in races if not r[3])
    out = [
        "| 指標 | 値 |", "|---|--:|",
        f"| 日数（日別 / レース別） | {days} / {race_days} |",
        f"| 商品 / 日 | {n_pred / days:.1f} |",
        f"| 個 / R | {n_sold / n_pred:.2f} |" if n_pred else "| 個 / R | — |",
        f"| 有償pt / 日 | {paid / days:,.0f} |",
        f"| 売上 / 日 | {paid * REVENUE_RATE / days:,.0f} 円 |",
        f"| 表示的中率 | {_pct(hit_x, n_pred)}（下限 {HIT_RATE_FLOOR_PCT:g}%） |",
        f"| ガミ率（的中に占める） | {_pct(hit_g - hit_x, hit_g)} |",
        f"| 的中1件の中央払戻 | {statistics.median(pays):,.0f} 円 |" if pays
        else "| 的中1件の中央払戻 | — |",
        f"| 無売上率 | {_pct(zero, len(races))} |",
        f"| {BIG_PAYOUT_YEN // 10_000}万円以上 | {n_big} 件"
        f"（{7 * n_big / race_days:.1f} 件/週） |" if race_days
        else f"| {BIG_PAYOUT_YEN // 10_000}万円以上 | — |",
    ]
    return out


def section_weekly(data: dict) -> list[str]:
    """月曜始まりの週次行。`sales_kpi.md` §8 に貼る。"""
    by_week: dict[date, dict] = defaultdict(lambda: defaultdict(float))
    for sd, n_pred, n_sold, paid, hx, hg in data["daily"]:
        d = datetime.strptime(sd, "%Y%m%d").date()
        w = by_week[d - timedelta(days=d.weekday())]
        w["days"] += 1
        w["pred"] += n_pred or 0
        w["sold"] += n_sold or 0
        w["paid"] += paid or 0
        w["hx"] += hx or 0
        w["hg"] += hg or 0
    big: dict[date, int] = defaultdict(int)
    pay_slots: dict[date, int] = defaultdict(int)
    for rd, _, _, _, _, _, _, payout, rank_key, _ in data["races"]:
        d = datetime.strptime(rd, "%Y%m%d").date()
        wk = d - timedelta(days=d.weekday())
        big[wk] += int((payout or 0) >= BIG_PAYOUT_YEN)
        pay_slots[wk] += int(is_payout_slot(rank_key))
    out = ["| 週 | 日数 | 商品/日 | 個/R | 有償pt/日 | 収益/日 | 表示的中 | "
           f"{BIG_PAYOUT_YEN // 10_000}万+ | 高額産出枠/日 |",
           "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for wk in sorted(by_week):
        w = by_week[wk]
        days = w["days"]
        out.append(
            f"| {wk:%m/%d}- | {days:.0f} | {w['pred'] / days:.1f} | "
            f"{w['sold'] / w['pred']:.2f} | {w['paid'] / days:,.0f} | "
            f"{w['paid'] * REVENUE_RATE / days:,.0f} | {_pct(w['hx'], w['pred'])} | "
            f"{big[wk]} | {pay_slots[wk] / days:.1f} |")
    return out


def section_design(data: dict) -> tuple[list[str], dict]:
    races = data["races"]
    days = len({r[0] for r in races})
    if not days:
        return ["（レース別の行がありません）"], {}
    pay = [r for r in races if is_payout_slot(r[8])]
    hit = [r for r in races if not is_payout_slot(r[8])]

    def rate(rows):
        return 100 * sum(1 for r in rows if (r[5] or 0) > 0) / len(rows) if rows else math.nan

    n = len(races) / days
    h = len(pay) / days
    h_pay, h_hit = rate(pay), rate(hit)
    k10 = sum(1 for r in pay if (r[7] or 0) >= BIG_PAYOUT_YEN)
    p10 = 100 * k10 / len(pay) if pay else math.nan
    lo, hi = wilson(k10, len(pay))
    params = {"n": n, "h": h, "h_pay": h_pay, "h_hit": h_hit, "p10": p10}
    out = [
        "| 枠 | 本/日 | 表示的中 | 1Rあたり有償pt | 10万円以上 |",
        "|---|--:|--:|--:|--:|",
        f"| 本線（的中枠） | {len(hit) / days:.1f} | {h_hit:.1f}% | "
        f"{sum(r[4] or 0 for r in hit) / max(len(hit), 1):,.0f} | "
        f"{sum(1 for r in hit if (r[7] or 0) >= BIG_PAYOUT_YEN)} 件 |",
        f"| 高額産出枠 | {h:.1f} | {h_pay:.1f}% | "
        f"{sum(r[4] or 0 for r in pay) / max(len(pay), 1):,.0f} | "
        f"{k10} 件（{p10:.2f}% · 95%CI [{100 * lo:.1f}, {100 * hi:.1f}]） |",
        "",
        f"現在 N={n:.1f}本/日・H={h:.1f}本/日 → 式の表示的中率 "
        f"{(h * h_pay + (n - h) * h_hit) / n:.1f}%・10万円以上 {h * 7 * p10 / 100:.1f} 件/週",
        "",
        "| 総本数 N | フロア | 置ける H | 10万円以上/週 |",
        "|--:|--:|--:|--:|",
    ]
    for fl in (HIT_RATE_FLOOR_PCT + 2, HIT_RATE_FLOOR_PCT, HIT_RATE_FLOOR_PCT - 2):
        hh = max_payout_slots(n, h_hit, h_pay, fl)
        out.append(f"| {n:.1f} | {fl:g}% | {hh:.1f} | {hh * 7 * p10 / 100:.1f} |")
    return out, params


def section_spare(data: dict, params: dict) -> list[str]:
    spare = data["spare"]
    days = data["n_cal_days"]
    if not spare:
        return ["（予備在庫なし・または型ラボの採点がまだ）"]
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for _, plan, payout, budget in spare:
        by[plan][0] += 1
        by[plan][1] += int((payout or 0) > (budget or STAKE_YEN))
    out = ["| プラン | 未使用R | R/日 | 表示的中 |", "|---|--:|--:|--:|"]
    for plan, (n, k) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        out.append(f"| {plan} | {n} | {n / days:.1f} | {_pct(k, n)} |")
    n_all = sum(v[0] for v in by.values())
    k_all = sum(v[1] for v in by.values())
    h_sp = 100 * k_all / n_all
    out.append(f"| **計** | {n_all} | {n_all / days:.1f} | {h_sp:.1f}% |")
    if params:
        n, h_hit, h_pay, p10 = params["n"], params["h_hit"], params["h_pay"], params["p10"]
        cur_rate = (params["h"] * h_pay + (n - params["h"]) * h_hit) / n
        n2 = n + n_all / days
        rate2 = (n * cur_rate + (n_all / days) * h_sp) / n2
        # 足した後の「本線」側の的中率（在庫を本線として足すので本線が混ざる）
        h_hit2 = ((n - params["h"]) * h_hit + (n_all / days) * h_sp) / (n2 - params["h"])
        hh = max_payout_slots(n2, h_hit2, h_pay, HIT_RATE_FLOOR_PCT)
        out += ["",
                f"全部を本線として足すと N {n:.1f} → {n2:.1f}・表示的中 "
                f"{cur_rate:.1f}% → {rate2:.1f}%。フロア {HIT_RATE_FLOOR_PCT:g}% のまま "
                f"H は {max_payout_slots(n, h_hit, h_pay, HIT_RATE_FLOOR_PCT):.1f} → "
                f"{hh:.1f} 本/日（10万円以上 {hh * 7 * p10 / 100:.1f} 件/週）",
                "🔴 在庫の売れ行きは測れない（売っていない）。表示的中だけが実測"]
    return out


def section_confident(data: dict) -> list[str]:
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for _, _, label, n_sold, paid, *_rest, confident in data["races"]:
        if not confident:
            continue
        c = by[race_class(label)]
        c[0] += 1
        c[1] += n_sold or 0
        c[2] += paid or 0
    if not by:
        return ["（自信ありの行なし）"]
    out = ["| 置いた種別 | 本数 | 販売件数/本 | 有償pt/本 |", "|---|--:|--:|--:|"]
    for k, (n, s, p) in sorted(by.items(), key=lambda kv: -kv[1][1] / kv[1][0]):
        out.append(f"| {k} | {n} | {s / n:.2f} | {p / n:,.0f} |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", help="YYYYMMDD（既定: 終了日の13日前）")
    ap.add_argument("--end", help="YYYYMMDD（既定: 日別テーブルの最新日）")
    args = ap.parse_args()

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            end = args.end
            if not end:
                cur.execute("SELECT max(sale_date) FROM keirin.netkeirin_sales_daily")
                end = cur.fetchone()[0]
            start = args.start or (datetime.strptime(end, "%Y%m%d").date()
                                   - timedelta(days=13)).strftime("%Y%m%d")
            data = fetch(cur, start, end)
    finally:
        conn.close()

    design, params = section_design(data)
    blocks = [
        (f"# 売上配分レポート {start}〜{end}", []),
        ("## 1. 期間サマリ", section_summary(data)),
        ("## 2. 週次（sales_kpi.md §8 に貼る）", section_weekly(data)),
        ("## 3. 2枠分解と設計式", design),
        ("## 4. 予備在庫（1商品も出していないレース）", section_spare(data, params)),
        ("## 5. 自信ありの置き場所", section_confident(data)),
    ]
    for head, body in blocks:
        print(head)
        if body:
            print()
            print("\n".join(body))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
