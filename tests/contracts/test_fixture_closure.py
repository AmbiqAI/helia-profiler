"""The fixture source closure is current, recomputable, and covers every file a build loads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

import helia_profiler
from helia_profiler._fixture_closure import CLOSURE_LISTING, closure_digest, closure_paths
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import (
    FIXTURE_API_VERSION,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureTimingScope,
    TypedFixture,
    build_fixed_fixture,
    source_closure,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(helia_profiler.__file__).resolve().parent
_recording: list[set[str]] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    if not _recording or event != "open" or not isinstance(args[0], str | Path):
        return
    path = Path(args[0]).resolve()
    if path.is_relative_to(PACKAGE) and "__pycache__" not in path.parts:
        _recording[-1].add(path.relative_to(PACKAGE).as_posix())


sys.addaudithook(_audit)


@contextmanager
def _package_reads() -> Iterator[set[str]]:
    """Record every package file opened inside the block."""
    reads: set[str] = set()
    _recording.append(reads)
    try:
        yield reads
    finally:
        _recording.remove(reads)


def _listed() -> set[str]:
    return set(closure_paths((PACKAGE / CLOSURE_LISTING).read_bytes()))


def test_listing_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "tools/gen_fixture_closure.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_source_closure_matches_an_independent_recomputation() -> None:
    closure = source_closure()
    listing = (PACKAGE / CLOSURE_LISTING).read_bytes()
    digest = hashlib.sha256(listing)
    for path in listing.decode().splitlines():
        digest.update(
            f"{path}\0{hashlib.sha256((PACKAGE / path).read_bytes()).hexdigest()}\n".encode()
        )
    assert closure.digest == digest.hexdigest()
    assert closure.api_version == FIXTURE_API_VERSION
    assert [path for path, _ in closure.files] == listing.decode().splitlines()


def test_digest_moves_with_any_listed_byte() -> None:
    files = (("a.py", "0" * 64), ("b.j2", "1" * 64))
    base = closure_digest(b"a.py\nb.j2\n", files)
    assert closure_digest(b"a.py\nb.j2\n", (files[0], ("b.j2", "2" * 64))) != base
    assert closure_digest(b"a.py\n", files[:1]) != base


@pytest.mark.parametrize(
    "listing", [b"", b"b.py\na.py\n", b"a.py\na.py\n", b"../x.py\n", b"/x.py\n"]
)
def test_malformed_listings_are_refused(listing: bytes) -> None:
    with pytest.raises(ConfigError):
        closure_paths(listing)


def test_source_closure_refuses_a_missing_file(tmp_path: Path) -> None:
    (tmp_path / CLOSURE_LISTING).write_bytes(b"gone.py\n")
    with pytest.raises(ConfigError, match="gone.py"):
        source_closure(tmp_path)


def test_heliaaot_fixture_build_reads_only_closure_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real host-only heliaAOT fixture build loads templates and vendored sources from the closure."""
    pytest.importorskip("helia_aot")
    from helia_profiler.config import load_config
    from helia_profiler.fixture_analysis import analyze_typed_fixture_model

    monkeypatch.delenv("SEGGER_RTT_PATH", raising=False)
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    model = tmp_path / "tiny_cnn.tflite"
    model.write_bytes((PACKAGE / "data" / "models" / "tiny_cnn.tflite").read_bytes())

    def pin(path: Path, data: bytes) -> FixtureFile:
        path.write_bytes(data)
        return FixtureFile(path, hashlib.sha256(data).hexdigest())

    analysis = analyze_typed_fixture_model(model)
    fixture = TypedFixture(
        pin(model, model.read_bytes()),
        tuple(
            FixtureIO(t, pin(tmp_path / f"in{i}.bin", bytes(t.size_bytes)))
            for i, t in enumerate(analysis.inputs)
        ),
        tuple(
            FixtureIO(t, pin(tmp_path / f"out{i}.bin", bytes(t.size_bytes)))
            for i, t in enumerate(analysis.outputs)
        ),
    )
    config = load_config(
        None,
        {
            "model": {
                "path": str(model),
                "arena_size": 65536,
                "arena_location": "sram",
                "weights_location": "mram",
            },
            "engine": {"type": "helia-aot"},
            "profiling": {"iterations": 3, "warmup": 1},
            "target": {"toolchain": "atfe", "board": "apollo510_evb", "clock": {"cpu": "lp"}},
            "work_dir": str(tmp_path / "work"),
        },
    )
    with _package_reads() as reads:
        build_fixed_fixture(
            config, fixture, method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY), compile=False
        )
    assert {
        "engines/templates/heliaaot_attributes.h.j2",
        "firmware/templates/fixed_fixture.cc.j2",
        "vendor/segger_rtt/RTT/SEGGER_RTT.c",
    } <= reads, "the tracer must see the engine template, firmware template and vendored copy"
    assert reads - _listed() == set()
