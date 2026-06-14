from __future__ import annotations

import os
from dataclasses import dataclass
import json
from pathlib import Path

DEFAULT_MODEL_PROVIDER = "huggingface"
DEFAULT_MODEL_ID = "HuggingFaceTB/SmolLM2-1.7B-Instruct"
DEFAULT_PARSE_MODEL_PROVIDER = "package"
DEFAULT_PARSE_MODEL_ID = "HuggingFaceTB/SmolVLM-256M-Instruct"
DEFAULT_AGENT_PROVIDER = "native"
DEFAULT_INSIGHTS_PROVIDER = "native"


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _find_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parents[1]


def _load_env_file(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _default_local_data_dir(repo_root: Path) -> Path:
    # Vercel functions run on a read-only filesystem except for /tmp.
    if os.getenv("VERCEL"):
        return Path("/tmp") / "soterra-backend"
    return repo_root / "artifacts" / "backend"


def _default_process_inline() -> bool:
    # FastAPI BackgroundTasks are not a durable extraction queue. Complete upload
    # extraction before responding so failed files return a clear 422.
    return True


def _default_model_provider() -> str:
    return DEFAULT_MODEL_PROVIDER


def _default_parse_provider() -> str:
    return DEFAULT_PARSE_MODEL_PROVIDER


def _default_agent_provider() -> str:
    return DEFAULT_AGENT_PROVIDER


@dataclass(frozen=True)
class ModelExtractionConfig:
    provider: str
    model_id: str
    name: str | None = None


@dataclass(frozen=True)
class Settings:
    app_env: str
    enable_docs: bool
    repo_root: Path
    storage_mode: str
    repository_mode: str
    extractor_mode: str
    package_extractor: str
    model_extractor: str
    allow_model_extraction: bool
    soterra_extraction_provider: str
    soterra_extraction_model_id: str
    soterra_document_parse_provider: str
    soterra_document_parse_model_id: str
    document_parse_max_pages: int
    document_parse_max_new_tokens: int
    document_parse_text_in_pictures: bool
    soterra_agent_provider: str
    soterra_agent_model_id: str
    soterra_insights_provider: str
    model_extraction_temperature: float
    model_extraction_max_findings: int
    model_extraction_timeout_seconds: int
    extraction_timeout_seconds: int
    model_extraction_retry_count: int
    model_extraction_models: list[ModelExtractionConfig]
    process_inline: bool
    local_data_dir: Path
    local_db_path: Path
    local_storage_dir: Path
    package_max_pages: int
    package_ocr_enabled: bool
    package_ocr_max_pages: int
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_bucket: str
    auth_session_ttl_hours: int
    max_upload_bytes: int
    max_bulk_upload_bytes: int
    max_bulk_file_count: int
    max_upload_pages: int
    max_reports_per_tenant: int
    upload_rate_limit_per_hour: int
    bootstrap_demo_account: bool
    demo_admin_password: str | None
    app_base_url: str
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from_email: str
    smtp_from_name: str
    smtp_use_tls: bool

    @classmethod
    def from_env(cls) -> "Settings":
        repo_root = _find_project_root()
        _load_env_file(repo_root)
        local_data_dir = _default_local_data_dir(repo_root)

        supabase_url = os.getenv("SUPABASE_URL")
        supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        app_env = os.getenv("SOTERRA_ENV", "production" if os.getenv("VERCEL") else "development").strip().lower()

        repository_mode = os.getenv(
            "SOTERRA_REPOSITORY_MODE",
            "supabase" if supabase_url and supabase_service_role_key else "sqlite",
        ).strip()
        storage_mode = os.getenv(
            "SOTERRA_STORAGE_MODE",
            "supabase" if supabase_url and supabase_service_role_key else "local",
        ).strip()
        extractor_mode = os.getenv(
            "SOTERRA_EXTRACTOR_MODE",
            "package",
        ).strip()
        extraction_provider = os.getenv("SOTERRA_EXTRACTION_PROVIDER", _default_model_provider()).strip()
        default_model_id = DEFAULT_MODEL_ID
        extraction_model_id = os.getenv("SOTERRA_EXTRACTION_MODEL_ID", default_model_id).strip()
        document_parse_provider = os.getenv("SOTERRA_DOCUMENT_PARSE_PROVIDER", _default_parse_provider()).strip()
        document_parse_model_id = os.getenv("SOTERRA_DOCUMENT_PARSE_MODEL_ID", DEFAULT_PARSE_MODEL_ID).strip()
        agent_provider = os.getenv("SOTERRA_AGENT_PROVIDER", _default_agent_provider()).strip()
        agent_model_id = os.getenv(
            "SOTERRA_AGENT_MODEL_ID",
            default_model_id,
        ).strip()
        extraction_models = _load_model_extraction_configs(
            default_provider=extraction_provider,
            default_model_id=extraction_model_id,
        )

        return cls(
            app_env=app_env,
            enable_docs=_to_bool(os.getenv("SOTERRA_ENABLE_DOCS"), app_env != "production"),
            repo_root=repo_root,
            storage_mode=storage_mode,
            repository_mode=repository_mode,
            extractor_mode=extractor_mode,
            package_extractor=os.getenv("SOTERRA_PACKAGE_EXTRACTOR", "doctr_rules_presidio").strip(),
            model_extractor=extraction_provider,
            allow_model_extraction=_to_bool(os.getenv("SOTERRA_ALLOW_MODEL_EXTRACTION"), False),
            soterra_extraction_provider=extraction_provider,
            soterra_extraction_model_id=extraction_model_id,
            soterra_document_parse_provider=document_parse_provider,
            soterra_document_parse_model_id=document_parse_model_id,
            document_parse_max_pages=int(os.getenv("SOTERRA_DOCUMENT_PARSE_MAX_PAGES", "12")),
            document_parse_max_new_tokens=int(os.getenv("SOTERRA_DOCUMENT_PARSE_MAX_NEW_TOKENS", "2048")),
            document_parse_text_in_pictures=_to_bool(os.getenv("SOTERRA_DOCUMENT_PARSE_TEXT_IN_PICTURES"), False),
            soterra_agent_provider=agent_provider,
            soterra_agent_model_id=agent_model_id,
            soterra_insights_provider=os.getenv("SOTERRA_INSIGHTS_PROVIDER", DEFAULT_INSIGHTS_PROVIDER).strip(),
            model_extraction_temperature=float(os.getenv("SOTERRA_MODEL_EXTRACTION_TEMPERATURE", "0.0")),
            model_extraction_max_findings=int(os.getenv("SOTERRA_MODEL_EXTRACTION_MAX_FINDINGS", "40")),
            model_extraction_timeout_seconds=int(os.getenv("SOTERRA_MODEL_EXTRACTION_TIMEOUT_SECONDS", "90")),
            extraction_timeout_seconds=int(
                os.getenv(
                    "SOTERRA_EXTRACTION_TIMEOUT_SECONDS",
                    os.getenv("SOTERRA_MODEL_EXTRACTION_TIMEOUT_SECONDS", "90"),
                )
            ),
            model_extraction_retry_count=int(os.getenv("SOTERRA_MODEL_EXTRACTION_RETRY_COUNT", "1")),
            model_extraction_models=extraction_models,
            process_inline=_to_bool(os.getenv("SOTERRA_PROCESS_INLINE"), _default_process_inline()),
            local_data_dir=local_data_dir,
            local_db_path=Path(
                os.getenv(
                    "SOTERRA_LOCAL_DB_PATH",
                    str(local_data_dir / "soterra-demo.sqlite3"),
                )
            ),
            local_storage_dir=Path(
                os.getenv(
                    "SOTERRA_LOCAL_STORAGE_DIR",
                    str(local_data_dir / "storage"),
                )
            ),
            package_max_pages=int(os.getenv("SOTERRA_PACKAGE_MAX_PAGES", "12")),
            package_ocr_enabled=_to_bool(os.getenv("SOTERRA_PACKAGE_OCR_ENABLED"), False),
            package_ocr_max_pages=int(os.getenv("SOTERRA_PACKAGE_OCR_MAX_PAGES", os.getenv("SOTERRA_PACKAGE_MAX_PAGES", "12"))),
            supabase_url=supabase_url,
            supabase_service_role_key=supabase_service_role_key,
            supabase_bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "inspection-reports"),
            auth_session_ttl_hours=int(os.getenv("SOTERRA_AUTH_SESSION_TTL_HOURS", "12")),
            max_upload_bytes=int(os.getenv("SOTERRA_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))),
            max_bulk_upload_bytes=int(os.getenv("SOTERRA_MAX_BULK_UPLOAD_BYTES", str(250 * 1024 * 1024))),
            max_bulk_file_count=int(os.getenv("SOTERRA_MAX_BULK_FILE_COUNT", "10")),
            max_upload_pages=int(os.getenv("SOTERRA_MAX_UPLOAD_PAGES", "100")),
            max_reports_per_tenant=int(os.getenv("SOTERRA_MAX_REPORTS_PER_TENANT", "1000")),
            upload_rate_limit_per_hour=int(os.getenv("SOTERRA_UPLOAD_RATE_LIMIT_PER_HOUR", "60")),
            bootstrap_demo_account=_to_bool(os.getenv("SOTERRA_BOOTSTRAP_DEMO_ACCOUNT"), False),
            demo_admin_password=os.getenv("SOTERRA_DEMO_ADMIN_PASSWORD"),
            app_base_url=os.getenv("SOTERRA_APP_BASE_URL", os.getenv("APP_BASE_URL", "http://localhost:3000")).rstrip("/"),
            smtp_host=os.getenv("SOTERRA_SMTP_HOST") or os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SOTERRA_SMTP_PORT", os.getenv("SMTP_PORT", "587"))),
            smtp_username=os.getenv("SOTERRA_SMTP_USERNAME") or os.getenv("SMTP_USERNAME"),
            smtp_password=os.getenv("SOTERRA_SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD"),
            smtp_from_email=os.getenv("SOTERRA_EMAIL_FROM", os.getenv("SMTP_FROM_EMAIL", "no-reply@soterra.local")),
            smtp_from_name=os.getenv("SOTERRA_EMAIL_FROM_NAME", "Soterra"),
            smtp_use_tls=_to_bool(os.getenv("SOTERRA_SMTP_USE_TLS", os.getenv("SMTP_USE_TLS")), True),
        )


def _load_model_extraction_configs(
    *,
    default_provider: str,
    default_model_id: str,
) -> list[ModelExtractionConfig]:
    raw_json = os.getenv("SOTERRA_EXTRACTION_MODELS_JSON")
    if raw_json:
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SOTERRA_EXTRACTION_MODELS_JSON must be valid JSON.") from exc
        if not isinstance(decoded, list):
            raise RuntimeError("SOTERRA_EXTRACTION_MODELS_JSON must be a list of model configs.")
        configs = []
        for index, item in enumerate(decoded, start=1):
            if not isinstance(item, dict):
                raise RuntimeError("Each SOTERRA_EXTRACTION_MODELS_JSON item must be an object.")
            provider = str(item.get("provider") or "").strip()
            model_id = str(item.get("model_id") or item.get("modelId") or "").strip()
            name = str(item.get("name") or f"model-{index}").strip()
            if provider and model_id:
                configs.append(ModelExtractionConfig(provider=provider, model_id=model_id, name=name))
        if configs:
            return configs

    return [
        ModelExtractionConfig(
            provider=default_provider or DEFAULT_MODEL_PROVIDER,
            model_id=default_model_id or DEFAULT_MODEL_ID,
            name="primary",
        )
    ]
