"""hpx CLI — Profile LiteRT models on Ambiq silicon."""

from __future__ import annotations

import sys

from .parser import build_parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "profile":
        from .profile_cmd import _cmd_profile

        _cmd_profile(args)
    elif args.command == "analyze":
        from .analyze_cmd import _cmd_analyze

        _cmd_analyze(args)
    elif args.command == "doctor":
        from .inspect_cmds import _cmd_doctor

        _cmd_doctor()
    elif args.command == "engines":
        from .inspect_cmds import _cmd_engines

        _cmd_engines()
    elif args.command == "boards":
        from .inspect_cmds import _cmd_boards

        _cmd_boards()
    elif args.command == "probes":
        from .inspect_cmds import _cmd_probes

        _cmd_probes(args)
    elif args.command == "ports":
        from .inspect_cmds import _cmd_ports

        _cmd_ports(args)
    elif args.command == "target":
        from .inspect_cmds import _cmd_target

        _cmd_target(args)
    elif args.command == "power-on":
        from .power_cmd import _cmd_power_on

        _cmd_power_on(args)
    elif args.command == "validate":
        from .validate_cmd import _cmd_validate

        _cmd_validate(args)
    elif args.command == "compare":
        from .compare_cmd import _cmd_compare

        _cmd_compare(args)
    elif args.command == "cache":
        from .cache_cmd import _cmd_cache

        _cmd_cache(args)


__all__ = ["main"]
