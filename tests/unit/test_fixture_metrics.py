"""Fixture measurements reject incomplete attribution and stale observation bindings."""

import json
from dataclasses import replace
from hashlib import sha256

import pytest

from helia_profiler import fixture_metrics as metrics
from helia_profiler.engines import EngineType
from helia_profiler.fixture import FixtureBuild, FixtureTimingScope
from helia_profiler.fixture_capture import FixtureCaptureResult, FixtureMemory, FixtureTiming
from helia_profiler.fixture_observation import FixtureEnergyWindow, summarize_fixture_measurements
from helia_profiler.fixture_runtime import FixtureFile
from helia_profiler.fixture_target import supported_fixture_target
from helia_profiler.hostenv.elf_inventory import (
    ElfSection,
    LoadSegment,
    SectionInventory,
    SymbolEntry,
)
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.power.metadata import MeasurementScope, PowerIntegrity, PowerMetadata


@pytest.fixture
def rig(tmp_path, monkeypatch):
    def pin(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return FixtureFile(path, sha256(data).hexdigest())

    elf = pin("firmware.elf", b"elf")
    image = pin("firmware.bin", b"image")
    link_map = pin(
        "firmware.map",
        b"VMA LMA Size Align Out In Symbol\n410000 410000 10 4 runtime.a(conv.o):(.text.arm_conv)\n410000 410000 10 4 runtime.a(alias.o):(.text.arm_alias)\n",
    )
    sections = (
        ElfSection(".text", 0x410000, 16, False, True, 1),
        ElfSection(".data", 0x20000000, 12, False, True, 2),
        ElfSection(".bss", 0x20000010, 20, True, True, 3),
        ElfSection(".stack", 0x20000100, 64, True, True, 4),
        ElfSection(".heap", 0x20000200, 128, True, True, 5, True),
        ElfSection(".sram_bss", 0x20080000, 100, True, True, 6),
    )
    inventory = SectionInventory(sections, (LoadSegment(0x410000, 0x410000, 20, 20),))
    monkeypatch.setattr(metrics, "section_inventory", lambda *a, **kw: inventory)
    monkeypatch.setattr(
        metrics,
        "symbol_inventory",
        lambda *a, **kw: ((SymbolEntry("arm_conv", 0x410000, 16, "T"),), 0),
    )
    args = dict(
        elf=elf, image=image, link_map=link_map, target=supported_fixture_target(), toolchain="atfe"
    )
    output = pin("output.bin", b"output")
    build = FixtureBuild(
        "fixture",
        tmp_path,
        elf,
        (),
        output,
        True,
        None,
        image,
        None,
        link_map,
        FixtureTimingScope.RESTORE_AND_INVOKE,
        EngineType.TFLM,
        "compiled",
        10,
        2,
        "intent",
        supported_fixture_target(),
        None,
    )
    identity = pin(
        "identity.json",
        json.dumps({"elf_sha256": elf.sha256, "image_sha256": image.sha256}).encode(),
    )
    capture = FixtureCaptureResult(
        "success",
        build.timing_scope,
        output,
        0,
        0,
        FixtureTiming(32768, 10, 2, 32768, 300, 96000000, build.timing_scope),
        FixtureMemory(100, 70, 80, 80),
        (identity, output),
    )
    return args, inventory, build, capture


def test_footprint_separates_reservation_occupancy_and_attribution(rig):
    args, inventory, build, capture = rig
    f = metrics.inspect_fixture_footprint(**args)
    assert f.linked_ram_bytes.value == 196
    assert f.static_ram_bytes.value == 132
    assert f.stack_reserved_bytes.value == 64
    assert f.heap_reserved_bytes.value == 128
    assert f.load_image_bytes.value == 20
    assert f.code_bytes.value == 16  # aliases/overlap are not added twice
    assert f.kernel_code_bytes.value is None and f.kernel_code_bytes.reason
    assert f.linked_kernel_symbols[0].name == "arm_conv"
    assert f.stack_peak_bytes.value is None and f.heap_peak_bytes.value is None
    result = summarize_fixture_measurements(build, capture, f)
    assert result.latency_s.value == 0.1
    assert result.arena_capacity_bytes.value == 100
    assert result.arena_used_after_invoke_bytes.value == 80
    assert result.energy_per_inference_j.value is None


@pytest.mark.parametrize("case", ["partial", "outside", "straddles"])
def test_partial_or_unattributed_sections_never_publish_small_total(rig, monkeypatch, case):
    args, inventory, *_ = rig
    if case == "partial":
        inventory = replace(inventory, unparsed_rows=1)
    else:
        inventory = replace(
            inventory,
            sections=inventory.sections
            + (
                ElfSection(
                    ".lost", 0xFFFFFFF0 if case == "outside" else 0x2007FFF0, 32, True, True, 7
                ),
            ),
        )
    monkeypatch.setattr(metrics, "section_inventory", lambda *a, **kw: inventory)
    assert metrics.inspect_fixture_footprint(**args).linked_ram_bytes.value is None


@pytest.mark.parametrize("bad", ["image", "map", "method", "render"])
def test_observation_binding_rejects_wrong_variant(rig, bad):
    args, _, build, capture = rig
    footprint = metrics.inspect_fixture_footprint(**args)
    if bad == "image":
        build = replace(build, binary=replace(build.binary, sha256="0" * 64))
    elif bad == "map":
        build = replace(build, link_map=replace(build.link_map, sha256="0" * 64))
    elif bad == "method":
        build = replace(build, iterations=11)
    else:
        build = replace(build, built=False, build_identity=None)
    with pytest.raises(ValueError):
        summarize_fixture_measurements(build, capture, footprint)


def power_window(build, capture):
    metadata = PowerMetadata(
        gating_method="gpi_stream+host_stats_integral",
        measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
        integrity=PowerIntegrity.VALID,
        window_count=1,
        gate_rise_observed=True,
        gate_fall_observed=True,
    )
    power = PowerResult(
        PowerSummary(0.1, 0.3, 0.2, 0.3, 1.0, 100),
        gated_windows=[GatedPowerWindow(0, 1, 1, 0.1, 0.3, 0.1, 0.3, 0.2, 100)],
        metadata=metadata,
    )
    return FixtureEnergyWindow(
        build.build_identity,
        power,
        10,
        1.0,
        "confirmed whole board",
        "js320-25qg",
        capture.artifacts,
    )


def test_gated_energy_and_invalid_windows(rig):
    args, _, build, capture = rig
    footprint = metrics.inspect_fixture_footprint(**args)
    energy = power_window(build, capture)
    result = summarize_fixture_measurements(build, capture, footprint, energy=energy)
    assert result.energy_j.value == 0.3 and result.energy_per_inference_j.value == 0.03
    assert result.idle_subtracted is False
    for bad in [
        replace(energy, completed_calls=9),
        replace(energy, build_identity="old"),
        replace(energy, firmware_duration_s=2),
        replace(energy, measured_domain=""),
        replace(energy, power=replace(energy.power, gated_windows=[])),
        replace(energy, power=replace(energy.power, metadata=PowerMetadata())),
    ]:
        with pytest.raises(ValueError):
            summarize_fixture_measurements(build, capture, footprint, energy=bad)


@pytest.mark.parametrize(
    "value,reason", [(None, None), (float("nan"), None), (1, "unknown"), (True, None)]
)
def test_metric_cannot_confuse_unavailable_and_measured(value, reason):
    with pytest.raises(ValueError):
        metrics.FixtureMetric(value, "B", "test", reason)


def test_cpp_anonymous_symbol_rows_are_not_map_inputs():
    text = "VMA LMA Size Align Out In Symbol\n410000 410000 10 4 rt.a(a.o):(.text.code)\n410001 410001 10 1 tflite::(anonymous namespace)::Invoke()\n"
    assert metrics._linked_text_size(text).value == 16
    assert metrics._linked_text_size(text + "malformed.a(o):(.text.bad)\n").value is None


def test_map_changed_after_inspection_is_rejected(rig):
    args, _, build, capture = rig
    footprint = metrics.inspect_fixture_footprint(**args)
    footprint.link_map.path.write_text("changed after inspection")
    with pytest.raises(ValueError, match="hash mismatch"):
        summarize_fixture_measurements(build, capture, footprint)
