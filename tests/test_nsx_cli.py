"""Tests for the nsx wrapper — error translation and timeout behaviour.

After the migration to the :mod:`neuralspotx.api` Python entry points the
shim no longer shells out to a binary. These tests now patch the API
functions directly and verify that:

* ``BuildError`` is raised on ``NSXError`` translation;
* ``flash`` forwards ``jlink_serial`` to ``flash_app`` as ``probe_serial``;
* ``timeout_s`` is forwarded to the underlying API entry points so the
  in-subprocess process-tree watchdog can enforce it; on timeout NSX
  raises ``NSXError`` which surfaces as ``BuildError``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler import nsx
from helia_profiler.errors import BuildError
from neuralspotx.api import NSXError


class TestNsxBuild:
    def test_success_calls_api(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.build_app") as build_mock:
            nsx.build(tmp_path, toolchain="armclang", timeout_s=42)
        build_mock.assert_called_once_with(
            tmp_path, toolchain="armclang", timeout_s=42, emit=nsx._quiet_emitter
        )

    def test_nsxerror_raises_build_error(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.build_app", side_effect=NSXError("boom")):
            with pytest.raises(BuildError) as exc_info:
                nsx.build(tmp_path)
        err = exc_info.value
        assert "nsx build" in str(err)
        assert "boom" in (err.stderr or "")

    def test_timeout_surfaces_as_build_error(self, tmp_path: Path) -> None:
        # The real subprocess-tree watchdog lives in
        # ``neuralspotx.subprocess_utils``; from the helia-profiler side
        # all we need to verify is that the resulting NSXError
        # ("Subprocess timed out after Ns: ...") is translated into a
        # BuildError carrying the same message.
        timeout_err = NSXError("Subprocess timed out after 1.0s: cmake -B build")
        with patch("helia_profiler.nsx.nsx_api.build_app", side_effect=timeout_err):
            with pytest.raises(BuildError) as exc_info:
                nsx.build(tmp_path, timeout_s=1)
        assert "nsx build" in str(exc_info.value)
        assert "timed out" in (exc_info.value.stderr or "")


class TestNsxFlash:
    def test_flash_forwards_probe_serial(self, tmp_path: Path) -> None:
        captured: dict[str, str | None] = {}

        def fake_flash(_app, **kwargs) -> None:  # noqa: ANN401
            captured["probe_serial"] = kwargs.get("probe_serial")

        with patch("helia_profiler.nsx.nsx_api.flash_app", side_effect=fake_flash):
            nsx.flash(tmp_path, jlink_serial="123456")
        assert captured["probe_serial"] == "123456"

    def test_flash_no_serial_passes_none(self, tmp_path: Path) -> None:
        captured: dict[str, str | None] = {}

        def fake_flash(_app, **kwargs) -> None:  # noqa: ANN401
            captured["probe_serial"] = kwargs.get("probe_serial")

        with patch("helia_profiler.nsx.nsx_api.flash_app", side_effect=fake_flash):
            nsx.flash(tmp_path)
        assert captured["probe_serial"] is None

    def test_flash_does_not_touch_sncode_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEGGER_SNCODE", "PRIOR")
        with patch("helia_profiler.nsx.nsx_api.flash_app"):
            nsx.flash(tmp_path, jlink_serial="OVERRIDE")
        assert os.environ["SEGGER_SNCODE"] == "PRIOR"


class TestNsxConfigure:
    def test_configure_calls_api(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.configure_app") as cfg_mock:
            nsx.configure(tmp_path, toolchain="gcc", timeout_s=120)
        cfg_mock.assert_called_once_with(
            tmp_path, toolchain="gcc", timeout_s=120, emit=nsx._quiet_emitter
        )


class TestNsxLock:
    def test_lock_calls_api_quietly(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.lock_app", return_value=tmp_path / "nsx.lock") as m:
            result = nsx.lock(tmp_path, timeout_s=180)
        m.assert_called_once_with(
            tmp_path,
            update=False,
            quiet=True,
            timeout_s=180,
            resolve_ttl_s=1800,
            emit=nsx._quiet_emitter,
        )
        assert result == tmp_path / "nsx.lock"

    def test_lock_propagates_update_flag(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.lock_app", return_value=None) as m:
            nsx.lock(tmp_path, update=True)
        assert m.call_args.kwargs["update"] is True


class TestNsxSync:
    def test_sync_calls_api(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.sync_app") as m:
            nsx.sync(tmp_path, timeout_s=300)
        m.assert_called_once_with(
            tmp_path, frozen=False, force=False, timeout_s=300, emit=nsx._quiet_emitter
        )

    def test_sync_frozen(self, tmp_path: Path) -> None:
        with patch("helia_profiler.nsx.nsx_api.sync_app") as m:
            nsx.sync(tmp_path, frozen=True)
        assert m.call_args.kwargs["frozen"] is True
