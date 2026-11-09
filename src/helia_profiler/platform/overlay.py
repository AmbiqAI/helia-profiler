"""Resolve an explicit catalog file into per-run HPX facts and inherited policy."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from helia_platforms import Catalog, Target, load_overlay
from helia_platforms import PlatformError as CatalogError
from helia_platforms.model import DomainPoint

from ..errors import ConfigError
from .catalog import _builtin_catalog, _soc, catalog_provenance, target_selection
from .placement import Placement
from .policy import BOARD_POLICIES, SOC_POLICIES
from .provenance import OverlaySource
from .registry import PlatformRegistry, _freeze_registry, build_platform_registry
from .soc import SocDef


def overlay_registry(
    settings: Mapping[str, Any], *, selected_board: str | None
) -> PlatformRegistry:
    """Project only the requested target and bases referenced by legacy declarations."""
    value = settings["platform_overlay"]
    if not isinstance(value, str | os.PathLike):
        raise ConfigError("target.platform_overlay must be a local YAML file path")
    try:
        path = Path(value).expanduser().resolve()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        catalog = load_overlay(path, base=_builtin_catalog())
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise ValueError("overlay changed while it was being resolved")
        return _project(catalog, OverlaySource(path, before), settings, selected_board)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, CatalogError) as exc:
        raise ConfigError(
            f"Cannot resolve target.platform_overlay {value}: {exc}",
            hint="Use a catalog overlay derived from supported HPX board and SoC policies; "
            "standalone HPX declarations remain available through custom_socs/custom_boards.",
        ) from exc


def _definitions(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _references(settings: Mapping[str, Any], selected: str | None) -> tuple[set[str], set[str]]:
    socs = _definitions(settings.get("custom_socs"))
    boards = _definitions(settings.get("custom_boards"))
    soc_names = {
        spec["based_on"]
        for name, spec in socs.items()
        if isinstance(spec, dict)
        and isinstance(spec.get("based_on"), str)
        and (spec["based_on"] not in socs or spec["based_on"] == name)
    }
    board_names = {
        spec["based_on"]
        for name, spec in boards.items()
        if isinstance(spec, dict)
        and isinstance(spec.get("based_on"), str)
        and (spec["based_on"] not in boards or spec["based_on"] == name)
    }
    soc_names.update(
        spec["soc"]
        for spec in boards.values()
        if isinstance(spec, dict) and isinstance(spec.get("soc"), str) and spec["soc"] not in socs
    )
    selected = selected if selected is not None else settings.get("board")
    if selected is not None:
        if not isinstance(selected, str):
            raise ValueError("target.board must be a string")
        if selected not in boards:
            board_names.add(selected)
    return soc_names, board_names


def _policy_key(target: Target) -> str:
    key = next((name for name in target.lineage("soc") if name in SOC_POLICIES), None)
    if key is None:
        raise ValueError(f"{target.id}: no supported HPX SoC policy lineage")
    return key


def _geometry(target: Target) -> dict[str, tuple[int | None, int | None]]:
    core = target.cores()[0].id
    result = {}
    for region in target.memories(core, include_discouraged=True):
        if region.size % 1024:
            raise ValueError(f"{target.id}: HPX memory views require whole KiB capacities")
        if region.technology == "psram":
            continue
        access = target.reachable(core, region.name)
        result[region.name] = (region.base, access.address if access else None)
    return result


def _native_soc(target: Target, key: str, reference: Target) -> SocDef:
    if target.core.id != reference.core.id or _geometry(target) != _geometry(reference):
        raise ValueError(
            f"{target.id}: changed core/address geometry is not supported by HPX views"
        )
    if target.trace_clock_mhz != reference.trace_clock_mhz:
        raise ValueError(f"{target.id}: a changed trace clock needs an explicit HPX probe policy")
    policy = SOC_POLICIES[key]
    domain = target.cores()[0].clock_domain
    reference_domain = reference.cores()[0].clock_domain
    for _, point, _ in policy.cpu_points:
        actual = target.operating_point(point, domain=domain)
        expected = reference.operating_point(point, domain=reference_domain)
        if (
            not isinstance(actual, DomainPoint)
            or not isinstance(expected, DomainPoint)
            or actual.mhz != expected.mhz
        ):
            raise ValueError(
                f"{target.id}: changed CPU clocks need an explicit HPX clock/build policy"
            )
    soc = _soc(target, target.soc.id, policy, custom=True, policy_name=key)
    for region in target.memories(target.cores()[0].id, include_discouraged=True):
        if region.technology == "psram":
            access = target.reachable(target.cores()[0].id, region.name)
            address = access.address if access and access.address is not None else region.base
            expected = soc.capabilities.memory.placement_bases.get(Placement.PSRAM)
            if expected is not None and address != expected:
                raise ValueError(f"{target.id}: PSRAM address geometry is not supported by HPX")
    return soc


def _project(
    catalog: Catalog, source: OverlaySource, settings: Mapping[str, Any], selected: str | None
) -> PlatformRegistry:
    base = build_platform_registry()
    socs, boards = dict(base.socs), dict(base.boards)
    board_socs, selections = dict(base.board_socs), dict(base.selections)
    provenance = catalog_provenance(catalog)
    soc_names, board_names = _references(settings, selected)
    board_policies = {board.name: board for board in BOARD_POLICIES}
    for name in sorted(soc_names):
        if name in base.socs:
            continue
        target = catalog.resolve(name)
        key = _policy_key(target)
        socs[name] = _native_soc(target, key, _builtin_catalog().resolve(key))
    legacy_boards = _definitions(settings.get("custom_boards"))
    for name in sorted(board_names):
        if name in base.boards and (name != selected or name in legacy_boards):
            continue
        target = catalog.resolve(name)
        key = _policy_key(target)
        policy = next(
            (board_policies[n] for n in target.lineage("board") if n in board_policies), None
        )
        if policy is None:
            raise ValueError(f"{name}: no supported HPX board policy lineage")
        if policy.soc != key:
            raise ValueError(f"{name}: board and SoC policy lineages select different NSX targets")
        native_soc = _native_soc(target, key, _builtin_catalog().resolve(SOC_POLICIES[key].board))
        if target.soc.id not in socs:
            socs[target.soc.id] = _native_soc(
                catalog.resolve(target.soc.id), key, _builtin_catalog().resolve(key)
            )
        boards[name] = replace(
            policy,
            name=name,
            soc=target.soc.id,
            psram_kb=None,
            starter_profile_board=policy.profile_source_board,
            is_fpga=target.category == "fpga",
        )
        board_socs[name] = native_soc
        selections[name] = target_selection(target, provenance, policy, custom=True)
    return _freeze_registry(socs, boards, selections, provenance, board_socs, source)
