"""Canonical toolchain capabilities shared by build, probes, and engines."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from .config import Toolchain


@dataclass(frozen=True)
class ToolchainSpec:
    toolchain: Toolchain
    compiler: str
    nsx_name: str | None
    nm: str
    size: str | None
    section_probe: Literal["size", "fromelf"]
    heliart_tag: str
    default_rtt_buffer_size_up: int = 32768


_SPECS = {
    Toolchain.ARM_NONE_EABI_GCC: ToolchainSpec(
        toolchain=Toolchain.ARM_NONE_EABI_GCC,
        compiler="arm-none-eabi-gcc",
        nsx_name=None,
        nm="arm-none-eabi-nm",
        size="arm-none-eabi-size",
        section_probe="size",
        heliart_tag="gcc",
    ),
    Toolchain.GCC: ToolchainSpec(
        toolchain=Toolchain.GCC,
        compiler="gcc",
        nsx_name=None,
        nm="gcc-nm",
        size="gcc-size",
        section_probe="size",
        heliart_tag="gcc",
    ),
    Toolchain.ARMCLANG: ToolchainSpec(
        toolchain=Toolchain.ARMCLANG,
        compiler="armclang",
        nsx_name="armclang",
        nm="llvm-nm",
        size=None,
        section_probe="fromelf",
        heliart_tag="armclang",
    ),
    Toolchain.ATFE: ToolchainSpec(
        toolchain=Toolchain.ATFE,
        compiler="clang",
        nsx_name="atfe",
        nm="llvm-nm",
        size=None,
        section_probe="fromelf",
        heliart_tag="atfe",
        default_rtt_buffer_size_up=12288,
    ),
}


def get_toolchain_spec(toolchain: str | Toolchain) -> ToolchainSpec:
    try:
        canonical = toolchain if isinstance(toolchain, Toolchain) else Toolchain(toolchain)
    except ValueError as exc:
        raise ValueError(f"Unsupported toolchain: {toolchain!r}.") from exc
    return _SPECS[canonical]


def resolve_toolchain_executable(toolchain: str | Toolchain, executable: str) -> str:
    """Resolve root-relative tools for installations such as ATfE."""
    spec = get_toolchain_spec(toolchain)
    if spec.toolchain is Toolchain.ATFE:
        root = os.environ.get("ATFE_ROOT")
        if root:
            return str(Path(root).expanduser() / "bin" / executable)
    return executable


__all__ = [
    "ToolchainSpec",
    "get_toolchain_spec",
    "resolve_toolchain_executable",
]
