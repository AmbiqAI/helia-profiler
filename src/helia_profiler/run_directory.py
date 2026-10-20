"""A run directory: one compiled build, its pinned inputs and its captures, in one folder.

``hpx build`` writes one; ``hpx validate build`` and ``hpx run`` read it::

    DIR/build.json      the saved CompiledBuild
    DIR/inputs/         the model and one input_<index>.bin per model input
    DIR/work/           the firmware build, unless another work directory is named
    DIR/evidence-<N>/   one per capture

The saved build names two roots, ``run`` for the directory and ``cache`` for
the HPX cache that holds a prepared runtime, so the directory can move to
another host as a unit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ConfigError
from .lifecycle import Lifecycle

if TYPE_CHECKING:
    from ._fixture_build import FixtureTimingScope, TypedFixture
    from .compiled_build import CompiledBuild, RunResult
    from .expect import Expect
    from .fixture_capture import FixtureCaptureGuard
    from .fixture_runtime import FixtureFile
    from .frozen_run import FrozenRun

BUILD_FILE = "build.json"
INPUTS_DIR = "inputs"
WORK_DIR = "work"
EVIDENCE_PREFIX = "evidence-"


def run_roots(directory: str | Path) -> dict[str, Path]:
    """The roots a run directory's build is saved and loaded with."""
    from .hostenv.cache_dirs import hpx_cache_root

    return {"run": _absolute(directory), "cache": hpx_cache_root()}


def pin_fixture(
    model: str | Path, directory: str | Path, inputs: Sequence[str | Path] = ()
) -> TypedFixture:
    """A fixture of *model*, its files copied under ``directory/inputs``.

    *inputs* holds one file of raw bytes per model input, in model order;
    without them every input is zero-filled. Inputs are named by index,
    since a tensor name such as ``serving_default:0`` is no portable filename.

    Raises:
        ConfigError: A file is unreadable, or the inputs do not match the
            model's input count or byte sizes.
    """
    from ._fixture_build import FixtureIO, FixtureOutput, TypedFixture
    from .run_inputs import analyze_run_model

    target = _absolute(directory) / INPUTS_DIR
    source = Path(model).expanduser()
    pinned = _pin(
        target / source.name,
        _read(source, "model", hint="Name the model the plan was frozen against."),
    )
    analysis = analyze_run_model(pinned.path)
    if inputs and len(inputs) != len(analysis.inputs):
        raise ConfigError(
            f"The model has {len(analysis.inputs)} input(s); {len(inputs)} input file(s) given",
            hint="Pass one input file per model input, in model order.",
        )
    payloads = (
        [_read(Path(path).expanduser(), f"input {index}") for index, path in enumerate(inputs)]
        if inputs
        else [bytes(tensor.size_bytes) for tensor in analysis.inputs]
    )
    for index, (tensor, payload) in enumerate(zip(analysis.inputs, payloads, strict=True)):
        if len(payload) != tensor.size_bytes:
            raise ConfigError(
                f"Input {index} ({tensor.name}) holds {len(payload)} bytes; the model's "
                f"{tensor.dtype} tensor of shape {list(tensor.shape)} takes {tensor.size_bytes}"
            )
    return TypedFixture(
        pinned,
        tuple(
            FixtureIO(tensor, _pin(target / f"input_{index}.bin", payload))
            for index, (tensor, payload) in enumerate(zip(analysis.inputs, payloads, strict=True))
        ),
        tuple(FixtureOutput(tensor) for tensor in analysis.outputs),
    )


def build_into(
    plan: FrozenRun,
    directory: str | Path,
    *,
    inputs: Sequence[str | Path] = (),
    model: str | Path | None = None,
    lifecycle: Lifecycle = Lifecycle.CARRY,
    timing_scope: FixtureTimingScope | str = "invoke_only",
    work_dir: str | Path | None = None,
) -> CompiledBuild:
    """Build *plan* for pinned inputs into *directory* and save it as ``build.json``.

    *model* is the plan's model when it is no longer at the path the plan
    records. *work_dir* defaults to ``directory/work``.
    """
    from ._fixture_build import FixtureMethod, FixtureTimingScope
    from .compiled_build import build

    root = _absolute(directory)
    fixture = pin_fixture(model if model is not None else plan.model.path, root, inputs)
    compiled = build(
        plan,
        fixture,
        method=FixtureMethod(FixtureTimingScope(timing_scope)),
        lifecycle=lifecycle,
        model=fixture.model.path,
        host={"work_dir": str(_absolute(work_dir) if work_dir else root / WORK_DIR)},
    )
    compiled.save(root / BUILD_FILE, roots=run_roots(root))
    return compiled


def load_from(directory: str | Path) -> CompiledBuild:
    """Load and verify the build saved in *directory*."""
    from .compiled_build import load_build

    return load_build(_absolute(directory) / BUILD_FILE, roots=run_roots(directory))


def fresh_evidence_dir(directory: str | Path) -> Path:
    """The first ``directory/evidence-<N>``, counting from 1, that does not exist yet."""
    root, index = _absolute(directory), 1
    while (root / f"{EVIDENCE_PREFIX}{index}").exists():
        index += 1
    return root / f"{EVIDENCE_PREFIX}{index}"


def run_from(
    directory: str | Path,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: str | Path,
    settle_seconds: float,
    expect: Expect | None = None,
) -> RunResult:
    """Capture once from the build saved in *directory*.

    The guard sees the build's target before anything else runs, so a board
    or probe it refuses is refused before the evidence directory exists or
    any device is touched.
    """
    from .compiled_build import run_build

    compiled = load_from(directory)
    guard.verify_target(target=compiled.build.target, jlink_serial=jlink_serial)
    return run_build(
        compiled,
        guard=guard,
        jlink_serial=jlink_serial,
        evidence_dir=_absolute(evidence_dir),
        settle_seconds=settle_seconds,
        expect=expect,
    )


def build_summary(directory: str | Path, compiled: CompiledBuild) -> dict[str, Any]:
    """The saved build's path and identity, JSON-ready."""
    return {
        "build": str(_absolute(directory) / BUILD_FILE),
        "identity": compiled.identity.to_dict(),
    }


def run_summary(result: RunResult, evidence_dir: str | Path) -> dict[str, Any]:
    """A capture's outcome, identity, verification facts and key timing, JSON-ready."""
    capture, verification = result.capture, result.verification
    timing, memory = capture.timing, capture.memory
    return {
        "succeeded": result.succeeded,
        "error": capture.error,
        "evidence_dir": str(_absolute(evidence_dir)),
        "identity": result.identity.to_dict(),
        "verification": None
        if verification is None
        else {
            "reference": verification.reference.origin.kind,
            "tolerance": None
            if verification.tolerance is None
            else verification.tolerance.max_abs_error,
            "matched": verification.matched,
            "tensors": [
                {
                    "name": tensor.name,
                    "identical": tensor.identical,
                    "mismatched_elements": tensor.mismatched_elements,
                    "max_abs_error": tensor.max_abs_error,
                }
                for tensor in verification.tensors
            ],
        },
        "timing": None
        if timing is None
        else {
            # A failed capture can carry the terminal it refused, zero counts included.
            "latency_s": timing.ticks / timing.timer_hz / timing.iterations
            if timing.timer_hz and timing.iterations
            else None,
            "ticks": timing.ticks,
            "timer_hz": timing.timer_hz,
            "iterations": timing.iterations,
            "warmups": timing.warmups,
            "timing_scope": timing.timing_scope.value,
        },
        "arena_used_after_invoke_bytes": None if memory is None else memory.after_invoke,
    }


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _read(path: Path, label: str, *, hint: str | None = None) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Cannot read the {label} file {path}: {exc}", hint=hint) from exc


def _pin(path: Path, data: bytes) -> FixtureFile:
    from .fixture_runtime import FixtureFile

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
    except OSError as exc:
        raise ConfigError(f"Cannot write {path}: {exc}") from exc
    return FixtureFile(path, hashlib.sha256(data).hexdigest())
