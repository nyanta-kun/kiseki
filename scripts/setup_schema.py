"""
VPS PostgreSQLにkeibaスキーマと専用ユーザーを作成するスクリプト

使い方:
  # 管理者権限のあるDB接続情報で実行
  python scripts/setup_schema.py --admin-url postgresql://admin:password@host:5432/dbname
"""

import argparse

from sqlalchemy import create_engine, text


def setup_schema(admin_url: str, app_password: str):
    """keibaスキーマと専用ユーザーを作成"""
    engine = create_engine(admin_url)

    with engine.connect() as conn:
        # スキーマ作成
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS keiba"))
        print("✓ Schema 'keiba' created")

        # 専用ユーザー作成（既に存在する場合はスキップ）
        result = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'keiba_app'")
        )
        if result.fetchone() is None:
            conn.execute(
                text(f"CREATE USER keiba_app WITH PASSWORD '{app_password}'")
            )
            print("✓ User 'keiba_app' created")
        else:
            print("✓ User 'keiba_app' already exists")

        # 権限付与
        conn.execute(text("GRANT USAGE ON SCHEMA keiba TO keiba_app"))
        conn.execute(
            text("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA keiba TO keiba_app")
        )
        conn.execute(
            text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA keiba "
                "GRANT ALL ON TABLES TO keiba_app"
            )
        )
        conn.execute(
            text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA keiba "
                "GRANT ALL ON SEQUENCES TO keiba_app"
            )
        )
        conn.execute(text("ALTER USER keiba_app SET search_path TO keiba, public"))
        print("✓ Privileges granted")

        conn.commit()

    print("\n=== Setup complete ===")
    print(f"DB URL for .env: postgresql://keiba_app:{app_password}@<host>:5432/<dbname>")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup keiba schema on VPS PostgreSQL")
    parser.add_argument("--admin-url", required=True, help="Admin DB connection URL")
    parser.add_argument(
        "--app-password", required=True, help="Password for keiba_app user"
    )
    args = parser.parse_args()
    setup_schema(args.admin_url, args.app_password)
