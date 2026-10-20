"""A saved run result: one capture from a compiled build, verifiable on any host.

:meth:`RunResult.save <helia_profiler.RunResult.save>` writes a
``hpx.run-result`` document; :func:`load_run` reads it back as a
:class:`RunRecord`. The document names its compiled build by path, sha256 and
size, holds the capture's facts, the power request, the output comparison
and the identity, and pins every file the capture references by sha256 and
size. Paths are relative to named roots, as a saved compiled build's are, so
the directories can move together.

Loading re-checks it all: the compiled build is the one recorded and
verifies, every pinned file is intact, and the identity and the output
comparison recompute from the build and the capture. The checks guard
against accidents such as an edited or mixed-up file, not against
tampering. A failed capture is saved and loaded as any other: its outcome is
data.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ._files import write_bytes_atomically, write_text_atomically
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
    }
)
_HINT = "Save the run again, or restore the files it records."


class RunResultError(ConfigError):
    """A saved run result that does not load; ``code`` names the check it failed.

    The codes are the verified result reader's: ``invalid_json``,
    ``unsupported_schema``, ``invalid_path``, ``artifact_changed``,
    ``identity_mismatch`` and ``invalid_evidence``.
    """

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        self.code = code
        super().__init__(message, hint=hint)


@dataclass(frozen=True)
class RunRecord:
    """A saved run result, loaded and verified.

    ``path``, ``size_bytes`` and ``sha256`` identify the saved document,
    ``document`` is its JSON, which the portable view cites by JSON pointer,
    and ``result`` is the run it records.
    """

    path: Path
    size_bytes: int
    sha256: str
    result: RunResult
    document: Mapping[str, Any]


def load_run(path: str | Path, *, roots: Mapping[str, Path]) -> RunRecord:
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


def read_run(path: Path, raw: bytes) -> RunRecord:
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
    recomputed = _identity(result.compiled, result.capture, result.power)
    _check_identity(recomputed, result.identity.to_dict())
    references: list[Path] = []
    if result.verification is not None:
        _check_failed(result.capture, result.verification)
        references = [
            target.with_name(f"{target.stem}.reference_{index}.bin")
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


def _load(source: Path, raw: bytes, roots: Mapping[str, Path]) -> RunRecord:
    from .compiled_build import RunResult, load_build

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
        raise RunResultError(
            "invalid_evidence", f"The run's compiled build does not load: {exc}"
        ) from exc
    try:
        capture = _decode_capture(data["capture"], files.fixture_file)
        power = _decode_power(data["power"])
    except (TypeError, ValueError) as exc:
        raise RunResultError("invalid_evidence", f"Malformed run result: {exc}") from exc
    identity = _identity(compiled, capture, power)
    try:
        _check_identity(identity, data["identity"])
    except ConfigError as exc:
        raise RunResultError("identity_mismatch", str(exc), hint=_HINT) from exc
    verification = _decode_verification(data["verification"], compiled, capture, files)
    result = RunResult(compiled, capture, identity, verification, power)
    return RunRecord(source.resolve(), len(raw), hashlib.sha256(raw).hexdigest(), result, data)


def _document(raw: bytes) -> dict[str, Any]:
    from ._fixture_record import _read_json

    try:
        data = _read_json(raw)
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
    return data


class _Files:
    """Paths under named roots, and files pinned by sha256 and size."""

    def __init__(self, roots: Mapping[str, Path]) -> None:
        from ._fixture_record import _Roots

        try:
            self.roots = _Roots(roots)
        except ValueError as exc:
            raise ConfigError(f"Invalid roots: {exc}") from exc

    def place(self, path: Path) -> dict[str, str]:
        try:
            return self.roots.encode(path)
        except ValueError as exc:
            raise ConfigError(f"Cannot save the run result: {exc}") from exc

    def file(self, item: FixtureFile) -> dict[str, Any]:
        try:
            data = item.read()
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot save the run result: {exc}") from exc
        return {**self.place(item.path), "sha256": item.sha256, "size_bytes": len(data)}

    def pin(self, path: Path) -> dict[str, Any]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigError(f"Cannot save the run result: {exc}") from exc
        return {
            **self.place(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def locate(self, value: Any) -> Path:
        try:
            return self.roots.decode(value)
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
    from .fixture_identity import fixture_run_identity
    from .fixture_power import FixtureRailPowerRequest

    rail = power.rail if isinstance(power, FixtureRailPowerRequest) else None
    return compiled._identity(fixture_run_identity(compiled.build, capture), rail)


def _check_identity(recomputed: RunIdentity, stated: Any) -> None:
    """Refuse a *stated* identity, in its JSON form, other than the *recomputed* one."""
    expected = recomputed.to_dict()
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
        f"The run's identity does not recompute from its build and capture: {', '.join(keys)} "
        "differ"
    )


def _check_failed(capture: FixtureCaptureResult, verification: Any) -> None:
    if capture.state != "success" and verification is not None:
        raise ConfigError("A failed capture has no output comparison")


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
        _check_failed(capture, data)
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
