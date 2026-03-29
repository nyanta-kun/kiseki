"""kiseki 環境設定"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """アプリケーション設定。.envファイルから自動読み込み。"""

    # --- DB ---
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "keiba"
    db_user: str = "keiba_app"
    db_password: str = ""
    db_schema: str = "keiba"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?options=-csearch_path={self.db_schema}"
        )

    # --- JRA-VAN ---
    jravan_sid: str = ""

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_env: str = "development"
    debug: bool = True
    change_notify_api_key: str = ""

    # --- Backend URL (Windows Agent → Mac) ---
    backend_url: str = "http://host.internal:8000"

    # --- Betting Safety ---
    bet_max_per_day: int = 30000
    bet_max_per_race: int = 5000
    bet_max_per_ticket: int = 1000
    bet_min_expected_value: float = 1.20
    bet_max_consecutive_losses: int = 10

    # --- Logging ---
    log_level: str = "INFO"
    log_file: str = "logs/kiseki.log"

    # --- Netkeiba スクレイピング ---
    netkeiba_user_id: str = ""
    netkeiba_password: str = ""

    # --- Auth (Auth.js / NextAuth.js) ---
    auth_secret: str = ""
    auth_password: str = ""
    auth_google_id: str = ""
    auth_google_secret: str = ""
    auth_url: str = ""

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
