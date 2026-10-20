"""A run directory: one compiled build, its pinned inputs and its captures, in one folder.

``hpx build`` writes one; ``hpx validate build`` and ``hpx run`` read it::

    DIR/build.json      the saved CompiledBuild
    DIR/inputs/         the model and one input_<index>.bin per model input
    DIR/work/           the firmware build, unless another work directory is named
    DIR/evidence-<N>/   one per capture, with its saved run result, result.json

The saved build and each saved run result name two roots, ``run`` for the
directory and ``cache`` for the HPX cache that holds a prepared runtime. The directory can move to
another host as a unit; a heliaRT or LiteRT build also needs its prepared
runtime in that host's HPX cache.
"""

from __future__ import annotations

import hashlib
import math
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
RESULT_FILE = "result.json"


def run_roots(directory: str | Path) -> dict[str, Path]:
    """The roots a run directory's build is saved and loaded with."""
    from .hostenv.cache_dirs import hpx_cache_root

    # Resolved like the paths a loaded build reports, so a symlinked cache matches.
    return {"run": _absolute(directory), "cache": hpx_cache_root().resolve()}


def run_directory_of(path: str | Path) -> Path:
    """The run directory *path* names: the directory itself, or its ``build.json``."""
    resolved = _absolute(path)
    return resolved.parent if resolved.name == BUILD_FILE and not resolved.is_dir() else resolved


def pin_fixture(
    model: str | Path,
    directory: str | Path,
    inputs: Sequence[str | Path] = (),
    *,
    model_sha256: str | None = None,
    model_hint: str | None = None,
) -> TypedFixture:
    """A fixture of *model*, its files copied under ``directory/inputs``.

    *inputs* holds one file of raw bytes per model input, in model order;
    without them every input is zero-filled. Inputs are named by index,
    since a tensor name such as ``serving_default:0`` is no portable filename.
    Every check runs before the first file is written. *model_hint* is the
    hint for an unreadable model.

    Raises:
        ConfigError: A file is unreadable, the model's sha256 is not
            *model_sha256*, or the inputs do not match the model's input
            count or byte sizes.
    """
    from ._fixture_build import FixtureIO, FixtureOutput, TypedFixture
    from .run_inputs import analyze_run_model

    target = _absolute(directory) / INPUTS_DIR
    source = Path(model).expanduser()
    data = _read(source, "model", hint=model_hint)
    digest = hashlib.sha256(data).hexdigest()
    if model_sha256 is not None and digest != model_sha256:
        raise ConfigError(
            f"The model {source} (sha256 {digest[:12]}...) is not the model the plan was "
            f"frozen against (sha256 {model_sha256[:12]}...)",
            hint="Name the model the plan was frozen against.",
        )
    analysis = analyze_run_model(source)
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
        _pin(target / source.name, data),
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

    *directory* must be new or empty, so a failed build never touches an
    earlier one. *model* is the plan's model when it is no longer at the
    path the plan records. *work_dir* defaults to ``directory/work`` and must
    sit inside *directory*: the saved build's files are recorded relative to
    its run root.

    Raises:
        ConfigError: *directory* holds files, *work_dir* is outside it, or
            the model or inputs do not fit the plan.
    """
    from ._fixture_build import FixtureMethod, FixtureTimingScope
    from .compiled_build import build

    root = _absolute(directory)
    work = _absolute(work_dir) if work_dir is not None else root / WORK_DIR
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ConfigError(
            f"{root} is not a new or empty directory",
            hint="Build into a fresh directory; an earlier build there stays as it is.",
        )
    if work == root or work.is_relative_to(root / INPUTS_DIR):
        raise ConfigError(
            f"The work directory {work} would mix the firmware tree with the run's own files",
            hint="Name a subdirectory of the run directory, or leave the default.",
        )
    if not work.is_relative_to(root):
        raise ConfigError(
            f"The work directory {work} is outside the run directory {root}",
            hint="The saved build records its files relative to the run directory; "
            "name a work directory inside it, or leave the default.",
        )
    fixture = pin_fixture(
        model if model is not None else plan.model.path,
        root,
        inputs,
        model_sha256=plan.model.sha256,
        model_hint=None
        if model is not None
        else "The plan records this path; name the model where it is now.",
    )
    compiled = build(
        plan,
        fixture,
        method=FixtureMethod(FixtureTimingScope(timing_scope)),
        lifecycle=lifecycle,
        model=fixture.model.path,
        host={"work_dir": str(work)},
    )
    compiled.save(root / BUILD_FILE, roots=run_roots(root))
    return compiled


def load_from(directory: str | Path) -> CompiledBuild:
    """Load and verify the build saved in *directory*.

    Raises:
        ConfigError: The build does not verify, or a file it records is
            missing, with a hint naming how to restore a missing runtime.
    """
    from .compiled_build import load_build

    roots = run_roots(directory)
    path = roots["run"] / BUILD_FILE
    try:
        return load_build(path, roots=roots)
    except ConfigError as exc:
        missing = exc.__cause__
        if not isinstance(missing, OSError) or missing.filename is None:
            raise
        message = str(exc.args[0]) if exc.args else str(exc)
        if Path(missing.filename) == path:
            raise
        if Path(missing.filename).is_relative_to(roots["cache"]):
            raise ConfigError(message, hint=_prepare_hint(path, roots["cache"])) from exc
        raise ConfigError(
            message, hint=f"The run directory {roots['run']} is incomplete; build it again."
        ) from exc


def _prepare_hint(path: Path, cache: Path) -> str:
    """How to restore the prepared runtime the build in *path* links."""
    import json

    from .compiled_build import _linked_runtime
    from .errors import HpxError
    from .frozen_run import FrozenRun

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        source = document["build"].get("runtime_source") or {}
        linked = _linked_runtime(FrozenRun.from_dict(document["plan"]), source.get("version"))
    except (HpxError, OSError, ValueError, KeyError, TypeError):
        linked = None
    command = (
        f"hpx runtimes prepare {linked.name} {linked.version}"
        if linked is not None and linked.version
        else "hpx runtimes prepare <engine> <version>"
    )
    return (
        f"The build links a runtime prepared under the HPX cache {cache}. Run "
        f"`{command}` on this host, or set HPX_CACHE_DIR to the cache that holds it."
    )


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
    """Capture once from the build saved in *directory* and save it as ``evidence_dir/result.json``.

    The guard sees the build's target before anything else runs, so a board
    or probe it refuses is refused before the evidence directory exists or
    any device is touched. An *evidence_dir* that already exists, or that
    is outside *directory*, is refused as early: the saved result records
    its files relative to the run directory.
    """
    from .compiled_build import run_build

    compiled = load_from(directory)
    guard.verify_target(target=compiled.build.target, jlink_serial=jlink_serial)
    roots = run_roots(directory)
    evidence = _absolute(evidence_dir)
    if evidence.exists():
        raise ConfigError(
            f"The evidence directory {evidence} already exists",
            hint="Each capture writes its evidence to a new directory.",
        )
    if evidence == roots["run"] or not evidence.is_relative_to(roots["run"]):
        raise ConfigError(
            f"The evidence directory {evidence} is not inside the run directory {roots['run']}",
            hint="The saved run result records its files relative to the run directory; "
            "name a new subdirectory of it, or leave the default.",
        )
    result = run_build(
        compiled,
        guard=guard,
        jlink_serial=jlink_serial,
        evidence_dir=evidence,
        settle_seconds=settle_seconds,
        expect=expect,
    )
    result.save(evidence / RESULT_FILE, roots=roots, build=roots["run"] / BUILD_FILE)
    return result


def build_summary(
    directory: str | Path, compiled: CompiledBuild, *, sources: Sequence[str] | None = None
) -> dict[str, Any]:
    """The saved build's path, pinned inputs and identity, JSON-ready.

    *sources* names where each input's bytes came from, in model order: a
    file path, or ``zero-filled``.
    """
    inputs = [
        {"name": io.tensor.name, "bytes": io.tensor.size_bytes, "path": str(io.data.path)}
        for io in compiled.build.inputs
    ]
    if sources is not None:
        for entry, source in zip(inputs, sources, strict=True):
            entry["source"] = source
    return {
        "build": str(_absolute(directory) / BUILD_FILE),
        "inputs": inputs,
        "identity": compiled.identity.to_dict(),
    }


def run_summary(
    result: RunResult,
    evidence_dir: str | Path,
    *,
    authority: str,
    saved: str | Path | None = None,
) -> dict[str, Any]:
    """A capture's outcome, identity, verification facts and key timing, JSON-ready.

    *authority* names who vouched for the board, ``operator`` for an
    :class:`~helia_profiler.OperatorGuard`. *saved* is the run result's
    file, when it was saved.
    """
    capture, verification = result.capture, result.verification
    timing, memory = capture.timing, capture.memory
    return {
        "succeeded": result.succeeded,
        "authority": authority,
        "error": capture.error,
        "evidence_dir": str(_absolute(evidence_dir)),
        "result": None if saved is None else str(_absolute(saved)),
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
                    # JSON has no inf or NaN; identical and mismatched_elements still tell.
                    "max_abs_error": _finite(tensor.max_abs_error),
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


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


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
