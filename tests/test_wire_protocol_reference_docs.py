"""Guards docs/reference/wire-protocol.md against drifting from the registry
it is generated from.

Run ``uv run python tools/gen_wire_protocol_reference.py`` and commit the
result whenever this test fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "gen_wire_protocol_reference.py"
DOCS_PATH = ROOT / "docs" / "reference" / "wire-protocol.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_wire_protocol_reference", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_wire_protocol_reference_matches_generator():
    generator = _load_generator()
    expected = generator.render()

    assert DOCS_PATH.is_file(), (
        f"{DOCS_PATH} is missing. Generate it with: "
        "uv run python tools/gen_wire_protocol_reference.py"
    )
    actual = DOCS_PATH.read_text(encoding="utf-8")
    # Tolerate CRLF checkouts on Windows (git core.autocrlf); the semantic
    # content is what must match, not the platform line endings.
    actual = actual.replace("\r\n", "\n")

    assert actual == expected, (
        "docs/reference/wire-protocol.md is stale relative to the wire "
        "registry. Regenerate it with: "
        "uv run python tools/gen_wire_protocol_reference.py"
    )


def test_generator_render_is_deterministic():
    generator = _load_generator()
    assert generator.render() == generator.render()


def test_every_declared_token_reaches_the_page():
    """The page is the protocol's only public description; nothing may be
    documented in the registry and missing from it."""
    from helia_profiler.wire import WIRE_REGISTRY

    page = DOCS_PATH.read_text(encoding="utf-8")
    missing = [token for token in WIRE_REGISTRY if f"`{token}`" not in page]
    assert not missing, f"tokens absent from the generated reference: {missing}"


def teardown_module(module) -> None:  # noqa: ARG001 - pytest hook signature
    sys.modules.pop("gen_wire_protocol_reference", None)
