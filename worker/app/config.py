from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _read_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _read_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _load_env_file() -> None:
    candidates: list[Path] = []
    explicit = os.getenv("KVCA_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))

    candidates.append(Path.cwd() / ".env")
    candidates.append(Path(__file__).resolve().parents[2] / ".env")

    env_path = next((path for path in candidates if path.exists() and path.is_file()), None)
    if env_path is None:
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _normalize_base_url(raw: str) -> str:
    text = raw.strip()
    if not text:
        return "https://edu.kvca.or.kr"
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        raise RuntimeError(f"Invalid KVCA_BASE_URL: {raw}")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class Settings:
    kvca_base_url: str
    kvca_admin_user_id: str
    kvca_admin_user_password: str
    kvca_request_timeout_ms: int
    kvca_token_skew_seconds: int
    kvca_retry_on_401: bool
    kvca_sync_default_category_id: int | None
    kvca_max_users_per_course: int | None
    worker_log_level: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_request_timeout_ms: int
    alert_cooldown_minutes: int
    job_lock_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        supabase_url_raw = os.getenv("SUPABASE_URL")
        supabase_url = _normalize_base_url(supabase_url_raw) if supabase_url_raw else None
        supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if supabase_service_role_key is not None:
            supabase_service_role_key = supabase_service_role_key.strip() or None
        return cls(
            kvca_base_url=_normalize_base_url(os.getenv("KVCA_BASE_URL", "https://edu.kvca.or.kr")),
            kvca_admin_user_id=_read_required("KVCA_ADMIN_USER_ID"),
            kvca_admin_user_password=_read_required("KVCA_ADMIN_USER_PASSWORD"),
            kvca_request_timeout_ms=int(os.getenv("KVCA_REQUEST_TIMEOUT_MS", "15000")),
            kvca_token_skew_seconds=int(os.getenv("KVCA_TOKEN_SKEW_SECONDS", "60")),
            kvca_retry_on_401=_parse_bool(os.getenv("KVCA_RETRY_ON_401"), True),
            kvca_sync_default_category_id=_read_optional_int("KVCA_SYNC_DEFAULT_CATEGORY_ID"),
            kvca_max_users_per_course=_read_optional_int("KVCA_MAX_USERS_PER_COURSE"),
            worker_log_level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
            supabase_url=supabase_url,
            supabase_service_role_key=supabase_service_role_key,
            supabase_request_timeout_ms=int(os.getenv("SUPABASE_REQUEST_TIMEOUT_MS", "15000")),
            alert_cooldown_minutes=int(os.getenv("ALERT_COOLDOWN_MINUTES", "30")),
            job_lock_ttl_seconds=int(os.getenv("JOB_LOCK_TTL_SECONDS", "900")),
        )
