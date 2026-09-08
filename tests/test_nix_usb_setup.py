"""USB rule installer behavior without changing host permissions."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "nix/scripts/install-udev-rules.sh"
_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(os.name == "nt" or not _BASH, reason="Requires a POSIX shell")


def _run(
    tmp_path: Path, *args: str, system: str = "Linux"
) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    for name, body in {
        "uname": f"echo {system}\n",
        "sudo": 'printf "%s\\n" "$*" >> "$HPX_TEST_SUDO_LOG"\n',
    }.items():
        command = bin_dir / name
        command.write_text("#!/bin/sh\n" + body)
        command.chmod(0o755)
    result = subprocess.run(
        [_BASH or "bash", str(_SCRIPT), *args],
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "HPX_TEST_SUDO_LOG": str(log),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, log.read_text() if log.exists() else ""


def test_preview_uses_early_access_rules_without_sudo(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "/etc/udev/rules.d/70-segger-jlink.rules" in result.stdout
    assert "/etc/udev/rules.d/70-joulescope.rules" in result.stdout
    assert 'TAG+="uaccess"' in result.stdout
    assert 'MODE="0666"' not in result.stdout
    assert log == ""


def test_installer_reloads_rules_and_refreshes_only_usb(tmp_path: Path) -> None:
    result, log = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    calls = log.splitlines()
    assert len(calls) == 4
    assert calls[0].endswith(" /etc/udev/rules.d/70-segger-jlink.rules")
    assert calls[1].endswith(" /etc/udev/rules.d/70-joulescope.rules")
    assert calls[2:] == ["udevadm control --reload-rules", "udevadm trigger --subsystem-match=usb"]


def test_preview_rejects_extra_arguments(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--dry-run", "unexpected")
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert log == ""


def test_installer_rejects_non_linux_hosts(tmp_path: Path) -> None:
    result, log = _run(tmp_path, system="Darwin")
    assert result.returncode == 1
    assert "only needed on Linux" in result.stderr
    assert log == ""
