#!/usr/bin/env python3
"""ページ表示ごとに「HTML → 最初の API」の秒数を nginx のアクセスログから出す。

iPhone の「間を空けて戻ると表示が遅い」の効果測定用（2026-10-15 の SW 撤去・
#507 と同じ指標）。サーバ側の処理時間ではなく**端末の中で止まっていた時間**を見る:
HTML が届いてからアプリが最初のデータを取りに行くまでの間隔。

使い方（VPS のログを UA で端末に絞ってから標準入力へ流す）::

    ssh sekito 'sudo sh -c "zcat -f /var/log/nginx/access.log.2.gz /var/log/nginx/access.log.1 /var/log/nginx/access.log"' \\
      | grep -F 'iPhone OS 18_7' | python3 scripts/dev/measure_resume_latency.py

⚠️ ログ時刻は**応答完了時刻**で秒単位。1〜2 秒の差は読まないこと。

撤去前（2026-10-12〜15・同一端末）::

    sw=あり  n=42  中央値 11秒（最大 29秒）
    sw=なし  n=31  中央値  1秒（最大 4秒）

撤去が効いていれば ``/sw.js`` を取る回が（初回の自滅を除いて）消え、
間を空けた回（idle が大きい行）でも数秒に収まる。
"""

from __future__ import annotations

import re
import statistics
import sys
from datetime import datetime

LINE = re.compile(r'\[(\S+) [+-]\d{4}\] "GET (\S+)')
NON_DOC_PREFIX = ("/api/", "/_next", "/sw.js", "/workbox", "/favicon", "/images", "/manifest")
WINDOW_SECONDS = 60


def is_document(path: str) -> bool:
    """HTML のページ要求か（API・静的アセット・RSC 先読みを除く）。"""
    return not path.startswith(NON_DOC_PREFIX) and "_rsc=" not in path


def main() -> None:
    """標準入力のアクセスログを集計して表と要約を出す。"""
    rows: list[tuple[datetime, str]] = []
    for line in sys.stdin:
        m = LINE.search(line)
        if m:
            rows.append((datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S"), m.group(2)))
    rows.sort()

    results: list[tuple[float, bool]] = []
    print(f"{'時刻':<15} {'直前の無操作(秒)':>14} {'sw.js':>6} {'再読込':>6} {'→API(秒)':>9}  path")
    for i, (t, path) in enumerate(rows):
        if not is_document(path):
            continue
        idle = (t - rows[i - 1][0]).total_seconds() if i else float("nan")
        api = None
        sw = False
        reload = 0
        for t2, p2 in rows[i + 1 :]:
            d = (t2 - t).total_seconds()
            if d > WINDOW_SECONDS:
                break
            if p2.startswith("/sw.js"):
                sw = True
            if p2 == path:
                reload += 1
            if p2.startswith("/api/"):
                api = d
                break
        print(
            f"{t:%m-%d %H:%M:%S} {idle:>14.0f} {'あり' if sw else '-':>6} {reload:>6} "
            f"{'-' if api is None else f'{api:.0f}':>9}  {path[:50]}"
        )
        if api is not None:
            results.append((api, sw))

    print()
    for flag, label in ((True, "sw=あり"), (False, "sw=なし")):
        xs = [a for a, s in results if s == flag]
        if xs:
            print(f"{label}  n={len(xs)}  中央値 {statistics.median(xs):.0f}秒（最大 {max(xs):.0f}秒）")
    n_sw = sum(1 for p in (p for _, p in rows) if p.startswith("/sw.js"))
    print(f"/sw.js の要求 {n_sw} 件")


if __name__ == "__main__":
    main()
