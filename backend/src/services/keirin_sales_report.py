"""netkeirin 売上の日次レポート（2026-08-16 新設）。

毎朝 9:40 の売上取り込み（`scripts/scrape_netkeirin_sales.sh`）のあとに、前日の
販売実績と当月ぶんの売上を Discord へ送るための計算・文面・送信をまとめる。

## 🔴 標準ライブラリ以外を import しないこと

このモジュールは **2つの Python 環境から読まれる**:

  - kiseki backend（FastAPI・`/api/keirin/netkeirin-sales` が売上率を使う）
  - **VPS の keirin venv**（`backend/scripts/scrape_netkeirin_sales.py` が
    そちらの venv で動く。FastAPI も SQLAlchemy も無い）

`requests` すら入れてはいけない（backend の CI venv に無く、入れると
**Web は無事なまま通知だけが落ちる**）。HTTP は `urllib` で足りる。
同じ制約が `services/keirin_marquee.py` にもある（そちらは keirin が
ファイル読み込みで束縛している）。

## 売上の定義

    売上 = 販売*有償*pt × REVENUE_RATE

🔴 `sold_points`（販売pt）には**無償ptが混ざり収益にならない**。
   画面にも通知にも両方を並べて出すこと。片方だけ見せると収益を誤読する。

## 文面の作法（2026-09-07 ユーザー指摘）

🔴 **コードブロック（```）で桁を揃えない。** Discord のスマホ表示は
   コードブロックを折り返さず、幅の広い行が途中で切れてレイアウトが崩れる。
   通常テキストで**1行を短く**書くこと（端末幅で自然に折り返す）。

本文は4節: 当日の売上 → 「自信あり」1レースの的中と売上 → 的中レースの売上 →
**直近7日の見張り**（表示的中率と10万円以上の件数・2026-09-14 追加）。
的中の定義は netkeirin の表示的中と同じ `n_hits_excl_garami`（ガミを除く）で、
ガミ（払戻＜賭け金）は「的中」と混ぜずに別の記号で出す。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

#: 売上金額 = 販売有償pt × この率。**ここが唯一の正本**
#: （API も日次通知もこの値を読む。写して使わないこと）。
REVENUE_RATE = 0.30

#: Discord へ送るときのタイムアウト（秒）
_TIMEOUT = 15

# ── 直近7日の見張り（2026-09-14 追加）────────────────────────────────────
#
# 売上ドライバー分析（`keirin/docs/sales_kpi.md` §12）で、商品配分は
#
#     表示的中率 = (H × 高額産出枠の的中 + (N − H) × 本線の的中) ÷ N
#
# の1本の式で決まり、高額枠を増やすほど表示的中率が下がると分かった。
# 停止条件（`keirin/docs/PREREG_SALES_ALLOCATION_2026_09_14.md`）を
# **人が毎週 SQL を叩いて確かめる運用は続かない**ので、毎朝届く通知に載せる。
#
# 🔴 **ここは「見張り」であって「判定」ではない。** 停止条件は
#    「2週連続で下限割れ」「3週連続で10万円以上が週2件未満」で、1日の通知で
#    割れていても即座に戻さない（1週の窓は下振れで簡単に割れる）。
#    通知は ⚠️ を付けるだけにする。

#: 見張りの窓（暦日）。**sale_date の暦7日**で数える（開催の無い日は日数が減る）。
GUARD_WINDOW_DAYS = 7

#: 表示的中率の**運用フロア**（%）。KPI 正本の 20% に 2pt のマージンを取った値。
#: 🔴 20% は競合ベンチマークで**測定ではない**（自社データで「的中率は売上に
#:    効かない」と言えるのは実測した 20〜36% の範囲内だけ）。だから手前で止める。
HIT_RATE_FLOOR_PCT = 22.0

#: 「高額払戻」の境目（円・払戻額）。売上は**直近7日にこの額以上が何回出たか**で
#: 決まる（spearman 0.522・p=0.0006・0〜1回 10,952pt/日 ↔ 3回以上 30,924pt/日）。
BIG_PAYOUT_YEN = 100_000

#: 1週間に欲しい高額払戻の件数（目安）。9/1〜9/12 の実測は 2.9件/週。
BIG_PAYOUT_WEEKLY_MIN = 2


def revenue_yen(sold_paid_points: int | float | None) -> int:
    """販売有償pt から売上（円）を出す。

    ⚠️ **販売pt（`sold_points`）を渡さないこと。** 無償ptが混ざっており、
       そのまま掛けると売上を過大に見せる。
    """
    return round(int(sold_paid_points or 0) * REVENUE_RATE)


def monthly_rollup(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """日別の売上行を**月別**へ畳む（売上金額つき・2026-09-13 新設）。

    `items` は `/api/keirin/netkeirin-sales` が返す日別行と同じ形
    （`date`=YYYY-MM-DD / `n_sold` / `sold_points` / `sold_paid_points` /
    `stake_amount` / `payout_amount`）。

    🔴 **売上は「月合計の有償pt × 料率」で出す。日別の円を足さない。**
       日ごとに丸めてから足すと端数が積み上がる。2026-08 実測:

           月合計方式  401,720 pt × 0.30 = 120,516 円（実際の入金 120,517 円）
           日別合算    日ごとに round →  120,519 円（3 円ずれる）

       有償ptは 5 pt 刻みの日があるので `x.5` が月に数回出る。

    ⚠️ **回収率は月内の平均ではなく「合計払戻 ÷ 合計賭け金」**。日別の率を
       平均すると賭け金の小さい日が過大に効いて実勢とズレる。

    ⚠️ `paid_known` は**月内に1日でも有償ptの欠測があれば False**。欠測を 0 と
       して積むと「その日は全部無償だった」という静かな嘘になる（棒も表も
       自然に見えてしまう）。
    """
    by: dict[str, dict[str, Any]] = {}
    for it in items:
        month = str(it.get("date") or "")[:7]
        if len(month) != 7:
            continue
        cur = by.get(month)
        if cur is None:
            cur = by[month] = {
                "month": month, "n_days": 0, "n_sold": 0,
                "sold_points": 0, "sold_paid_points": 0, "paid_known": True,
                "stake_amount": 0, "payout_amount": 0,
            }
        cur["n_days"] += 1
        cur["n_sold"] += int(it.get("n_sold") or 0)
        cur["sold_points"] += int(it.get("sold_points") or 0)
        cur["sold_paid_points"] += int(it.get("sold_paid_points") or 0)
        cur["paid_known"] = cur["paid_known"] and it.get("sold_paid_points") is not None
        cur["stake_amount"] += int(it.get("stake_amount") or 0)
        cur["payout_amount"] += int(it.get("payout_amount") or 0)

    out: list[dict[str, Any]] = []
    for month in sorted(by):
        m = by[month]
        stake = m["stake_amount"]
        m["recovery_rate_pct"] = (
            round(m["payout_amount"] / stake * 100, 1) if stake > 0 else None)
        m["revenue_yen"] = revenue_yen(m["sold_paid_points"])
        out.append(m)
    return out


def _hit_mark(n_incl: int | None, n_excl: int | None) -> str:
    """的中の記号。**ガミ（払戻＜賭け金）を「的中」と混ぜない**。

    netkeirin の表示的中率は `n_hits_excl_garami`（払戻＞賭け金）の方。
    ガミ込み（`n_hits_incl_garami`）だけが立っている行は「当たったが負けた」。
    採点前（両方 None）は黙って不的中にせず「採点待ち」と書く。
    """
    if n_incl is None and n_excl is None:
        return "⏳ 採点待ち"
    if int(n_excl or 0) > 0:
        return "⭕ 的中"
    if int(n_incl or 0) > 0:
        return "△ ガミ"
    return "❌ 不的中"


def _confident_lines(c: Mapping[str, Any] | None) -> list[str]:
    """「自信あり」1レースの的中と売上（2026-09-07 ユーザー指示）。

    🔴 **無い日も1行出す**。行ごと消えると「選定が落ちた」のか「該当が
       無かった」のか読み手に区別できない（入稿通知と同じ約束）。
    """
    if not c:
        return ["🎯 **自信あり** なし"]
    head = f"🎯 **自信あり** {c.get('label') or '?'}"
    if c.get("rank_key"):
        head += f"（{c['rank_key']}）"
    mark = _hit_mark(c.get("n_hits_incl"), c.get("n_hits_excl"))
    payout = int(c.get("payout") or 0)
    line2 = f"{mark}　払戻 {payout:,} 円"
    paid = int(c.get("sold_paid_points") or 0)
    line3 = (f"売上 {revenue_yen(paid):,} 円"
             f"（{int(c.get('n_sold') or 0):,} 点 / 有償 {paid:,} pt）")
    return [head, line2, line3]


def _hit_race_lines(r: Mapping[str, Any] | None) -> list[str]:
    """その日の**的中レースの売上**（2026-09-07 ユーザー指示）。

    ⚠️ レース別の売上は `keirin.netkeirin_sales_race` にしか無い。日別だけを
       取り込んだ回は `None` が来るので、その回はこの節を出さない
       （0件と書くと「1本も当たらなかった」と誤読する）。
    """
    if not r or not int(r.get("n_races") or 0):
        return []
    n = int(r["n_races"])
    n_hit = int(r.get("n_hit") or 0)
    n_incl = int(r.get("n_hit_incl") or 0)
    head = f"📊 **的中レース** {n_hit} / {n} R（{n_hit / n * 100:.1f}%）"
    if n_incl != n_hit:
        head += f"　※ガミ込み {n_incl}"
    paid = int(r.get("paid") or 0)
    paid_hit = int(r.get("paid_hit") or 0)
    share = f"　全体の {paid_hit / paid * 100:.1f}%" if paid else ""
    return [
        head,
        f"売上 {revenue_yen(paid_hit):,} 円{share}",
        f"販売 {int(r.get('n_sold_hit') or 0):,} 点 / 有償 {paid_hit:,} pt",
    ]


def _guard_lines(g: Mapping[str, Any] | None) -> list[str]:
    """直近7日の**表示的中率**と**10万円以上の件数**（2026-09-14 追加）。

    必要なキー: `start` / `end`（YYYYMMDD）/ `n_days` / `n_pred` / `n_hit`
    任意のキー: `n_race_days`（レース別を取り込めた日数）/ `n_big`

    🔴 **表示的中率は日別テーブルの `n_hits_excl_garami ÷ n_predictions`**。
       netkeirin が公表している値そのもので、ガミを混ぜない。
    🔴 **レース別を取り込めていない日があれば件数に注記する。** 10万円以上は
       レース別にしか無いので、欠けた日を 0件として数えると「出なかった」と
       誤読する。1日も無ければ件数を出さない。
    """
    if not g or not int(g.get("n_pred") or 0):
        return []
    start, end = str(g["start"]), str(g["end"])
    n_days = int(g.get("n_days") or 0)
    head = (f"🛡 **直近{GUARD_WINDOW_DAYS}日** "
            f"{start[4:6]}/{start[6:]}〜{end[4:6]}/{end[6:]}")
    if n_days < GUARD_WINDOW_DAYS:
        head += f"（{n_days}日分）"

    rate = int(g.get("n_hit") or 0) / int(g["n_pred"]) * 100
    margin = rate - HIT_RATE_FLOOR_PCT
    if margin >= 0:
        hit = f"表示的中 {rate:.1f}%（下限{HIT_RATE_FLOOR_PCT:g}%まで +{margin:.1f}pt）"
    else:
        hit = (f"⚠️ 表示的中 {rate:.1f}%"
               f"（下限{HIT_RATE_FLOOR_PCT:g}%を {-margin:.1f}pt 割れ）")

    label = f"{BIG_PAYOUT_YEN // 10_000}万円以上"
    race_days = int(g.get("n_race_days") or 0)
    if not race_days:
        big = f"{label} 未取込（レース別なし）"
    else:
        n_big = int(g.get("n_big") or 0)
        mark = "" if n_big >= BIG_PAYOUT_WEEKLY_MIN else "⚠️ "
        note = "" if race_days >= n_days else f"・{race_days}/{n_days}日分"
        big = f"{mark}{label} {n_big}件（目安 週{BIG_PAYOUT_WEEKLY_MIN}件以上{note}）"
    return [head, hit, big]


def build_sales_message(s: Mapping[str, Any]) -> str:
    """日次売上の本文を組む。

    必要なキー: `sale_date`(YYYYMMDD) / `n_sold` / `sold_points` /
    `sold_paid_points` / `month_n_days` / `month_sold_paid_points`
    任意のキー: `confident`（自信ありの1レース）/ `race_stats`（的中レースの売上）/
    `guard`（直近7日の見張り）

    🔴 **コードブロック（```）で桁を揃えない**（2026-09-07 ユーザー指摘）。
       Discord のスマホ表示はコードブロックを折り返さず、幅の広い行が
       途中で切れてレイアウトが崩れる。**1行を短く保ち、通常テキストで**
       書くこと（通常テキストは端末幅で自然に折り返す）。
    """
    d = str(s["sale_date"])
    paid = int(s["sold_paid_points"] or 0)
    month_paid = int(s["month_sold_paid_points"] or 0)
    lines = [
        f"💰 **netkeirin 売上 {d[:4]}-{d[4:6]}-{d[6:]}**",
        f"売上 **{revenue_yen(paid):,} 円**（販売有償pt × {REVENUE_RATE}）",
        f"販売 {int(s['n_sold'] or 0):,} 点",
        f"販売pt {int(s['sold_points'] or 0):,} pt"
        f"（販売有償pt {paid:,} pt）",
        # 🔴 **「累計」と書かない**（2026-09-13 ユーザー指摘）。値は当月分だけ
        #    （`sale_date LIKE 'YYYYMM%'`）なのに、月をまたいで積み上がった額だと
        #    読まれる。月を明示して「その月の売上」と書く。
        f"{int(d[4:6])}月の売上 {revenue_yen(month_paid):,} 円"
        f"（{int(s['month_n_days'] or 0)}日 / 有償 {month_paid:,} pt）",
    ]
    lines += [""] + _confident_lines(s.get("confident"))
    hit = _hit_race_lines(s.get("race_stats"))
    if hit:
        lines += [""] + hit
    guard = _guard_lines(s.get("guard"))
    if guard:
        lines += [""] + guard
    return "\n".join(lines)


def post_to_discord(webhook_url: str, content: str) -> bool:
    """Discord へ送る。成功で True。

    ⚠️ **例外を投げない。** 呼び出し元（売上の取り込み）は通知の失敗で
       落ちてはいけない —— データは既に DB に入っており、通知はその報告でしかない。
       ここで落とすと「スクレイプが失敗した」ように見える。
    """
    if not webhook_url:
        return False
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "kiseki-netkeirin-sales/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
