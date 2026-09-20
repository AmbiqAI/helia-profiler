"""Guards the docs-site extractors in ``tools/docs``.

The extractors produce the published CLI, configuration and issue-code
reference. Their output is compared against the committed artifacts by
``docs.yml``, not here: that comparison needs the locked ``--isolated --no-dev``
environment so the recorded typer, click and pydantic versions are the
package's pins. What this module holds is the part that is true in any
environment, and the two failures that would otherwise pass silently.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DOCS = ROOT / "tools" / "docs"
MODULES = ("_common", "source_audit", "extract_cli", "extract_schema", "extract_issues")

TREE = "0" * 40


def _load(name):
    """Import a docs extractor by path, the way the docs build runs them.

    ``tools/`` is build tooling and not a package: it is outside ``src`` so it
    cannot reach the wheel, and putting it on the import path statically would
    put six unresolvable module names in front of the type checker on every
    run. The extractors import each other by bare name, so the directory does
    go on ``sys.path`` while they load.
    """
    if str(TOOLS_DOCS) not in sys.path:
        sys.path.insert(0, str(TOOLS_DOCS))
    spec = importlib.util.spec_from_file_location(name, TOOLS_DOCS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_common = _load("_common")
source_audit = _load("source_audit")
extract_cli = _load("extract_cli")
extract_schema = _load("extract_schema")
extract_issues = _load("extract_issues")
check_reference = _load("check_reference")


@pytest.fixture(scope="module")
def cli_payload():
    return extract_cli.build(TREE)


def test_the_walk_finds_every_group_and_leaf(cli_payload):
    """Typer vendors click, so a naive walk finds one leaf and no groups.

    ``isinstance(command, click.Group)`` against the top-level ``click`` is
    always False here because the objects typer builds are instances of its
    vendored copy. Group detection is duck-typed on ``.commands``; if that
    regresses the counts collapse and this is where it shows.
    """
    counts = cli_payload["counts"]
    assert counts["groups"] == 5, counts["group_names"]
    assert counts["leaf_commands"] == 14, counts["leaf_command_names"]
    assert counts["top_level_entries"] == 12
    assert counts["options"] == 102
    assert counts["arguments"] == 4


def test_every_command_is_declared_in_the_source(cli_payload):
    """The AST audit is an independent second opinion on the click walk.

    It compares parameter name sets rather than counts: two commands with the
    same number of options and different options agree on a count and disagree
    on everything a reader cares about.
    """
    assert check_reference.cross_check_source(cli_payload) == []


def test_the_audit_refuses_to_pass_on_nothing(monkeypatch, tmp_path):
    """A glob over a directory that moved audits nothing and agrees."""
    monkeypatch.setattr(source_audit, "CLI_DIR", tmp_path / "gone")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        source_audit.audit()

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(source_audit, "CLI_DIR", empty)
    with pytest.raises(FileNotFoundError, match="no Python modules"):
        source_audit.audit()


def test_no_command_is_documented_from_outside_the_package(cli_payload):
    """Typer wraps every callback, and the wrapper lives in site-packages.

    Reading the source location off the wrapper records the absolute path of
    whichever virtualenv produced the artifact, which is both wrong and not
    portable.
    """

    def walk(node):
        yield node
        for child in node.get("commands", []):
            yield from walk(child)

    for node in walk(cli_payload["root"]):
        source = node["source"]
        assert source is not None, node["path"]
        assert source["path"].startswith("src/helia_profiler/"), source["path"]


def test_every_artifact_records_what_produced_it():
    for payload in (
        extract_cli.build(TREE),
        extract_schema.build(TREE),
        extract_issues.build(TREE),
    ):
        recorded = payload["generatedFrom"]
        assert recorded["sourceTree"] == TREE
        assert "sourceCommit" not in recorded
        for tool in ("typer", "click", "pydantic"):
            assert recorded[tool]


def test_provenance_has_to_be_a_source_tree():
    """An artifact with no provenance claims a source it cannot name."""
    with pytest.raises(ValueError, match="40-hex git tree"):
        _common.source_tree("main")
    with pytest.raises(ValueError, match="40-hex git tree"):
        _common.source_tree("")


def test_writing_without_provenance_fails_loudly(tmp_path):
    with pytest.raises(SystemExit) as raised:
        check_reference.main(["--write", "--data-dir", str(tmp_path)])
    assert raised.value.code == 2


def test_the_configuration_walk_records_what_it_could_not_reach():
    """``MonitorBoardPreset`` is a lookup-table value type, not a field.

    Nothing walking down from ``ProfileConfig`` finds it, so the extractor
    enumerates the classes and records the gap rather than publishing a
    configuration reference that is quietly missing a type.
    """
    payload = extract_schema.build(TREE)
    counts = payload["counts"]
    assert counts["classes"] == 15
    assert counts["declaredFields"] == 106
    assert counts["schemaProperties"] == 106
    assert counts["unreachableFromRoot"] == ["MonitorBoardPreset"]
    assert payload["x-hpx"]["fieldIndex"]["MonitorBoardPreset"]


def test_the_issue_families_are_published_as_their_wire_codes():
    """``metric.power_<dimension>_mismatch`` doubles the ``power`` prefix.

    A reader expanding the pattern by hand writes a code that is never
    emitted, so the artifact carries what ``code_for`` produces.
    """
    payload = extract_issues.build(TREE)
    assert payload["counts"]["issues"] == 27
    assert payload["counts"]["comparability"] == 8
    assert payload["counts"]["families"] == 3
    power = next(
        family for family in payload["families"] if family["pattern"].startswith("metric.power_")
    )
    assert "metric.power_power_scope_mismatch" in power["codes"]
    assert len(power["codes"]) == len(power["dimensions"])


def test_the_drift_check_names_what_changed(cli_payload):
    """A drift is reported as the command and the option, not as a diff."""
    mutated = extract_cli.build(TREE)
    analyze = next(node for node in mutated["root"]["commands"] if node["name"] == "analyze")
    analyze["options"][0]["default"] = "drifted"
    problems = check_reference.diff_cli(mutated, cli_payload)
    assert any("hpx analyze" in problem and "default" in problem for problem in problems)


def teardown_module(module) -> None:  # noqa: ARG001 - pytest hook signature
    for name in (*MODULES, "check_reference"):
        sys.modules.pop(name, None)
    if str(TOOLS_DOCS) in sys.path:
        sys.path.remove(str(TOOLS_DOCS))
