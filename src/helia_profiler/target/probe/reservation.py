"""Serialize HPX operations that target the same physical debug probe."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from hashlib import sha256

from neuralspotx.file_lock import file_mutex

from ...hostenv.cache_dirs import hpx_cache_root

_held: ContextVar[tuple[str | None, ...]] = ContextVar("hpx_reserved_probes", default=())


@contextmanager
def reserve_probe(serial: str | None) -> Iterator[None]:
    held = _held.get()
    if None in held or serial in held:
        yield
        return
    directory = hpx_cache_root() / "probe-locks"
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
