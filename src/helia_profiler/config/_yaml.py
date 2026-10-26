"""Read YAML configuration with file and document errors classified for callers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import ConfigError


def read_config_yaml(path: Path) -> dict[str, Any]:
    """Read an HPX YAML config file into an unvalidated mapping.

    An empty file reads as ``{}``. Raises :class:`ConfigError` if the file is
    missing or unreadable, is not valid YAML, or its top level is not a mapping.
    """
    import yaml

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Config file not found: {path}",
            hint="Check the config file path.",
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"Cannot read config file {path}: {exc}",
            hint="Check that the path names a readable file and its storage is accessible.",
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"Config file {path} is not valid UTF-8: {exc}",
            hint="Save the config file with UTF-8 encoding.",
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Malformed YAML in config file {path}: {exc}",
            hint="Check the file for YAML syntax errors (indentation, colons, quoting).",
        ) from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path} must contain a YAML mapping (key: value pairs), "
            f"got {type(data).__name__}.",
            hint="Top-level YAML must be a mapping with keys like model, engine, target.",
        )
    return data
