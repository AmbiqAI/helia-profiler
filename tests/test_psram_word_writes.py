"""PSRAM writes cover whole 32-bit words; every other length stays logical (#672)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helia_profiler.firmware.headers import _blob_to_header, _model_to_header
from tests.test_template_render import _render_aot

# Logical byte count -> the word-padded length the PSRAM write must use.
_PADDED = {1: 4, 3: 4, 4: 4, 5: 8, 4097: 4100}


def array_bytes(header: str, symbol: str) -> bytes:
    body = re.search(rf"\b{symbol}\[\] = \{{(.*?)\}};", header, re.DOTALL)
    assert body, symbol
    return bytes(int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", body.group(1)))


def c_value(expression: str, header: str) -> int:
    """Evaluate a length expression the firmware passes, against the emitted header."""
    expression = expression.strip()
    if match := re.fullmatch(r"\(unsigned\)(.+)", expression):
        return c_value(match.group(1), header)
    if match := re.fullmatch(r"(\d+)U?", expression):
        return int(match.group(1))
    if match := re.fullmatch(r"sizeof\((\w+)\)", expression):
        return len(array_bytes(header, match.group(1)))
    if match := re.fullmatch(r"(\w+_len)", expression):
        initializer = re.search(rf"\b{match.group(1)} = (.+?);", header)
        assert initializer, expression
        return c_value(initializer.group(1), header)
    raise AssertionError(f"unresolved length expression {expression!r}")


@pytest.mark.parametrize("size", [1, 3, 4, 4097])
def test_blob_header_pads_storage_and_keeps_logical_length(tmp_path: Path, size: int):
    data = bytes(i % 255 + 1 for i in range(size))
    (tmp_path / "w.bin").write_bytes(data)
    header = _blob_to_header(tmp_path / "w.bin", "hpx_const_blob_0")
    assert array_bytes(header, "hpx_const_blob_0") == data + bytes(_PADDED[size] - size)
    assert c_value("hpx_const_blob_0_len", header) == size


@pytest.mark.parametrize("size", [5, 24])
def test_model_header_pads_storage_and_keeps_logical_length(tmp_path: Path, size: int):
    data = bytes(i % 255 + 1 for i in range(size))
    (tmp_path / "m.tflite").write_bytes(data)
    header = _model_to_header(tmp_path / "m.tflite")
    padded = _PADDED.get(size, size)
    assert array_bytes(header, "model_data") == data + bytes(padded - size)
    assert c_value("model_data_len", header) == size


@pytest.mark.parametrize("size", [1, 3, 4, 4097])
def test_main_aot_psram_write_is_word_padded(tmp_path: Path, size: int):
    data = bytes(i % 255 + 1 for i in range(size))
    (tmp_path / "w.bin").write_bytes(data)
    headers = {
        region: _blob_to_header(tmp_path / "w.bin", f"hpx_const_blob_{region}") for region in (0, 1)
    }
    out = _render_aot(
        arena_region="psram",
        arena_regions=[
            {
                "region_id": 0,
                "placement": "psram",
                "alignment": 1,
                "size": size,
                "blob_filename": "w.bin",
                "role": "constant",
            },
            {
                "region_id": 1,
                "placement": "tcm",
                "alignment": 16,
                "size": size,
                "blob_filename": "w.bin",
                "role": "constant",
            },
        ],
    )
    padded = _PADDED[size]

    write = re.search(r"hpx_const_blob_0,\s*(.+?)\) != 0\) \{", out)
    assert write
    written = c_value(write.group(1), headers[0])
    assert written == padded and written % 4 == 0
    assert array_bytes(headers[0], "hpx_const_blob_0") == data + bytes(padded - size)

    copy = re.search(r"memcpy\(hpx_arena_buf_1,\s*hpx_const_blob_1,\s*(.+?)\);", out)
    assert copy
    assert c_value(copy.group(1), headers[1]) == size

    loaded = re.findall(r'HPX_CONST_BLOB_LOADED region=%d size=%u\\n",\s*(\d+),\s*(.+?)\);', out)
    assert [region for region, _ in loaded] == ["0", "1"]
    for region, expression in loaded:
        assert c_value(expression, headers[int(region)]) == size

    advance = re.findall(r"psram_off \+= (.+?);", out)
    assert len(advance) == 1
    assert c_value(advance[0].removeprefix("(uint32_t)"), headers[0]) == padded


def _psram_regions(alignment: int, *, blob: bool) -> list[dict[str, object]]:
    return [
        {
            "region_id": 0,
            "placement": "tcm",
            "alignment": 16,
            "size": 64,
            "blob_filename": None,
            "role": "scratch",
        },
        {
            "region_id": 1,
            "placement": "psram",
            "alignment": alignment,
            "size": 100,
            "blob_filename": "c.bin" if blob else None,
            "role": "constant",
        },
        {
            "region_id": 2,
            "placement": "psram",
            "alignment": alignment,
            "size": 8,
            "blob_filename": None,
            "role": "scratch",
        },
    ]


@pytest.mark.parametrize(("alignment", "line"), [(1, 32), (16, 32), (32, 32), (64, 64)])
def test_main_aot_stacks_psram_arenas_on_cache_lines(alignment: int, line: int):
    out = _render_aot(arena_region="psram", arena_regions=_psram_regions(alignment, blob=True))
    rounding = re.findall(r"psram_off = \(psram_off \+ (\d+) - 1u\) & ~\((\d+)u - 1u\);", out)
    assert rounding == [(str(line), str(line))] * 2


def test_main_aot_syncs_the_dcache_after_writing_constants_to_psram():
    out = _render_aot(arena_region="psram", arena_regions=_psram_regions(16, blob=True))
    sync = out.index("    HPX_CACHE_SYNC_SHARED_DATA();")
    assert out.rindex("HPX_CONST_BLOB_LOADED", 0, sync) > out.rindex("nsx_psram_write(", 0, sync)
    assert sync < out.index("fake_model_init(&fake_model_ctx);")
    assert out.count("HPX_CACHE_SYNC_SHARED_DATA();") == 1
    no_blob = _render_aot(arena_region="psram", arena_regions=_psram_regions(16, blob=False))
    assert "HPX_CACHE_SYNC_SHARED_DATA();" not in no_blob
