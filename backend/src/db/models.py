"""kiseki データベースモデル定義"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .session import Base

SCHEMA = "keiba"


class Horse(Base):
    """馬マスタ"""

    __tablename__ = "horses"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="馬名")
    sex: Mapped[str] = mapped_column(String(10), comment="性別（牡/牝/セン）")
    birthday: Mapped[str] = mapped_column(String(8), comment="生年月日（YYYYMMDD）")
    coat_color: Mapped[str | None] = mapped_column(String(20), comment="毛色")
    owner: Mapped[str | None] = mapped_column(String(100), comment="馬主名")
    breeder: Mapped[str | None] = mapped_column(String(100), comment="生産者名")
    jravan_code: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, comment="JRA-VAN馬コード（10桁）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="レコード作成日時"
    )


class ProvisionalHorse(Base):
    """JV-Link未登録の暫定馬マスタ（netkeiba スクレイプ由来）

    競走馬登録完了前の2歳馬を一時保持する。
    JV-Link から SE レコード（初出走）が届いた時点で keiba.horses とマージし
    merged_horse_id をセットする。
    """

    __tablename__ = "provisional_horses"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    netkeiba_horse_id: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, comment="netkeibaの馬ID（血統登録番号と一致することが多い）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="馬名（カタカナ）")
    birth_year: Mapped[int | None] = mapped_column(Integer, comment="生産年")
    birth_date: Mapped[str | None] = mapped_column(String(8), comment="生年月日 YYYYMMDD")
    sex: Mapped[str | None] = mapped_column(String(10), comment="性別")
    coat_color: Mapped[str | None] = mapped_column(String(20), comment="毛色")
    sire_name: Mapped[str | None] = mapped_column(String(100), comment="父馬名")
    dam_name: Mapped[str | None] = mapped_column(String(100), comment="母馬名")
    broodmare_sire_name: Mapped[str | None] = mapped_column(String(100), comment="母父馬名")
    trainer_name: Mapped[str | None] = mapped_column(String(100), comment="調教師名")
    owner_name: Mapped[str | None] = mapped_column(String(100), comment="馬主名")
    farm_name: Mapped[str | None] = mapped_column(String(100), comment="生産牧場名")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    merged_horse_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.horses.id"), nullable=True, comment="JV-Link登録後の keiba.horses.id"
    )
    merged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Pedigree(Base):
    """血統情報"""

    __tablename__ = "pedigrees"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("horse_id", name="uq_pedigree_horse_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    horse_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.horses.id"), unique=True, index=True
    )
    sire: Mapped[str | None] = mapped_column(String(100), comment="父馬名")
    dam: Mapped[str | None] = mapped_column(String(100), comment="母馬名")
    sire_of_dam: Mapped[str | None] = mapped_column(String(100), comment="母父馬名")
    sire_line: Mapped[str | None] = mapped_column(String(50), comment="父系統名")
    dam_sire_line: Mapped[str | None] = mapped_column(String(50), comment="母父系統名")


class BreedingHorse(Base):
    """繁殖馬マスタ（HNレコード永続キャッシュ）

    JV-Link BLOD HN レコードを永続化する。
    プロセス再起動後も繁殖登録番号 → 馬名の変換が可能になる。
    """

    __tablename__ = "breeding_horses"
    __table_args__ = {"schema": SCHEMA}

    breeding_code: Mapped[str] = mapped_column(Text(), primary_key=True, comment="繁殖登録番号")
    name: Mapped[str | None] = mapped_column(Text(), comment="馬名（日本語）")
    name_en: Mapped[str | None] = mapped_column(Text(), comment="馬名（欧字）")


class Jockey(Base):
    """騎手マスタ"""

    __tablename__ = "jockeys"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="騎手名")
    jravan_code: Mapped[str | None] = mapped_column(
        String(10), unique=True, index=True, comment="JRA-VAN騎手コード（5桁）"
    )


class JockeyRunningStyleStats(Base):
    """騎手戦法統計（v25 で導入、月次更新）。

    直近 window_months ヶ月の race_results から各騎手の脚質割合を集計したもの。
    pace_handicap v2 が「騎手主導の戦法予測」に使用する。
    """

    __tablename__ = "jockey_running_style_stats"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("jockey_id", "window_months", name="uq_jockey_style_window"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jockey_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.jockeys.id"), nullable=False, index=True
    )
    window_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, comment="集計対象の月数（直近24ヶ月）"
    )
    total_rides: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="集計対象期間の騎乗数"
    )
    escape_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), comment="逃げ率")
    leader_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), comment="先行率")
    mid_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), comment="中団率")
    closer_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), comment="後方率")
    makuri_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), comment="マクリ率（passing_1 - passing_4 ≥ 5）"
    )
    diversity: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), comment="戦法多様性 1 - Σpᵢ²（低=特化型, 高=柔軟型）"
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="集計日時"
    )


class Trainer(Base):
    """調教師マスタ"""

    __tablename__ = "trainers"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="調教師名")
    jravan_code: Mapped[str | None] = mapped_column(
        String(10), unique=True, index=True, comment="JRA-VAN調教師コード（5桁）"
    )


class Race(Base):
    """レースマスタ"""

    __tablename__ = "races"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(
        String(8), nullable=False, index=True, comment="開催日（YYYYMMDD）"
    )
    course: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="競馬場コード（コード表2001: 01=札幌,05=東京,06=中山等）",
    )
    course_name: Mapped[str] = mapped_column(String(20), comment="競馬場名（東京/中山/阪神等）")
    race_number: Mapped[int] = mapped_column(Integer, nullable=False, comment="レース番号（1-12）")
    race_name: Mapped[str | None] = mapped_column(String(100), comment="レース名")
    surface: Mapped[str] = mapped_column(String(5), comment="トラック種別（芝/ダ/障）")
    distance: Mapped[int] = mapped_column(Integer, comment="距離（m）")
    direction: Mapped[str | None] = mapped_column(String(5), comment="回り方向（右/左）")
    track_type: Mapped[str | None] = mapped_column(String(10), comment="トラック種類（内/外/直線）")
    condition: Mapped[str | None] = mapped_column(String(5), comment="馬場状態（良/稍/重/不）")
    weather: Mapped[str | None] = mapped_column(String(10), comment="天候（晴/曇/雨/小雨/雪/小雪）")
    grade: Mapped[str | None] = mapped_column(String(10), comment="グレード（G1/G2/G3/OP/条件等）")
    head_count: Mapped[int | None] = mapped_column(Integer, comment="出走頭数")
    registered_count: Mapped[int | None] = mapped_column(
        Integer, comment="登録頭数（取消前の出走予定頭数）"
    )
    finishers_count: Mapped[int | None] = mapped_column(
        Integer, comment="入線頭数（出走頭数から競走中止を除く）"
    )
    race_type_code: Mapped[str | None] = mapped_column(
        String(2),
        comment="競走種別コード（コード表2005: 11=2歳,12=3歳,13=3歳以上,14=4歳以上,20=障害等）",
    )
    weight_type_code: Mapped[str | None] = mapped_column(
        String(1), comment="重量種別コード（コード表2008: 1=馬齢,2=定量,3=別定,4=ハンデ）"
    )
    prize_1st: Mapped[int | None] = mapped_column(Integer, comment="1着本賞金（百円単位）")
    prize_2nd: Mapped[int | None] = mapped_column(Integer, comment="2着本賞金（百円単位）")
    prize_3rd: Mapped[int | None] = mapped_column(Integer, comment="3着本賞金（百円単位）")
    first_3f: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="前3ハロン通過タイム（秒、SST形式変換後）"
    )
    last_3f_race: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="レース後3ハロンタイム（秒、SEのlast_3fは個馬別）"
    )
    lap_times: Mapped[str | None] = mapped_column(
        String(75), comment="ラップタイム生データ（25F分×3バイトSST形式、平地のみ）"
    )
    record_update_type: Mapped[str | None] = mapped_column(
        String(1), comment="レコード更新区分（0:初期値,1:コース基準更新,2:コースレコード更新）"
    )
    prev_distance: Mapped[int | None] = mapped_column(
        Integer, comment="変更前距離（距離変更時のみ設定、単位m）"
    )
    prev_track_code: Mapped[str | None] = mapped_column(
        String(2), comment="変更前トラックコード（トラック変更時のみ）"
    )
    prev_grade_code: Mapped[str | None] = mapped_column(
        String(1), comment="変更前グレードコード（グレード変更時のみ）"
    )
    prev_post_time: Mapped[str | None] = mapped_column(
        String(4), comment="変更前発走時刻（hhmm形式、発走時刻変更時のみ）"
    )
    post_time: Mapped[str | None] = mapped_column(
        String(4), comment="発走時刻（hhmm形式、例: '1025' = 10:25）"
    )
    jravan_race_id: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
        index=True,
        comment="JRA-VANレースID（16文字: year+month_day+course+kai+day+race_num）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="レコード作成日時"
    )

    @property
    def race_class_label(self) -> str | None:
        """条件戦のクラスラベルを算出する（例: '3歳未勝利', '4歳以上2勝クラス'）。

        grade が設定されている場合（G1/G2/G3/OP特別等）は None を返す。
        race_type_code と prize_1st（百円単位）からクラスを判定。
        """
        if self.grade:
            return None

        _AGE: dict[str, str] = {
            "11": "2歳",
            "12": "3歳",
            "13": "3歳以上",
            "14": "4歳以上",
            "18": "障害3歳以上",
            "19": "障害4歳以上",
        }
        age = _AGE.get(self.race_type_code or "", "")

        if not self.prize_1st:
            return age or None

        p = self.prize_1st
        tc = self.race_type_code or ""

        if tc == "11":  # 2歳
            cls = "未勝利" if p <= 58000 else "1勝クラス"
        elif tc == "12":  # 3歳
            cls = "未勝利" if p <= 62000 else "1勝クラス" if p <= 74000 else "2勝クラス"
        elif tc == "13":  # 3歳以上
            cls = "1勝クラス" if p <= 100000 else "2勝クラス" if p <= 130000 else "3勝クラス"
        elif tc == "14":  # 4歳以上
            cls = "2勝クラス" if p <= 100000 else "3勝クラス"
        elif tc in ("18", "19"):  # 障害
            cls = "未勝利" if p <= 90000 else "2勝クラス" if p <= 150000 else "3勝クラス"
        else:
            cls = ""

        if age and cls:
            return f"{age}{cls}"
        return age or cls or None


class RaceEntry(Base):
    """出馬表"""

    __tablename__ = "race_entries"

    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_number", name="uq_race_entry_horse_num"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    frame_number: Mapped[int] = mapped_column(Integer, comment="枠番（1-8）")
    horse_number: Mapped[int] = mapped_column(Integer, comment="馬番（1-18）")
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.jockeys.id"))
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.trainers.id"))
    weight_carried: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="負担重量（kg）")
    horse_weight: Mapped[int | None] = mapped_column(
        Integer, comment="馬体重（kg、計不明時はNone）"
    )
    weight_change: Mapped[int | None] = mapped_column(Integer, comment="馬体重増減（kg、符号付き）")
    horse_age: Mapped[int | None] = mapped_column(Integer, comment="馬齢（満年齢、2001年以降）")
    east_west_code: Mapped[str | None] = mapped_column(
        String(1), comment="東西所属コード（1:東,2:西,3:地方,4:海外）"
    )
    prev_weight_carried: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="変更前負担重量（kg、斤量変更時のみ設定）"
    )
    blinker: Mapped[bool | None] = mapped_column(
        Boolean, comment="ブリンカー使用フラグ（True:使用,False:未使用）"
    )
    prev_jockey_code: Mapped[str | None] = mapped_column(
        String(5), comment="変更前騎手コード（騎手変更時のみ設定）"
    )
    jockey_apprentice_code: Mapped[str | None] = mapped_column(
        String(1), comment="騎手見習コード（0:なし,1=5kg減,2=3kg減,3=1kg減）"
    )
    jvan_time_dm: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="JRA-VAN NEXT タイム型DM指数（例: 43.1）"
    )
    jvan_battle_dm: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="JRA-VAN NEXT 対戦型DM指数スコア（例: 80.7）"
    )


class RacePayout(Base):
    """払戻情報（HR レコード由来）"""

    __tablename__ = "race_payouts"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "bet_type", "combination", name="uq_race_payouts_race_type_combo"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.races.id"), index=True, comment="races テーブルの id"
    )
    bet_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="馬券種別（win/place/bracket/quinella/wide/exacta/trio/trifecta）",
    )
    combination: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="馬番組み合わせ（単: '3', 2頭: '3-7', 3頭: '3-7-11' など）",
    )
    payout: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="払戻金額（100円あたり）",
    )
    popularity: Mapped[int | None] = mapped_column(Integer, comment="人気順位")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="レコード作成日時"
    )


class RaceResult(Base):
    """レース結果"""

    __tablename__ = "race_results"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_id", name="uq_race_result_horse"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    entry_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.race_entries.id"))
    finish_position: Mapped[int | None] = mapped_column(Integer, comment="確定着順")
    frame_number: Mapped[int | None] = mapped_column(Integer, comment="枠番（1-8）")
    horse_number: Mapped[int | None] = mapped_column(Integer, comment="馬番（1-18）")
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.jockeys.id"))
    weight_carried: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="負担重量（kg）")
    horse_weight: Mapped[int | None] = mapped_column(Integer, comment="馬体重（kg）")
    weight_change: Mapped[int | None] = mapped_column(Integer, comment="馬体重増減（kg、符号付き）")
    finish_time: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), comment="走破タイム（秒）")
    margin: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="着差（馬身）")
    passing_1: Mapped[int | None] = mapped_column(Integer, comment="1コーナー通過順位")
    passing_2: Mapped[int | None] = mapped_column(Integer, comment="2コーナー通過順位")
    passing_3: Mapped[int | None] = mapped_column(Integer, comment="3コーナー通過順位")
    passing_4: Mapped[int | None] = mapped_column(Integer, comment="4コーナー通過順位")
    last_3f: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 1), comment="後3ハロンタイム（秒、個馬別）"
    )
    abnormality_code: Mapped[int | None] = mapped_column(
        Integer, default=0, comment="異常区分（1:出走取消,2:発走除外,3:競走中止,4:失格,5:降着）"
    )
    arrival_position: Mapped[int | None] = mapped_column(
        Integer, comment="入線順位（失格・降着確定前の順位）"
    )
    dead_heat: Mapped[bool | None] = mapped_column(Boolean, comment="同着フラグ（True:同着あり）")
    margin_code: Mapped[str | None] = mapped_column(
        String(3), comment="着差コード（コード表2102: '000'=ハナ差等）"
    )
    win_odds: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="確定単勝オッズ（倍、期待値計算の基準値）"
    )
    win_popularity: Mapped[int | None] = mapped_column(
        Integer, comment="単勝人気順位（1位が最低オッズ）"
    )
    prize_money: Mapped[int | None] = mapped_column(Integer, comment="獲得本賞金（百円単位）")
    last_4f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="後4ハロンタイム（秒）")
    time_diff: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="1着とのタイム差（秒、マイナスは1着馬が速い）"
    )
    running_style: Mapped[str | None] = mapped_column(
        String(1), comment="JRA判定脚質（1:逃,2:先,3:差,4:追）"
    )
    place_odds: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1),
        comment="複勝確定払戻倍率（HR レコードから取得、100円あたり払戻÷100）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TrackCondition(Base):
    """馬場差データ"""

    __tablename__ = "track_conditions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(8), index=True, comment="開催日（YYYYMMDD）")
    course: Mapped[str] = mapped_column(String(10), comment="競馬場コード")
    surface: Mapped[str] = mapped_column(String(5), comment="トラック種別（芝/ダ）")
    distance: Mapped[int] = mapped_column(Integer, comment="距離（m）")
    condition: Mapped[str | None] = mapped_column(String(5), comment="馬場状態（良/稍/重/不）")
    bias_value: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), comment="馬場差値（基準タイムとの差、秒）"
    )


class CalculatedIndex(Base):
    """算出指数"""

    __tablename__ = "calculated_indices"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    version: Mapped[int] = mapped_column(
        Integer, default=1, comment="算出バージョン（指数ロジック変更時にインクリメント）"
    )
    # スピード指数系
    speed_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="スピード指数（基準50、標準偏差10）"
    )
    adjusted_speed_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="補正スピード指数（斤量・馬場差補正後）"
    )
    last_3f_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="後3ハロン指数")
    # 適性指数系
    course_aptitude: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="コース適性指数")
    distance_aptitude: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="距離適性指数")
    position_advantage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="枠順有利不利指数"
    )
    # 能力指数系
    jockey_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="騎手指数")
    trainer_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="調教師指数")
    pedigree_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="血統指数")
    pace_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="展開指数")
    # 状態指数系
    rotation_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="ローテーション指数"
    )
    training_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="調教指数")
    anagusa_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="穴ぐさ指数（sekito.anagusa ピック実績ベースの期待度スコア）"
    )
    paddock_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="パドック指数")
    rebound_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="巻き返し指数（前走不利+着順乖離から次走巻き返し期待度, 中立=50）"
    )
    rivals_growth_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment="上昇相手指数（過去に負かした相手馬の後続活躍度から競走強度を推定, 中立=50）",
    )
    career_phase_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment="成長曲線指数（直近N走のトレンドと馬齢フェーズ, 中立=50）",
    )
    distance_change_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment="距離変更適性指数（延長/短縮パターン別成績, 中立=50）",
    )
    jockey_trainer_combo_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment="騎手×厩舎コンビ指数（コンビ勝率 vs 単独騎手勝率, 中立=50）",
    )
    going_pedigree_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment="重馬場×血統指数（重/不良馬場での父系統適性, 中立=50）",
    )
    disadvantage_flag: Mapped[bool | None] = mapped_column(
        Boolean, default=False, comment="不利フラグ（True:レース中に不利あり）"
    )
    # 総合
    composite_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="総合指数（全指数の加重平均）"
    )
    win_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), comment="単勝確率")
    place_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), comment="複勝確率")
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="指数算出日時"
    )


class EntryChange(Base):
    """出走変更履歴"""

    __tablename__ = "entry_changes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"))
    horse_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    change_type: Mapped[str] = mapped_column(
        String(20), comment="変更種別（scratch/jockey_change/weight_change等）"
    )
    old_value: Mapped[str | None] = mapped_column(String(100), comment="変更前の値")
    new_value: Mapped[str | None] = mapped_column(String(100), comment="変更後の値")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="変更検知日時"
    )
    recalc_triggered: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="再算出実行済みフラグ"
    )


class SpecialRegistration(Base):
    """特別登録馬（出馬表確定前の候補馬リスト）"""

    __tablename__ = "special_registrations"

    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("jravan_race_id", "jravan_horse_code", name="uq_special_reg_race_horse"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jravan_race_id: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False,
        comment="JRA-VANレースID（16文字: YYYYMMDDCCKKDDNN）",
    )
    race_date: Mapped[str] = mapped_column(
        String(8), index=True, nullable=False,
        comment="開催日 YYYYMMDD",
    )
    course_code: Mapped[str] = mapped_column(String(2), nullable=False, comment="場コード")
    race_number: Mapped[int] = mapped_column(Integer, nullable=False, comment="レース番号")
    jravan_horse_code: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="血統登録番号（horses.jravan_code と対応）",
    )
    horse_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="馬名")
    sex: Mapped[str | None] = mapped_column(String(4), comment="性別（牡/牝/騸）")
    age: Mapped[int | None] = mapped_column(Integer, comment="馬齢")
    east_west_code: Mapped[str | None] = mapped_column(String(1), comment="東西所属コード")
    jravan_trainer_code: Mapped[str | None] = mapped_column(String(5), comment="調教師コード")
    trainer_name: Mapped[str | None] = mapped_column(String(50), comment="調教師名略称")
    # TKレコードのデータ区分 (1=新規, 2=変更)
    data_type: Mapped[str | None] = mapped_column(String(1), comment="データ区分")
    # レース名（TKレコードから補完）
    race_name: Mapped[str | None] = mapped_column(String(200), comment="競走名（TKレコード由来）")
    grade_code: Mapped[str | None] = mapped_column(String(1), comment="グレードコード")
    distance: Mapped[int | None] = mapped_column(Integer, comment="距離（m）")
    track_code: Mapped[str | None] = mapped_column(String(2), comment="トラックコード")
    expected_jockey_name: Mapped[str | None] = mapped_column(
        String(50),
        comment="想定騎手名（netkeiba shutuba.html スクレイピング由来。出馬表確定前の参考値）",
    )
    expected_jockey_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="想定騎手取得タイムスタンプ",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="登録日時",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新日時",
    )


class RacecourseFeatures(Base):
    """競馬場コース特徴マスタ"""

    __tablename__ = "racecourse_features"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    course_code: Mapped[str] = mapped_column(
        String(2), unique=True, nullable=False, comment="場コード（races.course と対応）"
    )
    course_name: Mapped[str] = mapped_column(String(20), nullable=False, comment="競馬場名")
    direction: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="回り方向: 1=左回り, -1=右回り"
    )
    straight_distance: Mapped[Decimal] = mapped_column(
        Numeric(6, 1), nullable=False, comment="最終直線距離(m)"
    )
    elevation_diff: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, comment="最終直線高低差(m)"
    )
    circuit_length: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="芝コース1周距離(m)"
    )
    grass_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="芝種別: 洋芝 / 野芝+洋芝"
    )
    corner_tightness: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), comment="コーナーきつさ (0.0=緩〜1.0=急)"
    )
    start_to_corner_m: Mapped[int | None] = mapped_column(
        Integer, comment="スタート〜第1コーナー代表距離(m)"
    )


class NetkeibaRaceExtra(Base):
    """netkeibaスクレイピングデータ（プレミアム会員取得分）"""

    __tablename__ = "netkeiba_race_extras"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_id", name="uq_netkeiba_race_extras_race_horse"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    remarks: Mapped[str | None] = mapped_column(
        String(200), comment="備考（出遅れ・不利・後方一気等の短評テキスト）"
    )
    notable_comment: Mapped[str | None] = mapped_column(
        String(1000), comment="注目馬レース後の短評（プレミアム）"
    )
    race_analysis: Mapped[str | None] = mapped_column(
        String(1000), comment="分析コメント（レース全体の流れ、全馬共通）"
    )
    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OddsHistory(Base):
    """オッズ推移"""

    __tablename__ = "odds_history"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    bet_type: Mapped[str] = mapped_column(
        String(20), comment="賭式（win/place/quinella/trio/trifecta等）"
    )
    combination: Mapped[str] = mapped_column(
        String(50), comment="馬番組み合わせ（単: '3', 連: '3-7', 3連: '3-7-12'）"
    )
    odds: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), comment="オッズ（倍）")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="取得日時"
    )


class User(Base):
    """ユーザーマスタ（Google OAuth）"""

    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, comment="Google subject ID（不変）"
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, comment="メールアドレス"
    )
    name: Mapped[str | None] = mapped_column(String(255), comment="表示名")
    image_url: Mapped[str | None] = mapped_column(String(1024), comment="プロフィール画像URL")
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="member", comment="ロール（member/admin）"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", comment="有効フラグ"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="登録日時"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最終ログイン日時"
    )
    can_input_index: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", comment="TARGET外部指数の投入権限フラグ"
    )
    is_yoso_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", comment="予想を他ユーザーに公開するか"
    )
    yoso_name: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, comment="予想公開時の表示名（予想名）"
    )


class InvitationCode(Base):
    """招待コード"""

    __tablename__ = "invitation_codes"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("code", name="uq_invitation_codes_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="招待コード文字列")
    grant_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="付与種別 (unlimited/weeks/date)"
    )
    weeks_count: Mapped[int | None] = mapped_column(Integer, comment="grant_type=weeks のとき")
    target_date: Mapped[date | None] = mapped_column(Date(), comment="grant_type=date のとき")
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserAccessGrant(Base):
    """ユーザーアクセス付与"""

    __tablename__ = "user_access_grants"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id"), nullable=False, index=True
    )
    grant_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="unlimited/weeks/date"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="NULL=無期限"
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="admin", comment="code/admin"
    )
    source_code_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.invitation_codes.id")
    )
    note: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserPrediction(Base):
    """ユーザー予想（印・指数）"""

    __tablename__ = "user_predictions"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("user_id", "race_id", "horse_id", name="uq_user_predictions_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    horse_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.horses.id", ondelete="CASCADE"), nullable=False
    )
    mark: Mapped[str | None] = mapped_column(String(4), comment="印（◎○▲△×）")
    user_index: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), comment="ユーザー投入指数（TARGET外部指数等）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserImport(Base):
    """ファイル投入ログ"""

    __tablename__ = "user_imports"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    race_date: Mapped[str] = mapped_column(String(8), nullable=False, comment="YYYYMMDD")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    saved_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RaceRecommendation(Base):
    """Claude APIによる推奨レース・馬券（1日5件）"""

    __tablename__ = "race_recommendations"
    __table_args__ = ({"schema": SCHEMA},)  # type: ignore[assignment]

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="開催日 YYYYMMDD")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, comment="推奨順位 1〜5")
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.races.id"), nullable=False, index=True
    )
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="win/place/quinella")
    target_horses: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="推奨馬リスト [{horse_number, horse_name, composite_index, ...}]"
    )
    snapshot_win_odds: Mapped[dict | None] = mapped_column(
        JSONB, comment="スナップショット時点の単勝オッズ {horse_number: odds}"
    )
    snapshot_place_odds: Mapped[dict | None] = mapped_column(
        JSONB, comment="スナップショット時点の複勝オッズ {horse_number: odds}"
    )
    snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="オッズスナップショット取得時刻"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, comment="Claudeによる推奨理由（日本語）")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, comment="推奨信頼スコア 0〜1")
    # レース後に更新
    result_correct: Mapped[bool | None] = mapped_column(Boolean, comment="推奨馬券が的中したか")
    result_payout: Mapped[int | None] = mapped_column(Integer, comment="払戻金額（円/100円購入あたり）")
    result_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserDisplaySetting(Base):
    """他ユーザーの予想表示設定"""

    __tablename__ = "user_display_settings"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint(
            "owner_user_id", "target_user_id", name="uq_user_display_settings_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    show_mark: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", comment="他ユーザーの印を表示するか"
    )
    show_index: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="他ユーザーの指数を表示するか（相手のcan_input_indexも必要）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppSettings(Base):
    """アプリケーション設定テーブル。キーバリュー形式で設定値を管理する。"""

    __tablename__ = "app_settings"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(100), primary_key=True, comment="設定キー（例: PAID_MODE）")
    value: Mapped[str] = mapped_column(String(500), nullable=False, comment="設定値（文字列）")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="最終更新日時",
    )
    updated_by: Mapped[str | None] = mapped_column(String(100), comment="更新者メールアドレス")


class SlopeTraining(Base):
    """坂路調教（HCレコード, SLOP DataSpec / 2003年以降・美浦栗東両方）

    血統登録番号（horses.jravan_code）で馬に紐付く。馬登録前に調教データが
    届く場合があるため FK でなく blood_reg_no をインデックス付き文字列で保持。
    タイムは秒単位（Numeric(4,1)）。測定不良（全ゼロ）の区間は NULL。
    """

    __tablename__ = "slope_training"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint(
            "blood_reg_no", "training_date", "training_time", "center",
            name="uq_slope_training_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    blood_reg_no: Mapped[str] = mapped_column(
        String(10), index=True, comment="血統登録番号（horses.jravan_code と一致）"
    )
    training_date: Mapped[str] = mapped_column(String(8), index=True, comment="調教年月日 YYYYMMDD")
    training_time: Mapped[str | None] = mapped_column(String(4), comment="調教時刻 HHMM")
    center: Mapped[str | None] = mapped_column(String(1), comment="トレセン区分（0:美浦 1:栗東）")
    time_4f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="4ハロン合計タイム（秒）")
    lap_800_600: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 800-600M（秒）")
    time_3f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="3ハロン合計タイム（秒）")
    lap_600_400: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 600-400M（秒）")
    time_2f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="2ハロン合計タイム（秒）")
    lap_400_200: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 400-200M（秒）")
    lap_200_0: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 200-0M（終い1F・秒）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WoodTraining(Base):
    """ウッドチップ調教（WCレコード, WOOD DataSpec / 2021-07-27以降・美浦のみ）

    SlopeTraining と同様に血統登録番号で紐付く。距離ごとの合計タイムと
    区間ラップ（2000M〜終い1F）を保持。測定不良区間は NULL。
    """

    __tablename__ = "wood_training"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint(
            "blood_reg_no", "training_date", "training_time", "center",
            name="uq_wood_training_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    blood_reg_no: Mapped[str] = mapped_column(
        String(10), index=True, comment="血統登録番号（horses.jravan_code と一致）"
    )
    training_date: Mapped[str] = mapped_column(String(8), index=True, comment="調教年月日 YYYYMMDD")
    training_time: Mapped[str | None] = mapped_column(String(4), comment="調教時刻 HHMM")
    center: Mapped[str | None] = mapped_column(String(1), comment="トレセン区分（0:美浦 1:栗東）")
    wood_course: Mapped[str | None] = mapped_column(String(1), comment="コース（0:A〜4:E）")
    wood_direction: Mapped[str | None] = mapped_column(String(1), comment="馬場周り（0:右 1:左）")
    time_10f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="10ハロン合計（秒）")
    lap_2000_1800: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 2000-1800M")
    time_9f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="9ハロン合計（秒）")
    lap_1800_1600: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 1800-1600M")
    time_8f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="8ハロン合計（秒）")
    lap_1600_1400: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 1600-1400M")
    time_7f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="7ハロン合計（秒）")
    lap_1400_1200: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 1400-1200M")
    time_6f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="6ハロン合計（秒）")
    lap_1200_1000: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 1200-1000M")
    time_5f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="5ハロン合計（秒）")
    lap_1000_800: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 1000-800M")
    time_4f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="4ハロン合計（秒）")
    lap_800_600: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 800-600M")
    time_3f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="3ハロン合計（秒）")
    lap_600_400: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 600-400M")
    time_2f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="2ハロン合計（秒）")
    lap_400_200: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 400-200M")
    lap_200_0: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), comment="ラップ 200-0M（終い1F・秒）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
