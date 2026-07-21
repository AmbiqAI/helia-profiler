"""Rich console output for heliaPROFILER.

Provides the :class:`HpxConsole` singleton that renders pipeline progress,
stage status, and final results.  Output detail adapts to verbosity level:

- **0** (default): results summary only — clean, copyable tables.
- **1** (``-v``): stage progress with timing + results.
- **2** (``-vv``): full debug logging via standard ``logging``.

All user-facing terminal output flows through this module.  The underlying
``logging`` system is still used for DEBUG-level diagnostics; this module
handles the *presentation* layer that users see.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from . import analysis, compare, doctor, progress, results

if TYPE_CHECKING:
    from ..evaluation import CompareResult
    from ..pipeline import PipelineContext, ProgressUpdate

# Module-level consoles: durable command results go to stdout; transient
# progress, logs, and errors go to stderr so future machine output stays clean.
_console = Console(highlight=False)
_status_console = Console(highlight=False, stderr=True)


class HpxConsole:
    """Manages all user-facing terminal output.

    Instantiate once per run via ``HpxConsole(verbosity)``.

    At default verbosity (0), a compact live spinner shows the current
    pipeline stage alongside a progress bar.  At ``-v``, each stage gets
    its own spinner that resolves to a ✓ + elapsed time.
    """

    def __init__(self, verbosity: int = 0) -> None:
        self.verbosity = verbosity
        self._console = _console
        self._status_console = _status_console
        self._stage_start: float | None = None
        self._run_start: float = time.monotonic()
        self._spinner: Any | None = None  # rich.status.Status when active
        self._completed_stages: list[str] = []
        self._stage_name: str | None = None
        self._stage_index = 0
        self._stage_total = 0
        self._phase_name: str | None = None

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------

    def print_banner(self) -> None:
        """Print the startup banner (verbosity >= 1)."""
        progress.print_banner(self)

    # ------------------------------------------------------------------
    # Pipeline progress (verbosity >= 1)
    # ------------------------------------------------------------------

    def stage_start(self, name: str, index: int = 0, total: int = 0) -> None:
        """Called when a pipeline stage begins."""
        progress.stage_start(self, name, index, total)

    def progress_update(self, update: ProgressUpdate) -> None:
        """Show a user-meaningful update within the active stage."""
        progress.progress_update(self, update)

    def stage_done(self, name: str) -> None:
        """Called when a pipeline stage completes."""
        progress.stage_done(self, name)

    def stage_skip(self, name: str) -> None:
        """Called when a pipeline stage is skipped."""
        progress.stage_skip(self, name)

    def pipeline_done(self) -> None:
        """Called after all stages complete — clean up any live spinner."""
        progress.pipeline_done(self)

    def _stop_spinner(self) -> None:
        progress.stop_spinner(self)

    # ------------------------------------------------------------------
    # Final results display (always shown)
    # ------------------------------------------------------------------

    def print_results(self, ctx: PipelineContext) -> None:
        """Render the rich results panel after a successful run."""
        results.print_results(self, ctx)

    # ------------------------------------------------------------------
    # Compare display
    # ------------------------------------------------------------------

    def print_compare(
        self,
        result: CompareResult,
        *,
        top_layers: int = 10,
        output_paths: list[Path] | None = None,
    ) -> None:
        """Render a rich comparison between two completed profile runs."""
        compare.print_compare(self, result, top_layers=top_layers, output_paths=output_paths)

    # ------------------------------------------------------------------
    # Memory plan rendering
    # ------------------------------------------------------------------

    def _render_memory_plan(self, plan: Any) -> None:
        """Render the engine-agnostic MemoryPlan as a region usage table.

        Shows each region present on the SoC with its used / capacity
        totals, a progress bar, and a breakdown of consumers when
        non-trivial.  Overflow rows are highlighted in red.
        """
        results.render_memory_plan(self, plan)

    # ------------------------------------------------------------------
    # Standalone model analysis display
    # ------------------------------------------------------------------

    def print_analysis(
        self,
        primary: Any,
        model_name: str,
        reference: Any | None = None,
    ) -> None:
        """Render standalone ``hpx analyze`` results.

        *primary* is the engine-specific analysis (what the engine actually
        executes).  *reference* is the original tflite analysis shown when
        ``--compare`` is used.
        """
        analysis.print_analysis(self, primary, model_name, reference)

    # ------------------------------------------------------------------
    # Error display
    # ------------------------------------------------------------------

    def print_error(self, exc: Exception) -> None:
        """Render a user-facing error."""
        doctor.print_error(self, exc)

    def print_interrupted(self) -> None:
        """Print a clean one-liner on Ctrl-C."""
        doctor.print_interrupted(self)

    # ------------------------------------------------------------------
    # Doctor
    # ------------------------------------------------------------------

    def print_doctor(
        self,
        result,
    ) -> None:
        """Render a typed ``hpx doctor`` result."""
        doctor.print_doctor(self, result)

    # ------------------------------------------------------------------
    # Boards & Engines
    # ------------------------------------------------------------------

    def print_boards(self, boards: list[tuple[str, str, str, str, str, str]]) -> None:
        """Render the boards list.

        Each tuple: ``(board, soc, core, backends, domains, channel)``
        """
        doctor.print_boards(self, boards)

    def print_engines(self, engines: list[str]) -> None:
        """Render the engine list."""
        doctor.print_engines(self, engines)
