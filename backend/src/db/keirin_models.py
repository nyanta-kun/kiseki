"""競輪データベースモデル定義（keirin スキーマ）"""

from datetime import datetime

from sqlalchemy import (
    REAL,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

KEIRIN_SCHEMA = "keirin"


class KeirinBase(DeclarativeBase):
    """SQLAlchemy ベースクラス（schema='keirin'）"""

    __table_args__ = {"schema": KEIRIN_SCHEMA}


class KeirinVenueInfo(KeirinBase):
    """競輪場マスタ"""

    __tablename__ = "venue_info"
    __table_args__ = {"schema": KEIRIN_SCHEMA}

    venue_code: Mapped[str] = mapped_column(String(10), primary_key=True, comment="場コード")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="場名")
    bank_length: Mapped[int | None] = mapped_column(Integer, comment="バンク長（m）")
    is_indoor: Mapped[int] = mapped_column(Integer, default=0, comment="屋内フラグ")
    prefecture: Mapped[str | None] = mapped_column(String(20), comment="都道府県")


class KeirinWtRace(KeirinBase):
    """winticket レース情報"""

    __tablename__ = "wt_races"
    __table_args__ = {"schema": KEIRIN_SCHEMA}

    race_key: Mapped[str] = mapped_column(String(30), primary_key=True, comment="レースキー(YYYYMMDD_VID_RNO)")
    venue_id: Mapped[str] = mapped_column(String(10), nullable=False, comment="会場ID")
    race_date: Mapped[str] = mapped_column(String(10), nullable=False, comment="開催日(YYYY-MM-DD)")
    race_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="レース番号")
    cup_id: Mapped[str] = mapped_column(String(20), nullable=False, comment="カップID")
    day_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="開催日次")
    grade: Mapped[str | None] = mapped_column(String(10), comment="グレード")
    race_type: Mapped[str | None] = mapped_column(String(20), comment="レース種別")
    distance: Mapped[int | None] = mapped_column(Integer, comment="距離(m)")
    n_entries: Mapped[int | None] = mapped_column(Integer, comment="出走頭数")
    start_at: Mapped[str | None] = mapped_column(String(20), comment="発走時刻(UNIX timestamp文字列)")
    status: Mapped[int] = mapped_column(Integer, default=0, comment="レースステータス")
    cancel: Mapped[int] = mapped_column(Integer, default=0, comment="中止フラグ")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="収集日時"
    )


class KeirinWtEntry(KeirinBase):
    """winticket 出走表エントリー"""

    __tablename__ = "wt_entries"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_key", "frame_no", name="uq_wt_entries_key_frame"),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_key: Mapped[str] = mapped_column(String(30), nullable=False)
    frame_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="車番")
    player_id: Mapped[int | None] = mapped_column(Integer, comment="選手登録番号")
    name: Mapped[str | None] = mapped_column(String(50), comment="選手名")
    prefecture: Mapped[str | None] = mapped_column(String(20), comment="都道府県")
    player_class: Mapped[str | None] = mapped_column(String(5), comment="選手クラス")
    term: Mapped[int | None] = mapped_column(Integer, comment="期")
    gear_ratio: Mapped[float | None] = mapped_column(Float, comment="ギア比")
    style: Mapped[str | None] = mapped_column(String(5), comment="戦法")
    race_point: Mapped[float | None] = mapped_column(Float, comment="競走得点（指数）")
    comment: Mapped[str | None] = mapped_column(Text, comment="選手コメント")
    prediction_mark: Mapped[int | None] = mapped_column(Integer, comment="予想印")
    s_count: Mapped[int | None] = mapped_column(Integer)
    h_count: Mapped[int | None] = mapped_column(Integer)
    b_count: Mapped[int | None] = mapped_column(Integer)
    front_runner: Mapped[int | None] = mapped_column(Integer)
    stalker: Mapped[int | None] = mapped_column(Integer)
    deep_closer: Mapped[int | None] = mapped_column(Integer)
    marker: Mapped[int | None] = mapped_column(Integer)
    first_rate: Mapped[float | None] = mapped_column(Float)
    second_rate: Mapped[float | None] = mapped_column(Float)
    third_rate: Mapped[float | None] = mapped_column(Float)
    ex_spurt_pct: Mapped[float | None] = mapped_column(Float)
    ex_thrust_pct: Mapped[float | None] = mapped_column(Float)
    ex_left_behind_pct: Mapped[float | None] = mapped_column(Float)
    ex_split_line_pct: Mapped[float | None] = mapped_column(Float)
    ex_snatch_pct: Mapped[float | None] = mapped_column(Float)
    line_group: Mapped[int | None] = mapped_column(Integer, comment="ライングループID")
    line_size: Mapped[int | None] = mapped_column(Integer, comment="ラインサイズ")
    line_pos: Mapped[int | None] = mapped_column(Integer, comment="ライン内ポジション")
    is_line_leader: Mapped[int | None] = mapped_column(Integer, comment="先頭フラグ")
    n_lines: Mapped[int | None] = mapped_column(Integer, comment="ライン数")
    finish_order: Mapped[int | None] = mapped_column(Integer, comment="着順(0=欠車/失格)")
    factor: Mapped[str | None] = mapped_column(Text, comment="着因")
    res_standing: Mapped[int | None] = mapped_column(
        Integer, comment="このレースでS（スタンディング先頭）を取ったか（0/1・結果確定後に記録）"
    )
    res_back: Mapped[int | None] = mapped_column(
        Integer, comment="このレースでB（バック先頭）を取ったか（0/1・結果確定後に記録）"
    )
    final_half: Mapped[float | None] = mapped_column(REAL, comment="上がりタイム（秒）")


class KeirinWtOdds(KeirinBase):
    """winticket オッズ（最終）"""

    __tablename__ = "wt_odds"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_key", "bet_type", "combination", name="uq_wt_odds"),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_key: Mapped[str] = mapped_column(String(30), nullable=False)
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="賭式(win/trio/trifecta等)")
    combination: Mapped[str] = mapped_column(String(50), nullable=False, comment="組み合わせJSON文字列")
    odds_value: Mapped[float | None] = mapped_column(Float, comment="オッズ")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="収集日時"
    )


class KeirinWtOddsSnapshot(KeirinBase):
    """winticket オッズスナップショット（朝・宵等）"""

    __tablename__ = "wt_odds_snapshot"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint(
            "race_key", "bet_type", "combination", "snapshot_type",
            name="uq_wt_odds_snapshot",
        ),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_key: Mapped[str] = mapped_column(String(30), nullable=False)
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False)
    combination: Mapped[str] = mapped_column(String(50), nullable=False)
    odds_value: Mapped[float | None] = mapped_column(Float)
    snapshot_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="morning/evening等")
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="スナップショット取得日時"
    )


class KeirinWtWeather(KeirinBase):
    """競輪場の気象データ（Open-Meteo 由来）"""

    __tablename__ = "wt_weather"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("venue_id", "dt_hour", name="uq_wt_weather"),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[str] = mapped_column(String(10), nullable=False, comment="場ID")
    dt_hour: Mapped[str] = mapped_column(String(20), nullable=False, comment="YYYY-MM-DD HH:00 JST")
    wind_speed: Mapped[float | None] = mapped_column(Float, comment="風速(m/s)")
    wind_dir: Mapped[float | None] = mapped_column(Float, comment="風向(度)")
    wind_gust: Mapped[float | None] = mapped_column(Float, comment="突風(m/s)")
    temp: Mapped[float | None] = mapped_column(Float, comment="気温(℃)")
    precip: Mapped[float | None] = mapped_column(Float, comment="降水量(mm)")


class KeirinPicksHistory(KeirinBase):
    """競輪 AI ピック履歴・実績"""

    __tablename__ = "picks_history"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("race_key", name="uq_picks_history_race_key"),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    race_date: Mapped[str] = mapped_column(String(10), nullable=False, comment="開催日")
    race_key: Mapped[str] = mapped_column(String(35), nullable=False, comment="レースキー(base#CAND/#7R/#7ST等)")
    rank: Mapped[str] = mapped_column(String(10), nullable=False, comment="ランク(7PLUS_R/7PLUS_ST/7PLUS_STP/7PLUS_CAND)")
    pred_combo: Mapped[str | None] = mapped_column(Text, comment="買い目文字列")
    n_combos: Mapped[int | None] = mapped_column(Integer, comment="点数")
    hit: Mapped[int] = mapped_column(Integer, default=0, comment="的中フラグ")
    payout: Mapped[int] = mapped_column(Integer, default=0, comment="払戻金額(円)")
    trio_payout: Mapped[int | None] = mapped_column(Integer, comment="三連複払戻(参考値・migration g3h4i5j6k7l8)")
    trifecta_payout: Mapped[int | None] = mapped_column(Integer, comment="三連単払戻(参考値・migration h4i5j6k7l8m9)")
    bet_amount: Mapped[int | None] = mapped_column(Integer, comment="投資金額(円)")
    route: Mapped[str] = mapped_column(String(10), default="ks", comment="データソース(ks/wt)")
    miwokuri: Mapped[bool | None] = mapped_column(Boolean, comment="見送りフラグ(migration d2e3f4a5b6c7)")
    prerace_gami: Mapped[float | None] = mapped_column(Numeric(6, 2), comment="発走15分前の三連複最安オッズ(閾値7.0以上=ガミOK/未満=条件落ち/NULL=未チェック)")
    gap12: Mapped[float | None] = mapped_column(Numeric(6, 4), comment="指数1-2位の予測確率差(0-1スケール・migration i5j6k7l8m9n0)")
    gap23: Mapped[float | None] = mapped_column(Numeric(8, 4), comment="指数2-3位の予測確率差(★ptスケール=×100済み・migration j6k7l8m9n0p1)")
    gap34: Mapped[float | None] = mapped_column(Numeric(6, 4), comment="指数3-4位の予測確率差(0-1スケール・migration i5j6k7l8m9n0)")


class KeirinModelEvaluation(KeirinBase):
    """モデル・戦略バックテスト評価（save_model_eval.py が書込・summary API が参照）"""

    __tablename__ = "model_evaluation"
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("model_name", "period_type", name="uq_model_eval_model_period"),
        {"schema": KEIRIN_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="モデル名(ランク別は #7R/#7ST/#7STP サフィックス)")
    period_from: Mapped[str] = mapped_column(String(10), nullable=False, comment="期間開始")
    period_to: Mapped[str] = mapped_column(String(10), nullable=False, comment="期間終了")
    period_type: Mapped[str] = mapped_column(String(10), nullable=False, comment="VAL/HOLD")
    n_picks: Mapped[int] = mapped_column(Integer, nullable=False)
    n_hits: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bet: Mapped[int] = mapped_column(Integer, nullable=False)
    total_payout: Mapped[int] = mapped_column(Integer, nullable=False)
    roi: Mapped[float | None] = mapped_column(Numeric(6, 3))
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="評価実行日時"
    )
