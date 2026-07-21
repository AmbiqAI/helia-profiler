from __future__ import annotations

import logging
from unittest.mock import Mock, patch

from helia_profiler.profiler import run_profile


def test_run_profile_is_presentation_neutral_by_default() -> None:
    config = Mock()
    context = Mock(pmu_result=Mock())
    pipeline = Mock()
    pipeline.run.return_value = context

    with (
        patch("helia_profiler.profiler.build_default_pipeline", return_value=pipeline) as build,
        patch("helia_profiler.profiler._cli_logging") as cli_logging,
        patch("helia_profiler.profiler.HpxConsole") as console_type,
    ):
        result = run_profile(config)

    assert result is context
    build.assert_called_once_with(progress_sink=None)
    cli_logging.assert_not_called()
    console_type.assert_not_called()


def test_cli_logging_state_is_restored_after_run() -> None:
    logger = logging.getLogger("hpx")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    config = Mock(verbose=1)
    context = Mock(pmu_result=None)
    pipeline = Mock()
    pipeline.run.return_value = context
    console = Mock()

    with patch("helia_profiler.profiler.build_default_pipeline", return_value=pipeline):
        run_profile(config, console=console)

    assert logger.handlers == previous_handlers
    assert logger.level == previous_level
