"""Runtime records and the runtime × target qualification lookup.

Each runtime version heliaPROFILER builds is one small JSON record under
``data/runtimes/<name>/<version>.json``: its source, the precisions it
supports and the targets it is qualified on. The records are the only place
these facts live; nothing keeps a combined index.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources.abc import Traversable
from typing import Any

from .errors import ConfigError
from .platform import get_soc_for_board

RUNTIME_SCHEMA = "helia-profiler/runtime@1"
#: The precision vocabulary shared with helia-model-zoo and helia-benchmark.
PRECISIONS = ("fp32", "fp16", "a8w8", "a16w8", "a8w4")
_FIELDS = frozenset({"schema", "name", "version", "default", "source", "precisions", "qualified"})
_QUALIFIED_FIELDS = frozenset({"board", "clock", "precisions", "basis", "trace"})
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SUPPORTED = "supported"


class RuntimeQualification(StrEnum):
    """What heliaPROFILER says about one runtime version, precision and target."""

    QUALIFIED = "qualified"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RuntimeSource:
    """The repository and commit a runtime version is built from."""

    repo: str
    commit: str


@dataclass(frozen=True)
class QualifiedTarget:
    """A board class and clock the runtime is qualified on, and why."""

    board: str
    clock: str
    precisions: tuple[str, ...]
    basis: str
    trace: str


@dataclass(frozen=True)
class RuntimeRecord:
    """One runtime version: its source, supported precisions and qualified targets."""

    name: str
    version: str
    default: bool
    source: RuntimeSource
    #: Declared precisions: ``None`` when supported, else the reason it is not.
    precisions: Mapping[str, str | None]
    qualified: tuple[QualifiedTarget, ...]


@dataclass(frozen=True)
class Qualification:
    """The answer for one runtime version, precision and target, with the record it came from."""

    state: RuntimeQualification
    reason: str | None
    record: RuntimeRecord | None


@functools.cache
def runtimes() -> tuple[RuntimeRecord, ...]:
    """Every runtime record shipped with this heliaPROFILER, by name then version."""
    return load_runtime_records(
        importlib.resources.files("helia_profiler.data").joinpath("runtimes")
    )


def load_runtime_records(root: Traversable) -> tuple[RuntimeRecord, ...]:
    """Load and check every ``<name>/<version>.json`` record under ``root``."""
    records = []
    directories = [entry for entry in root.iterdir() if entry.is_dir()]
    for directory in sorted(directories, key=lambda entry: entry.name):
        files = [entry for entry in directory.iterdir() if entry.name.endswith(".json")]
        for entry in sorted(files, key=lambda entry: entry.name):
            location = f"runtimes/{directory.name}/{entry.name}"
            try:
                raw = json.loads(entry.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
            except (OSError, ValueError) as exc:
                raise ConfigError(f"Cannot read runtime record {location}: {exc}") from exc
            record = _parse_record(raw, location)
            if f"{record.name}/{record.version}.json" != f"{directory.name}/{entry.name}":
                raise ConfigError(f"Runtime record {location} names {record.name} {record.version}")
            records.append(record)
    for name in sorted({record.name for record in records}):
        defaults = [record for record in records if record.name == name and record.default]
        if len(defaults) != 1:
            raise ConfigError(
                f"Runtime {name} needs exactly one default record, not {len(defaults)}"
            )
    return tuple(records)


def runtime(name: str, version: str | None = None) -> RuntimeRecord | None:
    """The record for ``name`` at ``version``, or its default record when ``version`` is None."""
    return next(
        (
            record
            for record in runtimes()
            if record.name == name
            and (record.version == version if version is not None else record.default)
        ),
        None,
    )


def qualification(
    name: str, version: str | None = None, *, board: str, clock: str, precision: str
) -> Qualification:
    """Whether ``name`` at ``version`` is qualified for ``precision`` on ``board`` at ``clock``.

    ``board`` and ``clock`` must be a built-in board and one of its CPU clock
    profiles (records qualify built-in boards only, so a ``target.custom_boards``
    name is refused too), and ``precision`` one of :data:`PRECISIONS`; anything
    else raises ``ValueError``, the precision first.

    A version without a record, or a precision its record does not declare,
    is unsupported here, even when an engine's own version check would build
    it: this heliaPROFILER makes no claim about it.
    """
    if precision not in PRECISIONS:
        raise ValueError(
            f"Unknown precision {precision!r}; expected one of {', '.join(PRECISIONS)}"
        )
    clocks = get_soc_for_board(board).cpu_clock.speed_names
    if clock not in clocks:
        raise ValueError(
            f"Board {board} has no {clock!r} clock; expected one of {', '.join(clocks)}"
        )
    record = runtime(name, version)
    if record is None:
        label = f"{name} {version}" if version is not None else name
        return Qualification(
            RuntimeQualification.UNSUPPORTED, f"No runtime record for {label}", None
        )
    if precision not in record.precisions:
        reason = f"{record.name} {record.version} does not declare {precision}"
        return Qualification(RuntimeQualification.UNSUPPORTED, reason, record)
    if (reason := record.precisions[precision]) is not None:
        return Qualification(RuntimeQualification.UNSUPPORTED, reason, record)
    if any(
        target.board == board and target.clock == clock and precision in target.precisions
        for target in record.qualified
    ):
        return Qualification(RuntimeQualification.QUALIFIED, None, record)
    reason = (
        f"{record.name} {record.version} is not qualified for {precision} on {board} at {clock}"
    )
    return Qualification(RuntimeQualification.SUPPORTED, reason, record)


def _parse_record(raw: Any, location: str) -> RuntimeRecord:
    record = _object(raw, _FIELDS, location)
    if record["schema"] != RUNTIME_SCHEMA:
        raise ConfigError(f"Runtime record {location} has schema {record['schema']!r}")
    if not isinstance(record["default"], bool):
        raise ConfigError(f"Runtime record {location} default must be true or false")
    source = _object(record["source"], frozenset({"repo", "commit"}), f"{location} source")
    if not _COMMIT.fullmatch(_text(source["commit"], f"{location} source commit")):
        raise ConfigError(f"Runtime record {location} source commit must be a 40-hex commit")
    precisions = record["precisions"]
    if not isinstance(precisions, dict):
        raise ConfigError(f"Runtime record {location} precisions must be an object")
    declared: dict[str, str | None] = {}
    for precision, status in precisions.items():
        _precision(precision, location)
        if status == _SUPPORTED:
            declared[precision] = None
        else:
            reason = _object(status, frozenset({"unsupported"}), f"{location} {precision}")
            declared[precision] = _text(reason["unsupported"], f"{location} {precision} reason")
    if not isinstance(record["qualified"], list):
        raise ConfigError(f"Runtime record {location} qualified must be a list")
    qualified = []
    for index, value in enumerate(record["qualified"]):
        owner = f"{location} qualified[{index}]"
        entry = _object(value, _QUALIFIED_FIELDS, owner)
        names = entry["precisions"]
        if not isinstance(names, list) or not names:
            raise ConfigError(f"Runtime record {owner} precisions must be a non-empty list")
        for precision in names:
            if declared.get(_precision(precision, owner), _SUPPORTED) is not None:
                raise ConfigError(
                    f"Runtime record {owner} qualifies {precision}, which is not supported"
                )
        qualified.append(
            QualifiedTarget(
                board=_text(entry["board"], f"{owner} board"),
                clock=_text(entry["clock"], f"{owner} clock"),
                precisions=tuple(names),
                basis=_text(entry["basis"], f"{owner} basis"),
                trace=_text(entry["trace"], f"{owner} trace"),
            )
        )
    return RuntimeRecord(
        name=_text(record["name"], f"{location} name"),
        version=_text(record["version"], f"{location} version"),
        default=record["default"],
        source=RuntimeSource(_text(source["repo"], f"{location} source repo"), source["commit"]),
        precisions=declared,
        qualified=tuple(qualified),
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate keys {sorted({k for k in keys if keys.count(k) > 1})}")
    return dict(pairs)


def _object(value: Any, fields: frozenset[str], owner: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        expected = ", ".join(sorted(fields))
        raise ConfigError(f"Runtime record {owner} must be an object with exactly: {expected}")
    return value


def _text(value: Any, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Runtime record {owner} must be a non-empty string")
    return value


def _precision(value: Any, owner: str) -> str:
    if value not in PRECISIONS:
        raise ConfigError(f"Runtime record {owner} names unknown precision {value!r}")
    return value
