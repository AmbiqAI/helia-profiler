"""Serialize HPX operations that target the same physical debug probe."""

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path

from neuralspotx.file_lock import file_mutex

from ...errors import ConfigError

_held: ContextVar[tuple[str | None, ...]] = ContextVar("hpx_reserved_probes", default=())


def _reservation_directory() -> Path:
    if os.name == "nt":
        base = (
            Path(os.environ["LOCALAPPDATA"])
            if "LOCALAPPDATA" in os.environ
            else Path.home() / "AppData" / "Local"
        )
        return base / "helia-profiler" / "probe-locks"
    # Job-specific cache and temporary directories cannot coordinate access to a physical probe.
    uid = os.getuid()
    directory = Path("/var/tmp") / f"helia-profiler-probes-{uid}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise ConfigError(
            f"Expected a private probe reservation directory owned by UID {uid}: {directory}"
        )
    return directory


@contextmanager
def reserve_probe(serial: str | None) -> Iterator[None]:
    held = _held.get()
    if None in held or serial in held:
        yield
        return
    directory = _reservation_directory()
    registry = directory / "registration"
    with ExitStack() as locks:
        if serial is None:
            # Auto-selection can power a board before its probe even enumerates.
            # Drain registered probes and exclude new registrations through selection/use.
            locks.enter_context(file_mutex(registry))
            for path in sorted(directory.glob("*.lock")):
                locks.enter_context(file_mutex(path))
        else:
            path = directory / (sha256(serial.encode()).hexdigest() + ".lock")
            with file_mutex(registry):
                path.touch(exist_ok=True)
            locks.enter_context(file_mutex(path))
        token = _held.set((*held, serial))
        try:
            yield
        finally:
            _held.reset(token)
