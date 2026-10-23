# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "helia-model-zoo @ git+https://github.com/AmbiqAI/helia-model-zoo.git@0124dea005e876b9589537180c0b1f111d115699",
#     "pyyaml>=6.0",
# ]
# ///
"""Build an ``hpx validate --models-file`` registry from pinned Zoo models.

Reads ``tools/zoo_models.yaml``, fetches each model with the
helia-model-zoo client, refuses any file whose sha256 differs from its pin,
and writes a registry that ``hpx validate --models-file`` loads:

    uv run tools/zoo_registry.py --output build/zoo/models.yaml

The script declares its own dependencies, so ``uv run`` installs the Zoo
client in an isolated environment rather than in the HPX project.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LIST = Path(__file__).resolve().with_name("zoo_models.yaml")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")

Resolver = Callable[[str], Any]


def load_model_list(path: Path) -> list[dict[str, Any]]:
    """The validated entries of a Zoo model list."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = document.get("models") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: needs a non-empty 'models' list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: each model must be a mapping")
        for key in ("zoo", "precision", "rev", "sha256", "arena_size"):
            if key not in entry:
                raise ValueError(f"{path}: model entry lacks {key!r}")
        if not _REVISION.fullmatch(str(entry["rev"])):
            raise ValueError(f"{path}: rev must be a 40-hex commit")
        if not _SHA256.fullmatch(str(entry["sha256"])):
            raise ValueError(f"{path}: sha256 must be 64 lowercase hex")
        arena = entry["arena_size"]
        if isinstance(arena, bool) or not isinstance(arena, int) or arena <= 0:
            raise ValueError(f"{path}: arena_size must be a positive integer")
        model_id = _model_id(entry)
        if model_id in seen:
            raise ValueError(f"{path}: duplicate model {model_id}")
        seen.add(model_id)
    return entries


def _model_id(entry: dict[str, Any]) -> str:
    return f"{entry['zoo']}-{entry['precision']}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_registry(entries: list[dict[str, Any]], resolve: Resolver) -> dict[str, Any]:
    """The ``--models-file`` document for these entries."""
    models: dict[str, Any] = {}
    for entry in entries:
        source = f"zoo://{entry['zoo']}/{entry['precision']}@{entry['rev']}"
        resolved = resolve(source)
        model_path = Path(resolved.model).resolve()
        actual = _sha256(model_path)
        if actual != entry["sha256"]:
            raise ValueError(f"{source}: sha256 {actual} differs from pin")
        models[_model_id(entry)] = {
            "path": str(model_path),
            "arena_size": entry["arena_size"],
            "name": f"{resolved.record.title} ({entry['precision']})",
            "family": entry["zoo"],
            "precision": entry["precision"],
            "source": source,
        }
    return {"models": models}


def _zoo_resolver(cache: Path | None) -> Resolver:
    import helia_model_zoo

    options = {} if cache is None else {"cache": cache}
    return lambda source: helia_model_zoo.resolve(source, **options)


def main(argv: list[str] | None = None, resolve: Resolver | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", type=Path, default=DEFAULT_LIST, help="Zoo model list")
    parser.add_argument("--output", type=Path, required=True, help="registry YAML to write")
    parser.add_argument("--cache", type=Path, help="Zoo download cache directory")
    args = parser.parse_args(argv)
    try:
        entries = load_model_list(args.models)
        registry = build_registry(entries, resolve or _zoo_resolver(args.cache))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"zoo_registry: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    print(f"Wrote {len(registry['models'])} models to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
