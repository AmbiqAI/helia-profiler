"""Every config the repo ships must load, so an unused one cannot rot."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIGS = sorted(
    [
        *REPO_ROOT.glob("configs/**/*.yaml"),
        *REPO_ROOT.glob("configs/**/*.yml"),
        *REPO_ROOT.glob("examples/quickstart/*.yaml"),
        *REPO_ROOT.glob("examples/quickstart/*.yml"),
    ]
)


def test_shipped_configs_are_found():
    assert len(SHIPPED_CONFIGS) > 1


@pytest.mark.parametrize(
    "config_path", SHIPPED_CONFIGS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_shipped_config_loads(config_path: Path):
    load_config(config_path, {})
