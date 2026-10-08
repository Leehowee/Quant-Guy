import json
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _project_path(value: str | Path, default: str) -> Path:
    path = Path(value or default).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


_source_config_path = Path(
    os.getenv("DATA_SOURCE_CONFIG", str(PROJECT_ROOT / "config" / "data_sources.json"))
).expanduser()
if not _source_config_path.is_absolute():
    _source_config_path = PROJECT_ROOT / _source_config_path


def _load_data_sources() -> dict:
    try:
        with _source_config_path.open("r", encoding="utf-8") as source_file:
            value = json.load(source_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load data source configuration: {_source_config_path}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(
            f"Data source configuration must be a JSON object: {_source_config_path}"
        )
    return value


data_sources = _load_data_sources()


def resolve_akshare_api(api_name: str):
    try:
        import akshare as ak

        api = getattr(ak, api_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f"AKShare API is not available: {api_name}") from exc
    if not callable(api):
        raise RuntimeError(f"Configured AKShare attribute is not callable: {api_name}")
    return api


@dataclass(frozen=True)
class Settings:
    data_dir: Path = _project_path(os.getenv("DATA_DIR", "./data"), "./data")
    dingtalk_webhook: str = os.getenv("DINGTALK_WEBHOOK", "").strip()
    dingtalk_keyword: str = os.getenv("DINGTALK_KEYWORD", "").strip()
    dingtalk_client_id: str = os.getenv("DINGTALK_CLIENT_ID", "").strip()
    dingtalk_client_secret: str = os.getenv("DINGTALK_CLIENT_SECRET", "").strip()
    dingtalk_robot_code: str = os.getenv("DINGTALK_ROBOT_CODE", "").strip()
    dingtalk_open_conversation_id: str = os.getenv(
        "DINGTALK_OPEN_CONVERSATION_ID", ""
    ).strip()
    openclaw_channel: str = os.getenv("OPENCLAW_CHANNEL", "").strip()
    openclaw_target: str = os.getenv("OPENCLAW_TARGET", "").strip()
    openclaw_account: str = os.getenv("OPENCLAW_ACCOUNT", "").strip()
    openclaw_bin: str = os.getenv("OPENCLAW_BIN", "openclaw").strip() or "openclaw"
    openclaw_timeout: int = int(os.getenv("OPENCLAW_TIMEOUT", "90"))
    openclaw_send_image: bool = os.getenv(
        "OPENCLAW_SEND_IMAGE", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
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
        return _project_path(os.getenv("REPORT_DIR", "./reports"), "./reports")

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
    def openclaw_configured(self) -> bool:
        return bool(self.openclaw_channel or self.openclaw_target)

    @property
    def openclaw_ready(self) -> bool:
        return bool(self.openclaw_channel and self.openclaw_target)

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
