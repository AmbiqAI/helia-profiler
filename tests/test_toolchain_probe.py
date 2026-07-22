"""Tests for read-only toolchain probes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from helia_profiler.config import Toolchain
from helia_profiler.results import BinarySections
from helia_profiler.toolchain_probe import binary_sections


def test_atfe_binary_sections_uses_llvm_size_from_atfe_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path / "atfe"))
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="text data bss dec hex filename\n10 20 30 60 3c firmware\n",
            stderr="",
        )

    monkeypatch.setattr("helia_profiler.toolchain_probe.subprocess.run", fake_run)

    sections = binary_sections(
        tmp_path / "firmware",
        Toolchain.ATFE,
        timeout_s=5,
    )

    assert sections == BinarySections(text=10, data=20, bss=30, total=60)
    assert calls == [[str(tmp_path / "atfe" / "bin" / "llvm-size"), str(tmp_path / "firmware")]]
