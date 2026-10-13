"""netkeirin の全予想家の商品成績を取り込む（2026-10-13 新設）。

## 何のためか

自分（二軸探偵・`SELF_YOSOKA_ID`）の成績が、netkeirin の予想家全体の中で何位かを
期間を変えて見る（`/keirin/stats` の「予想家比較」タブ）。

## 出どころ

レース単位の予想一覧 API。1回の取得でそのレースに予想を出した**全予想家**が並ぶ:

    GET https://keirin.netkeiba.com/yoso/api/api_get_goods_list_v4.html
        ?output=json&show_id=yoso_view&race_id=YYYYMMDDJJRR

- `race_id` は `wt_races.race_key` から `_` を除いたもの（場コードは同一体系）
- 返りは JSON 文字列 1つ＝一覧の HTML 断片。`<strong>N</strong>人が予想中` と
  `<li class="Selectable ...">` の数は一致する（2026-10-13 に 60 レースで確認・ページ分割なし）
- 2025-04 のレースでも取れることを確認済み

## 読み方（2026-10-13 の実測）

| 状態 | li の class | 払戻・収支の欄 |
|---|---|---|
| 払戻あり（ガミ含む） | `Selectable Hit` | あり（払戻 > 0） |
| 外れ | `Selectable NoHit` | あり（払戻 0） |
| **未確定** | `Selectable NoHit` | **欄そのものが無い** |

🔴 **未確定を 0 円にしない。** class だけ見ると未確定も `NoHit` なので、
   払戻欄の有無で判定する。未確定は `payout=NULL` で持ち、次の回で取り直す。

⚠️ 買い目（予想本文）は取らない。詳細ページに「転載はお控えください」とある。
   集計に要る金額だけを持つ。
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from src.database import get_connection

logger = logging.getLogger(__name__)

#: netkeirin 上の自分（二軸探偵）。プロフィールの URL `/yoso/profile/?id=732` から。
SELF_YOSOKA_ID = 732

API_URL = ("https://keirin.netkeiba.com/yoso/api/api_get_goods_list_v4.html"
           "?output=json&show_id=yoso_view&race_id={race_id}")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
#: 生データの保存先（gzip）。解析し直せるように取得したものは必ず残す。
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "netkeirin_rivals"

_LI_SPLIT = re.compile(r'(?=<li class="Selectable)')
_COUNT_RE = re.compile(r"<strong>(\d+)</strong>人")


def race_id_of(race_key: str) -> str:
    """`20261013_81_05` → `202610138105`。"""
    return race_key.replace("_", "")


def _int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("+", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def parse(race_key: str, html: str) -> dict[str, Any]:
    """一覧の HTML 断片 → `{"n_declared": 人数 or None, "goods": [1商品=1 dict]}`。

    各商品: goods_id / yosoka_id / yosoka_name / bet / payout / profit /
    published_at（'YYYY-MM-DD HH:MM:SS'）/ sortkey_raw。
    payout・profit が None なら未確定。
    """
    m = _COUNT_RE.search(html)
    goods = []
    for li in _LI_SPLIT.split(html)[1:]:
        gm = re.search(r'umai_v4_goods_state_(b\d+_(\d+))', li)
        if not gm:
            continue
        name = re.search(r'<p class="Name"><a[^>]*>(.*?)</a>', li, re.S)
        buy = re.search(r"<th>購入金額</th>\s*<td>([\d,]+)円", li)
        pub = re.search(r"<th>公開日時</th>\s*<td>([^<]*)</td>", li)
        bal = dict(re.findall(r"<dt>(払戻|収支)</dt>\s*<dd>\s*<em>([^<]*)</em>", li))
        sk = re.search(r'data-sortkey="([^"]*)"', li)
        published = None
        if pub:
            try:
                published = datetime.strptime(pub.group(1).strip(), "%Y/%m/%d %H:%M") \
                    .strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                published = None
        payout = _int(bal.get("払戻"))
        profit = _int(bal.get("収支"))
        if payout is None or profit is None:     # 片方だけ読めたら未確定扱いに揃える
            payout = profit = None
        goods.append({
            "goods_id": gm.group(1),
            "race_key": race_key,
            "yosoka_id": int(gm.group(2)),
            "yosoka_name": re.sub(r"<[^>]+>", "", name.group(1)).strip() if name else None,
            "bet": _int(buy.group(1)) if buy else None,
            "payout": payout,
            "profit": profit,
            "published_at": published,
            "sortkey_raw": sk.group(1) if sk else None,
        })
    return {"n_declared": int(m.group(1)) if m else None, "goods": goods}


#: 応答が JSON でなかったときの取り直し回数と待ち秒（試行ごとに倍）
FETCH_RETRIES = 4
FETCH_BACKOFF = 2.0


def _fetch_once(race_key: str) -> str:
    req = urllib.request.Request(API_URL.format(race_id=race_id_of(race_key)),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    html = json.loads(body)
    if not isinstance(html, str):
        raise ValueError(f"想定外の応答形式: {type(html).__name__}")
    return html


def fetch(race_key: str, *, retries: int = FETCH_RETRIES, backoff: float = FETCH_BACKOFF,
          _once=_fetch_once, _sleep=time.sleep) -> str:
    """API から一覧の HTML 断片を取り、生データを gzip で保存して返す。

    🔴 この API は**約4回に1回、JSON の代わりに 2,400 バイトの HTML ページを
    status 200 で返す**（2026-10-14 VPS 実測。同じレースを取り直せば正しく返る）。
    取り直さないと1日 60 レース中 19 レースが落ちた。
    """
    for i in range(retries + 1):
        try:
            html = _once(race_key)
            break
        except (ValueError, OSError):
            if i == retries:
                raise
            _sleep(backoff * (2 ** i))
    d = RAW_DIR / race_key[:8]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{race_key}.html.gz").write_bytes(gzip.compress(html.encode("utf-8")))
    return html


def settled_races(race_keys: Iterable[str]) -> set[str]:
    """取り込み済みで、未確定の商品が1つも無いレース（取り直さなくてよい）。"""
    ks = sorted({str(k) for k in race_keys if k})
    done: set[str] = set()
    if not ks:
        return done
    with get_connection() as c:
        for i in range(0, len(ks), 900):
            ch = ks[i:i + 900]
            q = ("SELECT race_key FROM netkeirin_rival_goods "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "GROUP BY race_key HAVING SUM(CASE WHEN payout IS NULL THEN 1 ELSE 0 END) = 0")
            done |= {str(r[0]) for r in c.execute(q, ch).fetchall()}
    return done


def races_on(dates: Iterable[str]) -> list[tuple[str, str]]:
    """[(race_key, race_date 'YYYY-MM-DD')]。`wt_races` に載っているレース。"""
    out: list[tuple[str, str]] = []
    with get_connection() as c:
        for d in dates:
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
            for (rk,) in c.execute(
                    "SELECT race_key FROM wt_races WHERE race_date = ? ORDER BY race_key",
                    (iso,)).fetchall():
                out.append((str(rk), iso))
    return out


def save(race_date: str, goods: list[dict[str, Any]]) -> None:
    if not goods:
        return
    with get_connection() as c:
        c.executemany(
            "INSERT OR REPLACE INTO netkeirin_rival_goods "
            "(goods_id, race_key, race_date, yosoka_id, yosoka_name, bet, payout, profit, "
            "published_at, sortkey_raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(g["goods_id"], g["race_key"], race_date, g["yosoka_id"], g["yosoka_name"],
              g["bet"], g["payout"], g["profit"], g["published_at"], g["sortkey_raw"])
             for g in goods])
        c.commit()


def collect(races: list[tuple[str, str]], *, fetcher: Callable[[str], str] | None = None,
            interval: float = 0.8, refetch_settled: bool = False) -> dict[str, int]:
    """レースごとに取り込む。取り込み済みで確定しきったレースは取りに行かない。

    Returns: {"target", "skipped", "saved", "goods", "unsettled", "mismatch", "failed"}
      - unsettled: 未確定の商品を含んだレース（次の回で取り直す）
      - mismatch: 「N人が予想中」と取れた商品数が合わない（ページ分割など。要調査）
    """
    fetcher = fetcher or fetch
    done = set() if refetch_settled else settled_races(rk for rk, _ in races)
    stats = {"target": len(races), "skipped": 0, "saved": 0, "goods": 0,
             "unsettled": 0, "mismatch": 0, "failed": 0}
    for rk, iso in races:
        if rk in done:
            stats["skipped"] += 1
            continue
        try:
            got = parse(rk, fetcher(rk))
        except Exception as e:                      # noqa: BLE001  1件の失敗で止めない
            stats["failed"] += 1
            logger.warning(f"[rivals] 取得失敗 {rk}: {e}")
            continue
        finally:
            if fetcher is fetch and interval > 0:
                time.sleep(interval)
        goods = got["goods"]
        if got["n_declared"] is not None and got["n_declared"] != len(goods):
            stats["mismatch"] += 1
            logger.warning(f"[rivals] 人数と商品数が合わない {rk}: "
                           f"{got['n_declared']}人 ↔ {len(goods)}件")
        if any(g["payout"] is None for g in goods):
            stats["unsettled"] += 1
        save(iso, goods)
        stats["saved"] += 1
        stats["goods"] += len(goods)
    return stats
