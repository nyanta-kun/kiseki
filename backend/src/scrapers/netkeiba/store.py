"""`sekito.netkeiba` への書き込み。

## 🔴 「取った項目だけ」を更新する

1 行（date, course_code, race_no, horse_no）に、**別々のジョブが別々の列を書く**:

    netkeiba-index    idx_*（タイム指数）, training（調教）
    netkeiba-paddock  p_rank / p_comment / p_type
    埋め戻し           sire / broodmare_sire

素直に全列を UPSERT すると、タイム指数を取り直しただけでパドックの列が NULL で
潰れる。移設元も「値があるカラムだけ更新する」形にしていたので、同じ性質を保つ。
ここでは**呼び出し側が更新する列を明示する**形にして、その判断をコードに書き出す。

## horse_name を上書きしてよいのは正しく取れたときだけ

2026-09-06 の障害はここが原因だった。paddock 側が文字コードを間違えたまま
`horse_name = EXCLUDED.horse_name` で上書きし、朝に正しく入った馬名を壊していた
（[[paddock-mojibake]]）。**置換文字を含む値は書かない**ガードを入れてある。
"""

from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# `errors="replace"` が残す置換文字。これを含む値は壊れているので書かない。
REPLACEMENT_CHAR = "�"

# 主キー。
_KEY_COLUMNS = ("date", "course_code", "race_no", "horse_no")

# 取得対象ごとに書き込む列。ここに無い列は触らない。
TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "time_index": ("horse_name", "idx_max", "idx_ave", "idx_distance",
                   "idx_course", "idx_third", "idx_second", "idx_last",
                   "is_time_index"),
    "training": ("training", "is_training"),
    # 🔴 paddock に horse_name を**入れない**。
    #    2026-09-06 の障害は、パドックが `horse_name = EXCLUDED.horse_name` で
    #    朝に正しく入った馬名を化けた値で上書きしていたこと。馬名の持ち主は
    #    time_index であって paddock ではない（[[paddock-mojibake]]）。
    "paddock": ("p_rank", "p_comment", "p_type", "is_paddock"),
    "blood": ("sire", "broodmare_sire", "broodmare_sire_color", "is_blood"),
}


def _is_broken(value: object) -> bool:
    """置換文字を含む＝復号に失敗した値か。"""
    return isinstance(value, str) and REPLACEMENT_CHAR in value


def upsert(session: Session, target: str, race_date: date, course_code: str,
           race_no: int, records: list[dict]) -> int:
    """`sekito.netkeiba` へ UPSERT する。

    Args:
        session: 同期 DB セッション。
        target: `TARGET_COLUMNS` のキー。これで更新する列が決まる。
        race_date: レース日。
        course_code: sekito 4 文字コード。
        race_no: レース番号。
        records: `horse_no` を必ず含むレコード。`TARGET_COLUMNS[target]` に
            無いキーは無視する。

    Returns:
        書き込んだ行数。
    """
    columns = TARGET_COLUMNS.get(target)
    if columns is None:
        raise ValueError(f"未知の取得対象: {target}")

    rows: list[dict] = []
    for record in records:
        horse_no = record.get("horse_no")
        if horse_no is None:
            continue
        row = {"date": race_date, "course_code": course_code,
               "race_no": race_no, "horse_no": int(horse_no)}
        for col in columns:
            value = record.get(col)
            if _is_broken(value):
                # 壊れた値で正しい値を上書きしない。2026-09-06 の障害の再発防止。
                logger.warning("復号に失敗した値は書きません: %s %s %sR 馬番%s %s",
                               race_date, course_code, race_no, horse_no, col)
                continue
            row[col] = value
        rows.append(row)

    if not rows:
        return 0

    # 行ごとに書く列が違いうる（壊れた値を落とした場合）ので、列の組合せでまとめる。
    written = 0
    by_shape: dict[tuple[str, ...], list[dict]] = {}
    for row in rows:
        shape = tuple(k for k in row if k not in _KEY_COLUMNS)
        by_shape.setdefault(shape, []).append(row)

    for shape, group in by_shape.items():
        all_cols = list(_KEY_COLUMNS) + list(shape)
        placeholders = ", ".join(f":{c}" for c in all_cols)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in shape) or "horse_no = EXCLUDED.horse_no"
        session.execute(
            text(
                f"INSERT INTO sekito.netkeiba ({', '.join(all_cols)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT (date, course_code, race_no, horse_no) "
                f"DO UPDATE SET {updates}"
            ),
            group,
        )
        written += len(group)

    session.commit()
    return written


_DATA_ANALYSIS_UPSERT = text(
    """
    INSERT INTO sekito.netkeiba_data_analysis
        (date, course_code, race_no, top_horse_1, top_horse_2, top_horse_3,
         analysis_data, scraped_at)
    VALUES (:date, :course_code, :race_no, :top1, :top2, :top3,
            CAST(:analysis AS jsonb), NOW())
    ON CONFLICT (date, course_code, race_no)
    DO UPDATE SET top_horse_1 = EXCLUDED.top_horse_1,
                  top_horse_2 = EXCLUDED.top_horse_2,
                  top_horse_3 = EXCLUDED.top_horse_3,
                  analysis_data = EXCLUDED.analysis_data,
                  scraped_at = NOW()
    """
)


def upsert_data_analysis(session: Session, race_date: date, course_code: str,
                         race_no: int, parsed: dict) -> None:
    """`sekito.netkeiba_data_analysis` へ UPSERT する。

    こちらはレース単位の 1 行なので、`sekito.netkeiba` のような
    「取った列だけ更新する」配慮は要らない（書くのはこのジョブだけ）。
    """
    top = list(parsed.get("top_horses") or [])
    top += [None] * (3 - len(top))
    session.execute(
        _DATA_ANALYSIS_UPSERT,
        {
            "date": race_date, "course_code": course_code, "race_no": race_no,
            "top1": top[0], "top2": top[1], "top3": top[2],
            "analysis": json.dumps(parsed.get("analysis_data") or {}, ensure_ascii=False),
        },
    )
    session.commit()
