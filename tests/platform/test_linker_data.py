"""Generated layouts preserve their source authority and existing window contract."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

import pytest

from helia_profiler.platform import build_platform_registry, get_soc
from helia_profiler.platform.linker.data import characterized_input
from helia_profiler.platform.linker.files import source_sha256
from helia_profiler.platform.memory_map import LinkFamily, linked_memory_map
from helia_profiler.platform.placement import MemoryRegion


def test_generated_source_matches_the_build_baseline_and_enabled_socs():
    root = files("helia_profiler").joinpath("data")
    generated = json.loads(root.joinpath("linker-windows-v1.json").read_text())
    baseline = json.loads(root.joinpath("compatibility-baseline-v1.json").read_text())
    assert generated["sdk_commit"] == baseline["projects"]["nsx-ambiq-sdk"]["ref"]
    assert set(generated["socs"]) == set(build_platform_registry().socs)


@pytest.mark.parametrize("family", list(LinkFamily))
def test_selected_inputs_retain_the_script_and_actual_fixed_stack_regions(family):
    layout = characterized_input("apollo510", family)
    assert layout is not None and layout.script in layout.files
    assert layout.script.path.endswith(".ld" if family is LinkFamily.GNU else ".sct")
    assert layout.script.sha256 and layout.definition_sha256
    by_region = {r.region: r for r in layout.regions}
    dtcm = by_region[MemoryRegion.DTCM]
    assert dtcm.allowed is None and dtcm.capacity is None
    assert dtcm.fixed_stack_bytes == (16384 if family is LinkFamily.ARMLINK else 0)
    assert dtcm.app_window == next(
        w.app_window[family]
        for w in linked_memory_map(get_soc("apollo510"))
        if w.region is MemoryRegion.DTCM
    )


def test_small_m55_nvm_is_the_current_sdk_extent_not_physical_capacity():
    for soc in ("apollo330P", "apollo510L"):
        for family in LinkFamily:
            layout = characterized_input(soc, family)
            assert layout is not None
            mram = next(r for r in layout.regions if r.region is MemoryRegion.MRAM)
            assert (mram.app_window.start, mram.app_window.end) == (0x00410000, 0x00594000)
            assert mram.app_window.length == 1_589_248
            assert mram.window.end > mram.app_window.end


def test_uncharacterized_profiles_and_names_remain_unavailable():
    assert characterized_input("apollo510", LinkFamily.GNU, "itcm") is None
    assert characterized_input("custom", LinkFamily.GNU) is None


def test_source_identity_is_portable_across_checkout_line_endings(tmp_path):
    path = tmp_path / "source.ld"
    source = b"MEMORY\n{\n A : ORIGIN = 0, LENGTH = 16\n}\n"
    expected = hashlib.sha256(source).hexdigest()
    path.write_bytes(source)
    assert source_sha256(path) == expected
    path.write_bytes(source.replace(b"\n", b"\r\n"))
    assert source_sha256(path) == expected
    path.write_bytes(source.replace(b"16", b"17"))
    assert source_sha256(path) != expected
