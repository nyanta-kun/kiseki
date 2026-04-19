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
