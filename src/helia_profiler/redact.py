"""Conservative, deterministic redaction for field-diagnostics support bundles.

Applied to every value written into a support bundle (see
:mod:`helia_profiler.support_bundle`) before it reaches disk. Redaction is
str -> str across arbitrarily nested JSON-safe structures (``dict`` / ``list``
/ ``tuple`` / ``str`` / scalars) and always returns a count of how many
values changed. These counts prove what categories of text were *found and
rewritten* — they are not a certificate that nothing sensitive remains;
treat every bundle as reviewable, not as provably clean.

Covered by default:

* Absolute filesystem paths (POSIX, Windows backslash and forward-slash
  drive paths, and UNC paths) — the directory portion is replaced with a
  stable placeholder; only the final path component is kept, since it is
  usually needed to make a diagnostic useful. The one exception is a path
  that resolves to a home directory itself (for example a bare
  ``/Users/<name>`` or ``C:\\Users\\<name>``): the final component *is* the
  account name there, so nothing is kept.
* URLs: credentials embedded in the authority component (both the
  ``user:password`` form and a single bearer-style credential with no
  colon), every query-parameter value except a narrow allow-list of
  clearly non-sensitive names, and — since a credential can just as easily
  appear in a URL's path or query as in plain text — the same
  credential/token-shape and secret-assignment passes described below are
  also applied to URL text after the authority/query pass. ``file://`` URLs
  additionally get their path component run through path redaction.
* Common credential/token shapes (GitHub PAT, AWS access key, Slack token,
  JWT, an HTTP bearer credential) wherever they appear in text, including
  inside a URL.
* ``KEY: value`` / ``KEY=value`` text assignments whose key looks
  secret-shaped (``token``, ``secret``, ``password``, ``api_key``, ...),
  and — structurally, not just in free text — any JSON mapping value whose
  *key* looks secret-shaped (``{"api_key": "..."}"``, ``{"NSX_SECRET":
  "..."}"``), regardless of the value's own shape. Mapping keys themselves
  are also passed through the same text redaction.
* Device serial numbers (J-Link probe serials, USB serial numbers) — these
  are redacted structurally (by field name, e.g. ``serial``/``serial_number``,
  and by substitution everywhere else a known serial value literally
  recurs, e.g. embedded in a ``hwid`` or device-path string) rather than by
  pattern-matching digits, to avoid false positives on ordinary
  counters/sizes. Pass ``RedactionPolicy(redact_probe_serials=False)`` to
  opt back into raw serials (only ever done for an explicit CLI opt-in,
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
# Authority-component credentials: either `user:password@host` or a single
# bearer-style credential with no colon (`token@host`) — both forms are
# common ways a PAT ends up embedded in a git remote URL.
_URL_USERINFO_RE = re.compile(r"://(?P<user>[^/@\s:]+)(?::(?P<password>[^/@\s]+))?@")
# Every `?key=value`/`&key=value`/`#key=value` pair — the `#` alternative
# catches OAuth implicit-flow-style fragment parameters
# (`#access_token=...&scope=...`), which are exactly as sensitive as a query
# parameter but live after the fragment marker instead. The allow-list below
# is the only thing spared, so an unrecognized parameter name is redacted by
# default rather than only a known-sensitive-name deny-list.
_URL_QUERY_PAIR_RE = re.compile(r"([?&#])([^=&#\s]+)=([^&#\s]*)")
_URL_QUERY_BENIGN_KEYS = frozenset(
    {
        "v",
        "version",
        "ref",
        "tag",
        "branch",
        "page",
        "per_page",
        "format",
        "raw",
        "download",
        # Deliberately no "path": a query value under that name could
        # itself be an absolute filesystem path, which is exactly what
        # this module exists to redact -- default to redacting it too.
        "id",
        "type",
        "lang",
        "locale",
    }
)
_FILE_SCHEME_RE = re.compile(r"(?i)^file://")

_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\r\n\"'<>|]+\\)*[^\\\r\n\"'<>|]+")
_WINDOWS_UNC_RE = re.compile(r"\\\\[^\\\r\n\"'<>|]+(?:\\[^\\\r\n\"'<>|]+)+")
# No `:` in the "must not be preceded by" set: URLs are already extracted
# and sentinel-substituted (see _protect_urls) before this ever runs, so a
# leading `scheme:` can no longer be confused for one; a Windows drive
# letter (`C:/Users/...`) or a `key:/path` form must still be caught.
_POSIX_ABS_RE = re.compile(r"(?<!\w)/(?:[^/\r\n\"'<>|]+/)+[^/\r\n\"'<>|]*")
_HOME_CONTAINER_NAMES = frozenset({"users", "home", "documents and settings"})

_TOKEN_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9\-_.=]{8,})")
# `(?<![A-Za-z0-9])` rather than `\b` before the key alternation: `\b` does
# not match between two word characters, so an underscore-joined key like
# `GITHUB_TOKEN=` (underscore is a word character) would otherwise never
# match at all.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<key>(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"
    r"\s*[:=]\s*)(?:(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s\"'&]{3,}))"
)
_SERIAL_KEY_RE = re.compile(r"(?i)serial")
_SECRET_KEY_RE = re.compile(
    r"(?i)(secret|password|passwd|token|api[_-]?key|access[_-]?key|credential)"
)
_HWID_SERIAL_RE = re.compile(r"(?i)\bSER=([^\s]+)")
_REDACTED_SERIAL_RE = re.compile(r"^<redacted-serial:[0-9a-f]{8}>$")


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
    raw = match.group(0)
    normalized = raw.replace("\\", "/").rstrip("/")
    segments = [part for part in normalized.split("/") if part]
    if not segments:
        return _PLACEHOLDER_PATH
    name = segments[-1]
    # A bare drive letter ("C:") is never sensitive on its own.
    if len(segments) == 1 and re.fullmatch(r"[A-Za-z]:", name):
        return _PLACEHOLDER_PATH
    # The final component of a home-directory path *is* the account name
    # (`/Users/<name>`, `C:\Users\<name>`, ...) — never keep it, unlike an
    # ordinary basename such as a filename or a project directory.
    if len(segments) >= 2 and segments[-2].lower() in _HOME_CONTAINER_NAMES:
        return _PLACEHOLDER_PATH
    return f"{_PLACEHOLDER_PATH}/{name}"


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


def _redact_url(url: str, policy: RedactionPolicy) -> tuple[str, RedactionCounts]:
    """Redact one URL's credentials, query values, and any embedded secret.

    Applied *before* the URL is sentinel-substituted out of the surrounding
    text (see :func:`_protect_urls`), so a token or secret-assignment
    appearing inside a URL's path or query string is still caught — those
    passes never otherwise see URL text once it has been protected.
    """
    changed = False

    def _strip_userinfo(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"://{_PLACEHOLDER_URL_AUTH}@"

    redacted = _URL_USERINFO_RE.sub(_strip_userinfo, url)

    def _redact_query_value(match: re.Match[str]) -> str:
        nonlocal changed
        separator, name, value = match.group(1), match.group(2), match.group(3)
        if not value or name.lower() in _URL_QUERY_BENIGN_KEYS:
            return match.group(0)
        changed = True
        return f"{separator}{name}={_PLACEHOLDER_URL_AUTH}"

    redacted = _URL_QUERY_PAIR_RE.sub(_redact_query_value, redacted)
    url_counts = RedactionCounts(urls=1) if changed else RedactionCounts()

    if policy.redact_paths and _FILE_SCHEME_RE.match(redacted):
        prefix = redacted[: len("file://")]
        rest = redacted[len("file://") :]
        if rest.startswith("/"):
            redacted_rest, path_count = _redact_paths(rest)
            redacted = prefix + redacted_rest
            url_counts = url_counts.combined(RedactionCounts(paths=path_count))

    redacted, token_count = _redact_token_shapes(redacted)
    # Deliberately no _redact_secret_assignments() pass here: every
    # `key=value` pair after a `?`/`&`/`#` marker (query string or a
    # fragment, e.g. an OAuth implicit-grant redirect) was already
    # default-redacted above — a stricter rule than the generic
    # secret-key-name pattern — so running both would double-count and
    # double-replace the same value.
    return redacted, url_counts.combined(RedactionCounts(tokens=token_count))


def _protect_urls(text: str, policy: RedactionPolicy) -> tuple[str, dict[str, str], RedactionCounts]:
    """Fully redact, then sentinel-substitute, every URL in *text*.

    Prevents the generic absolute-path regex from tearing a URL's ``/path``
    segment out of its scheme+host, while still letting credential/token
    detection see the URL's own content (see :func:`_redact_url`) before it
    is replaced by an opaque sentinel for the remainder of the pipeline.
    """
    replacements: dict[str, str] = {}
    counts = RedactionCounts()

    def _replace(match: re.Match[str]) -> str:
        nonlocal counts
        redacted, url_counts = _redact_url(match.group(0), policy)
        counts = counts.combined(url_counts)
        sentinel = f"\x00HPX-URL-{len(replacements)}\x00"
        replacements[sentinel] = redacted
        return sentinel

    protected = _URL_RE.sub(_replace, text)
    return protected, replacements, counts


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
        key = match.group("key")
        quote = match.group("quote")
        if quote is not None:
            return f"{key}{quote}{_PLACEHOLDER_SECRET}{quote}"
        return f"{key}{_PLACEHOLDER_SECRET}"

    return _SECRET_ASSIGNMENT_RE.sub(_replace, text), count


def redact_text(text: str, policy: RedactionPolicy = RedactionPolicy()) -> tuple[str, RedactionCounts]:
    """Redact one string value. Never raises; returns ``(text, counts)``."""

    if not text:
        return text, RedactionCounts()

    protected, url_replacements, url_counts = _protect_urls(text, policy)

    path_count = 0
    if policy.redact_paths:
        protected, path_count = _redact_paths(protected)

    protected, token_count = _redact_token_shapes(protected)
    protected, secret_count = _redact_secret_assignments(protected)

    for sentinel, redacted_url in url_replacements.items():
        protected = protected.replace(sentinel, redacted_url)

    counts = RedactionCounts(paths=path_count, tokens=token_count, env_values=secret_count)
    counts = counts.combined(url_counts)
    return protected, counts


def redact_serial(value: str, policy: RedactionPolicy = RedactionPolicy()) -> tuple[str, RedactionCounts]:
    """Redact one device serial number (J-Link probe, USB serial, ...).

    Uses a short, stable hash preview rather than removing the value
    outright, so repeated serials in the same bundle remain distinguishable
    without exposing the real number. A no-op when *value* is empty, the
    policy explicitly opts into raw probe identifiers, or *value* is
    already one of this function's own placeholders — idempotent, so a
    value redacted once by field-name routing and again by a generic
    text pass (or vice versa) is never re-hashed into a second, different
    placeholder for the same real serial.
    """
    if not value or not policy.redact_probe_serials or _REDACTED_SERIAL_RE.fullmatch(value):
        return value, RedactionCounts()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"<redacted-serial:{digest}>", RedactionCounts(serials=1)


def redact_known_serial(text: str, serial: str, policy: RedactionPolicy = RedactionPolicy()) -> tuple[str, RedactionCounts]:
    """Replace every literal occurrence of *serial* inside *text*.

    Structural (by field name, via :func:`redact_serial`) redaction only
    catches a serial number in the field that is *named* like a serial —
    it does not catch the same value recurring inside an unrelated field
    (for example a USB ``hwid`` string embedding ``SER=<serial>``, or a
    device path whose basename is derived from it). Call this for every
    other string field collected alongside a known serial so the same
    value can't leak through a sibling field.
    """
    if not text or not serial or not policy.redact_probe_serials or serial not in text:
        return text, RedactionCounts()
    placeholder, _ = redact_serial(serial, policy)
    return text.replace(serial, placeholder), RedactionCounts(serials=1)


def _redact_hwid_serial(text: str, policy: RedactionPolicy) -> tuple[str, RedactionCounts]:
    """Redact a ``SER=<value>`` marker in a USB ``hwid``-style string."""
    if not policy.redact_probe_serials:
        return text, RedactionCounts()
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        original = match.group(1)
        placeholder, changed_counts = redact_serial(original, policy)
        count += changed_counts.serials
        return f"SER={placeholder}"

    return _HWID_SERIAL_RE.sub(_replace, text), RedactionCounts(serials=count)


def redact_value(
    value: Any,
    policy: RedactionPolicy = RedactionPolicy(),
    *,
    key: str | None = None,
    _inside_secret: bool = False,
) -> tuple[Any, RedactionCounts]:
    """Recursively redact a JSON-safe value tree. Never raises.

    *key* is the enclosing mapping key (if any) — used to route
    secret-shaped fields (``api_key``, ``password``, ``NSX_SECRET``, ...)
    through an always-redact rule and serial-shaped fields (``serial``,
    ``serial_number``, ...) through :func:`redact_serial`, instead of the
    generic pattern-based text redactor. Mapping keys are themselves passed
    through :func:`redact_text` (a no-op for the ordinary field-name keys
    used throughout HPX's own schemas, but a real safety net for a
    dynamic/user-supplied mapping such as an engine's free-form config).

    Secret-key routing is *sticky*: once a mapping key matches
    ``_SECRET_KEY_RE`` (for example ``{"credentials": {"user": "...",
    "pass": "..."}}``), every string nested anywhere underneath it — through
    further mappings, lists, or tuples — is redacted too, regardless of
    those descendants' own key names. ``_inside_secret`` carries that state
    through the recursion; it is an internal implementation detail, not part
    of the public call contract.
    """
    inside_secret = _inside_secret or (key is not None and _SECRET_KEY_RE.search(key) is not None)
    if isinstance(value, str):
        if inside_secret:
            if not value:
                return value, RedactionCounts()
            return _PLACEHOLDER_SECRET, RedactionCounts(env_values=1)
        if key is not None and _SERIAL_KEY_RE.search(key) and policy.redact_probe_serials:
            return redact_serial(value, policy)
        redacted, counts = redact_text(value, policy)
        hwid_redacted, hwid_counts = _redact_hwid_serial(redacted, policy)
        return hwid_redacted, counts.combined(hwid_counts)
    if isinstance(value, Path):
        return redact_value(str(value), policy, key=key, _inside_secret=inside_secret)
    if isinstance(value, Mapping):
        counts = RedactionCounts()
        result: dict[Any, Any] = {}
        for item_key, item_value in value.items():
            child_key = str(item_key) if isinstance(item_key, str) else None
            redacted_value, value_counts = redact_value(
                item_value, policy, key=child_key, _inside_secret=inside_secret
            )
            counts = counts.combined(value_counts)
            redacted_key: Any = item_key
            if isinstance(item_key, str):
                redacted_key, key_counts = redact_text(item_key, policy)
                counts = counts.combined(key_counts)
            result[redacted_key] = redacted_value
        return result, counts
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        counts = RedactionCounts()
        items = []
        for item in value:
            redacted, item_counts = redact_value(item, policy, key=key, _inside_secret=inside_secret)
            items.append(redacted)
            counts = counts.combined(item_counts)
        return (tuple(items) if isinstance(value, tuple) else items), counts
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        # Defensive: any other JSON-safe sequence type behaves like a list.
        counts = RedactionCounts()
        items = []
        for item in value:
            redacted, item_counts = redact_value(item, policy, key=key, _inside_secret=inside_secret)
            items.append(redacted)
            counts = counts.combined(item_counts)
        return items, counts
    return value, RedactionCounts()


__all__ = [
    "RedactionCounts",
    "RedactionPolicy",
    "redact_known_serial",
    "redact_serial",
    "redact_text",
    "redact_value",
]
