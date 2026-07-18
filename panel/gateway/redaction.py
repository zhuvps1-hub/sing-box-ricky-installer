"""Secret redaction shared by APIs, audit logs, and diagnostics."""
from __future__ import annotations

import copy
import re
from typing import Any

_SECRET_KEYS = {
    "authorization",
    "cookie",
    "csrf",
    "csrf_token",
    "password",
    "pass",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
}

_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+"),
    re.compile(r"(?i)((?:password|passwd|pass|token|secret|private_key)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"ss://[^\s\"'<>]+"),
)


def is_secret_key(key: Any) -> bool:
    text = str(key).strip().lower().replace("-", "_")
    return text in _SECRET_KEYS or text.endswith("_password") or text.endswith("_secret") or text.endswith("_token")


def redact(value: Any) -> Any:
    """Return a deep-copied value with well-known secret fields masked."""
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            result[key] = "***" if is_secret_key(key) and item not in (None, "") else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return copy.deepcopy(value)


def redact_text(text: Any, limit: int = 20_000) -> str:
    value = str(text or "")
    for pattern in _TEXT_PATTERNS:
        if pattern.pattern.startswith("ss://"):
            value = pattern.sub("ss://***", value)
        else:
            value = pattern.sub(r"\1***", value)
    return value[-limit:]
