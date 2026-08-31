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
    MEMORY_DIMENSION_MISMATCH, POWER_DIMENSION_MISMATCH,
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


def test_family_membership_and_order_are_the_documented_sets():
    # Families derive from DIMENSION_REGISTRY, so asserting them against the
    # derivation would be X == X. These literals are the independent pin:
    # membership, effect-class bucketing, AND order (family order reaches the
    # emitted-issue order in compare artifacts). A dimension classified into
    # the wrong effect fails here even though every derivation still holds.
    assert [d.value for d in POWER_DIMENSION_MISMATCH.dimensions] == [
        "power_scope",
        "power_mode",
        "power_firmware",
        "power_monitor",
        "power_lockstep",
        "power_clean_window_probe",
        "power_firmware_fingerprint",
    ]
    assert [d.value for d in DIMENSION_DIFFERS.dimensions] == [
        "hpx_version",
        "engine",
        "board",
        "soc",
        "cpu_clock",
        "toolchain",
        "compiler_version",
        "system_clock_hz",
        "run_summary_schema_version",
        "run_metadata_schema_version",
        "transport",
        "arena_location",
        "weights_location",
        # #193: appended, never inserted -- family order is emitted-issue
        # order and existing positions are frozen shipped behavior.
        "engine_version",
    ]
    # #206: the first non-power metric group, its own family and wire prefix.
    assert [d.value for d in MEMORY_DIMENSION_MISMATCH.dimensions] == ["link_family"]
    assert MEMORY_DIMENSION_MISMATCH.code_for(ComparisonDimension.LINK_FAMILY) == (
        "metric.memory_link_family_mismatch"
    )
    assert MEMORY_DIMENSION_MISMATCH.metric_group == "memory"
    # The remaining enum members are exactly the two non-family classes.
    non_family = (
        set(ComparisonDimension)
        - set(POWER_DIMENSION_MISMATCH.dimensions)
        - set(MEMORY_DIMENSION_MISMATCH.dimensions)
        - set(DIMENSION_DIFFERS.dimensions)
    )
    assert {d.value for d in non_family} == {"model_sha256", "power_integrity"}


def test_family_metric_group_is_the_registry_group_of_its_dimensions():
    """#213 lens 1: a family literal ``metric_group="memory"`` would pass the
    membership census while its specs said something else. Pin both ways."""
    from helia_profiler.results.dimensions import DIMENSION_REGISTRY, uniform_metric_group
    from helia_profiler.results.issues import COMPARABILITY_FAMILIES

    for family in COMPARABILITY_FAMILIES:
        assert family.metric_group == uniform_metric_group(family.dimensions), family
        assert all(
            DIMENSION_REGISTRY[d].metric_group == family.metric_group for d in family.dimensions
        ), family


def test_family_rejects_foreign_dimension():
    with pytest.raises(ReportError):
        POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.HPX_VERSION)
    with pytest.raises(ReportError):
        DIMENSION_DIFFERS.code_for(ComparisonDimension.POWER_SCOPE)


def test_dimension_members_interoperate_with_str_keyed_dicts():
    # comparability.read_dimensions() builds str-keyed dicts; enum members must
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

    # Match either quote style. A composed string (f"power.{name}") is beyond
    # a literal scan by construction; the emitter chokepoints and enum-typed
    # factory signatures are the guard for that shape.
    pattern = re.compile(
        "|".join(f"['\"]{re.escape(code)}['\"]" for code in sorted(codes))
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
