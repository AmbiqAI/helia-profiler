"""NSX project-file rendering for generated profiler firmware apps."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .. import nsx as nsx_cli
from ..config import Transport
from ..errors import ConfigError, FirmwareError
from ..platform import get_soc_for_board
from .context import FirmwareRenderContext
from .render import _jinja_env, _write_text

if TYPE_CHECKING:
    from ..config import ProfileConfig
    from ..engines.base import EngineArtifacts, ArenaRegion
    from ..platform import BoardDef, PlatformRegistry, SocDef

log = logging.getLogger("hpx")



@dataclass(frozen=True)
class NsxModuleSpec:
    """Resolved NSX module identity for firmware generation.

    ``name`` is the module name used in ``NSX_APP_MODULES``. ``project`` is the
    owning NSX project/repository used for lock resolution. Keeping them
    separate avoids assuming that ``project == module`` for monorepo-backed SDK
    modules.
    """

    name: str
    project: str


# ---------------------------------------------------------------------------
# SDK tier → module set mapping
#
# hpx owns the *selection* of modules a profiler app needs (a deliberately lean
# set — e.g. it uses nsx-core's runtime helpers directly rather than the legacy
# nsx-harness / nsx-utils modules). The *ownership* of each module (which NSX
# project vendors it) is NOT hand-maintained here: it is derived from the NSX
# starter profile for the target board so it always tracks the upstream
# registry (which repoints migrated Ambiq modules onto the unified
# nsx-ambiq-sdk project). See ``_module_project``.
# ---------------------------------------------------------------------------


def _soc_has_backend(soc: Any, backend: str) -> bool:
    return backend in getattr(soc, "profiling_backends", ())


_POWER_SYNC_MODULE_NAMES: tuple[str, ...] = (
    "nsx-interrupt",
    "nsx-gpio",
)

_DEFAULT_MAIN_PROJECT_REFS: dict[str, str] = {
    "neuralspotx": "main",
    "nsx-ambiq-sdk": "main",
}


def _usb_provider_module_names(module_specs: list[NsxModuleSpec], profile: dict[str, Any]) -> list[str]:
    """Return provider-specific USB support modules implied by the SDK tier."""
    present = {spec.name for spec in module_specs}
    overrides = profile.get("module_overrides") or {}
    required: list[str] = []
    for name in sorted(present):
        if name == "nsx-ambiqsuite":
            candidate = "nsx-ambiq-usb"
        elif name.startswith("nsx-ambiqsuite-"):
            candidate = name.replace("nsx-ambiqsuite-", "nsx-ambiq-usb-", 1)
        else:
            continue
        if candidate in present or candidate in required:
            continue
        if candidate in overrides or nsx_cli.registry_module_project(candidate) is not None:
            required.append(candidate)
    return required


def _board_module_name(board: str) -> str:
    """Derive the NSX board module name from a board name."""
    return f"nsx-board-{board.replace('_', '-')}"


def _starter_profile_module_names(profile: dict[str, Any]) -> list[str]:
    """Return the starter profile's authoritative direct module list.

    hpx trusts the profile as the source of truth for the board/provider stack
    and direct runtime consumers.
    """
    modules = profile.get("modules") or []
    if not isinstance(modules, list) or not all(isinstance(name, str) for name in modules):
        raise FirmwareError(
            "NSX starter profile is missing a valid module list",
            hint="Update neuralspotx so the board starter profile declares its modules.",
        )
    # hpx intentionally does not consume these legacy helpers directly.
    return [name for name in modules if name not in {"nsx-harness", "nsx-utils"}]


def _get_starter_profile(board: str, *, profile_board: str | None = None) -> dict[str, Any]:
    """Return the NSX starter profile for *board*.

    The profile is the single source of truth for module/project ownership.
    hpx follows the installed NSX starter profile's module and project map
    rather than maintaining a parallel SDK-tier table.
    """
    lookup_board = profile_board or board
    profile = nsx_cli.starter_profile(lookup_board)
    if profile is None:
        raise FirmwareError(
            f"No NSX starter profile for board '{lookup_board}'",
            hint=(
                "The board must be registered in the NSX registry "
                "(registry.lock.yaml ships with neuralspotx). Check the board "
                "name or update neuralspotx."
            ),
        )
    return profile


def _needs_armv8m_pmu_module(
    board: str, *, profile_board: str | None = None, registry: PlatformRegistry | None = None
) -> bool:
    """Return whether this board needs the standalone Armv8-M PMU module.

    Some installed NSX starter profiles still omit ``nsx-pmu-armv8m`` for AP5
    boards even though hpx's generated firmware links ``nsx::pmu_armv8m``.
    Keep a narrow compatibility fallback until those profiles are updated.
    """
    for candidate in (board, profile_board):
        if candidate is None:
            continue
        try:
            soc = get_soc_for_board(candidate, registry=registry)
        except ValueError:
            continue
        return _soc_has_backend(soc, "armv8m-pmu")
    return False


def _module_project(name: str, profile: dict[str, Any]) -> str:
    """Resolve the owning NSX project for a module name.

    Resolution order, mirroring ``nsx``'s own manifest generation:

    0. The starter profile's ``module_overrides`` — authoritative repoint of a
       module onto the SDK monorepo for tiers that have migrated.
    1. The base registry entry (standalone project) for everything else.
    2. The module name itself as a last resort (opaque / local modules).
    """
    override = profile.get("module_overrides", {}).get(name)
    if isinstance(override, dict) and override.get("project"):
        return override["project"]
    return nsx_cli.registry_module_project(name) or name


def _render_module_registry(
    profile: dict[str, Any],
    project_ref_overrides: dict[str, tuple[str, str]],
) -> str:
    """Render the ``module_registry`` block for nsx.yml from the profile.

    Emitting the profile's full ``project_overrides`` / ``module_overrides``
    makes the app's effective registry agree with the per-module project pins
    in the manifest (so ``nsx`` alignment passes) and ensures transitive
    dependencies pulled in during closure resolution resolve to the same SDK
    monorepo as the explicitly listed modules.
    """
    project_overrides = dict(profile.get("project_overrides") or {})
    module_overrides = dict(profile.get("module_overrides") or {})
    ref_overrides_by_project: dict[str, str] = {}
    for project, (mode, value) in project_ref_overrides.items():
        if mode != "ref":
            continue
        override = dict(project_overrides.get(project) or {})
        if not override:
            override = nsx_cli.registry_project(project) or {"name": project}
        override["revision"] = value
        project_overrides[project] = override
        ref_overrides_by_project[project] = value
    # Align per-module revisions with their owning project's ref override.
    # The starter profile pins each migrated module's ``revision`` to ``main``;
    # NSX's lock resolution honours that module-level pin over the project
    # revision, so an un-aligned module would drag the whole SDK monorepo back
    # to ``main``. Re-point every module owned by an overridden project.
    if ref_overrides_by_project:
        for name, override in list(module_overrides.items()):
            if not isinstance(override, dict):
                continue
            project = override.get("project")
            if project in ref_overrides_by_project:
                aligned = dict(override)
                aligned["revision"] = ref_overrides_by_project[project]
                module_overrides[name] = aligned
    if not project_overrides and not module_overrides:
        return ""
    registry: dict[str, Any] = {}
    if project_overrides:
        registry["projects"] = dict(project_overrides)
    if module_overrides:
        registry["modules"] = dict(module_overrides)
    return yaml.safe_dump({"module_registry": registry}, sort_keys=False, default_flow_style=False)


def _default_nsx_channel(board_channel: str, configured_channel: str | None) -> str:
    """Resolve the NSX channel for a generated app.

    An explicit config override wins. Otherwise we use the board's registered
    default so preview-only boards naturally follow the matching NSX channel.
    """
    if configured_channel is not None:
        return configured_channel
    return board_channel


def _resolve_module_specs(
    board: str,
    *,
    profile_board: str | None = None,
    registry: PlatformRegistry | None = None,
) -> list[NsxModuleSpec]:
    """Build the ordered typed module list for a profiler app.

    Module selection and ownership are both derived from the board's NSX
    starter profile.
    """
    profile = _get_starter_profile(board, profile_board=profile_board)

    ordered_names: list[str] = _starter_profile_module_names(profile)
    if (
        _needs_armv8m_pmu_module(board, profile_board=profile_board, registry=registry)
        and "nsx-pmu-armv8m" not in ordered_names
    ):
        ordered_names.append("nsx-pmu-armv8m")

    return [NsxModuleSpec(name, _module_project(name, profile)) for name in ordered_names]


def _resolve_module_list(
    board: str,
    *,
    profile_board: str | None = None,
    registry: PlatformRegistry | None = None,
) -> list[str]:
    """Backward-compatible wrapper returning only module names."""
    return [
        spec.name
        for spec in _resolve_module_specs(board, profile_board=profile_board, registry=registry)
    ]


def _resolve_project_overrides(
    module_specs: list[NsxModuleSpec],
    nsx_overrides: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Collapse module-targeted ref/version overrides onto owning projects.

    Monorepo-backed SDK modules share a project, so ref/version overrides must
    resolve coherently at the project level. ``path`` overrides remain module-
    local because they install a concrete local module directory.
    """
    by_name = {spec.name: spec for spec in module_specs}
    project_overrides: dict[str, tuple[str, str]] = {}
    for name, override in nsx_overrides.items():
        spec = by_name.get(name)
        if spec is None or override.path is not None:
            continue
        mode = "ref" if override.ref is not None else "version"
        value = override.ref if override.ref is not None else override.version
        if value is None:
            continue
        existing = project_overrides.get(spec.project)
        if existing is not None and existing != (mode, value):
            raise ConfigError(
                f"Conflicting build.nsx_modules overrides for project '{spec.project}'",
                hint=(
                    "Modules from the same NSX project must use the same ref/version. "
                    f"Conflicting module names: '{name}' and another module in '{spec.project}'."
                ),
            )
        project_overrides[spec.project] = (mode, value)
    for project, ref in _DEFAULT_MAIN_PROJECT_REFS.items():
        if project in project_overrides:
            continue
        if any(spec.project == project for spec in module_specs):
            project_overrides[project] = ("ref", ref)
    return project_overrides


def _module_names_by_project(module_specs: list[NsxModuleSpec]) -> dict[str, set[str]]:
    """Index generated module names by owning NSX project."""

    names_by_project: dict[str, set[str]] = {}
    for spec in module_specs:
        names_by_project.setdefault(spec.project, set()).add(spec.name)
    return names_by_project


def _install_local_module_override(dest: Path, source: Path) -> None:
    """Copy a local NSX module directory into the app's ``modules/`` tree.

    Validates that the source contains an ``nsx-module.yaml`` (required by
    NSX for any local module).
    """
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise FirmwareError(
            f"NSX module override path is not a directory: {source}",
            hint="Provide a directory containing nsx-module.yaml.",
        )
    if not (source / "nsx-module.yaml").is_file():
        raise FirmwareError(
            f"NSX module override at {source} is missing nsx-module.yaml",
            hint="A valid NSX module must contain nsx-module.yaml at its root.",
        )
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    log.info("Installed local module override: %s → %s", source, dest)


def _copy_local_engine_module(dest: Path, source: Path) -> None:
    """Copy a prepared local engine module into the generated app tree."""
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

@dataclass(frozen=True)
class ProjectRenderContext:
    app_dir: Path
    board: "BoardDef"
    soc: "SocDef"
    config: "ProfileConfig"
    artifacts: "EngineArtifacts"
    modules: list[dict[str, object]]
    module_registry_yaml: str
    render_context: FirmwareRenderContext
    arena_regions: list["ArenaRegion"]
    compiler_launcher: str
    channel: str
    rtt_buffer_size_up: int


def render_project_files(ctx: ProjectRenderContext) -> None:
    """Render nsx.yml, cmake/nsx/modules.cmake, and CMakeLists.txt."""
    _write_text(
        ctx.app_dir / "nsx.yml",
        _jinja_env.get_template("nsx.yml.j2").render(
            board=ctx.board.name,
            soc=ctx.soc.name,
            toolchain=ctx.config.target.toolchain,
            channel=ctx.channel,
            modules=ctx.modules,
            module_registry_yaml=ctx.module_registry_yaml,
        ),
    )

    cmake_nsx_dir = ctx.app_dir / "cmake" / "nsx"
    cmake_nsx_dir.mkdir(parents=True, exist_ok=True)
    _write_text(
        cmake_nsx_dir / "modules.cmake",
        _jinja_env.get_template("modules.cmake.j2").render(modules=ctx.modules),
    )

    _write_text(
        ctx.app_dir / "CMakeLists.txt",
        _jinja_env.get_template("CMakeLists.txt.j2").render(
            board=ctx.board.name,
            engine_type=ctx.artifacts.engine_type,
            cmake_vars=ctx.artifacts.cmake_vars,
            compiler_launcher=ctx.compiler_launcher,
            aot_cmake_target=ctx.artifacts.aot_cmake_target or "",
            transport=ctx.config.target.transport,
            toolchain=ctx.config.target.toolchain,
            rtt_buffer_size_up=ctx.rtt_buffer_size_up,
            model_location=ctx.render_context.memory.model_location,
            arena_region=ctx.render_context.memory.arena_region,
            weights_region=ctx.render_context.memory.weights_region,
            profiling_backends=list(ctx.render_context.pmu.profiling_backends),
            has_armv8m_pmu=ctx.render_context.pmu.has_armv8m_pmu,
            power_sync_enabled=ctx.render_context.sync.power_sync_enabled,
            arena_regions=ctx.arena_regions,
        ),
    )
