"""Concurrent profiles retain their own engine, firmware, and report inputs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, current_thread
from typing import Iterator

import pytest

from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.errors import HpxError
from helia_profiler.pipeline import PipelineContext, PipelineRunner
import helia_profiler.pipeline as pipeline


def _config(tmp_path: Path, label: str, *, cached: bool, clean: bool = False) -> ProfileConfig:
    return load_config(
        None,
        {
            "model": {"path": str(tmp_path / f"{label}.tflite")},
            "engine": {"type": "tflm"},
            "target": {"jlink_serial": label},
            "clean": clean,
            **({} if cached else {"work_dir": str(tmp_path / "work")}),
        },
    )


class ArtifactStage:
    """Simulate shared engine preparation and profile/power artifact consumption."""

    def __init__(self, name: str, observations: list[tuple[str, str]]) -> None:
        self.name = name
        self.observations = observations

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        label = ctx.config.model.path.stem
        engine = ctx.work_dir / "engine.cc"
        profile = ctx.work_dir / "profile.bin"
        power = ctx.work_dir / "power.bin"
        if self.name == "prepare_engine":
            engine.write_text(label)
        elif self.name == "build_profile":
            profile.write_text(engine.read_text())
        elif self.name == "flash_profile":
            assert profile.read_text() == label
        elif self.name == "build_power":
            power.write_text(engine.read_text())
        elif self.name == "flash_power":
            assert power.read_text() == label
        elif self.name == "report":
            assert (engine.read_text(), profile.read_text(), power.read_text()) == (label,) * 3
        self.observations.append((label, self.name))


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("pause_at", ["prepare_engine", "build_profile", "build_power", "report"])
def test_overlapping_runs_keep_artifacts_until_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool, pause_at: str
) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    first_paused, second_lock_attempt, release_first = Event(), Event(), Event()
    observations: list[tuple[str, str]] = []
    real_mutex = pipeline.file_mutex

    @contextmanager
    def observed_mutex(path: Path) -> Iterator[None]:
        if current_thread().name.startswith("second"):
            second_lock_attempt.set()
        with real_mutex(path):
            yield

    monkeypatch.setattr(pipeline, "file_mutex", observed_mutex)

    class PausingStage(ArtifactStage):
        def run(self, ctx: PipelineContext) -> None:
            if cached and self.name == "prepare_engine":
                stale = ctx.work_dir / "stale-artifact"
                if ctx.config.model.path.stem == "first":
                    stale.write_text("old")
                else:
                    assert not stale.exists()
            super().run(ctx)
            if ctx.config.model.path.stem == "first" and self.name == pause_at:
                first_paused.set()
                assert release_first.wait(5), "test did not release first run"

    names = [
        "prepare_engine",
        "build_profile",
        "flash_profile",
        "build_power",
        "flash_power",
        "report",
    ]
    runner = PipelineRunner([PausingStage(name, observations) for name in names])
    first = _config(tmp_path, "first", cached=cached)
    second = _config(tmp_path, "second", cached=cached, clean=cached)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="first") as first_pool,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="second") as second_pool,
    ):
        first_future = first_pool.submit(runner.run, first)
        try:
            assert first_paused.wait(5)
            second_future = second_pool.submit(runner.run, second)
            assert second_lock_attempt.wait(5)
            assert all(label == "first" for label, _ in observations)
        finally:
            release_first.set()
        first_ctx = first_future.result(timeout=5)
        second_ctx = second_future.result(timeout=5)
    assert first_ctx.work_dir == second_ctx.work_dir
    assert observations == [(label, name) for label in ("first", "second") for name in names]


def test_failed_run_releases_workspace(tmp_path: Path) -> None:
    class FailingStage(ArtifactStage):
        def run(self, ctx: PipelineContext) -> None:
            raise HpxError("engine failed")

    config = _config(tmp_path, "first", cached=False)
    with pytest.raises(HpxError, match="engine failed"):
        PipelineRunner([FailingStage("prepare_engine", [])]).run(config)
    observations: list[tuple[str, str]] = []
    PipelineRunner([ArtifactStage("prepare_engine", observations)]).run(config)
    assert observations == [("first", "prepare_engine")]


def test_distinct_workspaces_can_run_concurrently(tmp_path: Path) -> None:
    first_started, second_started = Event(), Event()

    class RendezvousStage(ArtifactStage):
        def run(self, ctx: PipelineContext) -> None:
            own, other = (
                (first_started, second_started)
                if ctx.config.model.path.stem == "first"
                else (second_started, first_started)
            )
            own.set()
            assert other.wait(5), "independent workspace was blocked"

    runner = PipelineRunner([RendezvousStage("prepare_engine", [])])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(runner.run, _config(tmp_path / label, label, cached=False))
            for label in ("first", "second")
        ]
        for future in futures:
            future.result(timeout=5)


@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_clean_continues_after_entry_deletion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    entry_kind: str,
) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    config = _config(tmp_path, "first", cached=True, clean=True)
    work_dir, _ = pipeline._resolve_work_dir(config)
    blocked = work_dir / "blocked-entry"
    if entry_kind == "directory":
        blocked.mkdir()
    else:
        blocked.write_text("old")
    removable_file = work_dir / "removable-file"
    removable_file.write_text("old")
    removable_dir = work_dir / "removable-directory"
    removable_dir.mkdir()
    lock_path = work_dir / ".hpx-run.lock"
    lock_path.touch()
    original_lock = lock_path.stat()
    real_iterdir, real_unlink, real_rmtree = Path.iterdir, Path.unlink, pipeline.shutil.rmtree

    def ordered_entries(path: Path) -> Iterator[Path]:
        if path == work_dir:
            return iter([blocked, removable_file, removable_dir, lock_path])
        return real_iterdir(path)

    def unlink(path: Path, *args, **kwargs) -> None:
        if path == blocked:
            raise PermissionError("file is in use")
        real_unlink(path, *args, **kwargs)

    def rmtree(path, *args, **kwargs) -> None:
        if Path(path) == blocked:
            raise PermissionError("directory is read-only")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", ordered_entries)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(pipeline.shutil, "rmtree", rmtree)
    observations: list[tuple[str, str]] = []
    PipelineRunner([ArtifactStage("prepare_engine", observations)]).run(config)

    assert observations == [("first", "prepare_engine")]
    assert blocked.exists()
    assert not removable_file.exists()
    assert not removable_dir.exists()
    assert lock_path.stat().st_ino == original_lock.st_ino
    assert str(blocked) in caplog.text
    assert "Close programs using it or check permissions" in caplog.text
    assert "continuing with remaining cache contents" in caplog.text
