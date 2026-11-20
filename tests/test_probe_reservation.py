from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from threading import Event

import pytest

from helia_profiler.target.probe import reservation


@pytest.mark.parametrize(("owner", "waiter"), [("123", None), (None, "123"), ("123", "123")])
def test_same_or_implicit_target_waits_for_reservation(tmp_path, monkeypatch, owner, waiter):
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))
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
