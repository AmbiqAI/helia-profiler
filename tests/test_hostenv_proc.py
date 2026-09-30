"""Shared host tool probe mechanics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from helia_profiler.hostenv import _proc
from helia_profiler.hostenv.toolchain_probe import symbol_address


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_tool_output_decodes_utf8_with_replacement():
    stdout = _proc.tool_output(
        _python("import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9\\xff\\n')"), timeout_s=10
    )

    assert stdout == "café�\n"


def test_tool_output_is_none_on_nonzero_exit():
    assert _proc.tool_output(_python("print('partial'); raise SystemExit(7)"), timeout_s=10) is None


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("tool"),
        PermissionError("tool"),
        OSError("cannot execute"),
        subprocess.TimeoutExpired("tool", 13),
    ],
)
def test_tool_output_is_none_when_the_tool_cannot_run(monkeypatch, error):
    monkeypatch.setattr(_proc.subprocess, "run", Mock(side_effect=error))

    assert _proc.tool_output(["tool"], timeout_s=13) is None


def test_tool_output_does_not_swallow_unexpected_failures(monkeypatch):
    monkeypatch.setattr(_proc.subprocess, "run", Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        _proc.tool_output(["tool"], timeout_s=13)


def test_symbol_address_decodes_like_the_other_probes(monkeypatch):
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["nm"], 0, stdout="00000000 T bad_\ufffd\n20000000 B g_arena\n", stderr=""
        )
    )
    monkeypatch.setattr(_proc.subprocess, "run", run)

    address = symbol_address(Path("fw.elf"), "arm-none-eabi-gcc", "g_arena", timeout_s=13)

    assert address == (0x20000000, "B")
    assert run.call_args.kwargs == {
        "capture_output": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 13,
    }
