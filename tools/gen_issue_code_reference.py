"""Generate docs/reference/issue-codes.md from the issue-code registry.

The generated page is the authoritative catalogue of every machine-readable
issue code HPX can emit: it is derived mechanically from
``helia_profiler.results.issues`` so it can never drift from the emitters.
Regenerate after any registry change:

    uv run python tools/gen_issue_code_reference.py

``tests/test_issue_code_reference_docs.py`` fails CI if the committed page is
stale relative to the registry.
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.results.issues import (
    COMPARABILITY_FAMILIES,
    COMPARABILITY_REGISTRY,
    ISSUE_REGISTRY,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "docs" / "reference" / "issue-codes.md"

_HEADER = """\
# Issue codes

<!-- GENERATED FILE — do not edit by hand.
     Source: src/helia_profiler/results/issues.py
     Regenerate: uv run python tools/gen_issue_code_reference.py -->

Every machine-readable diagnostic code HPX can emit, generated from the
registry in `helia_profiler.results.issues`.

## Run-validity issues

Emitted by run evaluation into `summary.json` (`issues[]`) and
`result_manifest.json`. Severity `error` makes the run **invalid**; `warning`
makes it **degraded**. Two codes are mode-dependent: whether the broken
number is the measurement of record decides fatal-vs-warn.
"""

_COMPARABILITY_HEADER = """\
## Comparability issues

Emitted by `hpx compare` when assessing whether two runs may be compared.
Severity governs scope: `blocking` stops the whole comparison,
`layer_blocking` omits per-layer deltas, `metric_blocking` omits one metric
group, and `informative` annotates without blocking anything.
"""

_FAMILY_HEADER = """\
### Parameterized families

These codes embed a comparison dimension name. The `metric.power_…` family
doubles the `power` prefix because the dimension names themselves start with
`power_`; that is the shipped wire format, pinned until a deliberate
wire-format change renames it.
"""


def _severity_cell(spec) -> str:
    if spec.mode_dependent:
        return (
            f"`{spec.internal_severity}` (internal) / "
            f"`{spec.external_severity}` (external)"
        )
    return f"`{spec.severity}`"


def render() -> str:
    lines: list[str] = [_HEADER]
    lines.append("| Code | Severity | Description |")
    lines.append("| --- | --- | --- |")
    for code in sorted(ISSUE_REGISTRY):
        spec = ISSUE_REGISTRY[code]
        lines.append(f"| `{code.value}` | {_severity_cell(spec)} | {spec.description} |")
    lines.append("")
    lines.append(_COMPARABILITY_HEADER)
    lines.append("| Code | Severity | Description |")
    lines.append("| --- | --- | --- |")
    for code in sorted(COMPARABILITY_REGISTRY):
        spec = COMPARABILITY_REGISTRY[code]
        lines.append(f"| `{code.value}` | `{spec.severity.value}` | {spec.description} |")
    lines.append("")
    lines.append(_FAMILY_HEADER)
    for family in COMPARABILITY_FAMILIES:
        dims = ", ".join(f"`{dim.value}`" for dim in family.dimensions)
        lines.append(f"- **`{family.pattern}`** (`{family.severity.value}`) — {family.description}")
        lines.append(f"  Dimensions: {dims}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
