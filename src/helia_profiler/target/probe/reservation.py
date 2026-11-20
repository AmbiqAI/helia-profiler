"""Serialize HPX captures that target the same physical debug probe."""

from hashlib import sha256

from neuralspotx.file_lock import file_mutex

from ...hostenv.cache_dirs import hpx_cache_root


def reserve_probe(serial: str):
    name = sha256(serial.encode()).hexdigest()
    return file_mutex(hpx_cache_root() / "probe-locks" / f"{name}.lock")
