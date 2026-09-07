"""netkeirin 売上の日次レポート（2026-08-16 新設）。

毎朝 9:40 の売上取り込み（`scripts/scrape_netkeirin_sales.sh`）のあとに、前日の
販売実績と当月の累計売上を Discord へ送るための計算・文面・送信をまとめる。

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

本文は3節: 当日の売上 → 「自信あり」1レースの的中と売上 → 的中レースの売上。
的中の定義は netkeirin の表示的中と同じ `n_hits_excl_garami`（ガミを除く）で、
ガミ（払戻＜賭け金）は「的中」と混ぜずに別の記号で出す。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

#: 売上金額 = 販売有償pt × この率。**ここが唯一の正本**
#: （API も日次通知もこの値を読む。写して使わないこと）。
REVENUE_RATE = 0.30

#: Discord へ送るときのタイムアウト（秒）
_TIMEOUT = 15


def revenue_yen(sold_paid_points: int | float | None) -> int:
    """販売有償pt から売上（円）を出す。

    ⚠️ **販売pt（`sold_points`）を渡さないこと。** 無償ptが混ざっており、
       そのまま掛けると売上を過大に見せる。
    """
    return round(int(sold_paid_points or 0) * REVENUE_RATE)


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


def build_sales_message(s: Mapping[str, Any]) -> str:
    """日次売上の本文を組む。

    必要なキー: `sale_date`(YYYYMMDD) / `n_sold` / `sold_points` /
    `sold_paid_points` / `month_n_days` / `month_sold_paid_points`
    任意のキー: `confident`（自信ありの1レース）/ `race_stats`（的中レースの売上）

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
        f"{d[4:6]}月 累計 {revenue_yen(month_paid):,} 円"
        f"（{int(s['month_n_days'] or 0)}日 / 有償 {month_paid:,} pt）",
    ]
    lines += [""] + _confident_lines(s.get("confident"))
    hit = _hit_race_lines(s.get("race_stats"))
    if hit:
        lines += [""] + hit
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
