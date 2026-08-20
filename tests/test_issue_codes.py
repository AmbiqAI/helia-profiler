"""Contract tests for the issue-code registry (#154 Phase 1).

The registry in ``results/issues.py`` is the single declaration of every
machine-readable code HPX can emit. These tests pin three properties:

* **Coverage** — every enum member has a spec, every spec an enum member.
* **Wire format** — enum members and family constructors serialize to exactly
  the strings shipped before the registry existed, including the doubled
  ``metric.power_power_…`` prefix, which is frozen until a deliberate
  wire-format change.
* **Emission discipline** — a code cannot be constructed at a severity its
  spec does not allow, and no bare registered-code literal survives in
  ``src/`` outside the registry module itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from helia_profiler.errors import ReportError
from helia_profiler.evaluation import validity
from helia_profiler.results.issues import (
    COMPARABILITY_FAMILIES,
    COMPARABILITY_REGISTRY,
    DIMENSION_DIFFERS,
    ISSUE_REGISTRY,
    POWER_DIMENSION_MISMATCH,
    ComparabilityCode,
    ComparisonDimension,
    IssueCode,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "helia_profiler"


def test_every_issue_code_has_a_spec_and_vice_versa():
    assert set(ISSUE_REGISTRY) == set(IssueCode)


def test_every_comparability_code_has_a_spec_and_vice_versa():
    assert set(COMPARABILITY_REGISTRY) == set(ComparabilityCode)


def test_issue_code_members_serialize_to_their_wire_strings():
    for code in IssueCode:
        assert json.dumps(code) == json.dumps(code.value)
        assert str(code) == code.value


def test_family_codes_pin_the_shipped_wire_format():
    # The doubled power_ prefix is the shipped format; "fixing" it here would
    # silently break every stored artifact and downstream consumer.
    assert (
        POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_SCOPE)
        == "metric.power_power_scope_mismatch"
    )
    assert (
        POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_LOCKSTEP)
        == "metric.power_power_lockstep_mismatch"
    )
    assert (
        DIMENSION_DIFFERS.code_for(ComparisonDimension.HPX_VERSION)
        == "dimension.hpx_version_differs"
    )


def test_family_rejects_foreign_dimension():
    with pytest.raises(ReportError):
        POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.HPX_VERSION)
    with pytest.raises(ReportError):
        DIMENSION_DIFFERS.code_for(ComparisonDimension.POWER_SCOPE)


def test_dimension_members_interoperate_with_str_keyed_dicts():
    # comparability._dimensions() builds str-keyed dicts; enum members must
    # look up transparently.
    data = {"power_scope": "gpio_gated_clean_window", "hpx_version": "0.1.7"}
    assert data.get(ComparisonDimension.POWER_SCOPE) == "gpio_gated_clean_window"
    assert hash(ComparisonDimension.HPX_VERSION) == hash("hpx_version")


def test_emitting_outside_the_severity_envelope_raises():
    # pmu.missing is declared error-only; a warning emission is a programming
    # bug and must fail at construction, not ship in an artifact.
    with pytest.raises(ReportError):
        validity._warning(IssueCode.PMU_MISSING, "should not construct")
    with pytest.raises(ReportError):
        validity._error(IssueCode.POWER_OBSERVATION_DEGRADED, "should not construct")


def test_mode_dependent_codes_allow_both_severities():
    for code in (IssueCode.POWER_WINDOW_CLOCK_FROZEN, IssueCode.POWER_ON_DEVICE_OVERFLOW):
        spec = ISSUE_REGISTRY[code]
        assert spec.mode_dependent
        assert spec.allowed_severities() == {"error", "warning"}
        assert validity._error(code, "m").severity == "error"
        assert validity._warning(code, "m").severity == "warning"


def test_emitted_issue_code_is_a_plain_string():
    # ResultIssue.code stays str for cross-version bundle tolerance; the
    # factory coerces the enum member so artifacts and reprs are unchanged.
    issue = validity._error(IssueCode.PMU_MISSING, "m")
    assert type(issue.code) is str
    assert issue.code == "pmu.missing"


def test_no_bare_registered_code_literal_survives_in_src():
    """The acceptance criterion of #154 Phase 1, as a test.

    Every registered code string (static and family-generated) must appear as
    a quoted literal only inside the registry module. Prose mentions in
    comments and docstrings are fine; quoted literals are not. Matching the
    known code set exactly avoids false positives from lookalike dotted names
    (compare.py's metric field names such as ``power.avg_current_a``).
    """
    codes = {c.value for c in IssueCode} | {c.value for c in ComparabilityCode}
    for family in COMPARABILITY_FAMILIES:
        codes |= {family.code_for(dim) for dim in family.dimensions}

    pattern = re.compile(
        "|".join(f'"{re.escape(code)}"' for code in sorted(codes))
    )
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "results" / "issues.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Bare issue-code literals found in src/ — use the enums from "
        "results/issues.py:\n" + "\n".join(offenders)
    )
