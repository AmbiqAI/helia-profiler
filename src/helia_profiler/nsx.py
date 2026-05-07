"""NSX build-system helpers.

Thin facade over :mod:`neuralspotx.api`. The rest of the codebase calls into
this module (rather than ``neuralspotx`` directly) so that:

* failures surface as :class:`BuildError` with our standard hint structure;
* a single place enforces the per-subprocess wall-clock timeout for every
  long-running NSX operation (configure/build/flash/sync);
* the call sites stay agnostic of whether NSX is exposed as a CLI or a Python
  API in any given release.

Timeout enforcement is *robust*: NSX's underlying ``cmake`` / ``ninja`` /
``git`` / ``JLinkExe`` subprocesses are spawned in their own process group,
and the whole group is SIGTERM/SIGKILL'd when ``timeout_s`` elapses
(see :mod:`neuralspotx.subprocess_utils`).  No daemon-thread leak on
hang.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from neuralspotx import api as nsx_api
from neuralspotx.api import NSXError

from .errors import BuildError, NetworkError

log = logging.getLogger("hpx")

# Conservative default timeouts — cmake configure is fast, builds can be
# slow, flash involves J-Link probe negotiation. These are *defaults*; the
# profiler pipeline passes explicit values from ``ProfileConfig.timeouts``.
_DEFAULT_CONFIGURE_TIMEOUT_S = 120
_DEFAULT_BUILD_TIMEOUT_S = 300
_DEFAULT_FLASH_TIMEOUT_S = 120
_DEFAULT_LOCK_TIMEOUT_S = 180
_DEFAULT_SYNC_TIMEOUT_S = 300


def _translate(label: str, func: Callable[[], Any]) -> Any:
    """Run *func* and translate :class:`NSXError` → :class:`BuildError`.

    NSX raises ``NSXError`` for both ordinary subprocess failures and for
    the timeout-expired path (see ``neuralspotx.api._invoke``), so a
    single ``except`` is sufficient here.
    """

    try:
        return func()
    except NSXError as exc:
        log.error("%s failed: %s", label, exc)
        msg = str(exc).lower()
        if _is_network_error(msg):
            raise NetworkError(f"{label} failed (network)", details=str(exc)) from exc
        raise BuildError(f"{label} failed", details=str(exc)) from exc


# Heuristics for transient network failures from git/curl/fetch.
_NETWORK_KEYWORDS = (
    "could not resolve host",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "ssl_error",
    "tls handshake",
    "failed to connect",
    "unable to access",
    "the remote end hung up",
    "early eof",
)


def _is_network_error(msg: str) -> bool:
    return any(kw in msg for kw in _NETWORK_KEYWORDS)


# ---------------------------------------------------------------------------
# Public API — kwargs preserved from the previous subprocess-based shim so
# call sites in :mod:`helia_profiler.firmware` remain unchanged.
# ---------------------------------------------------------------------------


def configure(
    app_dir: Path,
    *,
    toolchain: str | None = None,
    timeout_s: int = _DEFAULT_CONFIGURE_TIMEOUT_S,
) -> None:
    """Run ``nsx configure`` on the given app directory."""
    log.info("nsx configure: %s (toolchain=%s)", app_dir, toolchain or "default")
    _translate(
        "nsx configure",
        lambda: nsx_api.configure_app(app_dir, toolchain=toolchain, timeout_s=timeout_s),
    )


def build(
    app_dir: Path,
    *,
    toolchain: str | None = None,
    timeout_s: int = _DEFAULT_BUILD_TIMEOUT_S,
) -> None:
    """Run ``nsx build`` on the given app directory."""
    log.info("nsx build: %s (toolchain=%s)", app_dir, toolchain or "default")
    _translate(
        "nsx build",
        lambda: nsx_api.build_app(app_dir, toolchain=toolchain, timeout_s=timeout_s),
    )


def flash(
    app_dir: Path,
    *,
    toolchain: str | None = None,
    jlink_serial: str | None = None,
    timeout_s: int = _DEFAULT_FLASH_TIMEOUT_S,
) -> None:
    """Run ``nsx flash`` on the given app directory.

    When *jlink_serial* is provided, ``SEGGER_SNCODE`` is temporarily set so
    the underlying CMake flash target selects the correct J-Link probe.
    """
    log.info("nsx flash: %s (toolchain=%s)", app_dir, toolchain or "default")

    prev_sncode = os.environ.get("SEGGER_SNCODE")
    if jlink_serial:
        os.environ["SEGGER_SNCODE"] = jlink_serial
        log.info("  J-Link serial: %s", jlink_serial)
    try:
        _translate(
            "nsx flash",
            lambda: nsx_api.flash_app(app_dir, toolchain=toolchain, timeout_s=timeout_s),
        )
    finally:
        # Restore previous state regardless of success/failure.
        if jlink_serial:
            if prev_sncode is None:
                os.environ.pop("SEGGER_SNCODE", None)
            else:
                os.environ["SEGGER_SNCODE"] = prev_sncode


# ---------------------------------------------------------------------------
# Lock / sync — used by the lock-aware build flow.
# ---------------------------------------------------------------------------


def lock(
    app_dir: Path,
    *,
    update: bool = False,
    timeout_s: int = _DEFAULT_LOCK_TIMEOUT_S,
) -> Path:
    """Resolve module constraints and write ``nsx.lock``."""
    log.info("nsx lock: %s (update=%s)", app_dir, update)
    return _translate(
        "nsx lock",
        lambda: nsx_api.lock_app(app_dir, update=update, quiet=True, timeout_s=timeout_s),
    )


def sync(
    app_dir: Path,
    *,
    frozen: bool = False,
    force: bool = False,
    timeout_s: int = _DEFAULT_SYNC_TIMEOUT_S,
    retries: int = 3,
) -> None:
    """Materialise ``modules/`` so it exactly matches ``nsx.lock``.

    Retries up to *retries* times on transient :class:`NetworkError` with
    exponential backoff (2s, 4s, 8s …).
    """
    log.info("nsx sync: %s (frozen=%s, force=%s)", app_dir, frozen, force)
    last_exc: NetworkError | None = None
    for attempt in range(1, retries + 1):
        try:
            _translate(
                "nsx sync",
                lambda: nsx_api.sync_app(
                    app_dir, frozen=frozen, force=force, timeout_s=timeout_s
                ),
            )
            return
        except NetworkError as exc:
            last_exc = exc
            if attempt < retries:
                delay = 2**attempt
                log.warning(
                    "nsx sync: transient network error (attempt %d/%d), "
                    "retrying in %ds…",
                    attempt,
                    retries,
                    delay,
                )
                time.sleep(delay)
    # All retries exhausted — re-raise the last NetworkError.
    raise last_exc  # type: ignore[misc]
