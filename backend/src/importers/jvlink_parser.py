"""JV-Link レコードパーサー

JRA-VAN JVDF v4.9 仕様に基づき、固定長テキストレコードからフィールドを抽出する。

フィールド位置はJVDF仕様書の1-indexed バイト位置。
JVRead が返す文字列は SJIS バイトが Latin-1 として格納されているため、
1 Python文字 = 1 SJISバイト となりバイト位置と文字インデックスが一致する。
全角(Kanji)フィールドのデコードには _decode() を使用すること。

対応レコード種別:
  RA - レース詳細 (1272バイト)
  SE - 馬毎レース情報 (555バイト)
  O1 - 単勝・複勝・枠連オッズ (962バイト)
  O2-O6 - 各種オッズ
  AV - 出走取消・競走除外 (78バイト)
  JC - 騎手変更 (161バイト)

共通ヘッダー構造 (RA/SE/AV/JC 共通, pos 1-27):
  1- 2: レコード種別ID (2バイト)
  3   : データ区分 (1バイト)
  4-11: データ作成年月日 YYYYMMDD (8バイト)
 12-15: 開催年 YYYY (4バイト)
 16-19: 開催月日 MMDD (4バイト)  ← データ作成日の月日ではなく開催月日
 20-21: 競馬場コード (2バイト)
 22-23: 開催回 (2バイト)
 24-25: 開催日目 (2バイト)
 26-27: レース番号 (2バイト)

レースID形式: YYYYMMDD + 競馬場コード + 開催回 + 開催日目 + レース番号
  例: "2026032205010105" (16文字)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# 競馬場コード → 競馬場名
# -------------------------------------------------------------------
COURSE_NAMES: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

# -------------------------------------------------------------------
# トラックコード (コード表2009) → (芝ダ, 方向)
# -------------------------------------------------------------------
TRACK_CODE_MAP: dict[str, tuple[str, str | None]] = {
    "10": ("芝", None),   "11": ("芝", "右"),   "12": ("芝", "右外"),
    "13": ("芝", "右"),   "14": ("芝", "右"),   "17": ("芝", "直"),
    "18": ("芝", "左"),   "19": ("芝", "左外"), "20": ("芝→ダ", None),
    "22": ("ダ", "右"),   "23": ("ダ", "左"),   "24": ("ダ", "右外"),
    "25": ("ダ", "左"),   "26": ("ダ", "直"),   "27": ("サンド", "右"),
    "28": ("サンド", "左"),
}

# グレードコード (コード表2003)
GRADE_MAP: dict[str, str] = {
    "A": "G1", "B": "G2", "C": "G3",
    "D": "J.G1", "E": "J.G2", "F": "J.G3",
    "G": "OP特別", "H": "重賞", "L": "Listed",
    " ": "一般", "": "",
}

# 馬場状態コード (コード表2010)
CONDITION_MAP: dict[str, str] = {"1": "良", "2": "稍", "3": "重", "4": "不"}

# 天候コード (コード表2011)
WEATHER_MAP: dict[str, str] = {
    "1": "晴", "2": "曇", "3": "雨", "4": "小雨", "5": "雪", "6": "小雪",
}

# 性別コード (コード表2202)
SEX_MAP: dict[str, str] = {"1": "牡", "2": "牝", "3": "セ"}


# -------------------------------------------------------------------
# ユーティリティ
# -------------------------------------------------------------------

def _s(data: str, start: int, end: int) -> str:
    """1-indexed バイト位置から半角フィールドを抽出する（末尾空白除去）。

    JVRead が返す文字列は1バイト=1文字で格納されるため、
    バイト位置とインデックスが一致する。

    Args:
        data: レコード文字列
        start: JVDF仕様の開始バイト位置 (1-indexed)
        end: JVDF仕様の終了バイト位置 (1-indexed, 含む)

    Returns:
        トリムされた文字列
    """
    return data[start - 1:end].strip()


def _i(data: str, start: int, end: int) -> int | None:
    """1-indexed バイト位置から整数を抽出する（全ゼロまたは空は None）。"""
    raw = data[start - 1:end].strip()
    if not raw or not raw.isdigit():
        return None
    v = int(raw)
    return v if v != 0 else None


def _decode(data: str, start: int, end: int) -> str:
    """全角(Kanji)フィールドを SJIS → Unicode へ変換して返す。

    JVRead は SJIS バイトを Latin-1 として格納するため、
    .encode('latin-1') で元の SJIS バイト列を復元してから CP932 でデコードする。

    Args:
        data: レコード文字列
        start: 開始バイト位置 (1-indexed)
        end: 終了バイト位置 (1-indexed)

    Returns:
        デコードされたUnicode文字列（末尾空白除去済み）
    """
    raw = data[start - 1:end]
    try:
        return raw.encode("latin-1").decode("cp932").strip()
    except (UnicodeDecodeError, UnicodeEncodeError):
        return raw.strip()


def _race_id(year: str, month_day: str, course: str, kai: str, day: str, rnum: str) -> str:
    """JRA-VAN レースIDを生成する。

    形式: YYYYMMDD + 競馬場コード(2) + 開催回(2) + 開催日目(2) + レース番号(2) = 16文字
    例: "2026032205010105"
    """
    return f"{year}{month_day}{course}{kai}{day}{rnum}"


def _parse_msst_time(raw: str) -> int | None:
    """走破タイム MSST形式を0.1秒単位の整数に変換する。

    JVDF SE レコード pos 339-342: M=分(1桁), SS=秒(2桁), T=0.1秒(1桁)
    例: "1345" → 1分34秒5 → 1*600 + 34*10 + 5 = 945 (= 94.5秒)
         "0580" → 0分58秒0 → 580 (= 58.0秒)

    Args:
        raw: 4文字の時刻文字列

    Returns:
        0.1秒単位の整数、無効の場合は None
    """
    if not raw or len(raw) < 4 or not raw.isdigit() or raw == "0000":
        return None
    m = int(raw[0])
    ss = int(raw[1:3])
    t = int(raw[3])
    return m * 600 + ss * 10 + t


def _parse_sst_time(raw: str) -> int | None:
    """後3ハロンタイム SST形式を0.1秒単位の整数として返す。

    JVDF SE レコード pos 391-393: SS=秒(2桁), T=0.1秒(1桁)
    例: "336" → 33.6秒 = 336 (そのまま0.1秒単位)

    Args:
        raw: 3文字の時刻文字列

    Returns:
        0.1秒単位の整数、無効の場合は None
    """
    if not raw or len(raw) < 3 or not raw.isdigit() or raw == "000":
        return None
    return int(raw)


# -------------------------------------------------------------------
# 共通ヘッダー抽出
# -------------------------------------------------------------------

def _parse_common_header(data: str) -> dict[str, str] | None:
    """RA/SE/AV/JC 共通ヘッダーを抽出する。

    Returns:
        {rec_id, data_type, year, month_day, course_code, kai, day, race_num,
         jravan_race_id, race_date}
        または None（フォーマット不正）
    """
    if len(data) < 27:
        return None

    year = _s(data, 12, 15)
    month_day = _s(data, 16, 19)
    course_code = _s(data, 20, 21)
    kai = _s(data, 22, 23)
    day = _s(data, 24, 25)
    race_num = _s(data, 26, 27)

    return {
        "rec_id": _s(data, 1, 2),
        "data_type": _s(data, 3, 3),
        "created_date": _s(data, 4, 11),
        "year": year,
        "month_day": month_day,
        "course_code": course_code,
        "kai": kai,
        "day": day,
        "race_num": race_num,
        "jravan_race_id": _race_id(year, month_day, course_code, kai, day, race_num),
        # 開催年 + 開催月日 = 実際の開催日
        "race_date": year + month_day,
    }


# -------------------------------------------------------------------
# RA レコード（レース詳細, 1272バイト）
# -------------------------------------------------------------------

def parse_ra(data: str) -> dict[str, Any] | None:
    """RAレコード（レース詳細）をパースする。

    JVDF v4.9 フィールド位置（1-indexed バイト）:
      1- 2: "RA"
      3   : データ区分 (0:削除, 1:出走馬名表, 2:出馬表, 3-7:速報, 7:成績, 9:中止)
      4-11: データ作成年月日 YYYYMMDD
     12-15: 開催年 YYYY
     16-19: 開催月日 MMDD
     20-21: 競馬場コード
     22-23: 開催回
     24-25: 開催日目
     26-27: レース番号
     33-92: 競走名本題 (60バイト = 全角30文字)
    615   : グレードコード
    617-618: 競走種別コード
    698-701: 距離 (メートル, 4桁)
    706-707: トラックコード (コード表2009)
    882-883: 登録頭数
    884-885: 出走頭数
    888   : 天候コード (コード表2011)
    889   : 芝馬場状態コード (コード表2010)
    890   : ダート馬場状態コード (コード表2010)
    """
    if len(data) < 890:
        logger.warning(f"RA record too short: {len(data)} bytes")
        return None

    try:
        header = _parse_common_header(data)
        if not header or header["rec_id"] != "RA":
            return None
        if header["data_type"] == "0":  # 削除レコード
            return None

        # トラックコード → 芝ダ, 方向
        track_code = _s(data, 706, 707)
        if track_code.startswith("5"):  # 障害
            surface, direction = "障", None
        else:
            surface, direction = TRACK_CODE_MAP.get(track_code, (track_code, None))

        # 馬場状態: 芝→芝状態, ダ→ダ状態
        if surface == "芝":
            condition = CONDITION_MAP.get(_s(data, 889, 889))
        else:
            condition = CONDITION_MAP.get(_s(data, 890, 890))

        grade_code = _s(data, 615, 615)

        return {
            "jravan_race_id": header["jravan_race_id"],
            "race_date": header["race_date"],         # 開催日 YYYYMMDD
            "created_date": header["created_date"],   # データ作成日 YYYYMMDD
            "year": header["year"],
            "month_day": header["month_day"],
            "course": header["course_code"],
            "course_name": COURSE_NAMES.get(header["course_code"], header["course_code"]),
            "kai": header["kai"],
            "day": header["day"],
            "race_number": int(header["race_num"]) if header["race_num"].isdigit() else 0,
            "race_name": _decode(data, 33, 92),
            "grade": GRADE_MAP.get(grade_code, grade_code),
            "surface": surface,
            "direction": direction,
            "track_code": track_code,
            "distance": _i(data, 698, 701),
            "condition": condition,
            "weather": WEATHER_MAP.get(_s(data, 888, 888)),
            "head_count": _i(data, 884, 885),  # 出走頭数 (取消除外後)
            "data_type": header["data_type"],
        }
    except Exception as e:
        logger.error(f"RA parse error: {e} | data[:30]={data[:30]!r}")
        return None


# -------------------------------------------------------------------
# SE レコード（馬毎レース情報, 555バイト）
# -------------------------------------------------------------------

def parse_se(data: str) -> dict[str, Any] | None:
    """SEレコード（馬毎レース情報）をパースする。

    JVDF v4.9 フィールド位置（1-indexed バイト）:
      1- 2: "SE"
      3   : データ区分
      4-11: データ作成年月日
     12-15: 開催年
     16-19: 開催月日
     20-21: 競馬場コード
     22-23: 開催回
     24-25: 開催日目
     26-27: レース番号
      28  : 枠番 (1バイト!)
     29-30: 馬番 (2バイト)
     31-40: 血統登録番号 (10バイト)
     41-76: 馬名 (36バイト = 全角18文字)
      79  : 性別コード
     83-84: 馬齢
     86-90: 調教師コード (5バイト)
     91-98: 調教師名略称 (8バイト = 全角4文字)
    289-291: 負担重量 (3バイト, 0.1kg単位, "560"=56.0kg)
    297-301: 騎手コード (5バイト)
    307-314: 騎手名略称 (8バイト = 全角4文字)
    325-327: 馬体重 (3バイト, kg, "999"=計量不能, "000"=出走取消)
     328  : 増減符号 ("+" / "-" / " ")
    329-331: 増減差 (3バイト, kg)
     332  : 異常区分コード (コード表2101)
    335-336: 確定着順
    339-342: 走破タイム (4バイト, MSST形式: "1345"=1分34秒5=94.5秒)
    352-353: 1コーナー通過順位
    354-355: 2コーナー通過順位
    356-357: 3コーナー通過順位
    358-359: 4コーナー通過順位
    391-393: 後3ハロンタイム (3バイト, SST形式: "336"=33.6秒)
    """
    if len(data) < 393:
        logger.warning(f"SE record too short: {len(data)} bytes")
        return None

    try:
        header = _parse_common_header(data)
        if not header or header["rec_id"] != "SE":
            return None
        if header["data_type"] == "0":
            return None

        # 負担重量: "560" → 56.0 kg
        weight_raw = _s(data, 289, 291)
        weight_carried = round(int(weight_raw) / 10, 1) if weight_raw.isdigit() and int(weight_raw) > 0 else None

        # 馬体重: "460" → 460 kg, "999"/"000" → None
        hw_raw = _s(data, 325, 327)
        horse_weight = int(hw_raw) if hw_raw.isdigit() and int(hw_raw) not in (0, 999) else None

        # 馬体重増減: 符号(pos328) + 差(pos329-331)
        wc_sign = data[327]  # pos 328, 0-indexed = 327
        wc_val_raw = _s(data, 329, 331)
        weight_change: int | None = None
        if wc_val_raw.isdigit() and wc_sign in ("+", "-", " "):
            wc_val = int(wc_val_raw)
            weight_change = -wc_val if wc_sign == "-" else wc_val

        # 異常区分 (pos 332)
        abnormal_raw = _s(data, 332, 332)
        abnormality_code = int(abnormal_raw) if abnormal_raw.isdigit() else 0

        # 確定着順 (pos 335-336)
        finish_pos_raw = _s(data, 335, 336)
        finish_position = int(finish_pos_raw) if finish_pos_raw.isdigit() and int(finish_pos_raw) > 0 else None

        # 走破タイム MSST形式 (pos 339-342)
        time_raw = _s(data, 339, 342)
        finish_time = _parse_msst_time(time_raw)

        # 後3ハロンタイム SST形式 (pos 391-393)
        last3f_raw = _s(data, 391, 393)
        last_3f = _parse_sst_time(last3f_raw)

        return {
            "jravan_race_id": header["jravan_race_id"],
            "race_date": header["race_date"],
            "frame_number": _i(data, 28, 28),       # 枠番 (1バイト)
            "horse_number": _i(data, 29, 30),        # 馬番
            "jravan_horse_code": _s(data, 31, 40),   # 血統登録番号
            "horse_name": _decode(data, 41, 76),     # 馬名
            "sex": SEX_MAP.get(_s(data, 79, 79), ""),
            "age": _i(data, 83, 84),
            "jravan_trainer_code": _s(data, 86, 90),
            "trainer_name": _decode(data, 91, 98),
            "weight_carried": weight_carried,
            "jravan_jockey_code": _s(data, 297, 301),
            "jockey_name": _decode(data, 307, 314),
            "horse_weight": horse_weight,
            "weight_change": weight_change,
            "abnormality_code": abnormality_code,
            "finish_position": finish_position,
            "finish_time": finish_time,       # 0.1秒単位 (MSST変換後)
            "last_3f": last_3f,               # 0.1秒単位
            "passing_1": _i(data, 352, 353),
            "passing_2": _i(data, 354, 355),
            "passing_3": _i(data, 356, 357),
            "passing_4": _i(data, 358, 359),
            "data_type": header["data_type"],
        }
    except Exception as e:
        logger.error(f"SE parse error: {e} | data[:30]={data[:30]!r}")
        return None


# -------------------------------------------------------------------
# オッズレコード（O1-O6）
# -------------------------------------------------------------------

def parse_odds(data: str) -> dict[str, Any] | None:
    """O1-O6レコード（オッズ各種）の共通ヘッダーをパースする。

    個別オッズの展開は odds_importer で行う。
    ここではレースIDと券種のみ抽出し raw_data を保持する。
    """
    if len(data) < 27:
        return None

    try:
        rec_id = _s(data, 1, 2)
        if rec_id not in ("O1", "O2", "O3", "O4", "O5", "O6"):
            return None

        bet_type_map = {
            "O1": "win_place_bracket",
            "O2": "quinella",
            "O3": "quinella_place",
            "O4": "exacta",
            "O5": "trio",
            "O6": "trifecta",
        }

        header = _parse_common_header(data)
        if not header:
            return None

        return {
            "rec_id": rec_id,
            "bet_type": bet_type_map.get(rec_id, rec_id),
            "jravan_race_id": header["jravan_race_id"],
            "raw_data": data,
        }
    except Exception as e:
        logger.error(f"Odds parse error: {e}")
        return None


# -------------------------------------------------------------------
# AV レコード（出走取消・競走除外, 78バイト）
# -------------------------------------------------------------------
# 共通ヘッダー (pos 1-27) + 馬番(28-29) + 取消区分(30) + 血統登録番号(31-40) + ...

def parse_av(data: str) -> dict[str, Any] | None:
    """AVレコード（出走取消・競走除外）をパースする。

    JVDF v4.9 推定フィールド位置:
      1-27: 共通ヘッダー
     28-29: 馬番
      30  : 取消区分 (1:出走取消, 2:発走除外, 3:競走除外)
     31-40: 血統登録番号
    """
    if len(data) < 40:
        return None
    try:
        header = _parse_common_header(data)
        if not header or header["rec_id"] != "AV":
            return None

        horse_num = _i(data, 28, 29)
        cancel_type = _s(data, 30, 30)
        cancel_label = {"1": "出走取消", "2": "発走除外", "3": "競走除外"}.get(cancel_type, cancel_type)
        jravan_horse_code = _s(data, 31, 40)

        return {
            "jravan_race_id": header["jravan_race_id"],
            "race_date": header["race_date"],
            "horse_number": horse_num,
            "jravan_horse_code": jravan_horse_code,
            "change_type": "scratch",
            "detail": cancel_label,
            "raw_data": data,
        }
    except Exception as e:
        logger.error(f"AV parse error: {e}")
        return None


# -------------------------------------------------------------------
# JC レコード（騎手変更, 161バイト）
# -------------------------------------------------------------------
# 共通ヘッダー (pos 1-27) + 馬番(28-29) +
# 変更前騎手コード(30-34) + 変更前騎手名略称(35-42) + 変更前見習コード(43) +
# 変更後騎手コード(44-48) + 変更後騎手名略称(49-56) + 変更後見習コード(57)

def parse_jc(data: str) -> dict[str, Any] | None:
    """JCレコード（騎手変更）をパースする。

    JVDF v4.9 推定フィールド位置:
      1-27: 共通ヘッダー
     28-29: 馬番
     30-34: 変更前騎手コード (5バイト)
     35-42: 変更前騎手名略称 (8バイト = 全角4文字)
      43  : 変更前騎手見習コード
     44-48: 変更後騎手コード (5バイト)
     49-56: 変更後騎手名略称 (8バイト)
      57  : 変更後騎手見習コード
    """
    if len(data) < 57:
        return None
    try:
        header = _parse_common_header(data)
        if not header or header["rec_id"] != "JC":
            return None

        horse_num = _i(data, 28, 29)
        old_jockey_code = _s(data, 30, 34)
        old_jockey_name = _decode(data, 35, 42)
        new_jockey_code = _s(data, 44, 48)
        new_jockey_name = _decode(data, 49, 56)

        return {
            "jravan_race_id": header["jravan_race_id"],
            "race_date": header["race_date"],
            "horse_number": horse_num,
            "change_type": "jockey_change",
            "old_value": f"{old_jockey_code}:{old_jockey_name}",
            "new_value": f"{new_jockey_code}:{new_jockey_name}",
            "raw_data": data,
        }
    except Exception as e:
        logger.error(f"JC parse error: {e}")
        return None


# -------------------------------------------------------------------
# レコード種別ディスパッチ
# -------------------------------------------------------------------

def parse_record(rec: dict[str, str]) -> dict[str, Any] | None:
    """rec_id に応じて適切なパーサーに振り分ける。

    Args:
        rec: {"rec_id": "RA", "data": "RA..."}

    Returns:
        パース結果dict（_rec_id キー付き）、またはNone
    """
    rec_id = rec.get("rec_id", "")
    data = rec.get("data", "")

    parsers = {
        "RA": parse_ra,
        "SE": parse_se,
        "O1": parse_odds, "O2": parse_odds, "O3": parse_odds,
        "O4": parse_odds, "O5": parse_odds, "O6": parse_odds,
        "AV": parse_av,
        "JC": parse_jc,
    }
    parser = parsers.get(rec_id)
    if parser is None:
        return None

    result = parser(data)
    if result:
        result["_rec_id"] = rec_id
    return result
