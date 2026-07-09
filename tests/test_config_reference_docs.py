"""Guards docs/reference/configuration.md against drifting from the config
models it is generated from.

Run ``uv run python tools/gen_config_reference.py`` and commit the result
whenever this test fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "gen_config_reference.py"
DOCS_PATH = ROOT / "docs" / "reference" / "configuration.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_config_reference", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec so dataclasses/typing.get_type_hints can resolve
    # postponed (`from __future__ import annotations`) type hints against
    # this module's namespace while it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_configuration_reference_matches_generator():
    generator = _load_generator()
    expected = generator.render()

    assert DOCS_PATH.is_file(), (
        f"{DOCS_PATH} is missing. Generate it with: "
        "uv run python tools/gen_config_reference.py"
    )
    actual = DOCS_PATH.read_text(encoding="utf-8")
    # Tolerate CRLF checkouts on Windows (git core.autocrlf); the semantic
    # content is what must match, not the platform line endings.
    actual = actual.replace("\r\n", "\n")

    assert actual == expected, (
        "docs/reference/configuration.md is stale relative to the ProfileConfig models. "
        "Regenerate it with: uv run python tools/gen_config_reference.py"
    )


def test_generator_render_is_deterministic():
    generator = _load_generator()
    assert generator.render() == generator.render()


def teardown_module(module) -> None:  # noqa: ARG001 - pytest hook signature
    sys.modules.pop("gen_config_reference", None)
