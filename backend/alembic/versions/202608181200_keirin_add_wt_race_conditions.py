"""add keirin.wt_race_conditions（走路条件を「朝の予報」と「発走時点の実測」の両建てで持つ）

波乱条件の仮説「普段と違う環境（雨走路・強風）で荒れる」を検証しようとしたところ、
**気象データが一つも無い**ことが分かった（2026-08-18）:

  - `keirin.wt_weather` は **0 行**。書き込み側の `keirin/scripts/collect_weather.py` は
    ローカル SQLite (`data/keirin.db`) にしか書かず、2026-07-22 の PG 一本化で
    取り残されていた

## 🔴 なぜ 2 系統を別々に持つのか（ユーザー指示・2026-08-18）

推奨は**朝**に作る。だが天候は発走までに変わる。したがって

  - **実績の検証**は「発走時点の実測」で見ないと誤った結論になる
  - **予想への投入**は「朝時点で知り得た値」＝予報でないと本番で使えない

この2つを1列に混ぜると、検証では正しく見えるのに配信では欠損する特徴量が
出来上がる（地方競馬 v14 の市場特徴で実際に踏んだ型）。列を分けて両方持つ。

| 系統 | 列 | 出所 | 遡及取得 |
|---|---|---|---|
| 発走時点の実測 | `weather` / `wind_speed` | winticket（競輪場発表） | 可 |
| 朝時点の予報 | `fc_*` | Open-Meteo historical-forecast | 可（2022-12〜） |

## winticket 側の注意

`/v1/keirin/cups/{cupId}` が開催の全レースを返す（1リクエストで最大21レース）。
🔴 **未確定レースは `weather=''` かつ `windSpeed='0.0'`** というプレースホルダを返す。
   そのまま取り込むと「無風の日」を大量に捏造する。`decidedAt` があるレースだけ書く。

## Open-Meteo 側の利点

`wind_direction_10m` が取れる。既存の風検証（G06）は wind_dir を DB から読みながら
**特徴量に入れていなかった**ため、「競輪場×向き×強さ」は一度も検証されていない。

Revision ID: 202608181200_keirin
Revises: 202608161100_shared
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608181200_keirin"
down_revision = "202608161100_shared"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "wt_race_conditions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("race_key", sa.Text(), primary_key=True,
                  comment="wt_races.race_key と同一（YYYYMMDD_VV_RR）"),

        # ── 発走時点の実測（winticket・競輪場発表）────────────────────────
        sa.Column("weather", sa.Text(), nullable=True,
                  comment="発走時点の天候（晴れ/曇り/雨/雪）。未確定レースは NULL"),
        sa.Column("wind_speed", sa.Float(), nullable=True,
                  comment="発走時点の風速 m/s。🔴 未確定の '0.0' を入れないこと"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True,
                  comment="winticket の decidedAt。これが無い行は実測ではない"),

        # ── 朝時点で知り得た予報（Open-Meteo historical-forecast）──────────
        sa.Column("fc_weather_code", sa.Integer(), nullable=True,
                  comment="WMO weather code（発走時刻の時。0=快晴〜95=雷雨）"),
        sa.Column("fc_wind_speed", sa.Float(), nullable=True,
                  comment="予報風速 km/h→m/s 換算済み"),
        sa.Column("fc_wind_dir", sa.Float(), nullable=True,
                  comment="予報風向（度・風が吹いてくる方角）。G06 が落としていた列"),
        sa.Column("fc_precip", sa.Float(), nullable=True,
                  comment="予報降水量 mm/h"),
        sa.Column("fc_source", sa.Text(), nullable=True,
                  comment="予報の出所（open-meteo-hist-forecast / open-meteo-forecast）"),

        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{SCHEMA}_{TABLE}_weather", TABLE, ["weather"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(f"ix_{SCHEMA}_{TABLE}_weather", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
