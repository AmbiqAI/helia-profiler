"""Build a heliaOptimus calibration file from heliaAOT result bundles (#648).

A bundle's ``summary.json`` records the conditions it measured under and the
model it profiled; its per-layer table records each execution position's
cycles; heliaAOT's pinned ``<prefix>_plan.json`` names the operator at each
position, the knob values the compiler chose for it, its fixed choices and
its kernel entry. Joining the three times every operator the plan lists,
knobbed or not, under the whole knob vector the capture compiled it with::

    operator_cycles[<plan operator id>] = [
        {"knobs": {<knob>: <value>}, "cycles": <cycles>, "facts": {<fact>: <value>}}, ...
    ]

This is calibration schema 2, whose exported schema is
``helia_profiler/data/calibration.schema.v2.json``, and these samples are the
only thing the file stores: a reader derives whatever price it needs from
them. Each operator has one entry per distinct vector, its list in the order
of the canonical JSON of its ``knobs``; an operator without knobs has one
entry whose ``knobs`` is empty. Operators keep the order they were first
seen in: the existing file first under ``--merge``, then the bundles, each in
plan order. A sample is keyed by the operator and its knob vector, so any set
of vectors merges: one knob at a time from a common baseline, several at
once, or every combination.

The facts are the plan's fixed choices (``fixed:<name>``) and its kernel
entry (``kernel:entry``). With the capture setup held equal they follow from
the knob values, so they are not part of the key, but two samples of one key
must report the same ones. Two samples of one key must also agree within the
tolerance of the larger value or within 8 cycles, whichever is looser, since
a small operator moves by a few cycles of layout jitter; the first source's
sample is the one kept.

The setup is one digest of the run's frozen settings as the bundle records
them: the frozen config less the heliaAOT knob values (``optimization.<knob>``
and ``operators[].optimization.<knob>`` for every knob heliaAOT registers),
the power, output and timeout sections, the verbosity and the run-only
fields (probe serials, ports, paths); the toolchain and engine records the
run resolved, less the cmake version; the configuration the heliaAOT
compiler received (``engine.effective_config_sha256``); the build's ISA
flags; and the kernel build (its commit, else its content hash). The hpx
version and the contents of locally overridden modules are not held. Power
sync adds code to the image, which a power-measured and a non-power capture
are allowed to differ in by this design. The file records the digest, the
fields it covers and the heliaAOT release the captures compiled with, whose
knob registry the stripping depends on; captures compiled by different
releases do not merge. Reading a bundle needs heliaAOT installed.

Beside the digest the file records a pairing digest, ``pairing_digest`` with
its ``pairing_fields``: the same basis less where the constants sit, so
captures that differ only in where the weights sit share it while their
setups differ. It leaves out ``config.model.weights_location``, every
``memory.tensors[]`` rule of the constants that sets nothing but their
placement (and those attributes of one that sets more), and holds
``engine.effective_config_pairing_sha256``, the compiler's configuration less
the same rules, in place of ``engine.effective_config_sha256``. The arena,
the PSRAM clock, the pool constraints and ``memory.allocate_arenas`` stay
held. hpx accepts PSRAM weights only with ``memory.allocate_arenas: false``,
so a pair that includes a PSRAM capture shares the digest only when every
capture of the pair runs with ``allocate_arenas: false``; a default TCM or
MRAM capture does not pair with a PSRAM one. A bundle that does not record
the pairing configuration, and a merge whose sources do not all share one
pairing digest, write ``null`` for both.

Everything that would make a sample mean something else is an error, never
a silent skip: another model, other conditions, another setup, a run whose
cycles the profiler flagged, an overflowed counter, a position the plan and
the capture disagree on, or two samples of one key whose cycles or facts
disagree. What hpx needs for a later ``--merge`` sits under ``capture``, and
only a schema 2 file that carries it can be merged into: a schema 1 file is
refused, to be calibrated again from its bundles, and a key schema 2 does not
define is refused rather than dropped when the file is written again.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeGuard

from ..errors import CalibrationError
from ..evaluation.compare import RunArtifacts, load_run_artifacts
from ..results import (
    METRIC_BLOCKING_CODE_BY_GROUP,
    IssueCode,
    ResultValidity,
    Severity,
    error_metric_group,
)
from ..results.models import Conditions
from ..results.serde import nested_get
from .setup import Setup, _canonical, _require_same_setup, _setup

#: The calibration file shape this release writes, and the only one it reads back.
CALIBRATION_SCHEMA_VERSION = 2
#: heliaAOT's plan schema this release reads.
PLAN_SCHEMA_VERSION = 1
#: Largest relative disagreement two samples of one key may have.
DEFAULT_TOLERANCE = 0.02

#: Cycles two samples of one key may differ by whatever the tolerance.
_CYCLES_FLOOR = 8.0
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
_MERGE_HINT = (
    "hpx calibrate merges only into a file it wrote; heliaOptimus reads a hand-written "
    "table directly."
)
_UNDEFINED_HINT = (
    "A schema 2 file holds only the fields helia_profiler/data/calibration.schema.v2.json "
    "names, so this one was edited or written by another tool; calibrate the result bundles "
    "again without --merge."
)
#: The keys calibration schema 2 defines, by where they sit.
_TOP_KEYS = frozenset({"schema_version", "model", "conditions", "operator_cycles", "capture"})
_MODEL_KEYS = frozenset({"sha256", "subgraph"})
_CAPTURE_KEYS = frozenset({"setup"})
_SETUP_KEYS = frozenset(
    {"digest", "fields", "pairing_digest", "pairing_fields", "helia_aot_version"}
)
_ENTRY_KEYS = frozenset({"knobs", "cycles", "facts"})
_FACT_NAME = re.compile(r"fixed:.+|kernel:entry")


#: A knob vector as a key: its (knob, value) pairs, sorted.
KnobVector = tuple[tuple[str, str], ...]


def _vector(knobs: Mapping[str, str]) -> KnobVector:
    return tuple(sorted(knobs.items()))


@dataclass(frozen=True)
class OperatorSample:
    """One measured operator: the whole knob vector it ran with, its cycles, and
    what the plan reported for it under that vector."""

    operator: str
    knobs: Mapping[str, str]
    cycles: float
    #: The fixed choices and the kernel entry: held equal on merge, not part of the key.
    facts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationSource:
    """What one bundle, or an existing file, contributes to a table."""

    source: str
    model_sha256: str
    subgraph: int
    #: The recorded block as written: heliaOptimus compares every field.
    conditions: Mapping[str, Any]
    setup: Setup
    samples: tuple[OperatorSample, ...] = ()


@dataclass(frozen=True)
class CalibrationTable:
    """The calibration file, typed: one model, one set of conditions, one setup."""

    model_sha256: str
    subgraph: int
    conditions: Mapping[str, Any]
    setup: Setup
    #: (operator id, knob vector) -> the sample, operators in first-seen order.
    samples: Mapping[tuple[str, KnobVector], OperatorSample]

    @property
    def operator_count(self) -> int:
        """The number of distinct operators timed."""
        return len({operator for operator, _ in self.samples})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "model": {"sha256": self.model_sha256, "subgraph": self.subgraph},
            "conditions": dict(self.conditions),
            "operator_cycles": self._operator_cycles_dict(),
            "capture": {"setup": self.setup.to_dict()},
        }

    def _operator_cycles_dict(self) -> dict[str, list[dict[str, Any]]]:
        """operator id -> one entry per vector, in the order of its knobs' canonical JSON."""
        timed: dict[str, list[OperatorSample]] = {}
        for (operator, _), sample in self.samples.items():
            timed.setdefault(operator, []).append(sample)
        return {
            operator: [
                {
                    "knobs": dict(sorted(sample.knobs.items())),
                    "cycles": float(sample.cycles),
                    "facts": dict(sorted(sample.facts.items())),
                }
                for sample in sorted(entries, key=lambda sample: _canonical(dict(sample.knobs)))
            ]
            for operator, entries in timed.items()
        }

    def as_source(self, source: str) -> CalibrationSource:
        """The table as a source, so ``--merge`` holds it to the same rules."""
        return CalibrationSource(
            source=source,
            model_sha256=self.model_sha256,
            subgraph=self.subgraph,
            conditions=self.conditions,
            setup=self.setup,
            samples=tuple(self.samples.values()),
        )


def calibrate(
    bundles: Sequence[Path],
    *,
    output: Path,
    merge: bool = False,
    tolerance: float = DEFAULT_TOLERANCE,
) -> CalibrationTable:
    """Time every operator the bundles' plans list and write *output*.

    With ``merge``, the samples already in *output*, a schema 2 file hpx
    wrote, take part under the same rules as a bundle's, and every one of
    them is kept.

    Raises:
        CalibrationError: when a bundle, a sample or the existing file breaks a rule.
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
    """Read a calibration schema 2 file hpx wrote.

    hpx writes the file it reads again, so a key schema 2 does not define is
    refused rather than dropped.

    Raises:
        CalibrationError: when the file is not JSON, not calibration schema 2,
            holds a key schema 2 does not define, is malformed, or carries no
            well-formed ``capture`` block (one hpx did not write, or one that
            was edited).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalibrationError(f"Could not read calibration file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationError(f"Calibration file {path} is not a JSON object.")
    version = data.get("schema_version")
    if type(version) is int and version == 1:
        raise CalibrationError(
            f"Calibration file {path} is calibration schema 1, which this release does not "
            "read: schema 2 stores only operator_cycles.",
            hint=(
                "Write it again from the result bundles it came from: hpx calibrate <bundles> "
                f"--output {path}, without --merge."
            ),
        )
    if type(version) is int and version > CALIBRATION_SCHEMA_VERSION:
        raise CalibrationError(
            f"Calibration file {path} is calibration schema {version}, newer than the version "
            f"{CALIBRATION_SCHEMA_VERSION} this release reads.",
            hint=(
                "Merge with the helia-profiler release that wrote it, or calibrate the bundles "
                "again without --merge."
            ),
        )
    if type(version) is not int or version != CALIBRATION_SCHEMA_VERSION:
        raise CalibrationError(
            f"Calibration file {path} has schema_version {version!r}; this release reads the "
            f"integer {CALIBRATION_SCHEMA_VERSION}.",
            hint=_UNDEFINED_HINT,
        )
    where = _undefined(data)
    if where is not None:
        raise CalibrationError(
            f"Calibration file {path} holds {where}, which calibration schema 2 does not define.",
            hint=_UNDEFINED_HINT,
        )
    sha256 = nested_get(data, "model", "sha256")
    subgraph = nested_get(data, "model", "subgraph")
    conditions = data.get("conditions")
    if (
        not isinstance(sha256, str)
        or not sha256
        or type(subgraph) is not int
        or not isinstance(conditions, dict)
        or "operator_cycles" not in data
    ):
        raise CalibrationError(
            f"Calibration file {path} is malformed: it needs model.sha256, model.subgraph, "
            "conditions and operator_cycles.",
        )
    digest = nested_get(data, "capture", "setup", "digest")
    fields = nested_get(data, "capture", "setup", "fields")
    helia_aot_version = nested_get(data, "capture", "setup", "helia_aot_version")
    pairing_digest = nested_get(data, "capture", "setup", "pairing_digest")
    pairing_fields = nested_get(data, "capture", "setup", "pairing_fields")
    if (
        not _is_digest(digest)
        or not _is_names(fields)
        or not (helia_aot_version is None or isinstance(helia_aot_version, str))
        or not (
            (pairing_digest is None and pairing_fields is None)
            or (_is_digest(pairing_digest) and _is_names(pairing_fields))
        )
    ):
        raise CalibrationError(
            f"Calibration file {path} records no setup under capture.setup.",
            hint=_MERGE_HINT,
        )
    return CalibrationTable(
        model_sha256=sha256,
        subgraph=subgraph,
        conditions=conditions,
        setup=Setup(
            digest,
            tuple(fields),
            helia_aot_version,
            pairing_digest=pairing_digest,
            pairing_fields=None if pairing_fields is None else tuple(pairing_fields),
        ),
        samples=_read_operator_cycles(path, data["operator_cycles"]),
    )


def _undefined(data: Mapping[str, Any]) -> str | None:
    """The first key calibration schema 2 does not define, as its path in the file."""
    blocks = (
        ("", data, _TOP_KEYS),
        ("model.", data.get("model"), _MODEL_KEYS),
        ("capture.", data.get("capture"), _CAPTURE_KEYS),
        ("capture.setup.", nested_get(data, "capture", "setup"), _SETUP_KEYS),
    )
    for prefix, block, known in blocks:
        if isinstance(block, dict):
            for key in block:
                if key not in known:
                    return f"{prefix}{key}"
    timed = data.get("operator_cycles")
    if not isinstance(timed, dict):
        return None
    for operator, entries in timed.items():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                for key in entry:
                    if key not in _ENTRY_KEYS:
                        return f"operator_cycles[{operator}][{index}].{key}"
    return None


def _read_operator_cycles(
    path: Path, recorded: Any
) -> dict[tuple[str, KnobVector], OperatorSample]:
    """A file's ``operator_cycles``, by (operator, knob vector), in the file's order."""
    if not isinstance(recorded, dict):
        raise CalibrationError(f"Calibration file {path}: operator_cycles is not an object.")
    samples: dict[tuple[str, KnobVector], OperatorSample] = {}
    for operator, entries in recorded.items():
        if not isinstance(entries, list):
            raise CalibrationError(
                f"Calibration file {path}: operator_cycles {operator} is not a list."
            )
        if not entries:
            raise CalibrationError(
                f"Calibration file {path}: operator_cycles {operator} lists no entry."
            )
        for entry in entries:
            knobs = _string_map(entry.get("knobs")) if isinstance(entry, dict) else None
            cycles = entry.get("cycles") if isinstance(entry, dict) else None
            facts = _facts(entry.get("facts")) if isinstance(entry, dict) else None
            if knobs is None or facts is None or not _is_cycle_count(cycles):
                raise CalibrationError(
                    f"Calibration file {path}: operator_cycles {operator} has an entry without "
                    "string knobs, finite non-negative cycles and string facts named "
                    "fixed:<name> or kernel:entry."
                )
            key = (operator, _vector(knobs))
            if key in samples:
                raise CalibrationError(
                    f"Calibration file {path}: operator_cycles {operator} lists the knobs "
                    f"{dict(key[1])} twice."
                )
            samples[key] = OperatorSample(operator, knobs, float(cycles), facts)
    return samples


def _is_digest(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_names(value: Any) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


def _string_map(value: Any) -> dict[str, str] | None:
    """``{name: str}`` from a capture entry, or ``None`` when it is not that."""
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        return None
    return dict(value)


def _facts(value: Any) -> dict[str, str] | None:
    """An entry's facts, or ``None`` when one is not a string named as schema 2 names facts."""
    facts = _string_map(value)
    if facts is None or not all(_FACT_NAME.fullmatch(name) for name in facts):
        return None
    return facts


def read_bundle(path: Path) -> CalibrationSource:
    """Join one result bundle's capture to its pinned heliaAOT plan.

    Raises:
        CalibrationError: when the profiler flagged the run's cycles, the
            bundle records no conditions, no usable weights memory or no
            settings, has no plan or a plan for another model, a layer
            overflowed, or the plan and the capture disagree on a position
            or an operator type.
        ReportError: when the bundle is not a verified, current result bundle.
    """
    run = load_run_artifacts(path)
    source = str(run.path)
    _require_usable_cycles(run, source)
    conditions = _conditions(run, source)
    model_sha256 = _model_sha256(run, source)
    setup = _setup(run, source)
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
        setup=setup,
        samples=tuple(samples),
    )


def merge_sources(
    sources: Sequence[CalibrationSource], *, tolerance: float = DEFAULT_TOLERANCE
) -> CalibrationTable:
    """One table from every source, holding them to one model, one set of
    conditions, one setup, and agreeing samples.

    Every source is compared with the first: the setup digests must be
    equal, which needs every covered field present on both sides with the
    same value. A sample is keyed by its operator and its whole knob vector,
    so any set of vectors merges. The first sample of a key keeps its cycles,
    facts and source, and a file's sample is never replaced; a repeat must
    agree within *tolerance* of the larger value or within 8 cycles,
    whichever is looser, and must report the same facts. The pairing digest
    is written only when every source carries the same one.

    Raises:
        CalibrationError: on no source, another model, other conditions,
            another setup, a repeat whose cycles or facts disagree, or no
            operator timed at all.
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
        _require_same_setup(first, other)
    samples: dict[tuple[str, KnobVector], OperatorSample] = {}
    origins: dict[tuple[str, KnobVector], str] = {}
    for source in sources:
        for sample in source.samples:
            key = (sample.operator, _vector(sample.knobs))
            held = samples.get(key)
            if held is None:
                samples[key] = OperatorSample(
                    sample.operator, dict(sample.knobs), sample.cycles, dict(sample.facts)
                )
                origins[key] = source.source
                continue
            _require_agreement(held, sample, origins[key], source.source, tolerance)
    if not samples:
        raise CalibrationError(
            "No operator in the bundles was timed, so there is nothing to calibrate.",
            hint="hpx calibrate reads the cycles of every operator heliaAOT's plan lists.",
        )
    pairing = {source.setup.pairing_digest for source in sources}
    setup = (
        first.setup
        if len(pairing) == 1 and None not in pairing
        else replace(first.setup, pairing_digest=None, pairing_fields=None)
    )
    return CalibrationTable(
        model_sha256=first.model_sha256,
        subgraph=first.subgraph,
        conditions=first.conditions,
        setup=setup,
        samples=samples,
    )


def _require_agreement(
    held: OperatorSample, sample: OperatorSample, origin: str, source: str, tolerance: float
) -> None:
    """Refuse a repeat of a held sample whose cycles or facts disagree with it."""
    vector = dict(_vector(sample.knobs))
    scale = max(held.cycles, sample.cycles)
    if abs(held.cycles - sample.cycles) > max(tolerance * scale, _CYCLES_FLOOR):
        apart = abs(held.cycles - sample.cycles) / scale * 100
        raise CalibrationError(
            f"{sample.operator} with the knobs at {vector} takes {held.cycles:g} cycles in "
            f"{origin} but {sample.cycles:g} in {source} ({apart:.1f}% apart, "
            f"tolerance {tolerance * 100:g}% or {_CYCLES_FLOOR:g} cycles).",
            hint="Raise --tolerance, or leave out the bundle that disagrees.",
        )
    if dict(held.facts) != dict(sample.facts):
        raise CalibrationError(
            f"{sample.operator} with the knobs at {vector} reports "
            f"{dict(sorted(sample.facts.items()))} in {source} but "
            f"{dict(sorted(held.facts.items()))} in {origin}, so something other than the "
            "knobs changed between the captures.",
            hint="Calibrate captures from one build; write another file for the other.",
        )


def _require_usable_cycles(run: RunArtifacts, source: str) -> None:
    """Refuse a run whose cycle counts the profiler flagged.

    The verdict follows the issue registry as ``hpx compare`` applies it: an
    invalid run is calibrated only when every error is one the registry confines
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

    Every knob value is timed, an approximate one included. The facts are
    the fixed choices (``fixed:<name>``) and the kernel entry
    (``kernel:entry``), held equal on merge.
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
