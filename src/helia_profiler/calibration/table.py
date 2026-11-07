"""Build a heliaOptimus calibration file from heliaAOT result bundles (#648).

A bundle's ``summary.json`` records the conditions it measured under and the
model it profiled; its per-layer table records each execution position's
cycles; heliaAOT's pinned ``<prefix>_plan.json`` names the operator at each
position and the knob values the compiler chose for it. Joining the three
prices every (operator, knob, value) the run exercised::

    operators[<plan operator id>][<knob>][<value>] = {"cycles": <cycles>}

Everything that would make a price mean something else is an error, never a
silent skip: another model, other conditions, a run whose cycles the
profiler flagged, a capture whose toolchain, engine, kernel library or arena
memory differs from another's, an overflowed counter, a position the plan
and the capture disagree on, an operator whose other knobs or fixed choices
did not hold still, or two sources that price one value differently. What a
knob determines (the kernel entry, the fixed choices it sets) is recorded
with the prices and never compared. The file is schema version 1 as
``helia_optimus.cost.calibration`` reads it; what hpx needs for a later
``--merge`` sits under ``capture``, a key heliaOptimus ignores.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

from ..errors import CalibrationError
from ..evaluation.comparability import read_dimensions
from ..evaluation.compare import RunArtifacts, load_run_artifacts
from ..results import ComparisonDimension, IssueCode, ResultValidity, Severity, error_metric_group
from ..results.models import Conditions
from ..results.serde import nested_get

#: The calibration file shape this release writes; heliaOptimus checks it.
CALIBRATION_SCHEMA_VERSION = 1
#: heliaAOT's plan schema this release reads.
PLAN_SCHEMA_VERSION = 1
#: Largest relative disagreement two prices of one value may have.
DEFAULT_TOLERANCE = 0.02

#: Facts of a capture that change cycles without being conditions. Every
#: bundle in a file, and the file itself under ``--merge``, must agree on them.
#: The comparison dimensions are read as ``hpx compare`` reads them; the
#: power family is left out because no price is a power figure.
CAPTURE_DIMENSIONS: tuple[ComparisonDimension, ...] = (
    ComparisonDimension.BOARD,
    ComparisonDimension.SOC,
    ComparisonDimension.CPU_CLOCK,
    ComparisonDimension.ENGINE,
    ComparisonDimension.ENGINE_VERSION,
    ComparisonDimension.ENGINE_BACKEND,
    ComparisonDimension.TOOLCHAIN,
    ComparisonDimension.COMPILER_VERSION,
    ComparisonDimension.LINK_FAMILY,
    ComparisonDimension.ARCHITECTURE_FLAGS,
    ComparisonDimension.ARENA_LOCATION,
)
#: The CORE (kernel library) identity and the planned arena memory, which are
#: not comparison dimensions.
_KERNEL_LIBRARY = "kernel_library"
_KERNEL_VERSION = "kernel_version"
_KERNEL_COMMIT = "kernel_commit"
_ARENA_MEMORY = "arena_memory"
#: Capture facts recorded for the reader but never compared: the kernel
#: commit moves with the library version a lock already names.
_RECORDED_ONLY: frozenset[str] = frozenset({_KERNEL_COMMIT})

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
#: The held and derived facts of an operator, as the file names them.
_FIXED_PREFIX = "fixed:"
_KERNEL_ENTRY = "kernel:entry"
#: A fixed choice the swept knob sets, which therefore moves with it.
_SET_BY_KNOB = "knob"


@dataclass(frozen=True)
class OperatorSample:
    """One measured operator: the whole knob vector it ran with, what else the
    plan held for it, what the knobs determined, and its cycles."""

    operator: str
    knobs: Mapping[str, str]
    cycles: float
    #: Fixed choices no knob sets, which must not move while a knob is credited.
    held: Mapping[str, str]
    #: The kernel entry and the fixed choices a knob sets: recorded, never compared.
    derived: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationSource:
    """What one bundle, or an existing file, contributes to a table."""

    source: str
    model_sha256: str
    subgraph: int
    #: The recorded block as written: heliaOptimus compares every field.
    conditions: Mapping[str, Any]
    #: The capture facts, by name; empty only for a file hpx did not write.
    dimensions: Mapping[str, Any]
    samples: tuple[OperatorSample, ...]
    #: A file's prices as it holds them; every one is kept through a merge.
    prices: Mapping[str, Mapping[str, Mapping[str, float]]] = field(default_factory=dict)
    #: A file's recorded derived facts, carried forward.
    derived: Mapping[str, Mapping[str, Mapping[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationTable:
    """The calibration file, typed: one model, one set of conditions."""

    model_sha256: str
    subgraph: int
    conditions: Mapping[str, Any]
    #: operator id -> knob -> value -> cycles.
    operators: Mapping[str, Mapping[str, Mapping[str, float]]]
    #: The capture facts the prices were measured with; empty for a file hpx did not write.
    dimensions: Mapping[str, Any]
    #: operator id -> the knobs that held still while its priced knob was swept.
    contexts: Mapping[str, Mapping[str, str]]
    #: operator id -> the fixed choices no knob sets, which the prices were measured with.
    held: Mapping[str, Mapping[str, str]]
    #: operator id -> knob vector -> the kernel entry and knob-set choices it produced.
    derived: Mapping[str, Mapping[str, Mapping[str, str]]] = field(default_factory=dict)

    @property
    def price_count(self) -> int:
        return sum(len(values) for knobs in self.operators.values() for values in knobs.values())

    def to_dict(self) -> dict[str, Any]:
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
            "capture": {
                "dimensions": dict(self.dimensions),
                "knobs": {operator: dict(knobs) for operator, knobs in self.contexts.items()},
                "held": {operator: dict(held) for operator, held in self.held.items()},
                "derived": {
                    operator: {vector: dict(facts) for vector, facts in sorted(entries.items())}
                    for operator, entries in self.derived.items()
                },
            },
        }

    def as_source(self, source: str) -> CalibrationSource:
        """The table as a source, so ``--merge`` holds it to the same rules.

        Raises:
            CalibrationError: when an operator prices several knobs at several
                values each, so the file cannot say which knob was swept, or
                prices the knobs of one vector differently.
        """
        samples: list[OperatorSample] = []
        for operator, knobs in self.operators.items():
            context = dict(self.contexts.get(operator, {}))
            held = dict(self.held.get(operator, {}))
            if len(knobs) == 1:
                (knob,) = knobs
                for value, cycles in knobs[knob].items():
                    samples.append(OperatorSample(operator, {**context, knob: value}, cycles, held))
            elif all(len(values) == 1 for values in knobs.values()):
                # No knob was swept: every knob was priced from the one vector,
                # so every knob carries that vector's cycles.
                vector = {**context, **{knob: next(iter(values)) for knob, values in knobs.items()}}
                prices = {next(iter(values.values())) for values in knobs.values()}
                if len(prices) != 1:
                    listed = ", ".join(f"{p:g}" for p in sorted(prices))
                    raise CalibrationError(
                        f"{source}: operator {operator} prices one vector of knobs "
                        f"{', '.join(sorted(knobs))} at different cycles ({listed}).",
                        hint="Write a new file from the bundles instead of merging into this one.",
                    )
                samples.append(OperatorSample(operator, vector, prices.pop(), held))
            else:
                raise CalibrationError(
                    f"{source}: operator {operator} prices knobs {', '.join(sorted(knobs))} at "
                    "several values each, so the file cannot say which knob was swept.",
                    hint="Write a new file from the bundles instead of merging into this one.",
                )
        return CalibrationSource(
            source=source,
            model_sha256=self.model_sha256,
            subgraph=self.subgraph,
            conditions=self.conditions,
            dimensions=self.dimensions,
            samples=tuple(samples),
            prices=self.operators,
            derived=self.derived,
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

    The temporary file is created with a plain open, so the umask applies as
    for any file; when *path* already exists its mode is copied onto the
    temporary before the rename, so the file keeps the mode its owner gave
    it. A path that is a symbolic link is refused rather than replaced.

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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
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
    typed: dict[str, dict[str, dict[str, float]]] = {}
    for operator, knobs in operators.items():
        if not isinstance(knobs, dict):
            raise CalibrationError(
                f"Calibration file {path}: operator {operator} is not an object."
            )
        typed[operator] = {}
        for knob, values in knobs.items():
            if not isinstance(values, dict):
                raise CalibrationError(
                    f"Calibration file {path}: operator {operator} knob {knob} is not an object."
                )
            typed[operator][knob] = {}
            for value, cost in values.items():
                cycles = cost.get("cycles") if isinstance(cost, dict) else None
                if not _is_cycle_count(cycles):
                    raise CalibrationError(
                        f"Calibration file {path}: operator {operator} {knob}={value} "
                        "has no finite, non-negative cycles."
                    )
                typed[operator][knob][value] = float(cycles)
    dimensions = nested_get(data, "capture", "dimensions")
    derived = nested_get(data, "capture", "derived")
    return CalibrationTable(
        model_sha256=sha256,
        subgraph=subgraph,
        conditions=conditions,
        operators=typed,
        dimensions=dimensions if isinstance(dimensions, dict) else {},
        contexts=_string_maps(nested_get(data, "capture", "knobs")),
        held=_string_maps(nested_get(data, "capture", "held")),
        derived={
            operator: _string_maps(entries)
            for operator, entries in (derived.items() if isinstance(derived, dict) else ())
        },
    )


def _string_maps(value: Any) -> dict[str, dict[str, str]]:
    """``{key: {name: str}}`` from a capture block, or empty when it is not that."""
    if not isinstance(value, dict) or not all(
        isinstance(entries, dict) and all(isinstance(v, str) for v in entries.values())
        for entries in value.values()
    ):
        return {}
    return {key: dict(entries) for key, entries in value.items()}


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
            samples.append(
                OperatorSample(sample.operator, sample.knobs, cycles, sample.held, sample.derived)
            )
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
    conditions, one capture setup, one swept knob per operator, and agreeing prices.

    Every source that records its capture setup must agree with every other
    that does; a file that records none (one hpx did not write) is merged into
    only when the bundles agree among themselves, and the table then records
    theirs. An operator's knob is priced only when every other knob of that
    operator and the fixed choices no knob sets held one value in every
    source; when no knob varied, every knob is priced. Every price a file
    already holds is kept; a later price of the same value must agree with it
    within *tolerance*, relative to the larger of the two.

    Raises:
        CalibrationError: on no source, another model, other conditions, a
            differing capture fact, two knobs of one operator varying, a knob
            set or held choice that differs, a disagreeing price, or no price
            at all.
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
        reference = recorded[0]
        for name in sorted((set(reference.dimensions) | set(other.dimensions)) - _RECORDED_ONLY):
            if reference.dimensions.get(name) != other.dimensions.get(name):
                raise CalibrationError(
                    f"{other.source} has {name} {other.dimensions.get(name)!r}; "
                    f"{reference.source} has {reference.dimensions.get(name)!r}, so their "
                    "cycles are not one measurement.",
                    hint="Calibrate captures from one setup; write another file for the other.",
                )
    samples: dict[str, list[tuple[OperatorSample, str]]] = {}
    for source in sources:
        for sample in source.samples:
            samples.setdefault(sample.operator, []).append((sample, source.source))
    swept: dict[str, str | None] = {}
    contexts: dict[str, dict[str, str]] = {}
    held: dict[str, dict[str, str]] = {}
    for operator, seen in samples.items():
        (reference, origin) = seen[0]
        for sample, source in seen[1:]:
            if set(sample.knobs) != set(reference.knobs):
                raise CalibrationError(
                    f"Operator {operator} has knobs {sorted(sample.knobs)} in {source} but "
                    f"{sorted(reference.knobs)} in {origin}.",
                    hint="Calibrate captures compiled by one heliaAOT release.",
                )
            moved = sorted(
                name
                for name in set(sample.held) | set(reference.held)
                if sample.held.get(name) != reference.held.get(name)
            )
            if moved:
                raise CalibrationError(
                    f"Operator {operator} ran with different {', '.join(moved)} in {source} "
                    f"({', '.join(repr(sample.held.get(n)) for n in moved)}) and {origin} "
                    f"({', '.join(repr(reference.held.get(n)) for n in moved)}), so no "
                    "cycles can be credited to a knob.",
                    hint="Hold the plan's fixed choices still while sweeping a knob.",
                )
        knobs = sorted(reference.knobs)
        varying = [knob for knob in knobs if len({sample.knobs[knob] for sample, _ in seen}) > 1]
        if len(varying) > 1:
            raise CalibrationError(
                f"Operator {operator} ran with different values of knobs "
                f"{', '.join(varying)} across the sources, so no cycles can be credited to "
                "one knob.",
                hint="Sweep one knob at a time; keep the others at one value.",
            )
        swept[operator] = varying[0] if varying else None
        # When nothing varied every knob is priced, so no knob is context.
        contexts[operator] = (
            {knob: reference.knobs[knob] for knob in knobs if knob != varying[0]} if varying else {}
        )
        held[operator] = dict(reference.held)
    kept: dict[tuple[str, str, str], tuple[float, str]] = {}
    operators: dict[str, dict[str, dict[str, float]]] = {}
    derived: dict[str, dict[str, dict[str, str]]] = {}
    # A file's prices come first and whole, so a merge never drops or replaces one.
    for source in sources:
        for operator, knobs in source.prices.items():
            for knob, values in knobs.items():
                for value, cycles in values.items():
                    kept.setdefault((operator, knob, value), (cycles, source.source))
                    operators.setdefault(operator, {}).setdefault(knob, {}).setdefault(
                        value, cycles
                    )
        for operator, entries in source.derived.items():
            derived.setdefault(operator, {}).update(
                {vector: dict(facts) for vector, facts in entries.items()}
            )
    for source in sources:
        for sample in source.samples:
            if sample.derived:
                vector = ",".join(f"{k}={sample.knobs[k]}" for k in sorted(sample.knobs))
                derived.setdefault(sample.operator, {})[vector] = dict(sample.derived)
            knob = swept[sample.operator]
            priced = [knob] if knob is not None else list(sample.knobs)
            for name in priced:
                value = sample.knobs[name]
                earlier = kept.get((sample.operator, name, value))
                if earlier is None:
                    kept[(sample.operator, name, value)] = (sample.cycles, source.source)
                    operators.setdefault(sample.operator, {}).setdefault(name, {})[value] = (
                        sample.cycles
                    )
                    continue
                cycles, origin = earlier
                scale = max(cycles, sample.cycles)
                if abs(cycles - sample.cycles) > tolerance * scale:
                    apart = abs(cycles - sample.cycles) / scale * 100
                    raise CalibrationError(
                        f"{sample.operator} {name}={value} costs {cycles:g} cycles in {origin} "
                        f"but {sample.cycles:g} in {source.source} ({apart:.1f}% apart, "
                        f"tolerance {tolerance * 100:g}%).",
                        hint="Raise --tolerance, or leave out the bundle that disagrees.",
                    )
    if not kept:
        raise CalibrationError(
            "No operator in the bundles has a knob, so there is nothing to price.",
            hint="Only operators heliaAOT's plan lists with knobs are calibrated.",
        )
    return CalibrationTable(
        model_sha256=first.model_sha256,
        subgraph=first.subgraph,
        conditions=first.conditions,
        operators=operators,
        dimensions=recorded[0].dimensions if recorded else {},
        contexts={operator: context for operator, context in contexts.items() if context},
        held={operator: entries for operator, entries in held.items() if entries},
        derived=derived,
    )


def _require_usable_cycles(run: RunArtifacts, source: str) -> None:
    """Refuse a run whose cycle counts the profiler flagged.

    The verdict follows the issue registry as ``hpx compare`` applies it: an
    invalid run is priced only when every error is one the registry confines
    to a metric family, which no price belongs to; an unrecognized code
    confines nothing, and an invalid run with no error to explain it is
    refused. A run carrying an issue after which the cycle counter is not
    trusted is refused whatever its validity.
    """
    if run.manifest is None:
        raise CalibrationError(f"Bundle {source} has no result manifest.")
    manifest = run.manifest
    flagged = sorted({issue.code for issue in manifest.issues if issue.code in CYCLES_UNUSABLE})
    errors = sorted({issue.code for issue in manifest.issues if issue.severity == Severity.ERROR})
    unconfined = [code for code in errors if error_metric_group(code) is None]
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
    """The capture facts a file must agree on, read as ``hpx compare`` reads them."""
    dimensions = read_dimensions(run)
    facts: dict[str, Any] = {
        dimension.value: dimensions.get(dimension) for dimension in CAPTURE_DIMENSIONS
    }
    kernels = nested_get(run.summary, "identity", "kernels")
    facts[_KERNEL_LIBRARY] = _plain(nested_get(kernels, "library"))
    facts[_KERNEL_VERSION] = _plain(nested_get(kernels, "version"))
    facts[_KERNEL_COMMIT] = _plain(nested_get(kernels, "commit"))
    plan = run.summary.get("memory_plan")
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if isinstance(regions, list):
        arena = sorted(
            {
                str(region.get("region"))
                for region in regions
                if isinstance(region, dict)
                and any(
                    isinstance(consumer, dict) and consumer.get("kind") == "arena"
                    for consumer in region.get("consumers") or ()
                )
            }
        )
        facts[_ARENA_MEMORY] = ",".join(arena) if arena else None
    else:
        facts[_ARENA_MEMORY] = None
    return facts


def _plain(value: Any) -> Any:
    """An identity fact as a comparable scalar; an unknown fact compares by its reason."""
    if isinstance(value, dict) and set(value) == {"unknown"}:
        return f"unknown: {value['unknown']}"
    return value


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

    Every knob value is priced, an approximate one included. The held facts
    are the fixed choices no knob sets (``fixed:<name>``); the derived facts
    are the kernel entry (``kernel:entry``) and the fixed choices a knob sets,
    which move with the knob and are recorded, never compared.
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
    held: dict[str, str] = {}
    derived: dict[str, str] = {}
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
        target = derived if choice.get("set_by") == _SET_BY_KNOB else held
        target[f"{_FIXED_PREFIX}{name}"] = value
    kernel = entry.get("kernel")
    if isinstance(kernel, dict) and isinstance(kernel.get("entry"), str):
        derived[_KERNEL_ENTRY] = kernel["entry"]
    return OperatorSample(operator, values, 0.0, held, derived), position, op_type
