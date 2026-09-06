#!/usr/bin/env python3
"""`sekito.netkeiba.horse_name` の文字化けを JRA-VAN / UmaConn 由来の名前で直す。

## 何が起きたか（2026-09-06 調査）

`bin/scrape/netkeiba-paddock` が netkeiba のページを EUC-JP 決め打ちで復号していた。
paddock.html は **UTF-8** なので必ず化ける。しかもその UPSERT は
`horse_name = EXCLUDED.horse_name` なので、朝の `netkeiba-index` が書いた
**正しい馬名を上書きしていた**。

    2026-09-06 実測（当日の中央）
      パドック取得あり  192 行 → 192 行が化け（100%）
      パドック取得なし  299 行 → 0 行（0%）

化けの全量（U+FFFD を含む行）:

    馬名の化け  22,707 行  2026-05-06 〜 2026-09-06   ← requests 移行以降
      中央  4,462 行
      地方 18,245 行
    馬名が空       711 行  2026-06-20 〜 2026-09-05
      中央    614 行（調教由来）
      地方     97 行（血統由来）

## 化けだけでなく「空」も直す

調教と血統の取得は、**馬名を持たないまま行を作る**ことがある（それらのページに
馬名が無い形で解析が通ったとき）。2026-09-06 実測で 711 行（調教由来 614 /
血統由来 97）。化け文字を含まないので「化けの検出」では拾えないが、
`sekito.netkeiba` に馬名の無い行があってよい理由は無いので同時に埋める。

## なぜ逆変換ではなく別ソースから直すのか

`errors="replace"` は復号できないバイト列を **U+FFFD 1 文字に潰す**。
潰した時点で元のバイトは失われているので、**文字列だけを見て元に戻すことは
原理的にできない**。

幸い馬名は netkeiba 固有の情報ではない。JRA-VAN（`keiba.horses`）と
UmaConn（`chihou.horses`）が正本を持っており、2026-09-06 の実測で
**22,707 行すべてに対応が見つかる**（中央 4,462/4,462・地方 18,245/18,245）。
ネットワークを使わず、権威あるソースから直せる。

⚠️ パドックの寸評（`p_comment` 391 行 / 73 レース）は netkeiba 固有なので
   この方法では直せない。paddock スクレイパを kiseki へ移した後に、
   対象レースを再取得して上書きする。

## 使い方

    # 何件直るか見るだけ（既定・書き込まない）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/repair_netkeiba_horse_names.py

    # 実際に直す
    ... scripts/repair_netkeiba_horse_names.py --apply

    # 期間を絞る
    ... scripts/repair_netkeiba_horse_names.py --apply --since 2026-09-01

終了コード: 0（対象が 0 件でも 0）。直せなかった行が残ったときだけ 1。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text  # noqa: E402

from src.db.session import SyncSessionLocal  # noqa: E402

# `errors="replace"` が残す置換文字。これを含む行だけを対象にする。
# 「カタカナ以外を含む行」のような判定にすると、外国馬名など正当な行まで拾う。
REPLACEMENT_CHAR = "�"

# 中央: keiba.races → race_entries → horses
_JRA_SQL = """
UPDATE sekito.netkeiba n
   SET horse_name = h.name
  FROM keiba.racecourse_map m,
       keiba.races r,
       keiba.race_entries e,
       keiba.horses h
 WHERE m.code = n.course_code
   AND m.jra_code IS NOT NULL
   AND r.date = to_char(n.date, 'YYYYMMDD')
   AND r.course = m.jra_code
   AND r.race_number = n.race_no
   AND e.race_id = r.id
   AND e.horse_number = n.horse_no
   AND h.id = e.horse_id
   AND h.name IS NOT NULL
   AND h.name <> ''
   AND (position(:bad in n.horse_name) > 0 OR coalesce(n.horse_name, '') = '')
   AND n.date >= :since
"""

# 地方: chihou.races → race_entries → horses（場コードは netkeiba 体系）
_NAR_SQL = """
UPDATE sekito.netkeiba n
   SET horse_name = h.name
  FROM keiba.racecourse_map m,
       chihou.races r,
       chihou.race_entries e,
       chihou.horses h
 WHERE m.code = n.course_code
   AND m.jra_code IS NULL
   AND r.date = to_char(n.date, 'YYYYMMDD')
   AND r.course = m.netkeiba_id
   AND r.race_number = n.race_no
   AND e.race_id = r.id
   AND e.horse_number = n.horse_no
   AND h.id = e.horse_id
   AND h.name IS NOT NULL
   AND h.name <> ''
   AND (position(:bad in n.horse_name) > 0 OR coalesce(n.horse_name, '') = '')
   AND n.date >= :since
"""

_COUNT_SQL = """
SELECT count(*) FILTER (WHERE position(:bad in horse_name) > 0)  AS broken,
       count(*) FILTER (WHERE coalesce(horse_name, '') = '')     AS empty
  FROM sekito.netkeiba
 WHERE (position(:bad in horse_name) > 0 OR coalesce(horse_name, '') = '')
   AND date >= :since
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="netkeiba の馬名の文字化けを直す")
    parser.add_argument("--apply", action="store_true",
                        help="実際に UPDATE する（既定は件数を数えるだけ）")
    parser.add_argument("--since", default="2026-05-01",
                        help="この日以降を対象にする (YYYY-MM-DD)。既定は requests 移行の直前")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    params = {"bad": REPLACEMENT_CHAR, "since": args.since}

    with SyncSessionLocal() as session:
        before = session.execute(text(_COUNT_SQL), params).fetchone()
        logging.info("直す対象: 化け %d 行 / 空 %d 行（%s 以降）",
                     before[0], before[1], args.since)

        if not args.apply:
            logging.info("--apply が無いので書き込みません")
            return 0

        jra = session.execute(text(_JRA_SQL), params).rowcount
        nar = session.execute(text(_NAR_SQL), params).rowcount
        session.commit()
        logging.info("復元: 中央 %d 行 / 地方 %d 行", jra, nar)

        after = session.execute(text(_COUNT_SQL), params).fetchone()
        remaining = after[0] + after[1]
        if remaining:
            # 出走取消などで出馬表に載っていない馬。再取得でしか直せない。
            logging.warning("直せなかった行が残っています: 化け %d / 空 %d",
                            after[0], after[1])
            return 1
        logging.info("残り: 0 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
