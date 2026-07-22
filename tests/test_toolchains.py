"""Canonical toolchain capability matrix."""

from helia_profiler.config import Toolchain
from helia_profiler.toolchains import get_toolchain_spec, resolve_toolchain_executable


def test_toolchain_specs_are_complete_and_consistent() -> None:
    expected = {
        Toolchain.ARM_NONE_EABI_GCC: ("arm-none-eabi-gcc", None, "gcc"),
        Toolchain.GCC: ("gcc", None, "gcc"),
        Toolchain.ARMCLANG: ("armclang", "armclang", "armclang"),
        Toolchain.ATFE: ("clang", "atfe", "atfe"),
    }

    for toolchain, (compiler, nsx_name, heliart_tag) in expected.items():
        spec = get_toolchain_spec(toolchain)
        assert spec.toolchain is toolchain
        assert spec.compiler == compiler
        assert spec.nsx_name == nsx_name
        assert spec.heliart_tag == heliart_tag
        assert spec.nm
        if spec.section_probe == "size":
            assert spec.size is not None
        else:
            assert spec.section_probe == "fromelf"
            assert spec.size is None


def test_atfe_uses_reduced_default_rtt_buffer() -> None:
    assert get_toolchain_spec(Toolchain.ATFE).default_rtt_buffer_size_up == 12288
    assert get_toolchain_spec(Toolchain.ARM_NONE_EABI_GCC).default_rtt_buffer_size_up == 32768


def test_atfe_uses_llvm_size() -> None:
    spec = get_toolchain_spec(Toolchain.ATFE)

    assert spec.section_probe == "size"
    assert spec.size == "llvm-size"


def test_atfe_tools_resolve_from_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path))

    assert resolve_toolchain_executable(Toolchain.ATFE, "clang") == str(tmp_path / "bin" / "clang")
    assert resolve_toolchain_executable(Toolchain.ATFE, "llvm-nm") == str(
        tmp_path / "bin" / "llvm-nm"
    )
    assert resolve_toolchain_executable(Toolchain.ATFE, "llvm-size") == str(
        tmp_path / "bin" / "llvm-size"
    )
