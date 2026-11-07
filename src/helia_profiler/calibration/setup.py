"""The setup a capture was measured under, and how two setups compare (#648).

A capture's setup is one digest of its held settings as the bundle records
them; :func:`_setup` builds it and :func:`_require_same_setup` refuses two
sources whose setups differ, naming what differs when it can. Beside it,
the pairing digest covers the same basis less where the constants sit, so
captures that differ only in the weights memory share it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from ..engines.helia_aot.compile import non_finite_field, without_constant_placement
from ..errors import CalibrationError, EngineError, EngineMissingError
from ..evaluation.compare import RunArtifacts
from ..results.dimensions import derive_architecture_flags
from ..results.serde import nested_get

if TYPE_CHECKING:
    from .table import CalibrationSource

#: Parts of the recorded settings the setup leaves out: the sections that
#: do not change a cycle count, and the fields that belong to one run.
SETUP_EXCLUDED: frozenset[str] = frozenset(
    {
        "config.power",
        "config.output",
        "config.timeouts",
        "config.verbose",
        "config.clean",
        "config.frozen",
        "config.work_dir",
        "config.model.path",
        "config.engine.config_path",
        "config.target.jlink_serial",
        "config.target.usb_port",
        "config.target.segger_rtt_path",
        "config.build.offline",
        "config.build.update_dependencies",
        "config.build.compiler_launcher",
        # CMake only drives the build; the flags come from the toolchain file.
        "toolchain.cmake_version",
        # The pairing digest's own field; the setup holds the full configuration.
        "engine.effective_config_pairing_sha256",
    }
)
#: What the pairing digest leaves out of the setup besides the constants'
#: rules: hpx derives the constants' placement rule from it.
PAIRING_EXCLUDED: frozenset[str] = frozenset({"config.model.weights_location"})
#: Where the heliaAOT arguments sit in the frozen config; the knob values
#: under them are removed by heliaAOT's own registry.
_AOT_ARGS = ("config", "engine", "config", "aot_args")
_EFFECTIVE_CONFIG = "effective_config_sha256"
_PAIRING_CONFIG = "effective_config_pairing_sha256"
_HELIA_AOT = "helia-aot"


@dataclass(frozen=True)
class Setup:
    """A capture's setup: the digest of its held settings and the fields it covers.

    ``values`` holds the settings themselves for a bundle read in this run,
    so a mismatch can name the field; a file records only the digest and
    the field names.
    """

    digest: str
    fields: tuple[str, ...]
    #: The heliaAOT release the run compiled with, whose knob registry the
    #: stripping depends on; ``None`` for another engine.
    helia_aot_version: str | None = None
    values: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)
    #: The digest of the basis less where the constants sit, and its fields;
    #: ``None`` for both when unknown.
    pairing_digest: str | None = None
    pairing_fields: tuple[str, ...] | None = None

    @classmethod
    def of(
        cls,
        basis: Mapping[str, Any],
        helia_aot_version: str | None = None,
        pairing: Mapping[str, Any] | None = None,
    ) -> Setup:
        """The setup of a nested *basis*, and of its *pairing* basis when known:
        each one's canonical JSON digested, its leaves listed."""
        pairing_digest, pairing_fields = (None, None) if pairing is None else _digested(pairing)
        digest, fields = _digested(basis)
        return cls(digest, fields, helia_aot_version, basis, pairing_digest, pairing_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "fields": list(self.fields),
            "pairing_digest": self.pairing_digest,
            "pairing_fields": None if self.pairing_fields is None else list(self.pairing_fields),
            "helia_aot_version": self.helia_aot_version,
        }


def _digested(basis: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    leaves: dict[str, Any] = {}
    _leaves("", basis, leaves)
    return _canonical_digest(basis), tuple(sorted(leaves))


def _require_same_setup(reference: CalibrationSource, other: CalibrationSource) -> None:
    """Refuse two sources whose setup digests differ, naming what differs when known.

    With both bases in hand the leaves are compared by their canonical JSON,
    an absent leaf counting as different from a null one; when no leaf
    differs the top-level sections are compared the same way. A file holds
    only the digest and the field names, so against one the field lists are
    compared and otherwise the digests are named.
    """
    mine_version, their_version = reference.setup.helia_aot_version, other.setup.helia_aot_version
    if mine_version != their_version:
        raise CalibrationError(
            f"{other.source} was compiled with heliaAOT {their_version} and {reference.source} "
            f"with heliaAOT {mine_version}; the knob registry the setup is stripped by is "
            "that release's.",
            hint="Calibrate captures compiled by one heliaAOT release.",
        )
    if reference.setup.digest == other.setup.digest:
        return
    mine, theirs = reference.setup.values, other.setup.values
    if mine is not None and theirs is not None:
        mine_leaves: dict[str, Any] = {}
        their_leaves: dict[str, Any] = {}
        _leaves("", mine, mine_leaves)
        _leaves("", theirs, their_leaves)
        # A name that is a leaf on one side and a mapping on the other is
        # not absent there; its own leaves say what differs.
        names = sorted(
            name
            for name in set(mine_leaves) | set(their_leaves)
            if _canonical(mine_leaves.get(name, _ABSENT))
            != _canonical(their_leaves.get(name, _ABSENT))
            and not any(
                other_name.startswith(name + ".")
                for other_name in (their_leaves if name in mine_leaves else mine_leaves)
            )
        )
        if names:
            detail = "; ".join(
                f"{name} is {_shown(their_leaves.get(name, _ABSENT))} in {other.source} but "
                f"{_shown(mine_leaves.get(name, _ABSENT))} in {reference.source}"
                for name in names[:5]
            )
        else:
            sections = sorted(
                name
                for name in set(mine) | set(theirs)
                if _canonical(mine.get(name, _ABSENT)) != _canonical(theirs.get(name, _ABSENT))
            )
            plural = len(sections) != 1
            detail = f"the {', '.join(sections)} section{'s' if plural else ''} differ{'' if plural else 's'}"
    else:
        missing = sorted(set(reference.setup.fields) ^ set(other.setup.fields))
        detail = (
            f"the fields {', '.join(missing[:5])} are held by only one of them"
            if missing
            else f"their setup digests differ ({other.setup.digest[:12]} against "
            f"{reference.setup.digest[:12]})"
        )
    raise CalibrationError(
        f"{other.source} and {reference.source} were captured under different setups: {detail}.",
        hint="Calibrate captures from one setup; write another file for the other.",
    )


_ABSENT = object()


def _canonical(value: Any) -> str:
    """The canonical JSON of *value*; an absent value has a form no JSON has."""
    if value is _ABSENT:
        return "<absent>"
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _shown(value: Any) -> str:
    return "absent" if value is _ABSENT else repr(value)


def _setup(run: RunArtifacts, source: str) -> Setup:
    """The held settings of a bundle, as one nested basis.

    The excluded sections go before anything is canonicalized, so a value
    they hold that is not a finite number (a power limit set to infinity)
    does not matter; one in a held field is an error naming the field.

    Raises:
        CalibrationError: when the bundle records no settings, a held field
            holds a NaN or infinity, heliaAOT rejects the recorded engine
            arguments, or a helia-aot bundle records no
            ``engine.effective_config_sha256``.
        EngineMissingError: when heliaAOT is not installed on this host.
    """
    config = run.metadata.get("config")
    if not isinstance(config, dict) or not config:
        raise CalibrationError(f"Bundle {source} records no settings in run_metadata.json.")
    basis: dict[str, Any] = {"config": copy.deepcopy(config)}
    for section in ("toolchain", "engine"):
        block = run.metadata.get(section)
        if isinstance(block, dict):
            basis[section] = copy.deepcopy(block)
    for excluded in SETUP_EXCLUDED:
        _remove(basis, tuple(excluded.split(".")))
    aot_args = _lookup(basis, _AOT_ARGS)
    helia_aot_version: str | None = None
    if nested_get(run.metadata, "config", "engine", "type") == _HELIA_AOT:
        from ..engines.helia_aot.compile import without_knob_values

        engine = basis.get("engine")
        if not (isinstance(engine, dict) and isinstance(engine.get(_EFFECTIVE_CONFIG), str)):
            raise CalibrationError(
                f"Bundle {source} records no engine.{_EFFECTIVE_CONFIG}, so the "
                "configuration its compiler received is unknown.",
                hint="It was written by a heliaPROFILER that did not record it; profile again.",
            )
        version = engine.get("version")
        helia_aot_version = version if isinstance(version, str) else None
        try:
            stripped = without_knob_values(aot_args if isinstance(aot_args, dict) else {})
        except EngineMissingError:
            # Nothing about the bundle: the host lacks heliaAOT, and the
            # install hint says what to do.
            raise
        except EngineError as exc:
            raise CalibrationError(f"Bundle {source}: {exc.args[0]}", hint=exc.hint) from exc
        _lookup(basis, _AOT_ARGS[:-1])[_AOT_ARGS[-1]] = stripped
    basis["build"] = {"architecture_flags": derive_architecture_flags(run.metadata)}
    basis["kernel"] = {"build": _kernel_build(run)}
    unfit = non_finite_field(basis)
    if unfit is not None:
        raise CalibrationError(
            f"Bundle {source}: the held setting {unfit} is not a finite number, which the "
            "record cannot hold.",
            hint="Profile with a finite value there, or leave the setting at its default.",
        )
    return Setup.of(basis, helia_aot_version, _pairing_basis(basis, run))


def _pairing_basis(basis: Mapping[str, Any], run: RunArtifacts) -> dict[str, Any] | None:
    """The setup basis less where the constants sit; ``None`` when the bundle
    records no ``engine.effective_config_pairing_sha256``.

    ``config.model.weights_location`` goes, the constants' placement goes
    from the heliaAOT arguments, already less their knob values, and the
    compiler's configuration is held by its pairing digest.
    """
    recorded = nested_get(run.metadata, "engine", _PAIRING_CONFIG)
    if not isinstance(recorded, str) or not isinstance(basis.get("engine"), dict):
        return None
    pairing = copy.deepcopy(dict(basis))
    for excluded in PAIRING_EXCLUDED:
        _remove(pairing, tuple(excluded.split(".")))
    aot_args = _lookup(pairing, _AOT_ARGS)
    if isinstance(aot_args, dict):
        _lookup(pairing, _AOT_ARGS[:-1])[_AOT_ARGS[-1]] = without_constant_placement(aot_args)
    pairing["engine"].pop(_EFFECTIVE_CONFIG, None)
    pairing["engine"][_PAIRING_CONFIG] = recorded
    return pairing


def _lookup(data: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _remove(data: dict[str, Any], path: tuple[str, ...]) -> None:
    parent = _lookup(data, path[:-1])
    if isinstance(parent, dict):
        parent.pop(path[-1], None)


def _leaves(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Dotted leaf names of a nested basis, for the file and for messages only."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _leaves(f"{prefix}.{key}" if prefix else str(key), item, out)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            _leaves(f"{prefix}.{index}" if prefix else str(index), item, out)
    else:
        out[prefix] = value


def _kernel_build(run: RunArtifacts) -> str | None:
    """The kernel provider module's commit, else its content hash, from the lock."""
    modules = nested_get(run.metadata, "dependencies", "modules")
    for module in modules if isinstance(modules, list) else ():
        if isinstance(module, dict) and module.get("name") in CMSIS_NN_PROVIDER_MODULES:
            commit = module.get("peeled_commit")
            if isinstance(commit, str) and commit:
                return f"commit:{commit}"
            digest = nested_get(module, "content_hash", "value")
            if isinstance(digest, str) and digest:
                return f"content:{digest}"
    return None
