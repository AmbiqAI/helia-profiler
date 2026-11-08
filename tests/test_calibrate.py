"""``hpx calibrate`` (#648): synthetic and real plans, every rule, and calibration schema 2.

Reading a bundle needs heliaAOT, so this module skips without the aot
extra; ``test_calibrate_no_aot.py`` holds what must run without it. Every
file a test here writes through ``calibrate`` is validated against the
exported schema by ``schema_checked_writes``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from helia_profiler.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    PAIRING_EXCLUDED,
    SETUP_EXCLUDED,
    CalibrationTable,
    OperatorSample,
    Setup,
    calibrate,
    merge_sources,
    read_bundle,
    read_calibration,
)
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, ConfigError, ReportError
from helia_profiler.results import ResultValidity
from helia_profiler.results.manifest import load_result_manifest
from tests.calibration_bundles import (
    CONDITIONS,
    GOLDEN,
    PAIR_AOT_MEMORY,
    PLAN_OPERATORS,
    PLANS,
    SHA,
    _golden_plan,
    _hand_written,
    _knob,
    _mark,
    _republish,
    bundle,
    flat,
    golden_bundles,
    pair,
    schema_checked_writes,  # noqa: F401
    two_bundles,
)

pytest.importorskip("helia_aot")
pytestmark = pytest.mark.usefixtures("schema_checked_writes")


def _real_bundle(path: Path, plan_name: str, *, offset: float = 0.0, **changes: Any) -> Path:
    """A bundle around one of heliaAOT 0.33's own plans for the kws models.

    The effective configuration is the real digest of a real config that
    sets the plan's knob value, so a sweep's bundles carry real, equal digests.
    The offset moves only the operators with knobs: the others ran the same
    code in both captures.
    """
    plan = json.loads((PLANS / f"{plan_name}_plan.json").read_text(encoding="utf-8"))
    ops = plan["operators"]
    (knob, value), *_ = {
        name: choice["value"] for op in ops for name, choice in op["knobs"].items()
    }.items()
    config = _aot_config(path.parent, plan_name, f"optimization: {{{knob}: {value}}}\n")
    return bundle(
        path,
        cycles={op["index"]: 1000.0 + op["index"] + (offset if op["knobs"] else 0.0) for op in ops},
        ops=tuple(op["op_type"] for op in ops),
        plan_ops=ops,
        sha256=plan["model"]["sha256"],
        effective_config=_digest(config)[0],
        knob_values={knob: value},
        **changes,
    )


#: What :func:`two_bundles` writes: the conv's two vectors, the fc and the softmax once each.
EXPECTED_SAMPLES = {
    "0:conv": [
        {"knobs": {"accumulation": "fast"}, "cycles": 9000.0, "facts": {}},
        {"knobs": {"accumulation": "precise"}, "cycles": 12000.0, "facts": {}},
    ],
    "0:fc": [
        {
            "knobs": {"accumulation": "precise", "kernel": "specialized"},
            "cycles": 3000.0,
            "facts": {},
        }
    ],
    "0:softmax": [{"knobs": {}, "cycles": 400.0, "facts": {}}],
}


def _check_expected(written: dict[str, Any]) -> None:
    assert list(written) == ["schema_version", "model", "conditions", "operator_cycles", "capture"]
    assert written["schema_version"] == 2
    assert written["model"] == {"sha256": SHA, "subgraph": 0}
    assert written["conditions"] == CONDITIONS
    assert written["operator_cycles"] == EXPECTED_SAMPLES
    assert list(written["capture"]) == ["setup"]


def test_a_file_holds_only_samples(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"

    table = calibrate([auto, wide], output=output)

    written = json.loads(output.read_text(encoding="utf-8"))
    _check_expected(written)
    assert CALIBRATION_SCHEMA_VERSION == 2
    assert len(table.samples) == 4 and table.operator_count == 3
    assert read_calibration(output) == table
    assert not any(output.parent.glob(".kws.json.*"))


def test_the_setup_covers_the_held_settings_and_nothing_swept_or_run_only(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)

    setup = json.loads(output.read_text(encoding="utf-8"))["capture"]["setup"]

    assert re.fullmatch(r"[0-9a-f]{64}", setup["digest"])
    fields = set(setup["fields"])
    held = {
        "config.target.toolchain",
        "config.target.board",
        "config.target.clock.cpu",
        "config.model.arena_location",
        "config.model.arena_size",
        "config.model.weights_location",
        "config.engine.backend",
        "config.engine.config.aot_args.optimization.goal",
        "config.profiling.iterations",
        "config.build.nsx_modules",
        "toolchain.compiler_version",
        "engine.version",
        "engine.effective_config_sha256",
        "engine.effective_config_keys.0",
        "build.architecture_flags",
        "kernel.build",
    }
    assert held <= fields, held - fields
    swept_or_run_only = {
        "config.engine.config.aot_args.optimization.accumulation",
        "config.engine.config.aot_args.optimization.kernel",
        "config.engine.config.aot_args.operators.0.optimization.accumulation",
        "config.engine.config.aot_args.operators.0.type",
        "config.engine.config.aot_args.operators",
        "config.power.enabled",
        "toolchain.cmake_version",
        "config.output.dir",
        "config.timeouts.build_s",
        "config.target.jlink_serial",
        "config.model.path",
        "config.work_dir",
        "config.build.offline",
    }
    assert not swept_or_run_only & fields
    assert not any(name.startswith("config.engine.config.aot_args.operators") for name in fields)
    assert "config.power" in SETUP_EXCLUDED
    source = read_bundle(auto)
    assert source.setup.digest == setup["digest"]
    assert source.setup.values is not None
    assert source.setup.values["kernel"]["build"] == "commit:" + "c" * 40
    assert source.setup.values["build"]["architecture_flags"] == "-mcpu=cortex-m55"


def test_an_approximate_value_is_still_priced(tmp_path: Path) -> None:
    _, wide = two_bundles(tmp_path)
    (sample,) = [s for s in read_bundle(wide).samples if s.operator == "0:conv"]
    assert sample.knobs == {"accumulation": "fast"} and sample.cycles == 9000.0


@pytest.mark.parametrize(
    ("model", "knob", "values", "operators", "samples"),
    [
        ("kws_int8", "kernel", ("specialized", "generic"), 13, 23),
        ("kws_fp16", "accumulation", ("precise", "fast"), 6, 10),
    ],
)
def test_a_real_knob_sweep_times_every_operator(
    tmp_path: Path, model: str, knob: str, values: tuple[str, str], operators: int, samples: int
) -> None:
    """heliaAOT's knobs exist to change the kernel entry and the choices that follow it."""
    first = _real_bundle(tmp_path / values[0], f"{model}_{knob}_{values[0]}")
    second = _real_bundle(tmp_path / values[1], f"{model}_{knob}_{values[1]}", offset=500.0)
    output = tmp_path / f"{model}.json"

    table = calibrate([first, second], output=output)

    assert (table.operator_count, len(table.samples)) == (operators, samples)
    assert re.fullmatch(r"[0-9a-f]{64}", table.setup.digest)
    assert "engine.effective_config_sha256" in table.setup.fields
    written = json.loads(output.read_text(encoding="utf-8"))
    plans = {
        value: json.loads((PLANS / f"{model}_{knob}_{value}_plan.json").read_text())["operators"]
        for value in values
    }
    # Every plan operator is timed under the plan's vector, with what the plan reported for it.
    for value, ops in plans.items():
        for op in ops:
            vector = {name: choice["value"] for name, choice in op["knobs"].items()}
            fixed = op.get("fixed") or {}
            facts = {f"fixed:{name}": choice["value"] for name, choice in fixed.items()}
            if "entry" in (op.get("kernel") or {}):
                facts["kernel:entry"] = op["kernel"]["entry"]
            cycles = 1000.0 + op["index"] + (500.0 if value == values[1] and vector else 0.0)
            entry = {"knobs": vector, "cycles": cycles, "facts": facts}
            assert entry in written["operator_cycles"][op["id"]], op["id"]
    assert list(written["operator_cycles"]) == [op["id"] for op in plans[values[0]]]
    # The swept operators are exactly those whose vector the knob moved.
    moved = {
        op["id"]
        for op, other in zip(plans[values[0]], plans[values[1]], strict=True)
        if op["knobs"] != other["knobs"]
    }
    swept = {op for op, entries in written["operator_cycles"].items() if len(entries) == 2}
    assert moved and swept == moved
    # Merging the second capture again rewrites the file byte for byte.
    before = output.read_bytes()
    assert calibrate([second], output=output, merge=True) == table
    assert output.read_bytes() == before


def test_merge_then_merge_again(tmp_path: Path) -> None:
    """The documented workflow: write, --merge, --merge the same bundle."""
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)

    once = calibrate([wide], output=output, merge=True)
    first = output.read_bytes()
    twice = calibrate([wide], output=output, merge=True)

    assert once == twice
    assert output.read_bytes() == first
    _check_expected(json.loads(output.read_text(encoding="utf-8")))


def _fc_entries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["operator_cycles"]["0:fc"]


def test_a_one_knob_at_a_time_sweep_of_two_knobs_merges(tmp_path: Path) -> None:
    """``0:fc``'s accumulation and kernel, each moved from ``(precise, specialized)``."""
    base, _, fc_generic, fc_fast = golden_bundles(tmp_path / "bundles")
    sweeps = {
        "S1": [base, fc_generic],
        "S2": [base, fc_fast],
        "S3": [base, fc_generic, fc_fast],
    }
    canonical = [
        {"accumulation": "fast", "kernel": "specialized"},
        {"accumulation": "precise", "kernel": "generic"},
        {"accumulation": "precise", "kernel": "specialized"},
    ]
    for name, bundles in sweeps.items():
        once = tmp_path / f"{name}-once.json"
        calibrate(bundles, output=once)
        stepped = tmp_path / f"{name}-stepped.json"
        calibrate(bundles[:1], output=stepped)
        for later in bundles[1:]:
            calibrate([later], output=stepped, merge=True)
        assert stepped.read_bytes() == once.read_bytes(), name
        knobs = [entry["knobs"] for entry in _fc_entries(once)]
        assert knobs == [vector for vector in canonical if vector in knobs], name
        assert len(knobs) == len(bundles), name
    # Both knobs moved at once is a vector of its own, and merges too.
    both = bundle(
        tmp_path / "fc_fast_generic",
        cycles={0: 12000.0, 1: 2000.0, 2: 400.0},
        plan_ops=_golden_plan({"0:fc": {"accumulation": "fast", "kernel": "generic"}}),
    )
    output = tmp_path / "S3-once.json"
    table = calibrate([both], output=output, merge=True)
    assert [entry["knobs"] for entry in _fc_entries(output)] == [
        {"accumulation": "fast", "kernel": "generic"},
        *canonical,
    ]
    assert len(table.samples) == 6


def _with_facts(
    entries: dict[str, str], fixed: dict[str, dict[str, dict[str, str]]] | None = None
) -> list[dict[str, Any]]:
    """:data:`PLAN_OPERATORS` with a kernel entry per operator and fixed choices on the fc.

    The fc's fixed choices are listed out of name order, so a written file
    shows whether its facts are sorted.
    """
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    for op in ops:
        op["kernel"] = {"entry": entries.get(op["id"], op["op_type"])}
    ops[1]["fixed"] = {
        "weight_layout": {"value": "packed", "set_by": "heuristic"},
        "bias_layout": {"value": "split", "set_by": "heuristic"},
    }
    for operator, choices in (fixed or {}).items():
        (op,) = [op for op in ops if op["id"] == operator]
        op["fixed"] = choices
    return ops


def test_facts_are_held_for_every_sample(tmp_path: Path) -> None:
    cycles = {0: 12000.0, 1: 3000.0, 2: 400.0}
    one = bundle(tmp_path / "one", cycles=cycles, plan_ops=_with_facts({}))
    same = bundle(tmp_path / "same", cycles=cycles, plan_ops=_with_facts({}))
    output = tmp_path / "out.json"
    table = calibrate([one, same], output=output)
    fc = (("accumulation", "precise"), ("kernel", "specialized"))
    assert table.samples[("0:fc", fc)].facts == {
        "fixed:bias_layout": "split",
        "fixed:weight_layout": "packed",
        "kernel:entry": "fully_connected",
    }
    written = json.loads(output.read_text(encoding="utf-8"))["operator_cycles"]
    assert list(written["0:fc"][0]["facts"]) == [
        "fixed:bias_layout",
        "fixed:weight_layout",
        "kernel:entry",
    ]
    packed = {"weight_layout": {"value": "packed", "set_by": "heuristic"}}
    split = {"bias_layout": {"value": "split", "set_by": "heuristic"}}
    unpacked = {"weight_layout": {"value": "unpacked", "set_by": "heuristic"}, **split}
    fc = r"\{'accumulation': 'precise', 'kernel': 'specialized'\}"
    for name, plan_ops, operator, vector in (
        (
            "conv_entry",
            _with_facts({"0:conv": "other"}),
            "0:conv",
            r"\{'accumulation': 'precise'\}",
        ),
        ("fc_layout", _with_facts({}, {"0:fc": unpacked}), "0:fc", fc),
        ("softmax_entry", _with_facts({"0:softmax": "other"}), "0:softmax", r"\{\}"),
        ("fc_without_layout", _with_facts({}, {"0:fc": split}), "0:fc", fc),
        ("softmax_with_layout", _with_facts({}, {"0:softmax": packed}), "0:softmax", r"\{\}"),
    ):
        other = bundle(tmp_path / name, cycles=cycles, plan_ops=plan_ops)
        with pytest.raises(
            CalibrationError,
            match=rf"{operator} with the knobs at {vector}.* reports .* in .*{name} but .* in "
            r".*one, so something other than the knobs changed",
        ) as refused:
            calibrate([one, other], output=tmp_path / "out.json")
        assert refused.value.hint == (
            "Calibrate captures from one build; write another file for the other."
        ), name


def test_bundles_of_another_model_are_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, sha256="b" * 64)
    with pytest.raises(CalibrationError, match="is for model bbbbbbbbbbbb subgraph 0"):
        calibrate([auto, other], output=tmp_path / "out.json")
    sub = bundle(tmp_path / "sub", cycles={0: 1.0, 1: 1.0, 2: 1.0}, subgraph=1)
    with pytest.raises(CalibrationError, match="subgraph 1"):
        calibrate([auto, sub], output=tmp_path / "out.json")


def test_a_plan_for_another_model_is_refused(tmp_path: Path) -> None:
    stale = bundle(tmp_path / "stale", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_sha256="c" * 64)
    with pytest.raises(CalibrationError, match="compiled from model cccccccccccc"):
        read_bundle(stale)


def test_bundles_under_other_conditions_are_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    mram = bundle(
        tmp_path / "mram",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "weights_memory": "mram"},
    )
    with pytest.raises(CalibrationError, match="measured under conditions .*'mram'"):
        calibrate([auto, mram], output=tmp_path / "out.json")
    # Every recorded field takes part, as heliaOptimus compares them.
    loaded = bundle(
        tmp_path / "loaded",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "application_load": "ble"},
    )
    with pytest.raises(CalibrationError, match="measured under conditions"):
        calibrate([auto, loaded], output=tmp_path / "out.json")


@pytest.mark.parametrize(
    ("change", "field", "values"),
    [
        (
            {"toolchain": "armclang"},
            "config.target.toolchain",
            ("'armclang'", "'arm-none-eabi-gcc'"),
        ),
        ({"compiler_version": "14.1.0"}, "toolchain.compiler_version", ("'14.1.0'", "'13.2.1'")),
        ({"backend": "ethos_u"}, "config.engine.backend", ("'ethos_u'", "None")),
        ({"arena_location": "sram"}, "config.model.arena_location", ("'sram'", "None")),
        (
            {"flags": "-mcpu=cortex-m55+nomve"},
            "build.architecture_flags",
            ("'-mcpu=cortex-m55\\+nomve'", "'-mcpu=cortex-m55'"),
        ),
        ({"kernel_commit": "d" * 40}, "kernel.build", ("'commit:dddd", "'commit:cccc")),
        (
            {"effective_config": "f" * 64},
            "engine.effective_config_sha256",
            ("'ffff", "'eeee"),
        ),
    ],
)
def test_a_held_setting_that_differs_is_refused_naming_it(
    tmp_path: Path, change: dict[str, Any], field: str, values: tuple[str, str]
) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, **change)
    message = (
        f"different setups: .*{re.escape(field)} is .*{values[0]}.* in .*other"
        f".*{values[1]}.* in .*auto|different setups: .*{re.escape(field)} is "
        f".*{values[1]}.* in .*auto.*{values[0]}.* in .*other"
    )
    with pytest.raises(CalibrationError, match=message):
        calibrate([auto, other], output=tmp_path / "out.json")
    with pytest.raises(CalibrationError, match=message):
        calibrate([other, auto], output=tmp_path / "out.json")
    # The file remembers its setup, so a later merge is held to it too.
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    with pytest.raises(CalibrationError, match="different setups"):
        calibrate([other], output=output, merge=True)


def test_local_kernel_trees_are_told_apart_by_their_content(tmp_path: Path) -> None:
    """A local or vendored ns-cmsis-nn records no commit, only a content hash."""
    one = bundle(
        tmp_path / "one",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="1" * 64,
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 5000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="2" * 64,
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
    )
    with pytest.raises(CalibrationError, match="kernel.build is 'content:2222"):
        calibrate([one, other], output=tmp_path / "out.json")
    same = bundle(
        tmp_path / "same",
        cycles={0: 5000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="1" * 64,
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
    )
    assert len(calibrate([one, same], output=tmp_path / "out.json").samples) == 4


def test_a_setting_absent_on_one_side_is_a_mismatch(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    bare = bundle(tmp_path / "bare", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, with_toolchain=False)
    with pytest.raises(
        CalibrationError,
        match="toolchain.compiler_version is absent in .*bare but '13.2.1' in .*auto",
    ):
        calibrate([auto, bare], output=tmp_path / "out.json")
    with pytest.raises(CalibrationError, match="different setups"):
        calibrate([bare, auto], output=tmp_path / "out.json")


def test_swept_and_run_only_settings_do_not_block_a_merge(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast", "kernel": "generic"},
        jlink_serial="1160009999",
        power_config={"enabled": True, "mode": "external", "serial": "004204"},
    )
    assert len(calibrate([auto, other], output=tmp_path / "out.json").samples) == 4


def test_a_helia_aot_bundle_without_the_effective_configuration_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    metadata = json.loads((auto / "run_metadata.json").read_text(encoding="utf-8"))
    del metadata["engine"]["effective_config_sha256"]
    (auto / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(auto)
    with pytest.raises(CalibrationError, match="records no engine.effective_config_sha256"):
        read_bundle(auto)


def _with_engine_config(path: Path, aot_args: dict[str, Any]) -> Path:
    metadata = json.loads((path / "run_metadata.json").read_text(encoding="utf-8"))
    metadata["config"]["engine"]["config"]["aot_args"] = aot_args
    (path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(path)
    return path


def test_the_digest_is_of_the_nested_settings_not_a_flattened_view(tmp_path: Path) -> None:
    dotted = _with_engine_config(
        bundle(tmp_path / "dotted", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"a.b": 1}}},
    )
    nested = _with_engine_config(
        bundle(tmp_path / "nested", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"a": {"b": 1}}}},
    )
    assert read_bundle(dotted).setup.digest != read_bundle(nested).setup.digest
    # No leaf differs, so the message names the section.
    with pytest.raises(CalibrationError, match="different setups: the config section differs"):
        calibrate([dotted, nested], output=tmp_path / "out.json")


def test_a_leaf_mismatch_names_the_leaf_and_absent_means_absent(tmp_path: Path) -> None:
    flag = _with_engine_config(
        bundle(tmp_path / "flag", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": True}}},
    )
    number = _with_engine_config(
        bundle(tmp_path / "number", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": 1}}},
    )
    with pytest.raises(
        CalibrationError,
        match="config.engine.config.aot_args.memory.planner_options.fast is 1 in .*number but True",
    ):
        calibrate([flag, number], output=tmp_path / "out.json")
    null = _with_engine_config(
        bundle(tmp_path / "null", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": None}}},
    )
    absent = _with_engine_config(
        bundle(tmp_path / "absent", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {}}},
    )
    with pytest.raises(
        CalibrationError,
        match="different setups: (.*; )?[a-z_.]*planner_options.fast is absent in .*absent but None in .*null",
    ) as caught:
        calibrate([null, absent], output=tmp_path / "out.json")
    assert "planner_options is" not in str(caught.value)


def test_power_measurement_state_never_takes_part(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    gated = bundle(
        tmp_path / "gated",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "firmware_code_fingerprint": "1" * 64,
        },
        power_config={"enabled": True, "mode": "external"},
    )
    assert len(calibrate([auto, gated], output=tmp_path / "out.json").samples) == 4
    # Two power-measured runs whose rendered firmware differs with the knob.
    other_print = bundle(
        tmp_path / "print",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        power={
            "measurement_scope": "whole_capture_window",
            "integrity": "degraded",
            "firmware_code_fingerprint": "2" * 64,
        },
        power_config={"enabled": True, "mode": "external"},
    )
    assert len(calibrate([gated, other_print], output=tmp_path / "out.json").samples) == 4


def test_the_setup_check_does_not_depend_on_bundle_order(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    odd = bundle(
        tmp_path / "odd",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        toolchain="armclang",
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "kernel", "generic"),
    )
    for order in ([auto, wide, odd], [odd, auto, wide], [auto, odd, wide]):
        with pytest.raises(CalibrationError, match="different setups: config.target.toolchain"):
            calibrate(order, output=tmp_path / "out.json")


def test_a_file_hpx_did_not_write_cannot_be_merged_into(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    timed = {"0:conv": [{"knobs": {"accumulation": "fast"}, "cycles": 9000}]}
    hand = _hand_written(tmp_path / "hand.json", timed)
    before = hand.read_bytes()
    with pytest.raises(CalibrationError, match="records no setup under capture.setup") as refused:
        calibrate([auto], output=hand, merge=True)
    assert "heliaOptimus reads a hand-written table directly" in str(refused.value)
    assert hand.read_bytes() == before


def test_a_damaged_file_cannot_be_merged_into(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)
    whole = json.loads(output.read_text(encoding="utf-8"))

    damaged = tmp_path / "no_setup.json"
    data = json.loads(json.dumps(whole))
    del data["capture"]["setup"]
    damaged.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="records no setup under capture.setup"):
        calibrate([wide], output=damaged, merge=True)

    edited = tmp_path / "edited.json"
    data = json.loads(json.dumps(whole))
    data["capture"]["setup"]["digest"] = "not a digest"
    edited.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="records no setup under capture.setup"):
        calibrate([wide], output=edited, merge=True)

    malformed = tmp_path / "malformed.json"
    data = json.loads(json.dumps(whole))
    data["operator_cycles"]["0:fc"][0]["facts"] = ["weights"]
    malformed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="operator_cycles 0:fc has an entry without"):
        calibrate([wide], output=malformed, merge=True)

    emptied = tmp_path / "emptied.json"
    data = json.loads(json.dumps(whole))
    data["operator_cycles"]["0:conv"] = []
    emptied.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="operator_cycles 0:conv lists no entry"):
        calibrate([wide], output=emptied, merge=True)


def test_two_files_are_held_to_the_tolerance(tmp_path: Path) -> None:
    low = bundle(tmp_path / "low", cycles={0: 1000.0, 1: 1.0, 2: 1.0})
    high = bundle(tmp_path / "high", cycles={0: 9000.0, 1: 1.0, 2: 1.0})
    first = calibrate([low], output=tmp_path / "f1.json").as_source("f1")
    second = calibrate([high], output=tmp_path / "f2.json").as_source("f2")
    with pytest.raises(
        CalibrationError,
        match=r"0:conv with the knobs at \{'accumulation': 'precise'\} takes 1000 cycles in f1 "
        r"but 9000 in f2 \(88\.9% apart, tolerance 2% or 8 cycles\)",
    ):
        merge_sources([first, second])


def test_merge_never_replaces_a_sample_the_file_holds(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    disagreeing = bundle(tmp_path / "disagreeing", cycles={0: 12000.0, 1: 4000.0, 2: 400.0})
    with pytest.raises(CalibrationError, match=r"takes 3000 cycles in .*kws\.json but 4000"):
        calibrate([disagreeing], output=output, merge=True)
    assert json.loads(output.read_text())["operator_cycles"]["0:fc"] == EXPECTED_SAMPLES["0:fc"]
    agreeing = bundle(tmp_path / "agreeing", cycles={0: 12000.0, 1: 3050.0, 2: 400.0})
    table = calibrate([agreeing], output=output, merge=True)
    fc = (("accumulation", "precise"), ("kernel", "specialized"))
    assert table.samples[("0:fc", fc)] == OperatorSample(
        "0:fc", {"accumulation": "precise", "kernel": "specialized"}, 3000.0, {}
    )


def test_a_bundle_without_conditions_or_weights_memory_is_refused(tmp_path: Path) -> None:
    old = bundle(tmp_path / "old", cycles={0: 1.0, 1: 1.0, 2: 1.0}, conditions=None)
    with pytest.raises(CalibrationError, match="records no conditions"):
        read_bundle(old)
    unplaced = bundle(
        tmp_path / "unplaced",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "weights_memory": None},
    )
    with pytest.raises(CalibrationError, match="records no weights memory"):
        read_bundle(unplaced)


def test_a_non_finite_condition_is_refused_naming_its_path(tmp_path: Path) -> None:
    """An open condition may hold any JSON value, but not one the writer cannot write."""
    conditions = {**CONDITIONS, "board": {"coefficient": 12345.5}}
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: 1.0, 2: 1.0}, conditions=conditions)
    summary = odd / "summary.json"
    summary.write_text(summary.read_text().replace("12345.5", "1e400"), encoding="utf-8")
    _republish(odd)
    output = tmp_path / "out.json"
    with pytest.raises(
        CalibrationError,
        match=r"Bundle .*odd: conditions\.board\.coefficient is not a finite number\.",
    ):
        calibrate([odd], output=output)
    assert not output.exists()
    # A file holding one is refused on --merge, and left as it is.
    auto, _ = two_bundles(tmp_path)
    calibrate([auto], output=output)
    data = json.loads(output.read_text(encoding="utf-8"))
    data["conditions"]["board"] = {"coefficient": 12345.5}
    output.write_text(json.dumps(data).replace("12345.5", "1e400"), encoding="utf-8")
    before = output.read_bytes()
    with pytest.raises(CalibrationError) as refused:
        calibrate([auto], output=output, merge=True)
    assert refused.value.args[0] == (
        f"Calibration file {output} is malformed: conditions.board.coefficient is not a "
        "finite number."
    )
    assert output.read_bytes() == before


def _nested(levels: int) -> str:
    """JSON text of *levels* objects, one inside the next, around a 1."""
    return '{"a": ' * levels + "1" + "}" * levels


def _deep_bundle(path: Path, levels: int) -> Path:
    """A bundle whose open condition ``board`` nests *levels* objects deep."""
    deep = bundle(path, cycles={0: 1.0, 1: 1.0, 2: 1.0}, conditions={**CONDITIONS, "board": "DEEP"})
    summary = deep / "summary.json"
    summary.write_text(summary.read_text().replace('"DEEP"', _nested(levels)), encoding="utf-8")
    _republish(deep)
    return deep


def test_conditions_nested_deeper_than_a_flat_record_are_refused(tmp_path: Path) -> None:
    """Fail closed on depth, without recursion: 3000 levels are refused cleanly."""
    from helia_profiler.calibration.table import CONDITIONS_DEPTH

    message = f"conditions nest deeper than {CONDITIONS_DEPTH} levels"
    output = tmp_path / "out.json"
    # The block itself is one level, so board may hold CONDITIONS_DEPTH - 1 objects.
    legal = calibrate([_deep_bundle(tmp_path / "legal", CONDITIONS_DEPTH - 1)], output=output)
    assert json.loads(output.read_text())["conditions"] == legal.conditions
    for levels in (CONDITIONS_DEPTH, 3000):
        deep = _deep_bundle(tmp_path / f"deep-{levels}", levels)
        with pytest.raises(CalibrationError, match=rf"Bundle .*deep-{levels}: {message}\."):
            calibrate([deep], output=tmp_path / f"deep-{levels}.json")
        assert not (tmp_path / f"deep-{levels}.json").exists()
    # A file holding such a block is refused on --merge, and left as it is.
    auto, _ = two_bundles(tmp_path)
    calibrate([auto], output=output)
    data = json.loads(output.read_text(encoding="utf-8"))
    data["conditions"]["board"] = "DEEP"
    output.write_text(json.dumps(data).replace('"DEEP"', _nested(3000)), encoding="utf-8")
    before = output.read_bytes()
    with pytest.raises(CalibrationError) as refused:
        calibrate([auto], output=output, merge=True)
    assert refused.value.args[0] == f"Calibration file {output} is malformed: {message}."
    assert output.read_bytes() == before


def test_a_key_in_a_refusal_path_is_shown_quoted(tmp_path: Path) -> None:
    """A dotted key or one with a newline or an escape cannot change how the path reads."""
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "out.json"
    calibrate([auto], output=output)
    data = json.loads(output.read_text(encoding="utf-8"))
    for key, shown in (
        ("a.b", 'conditions.board["a.b"]'),
        ("\x1b[2J\nFAKE: ok", 'conditions.board["\\u001b[2J\\nFAKE: ok"]'),
        ("plain_key-1", "conditions.board.plain_key-1"),
    ):
        data["conditions"]["board"] = {key: float("inf")}
        path = tmp_path / "keyed.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(CalibrationError) as refused:
            read_calibration(path)
        assert refused.value.args[0] == (
            f"Calibration file {path} is malformed: {shown} is not a finite number."
        ), key
        assert "\n" not in refused.value.args[0] and "\x1b" not in refused.value.args[0]


def test_a_run_the_profiler_marked_invalid_is_refused(tmp_path: Path) -> None:
    bad = bundle(tmp_path / "bad", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(bad, ResultValidity.INVALID, "firmware.model_mismatch")
    assert load_result_manifest(bad / "result_manifest.json", verify=True).validity.value == (
        "invalid"
    )
    with pytest.raises(
        CalibrationError, match=r"Bundle .*bad is invalid: firmware\.model_mismatch\."
    ):
        read_bundle(bad)

    degraded = bundle(tmp_path / "degraded", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(degraded, ResultValidity.DEGRADED)
    assert read_bundle(degraded).samples


@pytest.mark.parametrize(
    "code", ["profile.clean_window_clock_rate_low", "profile.clean_window_stalled"]
)
def test_a_run_whose_cycle_counter_is_suspect_is_refused(tmp_path: Path, code: str) -> None:
    slow = bundle(tmp_path / "slow", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(slow, ResultValidity.DEGRADED, code, severity="warning")
    with pytest.raises(CalibrationError, match=f"Bundle .*slow is degraded: {code}"):
        read_bundle(slow)


def test_validity_follows_the_issue_registry_as_compare_applies_it(tmp_path: Path) -> None:
    # The one error the registry confines to power metrics leaves the cycles usable.
    confined = bundle(tmp_path / "confined", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(confined, ResultValidity.INVALID, "power.window_observer_mismatch")
    assert read_bundle(confined).samples
    # A run-wide power error, an unrecognized code, and an invalid run with no
    # error to explain it are refused, as hpx compare refuses them.
    for name, codes, named in (
        ("dark", ("power.observation_missing",), "power.observation_missing"),
        ("newer", ("power.from_a_newer_hpx",), "power.from_a_newer_hpx"),
        ("silent", (), "no issue explains it"),
    ):
        path = bundle(tmp_path / name, cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
        _mark(path, ResultValidity.INVALID, *codes)
        with pytest.raises(CalibrationError, match=f"Bundle .*{name} is invalid: {named}"):
            read_bundle(path)


def test_an_overflowed_layer_is_refused(tmp_path: Path) -> None:
    hot = bundle(tmp_path / "hot", cycles={0: 1.0, 1: 1.0, 2: 1.0}, overflow=1)
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) overflowed"):
        read_bundle(hot)


@pytest.mark.parametrize("cycles", [float("nan"), float("inf"), -5.0])
def test_a_cycle_count_that_is_not_one_is_refused(tmp_path: Path, cycles: float) -> None:
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: cycles, 2: 1.0})
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) has no finite"):
        read_bundle(odd)


def test_a_cycle_count_too_large_for_a_float_is_refused(tmp_path: Path) -> None:
    huge = "9" * 401
    big = bundle(tmp_path / "big", cycles={0: 1.0, 1: 1.0, 2: 1.0}, layer_cycles_text=huge)
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) has no finite"):
        read_bundle(big)
    output = tmp_path / "big.json"
    output.write_text(
        '{"schema_version": 2, "model": {"sha256": "a", "subgraph": 0}, '
        '"conditions": {"target": "apollo510_evb", "weights_memory": "dtcm"}, '
        + _SETUP_TEXT
        + ', "operator_cycles": {"x": [{"knobs": {}, "facts": {}, "cycles": '
        + huge
        + "}]}}",
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError, match="x has an entry without string knobs, finite"):
        read_calibration(output)


def test_a_position_the_plan_and_the_capture_disagree_on_is_refused(tmp_path: Path) -> None:
    short = bundle(tmp_path / "short", cycles={0: 1.0, 1: 1.0})
    with pytest.raises(CalibrationError, match="operator 0:softmax at position 2 has no measured"):
        read_bundle(short)
    long = bundle(tmp_path / "long", cycles={0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0})
    with pytest.raises(CalibrationError, match=r"measured layer 3 \(CONV_2D\) has no operator"):
        read_bundle(long)


def test_an_operator_type_the_plan_and_the_capture_disagree_on_is_refused(tmp_path: Path) -> None:
    swapped = bundle(
        tmp_path / "swapped",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        ops=("SOFTMAX:5", "FULLY_CONNECTED:1", "CONV_2D:-1"),
    )
    with pytest.raises(
        CalibrationError, match="plan has conv_2d at position 0 but the capture measured SOFTMAX:5"
    ):
        read_bundle(swapped)
    # A suffixed label and a differently cased key are the same operator type.
    labelled = bundle(
        tmp_path / "labelled",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        ops=("CONV_2D:0", "FULLY_CONNECTED:1", "SOFTMAX:-1"),
    )
    assert read_bundle(labelled).samples


def test_a_plan_that_lists_a_position_or_an_operator_twice_is_refused(tmp_path: Path) -> None:
    twice = json.loads(json.dumps(PLAN_OPERATORS))
    twice[1]["index"] = 0
    doubled = bundle(tmp_path / "doubled", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=twice)
    with pytest.raises(CalibrationError, match="lists position 0 twice"):
        read_bundle(doubled)
    same = json.loads(json.dumps(PLAN_OPERATORS))
    same[1]["id"] = "0:conv"
    renamed = bundle(tmp_path / "renamed", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=same)
    with pytest.raises(CalibrationError, match="lists operator 0:conv twice"):
        read_bundle(renamed)


@pytest.mark.parametrize("fixed", [[], 0, "", "x"])
def test_a_plan_whose_fixed_is_not_an_object_is_refused(tmp_path: Path, fixed: Any) -> None:
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    ops[0]["fixed"] = fixed
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=ops)
    with pytest.raises(CalibrationError, match="operator 0:conv fixed is not an object"):
        read_bundle(odd)


@pytest.mark.parametrize("name", ["", "weight\nlayout", "weight_layout\n"])
def test_a_fixed_choice_a_calibration_file_cannot_name_is_refused(
    tmp_path: Path, name: str
) -> None:
    """The writer never emits a fact name the exported schema or the reader refuses."""
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    ops[1]["fixed"] = {name: {"value": "packed", "set_by": "heuristic"}}
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=ops)
    output = tmp_path / "out.json"
    with pytest.raises(
        CalibrationError,
        match=rf"Bundle .*odd: plan operator 0:fc names a fixed choice {re.escape(repr(name))}, "
        "which a calibration file cannot hold",
    ):
        calibrate([odd], output=output)
    assert not output.exists()


def test_a_plan_with_a_negative_subgraph_is_refused(tmp_path: Path) -> None:
    negative = bundle(tmp_path / "negative", cycles={0: 1.0, 1: 1.0, 2: 1.0}, subgraph=-1)
    output = tmp_path / "out.json"
    with pytest.raises(
        CalibrationError,
        match=r"Bundle .*negative: the plan names no model sha256 and non-negative subgraph",
    ):
        calibrate([negative], output=output)
    assert not output.exists()


def test_a_bundle_without_a_plan_is_refused(tmp_path: Path) -> None:
    bare = bundle(tmp_path / "bare", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_files=())
    with pytest.raises(CalibrationError, match="pins no heliaAOT plan"):
        read_bundle(bare)
    two = bundle(
        tmp_path / "two",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        plan_files=("a_plan.json", "b_plan.json"),
    )
    with pytest.raises(CalibrationError, match="pins 2 heliaAOT plans"):
        read_bundle(two)
    future = bundle(tmp_path / "future", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_schema=2)
    with pytest.raises(CalibrationError, match="plan schema version 2"):
        read_bundle(future)


def test_an_unverified_bundle_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    (auto / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReportError):
        read_bundle(auto)


def test_a_knobless_model_calibrates(tmp_path: Path) -> None:
    plain = bundle(
        tmp_path / "plain",
        cycles={0: 1.0, 1: 2.0, 2: 3.0},
        plan_ops=[{**op, "knobs": {}} for op in PLAN_OPERATORS],
    )
    output = tmp_path / "out.json"
    table = calibrate([plain], output=output)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert "operators" not in written and len(table.samples) == 3
    assert written["operator_cycles"] == {
        "0:conv": [{"knobs": {}, "cycles": 1.0, "facts": {}}],
        "0:fc": [{"knobs": {}, "cycles": 2.0, "facts": {}}],
        "0:softmax": [{"knobs": {}, "cycles": 3.0, "facts": {}}],
    }
    assert calibrate([plain], output=output, merge=True) == table
    result = CliRunner().invoke(app, ["calibrate", str(plain), "-o", str(tmp_path / "cli.json")])
    assert result.exit_code == 0, result.output
    assert "(3 operators, 3 samples)" in result.output
    with pytest.raises(CalibrationError, match="No result bundle"):
        merge_sources([])
    with pytest.raises(CalibrationError, match="No result bundle"):
        calibrate([], output=tmp_path / "out.json")


def test_merge_sources_without_samples_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    source = dataclasses.replace(read_bundle(auto), samples=())
    with pytest.raises(
        CalibrationError,
        match="No operator in the bundles was timed, so there is nothing to calibrate",
    ) as refused:
        merge_sources([source])
    assert refused.value.hint == (
        "hpx calibrate reads the cycles of every operator heliaAOT's plan lists."
    )


@pytest.mark.parametrize("tolerance", [float("nan"), float("inf"), -0.1, 1.0, 2.0])
def test_a_tolerance_that_is_not_a_fraction_is_refused(tmp_path: Path, tolerance: float) -> None:
    auto, _ = two_bundles(tmp_path)
    with pytest.raises(CalibrationError, match="--tolerance must be a fraction"):
        calibrate([auto], output=tmp_path / "out.json", tolerance=tolerance)


def test_merge_holds_the_existing_file_to_the_same_rules(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert json.loads(output.read_text())["operator_cycles"]["0:conv"] == [
        {"knobs": {"accumulation": "precise"}, "cycles": 12000.0, "facts": {}}
    ]

    calibrate([wide], output=output, merge=True)
    _check_expected(json.loads(output.read_text()))

    with pytest.raises(CalibrationError, match="Nothing to merge into"):
        calibrate([auto], output=tmp_path / "missing.json", merge=True)


def test_merge_refuses_a_file_for_another_model(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    output.write_text(
        json.dumps(
            CalibrationTable(
                model_sha256="d" * 64,
                subgraph=0,
                conditions=CONDITIONS,
                setup=Setup.of({"config.target.board": "apollo510_evb"}),
                samples={
                    ("0:conv", (("accumulation", "precise"),)): OperatorSample(
                        "0:conv", {"accumulation": "precise"}, 1.0
                    )
                },
            ).to_dict()
        )
    )
    with pytest.raises(CalibrationError, match="kws.json is for model dddddddddddd"):
        calibrate([auto], output=output, merge=True)


_V2_HEAD = (
    '{"schema_version": 2, "model": {"sha256": "a", "subgraph": 0}, '
    '"conditions": {"target": "t", "weights_memory": "dtcm"}, '
)
_SETUP_TEXT = (
    '"capture": {"setup": {"digest": "' + "0" * 64 + '", "fields": [], '
    '"pairing_digest": null, "pairing_fields": null, "helia_aot_version": null}}'
)
_MALFORMED = "is malformed: it needs model.sha256, model.subgraph, conditions and operator_cycles."
_RECALIBRATE = (
    "A schema 2 file holds only the fields helia_profiler/data/calibration.schema.v2.json "
    "names, so this one was edited or written by another tool; calibrate the result bundles "
    "again without --merge."
)


@pytest.mark.parametrize(
    ("document", "message", "hint"),
    [
        ("[]", "not a JSON object", None),
        (
            '{"schema_version": 3}',
            "is calibration schema 3, newer than the version 2 this release reads",
            "Merge with the helia-profiler release that wrote it, or calibrate the bundles "
            "again without --merge.",
        ),
        (
            '{"schema_version": 2.0}',
            "has schema_version 2.0; this release reads the integer 2",
            _RECALIBRATE,
        ),
        (
            '{"schema_version": true}',
            "has schema_version True; this release reads the integer 2",
            _RECALIBRATE,
        ),
        (
            '{"schema_version": "2"}',
            "has schema_version '2'; this release reads the integer 2",
            _RECALIBRATE,
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}, '
            '"operators": {"x": {"k": {"v": {"cycles": 1}}}}}',
            "is calibration schema 1, which this release does not read",
            None,
        ),
        ('{"schema_version": 2, "model": {"sha256": "a"}}', _MALFORMED, None),
        (
            '{"schema_version": 2, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}}',
            _MALFORMED,
            None,
        ),
        (
            _V2_HEAD + '"operator_cycles": {"x": [{"knobs": {}, "cycles": 1, "facts": {}}]}}',
            "records no setup",
            None,
        ),
        (
            _V2_HEAD
            + _SETUP_TEXT
            + ', "operator_cycles": {"x": [{"knobs": {}, "cycles": NaN, "facts": {}}]}}',
            "x has an entry without string knobs, finite non-negative cycles",
            None,
        ),
        (
            _V2_HEAD
            + _SETUP_TEXT
            + ', "operator_cycles": {"x": [{"knobs": {}, "cycles": -1, "facts": {}}]}}',
            "x has an entry without string knobs, finite non-negative cycles",
            None,
        ),
    ],
)
def test_a_malformed_existing_file_is_refused(
    tmp_path: Path, document: str, message: str, hint: str | None
) -> None:
    output = tmp_path / "bad.json"
    output.write_text(document, encoding="utf-8")
    with pytest.raises(CalibrationError, match=re.escape(message)) as refused:
        read_calibration(output)
    if hint is not None:
        assert refused.value.hint == hint


def _plain_help(runner: CliRunner) -> str:
    """The command's help as one line, at whatever width the terminal wraps it."""
    result = runner.invoke(app, ["calibrate", "--help"])
    assert result.exit_code == 0, result.output
    return flat(result.output)


def test_the_command_writes_the_file_and_reports_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"

    result = runner.invoke(app, ["calibrate", str(auto), str(wide), "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert "(3 operators, 4 samples)" in result.output
    _check_expected(json.loads(output.read_text(encoding="utf-8")))

    strict = runner.invoke(
        app, ["calibrate", str(auto), str(wide), "-o", str(output), "--tolerance", "0.005"]
    )
    assert strict.exit_code == 1
    assert "takes 3000 cycles" in flat(strict.output)
    assert "tolerance 0.5% or 8 cycles" in flat(strict.output)

    lax = runner.invoke(app, ["calibrate", str(auto), "-o", str(output), "--tolerance", "nan"])
    assert lax.exit_code == 1 and "--tolerance must be a fraction" in flat(lax.output)

    directory = runner.invoke(app, ["calibrate", str(auto), "-o", str(tmp_path)])
    assert directory.exit_code == 1, directory.output
    assert "Could not write" in flat(directory.output)
    assert not any(tmp_path.glob(f".{tmp_path.name}.*"))

    help_text = _plain_help(runner)
    assert "The file is calibration schema 2" in help_text
    assert "(calibration schema 2)" in help_text
    assert "--merge" in help_text and "--tolerance" in help_text
    assert "application_load=none" in help_text
    assert "capture.setup" in help_text
    assert "operator_cycles[<id>]" in help_text and "pairing_digest" in help_text
    assert "Any set of knob vectors merges" in help_text
    assert "within --tolerance of the larger or within 8 cycles" in help_text
    assert "--tolerance 0 still allows 8 cycles" in help_text
    assert "they must report the same facts" in help_text
    assert "a schema 1 file is refused and must be written again from its bundles" in help_text
    assert "a file of a newer schema is merged with the release that wrote it" in help_text
    assert "helia_profiler/data/calibration.schema.v2.json" in help_text
    assert "with memory.allocate_arenas: false" in help_text
    assert "price" not in help_text
    assert "kws-sram" not in help_text


def test_the_file_follows_the_umask_and_never_replaces_a_link(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    previous = os.umask(0o027)
    try:
        output = tmp_path / "kws.json"
        calibrate([auto], output=output)
        assert stat.S_IMODE(output.stat().st_mode) == 0o640
    finally:
        os.umask(previous)
    private = tmp_path / "private.json"
    private.write_text("{}", encoding="utf-8")
    private.chmod(0o600)
    calibrate([auto], output=private)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert not any(tmp_path.glob(".private.json.*"))
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(CalibrationError, match="link.json is a symbolic link"):
        calibrate([auto], output=link)
    assert link.is_symlink() and real.read_text(encoding="utf-8") == "{}"


def test_a_failed_write_removes_only_its_own_temporary_file(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    foreign = tmp_path / f".cal.json.{os.getpid()}.tmp"
    foreign.write_text("another writer's data", encoding="utf-8")
    calibrate([auto], output=output)
    assert foreign.read_text(encoding="utf-8") == "another writer's data"
    assert sorted(p.name for p in tmp_path.glob(".cal.json.*")) == [foreign.name]


def _aot_config(tmp_path: Path, name: str, text: str):
    """A profiler config whose engine.config_path holds *text*, which ConvertArgs accepts."""
    import yaml
    from helia_aot.cli.defines import ConvertArgs

    from helia_profiler.config import load_config

    ConvertArgs(**yaml.safe_load(text))
    config_path = tmp_path / f"{name}.yml"
    config_path.write_text(text, encoding="utf-8")
    model = tmp_path / "kws.tflite"
    model.write_bytes(b"\x00")
    return load_config(
        None,
        {
            "model": {"path": str(model), "weights_location": "mram"},
            "engine": {"type": "helia-aot", "config_path": str(config_path)},
            "target": {"board": "apollo510_evb"},
            "work_dir": str(tmp_path / "work"),
        },
    )


def _digest(config) -> tuple[str, tuple[str, ...]]:
    from helia_profiler.engines.helia_aot.compile import (
        effective_aot_args,
        effective_config_digest,
    )

    return effective_config_digest(
        effective_aot_args(config),
        module_name="hpx_model",
        prefix="hpx",
        aot_platform="apollo510_evb",
    )


def test_the_compiler_configuration_a_file_supplies_is_part_of_the_setup(tmp_path: Path) -> None:
    """Two runs differing only in a placement rule from engine.config_path do not merge."""
    sram = _aot_config(
        tmp_path,
        "sram",
        "memory:\n  tensors:\n    - type: constant\n      id: '0:w'\n      attributes: {memory: sram}\n",
    )
    dtcm = _aot_config(
        tmp_path,
        "dtcm",
        "memory:\n  tensors:\n    - type: constant\n      id: '0:w'\n      attributes: {memory: dtcm}\n",
    )
    sram_digest, keys = _digest(sram)
    assert sram_digest != _digest(dtcm)[0]
    assert keys == (
        "documentation",
        "force",
        "memory",
        "model",
        "module",
        "optimization",
        "platform",
        "result_views",
        "test",
        "transforms",
    )
    # The knob values are the swept inputs: a config that differs only there digests the same.
    generic = _aot_config(tmp_path, "generic", "optimization: {kernel: generic}\n")
    specialized = _aot_config(tmp_path, "specialized", "optimization: {kernel: specialized}\n")
    assert _digest(generic) == _digest(specialized)
    assert _digest(generic)[0] != sram_digest
    # A rule that only overrides a knob leaves nothing behind either.
    ruled = _aot_config(
        tmp_path,
        "ruled",
        "operators:\n  - type: conv_2d\n    optimization: {kernel: specialized}\n",
    )
    assert _digest(ruled) == _digest(generic)
    # The goal and approximation switch are held, the run's own paths are not.
    fast = _aot_config(tmp_path, "fast", "optimization: {goal: latency}\n")
    assert _digest(fast) != _digest(generic)
    logged = _aot_config(tmp_path, "logged", "log_file: run.log\nverbose: 2\n")
    assert _digest(logged) == _digest(generic)
    # And the bundles those runs write refuse one another on that field.
    one = bundle(
        tmp_path / "one", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, effective_config=sram_digest
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        effective_config=_digest(dtcm)[0],
    )
    with pytest.raises(CalibrationError, match="engine.effective_config_sha256 is"):
        calibrate([one, other], output=tmp_path / "out.json")
    same = bundle(
        tmp_path / "same",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        effective_config=sram_digest,
    )
    assert len(calibrate([one, same], output=tmp_path / "out.json").samples) == 4


def test_the_run_metadata_records_the_effective_configuration() -> None:
    from helia_profiler.report.metadata import _metadata_to_dict
    from helia_profiler.results import EngineInfo, RunMetadata

    recorded = _metadata_to_dict(
        RunMetadata(
            engine=EngineInfo(
                type="helia-aot",
                version="0.33.0",
                effective_config_sha256="e" * 64,
                effective_config_keys=("memory", "module"),
            )
        )
    )
    assert recorded["engine"] == {
        "type": "helia-aot",
        "version": "0.33.0",
        "effective_config_sha256": "e" * 64,
        "effective_config_keys": ("memory", "module"),
    }
    paired = _metadata_to_dict(
        RunMetadata(engine=EngineInfo(type="helia-aot", effective_config_pairing_sha256="9" * 64))
    )
    assert paired["engine"]["effective_config_pairing_sha256"] == "9" * 64
    assert json.loads(json.dumps(recorded))["engine"]["effective_config_keys"] == [
        "memory",
        "module",
    ]


def test_captures_compiled_by_different_heliaaot_releases_do_not_merge(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    newer = bundle(
        tmp_path / "newer",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        engine_version="0.34.0",
    )
    with pytest.raises(
        CalibrationError,
        match="newer was compiled with heliaAOT 0.34.0 and .*auto with heliaAOT 0.33.0",
    ):
        calibrate([auto, newer], output=tmp_path / "out.json")
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert json.loads(output.read_text())["capture"]["setup"]["helia_aot_version"] == "0.33.0"
    with pytest.raises(CalibrationError, match="compiled with heliaAOT 0.34.0 and .*kws.json"):
        calibrate([newer], output=output, merge=True)


def test_a_non_finite_value_in_an_excluded_section_does_not_matter(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    for path in (auto, wide):
        metadata = json.loads((path / "run_metadata.json").read_text(encoding="utf-8"))
        metadata["config"]["power"]["max_current_a"] = float("inf")
        metadata["config"]["power"]["io_voltage"] = float("nan")
        (path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        _republish(path)
    assert "Infinity" in (auto / "run_metadata.json").read_text(encoding="utf-8")
    assert len(calibrate([auto, wide], output=tmp_path / "out.json").samples) == 4
    result = CliRunner().invoke(
        app, ["calibrate", str(auto), str(wide), "--output", str(tmp_path / "cli.json")]
    )
    assert result.exit_code == 0, result.output


def test_a_non_finite_value_in_a_held_field_is_refused_naming_it(tmp_path: Path) -> None:
    held = _with_engine_config(
        bundle(tmp_path / "held", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"transforms": [{"name": "fuse_activation", "options": {"eps": float("inf")}}]},
    )
    with pytest.raises(
        CalibrationError, match="heliaAOT argument transforms.0.options.eps is not a finite number"
    ):
        read_bundle(held)
    metadata = json.loads((held / "run_metadata.json").read_text(encoding="utf-8"))
    metadata["config"]["engine"]["config"]["aot_args"] = {}
    metadata["config"]["profiling"]["window_target_ms"] = float("nan")
    (held / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(held)
    with pytest.raises(
        CalibrationError,
        match="held setting config.profiling.window_target_ms is not a finite number",
    ):
        read_bundle(held)
    # The CLI reports it as an error, never as a traceback.
    result = CliRunner().invoke(app, ["calibrate", str(held), "--output", str(tmp_path / "o.json")])
    assert result.exit_code == 1 and "window_target_ms" in flat(result.output)


def test_heliaaot_rejecting_the_arguments_surfaces_its_own_error(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import effective_config_digest
    from helia_profiler.errors import EngineError

    with pytest.raises(
        EngineError, match=r"(?s)Failed to build heliaAOT ConvertArgs: .*tolerance"
    ) as caught:
        effective_config_digest(
            {"test": {"tolerance": -1}}, module_name="m", prefix="p", aot_platform="apollo510_evb"
        )
    assert caught.value.hint == "Check engine.config_path and engine.config.aot_args."
    bad = _with_engine_config(
        bundle(tmp_path / "bad", cycles={0: 1.0, 1: 1.0, 2: 1.0}), {"optimization": {"knobs": {}}}
    )
    with pytest.raises(
        CalibrationError, match=r"(?s)Failed to build heliaAOT ConvertArgs: .*knobs"
    ):
        read_bundle(bad)


def test_the_dumped_form_makes_spelled_out_defaults_equal(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import effective_config_digest

    def digest(args: dict[str, Any]) -> str:
        return effective_config_digest(args, module_name="m", prefix="p", aot_platform="x")[0]

    plain = digest({})
    assert digest({"optimization": {"goal": "balanced", "kernel": "generic"}}) == plain
    assert digest({"optimization": {}}) == plain
    assert digest({"operators": [{"type": "conv_2d", "optimization": None}]}) == plain
    assert digest(
        {
            "operators": [
                {"type": "conv_2d", "attributes": {}, "optimization": {"kernel": "generic"}}
            ]
        }
    ) == digest({"operators": [{"type": "conv_2d", "optimization": {"kernel": "specialized"}}]})
    assert digest({"optimization": {"goal": "latency"}}) != plain


def test_a_rule_setting_operator_attributes_is_part_of_the_setup(tmp_path: Path) -> None:
    ruled = _aot_config(
        tmp_path,
        "ruled",
        "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
        "    optimization: {kernel: generic}\n",
    )
    swept = _aot_config(
        tmp_path,
        "swept",
        "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
        "    optimization: {kernel: specialized}\n",
    )
    bare = _aot_config(tmp_path, "bare", "optimization: {kernel: generic}\n")
    assert _digest(ruled) == _digest(swept)
    assert _digest(ruled)[0] != _digest(bare)[0]
    one = bundle(
        tmp_path / "one",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        effective_config=_digest(ruled)[0],
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        effective_config=_digest(bare)[0],
    )
    with pytest.raises(CalibrationError, match="engine.effective_config_sha256 is"):
        calibrate([one, other], output=tmp_path / "out.json")


def test_a_rule_with_attributes_digests_the_same_whatever_it_says_of_knobs(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import (
        effective_config_digest,
        without_knob_values,
    )

    def digest(rule: dict[str, Any]) -> str:
        return effective_config_digest(
            {"operators": [rule]}, module_name="m", prefix="p", aot_platform="x"
        )[0]

    attributes = {"memory": "sram"}
    explicit = {"type": "conv_2d", "attributes": attributes, "optimization": {"kernel": "generic"}}
    other = {"type": "conv_2d", "attributes": attributes, "optimization": {"kernel": "specialized"}}
    null = {"type": "conv_2d", "attributes": attributes, "optimization": None}
    omitted = {"type": "conv_2d", "attributes": attributes}
    assert digest(explicit) == digest(other) == digest(null) == digest(omitted)
    (stripped,) = without_knob_values({"operators": [explicit]})["operators"]
    assert "optimization" not in stripped and stripped["attributes"] == attributes
    assert digest(explicit) != digest({"type": "conv_2d", "attributes": {"memory": "dtcm"}})
    # And the same through two configs that differ only in the explicit knob.
    ruled = _aot_config(
        tmp_path,
        "ruled",
        "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
        "    optimization: {kernel: generic}\n",
    )
    silent = _aot_config(
        tmp_path, "silent", "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
    )
    assert _digest(ruled) == _digest(silent)


# -- operator_cycles and the pairing digest --


def test_a_knob_sweep_times_the_swept_operator_once_per_vector(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"

    table = calibrate([auto, wide], output=output)

    written = json.loads(output.read_text(encoding="utf-8"))
    # The precise capture came first, yet each list is in its knobs' canonical order.
    assert written["operator_cycles"] == EXPECTED_SAMPLES
    assert list(written)[-2:] == ["operator_cycles", "capture"]
    assert read_calibration(output) == table

    # The file round-trips unchanged, and merging the captures again changes nothing.
    first = output.read_bytes()
    assert calibrate([wide], output=output, merge=True) == table
    assert output.read_bytes() == first
    assert calibrate([wide, auto], output=output, merge=True) == table
    assert output.read_bytes() == first
    # In a new file the first source's timing of a repeated vector is the one written.
    reverse = calibrate([wide, auto], output=tmp_path / "reverse.json").to_dict()
    assert reverse["operator_cycles"]["0:conv"] == EXPECTED_SAMPLES["0:conv"]
    assert reverse["operator_cycles"]["0:softmax"] == [{"knobs": {}, "cycles": 405.0, "facts": {}}]


def _schema_1(path: Path) -> Path:
    """A file in the shape hpx wrote before calibration schema 2 (0659f2da)."""
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": {"sha256": SHA, "subgraph": 0},
                "conditions": CONDITIONS,
                "operators": {"0:conv": {"accumulation": {"precise": {"cycles": 12000.0}}}},
                "capture": {
                    "setup": {"digest": "0" * 64, "fields": [], "helia_aot_version": "0.33.0"},
                    "prices": {
                        "0:conv": {"accumulation": {"precise": {"others": {}, "facts": {}}}}
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_a_schema_1_file_is_refused_with_the_recalibrate_hint(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = _schema_1(tmp_path / "kws.json")
    before = output.read_bytes()

    result = CliRunner().invoke(
        app, ["calibrate", str(auto), str(wide), "-o", str(output), "--merge"]
    )

    # The CLI wraps the message at the terminal's width, path included, so it
    # is checked whole through the API below.
    assert result.exit_code == 1, result.output
    assert "is calibration schema 1, which this release does not read" in flat(result.output)
    with pytest.raises(CalibrationError) as refused:
        calibrate([auto, wide], output=output, merge=True)
    assert refused.value.hint == (
        "Write it again from the result bundles it came from: hpx calibrate <bundles> "
        f"--output {output}, without --merge."
    )
    assert output.read_bytes() == before
    # The same bundles without --merge write the file again as schema 2.
    calibrate([auto, wide], output=output)
    _check_expected(json.loads(output.read_text(encoding="utf-8")))


def test_the_pairing_field_leaves_the_ordinary_digest_unchanged(tmp_path: Path) -> None:
    recorded = read_bundle(bundle(tmp_path / "recorded", cycles={0: 1.0, 1: 1.0, 2: 1.0}))
    older = read_bundle(
        bundle(tmp_path / "older", cycles={0: 1.0, 1: 1.0, 2: 1.0}, effective_config_pairing=None)
    )
    assert recorded.setup.digest == older.setup.digest
    assert recorded.setup.fields == older.setup.fields
    assert "engine.effective_config_pairing_sha256" in SETUP_EXCLUDED
    assert "engine.effective_config_pairing_sha256" not in recorded.setup.fields
    assert "engine.effective_config_pairing_sha256" in (recorded.setup.pairing_fields or ())
    assert (older.setup.pairing_digest, older.setup.pairing_fields) == (None, None)


def test_a_bundle_without_the_pairing_configuration_calibrates_with_a_null_digest(
    tmp_path: Path,
) -> None:
    old = bundle(tmp_path / "old", cycles={0: 1.0, 1: 1.0, 2: 1.0}, effective_config_pairing=None)
    output = tmp_path / "out.json"
    calibrate([old], output=output)
    setup = json.loads(output.read_text(encoding="utf-8"))["capture"]["setup"]
    assert setup["pairing_digest"] is None and setup["pairing_fields"] is None
    assert re.fullmatch(r"[0-9a-f]{64}", setup["digest"])
    assert read_calibration(output).setup.pairing_digest is None


def test_a_merge_writes_the_pairing_digest_only_when_every_source_shares_it(
    tmp_path: Path,
) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    shared = calibrate([auto, wide], output=output).setup
    assert shared.pairing_digest == read_bundle(auto).setup.pairing_digest
    assert re.fullmatch(r"[0-9a-f]{64}", shared.pairing_digest or "")
    assert (
        shared.pairing_fields and "engine.effective_config_pairing_sha256" in shared.pairing_fields
    )
    assert read_calibration(output).setup == shared

    older = bundle(
        tmp_path / "older",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        effective_config_pairing=None,
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        effective_config_pairing="8" * 64,
    )
    for sources in ([auto, older], [older, auto], [auto, other]):
        table = calibrate(sources, output=tmp_path / "mixed.json")
        assert (table.setup.pairing_digest, table.setup.pairing_fields) == (None, None)
        setup = json.loads((tmp_path / "mixed.json").read_text())["capture"]["setup"]
        assert setup["pairing_digest"] is None and setup["pairing_fields"] is None
    # The file is a source too: one written with a digest, merged with a bundle without one.
    merged = calibrate([older], output=output, merge=True)
    assert merged.setup.pairing_digest is None


def _timed(path: Path, conv: float, softmax: float) -> Path:
    return bundle(path, cycles={0: conv, 1: 3000.0, 2: softmax})


def test_one_agreement_rule_for_every_sample(tmp_path: Path) -> None:
    """A knobbed operator and a knobless one, at the floor's and the tolerance's boundaries."""
    vectors = {"0:conv": r"\{'accumulation': 'precise'\}", "0:softmax": r"\{\}"}
    cases = (
        (100.0, 108.0, 0.02, True),
        (100.0, 109.0, 0.02, False),
        (10000.0, 10204.0, 0.02, True),
        (10000.0, 10205.0, 0.02, False),
        (10000.0, 10008.0, 0.0, True),
        (10000.0, 10009.0, 0.0, False),
    )
    for operator, vector in vectors.items():
        for held, other, tolerance, merges in cases:
            name = f"{operator[2:]}-{held:g}-{other:g}-{tolerance:g}"

            def timed(label: str, cycles: float) -> Path:
                conv, softmax = (cycles, 400.0) if operator == "0:conv" else (12000.0, cycles)
                return _timed(tmp_path / name / label, conv, softmax)

            first, second = timed("first", held), timed("second", other)
            output = tmp_path / name / "out.json"
            if merges:
                table = calibrate([first, second], output=output, tolerance=tolerance)
                (kept,) = [s for (op, _), s in table.samples.items() if op == operator]
                assert kept.cycles == held, name
                continue
            with pytest.raises(
                CalibrationError,
                match=rf"{operator} with the knobs at {vector} takes {held:g} cycles in "
                rf".*first but {other:g} in .*second \(.*% apart, tolerance "
                rf"{tolerance * 100:g}% or 8 cycles\)",
            ):
                calibrate([first, second], output=output, tolerance=tolerance)
    # A file read for --merge is held to the same rule, and keeps its sample.
    output = tmp_path / "kws.json"
    calibrate([_timed(tmp_path / "base", 12000.0, 100.0)], output=output)
    with pytest.raises(
        CalibrationError, match=r"0:softmax with the knobs at \{\} takes 100 cycles in .*kws\.json"
    ):
        calibrate([_timed(tmp_path / "far", 12000.0, 109.0)], output=output, merge=True)
    near = calibrate([_timed(tmp_path / "near", 12000.0, 107.0)], output=output, merge=True)
    assert near.samples[("0:softmax", ())].cycles == 100.0


def test_a_repeat_of_one_vector_is_refused_naming_the_operator_and_the_vector(
    tmp_path: Path,
) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto, wide], output=output)
    data = json.loads(output.read_text(encoding="utf-8"))
    # A knob sweep's swept operator keeps one entry per vector, so a repeat
    # of the fast capture is compared with its own entry only.
    data["operator_cycles"]["0:conv"][0]["cycles"] = 8800.0
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(data), encoding="utf-8")
    # 200 cycles apart is beyond both 1% and the 8-cycle floor.
    with pytest.raises(
        CalibrationError,
        match=r"0:conv with the knobs at \{'accumulation': 'fast'\} takes 8800 cycles in "
        r".*edited\.json but 9000 in .*wide",
    ):
        merge_sources(
            [read_calibration(edited).as_source(str(edited)), read_bundle(wide)], tolerance=0.01
        )


def test_a_malformed_operator_cycles_block_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    whole = json.loads(output.read_text(encoding="utf-8"))
    for name, change, message in (
        ("object", lambda d: d.update(operator_cycles=[]), "operator_cycles is not an object"),
        ("list", lambda d: d["operator_cycles"].update({"0:conv": {}}), "0:conv is not a list"),
        (
            "cycles",
            lambda d: d["operator_cycles"]["0:conv"][0].update(cycles=-1),
            "0:conv has an entry without string knobs",
        ),
        (
            "knobs",
            lambda d: d["operator_cycles"]["0:conv"][0].update(knobs={"accumulation": 1}),
            "0:conv has an entry without string knobs",
        ),
        (
            "no_facts",
            lambda d: d["operator_cycles"]["0:conv"][0].pop("facts"),
            "0:conv has an entry without string knobs",
        ),
        (
            "facts",
            lambda d: d["operator_cycles"]["0:conv"][0].update(facts={"kernel:entry": 1}),
            "0:conv has an entry without string knobs",
        ),
        (
            "empty",
            lambda d: d["operator_cycles"].update({"0:conv": []}),
            "operator_cycles 0:conv lists no entry",
        ),
        (
            "twice",
            lambda d: d["operator_cycles"]["0:conv"].append(d["operator_cycles"]["0:conv"][0]),
            r"0:conv lists the knobs \{'accumulation': 'precise'\} twice",
        ),
        (
            "half",
            lambda d: d["capture"]["setup"].update(pairing_fields=None),
            "records no setup under capture.setup",
        ),
    ):
        data = json.loads(json.dumps(whole))
        change(data)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(CalibrationError, match=message):
            read_calibration(path)


@pytest.mark.parametrize("slow_memory", ["mram", "psram"])
def test_a_synthetic_pair_shares_the_pairing_digest_but_not_the_setup(
    tmp_path: Path, slow_memory: str
) -> None:
    slow, fast = pair(tmp_path, slow_memory)
    # Both are settings hpx's preflight accepts, PSRAM weights included.
    for capture in (slow, fast):
        _preflight_recorded(tmp_path, capture)
    slow_setup, fast_setup = read_bundle(slow).setup, read_bundle(fast).setup

    assert slow_setup.pairing_digest is not None
    assert slow_setup.pairing_digest == fast_setup.pairing_digest
    assert slow_setup.pairing_fields == fast_setup.pairing_fields
    assert slow_setup.digest != fast_setup.digest
    assert "config.model.weights_location" in PAIRING_EXCLUDED
    assert "config.model.weights_location" in slow_setup.fields
    assert "config.model.weights_location" not in (slow_setup.pairing_fields or ())
    assert "engine.effective_config_sha256" not in (slow_setup.pairing_fields or ())
    for held in ("config.model.arena_size", "config.target.clock.cpu", "kernel.build"):
        assert held in (slow_setup.pairing_fields or ()), held
    # Each capture calibrates on its own, and the two files carry one pairing digest.
    slow_file = calibrate([slow], output=tmp_path / "slow.json").setup
    fast_file = calibrate([fast], output=tmp_path / "fast.json").setup
    assert slow_file.pairing_digest == fast_file.pairing_digest == slow_setup.pairing_digest


def _paired(path: Path, aot_args: dict[str, Any], **changes: Any) -> Setup:
    return read_bundle(
        _with_engine_config(bundle(path, cycles={0: 1.0, 1: 1.0, 2: 1.0}, **changes), aot_args)
    ).setup


def _tensors(*rules: dict[str, Any], **memory: Any) -> dict[str, Any]:
    return {"memory": {"tensors": list(rules), **memory}}


def test_the_pairing_digest_strips_only_the_constants_placement(tmp_path: Path) -> None:
    plain = _paired(tmp_path / "plain", _tensors())
    constant = _paired(
        tmp_path / "constant",
        _tensors(
            {
                "type": "Constant",
                "attributes": {"memory": "mram", "constant_destination_memory": "dtcm"},
            },
            {"type": "constant", "id": "0:w", "attributes": {"memory": "sram"}},
        ),
    )
    assert constant.pairing_digest == plain.pairing_digest
    assert constant.digest != plain.digest
    # A scratch rule and the pool constraints are held: they change what the arena is.
    dtcm = _paired(
        tmp_path / "dtcm", _tensors({"type": "scratch", "attributes": {"memory": "dtcm"}})
    )
    sram = _paired(
        tmp_path / "sram", _tensors({"type": "scratch", "attributes": {"memory": "sram"}})
    )
    assert dtcm.pairing_digest != sram.pairing_digest
    small = _paired(tmp_path / "small", _tensors(constraints=[{"name": "dtcm", "max_size": 65536}]))
    large = _paired(
        tmp_path / "large", _tensors(constraints=[{"name": "dtcm", "max_size": 131072}])
    )
    assert small.pairing_digest != large.pairing_digest
    # The arena's location stays held as well.
    moved = _paired(tmp_path / "moved", _tensors(), arena_location="sram")
    assert moved.pairing_digest != plain.pairing_digest


def test_without_constant_placement_keeps_whatever_else_a_rule_sets() -> None:
    from helia_profiler.engines.helia_aot.compile import without_constant_placement

    args = _tensors(
        {"type": "constant", "id": "0:w", "attributes": {"memory": "mram", "optimization": "x"}},
        {"type": "CONSTANT", "attributes": {"constant_destination_memory": "dtcm"}},
        {"type": "constant"},
        {"type": "scratch", "attributes": {"memory": "dtcm"}},
        {"type": "persistent", "attributes": {"memory": "sram"}},
        planner="greedy",
    )
    before = json.loads(json.dumps(args))
    assert without_constant_placement(args) == _tensors(
        {"type": "constant", "id": "0:w", "attributes": {"optimization": "x"}},
        {"type": "scratch", "attributes": {"memory": "dtcm"}},
        {"type": "persistent", "attributes": {"memory": "sram"}},
        planner="greedy",
    )
    assert args == before
    assert without_constant_placement({"optimization": {"goal": "latency"}}) == {
        "optimization": {"goal": "latency"}
    }


def test_a_constant_rule_heliaaot_refuses_is_refused_by_both_digests() -> None:
    from helia_profiler.engines.helia_aot.compile import (
        effective_config_digest,
        effective_config_pairing_digest,
        without_constant_placement,
    )
    from helia_profiler.errors import EngineError

    odd = _tensors({"type": "constant", "attributes": "zz"})
    assert without_constant_placement(odd) == odd
    names = {"module_name": "m", "prefix": "p", "aot_platform": "x"}
    with pytest.raises(EngineError, match="Failed to build heliaAOT ConvertArgs") as ordinary:
        effective_config_digest(odd, **names)
    with pytest.raises(EngineError, match="Failed to build heliaAOT ConvertArgs") as pairing:
        effective_config_pairing_digest(odd, **names)
    assert str(pairing.value) == str(ordinary.value)
    assert pairing.value.hint == ordinary.value.hint


def _capture(tmp_path: Path, name: str, weights_location: str, aot_args: dict[str, Any]):
    """A profile config as a capture runs it: one weights memory, inline heliaAOT arguments."""
    from helia_profiler.config import load_config

    model = tmp_path / f"{name}.tflite"
    model.write_bytes(b"\x00")
    return load_config(
        None,
        {
            "model": {"path": str(model), "weights_location": weights_location},
            "engine": {"type": "helia-aot", "config": {"aot_args": aot_args}},
            "target": {"board": "apollo510_evb"},
            "work_dir": str(tmp_path / "work"),
        },
    )


def _preflight(config) -> None:
    """hpx's preflight check of a heliaAOT placement; raises ConfigError on a refused one."""
    from helia_profiler.engines.helia_aot.adapter import HeliaAOTAdapter

    HeliaAOTAdapter().check_psram_placement(config)


def _preflight_recorded(tmp_path: Path, bundle_path: Path) -> None:
    """Preflight the settings a synthetic bundle records, as if a capture ran them."""
    recorded = json.loads((bundle_path / "run_metadata.json").read_text(encoding="utf-8"))["config"]
    _preflight(
        _capture(
            tmp_path,
            f"preflight-{bundle_path.parent.name}-{bundle_path.name}",
            recorded["model"]["weights_location"],
            recorded["engine"]["config"]["aot_args"],
        )
    )


def _pairing(config) -> str:
    from helia_profiler.engines.helia_aot.compile import (
        effective_aot_args,
        effective_config_pairing_digest,
    )

    return effective_config_pairing_digest(
        effective_aot_args(config),
        module_name="hpx_model",
        prefix="hpx",
        aot_platform="apollo510_evb",
    )[0]


def test_the_compiler_configuration_pairs_across_the_weights_memory(tmp_path: Path) -> None:
    """hpx's own constant rule follows weights_location; the pairing digest drops it."""
    external = {"memory": dict(PAIR_AOT_MEMORY)}
    tcm, mram, psram = (
        _capture(tmp_path, name, name, external) for name in ("tcm", "mram", "psram")
    )
    for config in (tcm, mram, psram):
        _preflight(config)
    assert len({_digest(config)[0] for config in (tcm, mram, psram)}) == 3
    assert _pairing(tcm) == _pairing(mram) == _pairing(psram)
    scratch = _capture(
        tmp_path,
        "scratch",
        "tcm",
        {
            "memory": {
                **PAIR_AOT_MEMORY,
                "tensors": [{"type": "scratch", "attributes": {"memory": "sram"}}],
            }
        },
    )
    assert _pairing(scratch) != _pairing(tcm)
    constrained = _capture(
        tmp_path,
        "constrained",
        "tcm",
        {"memory": {**PAIR_AOT_MEMORY, "constraints": [{"name": "dtcm", "max_size": 65536}]}},
    )
    assert _pairing(constrained) != _pairing(tcm)


def test_a_default_tcm_capture_does_not_pair_with_an_accepted_psram_one(tmp_path: Path) -> None:
    """hpx accepts PSRAM weights only with allocate_arenas false, and that setting is held."""
    external = {"memory": dict(PAIR_AOT_MEMORY)}
    default_tcm = _capture(tmp_path, "default-tcm", "tcm", {})
    external_tcm = _capture(tmp_path, "external-tcm", "tcm", external)
    psram = _capture(tmp_path, "psram", "psram", external)
    for config in (default_tcm, external_tcm, psram):
        _preflight(config)
    with pytest.raises(ConfigError, match="requires external-arena mode"):
        _preflight(_capture(tmp_path, "default-psram", "psram", {}))
    assert _pairing(default_tcm) != _pairing(psram)
    assert _pairing(external_tcm) == _pairing(psram)

    # And the bundles those captures write pair the same way.
    def captured(name: str, config, aot_memory: dict[str, Any] | None) -> Setup:
        memory = str(config.model.weights_location.value)
        conditions = {**CONDITIONS, "weights_memory": "dtcm" if memory == "tcm" else memory}
        if memory == "psram":
            conditions["external_memory_clock"] = 48
        return read_bundle(
            bundle(
                tmp_path / name,
                cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
                conditions=conditions,
                weights_location=memory,
                aot_memory=aot_memory,
                effective_config=_digest(config)[0],
                effective_config_pairing=_pairing(config),
            )
        ).setup

    slow = captured("psram-bundle", psram, PAIR_AOT_MEMORY)
    assert captured("default-tcm-bundle", default_tcm, None).pairing_digest != slow.pairing_digest
    assert captured("external-tcm-bundle", external_tcm, PAIR_AOT_MEMORY).pairing_digest == (
        slow.pairing_digest
    )


def test_every_helia_aot_bundle_records_the_pairing_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    from helia_profiler.engines.helia_aot.compile import (
        _DEFAULT_MODULE_NAME,
        _DEFAULT_PREFIX,
        effective_aot_args,
        effective_config_pairing_digest,
    )
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.report.metadata import _metadata_to_dict
    from helia_profiler.stages import prepare_engine
    from tests.test_helia_aot_adapter import _aot_cfg, _prepare

    config = _aot_cfg({})
    artifacts = _prepare(monkeypatch, tmp_path, config)
    expected, _ = effective_config_pairing_digest(
        effective_aot_args(config),
        module_name=_DEFAULT_MODULE_NAME,
        prefix=_DEFAULT_PREFIX,
        aot_platform="apollo510_evb",
    )
    assert artifacts.effective_config_pairing_sha256 == expected
    assert artifacts.effective_config_pairing_sha256 != artifacts.effective_config_sha256

    class _Adapter:
        name = "heliaAOT"

        def prepare(self, _config, _work_dir):
            return artifacts

    monkeypatch.setattr(prepare_engine, "get_adapter", lambda _type: _Adapter())
    ctx = PipelineContext(config=config, work_dir=tmp_path / "stage")
    prepare_engine.PrepareEngineStage().run(ctx)
    engine = _metadata_to_dict(ctx.run_metadata)["engine"]
    assert engine["effective_config_pairing_sha256"] == expected
    assert engine["effective_config_sha256"] == artifacts.effective_config_sha256


# -- calibration schema 2: the file's shape, order and exported schema --


def test_operators_keep_plan_order(tmp_path: Path) -> None:
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    for op, name in zip(ops, ("0:zeta", "0:alpha", "0:mid"), strict=True):
        op["id"] = name
    first = bundle(tmp_path / "first", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, plan_ops=ops)
    output = tmp_path / "out.json"
    calibrate([first], output=output)
    assert list(json.loads(output.read_text())["operator_cycles"]) == ["0:zeta", "0:alpha", "0:mid"]
    # A later capture that adds a vector for the middle operator moves nothing.
    moved = bundle(
        tmp_path / "moved",
        cycles={0: 12000.0, 1: 2400.0, 2: 400.0},
        plan_ops=_knob(ops, "0:alpha", "accumulation", "fast"),
    )
    calibrate([moved], output=output, merge=True)
    written = json.loads(output.read_text())["operator_cycles"]
    assert list(written) == ["0:zeta", "0:alpha", "0:mid"]
    assert len(written["0:alpha"]) == 2


def test_merge_rewrites_a_file_byte_for_byte(tmp_path: Path) -> None:
    """Re-merging bundles a file already holds changes none of its bytes."""
    ops = _with_facts({})
    a = bundle(tmp_path / "a", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, plan_ops=ops)
    b = bundle(
        tmp_path / "b",
        cycles={0: 9000.0, 1: 3030.0, 2: 405.0},
        plan_ops=_knob(ops, "0:conv", "accumulation", "fast"),
    )
    output = tmp_path / "out.json"
    calibrate([a, b], output=output)
    written = output.read_bytes()
    facts = json.loads(written)["operator_cycles"]["0:fc"][0]["facts"]
    assert list(facts) == sorted(facts)

    calibrate([b], output=output, merge=True)
    assert output.read_bytes() == written
    calibrate([b, a], output=output, merge=True)
    assert output.read_bytes() == written


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (lambda d: d.update(operators={}), "operators"),
        (lambda d: d["capture"].update(prices={}), "capture.prices"),
        (lambda d: d["model"].update(x=1), "model.x"),
        (lambda d: d["capture"]["setup"].update(x=1), "capture.setup.x"),
        (
            lambda d: d["operator_cycles"]["0:fc"][0].update(stdev=1.0),
            "operator_cycles[0:fc][0].stdev",
        ),
    ],
)
def test_a_field_schema_2_does_not_define_is_refused(
    tmp_path: Path, change: Any, where: str
) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    data = json.loads(output.read_text(encoding="utf-8"))
    change(data)
    output.write_text(json.dumps(data), encoding="utf-8")
    before = output.read_bytes()
    with pytest.raises(CalibrationError) as refused:
        calibrate([wide], output=output, merge=True)
    assert refused.value.args[0] == (
        f"Calibration file {output} holds {where}, which calibration schema 2 does not define."
    )
    assert refused.value.hint == _RECALIBRATE
    assert output.read_bytes() == before
    # A fact of a name schema 2 does not define is a malformed entry, named by its operator.
    noted = tmp_path / "noted.json"
    calibrate([auto], output=noted)
    data = json.loads(noted.read_text(encoding="utf-8"))
    data["operator_cycles"]["0:fc"][0]["facts"]["note"] = "x"
    noted.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(
        CalibrationError,
        match=r"operator_cycles 0:fc has an entry without string knobs, finite non-negative "
        r"cycles and string facts named fixed:<name> or kernel:entry\.",
    ):
        read_calibration(noted)


@pytest.mark.parametrize(
    "conditions",
    [
        {"target": "apollo510_evb", "weights_memory": "dtcm"},
        {**CONDITIONS, "external_memory_clock": None},
        {**CONDITIONS, "npu_hz": 96_000_000},
    ],
)
def test_a_bundle_with_conditions_as_recorded_writes_a_valid_file(
    tmp_path: Path, conditions: dict[str, Any]
) -> None:
    """The writer copies the recorded conditions verbatim, and the schema accepts them."""
    recorded = bundle(
        tmp_path / "recorded", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, conditions=conditions
    )
    output = tmp_path / "out.json"
    calibrate([recorded], output=output)
    assert json.loads(output.read_text(encoding="utf-8"))["conditions"] == conditions
    assert read_calibration(output).conditions == conditions


def test_the_committed_calibration_file_is_what_the_writer_writes(tmp_path: Path) -> None:
    """``capture.setup`` depends on the installed heliaAOT, so it is checked for form only."""
    output = tmp_path / "golden.json"
    table = calibrate(golden_bundles(tmp_path / "bundles"), output=output)
    assert (table.operator_count, len(table.samples)) == (3, 6)
    written = json.loads(output.read_text(encoding="utf-8"))
    committed = GOLDEN.read_bytes()
    setup = written["capture"]["setup"]
    assert re.fullmatch(r"[0-9a-f]{64}", setup["digest"])
    assert re.fullmatch(r"[0-9a-f]{64}", setup["pairing_digest"] or "")
    assert setup["fields"] and setup["pairing_fields"]
    written["capture"]["setup"] = json.loads(committed)["capture"]["setup"]
    assert (json.dumps(written, indent=2) + "\n").encode() == committed, (
        f"{GOLDEN} is not what the writer writes; write it again with "
        "python -m tests.calibration_bundles tests/fixtures/calibration/hpx_calibration.v2.json "
        "and copy it into heliaOptimus"
    )
