"""Implementation of the ``hpx runtimes`` command (list/show)."""

from __future__ import annotations

import importlib.resources

import typer

from ..runtime_records import runtime, runtimes


def _cmd_runtimes_list() -> None:
    for record in runtimes():
        qualified = "; ".join(
            f"{target.board}/{target.clock}: {', '.join(target.precisions)}"
            for target in record.qualified
        )
        print(
            f"{record.name:<11} {record.version:<8} {'default' if record.default else '':<8}"
            f"{record.source.repo}@{record.source.commit[:8]}  {qualified or '-'}"
        )


def _cmd_runtimes_show(name: str, version: str | None) -> None:
    record = runtime(name, version)
    if record is None:
        label = f"{name} {version}" if version is not None else name
        print(f"No runtime record for {label}.")
        raise typer.Exit(1)
    resource = (
        importlib.resources.files("helia_profiler.data")
        .joinpath("runtimes")
        .joinpath(record.name)
        .joinpath(f"{record.version}.json")
    )
    print(resource.read_text(encoding="utf-8"), end="")


def _cmd_runtimes_prepare(name: str, version: str | None) -> None:
    from ..console import HpxConsole
    from ..errors import HpxError
    from ..prepared_runtimes import prepare_runtime

    try:
        prepared = prepare_runtime(name, version)
    except HpxError as exc:
        HpxConsole(verbosity=1).print_error(exc)
        raise typer.Exit(1) from exc
    record = prepared.record
    print(f"Prepared {record.name} {record.version} in {prepared.directory}")
    sha256 = prepared.runtime.archive.sha256
    if prepared.matches_record:
        print(f"  archive {sha256} matches the record")
    else:
        print(f"  archive {sha256} differs from the record's {record.archive_sha256}")
