"""Tests for the hardware-validation workflow contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
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


def test_ns_cmsis_nn_resolution_requires_input(
    workflow: dict[Any, Any], validate_job: dict[str, Any]
) -> None:
    inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
    assert inputs["ns_cmsis_nn_ref"]["default"] == ""
    assert workflow["env"]["HPX_NS_CMSIS_NN_REF"] == "${{ inputs.ns_cmsis_nn_ref || '' }}"
    resolve = _step(validate_job, "Resolve ns-cmsis-nn source")
    assert resolve["if"] == "env.HPX_NS_CMSIS_NN_REF != ''"
    assert 'requested_ref="${HPX_NS_CMSIS_NN_REF}"' in resolve["run"]
    assert "HPX_QUALIFIED_NS_CMSIS_NN_REF" not in WORKFLOW_PATH.read_text()
    assert "qualified_baseline" not in resolve["run"]


def test_workflow_defaults_to_ns_provider_only(workflow: dict[Any, Any]) -> None:
    inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
    assert inputs["executorch_backends"]["default"] == "ns"
    assert set(inputs["executorch_backends"]["options"]) == {"ns", "arm", "both"}
    assert workflow["env"]["HPX_VALIDATION_EXECUTORCH_BACKENDS"] == (
        "${{ inputs.executorch_backends || 'ns' }}"
    )


def _run_bash(script: str, env: dict[str, str], cwd: Path) -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("workflow script tests require bash")
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-euo", "pipefail", "-c", script],
        env={"PATH": os.environ["PATH"], **env},
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize("step_name", ["Preview validation cases", "Run hardware validation"])
@pytest.mark.parametrize("requested_ref", ["", "candidate-branch", "a" * 40])
@pytest.mark.parametrize("provider", ["ns", "arm", "both"])
def test_ns_cmsis_nn_flag_only_passed_for_explicit_input(
    workflow: dict[Any, Any],
    validate_job: dict[str, Any],
    tmp_path: Path,
    step_name: str,
    requested_ref: str,
    provider: str,
) -> None:
    env = {key: "" for key in workflow["env"] if key.startswith("HPX_VALIDATION_")}
    env.update(
        HPX_VALIDATION_BOARD="apollo510_evb",
        HPX_VALIDATION_JLINK_SERIALS="",
        HPX_VALIDATION_POWER="off",
        HPX_VALIDATION_POWER_BOARDS="",
        HPX_VALIDATION_POWER_SERIALS="",
        HPX_VALIDATION_EXECUTORCH_BACKENDS=provider,
        HPX_NS_CMSIS_NN_REF=requested_ref,
    )
    if requested_ref:
        env["NS_CMSIS_NN_RESOLVED_COMMIT"] = "b" * 40
    script = 'uv() { printf "%s\\n" "$@"; }\n' + _step(validate_job, step_name)["run"]
    args = _run_bash(script, env, tmp_path).splitlines()
    assert args[:3] == ["run", "hpx", "validate"]
    assert args[args.index("--executorch-backends") + 1] == provider
    if requested_ref:
        assert args.count("--ns-cmsis-nn-ref") == 1
        assert args[args.index("--ns-cmsis-nn-ref") + 1] == env["NS_CMSIS_NN_RESOLVED_COMMIT"]
    else:
        assert "--ns-cmsis-nn-ref" not in args


@pytest.mark.parametrize("has_override", [False, True])
def test_executorch_provenance_with_optional_ns_override(
    workflow: dict[Any, Any],
    validate_job: dict[str, Any],
    tmp_path: Path,
    has_override: bool,
) -> None:
    if shutil.which("jq") is None:
        pytest.skip("workflow provenance requires jq")
    env = {
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "env"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "HPX_NSX_EXECUTORCH_REF": workflow["env"]["HPX_NSX_EXECUTORCH_REF"],
    }
    ns_revision = {
        "requested_kind": "branch",
        "requested_ref": "candidate-branch",
        "resolved_commit": "b" * 40,
    }
    if has_override:
        env["HPX_SOURCE_REVISIONS_JSON"] = json.dumps({"ns-cmsis-nn": ns_revision})
    mock_git = """
git() {
  if [[ "$*" == *"rev-parse HEAD" ]]; then
    if [[ "$2" == */external/executorch ]]; then
      echo "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
    else
      echo "${HPX_NSX_EXECUTORCH_REF}"
    fi
  fi
}
"""
    script = _step(validate_job, "Initialize qualified ExecuTorch dependencies")["run"]
    _run_bash(mock_git + script, env, tmp_path)
    exported = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines())
    revisions = json.loads(exported["HPX_SOURCE_REVISIONS_JSON"])
    assert revisions["nsx-executorch"]["resolved_commit"] == env["HPX_NSX_EXECUTORCH_REF"]
    assert revisions["executorch"]["resolved_commit"] == "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
    assert revisions["arm-cmsis-nn"]["requested_kind"] == "commit"
    if has_override:
        assert revisions["ns-cmsis-nn"] == ns_revision
    else:
        assert "ns-cmsis-nn" not in revisions
    assert (tmp_path / "results" / "validation" / "executorch-revisions.txt").is_file()


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
