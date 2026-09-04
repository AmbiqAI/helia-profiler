"""Tests for the hardware-validation workflow contract."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "hardware-validation.yml"

# Inputs the runner now answers from its own environment. They must not come
# back as workflow inputs: a job cannot know which probe it may open.
RUNNER_OWNED_INPUTS = {"jlink_serials", "power_serials", "power_boards"}


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


@pytest.fixture(scope="module")
def validate_job(workflow: dict[Any, Any]) -> dict[str, Any]:
    return workflow["jobs"]["validate"]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found")


def test_ns_cmsis_nn_default_matches_qualified_baseline() -> None:
    baseline = json.loads(
        (
            REPO_ROOT / "src" / "helia_profiler" / "data" / "compatibility-baseline-v1.json"
        ).read_text()
    )
    workflow = WORKFLOW_PATH.read_text()
    match = re.search(
        r"^\s*HPX_QUALIFIED_NS_CMSIS_NN_REF:\s*([0-9a-f]{40})\s*$",
        workflow,
        re.MULTILINE,
    )

    assert match is not None
    assert match.group(1) == baseline["projects"]["ns-cmsis-nn"]["ref"]
    assert 'requested_ref="${HPX_QUALIFIED_NS_CMSIS_NN_REF}"' in workflow
    assert 'requested_kind="qualified_baseline"' in workflow


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 loaders (PyYAML) read the bare ``on`` key as boolean True;
    # YAML 1.2 loaders keep the string. Accept either.
    return workflow[True] if True in workflow else workflow["on"]


def test_probe_serials_are_not_workflow_inputs(workflow: dict[Any, Any]) -> None:
    inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
    assert RUNNER_OWNED_INPUTS.isdisjoint(inputs)
    assert "boards" in inputs
    assert "power" in inputs


def test_one_job_per_board_pinned_by_board_label(
    workflow: dict[Any, Any], validate_job: dict[str, Any]
) -> None:
    assert validate_job["needs"] == "plan"
    assert validate_job["strategy"]["fail-fast"] is False
    assert validate_job["strategy"]["matrix"]["board"] == (
        "${{ fromJSON(needs.plan.outputs.boards) }}"
    )
    assert validate_job["runs-on"] == ["self-hosted", "hpx-hardware", "${{ matrix.board }}"]
    # Runner exclusivity is the only serialisation: a concurrency group keyed
    # by board would throttle several runners of one board type to one job.
    assert "concurrency" not in validate_job, "board jobs must not be serialised per board"
    assert "concurrency" not in workflow, "boards must not share one concurrency group"
    assert validate_job["env"]["HPX_VALIDATION_BOARD"] == "${{ matrix.board }}"


def test_board_and_probes_come_from_the_runner(validate_job: dict[str, Any]) -> None:
    resolve = _step(validate_job, "Resolve board and probes from the runner")
    # The guard runs before anything touches the checkout or the hardware.
    assert validate_job["steps"][0] is resolve
    script = resolve["run"]
    assert '"${HPX_BOARD}" != "${HPX_VALIDATION_BOARD}"' in script
    assert "HPX_VALIDATION_JLINK_SERIALS=${HPX_BOARD}=${HPX_JLINK_SERIAL}" in script
    assert "HPX_VALIDATION_POWER_SERIALS=${HPX_BOARD}=${HPX_JOULESCOPE_SERIAL}" in script
    assert "HPX_VALIDATION_POWER=${HPX_VALIDATION_POWER_MODE}" in script
    assert "HPX_VALIDATION_POWER=off" in script

    probe_check = _step(validate_job, "Check the runner can open its probe")
    assert '--board "${HPX_VALIDATION_BOARD}"' in probe_check["run"]
    assert '--jlink-serial "${HPX_JLINK_SERIAL}"' in probe_check["run"]

    for name in ("Preview validation cases", "Run hardware validation"):
        script = _step(validate_job, name)["run"]
        assert '--boards "${HPX_VALIDATION_BOARD}"' in script
        assert '--jlink-serials "${HPX_VALIDATION_JLINK_SERIALS}"' in script
        assert '--power "${HPX_VALIDATION_POWER}"' in script
        assert "HPX_VALIDATION_BOARDS" not in script


def test_artifact_is_uploaded_per_board(validate_job: dict[str, Any]) -> None:
    upload = _step(validate_job, "Upload validation artifacts")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == (
        "hardware-validation-${{ github.run_id }}-${{ matrix.board }}"
    )


def test_plan_job_builds_matrix_from_boards_input(workflow: dict[Any, Any]) -> None:
    plan = workflow["jobs"]["plan"]
    assert plan["outputs"]["boards"] == "${{ steps.matrix.outputs.boards }}"
    assert workflow["env"]["HPX_VALIDATION_BOARDS"] == (
        "${{ inputs.boards || 'apollo510_evb,apollo330mP_evb,apollo3p_evb' }}"
    )
