"""Make untrusted text safe to store: Postgres rejects NUL, and columns have length limits."""

import hashlib
import re

# C0 control characters except tab, newline and carriage return.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(value: str | None, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    text = _CONTROL.sub("", value)
    return text[:max_length] if max_length is not None else text


def fit_identifier(value: str, max_length: int) -> str:
    """Keep an external id within its column. An oversized id is replaced by a hash, applied
    the same way to stored ids and to thread references, so threading still matches."""
    value = _CONTROL.sub("", value)
    if len(value) <= max_length:
        return value
    return f"<sha256.{hashlib.sha256(value.encode()).hexdigest()}>"
