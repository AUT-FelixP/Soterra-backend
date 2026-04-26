from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    # Vercel serverless requests should complete the extraction before responding.
    # FastAPI BackgroundTasks are not a durable job queue and can leave uploads stuck
    # in "Extracting" when the request lifecycle ends before the background work finishes.
    return bool(os.getenv("VERCEL"))


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
    process_inline: bool
    local_data_dir: Path
    local_db_path: Path
    local_storage_dir: Path
    openai_api_key: str | None
    openai_model: str
    openai_max_pages: int
    package_max_pages: int
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_bucket: str
    auth_session_ttl_hours: int
    max_upload_bytes: int
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

        openai_api_key = os.getenv("OPENAI_API_KEY")
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
            "openai" if openai_api_key else "package",
        ).strip()

        return cls(
            app_env=app_env,
            enable_docs=_to_bool(os.getenv("SOTERRA_ENABLE_DOCS"), app_env != "production"),
            repo_root=repo_root,
            storage_mode=storage_mode,
            repository_mode=repository_mode,
            extractor_mode=extractor_mode,
            package_extractor=os.getenv("SOTERRA_PACKAGE_EXTRACTOR", "doctr_rules_presidio").strip(),
            model_extractor=os.getenv("SOTERRA_MODEL_EXTRACTOR", "openai").strip(),
            allow_model_extraction=_to_bool(os.getenv("SOTERRA_ALLOW_MODEL_EXTRACTION"), False),
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
            openai_api_key=openai_api_key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            openai_max_pages=int(os.getenv("OPENAI_MAX_PAGES", "8")),
            package_max_pages=int(os.getenv("SOTERRA_PACKAGE_MAX_PAGES", "12")),
            supabase_url=supabase_url,
            supabase_service_role_key=supabase_service_role_key,
            supabase_bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "inspection-reports"),
            auth_session_ttl_hours=int(os.getenv("SOTERRA_AUTH_SESSION_TTL_HOURS", "12")),
            max_upload_bytes=int(os.getenv("SOTERRA_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
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
