"""A saved run result: one capture from a compiled build, verifiable on any host.

:meth:`RunResult.save <helia_profiler.RunResult.save>` writes a
``hpx.run-result`` document; :func:`load_run` reads it back as a
:class:`RunResultRecord`. The document names its compiled build by path,
sha256 and size, holds the capture's facts, the power request, the output
comparison, the identity and the HPX version that wrote it, and pins every
file the capture references by sha256 and size. Paths are relative to named
roots, as a saved compiled build's are, so the directories can move together.

Loading checks that every pinned file is intact, that the compiled build is
the one recorded and verifies, that the identity and the output comparison
recompute from the build and the capture, and that the power request fits
the build's power wiring. Free text, such as a failed capture's error
message, is recorded, not verified. The checks guard against accidents such
as an edited or mixed-up file, not against tampering. A failed capture is
saved and loaded as any other: its outcome is data.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ._files import RootedPaths, read_json, write_bytes_atomically, write_text_atomically
from .errors import ConfigError

if TYPE_CHECKING:
    from .compiled_build import CompiledBuild, RunResult
    from .expect import ReferenceOutputs, Verification
    from .fixture_capture import FixtureCaptureResult
    from .fixture_power import FixtureBoardPowerRequest, FixtureRailPowerRequest
    from .fixture_runtime import FixtureFile
    from .identity import RunIdentity

RUN_RESULT_SCHEMA = "hpx.run-result"
RUN_RESULT_SCHEMA_VERSION = 1

_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "location",
        "compiled_build",
        "capture",
        "power",
        "verification",
        "identity",
        "hpx_version",
    }
)
_HINT = "Save the run again, or restore the files it records."


class RunResultError(ConfigError):
    """A saved run result that does not load; ``code`` names the check it failed.

    The codes are the verified result reader's: ``invalid_json`` (not strict
    JSON), ``unsupported_schema`` (another schema or version),
    ``invalid_path`` (unusable roots, or a result not where it records
    itself), ``artifact_changed`` (a pinned file missing or changed),
    ``runtime_missing`` (the build's prepared runtime is not in the cache),
    ``identity_mismatch`` (the identity or output comparison does not
    recompute) and ``invalid_evidence`` (anything else: an unreadable or
    malformed result, a build that does not verify, a power request the build
    cannot take).
    """

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        self.code = code
        super().__init__(message, hint=hint)


@dataclass(frozen=True)
class RunResultRecord:
    """A saved run result, loaded and verified.

    ``path``, ``size_bytes`` and ``sha256`` identify the saved document,
    ``document`` is its JSON, which the portable view cites by JSON pointer,
    ``hpx_version`` is the heliaPROFILER that wrote it, and ``result`` is the
    run it records.
    """

    path: Path
    size_bytes: int
    sha256: str
    hpx_version: str
    result: RunResult
    document: Mapping[str, Any]


def load_run(path: str | Path, *, roots: Mapping[str, Path]) -> RunResultRecord:
    """Read and verify a run result saved by :meth:`RunResult.save`, resolving paths under *roots*.

    Raises:
        RunResultError: A ``ConfigError`` naming the first check that fails:
            the schema or version, the compiled build, a pinned file, the
            identity or the output comparison.
    """
    source = Path(path).expanduser()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise RunResultError("invalid_evidence", f"Cannot read run result {source}: {exc}") from exc
    return _load(source, raw, roots)


def read_run(path: Path, raw: bytes) -> RunResultRecord:
    """Verify *raw*, the bytes of the run result at *path*, under the roots it implies.

    The root the result records itself under is the directory its recorded
    path leads back to, which :func:`load_run`'s checks confirm; ``cache``
    is this host's HPX cache, as for ``hpx run``.
    """
    from .hostenv.cache_dirs import hpx_cache_root

    data = _document(raw)
    location = _fields(data["location"], ("root", "path"), "location")
    name, relative = location["root"], location["path"]
    parts = PurePosixPath(relative).parts if isinstance(relative, str) else ()
    resolved = path.resolve()
    base = resolved.parents[len(parts) - 1] if 0 < len(parts) <= len(resolved.parents) else None
    if not isinstance(name, str) or base is None:
        raise RunResultError(
            "invalid_path",
            f"The run result records itself at {relative!r} under root {name!r}, "
            f"which does not lead back to {resolved}",
            hint="Load it with load_run(path, roots=...) naming where its root now is.",
        )
    roots = {"cache": hpx_cache_root().resolve(), name: base}
    return _load(path, raw, roots)


def save_run(
    result: RunResult, path: str | Path, *, roots: Mapping[str, Path], build: str | Path
) -> Path:
    """Write *result* as a run result; see :meth:`RunResult.save`."""
    from ._run_contracts import check_capture_counts
    from ._version import __version__
    from .compiled_build import load_build

    target = Path(path).expanduser()
    build_path = Path(build).expanduser()
    files = _Files(roots)
    try:
        saved = load_build(build_path, roots=roots)
    except ConfigError as exc:
        raise ConfigError(
            f"Cannot save the run: its compiled build {build_path} does not load: {exc}"
        ) from exc
    if (saved.binding, saved.identity) != (result.compiled.binding, result.compiled.identity):
        raise ConfigError(
            f"{build_path} is not the compiled build this run captured from",
            hint="Name the build.json the run's CompiledBuild was saved to.",
        )
    check_capture_counts(result.compiled.build, result.capture)
    _check_power(result.compiled, result.capture, result.power)
    recomputed = _identity(result.compiled, result.capture, result.power)
    _check_identity(recomputed, result.identity.to_dict())
    references: list[Path] = []
    if result.verification is not None:
        if not result.succeeded:
            raise ConfigError("A failed capture has no output comparison")
        publication = uuid.uuid4().hex
        references = [
            target.with_name(f"{target.stem}.reference_{index}.{publication}.bin")
            for index in range(len(result.verification.reference.outputs))
        ]
        compared = _compare(result.compiled, result.capture, result.verification)
        if _encode_verification(compared) != _encode_verification(result.verification):
            raise ConfigError(
                "The run's verification does not recompute from its reference and outputs"
            )
    document = {
        "schema": RUN_RESULT_SCHEMA,
        "schema_version": RUN_RESULT_SCHEMA_VERSION,
        "location": files.place(target),
        "compiled_build": files.pin(build_path),
        "capture": _encode(result.capture, files.file),
        "power": _encode_power(result.power),
        "verification": None,
        "identity": result.identity.to_dict(),
        "hpx_version": __version__,
    }
    try:
        if result.verification is not None:
            outputs = result.verification.reference.outputs
            for reference, data in zip(references, outputs, strict=True):
                write_bytes_atomically(reference, data)
            document["verification"] = {
                **_encode_verification(result.verification),
                "reference_files": [files.pin(reference) for reference in references],
            }
        text = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        write_text_atomically(target, text)
    except OSError as exc:
        raise ConfigError(f"Cannot save the run result to {target}: {exc}") from exc
    return target


def _load(source: Path, raw: bytes, roots: Mapping[str, Path]) -> RunResultRecord:
    from ._run_contracts import check_capture_counts
    from .compiled_build import RunResult, load_build, missing_runtime_hint

    data = _document(raw)
    files = _Files(roots)
    recorded = files.locate(data["location"])
    if recorded != source.resolve():
        raise RunResultError(
            "invalid_path",
            f"The run result records itself at {recorded}, not {source.resolve()}",
            hint="Pass roots= naming where the result's root now is.",
        )
    build_path = files.check(data["compiled_build"], "compiled build")
    try:
        compiled = load_build(build_path, roots=roots)
    except ConfigError as exc:
        message = exc.args[0] if exc.args else str(exc)
        cache = files.paths.roots.get("cache")
        hint = missing_runtime_hint(exc, build_path.resolve(), cache)
        if hint is not None:
            raise RunResultError("runtime_missing", message, hint=hint) from exc
        raise RunResultError(
            "invalid_evidence", f"The run's compiled build does not load: {message}"
        ) from exc
    try:
        capture = _decode_capture(data["capture"], files.fixture_file)
        power = _decode_power(data["power"])
    except (TypeError, ValueError) as exc:
        raise RunResultError("invalid_evidence", f"Malformed run result: {exc}") from exc
    try:
        check_capture_counts(compiled.build, capture)
        _check_power(compiled, capture, power)
    except ConfigError as exc:
        raise RunResultError("invalid_evidence", str(exc), hint=_HINT) from exc
    identity = _identity(compiled, capture, power)
    try:
        _check_identity(identity, data["identity"])
    except ConfigError as exc:
        raise RunResultError("identity_mismatch", str(exc), hint=_HINT) from exc
    verification = _decode_verification(data["verification"], compiled, capture, files)
    result = RunResult(compiled, capture, identity, verification, power)
    digest = hashlib.sha256(raw).hexdigest()
    return RunResultRecord(source.resolve(), len(raw), digest, data["hpx_version"], result, data)


def _document(raw: bytes) -> dict[str, Any]:
    try:
        data = read_json(raw, label="run result")
    except (ValueError, RecursionError) as exc:
        raise RunResultError("invalid_json", f"A run result must be JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RunResultError("invalid_json", "A run result must be a JSON object")
    if data.get("schema") != RUN_RESULT_SCHEMA:
        raise RunResultError(
            "unsupported_schema", f"Not a run result: schema {data.get('schema')!r}"
        )
    version = data.get("schema_version")
    if type(version) is not int or version != RUN_RESULT_SCHEMA_VERSION:
        raise RunResultError(
            "unsupported_schema",
            f"Unsupported run result version: {version!r}",
            hint=f"This heliaPROFILER reads version {RUN_RESULT_SCHEMA_VERSION}; run again.",
        )
    if data.keys() != _KEYS:
        raise RunResultError(
            "invalid_evidence",
            f"Malformed run result: fields {sorted(data)}, expected {sorted(_KEYS)}",
        )
    if not isinstance(data["hpx_version"], str) or not data["hpx_version"]:
        raise RunResultError("invalid_evidence", "Malformed run result: hpx_version")
    return data


class _Files:
    """Paths under named roots, and files pinned by sha256 and size."""

    def __init__(self, roots: Mapping[str, Path]) -> None:
        try:
            self.paths = RootedPaths(roots)
        except ValueError as exc:
            raise RunResultError("invalid_path", f"Invalid roots: {exc}") from exc

    def place(self, path: Path) -> dict[str, str]:
        try:
            return self.paths.encode(path)
        except ValueError as exc:
            raise ConfigError(f"Cannot save the run result: {exc}") from exc

    def file(self, item: FixtureFile) -> dict[str, Any]:
        """*item*, refused unless its bytes still match its sha256."""
        return self._entry(item.path, item.read)

    def pin(self, path: Path) -> dict[str, Any]:
        return self._entry(path, path.read_bytes)

    def _entry(self, path: Path, read: Callable[[], bytes]) -> dict[str, Any]:
        try:
            data = read()
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot save the run result: {exc}") from exc
        digest = hashlib.sha256(data).hexdigest()
        return {**self.place(path), "sha256": digest, "size_bytes": len(data)}

    def locate(self, value: Any) -> Path:
        try:
            return self.paths.decode(value)
        except ValueError as exc:
            raise RunResultError("invalid_path", f"Invalid run result path: {exc}") from exc

    def check(self, value: Any, label: str) -> Path:
        """The path *value* names, once its bytes match its sha256 and size."""
        entry = _fields(value, ("root", "path", "sha256", "size_bytes"), label)
        path = self.locate({"root": entry["root"], "path": entry["path"]})
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RunResultError(
                "artifact_changed", f"The run's {label} {path} is missing: {exc}", hint=_HINT
            ) from exc
        if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RunResultError(
                "artifact_changed",
                f"The run's {label} {path} is not the file it recorded "
                f"(sha256 {str(entry['sha256'])[:12]}…)",
                hint=_HINT,
            )
        return path

    def fixture_file(self, value: Any) -> FixtureFile:
        from .fixture_runtime import FixtureFile

        path = self.check(value, "evidence file")
        return FixtureFile(path, value["sha256"])


def _fields(value: Any, names: tuple[str, ...] | list[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != set(names):
        raise RunResultError(
            "invalid_evidence",
            f"Malformed run result {label}: fields "
            f"{sorted(value) if isinstance(value, dict) else type(value).__name__}, "
            f"expected {sorted(names)}",
        )
    return value


def _encode(value: Any, file: Callable[[FixtureFile], Any]) -> Any:
    """*value* as JSON-ready data, each pinned file through *file*."""
    from .fixture_runtime import FixtureFile

    if isinstance(value, FixtureFile):
        return file(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _encode(getattr(value, f.name), file) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_encode(item, file) for item in value]
    return value


def _decode_capture(value: Any, file: Callable[[Any], FixtureFile]) -> FixtureCaptureResult:
    from ._fixture_build import FixtureTimingScope
    from .fixture_capture import FixtureCaptureResult, FixtureMemory, FixtureTiming
    from .fixture_power import FixtureBatchCompletion, FixtureRailEnergy
    from .fixture_psram import FixturePsramObservation

    def optional(decode: Callable[[Any], Any]) -> Callable[[Any], Any]:
        return lambda item: None if item is None else decode(item)

    def many(decode: Callable[[Any], Any]) -> Callable[[Any], Any]:
        def each(items: Any) -> tuple[Any, ...]:
            if not isinstance(items, list):
                raise ValueError(f"expected a list, not {type(items).__name__}")
            return tuple(decode(item) for item in items)

        return each

    scope = FixtureTimingScope
    completion = optional(
        lambda item: _record(FixtureBatchCompletion, item, "completion", raw=file)
    )
    state = _record(
        FixtureCaptureResult,
        value,
        "capture",
        timing_scope=scope,
        output=optional(file),
        timing=optional(lambda item: _record(FixtureTiming, item, "timing", timing_scope=scope)),
        memory=optional(lambda item: _record(FixtureMemory, item, "memory")),
        artifacts=many(file),
        outputs=many(file),
        arena_scan=many(many(lambda item: item)),
        psram_weights=optional(
            lambda item: _record(
                FixturePsramObservation,
                item,
                "psram_weights",
                active_addresses=many(lambda address: address),
            )
        ),
        rail_energy=optional(
            lambda item: _record(
                FixtureRailEnergy, item, "rail_energy", raw=file, completion=completion
            )
        ),
    )
    if state.state not in ("success", "failure"):
        raise ValueError(f"unknown capture state {state.state!r}")
    _check_capture_error(state)
    return state


def _record(cls: Any, value: Any, label: str, **decode: Callable[[Any], Any]) -> Any:
    names = [f.name for f in fields(cls)]
    data = _fields(value, names, label)
    return cls(
        **{name: decode[name](data[name]) if name in decode else data[name] for name in names}
    )


def _encode_power(power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None) -> Any:
    from .fixture_power import FixtureRailPowerRequest

    if power is None:
        return None
    kind = "rail" if isinstance(power, FixtureRailPowerRequest) else "supply"
    return {"kind": kind, **asdict(power)}


def _decode_power(value: Any) -> FixtureRailPowerRequest | FixtureBoardPowerRequest | None:
    from .fixture_power import FixtureBoardPowerRequest, FixtureRailPowerRequest

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("the power request must be an object or null")
    kinds: dict[str, type] = {"rail": FixtureRailPowerRequest, "supply": FixtureBoardPowerRequest}
    kind = kinds.get(str(value.get("kind")))
    if kind is None:
        raise ValueError(f"unknown power request kind {value.get('kind')!r}")
    return _record(kind, {k: v for k, v in value.items() if k != "kind"}, "power")


def _identity(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None,
) -> RunIdentity:
    """The identity :meth:`CompiledBuild.run` gives *capture* under *power*."""
    from ._run_contracts import enrich_identity
    from .fixture_identity import fixture_run_identity
    from .fixture_power import FixtureRailPowerRequest

    rail = power.rail if isinstance(power, FixtureRailPowerRequest) else None
    return enrich_identity(
        fixture_run_identity(compiled.build, capture),
        compiled.lifecycle,
        compiled.plan.intent_hash,
        rail,
    )


def _check_identity(recomputed: RunIdentity, stated: Any) -> None:
    """Refuse a *stated* identity, in its JSON form, other than the *recomputed* one.

    An unknown fact matches an unknown fact whatever its reason, so a later
    heliaPROFILER that rewords a reason still reads the result.
    """
    expected = _unknown_as_one(recomputed.to_dict())
    stated = _unknown_as_one(stated)
    if stated == expected:
        return
    keys = (
        sorted(
            key for key in expected.keys() | stated.keys() if expected.get(key) != stated.get(key)
        )
        if isinstance(stated, dict)
        else ["identity"]
    )
    raise ConfigError(
        "The run's identity does not recompute from its build and capture; it differs in "
        + ", ".join(keys)
    )


def _unknown_as_one(value: Any) -> Any:
    if isinstance(value, dict):
        if value.keys() == {"unknown"} and isinstance(value["unknown"], str):
            return {"unknown": None}
        return {key: _unknown_as_one(item) for key, item in value.items()}
    return value


def _check_capture_error(capture: FixtureCaptureResult) -> None:
    if capture.error is not None and not isinstance(capture.error, str):
        raise ValueError("capture.error must be text or null")


def _check_power(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None,
) -> None:
    from ._run_contracts import check_power_request
    from .fixture_power import GROSS_RAIL_SCOPE, NO_SUBTRACTION, FixtureRailPowerRequest

    check_power_request(compiled.build, compiled.lifecycle, power)
    try:
        _check_capture_error(capture)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    energy = capture.rail_energy
    if energy is None:
        if capture.state == "success" and isinstance(power, FixtureRailPowerRequest):
            raise ConfigError("A successful rail capture requires retained energy")
        return
    if capture.state != "success" or not isinstance(power, FixtureRailPowerRequest):
        raise ConfigError("Retained rail energy requires a successful rail capture")
    timing = capture.timing
    if (
        energy.instrument_serial != power.serial
        or energy.rail != power.rail
        or energy.scope != GROSS_RAIL_SCOPE
        or energy.subtraction != NO_SUBTRACTION
    ):
        raise ConfigError("Retained rail energy differs from its power request or scope")
    if (
        type(energy.iterations) is not int
        or energy.iterations <= 0
        or energy.iterations != compiled.build.iterations
        or timing is None
        or timing.iterations != energy.iterations
        or timing.warmups != compiled.build.warmups
        or timing.timing_scope != compiled.build.timing_scope
        or capture.timing_scope != compiled.build.timing_scope
    ):
        raise ConfigError("Retained rail energy counts or timing differ from the build")
    completion = energy.completion
    if completion is None:
        raise ConfigError("Retained rail energy requires batch completion")
    samples = (
        completion.ready_sample,
        completion.go_ack_sample,
        completion.gate_rise_sample,
        completion.gate_fall_sample,
        completion.done_sample,
    )
    if (
        type(completion.protocol_version) is not int
        or completion.protocol_version != power.firmware_wiring().protocol_version
        or completion.counter_domain != "joulescope_sensor_sample_id"
        or type(completion.sample_rate) is not int
        or completion.sample_rate <= 0
        or any(type(sample) is not int or sample < 0 for sample in samples)
        or not samples[0] < samples[1] <= samples[2] < samples[3] <= samples[4]
        or completion.raw != energy.raw
    ):
        raise ConfigError("Retained rail energy has inconsistent batch completion")
    values = (
        energy.duration_s,
        energy.mean_power_w,
        energy.mean_current_a,
        energy.energy_j,
        energy.energy_per_iteration_j,
    )
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        for value in values
    ):
        raise ConfigError("Retained rail energy requires finite positive measurements")
    if not all(
        math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
        for actual, expected in (
            (energy.energy_j, energy.mean_power_w * energy.duration_s),
            (energy.energy_per_iteration_j, energy.energy_j / energy.iterations),
        )
    ):
        raise ConfigError("Retained rail energy measurements disagree")


def _compare(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    verification: Verification,
    reference: ReferenceOutputs | None = None,
) -> Verification:
    from .expect import compare_outputs

    try:
        observed = [item.read() for item in capture.outputs]
        return compare_outputs(
            compiled.build.output_tensors,
            reference or verification.reference,
            observed,
            verification.tolerance,
        )
    except (OSError, ValueError, ConfigError) as exc:
        raise ConfigError(f"The run's output comparison cannot be recomputed: {exc}") from exc


def _encode_verification(verification: Verification) -> dict[str, Any]:
    tolerance = verification.tolerance
    return {
        "reference": asdict(verification.reference.origin),
        "tolerance": None if tolerance is None else tolerance.max_abs_error,
        "matched": verification.matched,
        "tensors": [
            {
                **{
                    name: _number(value) if isinstance(value, float) else value
                    for name, value in asdict(tensor).items()
                },
                "shape": list(tensor.shape),
                "identical": tensor.identical,
            }
            for tensor in verification.tensors
        ],
    }


def _number(value: float) -> float | str:
    """*value*, or ``"inf"``, ``"-inf"`` or ``"nan"``, which JSON cannot hold."""
    return value if math.isfinite(value) else repr(value)


def _decode_verification(
    value: Any, compiled: CompiledBuild, capture: FixtureCaptureResult, files: _Files
) -> Verification | None:
    from .expect import ReferenceOrigin, ReferenceOutputs, Tolerance, Verification

    if value is None:
        return None
    data = _fields(
        value, ("reference", "tolerance", "matched", "tensors", "reference_files"), "verification"
    )
    try:
        if capture.state != "success":
            raise ValueError("a failed capture has no output comparison")
        if not isinstance(data["reference_files"], list):
            raise ValueError("reference_files must be a list")
        references = [files.check(item, "reference output") for item in data["reference_files"]]
        origin = _record(ReferenceOrigin, data["reference"], "reference")
        if origin.kind not in ("host-litert", "build", "files"):
            raise ValueError(f"unknown reference origin {origin.kind!r}")
        bound = data["tolerance"]
        tolerance = None if bound is None else Tolerance(max_abs_error=bound)
        reference = ReferenceOutputs(origin, tuple(path.read_bytes() for path in references))
        recomputed = _compare(
            compiled, capture, Verification(reference, (), tolerance), reference=reference
        )
    except RunResultError:
        raise
    except (ConfigError, OSError, TypeError, ValueError) as exc:
        raise RunResultError("invalid_evidence", f"Malformed run verification: {exc}") from exc
    stated = {key: item for key, item in data.items() if key != "reference_files"}
    if _encode_verification(recomputed) != stated:
        raise RunResultError(
            "identity_mismatch",
            "The run's output comparison does not recompute from its reference and outputs",
            hint=_HINT,
        )
    return recomputed
