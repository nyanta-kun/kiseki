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
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# 競馬場コード → 競馬場名
# -------------------------------------------------------------------
COURSE_NAMES: dict[str, str] = {
    # JRA 10場
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
    # 地方競馬
    "30": "門別",
    "34": "旭川(廃止)",
    "35": "盛岡",       # JBCクラシック2014（盛岡）・マーキュリーC Jpn3・ペラルゴニウム賞等より確定
    "36": "水沢",       # 岩手県（盛岡と同じ岩手競馬）、弘南鉄道・宮城スポンサー等より東北推定
    "41": "高崎(廃止)",
    "42": "浦和",
    "43": "船橋",
    "44": "大井",       # 東京大賞典G1・雲取賞Jpn3より確定
    "45": "川崎",       # ルビーフラワー賞・フェブラリーフラワー賞より確定
    "46": "荒尾(廃止)",
    "47": "笠松",
    "48": "名古屋",     # かきつばた記念Jpn3より確定
    "50": "園田",
    "51": "姫路",
    "54": "高知",       # 足摺盃・室戸盃より確定
    "55": "佐賀",       # 佐賀記念Jpn3より確定
    "56": "廃止場(56)", # 2011年以前のみ・霧島賞等
    "57": "廃止場(57)",
    "58": "廃止場(58)",
    "59": "廃止場(59)",
    "60": "新潟(地方廃止)",  # 信濃川特別・佐渡特別等より確定
    # 海外
    "A4": "米国",
    "A6": "英国",
    "A8": "フランス",
    "B2": "アイルランド",
    "B6": "オーストラリア",
    "B8": "カナダ",
    "C0": "イタリア",
    "C2": "ドイツ",
    "C7": "UAE",
    "F0": "韓国",
    "G0": "香港",
    "K6": "サウジアラビア",
    "M8": "カタール",
    "N2": "バーレーン",
}

# -------------------------------------------------------------------
# トラックコード (コード表2009) → (芝ダ, 方向)
# -------------------------------------------------------------------
TRACK_CODE_MAP: dict[str, tuple[str, str | None]] = {
    "10": ("芝", None),
    "11": ("芝", "右"),
    "12": ("芝", "右外"),
    "13": ("芝", "右"),
    "14": ("芝", "右"),
    "17": ("芝", "直"),
    "18": ("芝", "左"),
    "19": ("芝", "左外"),
    "20": ("芝→ダ", None),
    "22": ("ダ", "右"),
    "23": ("ダ", "左"),
    "24": ("ダ", "右外"),
    "25": ("ダ", "左"),
    "21": ("ダ", None),   # ダート・方向なし（地方競馬等で使用）
    "26": ("ダ", "直"),
    "27": ("サンド", "右"),
    "28": ("サンド", "左"),
}

# グレードコード (コード表2003)
# 'E' = OP特別（平地オープン特別戦）: 実データで確認済み（鎌ケ谷特別・スピカステークス等）
# 'D'/'F'/'G' = 障害グレード系: 障害レースデータ取得後に要検証
GRADE_MAP: dict[str, str] = {
    "A": "G1",
    "B": "G2",
    "C": "G3",
    "D": "J.G1",
    "E": "OP特別",
    "F": "J.G2",
    "G": "J.G3",
    "H": "重賞",
    "L": "Listed",
    " ": "一般",
    "": "",
}

# 馬場状態コード (コード表2010)
CONDITION_MAP: dict[str, str] = {"1": "良", "2": "稍", "3": "重", "4": "不"}

# 天候コード (コード表2011)
WEATHER_MAP: dict[str, str] = {
    "1": "晴",
    "2": "曇",
    "3": "雨",
    "4": "小雨",
    "5": "雪",
    "6": "小雪",
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
    return data[start - 1 : end].strip()


def _i(data: str, start: int, end: int) -> int | None:
    """1-indexed バイト位置から整数を抽出する（全ゼロまたは空は None）。"""
    raw = data[start - 1 : end].strip()
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
    raw = data[start - 1 : end]
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


def _parse_sst(data: str, start: int, end: int) -> Decimal | None:
    """SST形式フィールド（秒×10 3バイト）をDecimalに変換する。

    SST = 秒(2桁) + 0.1秒(1桁)
    例: "336" → Decimal('33.6')、"000" → None

    Args:
        data: レコード文字列
        start: 開始バイト位置 (1-indexed)
        end: 終了バイト位置 (1-indexed)

    Returns:
        秒単位のDecimal、無効の場合は None
    """
    from decimal import Decimal

    raw = _s(data, start, end)
    try:
        v = int(raw)
        if v <= 0:
            return None
        return Decimal(str(round(v / 10, 1)))
    except (ValueError, TypeError):
        return None


def _parse_weight_field(data: str, start: int, end: int) -> Decimal | None:
    """斤量フィールド（0.1kg単位整数）をDecimalに変換する。

    例: "560" → Decimal('56.0')、"000" → None

    Args:
        data: レコード文字列
        start: 開始バイト位置 (1-indexed)
        end: 終了バイト位置 (1-indexed)

    Returns:
        kg単位のDecimal、無効の場合は None
    """
    from decimal import Decimal

    raw = _s(data, start, end)
    try:
        v = int(raw)
        if v <= 0:
            return None
        return Decimal(str(round(v / 10, 1)))
    except (ValueError, TypeError):
        return None


def _parse_win_odds(data: str, start: int, end: int) -> Decimal | None:
    """単勝オッズフィールド（10倍精度, 4バイト）をDecimalに変換する。

    例: "0153" → Decimal('15.3')、"9999"=不成立 → None

    Args:
        data: レコード文字列
        start: 開始バイト位置 (1-indexed)
        end: 終了バイト位置 (1-indexed)

    Returns:
        倍率のDecimal、無効・不成立の場合は None
    """
    from decimal import Decimal

    raw = _s(data, start, end)
    try:
        v = int(raw)
        if v <= 0 or v >= 9999:
            return None
        return Decimal(str(round(v / 10, 1)))
    except (ValueError, TypeError):
        return None


def _parse_time_diff(data: str, pos: int) -> Decimal | None:
    """タイム差フィールド（pos=1-indexed先頭位置, 4バイト: 符号1+数値3）をDecimalに変換する。

    例: " 053" → Decimal('5.3')（プラス）、"-053" → Decimal('-5.3')

    Args:
        data: レコード文字列
        pos: 符号文字の1-indexedバイト位置

    Returns:
        秒単位のDecimal（符号付き）、無効の場合は None
    """
    from decimal import Decimal

    sign_char = data[pos - 1]
    num_str = data[pos : pos + 3]
    try:
        v = int(num_str)
        if sign_char == "-":
            v = -v
        return Decimal(str(round(v / 10, 1)))
    except (ValueError, TypeError):
        return None


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
    874-877: 発走時刻 (hhmm形式, 例: "1025" = 10:25)
    878-881: 変更前発走時刻 (発走時刻変更時のみ)
    882-883: 登録頭数
    884-885: 出走頭数
    888   : 天候コード (コード表2011)
    889   : 芝馬場状態コード (コード表2010)
    890   : ダート馬場状態コード (コード表2010)
    """
    if len(data) < 1272:
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
            "race_date": header["race_date"],  # 開催日 YYYYMMDD
            "created_date": header["created_date"],  # データ作成日 YYYYMMDD
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
            "post_time": _s(data, 874, 877) or None,  # 発走時刻 hhmm形式 (例: "1025")
            # 競走情報
            "race_type_code": _s(data, 617, 618),
            "weight_type_code": _s(data, 622, 622),
            "prev_grade_code": _s(data, 616, 616) or None,
            # 賞金（百円単位、0はNone）
            "prize_1st": _i(data, 714, 721) or None,
            "prize_2nd": _i(data, 722, 729) or None,
            "prize_3rd": _i(data, 730, 737) or None,
            # 頭数
            "registered_count": _i(data, 882, 883),
            "finishers_count": _i(data, 886, 887),
            # ラップ・ハロンタイム
            "first_3f": _parse_sst(data, 970, 972),
            "last_3f_race": _parse_sst(data, 976, 978),
            "lap_times": _s(data, 891, 965) or None,
            "record_update_type": _s(data, 1270, 1270) or None,
            # 変更前フィールド（変更検知用）
            "prev_distance": _i(data, 702, 705) or None,
            "prev_track_code": _s(data, 708, 709) or None,
            "prev_post_time": _s(data, 878, 881) or None,
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
    if len(data) < 555:
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
        weight_carried = (
            round(int(weight_raw) / 10, 1) if weight_raw.isdigit() and int(weight_raw) > 0 else None
        )

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
        finish_position = (
            int(finish_pos_raw) if finish_pos_raw.isdigit() and int(finish_pos_raw) > 0 else None
        )

        # 走破タイム MSST形式 (pos 339-342)
        time_raw = _s(data, 339, 342)
        finish_time = _parse_msst_time(time_raw)

        # 後3ハロンタイム SST形式 (pos 391-393)
        last3f_raw = _s(data, 391, 393)
        last_3f = _parse_sst_time(last3f_raw)

        return {
            "jravan_race_id": header["jravan_race_id"],
            "race_date": header["race_date"],
            "frame_number": _i(data, 28, 28),  # 枠番 (1バイト)
            "horse_number": _i(data, 29, 30),  # 馬番
            "jravan_horse_code": _s(data, 31, 40),  # 血統登録番号
            "horse_name": _decode(data, 41, 76),  # 馬名
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
            "finish_time": finish_time,  # 0.1秒単位 (MSST変換後)
            "last_3f": last_3f,  # 0.1秒単位
            "passing_1": _i(data, 352, 353),
            "passing_2": _i(data, 354, 355),
            "passing_3": _i(data, 356, 357),
            "passing_4": _i(data, 358, 359),
            # 馬情報
            "horse_age": _i(data, 83, 84),
            "east_west_code": _s(data, 85, 85),
            # 変更前フィールド（変更検知用）
            "prev_weight_carried": _parse_weight_field(data, 292, 294),
            "blinker": _s(data, 295, 295) == "1",
            "prev_jockey_code": _s(data, 302, 306) or None,
            "jockey_apprentice_code": _s(data, 323, 323) or None,
            # 着順・着差
            "arrival_position": _i(data, 333, 334) or None,
            "dead_heat": _s(data, 337, 337) == "1",
            "margin_code": _s(data, 343, 345) or None,
            # オッズ・賞金
            "win_odds": _parse_win_odds(data, 360, 363),
            "win_popularity": _i(data, 364, 365) or None,
            "prize_money": _i(data, 366, 373) or None,
            # ハロンタイム
            "last_4f": _parse_sst(data, 388, 390),
            # タイム差
            "time_diff": _parse_time_diff(data, 532),
            # 脚質
            "running_style": _s(data, 553, 553) or None,
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
            "O1": "win",  # 単勝
            "O2": "place",  # 複勝
            "O3": "bracket_quinella",  # 枠連
            "O4": "quinella",  # 馬連
            "O5": "quinella_place",  # ワイド
            "O6": "exacta",  # 馬単
            "O7": "trio",  # 三連複
            "O8": "trifecta",  # 三連単
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
        cancel_label = {"1": "出走取消", "2": "発走除外", "3": "競走除外"}.get(
            cancel_type, cancel_type
        )
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
# HN レコード（繁殖馬マスタ, 251バイト）
# -------------------------------------------------------------------
# 共通ヘッダー相当の位置:
#   1- 2: "HN"
#   3   : データ区分 (0:削除)
#   4-11: データ作成年月日
#  12-21: 繁殖登録番号 (10バイト) ← KEY
#  30-39: 血統登録番号 (10バイト)
#  41-76: 馬名 (36バイト, SJIS全角)
#  77-116: 馬名半角カナ (40バイト)
# 117-196: 馬名欧字 (80バイト)
# 197-200: 生年 (4バイト)
#  201  : 性別コード (1バイト)
# 230-239: 父馬繁殖登録番号 (10バイト)
# 240-249: 母馬繁殖登録番号 (10バイト)


def parse_hn(data: str) -> dict[str, Any] | None:
    """HNレコード（繁殖馬マスタ）をパースする。

    繁殖登録番号をキーとして、馬名・血統登録番号・親馬コードを返す。
    SKレコードの sire_code/dam_code をこのレコードで名前解決する。

    JVDF v4.9 フィールド位置（1-indexed バイト）:
      1- 2: "HN"
      3   : データ区分 (0:削除)
      4-11: データ作成年月日
     12-21: 繁殖登録番号 (KEY)
     30-39: 血統登録番号
     41-76: 馬名 (SJIS全角36バイト)
    117-196: 馬名欧字 (80バイト)
    230-239: 父馬繁殖登録番号
    240-249: 母馬繁殖登録番号
    """
    if len(data) < 30:
        return None
    try:
        rec_id = _s(data, 1, 2)
        if rec_id != "HN":
            return None

        data_type = _s(data, 3, 3)
        breeding_code = _s(data, 12, 21)  # 繁殖登録番号
        if not breeding_code:
            return None

        blood_code = _s(data, 30, 39)  # 血統登録番号（競走馬として登録がある場合）
        name = _decode(data, 41, 76)  # 馬名（全角）
        name_en = _s(data, 117, 196)  # 馬名欧字
        birth_year = _s(data, 197, 200)
        sex_code = _s(data, 201, 201)
        sire_breeding_code = _s(data, 230, 239)
        dam_breeding_code = _s(data, 240, 249)

        return {
            "_rec_id": "HN",
            "data_type": data_type,
            "breeding_code": breeding_code,
            "blood_code": blood_code,
            "name": name,
            "name_en": name_en.strip(),
            "birth_year": birth_year,
            "sex_code": sex_code,
            "sire_breeding_code": sire_breeding_code,
            "dam_breeding_code": dam_breeding_code,
        }
    except Exception as e:
        logger.error(f"HN parse error: {e} data={data[:30]!r}")
        return None


# -------------------------------------------------------------------
# SK レコード（産駒マスタ, 208バイト）
# -------------------------------------------------------------------
# 共通ヘッダー相当の位置:
#   1- 2: "SK"
#   3   : データ区分 (0:削除)
#   4-11: データ作成年月日
#  12-21: 血統登録番号 (10バイト) ← KEY = Horse.jravan_code
#  22-29: 生年月日 (8バイト, YYYYMMDD)
#  30   : 性別コード
#  31   : 品種コード
#  32-33: 毛色コード
#  34   : 産駒持込区分
#  35-38: 輸入年
#  39-46: 生産者コード
#  47-66: 産地名 (20バイト, SJIS全角)
#  67-206: 3代血統 繁殖登録番号 × 14頭 (10バイト×14=140バイト)
#          index 0: 父  index 1: 母  index 2: 父父  index 3: 父母
#          index 4: 母父 index 5: 母母 ...（以降は4代目）
# 207-208: レコード区切


def parse_sk(data: str) -> dict[str, Any] | None:
    """SKレコード（産駒マスタ）をパースする。

    血統登録番号（Horse.jravan_code）をキーとして、3代血統の繁殖登録番号を返す。
    繁殖登録番号は HNレコードの breeding_code で名前解決する。

    JVDF v4.9 フィールド位置（1-indexed バイト）:
      1- 2: "SK"
     12-21: 血統登録番号 (KEY, = Horse.jravan_code)
     22-29: 生年月日 YYYYMMDD
     67-206: 3代血統 繁殖登録番号 × 14頭 (10バイト×14)
             [0]父 [1]母 [2]父父 [3]父母 [4]母父 [5]母母
             [6]父父父 [7]父父母 [8]父母父 [9]父母母
             [10]母父父 [11]母父母 [12]母母父 [13]母母母

    注: 実際のJVLink返却データは仕様書より短い場合あり（11頭分=176バイト程度）。
    blood_code(pos12-21) と 母父(pos107-116) が取れる最小長=116 を下限とする。
    """
    if len(data) < 30:
        return None
    try:
        rec_id = _s(data, 1, 2)
        if rec_id != "SK":
            return None

        data_type = _s(data, 3, 3)
        blood_code = _s(data, 12, 21)  # 血統登録番号 = Horse.jravan_code
        if not blood_code:
            return None

        birth_date = _s(data, 22, 29)

        # 3代血統 繁殖登録番号 (14頭分)
        pedigree_codes: list[str] = []
        for i in range(14):
            start = 67 + i * 10
            end = start + 9  # 10バイト (start〜start+9)
            pedigree_codes.append(_s(data, start, end))

        return {
            "_rec_id": "SK",
            "data_type": data_type,
            "blood_code": blood_code,  # = Horse.jravan_code
            "birth_date": birth_date,
            "sire_code": pedigree_codes[0],  # 父
            "dam_code": pedigree_codes[1],  # 母
            "sire_sire_code": pedigree_codes[2],  # 父父
            "sire_dam_code": pedigree_codes[3],  # 父母
            "dam_sire_code": pedigree_codes[4],  # 母父
            "dam_dam_code": pedigree_codes[5],  # 母母
            "all_codes": pedigree_codes,
        }
    except Exception as e:
        logger.error(f"SK parse error: {e} data={data[:30]!r}")
        return None


# -------------------------------------------------------------------
# HR レコード（払戻情報）
# -------------------------------------------------------------------

# JVDF v4.9 HR レコード 馬券種別ごとのバイト位置定義
# 参考仕様: JV-Data JVDF v4.9 HR（払戻情報）レコード
#
# HR レコード共通ヘッダー (pos 1-27): RA/SE と同一構造
#
# 払戻データ部 (pos 28〜): 馬券種別ごとの払戻情報
#   単勝 (pos 28-40):   馬番(2) + 払戻(7) + 人気(3) + 未使用(1) = 13バイト × 1件
#   複勝 (pos 41-76):   馬番(2) + 払戻低(7) + 払戻高(7) + 人気(3) = 19バイト × 3件 = 57バイト
#                       ※ 実際: 各19バイト構造（TODO: 仕様書で要確認）
#   枠連 (pos 99〜):    枠番ペア(2) + 払戻(7) + 人気(3) = 12バイト × 1件（同枠含む1件）
#   馬連 (pos 112〜):   馬番ペア(4) + 払戻(7) + 人気(3) = 14バイト × 3件（同着対応）
#   ワイド (pos 155〜): 馬番ペア(4) + 払戻低(7) + 払戻高(7) + 人気(3) = 21バイト × 7件
#   馬単 (pos 303〜):   馬番ペア(4) + 払戻(7) + 人気(3) = 14バイト × 6件
#   三連複 (pos 387〜): 馬番3頭(6) + 払戻(7) + 人気(3) = 16バイト × 3件（同着対応）
#   三連単 (pos 435〜): 馬番3頭(6) + 払戻(7) + 人気(3) = 16バイト × 6件
#
# NOTE: バイト位置の一部は仕様書の詳細確認が必要なため TODO コメントを付与


def parse_hr(data: str) -> dict[str, Any] | None:
    """HRレコード（払戻情報）をパースする。

    JVDF v4.9 フィールド位置（1-indexed バイト）:
      1- 2: "HR"
      3   : データ区分
      4-11: データ作成年月日
     12-15: 開催年
     16-19: 開催月日
     20-21: 競馬場コード
     22-23: 開催回
     24-25: 開催日目
     26-27: レース番号
     28〜 : 払戻データ部（各馬券種）

    Returns:
        {
            "rec_id": "HR",
            "race_id": str,  # 16文字のレースキー
            "race_date": str,  # YYYYMMDD
            "course": str,
            "race_number": int,
            "payouts": [
                {"bet_type": "win", "combination": "3", "payout": 1540, "popularity": 1},
                ...
            ]
        }
        または None（パース失敗・削除レコード）
    """
    # HR レコードの最小長チェック（ヘッダー27バイト + データ部）
    if len(data) < 100:
        logger.warning(f"HR record too short: {len(data)} bytes")
        return None

    try:
        header = _parse_common_header(data)
        if not header or header["rec_id"] != "HR":
            return None
        if header["data_type"] == "0":  # 削除レコード
            return None

        payouts: list[dict[str, Any]] = []

        # JVDF v4.9 HR レコード払戻データ部（1-indexed バイト位置）
        # 各エントリの基本構造: 馬番/組番 + 払戻金(9バイト) + 人気順

        def _parse_payout_entries(
            start: int, count: int, entry_size: int,
            combo_bytes: int, pop_bytes: int,
            bet_type: str, multi_horse: bool = False,
        ) -> None:
            """払戻エントリを汎用パースしてpayoutsに追加する。"""
            for i in range(count):
                base = start + i * entry_size
                if base + entry_size - 1 > len(data):
                    break
                combo_raw = _s(data, base, base + combo_bytes - 1)
                payout_raw = _s(data, base + combo_bytes, base + combo_bytes + 8)  # 9バイト
                pop_raw = _s(data, base + combo_bytes + 9, base + combo_bytes + 8 + pop_bytes)
                if not combo_raw or not payout_raw or not payout_raw.isdigit():
                    continue
                payout_val = int(payout_raw)
                if payout_val <= 0:
                    continue
                if multi_horse:
                    # 組番: 2バイト × N頭 → "03-07-11" 形式
                    parts = []
                    for j in range(0, combo_bytes, 2):
                        chunk = combo_raw[j : j + 2]
                        if chunk.isdigit():
                            parts.append(str(int(chunk)))
                    combination = "-".join(parts) if parts else combo_raw
                elif combo_bytes == 2:
                    combination = str(int(combo_raw)) if combo_raw.isdigit() else combo_raw
                else:
                    # 枠番ペア "12" → "1-2"
                    f1 = combo_raw[0] if combo_raw else ""
                    f2 = combo_raw[1] if len(combo_raw) > 1 else ""
                    combination = f"{f1}-{f2}" if f2 else f1
                payouts.append({
                    "bet_type": bet_type,
                    "combination": combination,
                    "payout": payout_val,
                    "popularity": int(pop_raw) if pop_raw.isdigit() else None,
                })

        # --- 単勝 (Win): pos 103, 3件 × 13バイト: 馬番(2)+払戻金(9)+人気順(2) ---
        _parse_payout_entries(103, 3, 13, combo_bytes=2, pop_bytes=2, bet_type="win")

        # --- 複勝 (Place): pos 142, 5件 × 13バイト: 馬番(2)+払戻金(9)+人気順(2) ---
        _parse_payout_entries(142, 5, 13, combo_bytes=2, pop_bytes=2, bet_type="place")

        # --- 枠連 (Bracket Quinella): pos 207, 3件 × 13バイト: 組番(2)+払戻金(9)+人気順(2) ---
        _parse_payout_entries(207, 3, 13, combo_bytes=2, pop_bytes=2, bet_type="bracket")

        # --- 馬連 (Quinella): pos 246, 3件 × 16バイト: 組番(4)+払戻金(9)+人気順(3) ---
        _parse_payout_entries(246, 3, 16, combo_bytes=4, pop_bytes=3, bet_type="quinella", multi_horse=True)

        # --- ワイド (Wide): pos 294, 7件 × 16バイト: 組番(4)+払戻金(9)+人気順(3) ---
        _parse_payout_entries(294, 7, 16, combo_bytes=4, pop_bytes=3, bet_type="wide", multi_horse=True)

        # pos 406-453: 予備領域（3件 × 16バイト）- スキップ

        # --- 馬単 (Exacta): pos 454, 6件 × 16バイト: 組番(4)+払戻金(9)+人気順(3) ---
        _parse_payout_entries(454, 6, 16, combo_bytes=4, pop_bytes=3, bet_type="exacta", multi_horse=True)

        # --- 三連複 (Trio): pos 550, 3件 × 18バイト: 組番(6)+払戻金(9)+人気順(3) ---
        _parse_payout_entries(550, 3, 18, combo_bytes=6, pop_bytes=3, bet_type="trio", multi_horse=True)

        # --- 三連単 (Trifecta): pos 604, 6件 × 19バイト: 組番(6)+払戻金(9)+人気順(4) ---
        _parse_payout_entries(604, 6, 19, combo_bytes=6, pop_bytes=4, bet_type="trifecta", multi_horse=True)

        race_num_raw = header["race_num"]
        return {
            "rec_id": "HR",
            "race_id": header["jravan_race_id"],
            "race_date": header["race_date"],
            "course": header["course_code"],
            "race_number": int(race_num_raw) if race_num_raw.isdigit() else 0,
            "payouts": payouts,
        }
    except Exception as e:
        logger.error(f"HR parse error: {e} | data[:30]={data[:30]!r}")
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
        "O1": parse_odds,
        "O2": parse_odds,
        "O3": parse_odds,
        "O4": parse_odds,
        "O5": parse_odds,
        "O6": parse_odds,
        "AV": parse_av,
        "JC": parse_jc,
        "HN": parse_hn,
        "SK": parse_sk,
        "HR": parse_hr,
    }
    parser = parsers.get(rec_id)
    if parser is None:
        return None

    result = parser(data)
    if result:
        result["_rec_id"] = rec_id
    return result
