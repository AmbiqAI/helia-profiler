"""Refuse positional linker inputs that could be unrecorded implicit scripts."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

_OPERAND_OPTIONS = frozenset(
    {
        "-Map",
        "--Map",
        "--entry",
        "-e",
        "-u",
        "--undefined",
        "--wrap",
        "--diag_suppress",
        "--list",
        "--cpu",
        "--keep",
        "-o",
        "--output",
        "-l",
        "--library",
    }
)
_BINARY_HEADERS = (b"\x7fELF", b"!<arch>\n", b"!<thin>\n", b"BC\xc0\xde", b"\xde\xc0\x17\x0b")
_SEARCH_OPTIONS = ("library-path", "userlibpath")
_SCRIPT_OPTIONS = ("script", "default-script", "scatter", "via")


def _tokens(fragment: str) -> list[str]:
    # File API fragments use the native shell's quoting, not a portable argv encoding.
    if os.name == "nt":
        lexer = shlex.shlex(fragment, posix=False)
        lexer.quotes = '"'
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = [
            token[1:-1] if token.startswith('"') and token.endswith('"') else token
            for token in lexer
        ]
        if any('"' in token for token in tokens):
            raise ValueError("Unsupported native-shell encoding of linker input")
    else:
        tokens = shlex.split(fragment)
    return [
        part
        for token in tokens
        for part in (token[4:].split(",") if token.startswith("-Wl,") else [token])
        if part != "-Xlinker"
    ]


def _binary_input(argument: str, build_dir: Path, products: frozenset[Path]) -> None:
    path = (build_dir / argument).resolve()
    if not path.exists() and path in products:
        return
    try:
        with path.open("rb") as stream:
            header = stream.read(8)
    except OSError as exc:
        raise ValueError(f"Uncharacterized linker file input: {argument}") from exc
    if not header.startswith(_BINARY_HEADERS):
        raise ValueError(f"Linker file input may be an implicit script: {argument}")


def validate_link_inputs(
    fragments: tuple[str, ...], build_dir: Path, products: frozenset[Path]
) -> None:
    """Accept binary files and declared build products, never infer their format from a suffix."""
    operand = None
    for fragment in fragments:
        for token in _tokens(fragment):
            name = token.lstrip("-").partition("=")[0] if token.startswith("-") else ""
            # https://sourceware.org/binutils/docs/ld/Options.html: aliases and abbreviations.
            long_option = bool(name) and (token.startswith("--") or len(name) > 1)
            if token.startswith("-L") or (
                long_option
                and name != "library"
                and any(o.startswith(name) for o in _SEARCH_OPTIONS)
            ):
                raise ValueError("Explicit linker library search paths are not characterized")
            if token.startswith(("@", "-T", "-dT")) or (
                long_option and any(o.startswith(name) for o in _SCRIPT_OPTIONS)
            ):
                raise ValueError("Additional or opaque linker script input")
            if operand is not None:
                if operand in ("-l", "--library") and token.startswith(":"):
                    raise ValueError("Opaque linker library filename search")
                operand = None
            elif token in _OPERAND_OPTIONS:
                operand = token
            elif token.startswith(("-l:", "--library=:")):
                raise ValueError("Opaque linker library filename search")
            elif token.startswith("-"):
                continue
            else:
                _binary_input(token, build_dir, products)
    if operand is not None:
        raise ValueError(f"Missing linker option operand: {operand}")
