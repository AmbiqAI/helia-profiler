"""Tests for the error hierarchy."""

import pytest

from helia_profiler.errors import (
    BuildError,
    CaptureError,
    ConfigError,
    DependencyError,
    EngineError,
    FirmwareError,
    HpxError,
    LockError,
    NetworkError,
    PlatformError,
    PowerError,
    ReportError,
    VersionError,
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


class TestDependencyErrorTaxonomy:
    """VersionError and LockError specialize DependencyError for CLI/collector triage."""

    @pytest.mark.parametrize("cls", [DependencyError, VersionError, LockError])
    def test_is_dependency_build_and_hpx_error(self, cls):
        err = cls("test error")
        assert isinstance(err, DependencyError)
        assert isinstance(err, BuildError)
        assert isinstance(err, HpxError)

    def test_version_error_is_not_lock_error_and_vice_versa(self):
        assert not issubclass(VersionError, LockError)
        assert not issubclass(LockError, VersionError)

    def test_version_error_catchable_as_dependency_error(self):
        with pytest.raises(DependencyError):
            raise VersionError("nsx.lock schema mismatch")

    def test_lock_error_catchable_as_dependency_error(self):
        with pytest.raises(DependencyError):
            raise LockError("nsx.lock is missing")

    def test_lock_error_carries_hint(self):
        err = LockError("nsx.lock is missing", hint="Run hpx profile once online first.")
        assert err.hint == "Run hpx profile once online first."
        assert "Run hpx profile once online first." in str(err)

    def test_network_error_unrelated_to_new_taxonomy(self):
        # NetworkError predates and is orthogonal to Version/LockError: it is
        # about transport retriability, not what kind of dependency state broke.
        assert not issubclass(NetworkError, VersionError)
        assert not issubclass(NetworkError, LockError)
        assert issubclass(NetworkError, BuildError)
