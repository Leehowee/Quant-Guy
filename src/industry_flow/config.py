import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    dingtalk_webhook: str = os.getenv("DINGTALK_WEBHOOK", "").strip()
    dingtalk_keyword: str = os.getenv("DINGTALK_KEYWORD", "").strip()
    dingtalk_client_id: str = os.getenv("DINGTALK_CLIENT_ID", "").strip()
    dingtalk_client_secret: str = os.getenv("DINGTALK_CLIENT_SECRET", "").strip()
    dingtalk_robot_code: str = os.getenv("DINGTALK_ROBOT_CODE", "").strip()
    dingtalk_open_conversation_id: str = os.getenv(
        "DINGTALK_OPEN_CONVERSATION_ID", ""
    ).strip()
    digest_top_n: int = int(os.getenv("DIGEST_TOP_N", "5"))
    request_retries: int = int(os.getenv("REQUEST_RETRIES", "3"))
    request_retry_sleep: float = float(os.getenv("REQUEST_RETRY_SLEEP", "2"))

    @property
    def raw_daily_dir(self) -> Path:
        return self.data_dir / "raw" / "daily"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def reports_dir(self) -> Path:
        return Path(os.getenv("REPORT_DIR", "./reports"))

    @property
    def dingtalk_app_configured(self) -> bool:
        return any(
            (
                self.dingtalk_client_id,
                self.dingtalk_client_secret,
                self.dingtalk_robot_code,
                self.dingtalk_open_conversation_id,
            )
        )

    @property
    def dingtalk_app_ready(self) -> bool:
        return bool(
            self.dingtalk_client_id
            and self.dingtalk_client_secret
            and (self.dingtalk_robot_code or self.dingtalk_client_id)
            and self.dingtalk_open_conversation_id
        )

    @property
    def history_file(self) -> Path:
        return self.processed_dir / "industry_flow_history.parquet"

    @property
    def feature_file(self) -> Path:
        return self.processed_dir / "industry_flow_features.parquet"

    @property
    def ths_history_file(self) -> Path:
        return self.processed_dir / "industry_flow_ths_periods_history.parquet"

    @property
    def ths_feature_file(self) -> Path:
        return self.processed_dir / "industry_flow_ths_periods_features.parquet"

    @property
    def backfill_progress_file(self) -> Path:
        return self.processed_dir / "backfill_progress.json"

    @property
    def backfill_complete_file(self) -> Path:
        return self.processed_dir / "backfill_complete.json"

    def ensure_dirs(self) -> None:
        self.raw_daily_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
