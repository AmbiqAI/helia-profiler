from __future__ import annotations

from pathlib import Path

from helia_profiler.capture.rtt_symbol import resolve_rtt_control_block_address

_GCC_MAP = """\
Memory Configuration

Linker script and memory map

 .sram_bss       0x20088000     0x80b8
                0x20088010                _SEGGER_RTT
                0x2008c000                _acUpBuffer
"""


def test_resolves_address_from_gcc_map(tmp_path: Path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "hpx_profiler.map").write_text(_GCC_MAP)

    addr = resolve_rtt_control_block_address(build_dir, "arm-none-eabi-gcc")

    assert addr == 0x20088010


def test_resolves_address_from_nested_map(tmp_path: Path):
    build_dir = tmp_path / "build"
    nested = build_dir / "apollo510_evb"
    nested.mkdir(parents=True)
    (nested / "hpx_profiler.map").write_text(_GCC_MAP)

    addr = resolve_rtt_control_block_address(build_dir, "arm-none-eabi-gcc")

    assert addr == 0x20088010


def test_resolves_power_target_without_selecting_profile_map(tmp_path: Path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "hpx_profiler.map").write_text(
        _GCC_MAP.replace("0x20088010", "0x20001000")
    )
    (build_dir / "hpx_profiler_power.map").write_text(_GCC_MAP)

    addr = resolve_rtt_control_block_address(
        build_dir,
        "arm-none-eabi-gcc",
        target_name="hpx_profiler_power",
    )

    assert addr == 0x20088010


def test_returns_none_when_symbol_absent(tmp_path: Path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "hpx_profiler.map").write_text("no symbols here\n")

    assert resolve_rtt_control_block_address(build_dir, "arm-none-eabi-gcc") is None


def test_returns_none_when_build_dir_missing():
    assert resolve_rtt_control_block_address(None, "arm-none-eabi-gcc") is None
    assert (
        resolve_rtt_control_block_address(Path("/nonexistent/build"), "arm-none-eabi-gcc")
        is None
    )
