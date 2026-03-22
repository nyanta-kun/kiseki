"""kiseki データベースモデル定義"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .session import Base

SCHEMA = "keiba"


class Horse(Base):
    """馬マスタ"""

    __tablename__ = "horses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sex: Mapped[str] = mapped_column(String(10))  # 牡/牝/セ
    birthday: Mapped[str] = mapped_column(String(8))  # YYYYMMDD
    coat_color: Mapped[str | None] = mapped_column(String(20))
    owner: Mapped[str | None] = mapped_column(String(100))
    breeder: Mapped[str | None] = mapped_column(String(100))
    jravan_code: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Pedigree(Base):
    """血統情報"""

    __tablename__ = "pedigrees"
    __table_args__ = (
        UniqueConstraint("horse_id", name="uq_pedigree_horse_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"), unique=True, index=True)
    sire: Mapped[str | None] = mapped_column(String(100))  # 父
    dam: Mapped[str | None] = mapped_column(String(100))  # 母
    sire_of_dam: Mapped[str | None] = mapped_column(String(100))  # 母父
    sire_line: Mapped[str | None] = mapped_column(String(50))  # 父系統
    dam_sire_line: Mapped[str | None] = mapped_column(String(50))  # 母父系統


class Jockey(Base):
    """騎手マスタ"""

    __tablename__ = "jockeys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    jravan_code: Mapped[str | None] = mapped_column(String(10), unique=True, index=True)


class Trainer(Base):
    """調教師マスタ"""

    __tablename__ = "trainers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    jravan_code: Mapped[str | None] = mapped_column(String(10), unique=True, index=True)


class Race(Base):
    """レースマスタ"""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # YYYYMMDD
    course: Mapped[str] = mapped_column(String(10), nullable=False)  # 競馬場コード
    course_name: Mapped[str] = mapped_column(String(20))  # 東京/中山/阪神...
    race_number: Mapped[int] = mapped_column(Integer, nullable=False)
    race_name: Mapped[str | None] = mapped_column(String(100))
    surface: Mapped[str] = mapped_column(String(5))  # 芝/ダ
    distance: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str | None] = mapped_column(String(5))  # 右/左
    track_type: Mapped[str | None] = mapped_column(String(10))  # 内/外
    condition: Mapped[str | None] = mapped_column(String(5))  # 良/稍/重/不
    weather: Mapped[str | None] = mapped_column(String(10))
    grade: Mapped[str | None] = mapped_column(String(10))  # G1/G2/G3/OP/条件等
    head_count: Mapped[int | None] = mapped_column(Integer)
    jravan_race_id: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RaceEntry(Base):
    """出馬表"""

    __tablename__ = "race_entries"

    __table_args__ = (
        UniqueConstraint("race_id", "horse_number", name="uq_race_entry_horse_num"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    frame_number: Mapped[int] = mapped_column(Integer)  # 枠番
    horse_number: Mapped[int] = mapped_column(Integer)  # 馬番
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.jockeys.id"))
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.trainers.id"))
    weight_carried: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))  # 斤量
    horse_weight: Mapped[int | None] = mapped_column(Integer)  # 馬体重
    weight_change: Mapped[int | None] = mapped_column(Integer)  # 馬体重増減


class RaceResult(Base):
    """レース結果"""

    __tablename__ = "race_results"
    __table_args__ = (
        UniqueConstraint("race_id", "horse_id", name="uq_race_result_horse"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    entry_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.race_entries.id"))
    finish_position: Mapped[int | None] = mapped_column(Integer)
    frame_number: Mapped[int | None] = mapped_column(Integer)
    horse_number: Mapped[int | None] = mapped_column(Integer)
    jockey_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.jockeys.id"))
    weight_carried: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    horse_weight: Mapped[int | None] = mapped_column(Integer)
    weight_change: Mapped[int | None] = mapped_column(Integer)
    finish_time: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))  # 秒×10
    margin: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    passing_1: Mapped[int | None] = mapped_column(Integer)
    passing_2: Mapped[int | None] = mapped_column(Integer)
    passing_3: Mapped[int | None] = mapped_column(Integer)
    passing_4: Mapped[int | None] = mapped_column(Integer)
    last_3f: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))  # 上がり3F
    abnormality_code: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TrackCondition(Base):
    """馬場差データ"""

    __tablename__ = "track_conditions"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(8), index=True)
    course: Mapped[str] = mapped_column(String(10))
    surface: Mapped[str] = mapped_column(String(5))
    distance: Mapped[int] = mapped_column(Integer)
    condition: Mapped[str | None] = mapped_column(String(5))
    bias_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class CalculatedIndex(Base):
    """算出指数"""

    __tablename__ = "calculated_indices"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    horse_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # スピード指数系
    speed_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    adjusted_speed_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    last_3f_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    # 適性指数系
    course_aptitude: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    distance_aptitude: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    position_advantage: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    # 能力指数系
    jockey_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    trainer_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    pedigree_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    pace_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    # 状態指数系
    rotation_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    training_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    paddock_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    disadvantage_flag: Mapped[bool | None] = mapped_column(Boolean, default=False)
    # 総合
    composite_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    win_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    place_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EntryChange(Base):
    """出走変更履歴"""

    __tablename__ = "entry_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"))
    horse_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.horses.id"))
    change_type: Mapped[str] = mapped_column(String(20))  # scratch/jockey_change/...
    old_value: Mapped[str | None] = mapped_column(String(100))
    new_value: Mapped[str | None] = mapped_column(String(100))
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    recalc_triggered: Mapped[bool] = mapped_column(Boolean, default=False)


class OddsHistory(Base):
    """オッズ推移"""

    __tablename__ = "odds_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.races.id"), index=True)
    bet_type: Mapped[str] = mapped_column(String(20))  # win/place/quinella/trio/trifecta
    combination: Mapped[str] = mapped_column(String(50))  # 馬番の組み合わせ "3" or "3-7" or "3-7-12"
    odds: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
