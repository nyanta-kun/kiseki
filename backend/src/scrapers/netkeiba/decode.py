"""netkeiba のレスポンスを正しい文字コードで復号する。

sekito@827cdb3 の `decode_page()` をそのまま移設した。

🔴 **決め打ちしてはいけない。** netkeiba は**ホストごとに文字コードが違う**
（2026-09-06 実測）:

    race.netkeiba.com / nar.netkeiba.com  → UTF-8
    db.netkeiba.com                        → EUC-JP

移設元は `r.content.decode("euc-jp", errors="replace")` と決め打ちしていた。
片方が必ず壊れ、しかも `errors="replace"` が例外を握り潰すので **U+FFFD 入りの
文字列が黙って DB に入る**。2026-05-04 の requests 移行から 4 か月、
馬名 95% / 血統 100% / 調教 31% / idx_course 13% が文字化けしたまま誰も気づかなかった
（[[netkeiba-scraper-facts]]）。

⚠️ 移設時点で `lib/sekito/scrapers/odds/login.py` にはこの修正が**届いていない**。
   ログイン確認で `race.netkeiba.com` を EUC-JP で読んでいるため「ログアウト」判定が
   常に外れ、`memberRank` の正規表現だけが実質のゲートになっている。
   移植版（`session.py`）はここを `decode_page` に直してある。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# HTML の先頭からこのバイト数までを meta charset の探索範囲にする。
_META_SCAN_BYTES = 2048

# 判定順の最後に置く固定候補。UTF-8 を先に試すのは、厳密デコードだと
# EUC-JP のバイト列が UTF-8 として通ってしまう確率が実用上ほぼ無いため。
_FALLBACK_ENCODINGS = ("utf-8", "euc-jp")


def decode_page(response) -> str:
    """レスポンスを復号する。

    判定は次の順で候補を並べ、**errors を指定せず厳密に**デコードする。
    厳密にすると誤った候補は例外で弾かれるので、次の候補へ進める。

        1. Content-Type ヘッダの charset
        2. HTML 先頭 2048 バイトの meta charset
        3. UTF-8
        4. EUC-JP

    Args:
        response: `requests.Response`。`.content` と `.headers` を使う。

    Returns:
        復号した本文。全候補が失敗したときだけ、最後の手段として
        UTF-8 + `errors="replace"` で返す（このときは WARNING を出す）。
    """
    candidates: list[str] = []

    header_match = re.search(
        r"charset=([\w-]+)", response.headers.get("content-type", ""), re.I
    )
    if header_match:
        candidates.append(header_match.group(1))

    meta_match = re.search(
        rb"charset=[\"']?([\w-]+)", response.content[:_META_SCAN_BYTES], re.I
    )
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", "ignore"))

    candidates.extend(_FALLBACK_ENCODINGS)

    seen: set[str] = set()
    for raw in candidates:
        enc = (raw or "").strip().lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return response.content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    logger.warning("文字コードを判定できませんでした。置換文字入りで復号します: %s",
                   getattr(response, "url", "?"))
    return response.content.decode("utf-8", errors="replace")
