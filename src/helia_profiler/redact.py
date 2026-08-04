"""Conservative, deterministic redaction for field-diagnostics support bundles.

Applied to every value written into a support bundle (see
:mod:`helia_profiler.support_bundle`) before it reaches disk. Redaction is
str -> str across arbitrarily nested JSON-safe structures (``dict`` / ``list``
/ ``tuple`` / ``str`` / scalars) and always returns a count of how many
values changed, so a bundle can prove what happened without ever needing to
log the original secret to verify it.

Covered by default:

* Absolute filesystem paths (POSIX and Windows/UNC) — the directory portion
  is replaced with a stable placeholder; only the final path component
  (filename) is kept, since it is usually needed to make a diagnostic useful.
* URL userinfo credentials (``https://user:pass@host/...``) and common
  token-shaped query parameters (``?token=...``, ``&api_key=...``).
* Common credential/token shapes (GitHub PAT, AWS access key, Slack token,
  JWT, ``Bearer <token>``) wherever they appear in text.
* ``KEY: value`` / ``KEY=value`` assignments whose key looks secret-shaped
  (``token``, ``secret``, ``password``, ``api_key``, ...).
* Device serial numbers (J-Link probe serials, USB serial numbers) — these
  are redacted structurally (by field name, e.g. ``serial``/``serial_number``)
  rather than by pattern-matching digits, to avoid false positives on
  ordinary counters/sizes. Pass ``RedactionPolicy(redact_probe_serials=False)``
  to opt back into raw serials (only ever done for an explicit CLI opt-in,
  which must also surface a warning to the caller).

Redaction never fails: it only ever narrows/replaces text, never raises.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

_PLACEHOLDER_PATH = "<redacted-path>"
_PLACEHOLDER_URL_AUTH = "<redacted>"
_PLACEHOLDER_TOKEN = "<redacted-token>"
_PLACEHOLDER_SECRET = "<redacted>"

_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>]+")
_URL_USERINFO_RE = re.compile(r"://([^/@\s:]+):([^/@\s]+)@")
_URL_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|access_token|api[_-]?key|apikey|key|auth|secret)=)([^&\s]+)"
)

_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\r\n\"'<>|]+\\)*[^\\\r\n\"'<>|]+")
_WINDOWS_UNC_RE = re.compile(r"\\\\[^\\\r\n\"'<>|]+(?:\\[^\\\r\n\"'<>|]+)+")
_POSIX_ABS_RE = re.compile(r"(?<![:\w])/(?:[^/\r\n\"'<>|]+/)+[^/\r\n\"'<>|]*")

_TOKEN_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9\-_.=]{8,})")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|access[_-]?key)\s*[:=]\s*)"
    r"([\"']?)([^\s\"'&]{3,})\2"
)
_SERIAL_KEY_RE = re.compile(r"(?i)serial")


@dataclass(frozen=True)
class RedactionPolicy:
    """What a redaction pass should scrub.

    ``redact_probe_serials=False`` is the one explicit, opt-in exception
    (``hpx doctor --bundle --raw-probe-ids``): every other field is always
    redacted so a support bundle is safe to attach to a public issue by
    default.
    """

    redact_paths: bool = True
    redact_probe_serials: bool = True


@dataclass(frozen=True)
class RedactionCounts:
    """How many values a redaction pass changed, by category."""

    paths: int = 0
    urls: int = 0
    tokens: int = 0
    serials: int = 0
    env_values: int = 0

    @property
    def total(self) -> int:
        return self.paths + self.urls + self.tokens + self.serials + self.env_values

    def combined(self, other: RedactionCounts) -> RedactionCounts:
        return RedactionCounts(
            paths=self.paths + other.paths,
            urls=self.urls + other.urls,
            tokens=self.tokens + other.tokens,
            serials=self.serials + other.serials,
            env_values=self.env_values + other.env_values,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "paths": self.paths,
            "urls": self.urls,
            "tokens": self.tokens,
            "serials": self.serials,
            "env_values": self.env_values,
            "total": self.total,
        }


def _basename(path_text: str) -> str:
    if "\\" in path_text and "/" not in path_text:
        name = PureWindowsPath(path_text).name
    else:
        name = path_text.rstrip("/\\").rsplit("/", 1)[-1]
    return name


def _redact_path_match(match: re.Match[str]) -> str:
    name = _basename(match.group(0))
    return f"{_PLACEHOLDER_PATH}/{name}" if name else _PLACEHOLDER_PATH


def _redact_paths(text: str) -> tuple[str, int]:
    count = 0

    def _sub(pattern: re.Pattern[str], value: str) -> str:
        nonlocal count

        def _replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return _redact_path_match(match)

        return pattern.sub(_replace, value)

    text = _sub(_WINDOWS_UNC_RE, text)
    text = _sub(_WINDOWS_ABS_RE, text)
    text = _sub(_POSIX_ABS_RE, text)
    return text, count


def _redact_url(url: str) -> tuple[str, bool]:
    changed = False

    def _strip_userinfo(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"://{_PLACEHOLDER_URL_AUTH}@"

    redacted = _URL_USERINFO_RE.sub(_strip_userinfo, url)

    def _strip_query_value(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group(1)}{_PLACEHOLDER_URL_AUTH}"

    redacted = _URL_SENSITIVE_QUERY_RE.sub(_strip_query_value, redacted)
    return redacted, changed


def _protect_urls(text: str) -> tuple[str, dict[str, str], int]:
    """Extract, redact, and sentinel-substitute URLs before path/token scans.

    Prevents the generic absolute-path regex from tearing a URL's ``/path``
    segment out of its scheme+host, and keeps URL-specific redaction (auth,
    sensitive query params) from double-processing through later passes.
    """
    replacements: dict[str, str] = {}
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        redacted, changed = _redact_url(match.group(0))
        if changed:
            count += 1
        sentinel = f"\x00HPX-URL-{len(replacements)}\x00"
        replacements[sentinel] = redacted
        return sentinel

    protected = _URL_RE.sub(_replace, text)
    return protected, replacements, count


def _redact_token_shapes(text: str) -> tuple[str, int]:
    count = 0

    for pattern in _TOKEN_SHAPE_PATTERNS:

        def _replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return _PLACEHOLDER_TOKEN

        text = pattern.sub(_replace, text)

    def _replace_bearer(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{_PLACEHOLDER_TOKEN}"

    text = _BEARER_RE.sub(_replace_bearer, text)
    return text, count


def _redact_secret_assignments(text: str) -> tuple[str, int]:
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}{_PLACEHOLDER_SECRET}{match.group(2)}"

    return _SECRET_ASSIGNMENT_RE.sub(_replace, text), count


def redact_text(text: str, policy: RedactionPolicy = RedactionPolicy()) -> tuple[str, RedactionCounts]:
    """Redact one string value. Never raises; returns ``(text, counts)``."""

    if not text:
        return text, RedactionCounts()

    protected, url_replacements, url_count = _protect_urls(text)

    path_count = 0
    if policy.redact_paths:
        protected, path_count = _redact_paths(protected)

    protected, token_count = _redact_token_shapes(protected)
    protected, secret_count = _redact_secret_assignments(protected)

    for sentinel, redacted_url in url_replacements.items():
        protected = protected.replace(sentinel, redacted_url)

    counts = RedactionCounts(
        paths=path_count, urls=url_count, tokens=token_count, env_values=secret_count
    )
    return protected, counts


def redact_serial(value: str, policy: RedactionPolicy = RedactionPolicy()) -> tuple[str, RedactionCounts]:
    """Redact one device serial number (J-Link probe, USB serial, ...).

    Uses a short, stable hash preview rather than removing the value
    outright, so repeated serials in the same bundle remain distinguishable
    without exposing the real number. A no-op when *value* is empty or the
    policy explicitly opts into raw probe identifiers.
    """
    if not value or not policy.redact_probe_serials:
        return value, RedactionCounts()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"<redacted-serial:{digest}>", RedactionCounts(serials=1)


def redact_value(
    value: Any,
    policy: RedactionPolicy = RedactionPolicy(),
    *,
    key: str | None = None,
) -> tuple[Any, RedactionCounts]:
    """Recursively redact a JSON-safe value tree. Never raises.

    *key* is the enclosing mapping key (if any) — used only to route
    serial-shaped fields (``serial``, ``serial_number``, ...) through
    :func:`redact_serial` instead of the generic text redactor.
    """
    if isinstance(value, str):
        if key is not None and _SERIAL_KEY_RE.search(key):
            return redact_serial(value, policy)
        return redact_text(value, policy)
    if isinstance(value, Path):
        return redact_value(str(value), policy, key=key)
    if isinstance(value, Mapping):
        counts = RedactionCounts()
        result: dict[Any, Any] = {}
        for item_key, item_value in value.items():
            redacted, item_counts = redact_value(
                item_value, policy, key=str(item_key) if isinstance(item_key, str) else None
            )
            result[item_key] = redacted
            counts = counts.combined(item_counts)
        return result, counts
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        counts = RedactionCounts()
        items = []
        for item in value:
            redacted, item_counts = redact_value(item, policy, key=key)
            items.append(redacted)
            counts = counts.combined(item_counts)
        return (tuple(items) if isinstance(value, tuple) else items), counts
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        # Defensive: any other JSON-safe sequence type behaves like a list.
        counts = RedactionCounts()
        items = []
        for item in value:
            redacted, item_counts = redact_value(item, policy, key=key)
            items.append(redacted)
            counts = counts.combined(item_counts)
        return items, counts
    return value, RedactionCounts()


__all__ = [
    "RedactionCounts",
    "RedactionPolicy",
    "redact_serial",
    "redact_text",
    "redact_value",
]
