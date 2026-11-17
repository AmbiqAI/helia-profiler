"""Run-local compiler requests from the profiler's frozen target selection."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ...config import ProfileConfig
from ...errors import EngineError
from ...platform.catalog import _builtin_catalog, catalog_provenance
from ...platform.provenance import CatalogProvenance, OverlaySource
from ...runtime.records import runtimes
from ..semver import parse_semver

log = logging.getLogger("hpx")


_BOARD_TO_AOT_PLATFORM: dict[str, str] = {
    "apollo3p_evb": "apollo3p_evb",
    "apollo4p_evb": "apollo4p_evb",
    "apollo4p_blue_kbr_evb": "apollo4p_blue_kbr_evb",
    "apollo4p_blue_kxr_evb": "apollo4p_blue_kxr_evb",
    "apollo4l_evb": "apollo4l_evb",
    "apollo4l_blue_evb": "apollo4l_blue_evb",
    "apollo510_evb": "apollo510_evb",
    "apollo510b_evb": "apollo510b_evb",
    "atomiq110_fpga_turbo": "atomiq110",
    "apollo5b_evb": "apollo510_evb",
    "apollo330mP_evb": "apollo330mp_evb",
    "apollo510dL_evb": "apollo510l_evb",
}

_LEGACY_AOT_ALIASES = frozenset({"apollo5b_evb", "atomiq110_fpga_turbo"})


def aot_platform_for_board(board: str, *, warn: bool = True) -> str | None:
    """Return the mapped AOT target, warning for legacy compatibility aliases."""
    aot_platform = _BOARD_TO_AOT_PLATFORM.get(board)
    if warn and board in _LEGACY_AOT_ALIASES:
        log.warning(
            "Board '%s' has no exact heliaAOT platform — using '%s'. "
            "Memory sizes and capabilities may differ.  "
            "Set engine.config.platform_name to override.",
            board,
            aot_platform,
        )
    return aot_platform


def _legacy_aot_platform(config: ProfileConfig) -> str:
    """Map the profiler's target board to a heliaAOT platform name.

    Resolution order:
    1. Explicit ``engine.config.platform_name`` override.
    2. Built-in ``_BOARD_TO_AOT_PLATFORM`` mapping.
    3. Raise ``EngineError`` with guidance.
    """
    explicit = config.engine.config.get("platform_name")
    if explicit:
        log.info("Using explicit AOT platform override: %s", explicit)
        return str(explicit)

    board = config.target.board
    aot_platform = aot_platform_for_board(board)

    if aot_platform is None:
        known = ", ".join(sorted(_BOARD_TO_AOT_PLATFORM))
        raise EngineError(
            f"No heliaAOT platform mapping for board '{board}'",
            hint=(f"Set engine.config.platform_name explicitly, or use a supported board: {known}"),
        )

    return aot_platform


@dataclass(frozen=True)
class CompilerTarget:
    """Compiler inputs and the public record checked against their frozen source."""

    name: str
    options_json: str
    schema: int | None = None
    catalog: CatalogProvenance | None = None
    target_id: str | None = None
    cpu: str | None = None
    overlay: OverlaySource | None = None
    requires_overlay: bool = False
    record_json: str | None = None

    def options(self) -> dict[str, Any]:
        return json.loads(self.options_json)

    def record(self) -> dict[str, Any] | None:
        return json.loads(self.record_json) if self.record_json is not None else None

    def verify_source(self) -> None:
        if self.overlay is not None:
            try:
                digest = hashlib.sha256(self.overlay.path.read_bytes()).hexdigest()
            except OSError as exc:
                raise EngineError(f"Cannot read compiler catalog overlay: {exc}") from exc
            if digest != self.overlay.sha256:
                raise EngineError("Compiler catalog overlay changed after target resolution")


def installed_version() -> str | None:
    try:
        return version("helia-aot")
    except PackageNotFoundError:
        return None


def select_target(config: ProfileConfig, options: object) -> CompilerTarget:
    """Select catalog projection or preserve an explicitly declared compatibility route."""
    if options is not None and not isinstance(options, Mapping):
        raise EngineError("heliaAOT platform must be a mapping")
    values = dict(options or {})
    override = config.engine.config.get("platform_name")
    selection = config.platform_registry.selections.get(config.target.board)
    faithful = selection is not None and selection.target_id is not None
    explicit_catalog = values.get("facts") == "catalog"
    automatic = faithful and not values and not override
    if not explicit_catalog and not automatic:
        name = _legacy_aot_platform(config)
        values["name"] = name
        return CompilerTarget(name, _json(values))

    actual = parse_semver(installed_version() or "0.0.0")
    contracts = [
        r for r in runtimes() if r.name == "helia-aot" and r.catalog_platform_schema is not None
    ]
    supported = next((r for r in contracts if parse_semver(r.version)[:2] == actual[:2]), None)
    if supported is None:
        earliest = min((parse_semver(r.version) for r in contracts), default=None)
        if (
            automatic
            and (earliest is None or actual < earliest)
            and config.target.platform_overlay is None
        ):
            name = _legacy_aot_platform(config)
            return CompilerTarget(name, _json({"name": name}))
        raise EngineError(
            "Installed heliaAOT has no recorded catalog-platform contract",
            hint="Use the recorded AOT release, or explicitly select a registered compiler platform.",
        )
    if supported.catalog_platform_schema != 1:
        raise EngineError("Unsupported heliaAOT catalog-platform record schema")
    frozen_catalog = (
        selection.catalog if selection is not None else None
    ) or config.platform_registry.catalog
    if frozen_catalog is None or (not faithful and not override):
        raise EngineError("The HPX target has no frozen catalog selection for compiler projection")

    overlay = config.platform_registry.overlay_source
    if values.get("overlay") is not None and (
        overlay is None or Path(values["overlay"]).expanduser().resolve() != overlay.path
    ):
        raise EngineError("Use target.platform_overlay to freeze the compiler's catalog source")
    name = str(override or config.target.board)
    if not override:
        assert selection is not None
        for key, expected in (("boot", selection.boot), ("addons", list(selection.addons))):
            if key in values and values[key] != expected:
                raise EngineError(f"Compiler platform.{key} differs from the frozen HPX target")
            values[key] = expected
    values.update(name=name, facts="catalog")
    if overlay is not None:
        values["overlay"] = str(overlay.path)
    request = CompilerTarget(
        name.lower(), _json(values), supported.catalog_platform_schema, overlay=overlay
    )
    request.verify_source()
    from helia_platforms import PlatformError, load_overlay

    try:
        catalog = _builtin_catalog()
        if overlay is not None:
            catalog = load_overlay(overlay.path, base=catalog)
        provenance = catalog_provenance(catalog)
        if provenance != frozen_catalog:
            raise EngineError("Compiler catalog differs from the frozen HPX source")
        chosen = catalog.resolve(name, boot=values.get("boot"), addons=values.get("addons", []))
        requires_overlay = False
        if overlay is not None:
            try:
                _builtin_catalog().resolve(
                    name, boot=values.get("boot"), addons=values.get("addons", [])
                )
            except PlatformError:
                requires_overlay = True
        if not override and (
            selection is None
            or chosen.id != selection.target_id
            or chosen.cores()[0].id != selection.core
            or (chosen.implementation.id if chosen.implementation else None)
            != selection.implementation
        ):
            raise EngineError(
                "Compiler selection cannot preserve the frozen HPX core/implementation"
            )
    except (OSError, ValueError, PlatformError) as exc:
        raise EngineError(f"Cannot resolve compiler catalog target: {exc}") from exc
    request.verify_source()
    values["boot"] = chosen.boot.name if chosen.boot is not None else None
    values["addons"] = [addon.id for addon in chosen.addons]
    return replace(
        request,
        options_json=_json(values),
        catalog=provenance,
        target_id=chosen.id,
        cpu=chosen.cores()[0].core,
        requires_overlay=requires_overlay,
    )


def resolve_target(request: CompilerTarget, platform: Any = None) -> CompilerTarget:
    """Resolve through the installed public API and validate its actual source and selection."""
    if request.schema is None:
        return request
    request.verify_source()
    if platform is None:
        from helia_aot import ConvertArgs

        try:
            platform = ConvertArgs(platform=request.options()).platform
        except ValueError as exc:
            raise EngineError(f"Invalid heliaAOT compiler platform: {exc}") from exc
    record = compiler_record(platform)
    if type(record.get("schema")) is not int or record["schema"] != request.schema:
        raise EngineError("heliaAOT returned an unsupported compiler-platform record schema")
    options = request.options()
    if (
        record.get("facts") != "catalog"
        or record.get("name") != request.name
        or record.get("target") != request.target_id
        or record.get("cpu") != request.cpu
        or (options.get("boot") is not None and record.get("boot") != options["boot"])
        or record.get("addons") != options.get("addons", [])
    ):
        raise EngineError("heliaAOT compiler target differs from the selected catalog request")
    _verify_catalog(request.catalog, record.get("catalog"))
    if (
        request.requires_overlay
        and request.overlay is not None
        and not any(
            source["label"] == f"overlay:{request.overlay.path.name}"
            for source in record["catalog"]["sources"]
        )
    ):
        raise EngineError("heliaAOT compiler record omits the selected overlay source")
    request.verify_source()
    return replace(request, record_json=_json(record))


def compiler_record(platform: Any) -> dict[str, Any]:
    """The public compiler record on an explicitly supported experimental minor."""
    actual = parse_semver(installed_version() or "0.0.0")
    supported = next(
        (
            r
            for r in runtimes()
            if r.name == "helia-aot"
            and r.catalog_platform_schema == 1
            and parse_semver(r.version)[:2] == actual[:2]
        ),
        None,
    )
    if supported is None:
        raise EngineError("Installed heliaAOT has no recorded catalog-platform contract")
    from helia_aot.platforms import platform_record, resolve_platform

    try:
        record = platform_record(resolve_platform(platform, warn=False))
    except (TypeError, ValueError) as exc:
        raise EngineError(f"heliaAOT catalog projection failed: {exc}") from exc
    if (
        type(record.get("schema")) is not int
        or record["schema"] != supported.catalog_platform_schema
    ):
        raise EngineError("heliaAOT returned an unsupported compiler-platform record schema")
    return record


def verify_conversion(request: CompilerTarget, result: Any) -> None:
    """Check the conversion's own plan, rather than assuming preflight was its platform."""
    expected = request.record()
    if expected is None:
        return
    request.verify_source()
    plan = getattr(result, "plan", None)
    if plan is None:
        raise EngineError("heliaAOT returned no plan for its catalog target")
    actual = plan.platform.model_dump(mode="json")
    expected_platform = {
        k: v for k, v in expected.items() if k not in {"schema", "catalog", "speeds_mhz"}
    }
    expected_platform["capabilities"] = sorted(expected_platform["capabilities"])
    identity = plan.generator.helia_platforms
    if (
        actual != expected_platform
        or identity is None
        or identity.model_dump(mode="json") != expected["catalog"]
    ):
        raise EngineError("heliaAOT conversion plan differs from the resolved compiler target")
    request.verify_source()


def _verify_catalog(expected: CatalogProvenance | None, actual: object) -> None:
    if (
        expected is None
        or not isinstance(actual, dict)
        or any(
            actual.get(key) != getattr(expected, key)
            for key in ("package_version", "schema_version", "catalog_digest")
        )
    ):
        raise EngineError("heliaAOT compiler catalog identity differs from the frozen HPX source")
    known = {
        (s.label, s.schema_version, s.sha256, s.distribution_version) for s in expected.sources
    }
    sources = actual.get("sources")
    if not isinstance(sources, list) or any(
        not isinstance(s, dict)
        or (s.get("label"), s.get("schema_version"), s.get("sha256"), s.get("distribution_version"))
        not in known
        for s in sources
    ):
        raise EngineError("heliaAOT compiler used an unknown or changed catalog source")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
