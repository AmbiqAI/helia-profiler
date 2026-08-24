"""Compiler-launcher resolution (sccache/ccache) for generated app builds.

Owns the ``build.compiler_launcher`` / ``HPX_COMPILER_LAUNCHER`` resolution:
the auto-detect launcher list, the disabled-value vocabulary, the
per-toolchain launcher compatibility table, and the resolver itself.
Extracted from ``firmware/__init__`` at the module size ceiling (see the
elf_inventory precedent in toolchain_probe); the package re-exports every
name so callers keep one import surface.

NOTE: ``shutil`` is imported as a module (never ``from shutil import
which``) so tests that monkeypatch ``helia_profiler.firmware.shutil.which``
keep patching the same module object this code reads at call time.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import FirmwareError

if TYPE_CHECKING:
    from ..config import ProfileConfig

log = logging.getLogger("hpx")


# Compiler launchers tried, in order, when ``build.compiler_launcher`` is
# ``"auto"``.  sccache is preferred (better cross-platform + CI story); ccache
# is the common local fallback.
_AUTO_COMPILER_LAUNCHERS: tuple[str, ...] = ("sccache", "ccache")
_DISABLED_LAUNCHER_VALUES = frozenset({"", "none", "off", "false", "disabled", "0"})

# Compiler launchers that do not understand a given toolchain's compiler driver.
# sccache rejects armclang outright ("Compiler not supported"), and because it
# wraps the driver it also drops ``--target``, which surfaces as the misleading
# ``armclang: fatal error: no target architecture given``.  Auto-detect must
# therefore treat sccache as unavailable for these toolchains rather than
# silently breaking the build.
_LAUNCHER_UNSUPPORTED_TOOLCHAINS: dict[str, frozenset[str]] = {
    "sccache": frozenset({"armclang"}),
}


def _launcher_basename(launcher: str) -> str:
    """Return the bare tool name for a launcher path or command."""
    return Path(launcher).name.lower()


def _launcher_supports_toolchain(launcher: str, toolchain: str) -> bool:
    """Whether ``launcher`` can wrap ``toolchain``'s compiler driver."""
    unsupported = _LAUNCHER_UNSUPPORTED_TOOLCHAINS.get(_launcher_basename(launcher))
    return not (unsupported and toolchain in unsupported)


def _resolve_compiler_launcher(config: "ProfileConfig") -> str | None:
    """Resolve the CMake compiler launcher executable for this build.

    Precedence: the ``HPX_COMPILER_LAUNCHER`` environment variable overrides
    ``build.compiler_launcher``.  Returns an absolute path to the launcher, or
    ``None`` when caching is disabled or no launcher is available.

    * ``"auto"`` — use the first of :data:`_AUTO_COMPILER_LAUNCHERS` found on
      ``PATH`` that supports the active toolchain; do nothing if none are
      installed (installing the binary is the opt-in).
    * disabled values (``none``/``off``/``false``/empty) — ``None``.
    * an explicit tool name or path — required: raises if it cannot be found.
      If the named launcher cannot wrap the active toolchain (e.g. sccache with
      armclang) it is skipped with a warning rather than breaking the build.
    """
    toolchain = config.target.toolchain
    setting = os.environ.get("HPX_COMPILER_LAUNCHER")
    source = "HPX_COMPILER_LAUNCHER"
    if setting is None:
        setting = config.build.compiler_launcher
        source = "build.compiler_launcher"
    setting = setting.strip()

    if setting.lower() in _DISABLED_LAUNCHER_VALUES:
        return None

    if setting.lower() == "auto":
        for name in _AUTO_COMPILER_LAUNCHERS:
            found = shutil.which(name)
            if not found:
                continue
            if not _launcher_supports_toolchain(name, toolchain):
                log.debug(
                    "Skipping compiler launcher %s: unsupported for toolchain %s",
                    name,
                    toolchain,
                )
                continue
            log.info("Using compiler launcher: %s (auto-detected)", found)
            return found
        return None

    found = shutil.which(setting)
    if found is None and Path(setting).is_file() and os.access(setting, os.X_OK):
        found = str(Path(setting).resolve())
    if found is None:
        raise FirmwareError(
            f"Compiler launcher {setting!r} (from {source}) was not found on PATH.",
            hint=(
                "Install it, use the full path, or set the launcher to 'auto'/'none'. "
                "For sccache: https://github.com/mozilla/sccache."
            ),
        )
    if not _launcher_supports_toolchain(setting, toolchain):
        log.warning(
            "Compiler launcher %r (from %s) does not support the %s toolchain; "
            "disabling it for this build.",
            setting,
            source,
            toolchain,
        )
        return None
    log.info("Using compiler launcher: %s (from %s)", found, source)
    return found
