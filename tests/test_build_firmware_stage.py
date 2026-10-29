from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.build_firmware import BuildFirmwareStage


@pytest.fixture(autouse=True)
def _no_symbol_inventory(monkeypatch):
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.symbol_inventory", lambda *a, **kw: None
    )


def test_missing_binary_sections_does_not_fail_successful_build(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    build_dir = tmp_path / "build"
    binary_path = build_dir / "hpx_profiler"
    progress = []
    ctx.progress_sink = progress.append

    monkeypatch.setattr("helia_profiler.firmware.build_app", lambda _ctx: (build_dir, binary_path))
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.compiler_version",
        lambda *_args, **_kwargs: "clang version",
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.cmake_version",
        lambda **_kwargs: "cmake version",
    )

    BuildFirmwareStage().run(ctx)

    assert ctx.binary_sections is None
    assert ctx.profile_run is not None
    assert progress[-1].message == "Profile firmware ready"


def test_measured_regions_wiring_passes_soc_and_linker_profile(tmp_path: Path, monkeypatch) -> None:
    """#177: the stage->measure_memory_regions wiring — the ctx.soc gate,
    ctx.soc gate, the engine-config linker_profile extraction, and the
    argument order."""
    from helia_profiler.platform import get_soc

    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-aot", "config": {"linker_profile": "itcm"}},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.soc = get_soc("apollo510")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    build_dir = tmp_path / "build"
    binary_path = build_dir / "hpx_profiler"
    ctx.progress_sink = lambda *_a, **_k: None

    calls = []

    def _measure(bp, toolchain, soc, *, linker_profile, timeout_s):
        calls.append((bp, toolchain, soc.name, linker_profile))
        return None  # itcm profile degrades inside the real function

    monkeypatch.setattr("helia_profiler.firmware.build_app", lambda _ctx: (build_dir, binary_path))
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("helia_profiler.stages.build_firmware.measure_memory_regions", _measure)
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.compiler_version",
        lambda *_args, **_kwargs: "v",
    )
    monkeypatch.setattr("helia_profiler.stages.build_firmware.cmake_version", lambda **_k: "v")

    BuildFirmwareStage().run(ctx)

    assert calls == [(binary_path, config.target.toolchain, "apollo510", "itcm")]
    assert ctx.memory_regions is None


def test_measured_regions_skipped_without_resolved_soc(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    ctx.progress_sink = lambda *_a, **_k: None

    def _explode(*_a, **_k):
        raise AssertionError("must not measure without a resolved SoC")

    monkeypatch.setattr(
        "helia_profiler.firmware.build_app",
        lambda _ctx: (tmp_path / "build", tmp_path / "build" / "fw"),
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("helia_profiler.stages.build_firmware.measure_memory_regions", _explode)
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.compiler_version",
        lambda *_args, **_kwargs: "v",
    )
    monkeypatch.setattr("helia_profiler.stages.build_firmware.cmake_version", lambda **_k: "v")

    BuildFirmwareStage().run(ctx)
    assert ctx.memory_regions is None


def test_partial_nm_listing_is_refused(tmp_path: Path, monkeypatch) -> None:
    """A partial nm listing must yield NO symbols and NO reconciliation --
    never an understated one (#179)."""
    from helia_profiler.platform import get_soc

    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.soc = get_soc("apollo510")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    ctx.progress_sink = lambda *_a, **_k: None

    monkeypatch.setattr(
        "helia_profiler.firmware.build_app",
        lambda _ctx: (tmp_path / "build", tmp_path / "build" / "fw"),
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.measure_memory_regions",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.symbol_inventory",
        lambda *_a, **_k: ((), 3),  # partial: 3 unparsed rows
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.compiler_version",
        lambda *_a, **_k: "v",
    )
    monkeypatch.setattr("helia_profiler.stages.build_firmware.cmake_version", lambda **_k: "v")

    BuildFirmwareStage().run(ctx)
    assert ctx.memory_symbols is None
    assert ctx.memory_reconciliation is None


def test_find_target_binary_is_deterministic(tmp_path: Path) -> None:
    """#180: glob order is filesystem-dependent — the shallowest match
    must win reproducibly, and the per-pattern loop keeps extension
    precedence (a bare/axf match beats a fresh .elf in a later pattern)."""
    from helia_profiler.firmware import find_target_binary

    deep = tmp_path / "sub" / "deeper"
    deep.mkdir(parents=True)
    (deep / "hpx_profiler.axf").write_bytes(b"deep")
    (tmp_path / "sub" / "hpx_profiler.axf").write_bytes(b"shallow")
    found = find_target_binary(tmp_path, "hpx_profiler")
    assert found == tmp_path / "sub" / "hpx_profiler.axf"

    # extension precedence: .axf pattern is tried before .elf, so even a
    # shallower .elf loses to a deeper .axf.
    (tmp_path / "hpx_profiler.elf").write_bytes(b"elf")
    found = find_target_binary(tmp_path, "hpx_profiler")
    assert found is not None
    assert found.suffix == ".axf"


def test_find_target_binary_tiebreak_is_filesystem_order_independent(
    tmp_path: Path, monkeypatch
) -> None:
    """#180: glob already walks top-down, so shallow-vs-deep never
    depended on FS order — SAME-DEPTH siblings did. Feed the raw glob in
    reversed order and require the lexicographic winner."""
    import glob as glob_module

    from helia_profiler.firmware import find_target_binary

    for name in ("zzz", "aaa"):
        d = tmp_path / name
        d.mkdir()
        (d / "hpx_profiler.axf").write_bytes(name.encode())

    real_glob = glob_module.glob

    def reversed_glob(pattern, **kwargs):
        return list(reversed(real_glob(pattern, **kwargs)))

    monkeypatch.setattr("helia_profiler.firmware.glob.glob", reversed_glob)
    found = find_target_binary(tmp_path, "hpx_profiler")
    assert found == tmp_path / "aaa" / "hpx_profiler.axf"


def _mram_regions(load_image: int, app_length: int = 1_589_248):
    from helia_profiler.platform.placement import MemoryRegion
    from helia_profiler.results import MeasuredMemoryRegions, MeasuredRegion

    mram = MeasuredRegion(
        region=MemoryRegion.MRAM,
        window_start=0x00410000,
        window_length=app_length,
        app_start=0x00410000,
        app_length=app_length,
        used=load_image,
        reserved=0,
        load_image=load_image,
    )
    return MeasuredMemoryRegions(link_family="gnu", linker_profile="default", regions=(mram,))


def test_image_past_flashable_mram_is_refused() -> None:
    from helia_profiler.errors import BuildError, ImageTooLargeError
    from helia_profiler.stages.build_firmware import check_image_fits

    with pytest.raises(ImageTooLargeError, match="by 11,656 bytes") as exc:
        check_image_fits(_mram_regions(1_600_904), has_psram=True)
    assert isinstance(exc.value, BuildError)
    assert exc.value.hint is not None and "PSRAM" in exc.value.hint


def test_oversize_hint_omits_psram_without_it() -> None:
    from helia_profiler.errors import ImageTooLargeError
    from helia_profiler.stages.build_firmware import check_image_fits

    with pytest.raises(ImageTooLargeError) as exc:
        check_image_fits(_mram_regions(1_600_904), has_psram=False)
    assert exc.value.hint == "Use a smaller model."


@pytest.mark.parametrize(("tail_size", "too_large"), [(0, False), (16, True)])
def test_segment_past_flashable_mram_is_caught(monkeypatch, tail_size, too_large) -> None:
    """A segment starting at the flashable limit still counts."""
    import helia_profiler.hostenv.memory_measurement as mm
    from helia_profiler.errors import ImageTooLargeError
    from helia_profiler.hostenv.memory_measurement import measure_memory_regions
    from helia_profiler.hostenv.toolchain_probe import ElfSection, LoadSegment, SectionInventory
    from helia_profiler.platform import get_soc
    from helia_profiler.stages.build_firmware import check_image_fits

    inventory = SectionInventory(
        sections=(ElfSection(".text", 0x00410000, 1_589_248, False, True, index=1),),
        segments=(
            LoadSegment(0x00410000, 0x00410000, 1_589_248, 1_589_248),
            LoadSegment(0x20080000, 0x00594000, tail_size, tail_size),
        ),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    regions = measure_memory_regions(Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo330P"))
    assert regions is not None
    assert regions.unattributed_load_bytes == 0

    if too_large:
        with pytest.raises(ImageTooLargeError, match="by 16 bytes"):
            check_image_fits(regions, has_psram=True)
    else:
        check_image_fits(regions, has_psram=True)


@pytest.mark.parametrize("load_image", [0, 1_589_248])
def test_image_that_fits_passes(load_image: int) -> None:
    from helia_profiler.stages.build_firmware import check_image_fits

    check_image_fits(_mram_regions(load_image), has_psram=True)


def test_unmeasured_image_is_not_guessed() -> None:
    from helia_profiler.results import MeasuredMemoryRegions
    from helia_profiler.stages.build_firmware import check_image_fits

    check_image_fits(None, has_psram=True)
    empty = MeasuredMemoryRegions(link_family="gnu", linker_profile="default", regions=())
    check_image_fits(empty, has_psram=True)


def test_oversized_image_stops_before_publish(tmp_path: Path, monkeypatch) -> None:
    from helia_profiler.errors import ImageTooLargeError
    from helia_profiler.platform import get_soc

    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.soc = get_soc("apollo330P")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    ctx.progress_sink = lambda *_a, **_k: None

    monkeypatch.setattr(
        "helia_profiler.firmware.build_app",
        lambda _ctx: (tmp_path / "build", tmp_path / "build" / "fw"),
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.measure_memory_regions",
        lambda *_a, **_k: _mram_regions(1_589_249),
    )

    with pytest.raises(ImageTooLargeError):
        BuildFirmwareStage().run(ctx)
    assert ctx.profile_run is None
