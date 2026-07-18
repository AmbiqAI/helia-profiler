"""Tests for the immutable interactive Session API."""

from __future__ import annotations

from pathlib import Path
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

import helia_profiler as hpx
from helia_profiler.errors import ConfigError
from helia_profiler.platform import CoreArch
from helia_profiler.results import FirmwareMeta, PmuResult, ProfileResult
from helia_profiler.target.probe.jlink import JLinkProbe, JLinkProbeMatch


def test_session_branches_without_mutating_parent() -> None:
    base = hpx.Session().with_model("model.tflite").with_engine(
        "helia-rt", config={"nested": {"base": True}}
    )

    child = base.with_engine("helia-aot", config={"nested": {"child": True}})

    assert base.resolve().engine.type is hpx.EngineType.HELIA_RT
    assert base.resolve().engine.config == {"nested": {"base": True}}
    assert child.resolve().engine.type is hpx.EngineType.HELIA_AOT
    assert child.resolve().engine.config == {
        "nested": {"base": True, "child": True}
    }
    assert child != base


def test_session_defensively_copies_nested_overrides() -> None:
    engine_config = {"passes": ["first"], "nested": {"value": 1}}
    session = hpx.Session().with_engine("helia-rt", config=engine_config)
    engine_config["passes"].append("second")
    engine_config["nested"]["value"] = 2

    resolved = session.with_model("model.tflite").resolve()

    assert resolved.engine.config == {
        "passes": ["first"],
        "nested": {"value": 1},
    }


def test_session_recursively_merges_mappings_and_replaces_lists() -> None:
    base = hpx.Session().with_overrides(
        {
            "model": {"path": "model.tflite"},
            "engine": {"type": "helia-rt", "config": {"items": [1], "left": 1}},
        }
    )

    child = base.with_overrides(
        {"engine": {"config": {"items": [2, 3], "right": 2}}}
    )

    assert child.resolve().engine.config == {
        "items": [2, 3],
        "left": 1,
        "right": 2,
    }


def test_session_overrides_yaml_and_preserves_yaml_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "hpx.yml"
    config_path.write_text(
        """
model:
  path: yaml.tflite
engine:
  type: helia-rt
target:
  board: apollo4p_blue_kxr_evb
profiling:
  iterations: 20
  warmup: 3
""".strip()
    )

    config = (
        hpx.Session.from_yaml(config_path)
        .with_engine("helia-aot")
        .with_profiling(iterations=50)
        .resolve()
    )

    assert config.model.path == Path("yaml.tflite")
    assert config.engine.type is hpx.EngineType.HELIA_AOT
    assert config.target.board == "apollo4p_blue_kxr_evb"
    assert config.profiling.iterations == 50
    assert config.profiling.warmup == 3


def test_session_snapshots_yaml_at_construction(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "hpx.yml"
    config_path.write_text("model:\n  path: original.tflite\n")
    session = hpx.Session.from_yaml(config_path)

    config_path.write_text("model:\n  path: changed.tflite\n")
    monkeypatch.chdir(tmp_path.parent)

    assert session.resolve().model.path == Path("original.tflite")


def test_session_can_clear_optional_yaml_values(tmp_path: Path) -> None:
    config_path = tmp_path / "hpx.yml"
    config_path.write_text(
        "model:\n  path: model.tflite\ntarget:\n  jlink_serial: '123'\n"
    )

    config = hpx.Session.from_yaml(config_path).with_target(jlink_serial=None).resolve()

    assert config.target.jlink_serial is None


def test_session_uses_existing_config_coercion() -> None:
    config = (
        hpx.Session()
        .with_model("model.tflite")
        .with_target(toolchain="gcc", transport="usb_cdc")
        .resolve()
    )

    assert config.model.path == Path("model.tflite")
    assert config.target.toolchain is hpx.Toolchain.ARM_NONE_EABI_GCC
    assert config.target.transport is hpx.Transport.USB_CDC


def test_session_requires_model() -> None:
    with pytest.raises(ConfigError, match="model.path is required"):
        hpx.Session().resolve()


def test_profile_model_argument_overrides_session_model() -> None:
    expected = ProfileResult(pmu=PmuResult(meta=FirmwareMeta()))
    session = hpx.Session().with_model("stored.tflite")

    with patch("helia_profiler.api.profile", return_value=expected) as profile:
        result = session.profile("argument.tflite")

    assert result is expected
    assert profile.call_args.args[0].model.path == Path("argument.tflite")


def test_session_analyze_uses_resolved_engine_and_board() -> None:
    expected = object()
    session = (
        hpx.Session()
        .with_model("model.tflite")
        .with_engine("helia-aot")
        .with_target(board="apollo510_evb")
    )

    with patch(
        "helia_profiler.model_analysis.analyze_for_engine", return_value=expected
    ) as analyze:
        result = session.analyze()

    assert result is expected
    analyze.assert_called_once_with(
        Path("model.tflite"),
        engine=hpx.EngineType.HELIA_AOT,
        board="apollo510_evb",
    )


def test_session_compare_accepts_profile_results(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline = ProfileResult(
        pmu=PmuResult(meta=FirmwareMeta()),
        report_paths=[baseline_dir / "summary.json"],
    )
    candidate = ProfileResult(
        pmu=PmuResult(meta=FirmwareMeta()),
        report_paths=[candidate_dir / "summary.json"],
    )
    expected = object()

    with (
        patch("helia_profiler.compare.compare_runs", return_value=expected) as compare,
        patch("helia_profiler.compare.write_compare_artifacts") as write,
    ):
        result = hpx.Session().compare(
            baseline,
            candidate,
            output_dir=tmp_path / "comparison",
        )

    assert result is expected
    compare.assert_called_once_with(baseline_dir, candidate_dir)
    write.assert_called_once_with(expected, tmp_path / "comparison")


def test_session_discovery_returns_typed_values(monkeypatch) -> None:
    doctor = hpx.DoctorResult((hpx.DoctorCheck("tool", "tool", True),))
    port = hpx.SerialPortInfo(device="/dev/ttyUSB0", kind="serial")
    monkeypatch.setattr(
        "helia_profiler.doctor.inspect_environment", lambda **_kwargs: doctor
    )
    monkeypatch.setattr(
        "helia_profiler.transport.ports.list_serial_ports",
        lambda **_kwargs: (port,),
    )

    session = hpx.Session()

    assert session.doctor() is doctor
    assert session.doctor().ok
    assert session.ports() == (port,)
    assert hpx.EngineType.HELIA_RT in session.engines()
    assert "cpu" in session.counter_groups()
    assert all(counter.group == "cpu" for counter in session.counters("cpu"))
    assert any(board.name == "apollo510_evb" for board in session.boards())


def test_session_doctor_requires_rtt_sources_for_rtt_transport(
    tmp_path: Path, monkeypatch
) -> None:
    rtt_root = tmp_path / "segger-rtt"
    (rtt_root / "RTT").mkdir(parents=True)
    (rtt_root / "Config").mkdir()
    (rtt_root / "RTT" / "SEGGER_RTT.c").write_text("")
    (rtt_root / "RTT" / "SEGGER_RTT.h").write_text("")
    (rtt_root / "Config" / "SEGGER_RTT_Conf.h").write_text("")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = hpx.Session().with_target(
        transport="rtt", segger_rtt_path=rtt_root
    ).doctor()

    rtt_check = next(check for check in result.checks if "RTT source" in check.label)
    assert rtt_check.available
    assert rtt_check.path == str(rtt_root.resolve())


def test_session_show_renders_doctor_and_returns_original_value() -> None:
    result = hpx.DoctorResult(
        (
            hpx.DoctorCheck("Required tool", "tool", True, path="/usr/bin/tool"),
            hpx.DoctorCheck("Optional tool", "optional", False, required=False),
        )
    )
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, width=100)

    returned = hpx.Session().show(result, console=console)

    assert returned is result
    output = stream.getvalue()
    assert "Environment Check" in output
    assert "Required tool" in output
    assert "All required dependencies found" in output


def test_session_show_renders_probe_table() -> None:
    probes = (JLinkProbe(serial="123", product="J-Link OB", connection="USB"),)
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, width=100)

    returned = hpx.Session().show(probes, console=console)

    assert returned is probes
    output = stream.getvalue()
    assert "J-Link Probes" in output
    assert "123" in output
    assert "J-Link OB" in output


def test_session_probe_inspection_and_matching_use_target(monkeypatch) -> None:
    probe = JLinkProbe(serial="123")
    match = JLinkProbeMatch(probe=probe, detected_core=CoreArch.CORTEX_M55)
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.list_connected_probes", lambda: [probe]
    )
    inspect = patch(
        "helia_profiler.target.probe.jlink.inspect_probe_target", return_value=match
    )
    resolve = patch(
        "helia_profiler.target.probe.jlink.resolve_probe_serial", return_value="123"
    )

    session = hpx.Session().with_target(board="apollo510_evb", jlink_serial="123")
    with inspect as inspect_target, resolve as resolve_serial:
        assert session.probes() == (probe,)
        assert session.inspect_probes() == (match,)
        assert session.match_probe() == "123"

    assert inspect_target.call_args.kwargs["device"] == "AP510NFA-CBR"
    assert resolve_serial.call_args.kwargs["requested_serial"] == "123"


def test_session_reset_uses_board_and_serial(monkeypatch) -> None:
    calls: list[dict[str, str | None]] = []

    def fake_reset(*, device: str, jlink_serial: str | None = None) -> None:
        calls.append({"device": device, "serial": jlink_serial})

    monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", fake_reset)

    hpx.Session().with_target(
        board="apollo4p_blue_kxr_evb", jlink_serial="456"
    ).reset()

    assert calls == [{"device": "AMAP42KP-KBR", "serial": "456"}]


def test_session_reset_rejects_unknown_kind() -> None:
    with pytest.raises(ConfigError, match="reset kind"):
        hpx.Session().reset(kind="typo")  # type: ignore[arg-type]
