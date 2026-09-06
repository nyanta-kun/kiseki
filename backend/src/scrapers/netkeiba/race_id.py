"""netkeiba のレース ID と各ページの URL。

## race_id の作り

    中央: YYYY + netkeiba場コード(2) + 開催回(2) + 日目(2) + レース番号(2)
    地方: YYYY + netkeiba場コード(2) + MMDD          + レース番号(2)

中央だけ「開催回・日目」が要る。移設元はこれを `sekito.kaisai` から引いていたが、
**kiseki は `keiba.races.jravan_race_id` から取れる**（2026-09-06 実測）:

    jravan_race_id は 16 文字
      YYYY(4) + MMDD(4) + 場(2) + 開催回(2) + 日目(2) + R(2)
      例) '2026090601020601' = 2026-09-06 札幌 2回6日目 1R

    直近 60 日の 54 組合せで `sekito.kaisai` と完全一致・不一致 0。

これで `sekito.kaisai` への依存が消える。netkeiba を移し終えれば、
供給用の同期ジョブ（sekito id=76 / 88）もまとめて不要になる。
"""

from __future__ import annotations

from datetime import date

# jravan_race_id(16文字) の中で開催回・日目が占める位置（0 始まり）
_KAI_SLICE = slice(10, 12)
_DAY_SLICE = slice(12, 14)

JRA_HOST = "https://race.netkeiba.com"
NAR_HOST = "https://nar.netkeiba.com"


def jra_race_id(target_date: date, netkeiba_id: str, race_no: int, jravan_race_id: str) -> str:
    """中央の netkeiba race_id を作る。

    Args:
        target_date: レース日。
        netkeiba_id: netkeiba の場コード（中央は JRA 課コードと同じ 2 桁）。
        race_no: レース番号。
        jravan_race_id: `keiba.races.jravan_race_id`（16 文字）。

    Raises:
        ValueError: `jravan_race_id` の長さが足りないとき。開催回・日目が
            取れないまま組み立てると、存在しない race_id で叩き続けることになる。
    """
    if not jravan_race_id or len(jravan_race_id) < 14:
        raise ValueError(
            f"jravan_race_id から開催回・日目を取り出せません: {jravan_race_id!r}"
        )
    kai = jravan_race_id[_KAI_SLICE]
    day = jravan_race_id[_DAY_SLICE]
    return f"{target_date:%Y}{netkeiba_id}{kai}{day}{race_no:02d}"


def nar_race_id(target_date: date, netkeiba_id: str, race_no: int) -> str:
    """地方の netkeiba race_id を作る。開催回・日目は使わない。"""
    return f"{target_date:%Y}{netkeiba_id}{target_date:%m%d}{race_no:02d}"


def time_index_url(race_id: str, *, is_jra: bool) -> str:
    """タイム指数ページ（speed.html）。"""
    host = JRA_HOST if is_jra else NAR_HOST
    return f"{host}/race/speed.html?race_id={race_id}&type=shutuba&mode=default#d"


def training_url(race_id: str) -> str:
    """調教ページ（oikiri.html）。**中央のみ**。地方には無い。"""
    return f"{JRA_HOST}/race/oikiri.html?race_id={race_id}&rf=race_submenu"


def paddock_url(race_id: str) -> str:
    """パドックページ（paddock.html）。**中央のみ**。"""
    return f"{JRA_HOST}/race/paddock.html?race_id={race_id}&rf=shutuba_submenu"


def blood_url(race_id: str, *, is_jra: bool) -> str:
    """血統・バイアスページ（bias.html）。

    ⚠️ 2026-09-06 に日次の取得対象から外した（JRA-VAN が上位互換で、実測の一致率
    99.6%）。**埋め戻し専用**に残してある。日次ジョブでは叩かないこと。
    """
    host = JRA_HOST if is_jra else NAR_HOST
    return f"{host}/race/bias.html?race_id={race_id}&rf=shutuba_submenu"


def data_analysis_url(race_id: str, *, is_jra: bool) -> str:
    """データ分析ページ（data_top.html）。sekito のレース詳細 UI が使う。"""
    host = JRA_HOST if is_jra else NAR_HOST
    return f"{host}/race/data_top.html?race_id={race_id}"
