"""HPX-owned compatibility baselines and qualification provenance."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigError

BASELINE_SCHEMA = "hpx.compatibility-baseline"
BASELINE_SCHEMA_VERSION = 1
_BASELINE_RESOURCE = "compatibility-baseline-v1.json"
_REQUIRED_PROJECTS = frozenset(
    {
        "neuralspotx",
        "nsx-ambiq-sdk",
        "nsx-pmu-armv8m",
        "nsx-tflite-micro",
        "arm-cmsis-nn",
        "ns-cmsis-nn",
        "nsx-executorch",
    }
)
_REQUIRED_MODULES = frozenset(
    {
        "nsx-pmu-armv8m",
        "nsx-tflite-micro",
        "arm-cmsis-nn",
        "nsx-cmsis-nn",
        "nsx-helia-rt",
        "nsx-executorch",
    }
)


class QualificationState(StrEnum):
    """Compatibility state of a resolved profiling configuration."""

    QUALIFIED = "qualified"
    QUALIFIED_WITH_ENGINE_OVERRIDE = "qualified-with-engine-override"
    DEVELOPMENT_OVERRIDES = "development-overrides"


@dataclass(frozen=True)
class CompatibilityProject:
    """One immutable NSX project reference in a compatibility baseline."""

    name: str
    url: str
    ref: str


@dataclass(frozen=True)
class CompatibilityModule:
    """One module-to-project reference qualified by HPX."""

    name: str
    project: str
    ref: str


@dataclass(frozen=True)
class CompatibilityBaseline:
    """Validated, immutable baseline loaded from HPX package data."""

    schema: str
    schema_version: int
    baseline_id: str
    neuralspotx_package: str
    neuralspotx_version: str
    neuralspotx_sha256: str
    projects: tuple[CompatibilityProject, ...]
    modules: tuple[CompatibilityModule, ...]

    def project(self, name: str) -> CompatibilityProject:
        for project in self.projects:
            if project.name == name:
                return project
        raise ConfigError(
            f"Compatibility baseline does not define project '{name}'",
            hint="Update the HPX compatibility baseline before using this project.",
        )

    def module(self, name: str) -> CompatibilityModule:
        for module in self.modules:
            if module.name == name:
                return module
        raise ConfigError(
            f"Compatibility baseline does not define module '{name}'",
            hint="Update the HPX compatibility baseline before using this module.",
        )

    @property
    def fingerprint(self) -> str:
        """Return the canonical SHA-256 identity reserved for Stage 5."""
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-safe representation for reports and Stage 5."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "baseline_id": self.baseline_id,
            "neuralspotx": {
                "package": self.neuralspotx_package,
                "version": self.neuralspotx_version,
                "sha256": self.neuralspotx_sha256,
            },
            "projects": {
                project.name: {"url": project.url, "ref": project.ref} for project in self.projects
            },
            "modules": {
                module.name: {"project": module.project, "ref": module.ref}
                for module in self.modules
            },
        }


@dataclass(frozen=True)
class CompatibilityResolution:
    """Resolved baseline plus explicit override classification."""

    baseline: CompatibilityBaseline
    qualification: QualificationState
    # NSX *module* names, not project names — the NSX registry projects a
    # module belongs to (see baseline.project() vs baseline.module()) may
    # aggregate several modules, but an override here always targets one
    # module by name. Includes the CMSIS-NN provider module replaced through
    # engine.config selectors (cmsis_nn_path / cmsis_nn_ref / CMSIS_NN_PATH),
    # classified by what they replace rather than by the key that carried
    # them. Named distinctly from
    # firmware/project.py's unrelated `_resolve_project_overrides()` (which
    # groups module overrides up to their owning project for module_registry
    # generation) to avoid confusing the two concepts.
    module_overrides: tuple[str, ...] = ()
    engine_overrides: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return self.baseline.fingerprint

    def to_dict(self) -> dict[str, Any]:
        """Return structured result provenance without lossy enum conversion."""
        return {
            "qualification": self.qualification.value,
            "baseline_fingerprint": self.fingerprint,
            "baseline": self.baseline.to_dict(),
            "module_overrides": list(self.module_overrides),
            "engine_overrides": list(self.engine_overrides),
        }


def load_compatibility_baseline(path: Path | None = None) -> CompatibilityBaseline:
    """Load and strictly validate an HPX compatibility baseline."""
    try:
        if path is None:
            resource = importlib.resources.files("helia_profiler.data").joinpath(_BASELINE_RESOURCE)
            raw = json.loads(resource.read_text(encoding="utf-8"))
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ModuleNotFoundError) as exc:
        location = str(path) if path is not None else _BASELINE_RESOURCE
        raise ConfigError(
            f"Cannot load compatibility baseline {location}: {exc}",
            hint="Install a complete HPX package or provide valid baseline JSON.",
        ) from exc

    try:
        return _parse_baseline(raw)
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(
            f"Malformed compatibility baseline: {exc}",
            hint="Check the baseline schema, version, and required immutable refs.",
        ) from exc


# engine.config keys that redirect where an engine's source/binary comes from.
# Other keys (e.g. "variant", "linker_profile", "aot_args") are ordinary
# build knobs and do not deviate from the qualified engine baseline.
_ENGINE_SOURCE_OVERRIDE_KEYS = frozenset({"dist_path", "source_path", "source"})
# engine.config keys that replace a baseline-pinned NSX *module* (the CMSIS-NN
# provider) rather than the engine itself — see select_cmsis_nn_override().
_MODULE_SOURCE_OVERRIDE_KEYS = frozenset({"cmsis_nn_path", "cmsis_nn_ref"})

CMSIS_NN_PROVIDER_MODULES = frozenset({"arm-cmsis-nn", "nsx-cmsis-nn"})


@dataclass(frozen=True)
class CmsisNnOverride:
    """One effective CMSIS-NN provider replacement and the selector that carried it."""

    module: str  # NSX module replaced ("arm-cmsis-nn" or "nsx-cmsis-nn")
    mode: str  # "path" or "ref"
    selector: (
        str  # "engine.config.cmsis_nn_path" / "engine.config.cmsis_nn_ref" / "env.CMSIS_NN_PATH"
    )
    requested: Any  # the raw configured value; callers validate its type


def cmsis_nn_provider_module(engine_type: str, engine_backend: str | None) -> str:
    """The CMSIS-NN provider module an engine builds, from config alone.

    The helia engines always build AmbiqAI's ``nsx-cmsis-nn``. ExecuTorch
    declares exactly one provider: ``engine.backend`` ``"ns"`` selects
    ``nsx-cmsis-nn``, anything else (including an unset backend, ExecuTorch's
    ``arm`` default) selects ``arm-cmsis-nn``. A PTE sidecar can still flip an
    unset backend to ``ns`` at prepare-engine time; the dependency
    provenance, which sees the prepared artifacts, is authoritative there.
    Engines that never consume the selectors (TFLM) still get a name so a
    stray selector stamps conservatively rather than being ignored.
    """
    if engine_type == "executorch" and engine_backend != "ns":
        return "arm-cmsis-nn"
    return "nsx-cmsis-nn"


def select_cmsis_nn_override(engine_config: Any, *, provider_module: str) -> CmsisNnOverride | None:
    """Return the CMSIS-NN selector that actually takes effect, if any.

    The single precedence the engine adapters build from (they validate it via
    ``engines.cmsis_nn.resolve_cmsis_nn_selector``): an explicit
    ``cmsis_nn_path`` wins, then ``cmsis_nn_ref``, then the ``CMSIS_NN_PATH``
    environment fallback — which only reaches ``nsx-cmsis-nn`` builds;
    ExecuTorch's ``arm`` provider never reads it. Explicit config beats the
    environment so a resolved commit stays a git-backed lock entry rather
    than an unversioned local module. Empty values are not overrides.
    """
    config = engine_config if isinstance(engine_config, Mapping) else {}
    path = config.get("cmsis_nn_path")
    if path:
        return CmsisNnOverride(provider_module, "path", "engine.config.cmsis_nn_path", path)
    ref = config.get("cmsis_nn_ref")
    if ref:
        return CmsisNnOverride(provider_module, "ref", "engine.config.cmsis_nn_ref", ref)
    env_path = os.environ.get("CMSIS_NN_PATH")
    if env_path and provider_module == "nsx-cmsis-nn":
        return CmsisNnOverride(provider_module, "path", "env.CMSIS_NN_PATH", env_path)
    return None


# NSX module names that engine adapters resolve themselves (via
# engine.config's dist_path/source_path/source/cmsis_nn_path/cmsis_nn_ref, not build.nsx_modules).
# Mirrors the canonical engine module constants — a test
# asserts these literals never drift from those constants. A build.nsx_modules
# entry targeting one of these names is never applied (see
# firmware/__init__.py), so it must not be counted as a development override.
ENGINE_OWNED_MODULE_NAMES = frozenset({"nsx-helia-rt", "nsx-cmsis-nn", "nsx-executorch"})


def resolve_compatibility(
    baseline: CompatibilityBaseline,
    *,
    module_overrides: Mapping[str, Any],
    engine_config: Any,
    engine_config_path: Path | None,
    engine_type: str,
    engine_backend: str | None,
) -> CompatibilityResolution:
    """Classify explicit module and engine overrides without mutating config.

    Overrides are bucketed by what they replace, not by the key that carried
    them: a CMSIS-NN selector replaces a baseline-pinned module, so it lands
    in ``module_overrides`` under the provider module's name and stamps
    ``development-overrides`` exactly like the ``build.nsx_modules`` form.
    """
    modules = {str(name) for name in module_overrides if str(name) not in ENGINE_OWNED_MODULE_NAMES}
    engines: set[str] = set()
    if engine_config_path is not None:
        # The file's contents aren't parsed here, so treat any use of an
        # engine config file conservatively as a possible source override.
        engines.add("engine.config_path")
    if isinstance(engine_config, Mapping):
        # Empty values are not overrides: the adapters gate on truthiness and
        # fall back to the baseline, so the stamp must too.
        engines.update(
            f"engine.config.{key}"
            for key in sorted(engine_config)
            if key in _ENGINE_SOURCE_OVERRIDE_KEYS and engine_config[key]
        )
    cmsis_nn = select_cmsis_nn_override(
        engine_config,
        provider_module=cmsis_nn_provider_module(engine_type, engine_backend),
    )
    if cmsis_nn is not None:
        modules.add(cmsis_nn.module)
    for variable in ("HELIART_DIST_PATH", "HELIART_SOURCE_PATH"):
        if os.environ.get(variable):
            engines.add(f"env.{variable}")

    if modules:
        qualification = QualificationState.DEVELOPMENT_OVERRIDES
    elif engines:
        qualification = QualificationState.QUALIFIED_WITH_ENGINE_OVERRIDE
    else:
        qualification = QualificationState.QUALIFIED
    return CompatibilityResolution(
        baseline=baseline,
        qualification=qualification,
        module_overrides=tuple(sorted(modules)),
        engine_overrides=tuple(sorted(engines)),
    )


def _parse_baseline(raw: Any) -> CompatibilityBaseline:
    if not isinstance(raw, dict):
        raise ConfigError("Compatibility baseline must contain a JSON object.")
    if raw.get("schema") != BASELINE_SCHEMA:
        raise ConfigError(f"Unsupported compatibility baseline schema: {raw.get('schema')!r}")
    if raw.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported compatibility baseline version: {raw.get('schema_version')!r}",
            hint=f"This HPX version supports baseline v{BASELINE_SCHEMA_VERSION}.",
        )

    neuralspotx = _mapping(raw, "neuralspotx")
    package = _string(neuralspotx, "package")
    version = _string(neuralspotx, "version")
    package_sha256 = _string(neuralspotx, "sha256")
    if package != "neuralspotx" or not _is_sha256(package_sha256):
        raise ConfigError("Compatibility baseline has an invalid neuralspotx package identity.")

    projects = tuple(
        CompatibilityProject(
            name=name,
            url=_string(entry, "url"),
            ref=_immutable_ref(entry, "ref", f"project '{name}'"),
        )
        for name, value in _mapping(raw, "projects").items()
        for entry in (_entry(value, f"project '{name}'"),)
    )
    project_names = {project.name for project in projects}
    missing = sorted(_REQUIRED_PROJECTS - project_names)
    if missing:
        raise ConfigError(
            "Compatibility baseline is missing required projects: " + ", ".join(missing)
        )

    modules = tuple(
        CompatibilityModule(
            name=name,
            project=_string(entry, "project"),
            ref=_immutable_ref(entry, "ref", f"module '{name}'"),
        )
        for name, value in _mapping(raw, "modules").items()
        for entry in (_entry(value, f"module '{name}'"),)
    )
    missing_modules = sorted(_REQUIRED_MODULES - {module.name for module in modules})
    if missing_modules:
        raise ConfigError(
            "Compatibility baseline is missing required modules: " + ", ".join(missing_modules)
        )
    for module in modules:
        if module.project not in project_names:
            raise ConfigError(
                f"Compatibility baseline module '{module.name}' references unknown "
                f"project '{module.project}'"
            )

    return CompatibilityBaseline(
        schema=BASELINE_SCHEMA,
        schema_version=BASELINE_SCHEMA_VERSION,
        baseline_id=_string(raw, "baseline_id"),
        neuralspotx_package=package,
        neuralspotx_version=version,
        neuralspotx_sha256=package_sha256,
        projects=projects,
        modules=modules,
    )


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping) or not result:
        raise ConfigError(f"Compatibility baseline field '{key}' must be a non-empty object.")
    return result


def _entry(value: Any, owner: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"Compatibility baseline {owner} must be an object.")
    return value


def _string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ConfigError(f"Compatibility baseline field '{key}' must be a non-empty string.")
    return result


_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _immutable_ref(value: Mapping[str, Any], key: str, owner: str) -> str:
    """Validate that a baseline ref is an immutable, qualifiable pin.

    Accepts only a full 40-character commit SHA. Tags and branches are both
    mutable names, so preserving either in a qualified baseline would make
    the baseline's identity dependent on remote repository state.

    ``fullmatch`` (rather than ``match`` with a ``$`` anchor) is used
    deliberately: ``$`` matches just before a trailing newline, which would
    otherwise let a ref like ``"v1.2.3\\n"`` slip through as immutable.
    """
    ref = _string(value, key)
    if _COMMIT_SHA_RE.fullmatch(ref):
        return ref
    raise ConfigError(
        f"Compatibility baseline {owner} must use a full 40-character commit SHA, not {ref!r}."
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
