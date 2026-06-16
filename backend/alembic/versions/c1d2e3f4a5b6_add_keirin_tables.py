"""add keirin schema tables

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # venue_info
    op.create_table(
        "venue_info",
        sa.Column("venue_code", sa.String(10), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("bank_length", sa.Integer()),
        sa.Column("is_indoor", sa.Integer(), server_default="0"),
        sa.Column("prefecture", sa.String(20)),
        schema=SCHEMA,
    )

    # wt_races
    op.create_table(
        "wt_races",
        sa.Column("race_key", sa.String(30), primary_key=True),
        sa.Column("venue_id", sa.String(10), nullable=False),
        sa.Column("race_date", sa.String(10), nullable=False),
        sa.Column("race_no", sa.Integer(), nullable=False),
        sa.Column("cup_id", sa.String(20), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(10)),
        sa.Column("race_type", sa.String(20)),
        sa.Column("distance", sa.Integer()),
        sa.Column("n_entries", sa.Integer()),
        sa.Column("start_at", sa.String(20)),
        sa.Column("status", sa.Integer(), server_default="0"),
        sa.Column("cancel", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_keirin_wt_races_date", "wt_races", ["race_date"], schema=SCHEMA)
    op.create_index("ix_keirin_wt_races_venue", "wt_races", ["venue_id"], schema=SCHEMA)

    # wt_entries
    op.create_table(
        "wt_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("race_key", sa.String(30), nullable=False),
        sa.Column("frame_no", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer()),
        sa.Column("name", sa.String(50)),
        sa.Column("prefecture", sa.String(20)),
        sa.Column("player_class", sa.String(5)),
        sa.Column("term", sa.Integer()),
        sa.Column("gear_ratio", sa.Float()),
        sa.Column("style", sa.String(5)),
        sa.Column("race_point", sa.Float()),
        sa.Column("comment", sa.Text()),
        sa.Column("prediction_mark", sa.Integer()),
        sa.Column("s_count", sa.Integer()),
        sa.Column("h_count", sa.Integer()),
        sa.Column("b_count", sa.Integer()),
        sa.Column("front_runner", sa.Integer()),
        sa.Column("stalker", sa.Integer()),
        sa.Column("deep_closer", sa.Integer()),
        sa.Column("marker", sa.Integer()),
        sa.Column("first_rate", sa.Float()),
        sa.Column("second_rate", sa.Float()),
        sa.Column("third_rate", sa.Float()),
        sa.Column("ex_spurt_pct", sa.Float()),
        sa.Column("ex_thrust_pct", sa.Float()),
        sa.Column("ex_left_behind_pct", sa.Float()),
        sa.Column("ex_split_line_pct", sa.Float()),
        sa.Column("ex_snatch_pct", sa.Float()),
        sa.Column("line_group", sa.Integer()),
        sa.Column("line_size", sa.Integer()),
        sa.Column("line_pos", sa.Integer()),
        sa.Column("is_line_leader", sa.Integer()),
        sa.Column("n_lines", sa.Integer()),
        sa.Column("finish_order", sa.Integer()),
        sa.Column("factor", sa.Text()),
        sa.UniqueConstraint("race_key", "frame_no", name="uq_wt_entries_key_frame"),
        schema=SCHEMA,
    )
    op.create_index("ix_keirin_wt_entries_race", "wt_entries", ["race_key"], schema=SCHEMA)

    # wt_odds
    op.create_table(
        "wt_odds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("race_key", sa.String(30), nullable=False),
        sa.Column("bet_type", sa.String(20), nullable=False),
        sa.Column("combination", sa.String(50), nullable=False),
        sa.Column("odds_value", sa.Float()),
        sa.Column("collected_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("race_key", "bet_type", "combination", name="uq_wt_odds"),
        schema=SCHEMA,
    )
    op.create_index("ix_keirin_wt_odds_race", "wt_odds", ["race_key"], schema=SCHEMA)

    # wt_odds_snapshot
    op.create_table(
        "wt_odds_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("race_key", sa.String(30), nullable=False),
        sa.Column("bet_type", sa.String(20), nullable=False),
        sa.Column("combination", sa.String(50), nullable=False),
        sa.Column("odds_value", sa.Float()),
        sa.Column("snapshot_type", sa.String(20), nullable=False, server_default="morning"),
        sa.Column("snapshot_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "race_key", "bet_type", "combination", "snapshot_type",
            name="uq_wt_odds_snapshot",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_keirin_wt_odds_snap_race", "wt_odds_snapshot", ["race_key"], schema=SCHEMA)

    # wt_weather
    op.create_table(
        "wt_weather",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("venue_id", sa.String(10), nullable=False),
        sa.Column("dt_hour", sa.String(20), nullable=False),
        sa.Column("wind_speed", sa.Float()),
        sa.Column("wind_dir", sa.Float()),
        sa.Column("wind_gust", sa.Float()),
        sa.Column("temp", sa.Float()),
        sa.Column("precip", sa.Float()),
        sa.UniqueConstraint("venue_id", "dt_hour", name="uq_wt_weather"),
        schema=SCHEMA,
    )

    # picks_history
    op.create_table(
        "picks_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("race_date", sa.String(10), nullable=False),
        sa.Column("race_key", sa.String(35), nullable=False),
        sa.Column("rank", sa.String(10), nullable=False),
        sa.Column("pred_combo", sa.Text()),
        sa.Column("n_combos", sa.Integer()),
        sa.Column("hit", sa.Integer(), server_default="0"),
        sa.Column("payout", sa.Integer(), server_default="0"),
        sa.Column("bet_amount", sa.Integer()),
        sa.Column("route", sa.String(10), server_default="ks"),
        sa.UniqueConstraint("race_key", name="uq_picks_history_race_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_keirin_picks_date", "picks_history", ["race_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("picks_history", schema=SCHEMA)
    op.drop_table("wt_weather", schema=SCHEMA)
    op.drop_table("wt_odds_snapshot", schema=SCHEMA)
    op.drop_table("wt_odds", schema=SCHEMA)
    op.drop_table("wt_entries", schema=SCHEMA)
    op.drop_table("wt_races", schema=SCHEMA)
    op.drop_table("venue_info", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
