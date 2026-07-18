"""Tests for the error hierarchy."""

import pytest

from helia_profiler.errors import (
    BuildError,
    CaptureError,
    ConfigError,
    EngineError,
    FirmwareError,
    HpxError,
    PlatformError,
    PowerError,
    ReportError,
)


class TestHpxErrorBase:
    def test_message_only(self):
        err = HpxError("something broke")
        assert str(err) == "something broke"

    def test_message_with_hint(self):
        err = HpxError("something broke", hint="try this")
        assert "something broke" in str(err)
        assert "try this" in str(err)

    def test_hint_attribute(self):
        err = HpxError("msg", hint="the hint")
        assert err.hint == "the hint"

    def test_no_hint_attribute(self):
        err = HpxError("msg")
        assert err.hint is None


class TestSubclassHierarchy:
    """All error types should be subclasses of HpxError."""

    @pytest.mark.parametrize(
        "cls",
        [
            ConfigError,
            PlatformError,
            EngineError,
            FirmwareError,
            BuildError,
            CaptureError,
            PowerError,
            ReportError,
        ],
    )
    def test_is_hpx_error(self, cls):
        err = cls("test error")
        assert isinstance(err, HpxError)

    @pytest.mark.parametrize(
        "cls",
        [
            ConfigError,
            PlatformError,
            EngineError,
            FirmwareError,
            BuildError,
            CaptureError,
            PowerError,
            ReportError,
        ],
    )
    def test_catchable_as_hpx_error(self, cls):
        with pytest.raises(HpxError):
            raise cls("test error")


class TestBuildError:
    def test_returncode_and_details(self):
        err = BuildError(
            "build failed",
            returncode=2,
            details="fatal: missing header",
        )
        assert err.returncode == 2
        assert err.details == "fatal: missing header"
        assert isinstance(err, HpxError)

    def test_defaults_to_none(self):
        err = BuildError("build failed")
        assert err.returncode is None
        assert err.details is None
