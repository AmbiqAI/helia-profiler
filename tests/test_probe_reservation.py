import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from threading import Event

import pytest

from helia_profiler.target.probe import reservation


@pytest.mark.parametrize(("owner", "waiter"), [("123", None), (None, "123"), ("123", "123")])
def test_same_or_implicit_target_waits_for_reservation(tmp_path, monkeypatch, owner, waiter):
    monkeypatch.setattr(reservation, "_reservation_directory", lambda: tmp_path / "probe-locks")
    trying = Event()
    primary = tmp_path / "probe-locks"
    contested = primary / (
        "registration" if owner is None else sha256(b"123").hexdigest() + ".lock"
    )
    mutex = reservation.file_mutex

    @contextmanager
    def observed(path):
        if path == contested:
            trying.set()
        with mutex(path):
            yield

    def enter():
        with reservation.reserve_probe(waiter):
            return True

    with ThreadPoolExecutor() as pool:
        with reservation.reserve_probe(owner):
            with reservation.reserve_probe("123"):
                monkeypatch.setattr(reservation, "file_mutex", observed)
                if owner == "123":

                    def other_board():
                        with reservation.reserve_probe("456"):
                            return True

                    assert pool.submit(other_board).result(timeout=5)
                other = pool.submit(enter)
                assert trying.wait(timeout=5)
                assert not other.done()
        assert other.result(timeout=5)


def test_independent_job_caches_share_the_probe_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "terminal-cache"))
    serial = "hpx-test-cross-cache-reservation"
    child = """
import sys
from helia_profiler.target.probe.reservation import reserve_probe
print("ready", flush=True)
with reserve_probe(sys.argv[1]):
    print("acquired", flush=True)
"""
    process = None
    try:
        with reservation.reserve_probe(serial):
            process = subprocess.Popen(
                [sys.executable, "-c", child, serial],
                stdout=subprocess.PIPE,
                text=True,
                env={**os.environ, "HPX_CACHE_DIR": str(tmp_path / "runner-cache")},
            )
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.3)
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 0
        assert stdout.strip() == "acquired"
    finally:
        if process is not None:
            process.kill()
            process.wait()
