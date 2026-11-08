"""Match each configured executable's linker selection to recorded SDK inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from ..platform.linker.files import source_sha256
from ..platform.linker.model import LinkerInput, LinkerVerification
from ..results.dependencies import DependencyModule, validate_sdk_modules
from .link_inputs import validate_link_inputs

_CLIENT = "client-hpx-linker"
_SCRIPT_OPTIONS = ("-T", "--scatter=", "-Wl,-T,", "-Wl,--script=", "--script=")
_SCRIPT_FLAG = re.compile(r"(?:^|[\s,])[\"']?(?:-T|--script|--scatter)")
_RESPONSE_FLAG = re.compile(r"(?:^|[\s,])[\"']?(?:@|--via(?:[=,\s]|$))")


def request_linker_information(build_dir: Path) -> None:
    """Request backend metadata before configuration, including for reused workspaces."""
    query = build_dir / ".cmake/api/v1/query" / _CLIENT
    query.mkdir(parents=True, exist_ok=True)
    (query / "codemodel-v2").touch()


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Backend metadata is not an object: {path.name}")
    return value


def _argument(value: str, *, quoted: bool = False) -> str:
    value = value.strip()
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        value, quoted = value[1:-1], True
    if (
        not value
        or '"' in value
        or (not quoted and ("'" in value or any(c.isspace() for c in value)))
    ):
        raise ValueError("Unsupported native-shell encoding of linker script path")
    return value


def _selected_script(
    fragments: list[dict[str, Any]], build_dir: Path, products: frozenset[Path]
) -> Path:
    scripts = []
    other_inputs = []
    for entry in fragments:
        value = entry["fragment"]
        if not isinstance(value, str):
            raise ValueError("Backend link fragment is not text")
        value = value.strip()
        quoted = value.startswith(('"', "'")) and value.endswith(value[0])
        if quoted:
            value = value[1:-1]
        if _RESPONSE_FLAG.search(value):
            raise ValueError("Opaque linker response inputs have no characterized source closure")
        script_input = _SCRIPT_FLAG.search(value)
        if entry.get("role") != "flags":
            if script_input:
                raise ValueError("Additional linker script input outside supported flags")
            other_inputs.append(entry["fragment"])
            continue
        prefix = next((flag for flag in _SCRIPT_OPTIONS if value.startswith(flag)), None)
        if prefix is not None:
            argument = _argument(value[len(prefix) :], quoted=quoted)
            if prefix.startswith("-Wl,") and "," in argument:
                raise ValueError("Ambiguous forwarded linker script arguments")
            scripts.append((build_dir / argument).resolve())
        elif script_input:
            raise ValueError("Unsupported combined linker script options")
        else:
            other_inputs.append(entry["fragment"])
    if len(scripts) != 1:
        raise ValueError(f"Expected one configured linker script, found {len(scripts)}")
    validate_link_inputs(tuple(other_inputs), build_dir, products)
    return scripts[0]


def configured_script(app_dir: Path, build_dir: Path, target: str) -> Path:
    """Read the actual target link information; no requested-profile inference."""
    reply = build_dir / ".cmake/api/v1/reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise ValueError("CMake linker information is unavailable")
    references = _object(indexes[-1])["reply"][_CLIENT]
    model = _object(reply / references["codemodel-v2"]["jsonFile"])
    if (
        Path(model["paths"]["source"]).resolve() != app_dir.resolve()
        or Path(model["paths"]["build"]).resolve() != build_dir.resolve()
    ):
        raise ValueError("Configured linker information belongs to another workspace")
    configurations = model["configurations"]
    if len(configurations) != 1:
        raise ValueError("Configured linker information has ambiguous build configurations")
    targets = configurations[0]["targets"]
    matches = [entry for entry in targets if entry["name"] == target]
    if len(matches) != 1:
        raise ValueError(f"Configured executable {target!r} is unavailable or ambiguous")
    record = _object(reply / matches[0]["jsonFile"])
    products = frozenset(
        (build_dir / artifact["path"]).resolve()
        for entry in targets
        for product in (_object(reply / entry["jsonFile"]),)
        if product.get("type")
        in {"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY", "OBJECT_LIBRARY"}
        for artifact in product.get("artifacts", ())
    )
    return _selected_script(record["link"]["commandFragments"], build_dir, products)


def verify_configured_linker(
    *,
    app_dir: Path,
    build_dir: Path,
    modules: tuple[DependencyModule, ...],
    expected: LinkerInput | None,
    role: Literal["profile", "power"],
    target: str,
) -> LinkerVerification:
    """A source decision for this role/target now, never a prior target's cached verdict."""
    if expected is None:
        return LinkerVerification(
            role, target, None, "No characterized linker input for this target/profile"
        )
    try:
        script = configured_script(app_dir, build_dir, target)
        sdk_modules = validate_sdk_modules(modules, expected.sdk_commit)
        roots = {(app_dir / module.vendored_at).resolve() for module in sdk_modules}
        if len(roots) != 1:
            raise ValueError("Split SDK source roots have no single characterized input closure")
        root = roots.pop()
        if script != (root / expected.script.path).resolve():
            raise ValueError("Configured executable selects another linker script")
        for source in expected.files:
            path = root / source.path
            if source_sha256(path) != source.sha256:
                raise ValueError(f"SDK linker input content differs: {source.path}")
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        return LinkerVerification(role, target, None, str(exc))
    return LinkerVerification(role, target, expected)
