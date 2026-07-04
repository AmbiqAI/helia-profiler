"""DEPRECATED: J-Link helpers moved to :mod:`helia_profiler.target.probe.jlink`."""

from __future__ import annotations

from .target.probe.jlink import (
    JLinkFlashBackend,
    JLinkProbe,
    JLinkProbeMatch,
    JLinkResetController,
    create_debug_memory_session,
    find_jlink_exe,
    inspect_probe_target,
    list_connected_probes,
    resolve_probe_serial,
    reset_target,
    reset_target_poi,
    run_jlink_script,
)
from .target.probe.jlink import _inspect_probe_target

__all__ = [
    "JLinkProbe",
    "JLinkProbeMatch",
    "JLinkResetController",
    "JLinkFlashBackend",
    "create_debug_memory_session",
    "find_jlink_exe",
    "inspect_probe_target",
    "list_connected_probes",
    "resolve_probe_serial",
    "reset_target",
    "reset_target_poi",
    "run_jlink_script",
]
