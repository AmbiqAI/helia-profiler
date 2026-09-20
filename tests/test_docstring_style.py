"""heliaPROFILER docstrings are Google style, package-wide.

The docs pipeline dumps the package with ``griffe dump --docstyle google``
(one parser, no ``auto``), so a NumPy-style section is not a style preference
here: griffe parses it as prose and the parameter table for that symbol comes
out empty. #330 converted the last 11 sections; this keeps them converted.
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "helia_profiler"

# The section names NumPy style underlines. griffe's google parser wants
# "Args:" / "Returns:" and treats these as ordinary paragraph text.
_SECTIONS = (
    "Parameters",
    "Other Parameters",
    "Attributes",
    "Returns",
    "Yields",
    "Receives",
    "Raises",
    "Warns",
    "Warnings",
    "See Also",
    "Notes",
    "References",
    "Examples",
    "Methods",
)
_HEADER = re.compile(
    rf"^([ \t]*)(?:{'|'.join(_SECTIONS)})[ \t]*\n[ \t]*-{{3,}}[ \t]*$",
    re.MULTILINE,
)


def _numpy_section_headers() -> list[str]:
    found: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _HEADER.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{line}")
    return found


def test_no_numpy_style_docstring_sections() -> None:
    found = _numpy_section_headers()
    assert found == [], "NumPy-style docstring sections (convert to Google style):\n" + "\n".join(
        found
    )
