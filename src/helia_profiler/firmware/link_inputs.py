"""Refuse positional linker inputs that could be unrecorded implicit scripts."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

_OPERAND_OPTIONS = frozenset(
    {
        "Map",
        "entry",
        "undefined",
        "wrap",
        "diag_suppress",
        "list",
        "cpu",
        "keep",
        "output",
        "library",
    }
)
_FLAG_OPTIONS = frozenset(
    {
        "remove",
        "map",
        "symbols",
        "gc-sections",
        "start-group",
        "end-group",
        "whole-archive",
        "no-whole-archive",
    }
)
_SHORT_OPERANDS = {"-Map": "Map", "-e": "entry", "-u": "undefined", "-o": "output", "-l": "library"}
_DRIVER_FLAG = re.compile(
    r"-O(?:[0-3sgz]|fast)|-D[A-Za-z_]\w*(?:=.*)?|"
    r"-m(?:cpu|arch|fpu|float-abi)=[A-Za-z0-9_.+-]+|-mthumb|"
    r"-f(?:no-exceptions|use-ld=(?:lld|bfd|gold))|-nostartfiles|-static|"
    r"--target=[A-Za-z0-9_.-]+|--config=newlib\.cfg"
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


def _option_value(option: str, value: str) -> None:
    if not value or value.startswith(("-", "@")):
        raise ValueError(f"Unsupported linker option operand: {option}")
    if option == "library" and not re.fullmatch(r"[A-Za-z0-9_.+-]+", value):
        raise ValueError("Opaque linker library filename search")


def validate_link_inputs(
    fragments: tuple[str, ...],
    build_dir: Path,
    products: frozenset[Path],
    *,
    script_fragment_index: int,
) -> None:
    """Recognize supported options without letting an operand consume the selected script."""
    operand = None
    for position, fragment in enumerate(fragments):
        if position == script_fragment_index:
            if operand is not None:
                raise ValueError("Selected linker script is consumed as another option's operand")
            continue
        for token in _tokens(fragment):
            if operand is not None:
                _option_value(operand, token)
                operand = None
                continue
            name, separator, value = token.lstrip("-").partition("=")
            long_option = token.startswith("--") or (token.startswith("-") and len(name) > 1)
            if token.startswith("-L") or (
                long_option
                and name
                and name != "library"
                and any(o.startswith(name) for o in _SEARCH_OPTIONS)
            ):
                raise ValueError("Explicit linker library search paths are not characterized")
            if token.startswith(("@", "-T", "-dT")) or (
                long_option and name and any(o.startswith(name) for o in _SCRIPT_OPTIONS)
            ):
                raise ValueError("Additional or opaque linker script input")
            if _DRIVER_FLAG.fullmatch(token):
                continue
            if token.startswith("-") and name in _OPERAND_OPTIONS:
                if separator:
                    _option_value(name, value)
                else:
                    operand = name
            elif token in _SHORT_OPERANDS:
                operand = _SHORT_OPERANDS[token]
            elif (
                token.startswith("-")
                and not token.startswith("--")
                and token[:2] in _SHORT_OPERANDS
            ):
                _option_value(_SHORT_OPERANDS[token[:2]], token[2:])
            elif token.startswith("-") and name in _FLAG_OPTIONS and not separator:
                continue
            elif token.startswith("-"):
                raise ValueError(f"Unsupported linker option: {token}")
            else:
                _binary_input(token, build_dir, products)
    if operand is not None:
        raise ValueError(f"Missing linker option operand: {operand}")
