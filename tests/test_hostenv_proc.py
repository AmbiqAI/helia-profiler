"""Shared probe subprocess mechanics and caller-specific failure contracts."""

from __future__ import annotations

import subprocess
import sys
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest

from helia_profiler.hostenv import _proc, elf_inventory as inventory, toolchain_probe as probes
from helia_profiler.results import BinarySections


def test_run_text_preserves_result_and_subprocess_options(monkeypatch):
    command = ["tool with spaces", "--version"]
    result = subprocess.CompletedProcess(command, 7, stdout="partial", stderr="failure")
    run = Mock(return_value=result)
    monkeypatch.setattr(_proc.subprocess, "run", run)

    assert _proc.run_text(command, timeout_s=13) is result
    run.assert_called_once_with(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=13,
    )


def test_run_text_decodes_both_streams_with_replacement():
    command = [
        sys.executable,
        "-c",
        "import sys; "
        "sys.stdout.buffer.write(b'caf\\xc3\\xa9\\xff\\r\\n'); "
        "sys.stderr.buffer.write(b'error\\x81\\r\\n'); "
        "sys.exit(7)",
    ]

    result = _proc.run_text(command, timeout_s=10)

    assert result.returncode == 7
    assert result.stdout == "café\ufffd\n"
    assert result.stderr == "error\ufffd\n"


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("tool"),
        PermissionError("tool"),
        OSError("cannot execute"),
        subprocess.TimeoutExpired("tool", 13, output=b"\xff", stderr=b"\x81"),
        RuntimeError("unexpected failure"),
    ],
)
def test_run_text_leaves_exception_mapping_to_callers(monkeypatch, error):
    monkeypatch.setattr(_proc.subprocess, "run", Mock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        _proc.run_text(["tool"], timeout_s=13)

    assert caught.value is error


_BINARY = Path("firmware with spaces.elf")
_READELF = b"[ 1] .heap NOBITS 20000000 000000 000010 00 WA 0 0 4\n"
_FROMELF = (
    b"** Section #1\n"
    b"    Name : .heap\n"
    b"    Type : SHT_NOBITS\n"
    b"    Flags : SHF_ALLOC\n"
    b"    Addr : 0x20000000\n"
    b"    Size : 16 bytes\n"
)
_SECTION = inventory.ElfSection(
    name=".heap",
    address=0x20000000,
    size=16,
    nobits=True,
    allocated=True,
    index=1,
    linker_reserved=True,
)
_PROBES = [
    pytest.param(
        partial(probes._run_version, "compiler"),
        b"compiler caf\xc3\xa9\xff\nsecond line\n",
        "compiler café\ufffd",
        "",
        id="version",
    ),
    pytest.param(
        partial(probes._sections_via_size, _BINARY, size_cmd="size", readelf_cmd=None),
        b"text data bss dec hex filename\n10 20 30 60 3c fw\xff\n",
        BinarySections(text=10, data=20, bss=30, total=60),
        None,
        id="size",
    ),
    pytest.param(
        partial(probes._reserved_via_readelf, _BINARY, readelf_cmd="readelf"),
        _READELF,
        16,
        None,
        id="readelf-reserved",
    ),
    pytest.param(
        partial(probes._reserved_via_fromelf, _BINARY),
        _FROMELF,
        16,
        None,
        id="fromelf-reserved",
    ),
    pytest.param(
        partial(probes._sections_via_fromelf, _BINARY),
        b"Grand Totals: 10 0 20 30\n",
        BinarySections(text=10, data=20, bss=30, total=60),
        None,
        id="fromelf-size",
    ),
    pytest.param(
        partial(probes.symbol_address, _BINARY, "arm-none-eabi-gcc", "arena"),
        b"20000000 B _\xff_arena\n",
        (0x20000000, "B"),
        None,
        id="symbol-address",
    ),
    pytest.param(
        partial(inventory._inventory_via_readelf, _BINARY, readelf_cmd="readelf"),
        _READELF,
        ((_SECTION,), 0),
        None,
        id="readelf-inventory",
    ),
    pytest.param(
        partial(inventory._segments_via_readelf, _BINARY, readelf_cmd="readelf"),
        b"LOAD 0x0 0x20000000 0x00400000 0x10 0x20 RW 0x4\n",
        (inventory.LoadSegment(0x20000000, 0x00400000, 16, 32),),
        (),
        id="readelf-segments",
    ),
    pytest.param(
        partial(inventory.section_inventory, _BINARY, "armclang"),
        _FROMELF,
        inventory.SectionInventory(sections=(_SECTION,)),
        None,
        id="fromelf-inventory",
    ),
    pytest.param(
        partial(inventory.symbol_inventory, _BINARY, "arm-none-eabi-gcc"),
        b"20000000 00000010 B arena\xff\n",
        ((inventory.SymbolEntry("arena\ufffd", 0x20000000, 16, "B"),), 0),
        None,
        id="symbol-inventory",
    ),
]


@pytest.mark.parametrize("probe,stdout,expected,fallback", _PROBES)
@pytest.mark.parametrize("returncode", [0, 7])
def test_probes_share_decoding_and_timeout_options(
    monkeypatch, probe, stdout, expected, fallback, returncode
):
    def run(command, **kwargs):
        assert isinstance(command, list)
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 13,
        }
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=stdout.decode(kwargs["encoding"], kwargs["errors"]),
            stderr=b"diagnostic\x81".decode(kwargs["encoding"], kwargs["errors"]),
        )

    monkeypatch.setattr(_proc.subprocess, "run", run)

    assert probe(timeout_s=13) == (expected if returncode == 0 else fallback)


@pytest.mark.parametrize("probe,stdout,expected,fallback", _PROBES)
@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("tool"),
        PermissionError("tool"),
        OSError("cannot execute"),
        subprocess.TimeoutExpired("tool", 13, output=b"\xff", stderr=b"\x81"),
    ],
)
def test_probe_failure_mapping_is_unchanged(monkeypatch, probe, stdout, expected, fallback, error):
    monkeypatch.setattr(_proc.subprocess, "run", Mock(side_effect=error))

    assert probe(timeout_s=13) == fallback


@pytest.mark.parametrize("probe,stdout,expected,fallback", _PROBES)
def test_probes_do_not_swallow_unexpected_failures(monkeypatch, probe, stdout, expected, fallback):
    monkeypatch.setattr(
        _proc.subprocess, "run", Mock(side_effect=RuntimeError("unexpected failure"))
    )

    with pytest.raises(RuntimeError, match="unexpected failure"):
        probe(timeout_s=13)


@pytest.mark.parametrize(
    "probe,stdout,expected",
    [
        pytest.param(
            partial(probes.binary_sections, _BINARY, "arm-none-eabi-gcc"),
            "text data bss dec hex filename\n10 20 30 60 3c fw\n",
            BinarySections(text=10, data=20, bss=30, total=60),
            id="size-reservations",
        ),
        pytest.param(
            partial(probes.binary_sections, _BINARY, "armclang"),
            "Grand Totals: 10 0 20 30\n",
            BinarySections(text=10, data=20, bss=30, total=60),
            id="fromelf-reservations",
        ),
        pytest.param(
            partial(inventory.section_inventory, _BINARY, "arm-none-eabi-gcc"),
            _READELF.decode("utf-8"),
            inventory.SectionInventory(sections=(_SECTION,)),
            id="readelf-segments",
        ),
    ],
)
@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("tool"),
        subprocess.TimeoutExpired("tool", 13),
        OSError("cannot execute"),
        subprocess.CompletedProcess(["tool"], 7, stdout="", stderr="failure"),
    ],
)
def test_secondary_probe_failure_keeps_primary_results(
    monkeypatch, probe, stdout, expected, failure
):
    run = Mock(
        side_effect=[subprocess.CompletedProcess(["tool"], 0, stdout=stdout, stderr=""), failure]
    )
    monkeypatch.setattr(_proc.subprocess, "run", run)

    assert probe(timeout_s=13) == expected
    assert run.call_count == 2
    assert all(call.kwargs["timeout"] == 13 for call in run.call_args_list)
