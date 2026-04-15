"""地方競馬データベースモデル定義（chihou スキーマ）"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CHIHOU_SCHEMA = "chihou"


class ChihouBase(DeclarativeBase):
    """SQLAlchemy ベースクラス（schema='chihou'）"""

    __table_args__ = {"schema": CHIHOU_SCHEMA}


class ChihouHorse(ChihouBase):
    """地方競馬 馬マスタ"""

    __tablename__ = "horses"
    __table_args__ = {"schema": CHIHOU_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="馬名")
    sex: Mapped[str] = mapped_column(String(10), comment="性別（牡/牝/セン）")
    birthday: Mapped[str] = mapped_column(String(8), comment="生年月日（YYYYMMDD）")
    coat_color: Mapped[str | None] = mapped_column(String(20), comment="毛色")
    owner: Mapped[str | None] = mapped_column(String(100), comment="馬主名")
    breeder: Mapped[str | None] = mapped_column(String(100), comment="生産者名")
    umaconn_code: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, comment="UmaConn馬コード"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="レコード作成日時"
    )


class ChihouJockey(ChihouBase):
    """地方競馬 騎手マスタ"""

    __tablename__ = "jockeys"
    __table_args__ = {"schema": CHIHOU_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="騎手名")
    umaconn_code: Mapped[str | None] = mapped_column(
        String(10), unique=True, index=True, comment="UmaConn騎手コード"
    )


class ChihouTrainer(ChihouBase):
    """地方競馬 調教師マスタ"""

    __tablename__ = "trainers"
    __table_args__ = {"schema": CHIHOU_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="調教師名")
    umaconn_code: Mapped[str | None] = mapped_column(
        String(10), unique=True, index=True, comment="UmaConn調教師コード"
    )


class ChihouRace(ChihouBase):
    """地方競馬 レースマスタ"""

    __tablename__ = "races"
    __table_args__ = {"schema": CHIHOU_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(
        String(8), nullable=False, index=True, comment="開催日（YYYYMMDD）"
    )
    course: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="競馬場コード",
    )
    course_name: Mapped[str] = mapped_column(String(20), comment="競馬場名")
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
        comment="競走種別コード",
    )
    weight_type_code: Mapped[str | None] = mapped_column(
        String(1), comment="重量種別コード（1=馬齢,2=定量,3=別定,4=ハンデ）"
    )
    prize_1st: Mapped[int | None] = mapped_column(Integer, comment="1着本賞金（百円単位）")
    prize_2nd: Mapped[int | None] = mapped_column(Integer, comment="2着本賞金（百円単位）")
    prize_3rd: Mapped[int | None] = mapped_column(Integer, comment="3着本賞金（百円単位）")
    first_3f: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="前3ハロン通過タイム（秒）"
    )
    last_3f_race: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="レース後3ハロンタイム（秒）"
    )
    lap_times: Mapped[str | None] = mapped_column(
        String(75), comment="ラップタイム生データ"
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
    umaconn_race_id: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
        index=True,
        comment="UmaConnレースID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="レコード作成日時"
    )


class ChihouRaceEntry(ChihouBase):
    """地方競馬 出馬表"""

    __tablename__ = "race_entries"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_number", name="uq_chihou_race_entry_horse_num"),
        {"schema": CHIHOU_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id"), index=True
    )
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{CHIHOU_SCHEMA}.horses.id"))
    frame_number: Mapped[int] = mapped_column(Integer, comment="枠番（1-8）")
    horse_number: Mapped[int] = mapped_column(Integer, comment="馬番（1-18）")
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{CHIHOU_SCHEMA}.jockeys.id"))
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey(f"{CHIHOU_SCHEMA}.trainers.id"))
    weight_carried: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="負担重量（kg）")
    horse_weight: Mapped[int | None] = mapped_column(
        Integer, comment="馬体重（kg、計不明時はNone）"
    )
    weight_change: Mapped[int | None] = mapped_column(Integer, comment="馬体重増減（kg、符号付き）")
    horse_age: Mapped[int | None] = mapped_column(Integer, comment="馬齢（満年齢）")
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


class ChihouRaceResult(ChihouBase):
    """地方競馬 レース結果"""

    __tablename__ = "race_results"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_id", name="uq_chihou_race_result_horse"),
        {"schema": CHIHOU_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id"), index=True
    )
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{CHIHOU_SCHEMA}.horses.id"))
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.race_entries.id")
    )
    finish_position: Mapped[int | None] = mapped_column(Integer, comment="確定着順")
    frame_number: Mapped[int | None] = mapped_column(Integer, comment="枠番（1-8）")
    horse_number: Mapped[int | None] = mapped_column(Integer, comment="馬番（1-18）")
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{CHIHOU_SCHEMA}.jockeys.id"))
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
        String(3), comment="着差コード"
    )
    win_odds: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), comment="確定単勝オッズ（倍）"
    )
    win_popularity: Mapped[int | None] = mapped_column(
        Integer, comment="単勝人気順位（1位が最低オッズ）"
    )
    prize_money: Mapped[int | None] = mapped_column(Integer, comment="獲得本賞金（百円単位）")
    last_4f: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), comment="後4ハロンタイム（秒）")
    time_diff: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), comment="1着とのタイム差（秒）"
    )
    running_style: Mapped[str | None] = mapped_column(
        String(1), comment="脚質（1:逃,2:先,3:差,4:追）"
    )
    place_odds: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1),
        comment="複勝確定払戻倍率（100円あたり払戻÷100）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChihouRacePayout(ChihouBase):
    """地方競馬 払戻情報"""

    __tablename__ = "race_payouts"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint(
            "race_id", "bet_type", "combination", name="uq_chihou_race_payouts_race_type_combo"
        ),
        {"schema": CHIHOU_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id"), index=True, comment="races テーブルの id"
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


class ChihouOddsHistory(ChihouBase):
    """地方競馬 オッズ推移"""

    __tablename__ = "odds_history"
    __table_args__ = {"schema": CHIHOU_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id"), index=True
    )
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


class ChihouCalculatedIndex(ChihouBase):
    """地方競馬 算出指数"""

    __tablename__ = "calculated_indices"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_id", "horse_id", "version", name="uq_chihou_calc_idx_race_horse_ver"),
        {"schema": CHIHOU_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="chihou.races.id",
    )
    horse_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.horses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="chihou.horses.id",
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="計算ロジックバージョン")
    speed_index: Mapped[float | None] = mapped_column(comment="スピード指数（0-100, 平均50）")
    last3f_index: Mapped[float | None] = mapped_column(comment="後3ハロン指数（0-100, 平均50）")
    jockey_index: Mapped[float | None] = mapped_column(comment="騎手指数（0-100, 平均50）")
    rotation_index: Mapped[float | None] = mapped_column(comment="ローテーション指数（0-100）")
    last_margin_index: Mapped[float | None] = mapped_column(
        comment="前走着差指数（0-100, 前走タイム差が小さいほど高評価, 接戦=高評価）"
    )
    composite_index: Mapped[float | None] = mapped_column(comment="総合指数（0-100）")
    win_probability: Mapped[float | None] = mapped_column(comment="推定単勝確率（0-1）")
    place_probability: Mapped[float | None] = mapped_column(comment="推定複勝確率（0-1）")
    place_ev_index: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        comment=(
            "複勝期待値指数（place_probability × estimated_place_odds, "
            "EV=1.0→50, EV>1.0で期待値プラス, 中立=50）"
        ),
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="算出日時"
    )


class ChihouPedigree(ChihouBase):
    """地方競馬 血統情報"""

    __tablename__ = "pedigrees"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("horse_id", name="uq_chihou_pedigree_horse_id"),
        {"schema": CHIHOU_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    horse_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.horses.id"), unique=True, index=True
    )
    sire: Mapped[str | None] = mapped_column(String(100), comment="父馬名")
    dam: Mapped[str | None] = mapped_column(String(100), comment="母馬名")
    sire_of_dam: Mapped[str | None] = mapped_column(String(100), comment="母父馬名")
    sire_line: Mapped[str | None] = mapped_column(String(50), comment="父系統名")
    dam_sire_line: Mapped[str | None] = mapped_column(String(50), comment="母父系統名")


class ChihouRaceRecommendation(ChihouBase):
    """Claude APIによる地方競馬推奨レース・馬券（1日最大5件）"""

    __tablename__ = "race_recommendations"
    __table_args__ = ({"schema": CHIHOU_SCHEMA},)  # type: ignore[assignment]

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="開催日 YYYYMMDD")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, comment="推奨順位 1〜5")
    race_id: Mapped[int] = mapped_column(
        ForeignKey(f"{CHIHOU_SCHEMA}.races.id"), nullable=False, index=True
    )
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="win/place")
    target_horses: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="推奨馬リスト [{horse_number, horse_name, composite_index, ...}]"
    )
    # 10分前オッズスナップショット（生成時は null）
    snapshot_win_odds: Mapped[dict | None] = mapped_column(JSONB, comment="単勝オッズスナップショット")
    snapshot_place_odds: Mapped[dict | None] = mapped_column(JSONB, comment="複勝オッズスナップショット")
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False, comment="Claude推奨理由")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, comment="推奨信頼スコア 0〜1")
    # 発走10分前のオッズ購入判断
    odds_decision: Mapped[str | None] = mapped_column(String(10), comment="'buy' | 'pass' | null=未判断")
    odds_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    odds_decision_reason: Mapped[str | None] = mapped_column(Text, comment="判断理由テキスト")
    # レース後に更新
    result_correct: Mapped[bool | None] = mapped_column(Boolean)
    result_payout: Mapped[int | None] = mapped_column(Integer, comment="払戻金額（円/100円）")
    result_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
