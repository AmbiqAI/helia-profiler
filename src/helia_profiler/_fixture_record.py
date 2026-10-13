"""Portable compiled-build records owned by the fixture producer.

Records describe an already completed build. They neither authorize an operation
nor replace a caller's independently pinned experiment/source/image identities.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fixture_capture import FixtureCaptureRequest

from ._fixture_build import (
    EngineSource,
    FixtureBuild,
    FixtureCapability,
    FixtureIO,
    FixtureRole,
    FixtureStatePolicy,
    FixtureTimingScope,
    _io_capabilities,
)
from .engines import EngineType
from .fixture_analysis import FixtureTensor, PerAxisQuantization, PerTensorQuantization
from .fixture_psram import FixturePsramPayload, FixturePsramWeights
from .fixture_runtime import FixtureFile
from .fixture_target import (
    FIXTURE_CLOCK_PROFILE,
    FixtureTarget,
    fixture_cpu_hz,
    validate_fixture_placement,
)
from .placement import Placement
from .results.models import MemoryConsumer, MemoryPlan, MemoryRegionUsage, ToolchainInfo
from .wire import STIMER_HZ

_FILE_FIELDS = (
    "binary",
    "expected",
    "runtime_manifest",
    "flat_binary",
    "dependency_lock",
    "link_map",
    "dependency_state",
    "model",
)
_FILES_FIELDS = ("generated_sources", "outputs", "aot_outputs")
_FIELDS = frozenset(
    (
        *_FILE_FIELDS,
        *_FILES_FIELDS,
        "fixture_identity",
        "app_dir",
        "built",
        "timing_scope",
        "engine",
        "build_identity",
        "iterations",
        "warmups",
        "intent_identity",
        "target",
        "toolchain",
        "provider_provenance",
        "planned_memory",
        "planned_memory_reason",
        "capabilities",
        "aot_arena_scan",
        "engine_source",
        "state_policy",
        "source_initializer_present",
        "psram_weights",
        "arena_location",
        "weights_location",
        "inputs",
        "output_tensors",
        "arena_capacity",
        "clock_profile",
        "cpu_hz",
        "timer_hz",
    )
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Fixture build record has missing or unexpected fields")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate fixture build field: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Nonfinite fixture build value: {value}")


def _read_json(value):
    return json.loads(value, object_pairs_hook=_unique, parse_constant=_reject_constant)


def _payload(build: FixtureBuild, *, file, path) -> dict:
    """The single build schema, with different path encodings for identity/storage."""
    result = asdict(build)
    _object(result, _FIELDS)
    result["app_dir"] = path(build.app_dir)
    for name in _FILE_FIELDS:
        value = getattr(build, name)
        result[name] = file(value) if value is not None else None
    for name in _FILES_FIELDS:
        result[name] = [file(value) for value in getattr(build, name)]
    result["inputs"] = [
        {"tensor": asdict(io.tensor), "data": file(io.data), "role": io.role.value}
        for io in build.inputs
    ]
    return result


def compiled_fixture_identity(build: FixtureBuild) -> str:
    """Bind all recorded execution facts and artifacts, independently of roots."""
    value = _payload(build, file=lambda f: f.sha256, path=lambda _: None)
    value.pop("build_identity")
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _digest(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Lowercase SHA256 fixture identity required")


def verify_fixture_build(build: FixtureBuild) -> None:
    """Verify a completed record's files, execution facts and dependency binding.

    This is offline integrity validation, not runtime/numerical qualification or
    station permission. A caller must separately bind its intended experiment.
    """
    if not isinstance(build, FixtureBuild) or build.built is not True:
        raise ValueError("A completed compiled FixtureBuild is required")
    for name in ("fixture_identity", "intent_identity", "build_identity"):
        _digest(getattr(build, name))
    for name in (
        "binary",
        "flat_binary",
        "dependency_lock",
        "dependency_state",
        "link_map",
        "model",
    ):
        if not isinstance(getattr(build, name), FixtureFile):
            raise ValueError(f"Compiled fixture is missing {name}")
    assert build.flat_binary is not None
    assert build.dependency_state is not None
    assert build.dependency_lock is not None
    if not build.generated_sources or not build.inputs or not build.output_tensors:
        raise ValueError("Compiled fixture requires generated sources and complete IO")
    if not isinstance(build.target, FixtureTarget):
        raise ValueError("Explicit fixture target required")
    build.target.verify()
    if not isinstance(build.engine, EngineType) or not isinstance(
        build.timing_scope, FixtureTimingScope
    ):
        raise ValueError("Explicit fixture engine and timing scope required")
    if build.state_policy is not None and not isinstance(build.state_policy, FixtureStatePolicy):
        raise ValueError("Invalid fixture state policy")
    if type(build.source_initializer_present) is not bool:
        raise ValueError("Invalid source initializer fact")
    validate_fixture_placement(build.arena_location, build.weights_location, build.engine)
    for name, low, high in (
        ("iterations", 1, 100_000),
        ("warmups", 0, 10_000),
        ("arena_capacity", 1, 3 * 1024 * 1024),
    ):
        value = getattr(build, name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid fixture {name}")
    if (build.clock_profile, build.cpu_hz, build.timer_hz) != (
        FIXTURE_CLOCK_PROFILE,
        fixture_cpu_hz(),
        STIMER_HZ,
    ):
        raise ValueError("Unsupported fixture clock/timer facts")
    if type(build.cpu_hz) is not int or type(build.timer_hz) is not int:
        raise ValueError("Integer fixture clock/timer facts required")
    if build.toolchain is None or not build.toolchain.compiler_version:
        raise ValueError("Compiled fixture requires toolchain provenance")
    if any(not isinstance(v, str) for v in asdict(build.toolchain).values()):
        raise ValueError("Toolchain provenance fields must be strings")
    if not isinstance(build.provider_provenance, str) or not build.provider_provenance:
        raise ValueError("Provider provenance description required")
    if build.planned_memory_reason is not None and not isinstance(build.planned_memory_reason, str):
        raise ValueError("Invalid memory-plan reason")
    if build.engine_source is not None and (
        not isinstance(build.engine_source.package, str)
        or not isinstance(build.engine_source.version, str)
        or (
            build.engine_source.commit is not None
            and not isinstance(build.engine_source.commit, str)
        )
    ):
        raise ValueError("Engine source fields must be strings or an absent commit")
    if build.planned_memory is not None:
        _memory(asdict(build.planned_memory))
    for name in _FILE_FIELDS:
        value = getattr(build, name)
        if value is not None:
            value.read()
    for name in _FILES_FIELDS:
        for value in getattr(build, name):
            value.read()
    for io in build.inputs:
        _tensor(asdict(io.tensor))
        if not isinstance(io.role, FixtureRole) or len(io.data.read()) != io.tensor.size_bytes:
            raise ValueError("Incomplete fixture input/state bytes")
    for tensor in build.output_tensors:
        _tensor(asdict(tensor))
    capabilities = _io_capabilities(
        tuple(io.tensor for io in build.inputs), build.output_tensors, build.engine
    )
    if build.capabilities != capabilities:
        raise ValueError("Compiled fixture IO capability facts differ from the producer")
    if build.outputs:
        if len(build.outputs) != len(build.output_tensors) or build.expected != build.outputs[0]:
            raise ValueError("Incomplete legacy reference outputs")
        if any(
            len(f.read()) != t.size_bytes
            for f, t in zip(build.outputs, build.output_tensors, strict=True)
        ):
            raise ValueError("Legacy reference output byte extent mismatch")
    elif build.expected is not None:
        raise ValueError("Expected output without complete reference outputs")
    if build.engine is EngineType.HELIA_AOT:
        if build.runtime_manifest is not None:
            raise ValueError("AOT build cannot have an interpreter runtime manifest")
    elif build.runtime_manifest is None:
        raise ValueError("Interpreter build requires its runtime manifest")
    if build.aot_arena_scan and build.engine is not EngineType.HELIA_AOT:
        raise ValueError("Only AOT builds scan AOT scratch arenas")
    ids = []
    for region_id, size in build.aot_arena_scan:
        if (
            type(region_id) is not int
            or not 0 <= region_id <= 255
            or type(size) is not int
            or size <= 0
        ):
            raise ValueError("Invalid AOT arena scan facts")
        ids.append(region_id)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate AOT arena scan regions")
    if build.weights_location is Placement.PSRAM:
        if (
            build.psram_weights is None
            or build.psram_weights.image_sha256 != build.flat_binary.sha256
        ):
            raise ValueError("PSRAM descriptor must bind the exact compiled image")
    elif build.psram_weights is not None:
        raise ValueError("Unexpected PSRAM descriptor for compiled placement")
    state = _read_json(build.dependency_state.read())
    try:
        lock_hash = state["lock"]["sha256"]["value"]
    except (TypeError, KeyError) as exc:
        raise ValueError("Dependency state lacks its lock binding") from exc
    if lock_hash != build.dependency_lock.sha256:
        raise ValueError("Dependency state/lock hash mismatch")
    if compiled_fixture_identity(build) != build.build_identity:
        raise ValueError("Compiled fixture identity mismatch")


class _Roots:
    def __init__(self, roots: Mapping[str, Path]):
        if not roots or any(
            not isinstance(k, str) or not k or not isinstance(v, Path) for k, v in roots.items()
        ):
            raise ValueError("Named explicit Path roots required")
        self.roots = {k: v.resolve() for k, v in roots.items()}

    def encode(self, value: Path) -> dict:
        resolved = value.resolve()
        for name, root in sorted(self.roots.items(), key=lambda kv: (-len(kv[1].parts), kv[0])):
            if resolved.is_relative_to(root):
                return {"root": name, "path": resolved.relative_to(root).as_posix()}
        raise ValueError(f"Fixture path has no supplied root: {value}")

    def decode(self, value: dict) -> Path:
        _object(value, ("root", "path"))
        name, relative = value["root"], value["path"]
        if not isinstance(name, str) or name not in self.roots or not isinstance(relative, str):
            raise ValueError("Unknown fixture record root/path")
        path = PurePosixPath(relative)
        if (
            not relative
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or path.as_posix() != relative
        ):
            raise ValueError("Fixture record requires canonical relative paths")
        result = (self.roots[name] / relative).resolve()
        if not result.is_relative_to(self.roots[name]):
            raise ValueError("Fixture record path escapes its root")
        return result

    def file(self, value: FixtureFile) -> dict:
        return {**self.encode(value.path), "sha256": value.sha256}

    def read_file(self, value) -> FixtureFile | None:
        if value is None:
            return None
        _object(value, ("root", "path", "sha256"))
        _digest(value["sha256"])
        return FixtureFile(self.decode({k: value[k] for k in ("root", "path")}), value["sha256"])

    def required_file(self, value) -> FixtureFile:
        result = self.read_file(value)
        if result is None:
            raise ValueError("Required fixture file is null")
        return result


def dump_fixture_build(build: FixtureBuild, *, roots: Mapping[str, Path]) -> str:
    """Return canonical JSON for a verified compiled build under caller roots."""
    from .fixture import FIXTURE_API_VERSION

    verify_fixture_build(build)
    mapping = _Roots(roots)
    return (
        _json(
            {
                "schema_version": 1,
                "fixture_api": FIXTURE_API_VERSION,
                "build": _payload(build, file=mapping.file, path=mapping.encode),
            }
        )
        + "\n"
    )


def _tensor(value) -> FixtureTensor:
    data = dict(_object(value, ("name", "index", "dtype", "shape", "quantization")))
    data["shape"] = tuple(data["shape"])
    quant = data["quantization"]
    if quant is not None:
        if "axis" in quant:
            _object(quant, ("axis", "scales", "zero_points"))
            if type(quant["axis"]) is not int or any(
                type(s) not in (int, float) for s in quant["scales"]
            ):
                raise ValueError("Invalid per-axis quantization types")
            quant = PerAxisQuantization(
                quant["axis"], tuple(quant["scales"]), tuple(quant["zero_points"])
            )
        else:
            if type(quant["scale"]) not in (int, float):
                raise ValueError("Invalid quantization scale type")
            quant = PerTensorQuantization(**_object(quant, ("scale", "zero_point")))
    data["quantization"] = quant
    return FixtureTensor(**data)


def _memory(value) -> MemoryPlan | None:
    if value is None:
        return None
    _object(value, ("engine", "regions", "model_weight_bytes", "has_overflow"))
    if (
        type(value["model_weight_bytes"]) is not int
        or value["model_weight_bytes"] < 0
        or type(value["has_overflow"]) is not bool
    ):
        raise ValueError("Invalid memory-plan total/overflow types")
    regions = []
    for region in value["regions"]:
        _object(region, ("region", "capacity", "used", "consumers"))
        if any(type(region[k]) is not int or region[k] < 0 for k in ("capacity", "used")):
            raise ValueError("Invalid memory-region sizes")
        for consumer in region["consumers"]:
            if (
                not isinstance(consumer["name"], str)
                or type(consumer["size"]) is not int
                or consumer["size"] < 0
                or (consumer["symbol"] is not None and not isinstance(consumer["symbol"], str))
            ):
                raise ValueError("Invalid memory-consumer fields")
        consumers = tuple(
            MemoryConsumer(**_object(c, ("name", "size", "kind", "symbol")))
            for c in region["consumers"]
        )
        regions.append(
            MemoryRegionUsage(region["region"], region["capacity"], region["used"], consumers)
        )
    return MemoryPlan(
        value["engine"], tuple(regions), value["model_weight_bytes"], value["has_overflow"]
    )


def load_fixture_build(record: str | bytes, *, roots: Mapping[str, Path]) -> FixtureBuild:
    """Load and verify a producer record without resolving/preparing an engine."""
    from .fixture import FIXTURE_API_VERSION

    if not isinstance(record, (str, bytes)):
        raise ValueError("Fixture build record must be JSON text or bytes")
    mapping = _Roots(roots)
    outer = _object(_read_json(record), ("schema_version", "fixture_api", "build"))
    if (
        type(outer["schema_version"]) is not int
        or outer["schema_version"] != 1
        or _json(outer["fixture_api"]) != _json(FIXTURE_API_VERSION)
    ):
        raise ValueError("Unsupported fixture build record/API version")
    value = dict(_object(outer["build"], _FIELDS))
    try:
        value["app_dir"] = mapping.decode(value["app_dir"])
        for name in _FILE_FIELDS:
            value[name] = mapping.read_file(value[name])
        for name in _FILES_FIELDS:
            if not isinstance(value[name], list):
                raise ValueError("Fixture file collections require JSON arrays")
            value[name] = tuple(mapping.required_file(f) for f in value[name])
        value["inputs"] = tuple(
            FixtureIO(
                _tensor(io["tensor"]), mapping.required_file(io["data"]), FixtureRole(io["role"])
            )
            for io in (_object(io, ("tensor", "data", "role")) for io in value["inputs"])
        )
        value["output_tensors"] = tuple(_tensor(t) for t in value["output_tensors"])
        value["target"] = FixtureTarget(
            **_object(value["target"], ("board", "device", "load_address"))
        )
        value["toolchain"] = ToolchainInfo(
            **_object(value["toolchain"], ("compiler", "compiler_version", "cmake_version"))
        )
        value["planned_memory"] = _memory(value["planned_memory"])
        if value["engine_source"] is not None:
            value["engine_source"] = EngineSource(
                **_object(value["engine_source"], ("package", "version", "commit"))
            )
        for name, enum in (
            ("engine", EngineType),
            ("timing_scope", FixtureTimingScope),
            ("state_policy", FixtureStatePolicy),
            ("arena_location", Placement),
            ("weights_location", Placement),
        ):
            value[name] = enum(value[name]) if value[name] is not None else None
        value["capabilities"] = tuple(
            (dtype, FixtureCapability(capability)) for dtype, capability in value["capabilities"]
        )
        value["aot_arena_scan"] = tuple(tuple(pair) for pair in value["aot_arena_scan"])
        if value["psram_weights"] is not None:
            psram = _object(value["psram_weights"], ("payloads", "image_sha256", "clock_hz"))
            value["psram_weights"] = FixturePsramWeights(
                tuple(
                    FixturePsramPayload(**_object(p, ("region_id", "size", "checksum")))
                    for p in psram["payloads"]
                ),
                psram["image_sha256"],
                psram["clock_hz"],
            )
        result = FixtureBuild(**value)
        verify_fixture_build(result)
        # Caller roots may add aliases or narrower directories after the record
        # was written. Decode already checked containment and canonical relative
        # paths; compare field types using the same resolved path representation.
        received = dict(outer["build"])
        received["app_dir"] = mapping.encode(result.app_dir)
        for name in _FILE_FIELDS:
            item = getattr(result, name)
            received[name] = mapping.file(item) if item is not None else None
        for name in _FILES_FIELDS:
            received[name] = [mapping.file(item) for item in getattr(result, name)]
        received["inputs"] = [
            {**io, "data": mapping.file(parsed.data)}
            for io, parsed in zip(received["inputs"], result.inputs, strict=True)
        ]
        if _json(_payload(result, file=mapping.file, path=mapping.encode)) != _json(received):
            raise ValueError("Noncanonical fixture build field types/paths")
        return result
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError("Malformed fixture build record") from exc


def fixture_capture_request(
    build: FixtureBuild,
    *,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expected_duration_s: float | None = None,
) -> FixtureCaptureRequest:
    """Construct capture facts from a verified build; the caller supplies authority separately."""
    from .fixture_capture import FixtureCaptureRequest

    verify_fixture_build(build)
    assert build.binary is not None and build.flat_binary is not None
    return FixtureCaptureRequest(
        elf=build.binary,
        image=build.flat_binary,
        load_address=build.target.load_address,
        output_size=build.output_tensors[0].size_bytes,
        device=build.target.device,
        jlink_serial=jlink_serial,
        evidence_dir=evidence_dir,
        settle_seconds=settle_seconds,
        timing_scope=build.timing_scope,
        target=build.target,
        arena_capacity=build.arena_capacity if build.engine is not EngineType.HELIA_AOT else None,
        expected_duration_s=expected_duration_s,
        extra_output_sizes=tuple(t.size_bytes for t in build.output_tensors[1:]),
        arena_scan_sizes=tuple(size for _, size in build.aot_arena_scan),
        psram_weights=build.psram_weights,
    )
