"""Build a heliaOptimus calibration file from heliaAOT result bundles (#648).

A bundle's ``summary.json`` records the conditions it measured under and the
model it profiled; its per-layer table records each execution position's
cycles; heliaAOT's pinned ``<prefix>_plan.json`` names the operator at each
position and the knob values the compiler chose for it. Joining the three
prices every (operator, knob, value) the run exercised::

    operators[<plan operator id>][<knob>][<value>] = {"cycles": <cycles>}

A price is keyed by the operator, the knob, its value and the operator's
other knobs' values in that capture. With the capture setup held equal,
whatever else the plan reports for the operator (its fixed choices, the
kernel entry) follows from the knob values, so it is recorded with the price
as a fact and never compared.

Everything that would make a price mean something else is an error, never a
silent skip: another model, other conditions, a capture setup (the blocking
comparison dimensions and the kernel build) that differs, a run whose
cycles the profiler flagged, an overflowed counter, a position the plan and
the capture disagree on, two prices of one key that disagree, or two
records of one vector's facts that disagree. The setup is the blocking
comparison dimensions, the dimensions that change cycle counts although a
comparison only reports them (toolchain and compiler version, engine
version and backend, arena placement and memory, link family), and the
kernel build. The file is schema version 1 as
``helia_optimus.cost.calibration`` reads it; what hpx needs for a later
``--merge`` sits under ``capture``, a key heliaOptimus ignores.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

from ..errors import CalibrationError
from ..evaluation.comparability import read_dimensions
from ..evaluation.compare import RunArtifacts, load_run_artifacts
from ..results import (
    METRIC_BLOCKING_CODE_BY_GROUP,
    ComparisonDimension,
    DimensionEffect,
    IssueCode,
    ResultValidity,
    Severity,
    dimensions_with_effect,
    error_metric_group,
)
from ..results.models import Conditions
from ..results.serde import nested_get

#: The calibration file shape this release writes; heliaOptimus checks it.
CALIBRATION_SCHEMA_VERSION = 1
#: heliaAOT's plan schema this release reads.
PLAN_SCHEMA_VERSION = 1
#: Largest relative disagreement two prices of one key may have.
DEFAULT_TOLERANCE = 0.02

#: The comparison dimensions whose difference blocks a comparison, read as
#: ``hpx compare`` reads them and compared under its rule (a value absent on
#: either side does not block).
BLOCKING_DIMENSIONS: tuple[ComparisonDimension, ...] = (
    dimensions_with_effect(DimensionEffect.IDENTITY_BLOCKING)
    + dimensions_with_effect(DimensionEffect.POWER_METRIC_BLOCKING)
    + dimensions_with_effect(DimensionEffect.METRIC_GATE)
    + dimensions_with_effect(DimensionEffect.MEMORY_METRIC_BLOCKING)
)
#: Dimensions ``hpx compare`` reports as informative that change cycle
#: counts all the same; any difference, an absent value included, refuses.
CYCLE_DIMENSIONS: tuple[ComparisonDimension, ...] = (
    ComparisonDimension.TOOLCHAIN,
    ComparisonDimension.COMPILER_VERSION,
    ComparisonDimension.ENGINE_VERSION,
    ComparisonDimension.ENGINE_BACKEND,
    ComparisonDimension.ARENA_LOCATION,
    ComparisonDimension.LINK_FAMILY,
)
#: What identifies a capture's setup, with the planned arena memory and the
#: kernel build below.
CAPTURE_DIMENSIONS: tuple[ComparisonDimension, ...] = BLOCKING_DIMENSIONS + tuple(
    dimension for dimension in CYCLE_DIMENSIONS if dimension not in BLOCKING_DIMENSIONS
)
#: The memory the plan put the arena in, which no comparison dimension names.
ARENA_MEMORY = "arena_memory"
#: The kernel build: the commit is always compared, the version when both
#: sides state one (an untagged lock records why the version is unknown).
KERNEL_COMMIT = "kernel_commit"
KERNEL_VERSION = "kernel_version"
_STRICT: frozenset[str] = frozenset(
    {dimension.value for dimension in CYCLE_DIMENSIONS} | {ARENA_MEMORY, KERNEL_COMMIT}
)
#: The one metric family whose confined errors leave the cycles usable.
_EXCUSED_METRIC_GROUP = "power"

#: Issue codes after which a run's cycle counts are not a measurement,
#: whatever the run's validity says.
CYCLES_UNUSABLE: frozenset[str] = frozenset(
    code.value
    for code in (
        IssueCode.PMU_MISSING,
        IssueCode.PMU_COUNTER_OVERFLOW,
        IssueCode.FIRMWARE_MODEL_MISMATCH,
        IssueCode.FIRMWARE_MODEL_IDENTITY_UNVERIFIABLE,
        IssueCode.PROFILE_CLEAN_WINDOW_FROZEN,
        IssueCode.PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW,
        IssueCode.PROFILE_CLEAN_WINDOW_STALLED,
    )
)

_PLAN_ARTIFACT = "helia-aot.plan"
_PLAN_SUFFIX = "_plan.json"
_LAYER_SUFFIX = re.compile(r":-?\d+$")
#: The recorded facts of an operator, as the file names them.
_FIXED_PREFIX = "fixed:"
_KERNEL_ENTRY = "kernel:entry"

#: A price's key: operator, knob, value, and the other knobs' values.
PriceKey = tuple[str, str, str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class OperatorSample:
    """One measured operator: the whole knob vector it ran with, its cycles, and
    what the plan reported for it under that vector."""

    operator: str
    knobs: Mapping[str, str]
    cycles: float
    #: The fixed choices and the kernel entry: recorded, never compared.
    facts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Price:
    """One price as a file holds it: the key's vector and facts, when recorded."""

    cycles: float
    #: The other knobs' values, or ``None`` for a file that records none.
    others: Mapping[str, str] | None = None
    facts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationSource:
    """What one bundle, or an existing file, contributes to a table."""

    source: str
    model_sha256: str
    subgraph: int
    #: The recorded block as written: heliaOptimus compares every field.
    conditions: Mapping[str, Any]
    #: The capture setup, by name; empty only for a file hpx did not write.
    dimensions: Mapping[str, Any]
    samples: tuple[OperatorSample, ...] = ()
    #: A file's prices as it holds them, by (operator, knob, value).
    prices: Mapping[tuple[str, str, str], Price] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationTable:
    """The calibration file, typed: one model, one set of conditions."""

    model_sha256: str
    subgraph: int
    conditions: Mapping[str, Any]
    #: (operator id, knob, value) -> the price, with its vector and facts when known.
    prices: Mapping[tuple[str, str, str], Price]
    #: The capture setup the prices were measured with; empty for a file hpx did not write.
    dimensions: Mapping[str, Any]

    @property
    def operators(self) -> dict[str, dict[str, dict[str, float]]]:
        """operator id -> knob -> value -> cycles, as the file lays them out."""
        out: dict[str, dict[str, dict[str, float]]] = {}
        for (operator, knob, value), price in self.prices.items():
            out.setdefault(operator, {}).setdefault(knob, {})[value] = price.cycles
        return out

    @property
    def price_count(self) -> int:
        return len(self.prices)

    def to_dict(self) -> dict[str, Any]:
        recorded: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for (operator, knob, value), price in self.prices.items():
            if price.others is None:
                continue
            recorded.setdefault(operator, {}).setdefault(knob, {})[value] = {
                "others": dict(price.others),
                "facts": dict(price.facts),
            }
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "model": {"sha256": self.model_sha256, "subgraph": self.subgraph},
            "conditions": dict(self.conditions),
            "operators": {
                operator: {
                    knob: {value: {"cycles": cycles} for value, cycles in sorted(values.items())}
                    for knob, values in knobs.items()
                }
                for operator, knobs in self.operators.items()
            },
            "capture": {"dimensions": dict(self.dimensions), "prices": recorded},
        }

    def as_source(self, source: str) -> CalibrationSource:
        """The table as a source, so ``--merge`` holds it to the same rules."""
        return CalibrationSource(
            source=source,
            model_sha256=self.model_sha256,
            subgraph=self.subgraph,
            conditions=self.conditions,
            dimensions=self.dimensions,
            prices=self.prices,
        )


def calibrate(
    bundles: Sequence[Path],
    *,
    output: Path,
    merge: bool = False,
    tolerance: float = DEFAULT_TOLERANCE,
) -> CalibrationTable:
    """Price every knob value the bundles exercised and write *output*.

    With ``merge``, the prices already in *output* take part under the same
    rules as a bundle's, and every one of them is kept.

    Raises:
        CalibrationError: when a bundle, a price or the existing file breaks a rule.
    """
    if not bundles:
        raise CalibrationError("No result bundle to calibrate from.")
    if not math.isfinite(tolerance) or not 0 <= tolerance < 1:
        raise CalibrationError(
            f"--tolerance must be a fraction from 0 up to but not including 1 (got {tolerance})."
        )
    sources: list[CalibrationSource] = []
    if merge:
        if not output.is_file():
            raise CalibrationError(
                f"Nothing to merge into: {output} does not exist.",
                hint="Drop --merge to write a new file.",
            )
        sources.append(read_calibration(output).as_source(str(output)))
    sources.extend(read_bundle(bundle) for bundle in bundles)
    table = merge_sources(sources, tolerance=tolerance)
    write_calibration(table, output)
    return table


def write_calibration(table: CalibrationTable, path: Path) -> Path:
    """Write the file whole: a temporary file beside it, then one rename.

    The temporary file has a name of this call's own and is created
    exclusively with a plain open, so the umask applies as for any file and a
    failed write never removes a file this call did not create; when *path*
    already exists its mode is copied onto the temporary before the rename.
    A path that is a symbolic link is refused rather than replaced.

    Raises:
        CalibrationError: when *path* is a symbolic link.
    """
    if path.is_symlink():
        raise CalibrationError(
            f"{path} is a symbolic link; hpx calibrate writes a regular file.",
            hint="Pass the file the link points to, or another path.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(table.to_dict(), indent=2, allow_nan=False) + "\n"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    handle = open(temporary, "x", encoding="utf-8", newline="\n")
    try:
        with handle:
            handle.write(rendered)
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def read_calibration(path: Path) -> CalibrationTable:
    """Read a calibration file this release wrote, or one of the same shape.

    A file without a ``capture`` block (one hpx did not write) holds prices
    whose vectors are unknown; they merge unchecked.

    Raises:
        CalibrationError: when the file is not JSON, another schema version, or malformed.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalibrationError(f"Could not read calibration file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationError(f"Calibration file {path} is not a JSON object.")
    if data.get("schema_version") != CALIBRATION_SCHEMA_VERSION:
        raise CalibrationError(
            f"Calibration file {path} is schema version {data.get('schema_version')!r}; "
            f"this release reads version {CALIBRATION_SCHEMA_VERSION}.",
        )
    sha256 = nested_get(data, "model", "sha256")
    subgraph = nested_get(data, "model", "subgraph")
    conditions = data.get("conditions")
    operators = data.get("operators")
    if (
        not isinstance(sha256, str)
        or not sha256
        or type(subgraph) is not int
        or not isinstance(conditions, dict)
        or not isinstance(operators, dict)
    ):
        raise CalibrationError(
            f"Calibration file {path} is malformed: it needs model.sha256, model.subgraph, "
            "conditions and operators.",
        )
    recorded = nested_get(data, "capture", "prices")
    prices: dict[tuple[str, str, str], Price] = {}
    for operator, knobs in operators.items():
        if not isinstance(knobs, dict):
            raise CalibrationError(
                f"Calibration file {path}: operator {operator} is not an object."
            )
        for knob, values in knobs.items():
            if not isinstance(values, dict):
                raise CalibrationError(
                    f"Calibration file {path}: operator {operator} knob {knob} is not an object."
                )
            for value, cost in values.items():
                cycles = cost.get("cycles") if isinstance(cost, dict) else None
                if not _is_cycle_count(cycles):
                    raise CalibrationError(
                        f"Calibration file {path}: operator {operator} {knob}={value} "
                        "has no finite, non-negative cycles."
                    )
                entry = nested_get(recorded, operator, knob, value)
                others = _string_map(nested_get(entry, "others"))
                facts = _string_map(nested_get(entry, "facts"))
                prices[(operator, knob, value)] = Price(
                    float(cycles), others, facts if facts is not None else {}
                )
    dimensions = nested_get(data, "capture", "dimensions")
    return CalibrationTable(
        model_sha256=sha256,
        subgraph=subgraph,
        conditions=conditions,
        prices=prices,
        dimensions=dimensions if isinstance(dimensions, dict) else {},
    )


def _string_map(value: Any) -> dict[str, str] | None:
    """``{name: str}`` from a capture entry, or ``None`` when it is not that."""
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        return None
    return dict(value)


def read_bundle(path: Path) -> CalibrationSource:
    """Join one result bundle's capture to its pinned heliaAOT plan.

    Raises:
        CalibrationError: when the profiler flagged the run's cycles, the
            bundle records no conditions or no usable weights memory, has no
            plan or a plan for another model, a layer overflowed, or the plan
            and the capture disagree on a position or an operator type.
        ReportError: when the bundle is not a verified, current result bundle.
    """
    run = load_run_artifacts(path)
    source = str(run.path)
    _require_usable_cycles(run, source)
    conditions = _conditions(run, source)
    model_sha256 = _model_sha256(run, source)
    plan = _plan(run, source)
    plan_sha256 = nested_get(plan, "model", "sha256")
    subgraph = nested_get(plan, "model", "subgraph")
    if not isinstance(plan_sha256, str) or type(subgraph) is not int:
        raise CalibrationError(f"Bundle {source}: the plan names no model sha256 and subgraph.")
    if plan_sha256 != model_sha256:
        raise CalibrationError(
            f"Bundle {source}: the plan was compiled from model {plan_sha256[:12]} but the "
            f"run profiled {model_sha256[:12]}.",
            hint="The plan and the capture come from different builds; profile again.",
        )
    layers = _layers(run, source)
    operators = plan.get("operators")
    if not isinstance(operators, list):
        raise CalibrationError(f"Bundle {source}: the plan has no operators list.")
    samples: list[OperatorSample] = []
    positions: set[int] = set()
    ids: set[str] = set()
    for entry in operators:
        sample, position, op_type = _plan_operator(entry, source)
        if position in positions:
            raise CalibrationError(f"Bundle {source}: the plan lists position {position} twice.")
        if sample.operator in ids:
            raise CalibrationError(
                f"Bundle {source}: the plan lists operator {sample.operator} twice."
            )
        positions.add(position)
        ids.add(sample.operator)
        if position not in layers:
            raise CalibrationError(
                f"Bundle {source}: plan operator {sample.operator} at position {position} has "
                "no measured layer.",
                hint="The plan and the capture come from different builds; profile again.",
            )
        cycles, op = layers[position]
        if _firmware_label(op_type) != _firmware_label(op):
            raise CalibrationError(
                f"Bundle {source}: the plan has {op_type} at position {position} but the "
                f"capture measured {op} there.",
                hint="The plan and the capture come from different builds; profile again.",
            )
        if sample.knobs:
            samples.append(OperatorSample(sample.operator, sample.knobs, cycles, sample.facts))
    unplanned = sorted(set(layers) - positions)
    if unplanned:
        position = unplanned[0]
        raise CalibrationError(
            f"Bundle {source}: measured layer {position} ({layers[position][1]}) has no "
            "operator in the plan.",
            hint="The plan and the capture come from different builds; profile again.",
        )
    return CalibrationSource(
        source=source,
        model_sha256=model_sha256,
        subgraph=subgraph,
        conditions=conditions,
        dimensions=_capture_dimensions(run),
        samples=tuple(samples),
    )


def merge_sources(
    sources: Sequence[CalibrationSource], *, tolerance: float = DEFAULT_TOLERANCE
) -> CalibrationTable:
    """One table from every source, holding them to one model, one set of
    conditions, one capture setup, and agreeing prices.

    Every source that records its setup must agree with every other that
    does; a file that records none (one hpx did not write) merges unchecked
    and the table records the bundles' setup. A price is keyed by operator,
    knob, value and the other knobs' values; two prices of one key must agree
    within *tolerance*, relative to the larger, and a file's price is never
    replaced. The file holds one price per (operator, knob, value), so when a
    later key differs from the held one only in its vector the held price
    stands. A vector's facts may be recorded once; a differing record is an
    error.

    Raises:
        CalibrationError: on no source, another model, other conditions, a
            differing capture setup, a disagreeing price or fact, a key the
            file cannot hold, or no price at all.
    """
    if not sources:
        raise CalibrationError("No result bundle to calibrate from.")
    first = sources[0]
    for other in sources[1:]:
        if (other.model_sha256, other.subgraph) != (first.model_sha256, first.subgraph):
            raise CalibrationError(
                f"{other.source} is for model {other.model_sha256[:12]} subgraph "
                f"{other.subgraph}; {first.source} is for model {first.model_sha256[:12]} "
                f"subgraph {first.subgraph}.",
                hint="Calibrate one model at a time.",
            )
        if dict(other.conditions) != dict(first.conditions):
            raise CalibrationError(
                f"{other.source} was measured under conditions {dict(other.conditions)}; "
                f"{first.source} under {dict(first.conditions)}.",
                hint=(
                    "A calibration file holds one set of conditions; write another file "
                    "for the other."
                ),
            )
    recorded = [source for source in sources if source.dimensions]
    for other in recorded[1:]:
        _require_same_setup(recorded[0], other)
    prices: dict[tuple[str, str, str], Price] = {}
    origins: dict[tuple[str, str, str], str] = {}
    # A file's prices come first and whole, so a merge never drops or replaces one.
    for source in sources:
        for key, price in source.prices.items():
            prices.setdefault(key, price)
            origins.setdefault(key, source.source)
    for source in sources:
        for sample in source.samples:
            for knob, value in sample.knobs.items():
                others = {name: v for name, v in sample.knobs.items() if name != knob}
                key = (sample.operator, knob, value)
                held = prices.get(key)
                if held is None:
                    prices[key] = Price(sample.cycles, others, dict(sample.facts))
                    origins[key] = source.source
                    continue
                if held.others is not None and held.others != others:
                    # Another key that the file has no slot for: the price it
                    # holds for this value stands.
                    continue
                scale = max(held.cycles, sample.cycles)
                if abs(held.cycles - sample.cycles) > tolerance * scale:
                    apart = abs(held.cycles - sample.cycles) / scale * 100
                    raise CalibrationError(
                        f"{sample.operator} {knob}={value} costs {held.cycles:g} cycles in "
                        f"{origins[key]} but {sample.cycles:g} in {source.source} "
                        f"({apart:.1f}% apart, tolerance {tolerance * 100:g}%).",
                        hint="Raise --tolerance, or leave out the bundle that disagrees.",
                    )
                if held.others is not None and held.facts != dict(sample.facts):
                    raise CalibrationError(
                        f"{sample.operator} {knob}={value} with the other knobs at {others} "
                        f"reports {dict(sample.facts)} in {source.source} but "
                        f"{dict(held.facts)} in {origins[key]}, so something other than "
                        "the knobs changed between the captures.",
                        hint="Calibrate captures from one build; write another file for the other.",
                    )
                if held.others is None:
                    # The file recorded no vector for this price; the bundle's is the record.
                    prices[key] = Price(held.cycles, others, dict(sample.facts))
    if not prices:
        raise CalibrationError(
            "No operator in the bundles has a knob, so there is nothing to price.",
            hint="Only operators heliaAOT's plan lists with knobs are calibrated.",
        )
    return CalibrationTable(
        model_sha256=first.model_sha256,
        subgraph=first.subgraph,
        conditions=first.conditions,
        prices=prices,
        dimensions=recorded[0].dimensions if recorded else {},
    )


def _require_same_setup(reference: CalibrationSource, other: CalibrationSource) -> None:
    """Refuse two sources whose capture setups differ.

    A blocking dimension is compared when both sides state it, as ``hpx
    compare`` compares it; a cycle dimension, the arena memory and the
    kernel commit are always compared, and the kernel version when both
    sides state one.
    """
    for name in sorted(set(reference.dimensions) | set(other.dimensions)):
        mine, theirs = reference.dimensions.get(name), other.dimensions.get(name)
        if name in _STRICT:
            differs = mine != theirs
        elif name == KERNEL_VERSION:
            differs = isinstance(mine, str) and isinstance(theirs, str) and mine != theirs
        else:
            differs = mine is not None and theirs is not None and mine != theirs
        if differs:
            raise CalibrationError(
                f"{other.source} has {name} {theirs!r}; {reference.source} has {mine!r}, "
                "so their cycles are not one measurement.",
                hint="Calibrate captures from one setup; write another file for the other.",
            )


def _require_usable_cycles(run: RunArtifacts, source: str) -> None:
    """Refuse a run whose cycle counts the profiler flagged.

    The verdict follows the issue registry as ``hpx compare`` applies it: an
    invalid run is priced only when every error is one the registry confines
    to power metrics and that family has a metric-blocking code; an
    unrecognized code or another family confines nothing, and an invalid run
    with no error to explain it is refused. A run carrying an issue after
    which the cycle counter is not trusted is refused whatever its validity.
    """
    if run.manifest is None:
        raise CalibrationError(f"Bundle {source} has no result manifest.")
    manifest = run.manifest
    flagged = sorted({issue.code for issue in manifest.issues if issue.code in CYCLES_UNUSABLE})
    errors = sorted({issue.code for issue in manifest.issues if issue.severity == Severity.ERROR})
    excused = (
        _EXCUSED_METRIC_GROUP if _EXCUSED_METRIC_GROUP in METRIC_BLOCKING_CODE_BY_GROUP else None
    )
    unconfined = [code for code in errors if error_metric_group(code) != excused]
    invalid = manifest.validity is ResultValidity.INVALID and (unconfined or not errors)
    if flagged or invalid:
        codes = sorted(set(flagged) | set(unconfined)) or ["no issue explains it"]
        raise CalibrationError(
            f"Bundle {source} is {manifest.validity.value}: {', '.join(codes)}.",
            hint="Its cycle counts are not a measurement; profile again.",
        )


def _conditions(run: RunArtifacts, source: str) -> Mapping[str, Any]:
    recorded = run.summary.get("conditions")
    if not isinstance(recorded, dict):
        raise CalibrationError(f"Bundle {source} records no conditions.")
    try:
        typed = Conditions.from_dict(recorded)
    except ValueError as exc:
        raise CalibrationError(f"Bundle {source}: {exc}.") from exc
    if typed.weights_memory is None:
        raise CalibrationError(
            f"Bundle {source} records no weights memory: hpx set no placement rule for the "
            "constants, so heliaOptimus could not match the calibration to a plan.",
            hint=(
                "Profile with model.weights_location set, or without a user rule for the "
                "constant tensor kind."
            ),
        )
    return recorded


def _capture_dimensions(run: RunArtifacts) -> dict[str, Any]:
    """The capture setup a file must agree on, read as ``hpx compare`` reads it."""
    dimensions = read_dimensions(run)
    facts: dict[str, Any] = {
        dimension.value: dimensions.get(dimension) for dimension in CAPTURE_DIMENSIONS
    }
    # An unknown identity fact stays the ``{"unknown": reason}`` record the
    # summary writes; two unknown commits compare equal by their reason.
    kernels = nested_get(run.summary, "identity", "kernels")
    facts[KERNEL_VERSION] = nested_get(kernels, "version")
    facts[KERNEL_COMMIT] = nested_get(kernels, "commit")
    plan = run.summary.get("memory_plan")
    regions = plan.get("regions") if isinstance(plan, dict) else None
    arena = sorted(
        {
            str(region.get("region"))
            for region in (regions if isinstance(regions, list) else ())
            if isinstance(region, dict)
            and any(
                isinstance(consumer, dict) and consumer.get("kind") == "arena"
                for consumer in region.get("consumers") or ()
            )
        }
    )
    facts[ARENA_MEMORY] = ",".join(arena) if arena else None
    return facts


def _model_sha256(run: RunArtifacts, source: str) -> str:
    sha256 = nested_get(run.summary, "identity", "model_sha256")
    if not isinstance(sha256, str) or not sha256:
        sha256 = nested_get(run.metadata, "model", "sha256")
    if not isinstance(sha256, str) or not sha256:
        raise CalibrationError(f"Bundle {source} records no model sha256.")
    return sha256


def _plan(run: RunArtifacts, source: str) -> dict[str, Any]:
    if run.manifest is None:
        raise CalibrationError(f"Bundle {source} has no result manifest.")
    candidates = [
        artifact.path
        for artifact in run.manifest.artifacts
        if artifact.name == _PLAN_ARTIFACT or artifact.path.endswith(_PLAN_SUFFIX)
    ]
    if len(candidates) != 1:
        raise CalibrationError(
            f"Bundle {source} pins {'no' if not candidates else len(candidates)} heliaAOT plan"
            f"{'' if len(candidates) < 2 else 's'}.",
            hint=(
                "hpx calibrate reads the <prefix>_plan.json a helia-aot run pins into its "
                "bundle; profile with --engine helia-aot on this release."
            ),
        )
    path = run.path / candidates[0]
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalibrationError(f"Bundle {source}: could not read {path.name}: {exc}") from exc
    if not isinstance(plan, dict):
        raise CalibrationError(f"Bundle {source}: {path.name} is not a JSON object.")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise CalibrationError(
            f"Bundle {source}: {path.name} is plan schema version "
            f"{plan.get('schema_version')!r}; this release reads version {PLAN_SCHEMA_VERSION}.",
        )
    return plan


def _layers(run: RunArtifacts, source: str) -> dict[int, tuple[float, str]]:
    """Execution position -> (cycles, op) for every measured layer."""
    layers: dict[int, tuple[float, str]] = {}
    for row in run.layers:
        position = row.get("id")
        op = str(row.get("op", ""))
        if isinstance(position, bool) or not isinstance(position, int):
            raise CalibrationError(
                f"Bundle {source}: layer {position!r} ({op}) is not labelled with an "
                "execution position.",
            )
        if position in layers:
            raise CalibrationError(f"Bundle {source}: position {position} is measured twice.")
        if row.get("overflow"):
            raise CalibrationError(
                f"Bundle {source}: layer {position} ({op}) overflowed its counter, so its "
                "cycles are not a measurement.",
                hint="Profile again with fewer iterations or a narrower counter set.",
            )
        cycles = row.get("cycles")
        if not _is_cycle_count(cycles):
            raise CalibrationError(
                f"Bundle {source}: layer {position} ({op}) has no finite, non-negative cycles."
            )
        layers[position] = (float(cycles), op)
    if not layers:
        raise CalibrationError(f"Bundle {source} measured no layers.")
    return layers


def _is_cycle_count(value: Any) -> TypeGuard[int | float]:
    """A finite, non-negative number a float can hold."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except OverflowError:
        return False
    return math.isfinite(number) and number >= 0


def _firmware_label(op: str) -> str:
    """The label the firmware gives an operator type, for the plan and the capture alike.

    The capture's label may carry a ``:<index>`` suffix; the plan's op_type is
    a lowercase key. Both reduce to upper case with every character outside
    ``A-Z``, ``0-9`` and ``_`` replaced by ``_``.
    """
    return re.sub(r"[^A-Z0-9_]", "_", _LAYER_SUFFIX.sub("", op).upper())


def _plan_operator(entry: Any, source: str) -> tuple[OperatorSample, int, str]:
    """One plan operator as a sample without cycles, with its position and op_type.

    Every knob value is priced, an approximate one included. The facts are
    the fixed choices (``fixed:<name>``) and the kernel entry
    (``kernel:entry``), recorded with the price and never compared.
    """
    operator = entry.get("id") if isinstance(entry, dict) else None
    position = entry.get("index") if isinstance(entry, dict) else None
    op_type = entry.get("op_type") if isinstance(entry, dict) else None
    knobs = entry.get("knobs", {}) if isinstance(entry, dict) else None
    if (
        not isinstance(operator, str)
        or isinstance(position, bool)
        or not isinstance(position, int)
        or not isinstance(op_type, str)
        or not isinstance(knobs, dict)
    ):
        raise CalibrationError(
            f"Bundle {source}: a plan operator lacks id, index, op_type or knobs."
        )
    values: dict[str, str] = {}
    for knob, choice in knobs.items():
        value = choice.get("value") if isinstance(choice, dict) else None
        if not isinstance(value, str):
            raise CalibrationError(
                f"Bundle {source}: plan operator {operator} knob {knob} has no value."
            )
        values[str(knob)] = value
    facts: dict[str, str] = {}
    fixed = entry.get("fixed")
    if fixed is None:
        fixed = {}
    if not isinstance(fixed, dict):
        raise CalibrationError(f"Bundle {source}: plan operator {operator} fixed is not an object.")
    for name, choice in fixed.items():
        value = choice.get("value") if isinstance(choice, dict) else None
        if not isinstance(value, str):
            raise CalibrationError(
                f"Bundle {source}: plan operator {operator} fixed choice {name} has no value."
            )
        facts[f"{_FIXED_PREFIX}{name}"] = value
    kernel = entry.get("kernel")
    if isinstance(kernel, dict) and isinstance(kernel.get("entry"), str):
        facts[_KERNEL_ENTRY] = kernel["entry"]
    return OperatorSample(operator, values, 0.0, facts), position, op_type
