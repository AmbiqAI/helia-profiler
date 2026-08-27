"""Implementation of the ``hpx validate`` command.

Thin CLI layer over :mod:`helia_profiler.validation.plan`: resolves the axis
flags into a :class:`~helia_profiler.validation.plan.ValidationPlan`, renders
output, maps errors to exit codes, and invokes pytest. All selection policy
(aliases, suite presets, custom-model registry, pytest args) lives in the
validation package.
"""

from __future__ import annotations

from pathlib import Path
import sys

from .common import _find_repo_root


def _cmd_validate(
    *,
    models: str = "",
    models_file: Path | None = None,
    model_paths: str = "",
    comparison_group: str = "custom",
    model_arena_size: int = 524288,
    engines: str = "",
    executorch_backends: str = "both",
    ns_cmsis_nn_ref: str = "",
    power: str = "off",
    power_boards: str = "",
    boards: str = "",
    toolchains: str = "",
    transports: str = "",
    memories: str = "",
    suite: str | None = None,
    jlink_serials: str = "",
    power_serials: str = "",
    power_gpios: str = "",
    repeat: int = 1,
    output_dir: Path = Path("results/validation"),
    timeout: float = 900.0,
    keyword: str = "",
    junit_xml: Path | None = None,
    list_: bool = False,
    verbose: int = 0,
) -> None:
    """Drive the hardware validation suite via pytest."""
    from ..validation import BOARDS
    from ..validation.plan import resolve_plan

    try:
        plan = resolve_plan(
            models=models,
            models_file=models_file,
            model_paths=model_paths,
            comparison_group=comparison_group,
            model_arena_size=model_arena_size,
            engines=engines,
            executorch_backends=executorch_backends,
            ns_cmsis_nn_ref=ns_cmsis_nn_ref,
            power=power,
            power_boards=power_boards,
            boards=boards,
            toolchains=toolchains,
            transports=transports,
            memories=memories,
            suite=suite,
            jlink_serials=jlink_serials,
            power_serials=power_serials,
            power_gpios=power_gpios,
            repeat=repeat,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    # --list mode — preview the matrix, don't touch hardware.
    if list_:
        try:
            cases = plan.cases()
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

        print(f"Registered models: {', '.join(sorted(plan.model_registry))}")
        print(f"Registered boards: {', '.join(sorted(BOARDS))}")
        print(f"\n{len(cases)} case(s) would run:\n")
        for c in cases:
            power_flag = "power" if c.power else "     "
            engine = c.engine.value
            engine = f"{engine}/{c.cmsis_nn_provider.value}"
            print(
                f"  {c.case_id:<82}  {engine:<14}  "
                f"{c.toolchain.value:<18}  {c.transport.value:<7}  {c.memory.value:<5}  "
                f"{power_flag}"
            )
        return

    # Locate the validation test directory inside the installed package /
    # repo checkout.  We support both the editable/repo layout
    # (``helia-profiler/tests/validation``) and any future packaged layout.
    repo_root = _find_repo_root()
    tests_dir = repo_root / "tests" / "validation"
    if not tests_dir.exists():
        print(
            f"Error: validation tests not found at {tests_dir}.\n"
            "  `hpx validate` must be run from a heliaPROFILER checkout.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import pytest
    except ImportError:
        print(
            "Error: pytest is required for `hpx validate`. Install it with `pip install pytest`.",
            file=sys.stderr,
        )
        sys.exit(2)

    pytest_args = plan.pytest_args(
        tests_dir=tests_dir,
        output_dir=output_dir,
        timeout=timeout,
        keyword=keyword,
        junit_xml=junit_xml,
        verbose=verbose,
    )

    report_dir = output_dir.resolve()
    report_json = report_dir / "validation_report.json"
    report_before = report_json.stat().st_mtime_ns if report_json.exists() else None

    print(f"Running: pytest {' '.join(pytest_args)}\n")
    rc = pytest.main(pytest_args)

    report_md = report_dir / "validation_report.md"
    report_manifest = report_dir / "validation_manifest.json"
    report_after = report_json.stat().st_mtime_ns if report_json.exists() else None
    report_is_fresh = report_after is not None and report_after != report_before
    if report_is_fresh:
        from ..console import HpxConsole
        from ..errors import ReportError
        from ..validation.report import load_validation_report

        console = HpxConsole(verbosity=verbose)
        try:
            report = load_validation_report(report_json)
        except ReportError as exc:
            console.print_error(exc)
            rc = int(rc) or 1
        else:
            output_paths = [
                path for path in (report_json, report_md, report_manifest) if path.exists()
            ]
            console.print_validation(report, output_paths=output_paths)
    else:
        if report_md.exists():
            print(f"\nMarkdown report: {report_md}")
        if report_json.exists():
            print(f"JSON report:     {report_json}")
    sys.exit(int(rc))
