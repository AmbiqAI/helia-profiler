"""Guards against API-reference drift.

``helia_profiler.__all__`` is the public API surface. Every name in it must
be documented on exactly one page under ``docs/reference/api/`` -- either via
an mkdocstrings ``:::`` directive (``::: helia_profiler.<Name>``) on one of
the sub-pages, or, for the one name that isn't a class/function
(``__version__``), an explicit mention in prose on the overview page
(``index.md``, which is otherwise excluded from this check since it links to
every other page). If this test fails after adding a new export, add a
``:::`` directive (or explicit mention) for it to the appropriate page in
``docs/reference/api/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import helia_profiler

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DOCS_DIR = REPO_ROOT / "docs" / "reference" / "api"

# mkdocstrings directive lines look like "::: helia_profiler.SomeName"
DIRECTIVE_RE = re.compile(r"^:::\s+helia_profiler\.(\w+)\s*$", re.MULTILINE)


def _collect_documented_names() -> dict[str, set[str]]:
    """Map each API doc page to the set of names it documents.

    A name counts as documented on a page if it appears in a ``:::``
    directive. ``docs/reference/api/index.md`` is an overview/landing page
    that links to the other pages and is excluded from this collection, so
    it can freely reference other pages' names without being mistaken for
    their canonical documentation page.
    """
    documented: dict[str, set[str]] = {}
    for md_file in sorted(API_DOCS_DIR.glob("*.md")):
        if md_file.name == "index.md":
            continue
        text = md_file.read_text(encoding="utf-8")
        documented[md_file.name] = set(DIRECTIVE_RE.findall(text))
    return documented


def _names_without_directives() -> set[str]:
    """Public names that have no ``:::`` directive anywhere (e.g. ``__version__``)."""
    documented_via_directive: set[str] = set()
    for names in _collect_documented_names().values():
        documented_via_directive.update(names)
    return {name for name in helia_profiler.__all__ if name not in documented_via_directive}


def test_every_public_name_is_documented_on_exactly_one_page() -> None:
    documented = _collect_documented_names()

    counts: dict[str, int] = {name: 0 for name in helia_profiler.__all__}
    pages_by_name: dict[str, list[str]] = {name: [] for name in helia_profiler.__all__}
    for page, names in documented.items():
        for name in names:
            if name in counts:
                counts[name] += 1
                pages_by_name[name].append(page)

    # Names with no ::: directive (e.g. __version__) must at least be
    # explicitly mentioned in prose on the overview page.
    overview = API_DOCS_DIR / "index.md"
    overview_text = overview.read_text(encoding="utf-8") if overview.is_file() else ""
    for name in _names_without_directives():
        if f"helia_profiler.{name}" in overview_text or f"`{name}`" in overview_text:
            counts[name] += 1
            pages_by_name[name].append(overview.name)

    missing = [name for name, count in counts.items() if count == 0]
    assert not missing, (
        f"These public names from helia_profiler.__all__ are not documented "
        f"on any page under docs/reference/api/: {missing}. Add a ':::' "
        f"directive (or explicit mention) for each."
    )

    duplicated = {name: pages for name, pages in pages_by_name.items() if len(pages) > 1}
    assert not duplicated, (
        f"These public names are documented on more than one API reference "
        f"page (expected exactly one): {duplicated}"
    )


def test_api_reference_directory_exists() -> None:
    assert API_DOCS_DIR.is_dir()
    assert list(API_DOCS_DIR.glob("*.md")), "expected at least one api reference page"
