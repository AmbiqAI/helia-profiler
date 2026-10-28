"""A saved run result mapped to the schema-owned portable result view."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .._semantic_hash import semantic_hash
from ..fixtures.power import GROSS_RAIL_SCOPE, FixtureBoardPowerRequest
from ..identity import Unknown
from ..runs.record import RunResultRecord

MAPPING_VERSION = "hpx-run-2"
_RESULT = "native-result"
#: What ``engine.version`` means for each engine.
_VERSION_BASIS = {
    # heliaAOT generates the module that runs; its release is what versions the engine.
    "helia-aot": "aot_provider_release",
    "helia-rt": "linked_runtime_release",
    "litert": "linked_runtime_release",
    "executorch": "linked_runtime_release",
}
_CLOCK_DOMAINS = {"stimer": "timer", "cpu": "cpu"}
_FILE_KEYS = {"root", "path", "sha256", "size_bytes"}
_VERIFIER = "helia-profiler.run_record/run-1"
_CHECKS = (
    "run-result-schema-version",
    "compiled-build-path-size-sha256-and-verify",
    "evidence-path-size-sha256",
    "identity-recomputed",
    "power-request-fits-build",
)
_COMPARED = "output-comparison-recomputed"
_LIMITATIONS = (
    "Not authentication or numerical qualification.",
    "Free text, such as a failed capture's error message, is recorded, not verified.",
    "Clock rates are configured or firmware-reported, not externally calibrated.",
    "Rail energy is the gross MCU rail over the timed restore-and-invoke loop; "
    "nothing is subtracted.",
    "Per-layer attribution is not mapped.",
)
_COMPARED_LIMITATION = (
    "The output comparison is against the recorded reference and the caller's "
    "tolerance; it does not qualify the engine numerically."
)
_NOT_COMPARED = "Outputs were not compared with a reference."


@dataclass
class _Observations:
    """The observations of one view, each citing the saved result by JSON pointer."""

    blocked: str | None
    roles: set[str]
    items: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        name: str,
        value: float | int | None,
        *,
        unit: str,
        domain: str,
        scope: str,
        origin: str,
        statistic: str,
        sources: tuple[str, ...],
        count: int | None = None,
        transformation: str | None = None,
        reason: str | None = None,
        clocks: list[dict[str, Any]] | None = None,
        evidence: tuple[str, ...] = (),
    ) -> None:
        unavailable = self.blocked or reason or ("not_recorded" if value is None else None)
        basis = None
        if not unavailable:
            basis = {
                "kind": "derived" if transformation else "reported",
                "sources": [{"artifact": _RESULT, "pointer": pointer} for pointer in sources],
                "transformation": transformation,
            }
            if clocks:
                basis["clocks"] = clocks
        self.items.append(
            {
                "id": name,
                "metric": name,
                "unit": unit,
                "domain": domain,
                "origin": origin,
                "scope": scope,
                "statistic": statistic,
                "value": None if unavailable else value,
                "unavailable_reason": unavailable,
                "sample_count": None if unavailable else count,
                "evidence": [_RESULT, *(ref for ref in evidence if ref in self.roles)],
                "basis": basis,
            }
        )


def run_observations(record: RunResultRecord) -> dict[str, Any]:
    """Map a verified run result; schema validity alone does not establish verification."""
    result, document = record.result, record.document
    home = document["location"]["root"]
    artifacts = _artifacts(record, home)
    observations = _Observations(
        None if result.succeeded else "capture_failed", {item["id"] for item in artifacts}
    )
    _latency(record, observations)
    _energy(record, observations, home)
    _memory(record, observations)
    return {
        **_header(record, artifacts),
        "observations": observations.items,
        "verification": _verification(record),
        "extensions": {"helia-profiler": _extension(record)},
    }


def _header(record: RunResultRecord, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    result, identity = record.result, record.result.identity
    model = result.compiled.build.model
    return {
        "schema_version": "1.0-draft",
        "producer": {
            "name": "helia-profiler",
            "version": record.hpx_version,
            "revision": None,
            "mapping_version": MAPPING_VERSION,
        },
        "native_result": {
            "schema": record.document["schema"],
            "identity": record.sha256,
            "artifacts": artifacts,
        },
        "workload": {
            # The pinned file, not the plan's path, which may be another host's.
            "name": model.path.name if model is not None else None,
            "model_identity": _text(identity.model_sha256),
            "input_identity": _text(identity.inputs_hash),
            "state_policy": identity.run.lifecycle,
        },
        "engine": {
            "name": identity.runtime.engine.value,
            "version": _text(identity.runtime.version),
        },
        "target": {"name": identity.build.board, "identity": _target_config(record)["identity"]},
        "outcome": {
            "status": "completed" if result.succeeded else "failed",
            "stage": None if result.succeeded else "capture",
            "reason": None if result.succeeded else result.capture.error or None,
        },
    }


def _latency(record: RunResultRecord, observations: _Observations) -> None:
    timing, run = record.result.capture.timing, record.result.identity.run
    latency = None
    if timing is not None and timing.timer_hz > 0 and timing.iterations > 0:
        latency = timing.ticks / timing.timer_hz / timing.iterations * 1000
    domain = _CLOCK_DOMAINS.get(run.timing_clock) if isinstance(run.timing_clock, str) else None
    clocks = []
    if isinstance(run.timing_clock_hz, int) and run.timing_clock_hz > 0:
        clocks.append(
            _clock("configured", run.timing_clock_hz, "/identity/run/timing_clock_hz", domain)
        )
    if timing is not None and timing.timer_hz > 0:
        clocks.append(_clock("reported", timing.timer_hz, "/capture/timing/timer_hz", domain))
    if domain != "cpu":
        for role, fact in _cpu_clocks(record)["facts"].items():
            if fact["value_hz"] is not None:
                clocks.append(
                    {
                        "role": role,
                        "domain": "cpu",
                        "value_hz": fact["value_hz"],
                        "source": fact["source"],
                    }
                )
    observations.add(
        "latency.invoke.v1" if run.timing_scope == "invoke_only" else "latency.restore_invoke.v1",
        latency,
        unit="ms",
        domain="cpu",
        scope=run.timing_scope,
        origin="measured",
        statistic="mean",
        sources=("/capture/timing/ticks", "/capture/timing/timer_hz", "/capture/timing/iterations"),
        count=timing.iterations if timing is not None else None,
        transformation="ticks / timer_hz / iterations * 1000",
        clocks=clocks,
    )


def _clock(role: str, hz: int, pointer: str, domain: str | None) -> dict[str, Any]:
    return {
        "role": role,
        "value_hz": hz,
        "source": {"artifact": _RESULT, "pointer": pointer},
        "domain": domain,
    }


def _energy(record: RunResultRecord, observations: _Observations, home: str) -> None:
    result = record.result
    energy = result.capture.rail_energy
    reason = (
        None
        if energy is not None
        else "energy_not_measured_timing_only"
        if result.power is None
        else "energy_not_measured_supply_only"
        if isinstance(result.power, FixtureBoardPowerRequest)
        else None
    )
    raw = record.document["capture"]["rail_energy"]
    evidence = (_file_id(raw["raw"], home),) if energy is not None else ()
    rail: dict[str, Any] = {
        # The MCU rail feeds the core, its memories and on-chip peripherals, not the CPU alone.
        "domain": "system",
        "scope": GROSS_RAIL_SCOPE,
        "origin": "measured",
        "statistic": "mean",
        "reason": reason,
        "evidence": evidence,
    }
    observations.add(
        "energy.per_inference.v1",
        energy.energy_per_iteration_j * 1e6 if energy else None,
        unit="uJ",
        sources=("/capture/rail_energy/energy_per_iteration_j", "/capture/rail_energy/iterations"),
        transformation="joules * 1e6",
        count=energy.iterations if energy else None,
        **rail,
    )
    # A time average over the gated window, whose instrument sample count is not retained.
    observations.add(
        "power.mean.v1",
        energy.mean_power_w * 1e3 if energy else None,
        unit="mW",
        sources=("/capture/rail_energy/mean_power_w", "/capture/rail_energy/duration_s"),
        transformation="watts * 1e3",
        **rail,
    )


def _memory(record: RunResultRecord, observations: _Observations) -> None:
    memory = record.result.capture.memory
    for name, field_name, scope in (
        ("memory.arena_capacity.v1", "capacity", "arena_capacity"),
        ("memory.arena_used_after_invoke.v1", "after_invoke", "allocator_after_invoke"),
    ):
        observations.add(
            name,
            getattr(memory, field_name) if memory is not None else None,
            unit="B",
            domain="memory",
            scope=scope,
            origin="reported",
            statistic="size",
            sources=(f"/capture/memory/{field_name}",),
        )


def _verification(record: RunResultRecord) -> dict[str, Any]:
    compared = record.result.verification is not None
    return {
        "verifier": _VERIFIER,
        "checks": [*_CHECKS, _COMPARED] if compared else list(_CHECKS),
        "limitations": [*_LIMITATIONS, _COMPARED_LIMITATION if compared else _NOT_COMPARED],
    }


def _extension(record: RunResultRecord) -> dict[str, Any]:
    result, document = record.result, record.document
    identity = result.identity
    run, version = identity.run, identity.runtime.version
    return {
        "engine_version_basis": _VERSION_BASIS.get(identity.runtime.engine.value),
        "engine_version_unavailable_reason": version.reason
        if isinstance(version, Unknown)
        else None,
        "build_producer": _plain(identity.producer),
        "target_config": _target_config(record),
        "cpu_clocks": _cpu_clocks(record),
        "semantic_hashes": {
            "intent_hash": identity.intent_hash,
            "build_hash": _plain(identity.build_hash),
            "inputs_hash": _plain(identity.inputs_hash),
            "observation_hash": identity.observation_hash,
            "binding": result.compiled.binding,
        },
        "run": {
            "lifecycle": run.lifecycle,
            "timing_scope": run.timing_scope,
            "iterations": run.iterations,
            "warmups": run.warmups,
            "instance_boundary": _plain(run.instance_boundary),
            "first_invoke_initializer": _plain(run.first_invoke_initializer),
            "first_invoke_window": _plain(run.first_invoke_window),
            "timing_clock": _plain(run.timing_clock),
            "timing_clock_hz": _plain(run.timing_clock_hz),
            "timing_statistic": _plain(run.timing_statistic),
        },
        "capture_state": result.capture.state,
        "power_request": None if result.power is None else document["power"]["kind"],
        "output_comparison": _comparison(document["verification"]),
    }


def _target_config(record: RunResultRecord) -> dict[str, Any]:
    facts = record.result.identity.build
    target = record.result.compiled.build.target
    values = {
        "board": facts.board,
        "device": target.device,
        "load_address": target.load_address,
        "clock_profile": facts.clock_profile,
        "cpu_hz": facts.cpu_hz,
        "arena_location": facts.arena_location,
        "weights_location": facts.weights_location,
    }
    home = record.document["location"]["root"]
    compiled_artifact = _file_id(record.document["compiled_build"], home)
    sources = {
        name: {
            "artifact": compiled_artifact if name in ("device", "load_address") else _RESULT,
            "pointer": f"/build/build/target/{name}"
            if name in ("device", "load_address")
            else f"/identity/build/{name}",
        }
        for name in values
    }
    unavailable = {}
    for name, value in values.items():
        if name in ("cpu_hz", "load_address"):
            valid = type(value) is int and value >= (1 if name == "cpu_hz" else 0)
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            unavailable[name] = (
                value.reason if isinstance(value, Unknown) else "not_recorded_or_invalid"
            )
    basis = {name: None if name in unavailable else value for name, value in values.items()}
    return {
        "schema_id": "hpx.target-config",
        "schema_version": 1,
        "identity": None if unavailable else semantic_hash("target-config", basis),
        "basis": basis,
        "sources": sources,
        "unavailable_reasons": unavailable,
    }


def _cpu_clocks(record: RunResultRecord) -> dict[str, Any]:
    timing = record.result.capture.timing
    values = {
        "configured": (record.result.identity.build.cpu_hz, "/identity/build/cpu_hz"),
        "reported": (timing.cpu_hz if timing is not None else None, "/capture/timing/cpu_hz"),
    }
    facts = {}
    for role, (value, pointer) in values.items():
        valid = type(value) is int and value > 0
        source = {"artifact": _RESULT, "pointer": pointer}
        facts[role] = {
            "value_hz": value if valid else None,
            "source": source,
            "unavailable_reason": None
            if valid
            else value.reason
            if isinstance(value, Unknown)
            else "not_recorded_or_invalid",
        }
    return {"schema_id": "hpx.cpu-clocks", "schema_version": 1, "facts": facts}


def _comparison(verification: dict[str, Any] | None) -> dict[str, Any] | None:
    if verification is None:
        return None
    return {key: verification[key] for key in ("reference", "tolerance", "matched", "tensors")}


def _artifacts(record: RunResultRecord, home: str) -> list[dict[str, Any]]:
    document = record.document
    artifacts = [
        {
            "id": _RESULT,
            "role": "hpx.run-result",
            "location": _location(document["location"], home),
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
        },
        _artifact(document["compiled_build"], home, "hpx.compiled-build"),
    ]
    capture = document["capture"]
    special = {_key(item): "hpx.capture-output" for item in capture["outputs"]}
    if capture["rail_energy"] is not None:
        special[_key(capture["rail_energy"]["raw"])] = "hpx.rail-trace"
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _files(capture):
        evidence.setdefault(_key(item), item)
    for key in sorted(evidence):
        artifacts.append(_artifact(evidence[key], home, special.get(key, "hpx.capture-evidence")))
    verification = document["verification"] or {}
    for item in verification.get("reference_files", ()):
        artifacts.append(_artifact(item, home, "hpx.reference-output"))
    return artifacts


def _files(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if value.keys() == _FILE_KEYS:
            return [value]
        return [item for child in value.values() for item in _files(child)]
    if isinstance(value, list):
        return [item for child in value for item in _files(child)]
    return []


def _artifact(item: dict[str, Any], home: str, role: str) -> dict[str, Any]:
    return {
        "id": _file_id(item, home),
        "role": role,
        "location": _location(item, home),
        "sha256": item["sha256"],
        "size_bytes": item["size_bytes"],
    }


def _key(item: dict[str, Any]) -> tuple[str, str]:
    return item["root"], item["path"]


def _file_id(item: dict[str, Any], home: str) -> str:
    return f"artifact:{_location(item, home)}"


def _location(item: dict[str, Any], home: str) -> str:
    """The path under the result's own root, or ``<root>:<path>`` under another."""
    return item["path"] if item["root"] == home else f"{item['root']}:{item['path']}"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _plain(value: Any) -> Any:
    return {"unknown": value.reason} if isinstance(value, Unknown) else value
