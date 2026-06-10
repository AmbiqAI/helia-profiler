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

import contextlib
import functools
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from neuralspotx import api as nsx_api
from neuralspotx._io import Emitter, Event
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


# Quiet mode temporarily swaps the process stdio file descriptors so noisy NSX
# subprocesses inherit /dev/null. Serialize that swap so concurrent quiet-mode
# calls cannot race and restore each other's stdout/stderr handles incorrectly.
_QUIET_OUTPUT_LOCK = threading.RLock()


def _quiet_emitter(event: Event) -> None:
    """Swallow NSX output — used at default verbosity (no ``-v``)."""


def emitter_for_verbosity(verbose: int) -> Emitter | None:
    """Return the appropriate NSX emitter for a given verbosity level.

    * verbose == 0 → quiet (suppress cmake/ninja/JLink output)
    * verbose >= 1 → None (use neuralspotx default: prints to stderr/stdout)
    """
    if verbose >= 1:
        return None
    return _quiet_emitter


@contextlib.contextmanager
def _suppress_output() -> Iterator[None]:
    """Redirect fd 1 & 2 to ``/dev/null`` for the duration of the block.

    This silences subprocess output that neuralspotx streams directly to the
    terminal (cmake, ninja, JLinkExe).  Python-level writes to sys.stdout /
    sys.stderr are also suppressed since the underlying fds are redirected.
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stdout_fd = os.dup(1)
    saved_stderr_fd = os.dup(2)
    try:
        # Flush Python buffers before redirecting the underlying fds.
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        # Flush again (any buffered writes during the block go to devnull).
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_stdout_fd, 1)
        os.dup2(saved_stderr_fd, 2)
        os.close(saved_stdout_fd)
        os.close(saved_stderr_fd)
        os.close(devnull_fd)


@contextlib.contextmanager
def _quiet_context(verbose: int) -> Iterator[None]:
    """Yield a context that suppresses subprocess output when *verbose* == 0."""
    if verbose >= 1:
        yield
    else:
        with _QUIET_OUTPUT_LOCK:
            with _suppress_output():
                yield


def _translate(label: str, func: Callable[[], Any]) -> Any:
    """Run *func* and translate :class:`NSXError` → :class:`BuildError`.

    NSX raises ``NSXError`` for both ordinary subprocess failures and for
    the timeout-expired path (see ``neuralspotx.api._invoke``), so a
    single ``except`` is sufficient here.  We also catch
    :class:`subprocess.CalledProcessError` from operations that do not
    wrap it in ``NSXError`` (e.g. the build path at verbosity 0).
    """
    import subprocess

    try:
        return func()
    except NSXError as exc:
        log.error("%s failed: %s", label, exc)
        msg = str(exc).lower()
        if _is_network_error(msg):
            raise NetworkError(f"{label} failed (network)", details=str(exc)) from exc
        raise BuildError(f"{label} failed", details=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        details = f"exit code {exc.returncode}: {' '.join(str(a) for a in exc.cmd)}"
        if exc.stderr:
            details += f"\n{exc.stderr}"
        log.error("%s failed: %s", label, details)
        raise BuildError(
            f"{label} failed",
            details=details,
            hint="Re-run with -v for full subprocess output.",
        ) from exc


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
    verbose: int = 0,
) -> None:
    """Run ``nsx configure`` on the given app directory."""
    log.info("nsx configure: %s (toolchain=%s)", app_dir, toolchain or "default")
    emit = emitter_for_verbosity(verbose)
    with _quiet_context(verbose):
        _translate(
            "nsx configure",
            lambda: nsx_api.configure_app(
                app_dir, toolchain=toolchain, timeout_s=timeout_s, emit=emit
            ),
        )


def build(
    app_dir: Path,
    *,
    toolchain: str | None = None,
    timeout_s: int = _DEFAULT_BUILD_TIMEOUT_S,
    verbose: int = 0,
) -> None:
    """Run ``nsx build`` on the given app directory."""
    log.info("nsx build: %s (toolchain=%s)", app_dir, toolchain or "default")
    emit = emitter_for_verbosity(verbose)
    with _quiet_context(verbose):
        _translate(
            "nsx build",
            lambda: nsx_api.build_app(app_dir, toolchain=toolchain, timeout_s=timeout_s, emit=emit),
        )


def flash(
    app_dir: Path,
    *,
    toolchain: str | None = None,
    jlink_serial: str | None = None,
    timeout_s: int = _DEFAULT_FLASH_TIMEOUT_S,
    verbose: int = 0,
) -> None:
    """Run ``nsx flash`` on the given app directory.

    When *jlink_serial* is provided it is forwarded to ``flash_app`` as the
    ``probe_serial`` so the underlying J-Link tool selects the correct probe
    (required when multiple probes are attached).
    """
    log.info("nsx flash: %s (toolchain=%s)", app_dir, toolchain or "default")
    emit = emitter_for_verbosity(verbose)

    if jlink_serial:
        log.info("  J-Link serial: %s", jlink_serial)
    with _quiet_context(verbose):
        _translate(
            "nsx flash",
            lambda: nsx_api.flash_app(
                app_dir,
                toolchain=toolchain,
                probe_serial=jlink_serial,
                timeout_s=timeout_s,
                emit=emit,
            ),
        )


# ---------------------------------------------------------------------------
# Lock / sync — used by the lock-aware build flow.
# ---------------------------------------------------------------------------


_RESOLVE_TTL_S: float = 1800  # 30 min — safe for typical profiling sessions


def lock(
    app_dir: Path,
    *,
    update: bool = False,
    timeout_s: int = _DEFAULT_LOCK_TIMEOUT_S,
    verbose: int = 0,
) -> Path:
    """Resolve module constraints and write ``nsx.lock``."""
    log.info("nsx lock: %s (update=%s)", app_dir, update)
    emit = emitter_for_verbosity(verbose)
    with _quiet_context(verbose):
        return _translate(
            "nsx lock",
            lambda: nsx_api.lock_app(
                app_dir,
                update=update,
                quiet=True,
                timeout_s=timeout_s,
                resolve_ttl_s=_RESOLVE_TTL_S,
                emit=emit,
            ),
        )


def sync(
    app_dir: Path,
    *,
    frozen: bool = False,
    force: bool = False,
    timeout_s: int = _DEFAULT_SYNC_TIMEOUT_S,
    retries: int = 3,
    verbose: int = 0,
) -> None:
    """Materialise ``modules/`` so it exactly matches ``nsx.lock``.

    Retries up to *retries* times on transient :class:`NetworkError` with
    exponential backoff (2s, 4s, 8s …).
    """
    log.info("nsx sync: %s (frozen=%s, force=%s)", app_dir, frozen, force)
    emit = emitter_for_verbosity(verbose)
    last_exc: NetworkError | None = None
    for attempt in range(1, retries + 1):
        try:
            with _quiet_context(verbose):
                _translate(
                    "nsx sync",
                    lambda: nsx_api.sync_app(
                        app_dir, frozen=frozen, force=force, timeout_s=timeout_s, emit=emit
                    ),
                )
            return
        except NetworkError as exc:
            last_exc = exc
            if attempt < retries:
                delay = 2**attempt
                log.warning(
                    "nsx sync: transient network error (attempt %d/%d), retrying in %ds…",
                    attempt,
                    retries,
                    delay,
                )
                time.sleep(delay)
    # All retries exhausted — re-raise the last NetworkError.
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NSX module registry / starter-profile access
#
# The NSX registry (registry.lock.yaml shipped inside neuralspotx) is the
# single source of truth for which NSX project owns each module and which
# starter profile a board resolves to. The firmware generator consults it so
# module/project ownership is *derived* rather than hand-maintained — the same
# data ``nsx create-app`` uses to scaffold a manifest.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    """Return the effective NSX registry (cached).

    This is the in-memory registry.lock with derived ``starter_profiles``. It
    resolves purely from the local neuralspotx install — no network or git.
    """
    if hasattr(nsx_api, "load_registry"):
        return nsx_api.load_registry()

    from neuralspotx.project_config import _load_registry

    return _load_registry()


def starter_profile(board: str) -> dict[str, Any] | None:
    """Return the ``{board}_minimal`` starter profile, or *None* if absent."""
    if hasattr(nsx_api, "starter_profile"):
        return nsx_api.starter_profile(board)

    profiles = load_registry().get("starter_profiles", {})
    return profiles.get(f"{board}_minimal")


def registry_module_project(name: str) -> str | None:
    """Resolve a module name to its owning project via the base registry.

    Returns *None* when the module has no registry entry (e.g. a local module
    such as a generated heliaRT wrapper).
    """
    if hasattr(nsx_api, "registry_module_project"):
        return nsx_api.registry_module_project(name)

    from neuralspotx.metadata import registry_entry_for_module

    try:
        return registry_entry_for_module(load_registry(), name).project
    except (KeyError, ValueError):
        return None
