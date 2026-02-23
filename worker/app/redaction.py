from __future__ import annotations

from typing import Any


SENSITIVE_KEYS = {
    "userPassword",
    "juminNumber",
    "refreshToken",
    "accessToken",
}


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in SENSITIVE_KEYS:
                continue
            sanitized[key] = redact_sensitive(item)
        return sanitized
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value
