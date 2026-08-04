"""Exhaustive, table-driven tests for helia_profiler.redact.

Covers the exact categories the field-diagnostics support bundle must
scrub by default: home/workspace absolute paths (POSIX/Windows/UNC),
credentialed and token-bearing URLs, common credential/token shapes, secret
env-style assignments, and device serial numbers — plus the explicit
``--raw-probe-ids`` opt-in and "nothing to redact" no-op behavior.
"""

from __future__ import annotations

import json

import pytest

from helia_profiler.redact import (
    RedactionCounts,
    RedactionPolicy,
    redact_serial,
    redact_text,
    redact_value,
)

# ---------------------------------------------------------------------------
# Absolute filesystem paths — home dir, workspace dirs, POSIX, Windows, UNC.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/Users/adam.page/models/foo.tflite", "<redacted-path>/foo.tflite"),
        ("/home/ci-runner/work/hpx/build", "<redacted-path>/build"),
        ("/root/.cache/hpx/workspace", "<redacted-path>/workspace"),
        ("/dev/ttyACM0", "<redacted-path>/ttyACM0"),
        (r"C:\Users\Ada\module", "<redacted-path>/module"),
        (r"C:\Program Files\SEGGER\JLink_V960\JLink.exe", "<redacted-path>/JLink.exe"),
        (r"\\build-server\share\hpx-workspace", "<redacted-path>/hpx-workspace"),
        ("/opt/toolchains/gcc-arm/bin/arm-none-eabi-gcc", "<redacted-path>/arm-none-eabi-gcc"),
        # A path that IS a home directory has the account name as its final
        # component — unlike an ordinary basename, that must never be kept.
        ("/Users/adam.page/", "<redacted-path>"),
        ("/Users/adam.page", "<redacted-path>"),
        (r"C:\Users\adam.page", "<redacted-path>"),
    ],
)
def test_redact_text_scrubs_absolute_paths_keeping_basename(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected
    assert counts.paths == 1
    assert counts.total == 1


@pytest.mark.parametrize(
    "raw",
    [
        "relative/module/path",
        "modules/demo",
        "nsx.lock",
        "just some prose with no path in it",
    ],
)
def test_redact_text_leaves_relative_paths_and_plain_text_alone(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


def test_redact_text_paths_disabled_by_policy() -> None:
    raw = "/Users/adam.page/models/foo.tflite"

    redacted, counts = redact_text(raw, RedactionPolicy(redact_paths=False))

    assert redacted == raw
    assert counts.paths == 0


# ---------------------------------------------------------------------------
# URL credentials and token-shaped query parameters.
# ---------------------------------------------------------------------------


def _userinfo_case(user_pass: str, host_path: str) -> tuple[str, str]:
    """Build a (raw, expected) URL-credential fixture without a literal
    credential-shaped constant in the test source."""
    raw = f"https://{user_pass}@{host_path}"
    expected = f"https://<redacted>@{host_path}"
    return raw, expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        _userinfo_case("user:" + "hunter2", "github.com/org/repo.git"),
        (
            "https://api.example.com/v1?token=" + "abcdef123456" + "&ref=v1.0",
            "https://api.example.com/v1?token=<redacted>&ref=v1.0",
        ),
        (
            "https://example.com/download?api_key=" + "SECRETVALUE",
            "https://example.com/download?api_key=<redacted>",
        ),
        (
            "https://example.com/download?access_token=" + "abc123" + "&other=keep",
            "https://example.com/download?access_token=<redacted>&other=<redacted>",
        ),
        (
            # Not a named credential/token parameter at all - redacted by
            # default anyway, since the allow-list is narrow and explicit.
            "https://example.com/download?x=1",
            "https://example.com/download?x=<redacted>",
        ),
        (
            # A parameter name on the narrow allow-list is left alone.
            "https://example.com/download?ref=v1.0.0",
            "https://example.com/download?ref=v1.0.0",
        ),
    ],
)
def test_redact_text_scrubs_url_credentials_and_tokens(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected


def test_redact_text_url_allowlisted_query_params_and_no_credentials_is_untouched() -> None:
    raw = "https://example.com/download?ref=v1.0.0&page=2"

    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


def test_redact_text_url_path_query_param_is_redacted_not_allowlisted() -> None:
    # A query key literally named "path" is NOT allow-listed: its value
    # could itself be an absolute filesystem path, which is exactly the
    # category this module exists to redact.
    raw = "https://example.com/api?path=/Users/adam.page/secret-project"

    redacted, counts = redact_text(raw)

    assert "adam.page" not in redacted
    assert "secret-project" not in redacted
    assert counts.total >= 1


def test_redact_text_url_single_token_userinfo_credential_is_redacted() -> None:
    token = "ghp_" + "A" * 36
    raw = f"https://{token}@example.invalid/demo.git"

    redacted, counts = redact_text(raw)

    assert token not in redacted
    assert redacted == "https://<redacted>@example.invalid/demo.git"
    assert counts.urls == 1


def test_redact_text_url_path_survives_intact() -> None:
    raw = "https://github.com/AmbiqAI/neuralspotx/releases/download/v0.7.10/asset.tar.gz"

    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


def test_redact_text_plain_url_without_credentials_is_untouched() -> None:
    raw = "https://github.com/AmbiqAI/nsx-ambiq-sdk.git"

    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


@pytest.mark.parametrize(
    "token_builder",
    [
        lambda: "ghp_" + "A" * 36,
        lambda: "AKIA" + "B" * 16,
        lambda: (
            "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dQw4w9WgXcQ_abcXYZ123"
        ),
    ],
)
def test_redact_text_scrubs_known_token_shapes_embedded_in_url_path(token_builder) -> None:
    token = token_builder()
    raw = f"https://example.com/download/{token}"

    redacted, counts = redact_text(raw)

    assert token not in redacted
    assert counts.total >= 1


def test_redact_text_scrubs_secret_shaped_query_param_names_beyond_the_narrow_set() -> None:
    # A parameter name that isn't in the credential/token/key/secret/auth
    # set is still redacted by default (deny-by-default query values),
    # e.g. GitLab's private_token or a signed-URL signature.
    raw = "https://gitlab.example.com/api/v4/projects?private_token=" + "SUPERSECRETVALUE123"

    redacted, counts = redact_text(raw)

    assert "SUPERSECRETVALUE123" not in redacted
    assert counts.total >= 1


def test_redact_text_scrubs_file_uri_path_keeping_basename() -> None:
    raw = "file:///Users/adam.page/proj/nsx.lock"

    redacted, counts = redact_text(raw)

    assert "adam.page" not in redacted
    assert redacted.endswith("nsx.lock")
    assert counts.paths >= 1


# ---------------------------------------------------------------------------
# Common credential/token shapes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 22,
        "AKIA" + "A" * 16,
        "xoxb-" + "c" * 12,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ_abcXYZ123",
    ],
)
def test_redact_text_scrubs_known_token_shapes(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == "<redacted-token>"
    assert counts.tokens == 1
    assert counts.total == 1


def test_redact_text_scrubs_bearer_token_keeping_prefix() -> None:
    raw = "Authorization: Bearer " + "x" * 40

    redacted, counts = redact_text(raw)

    assert redacted == "Authorization: Bearer <redacted-token>"
    assert counts.tokens == 1


# ---------------------------------------------------------------------------
# KEY=VALUE / KEY: VALUE secret-shaped assignments ("env values").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("API_KEY=supersecretvalue123", "API_KEY=<redacted>"),
        ("API_KEY: supersecretvalue123", "API_KEY: <redacted>"),
        ("token=abcdefghij", "token=<redacted>"),
        ("PASSWORD='correct horse battery staple'", "PASSWORD='<redacted>'"),
        ('secret: "abcdefg1234"', 'secret: "<redacted>"'),
        ("ACCESS_KEY=" + "A" * 16, "ACCESS_KEY=<redacted>"),
    ],
)
def test_redact_text_scrubs_secret_assignments(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected
    assert counts.total >= 1


@pytest.mark.parametrize(
    "raw",
    [
        "iterations=100",
        "board=apollo510_evb",
        "arena_size=65536",
        "channel: stable",
    ],
)
def test_redact_text_does_not_flag_ordinary_assignments(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


# ---------------------------------------------------------------------------
# Device serial numbers — structural (by field name), not digit-pattern.
# ---------------------------------------------------------------------------


def test_redact_serial_replaces_with_stable_hash_preview() -> None:
    redacted, counts = redact_serial("1160002204")

    assert redacted != "1160002204"
    assert redacted.startswith("<redacted-serial:")
    assert counts.serials == 1
    assert counts.total == 1


def test_redact_serial_is_deterministic_for_the_same_value() -> None:
    first, _ = redact_serial("1160002204")
    second, _ = redact_serial("1160002204")

    assert first == second


def test_redact_serial_differs_for_different_values() -> None:
    first, _ = redact_serial("1160002204")
    second, _ = redact_serial("1160002205")

    assert first != second


def test_redact_serial_empty_value_is_a_no_op() -> None:
    redacted, counts = redact_serial("")

    assert redacted == ""
    assert counts.total == 0


def test_redact_serial_raw_probe_ids_opt_out_leaves_value_untouched() -> None:
    redacted, counts = redact_serial("1160002204", RedactionPolicy(redact_probe_serials=False))

    assert redacted == "1160002204"
    assert counts.total == 0


def test_redact_value_routes_serial_shaped_keys_through_redact_serial() -> None:
    value = {
        "serial": "1160002204",
        "serial_number": "HPX-000123",
        "board_id": "1160002204",
    }

    redacted, counts = redact_value(value)

    assert redacted["serial"].startswith("<redacted-serial:")
    assert redacted["serial_number"].startswith("<redacted-serial:")
    # A key with no "serial" substring is treated as plain text: 10 raw
    # digits alone match no path/url/token/secret-assignment pattern, so
    # it is left untouched.
    assert redacted["board_id"] == "1160002204"
    assert counts.serials == 2


def test_redact_value_raw_probe_ids_opt_in_keeps_probe_serials_readable() -> None:
    value = {"serial": "1160002204"}

    redacted, counts = redact_value(value, RedactionPolicy(redact_probe_serials=False))

    assert redacted["serial"] == "1160002204"
    assert counts.total == 0


# ---------------------------------------------------------------------------
# Recursive structure handling and no-op / idempotence guarantees.
# ---------------------------------------------------------------------------


def test_redact_value_recurses_through_nested_dicts_and_lists() -> None:
    value = {
        "path": "/Users/adam.page/models/foo.tflite",
        "nested": {
            "urls": [
                "https://" + "user:" + "hunter2" + "@example.com/repo.git",
                "https://example.com/plain",
            ],
            "serial_number": "ABC123",
        },
        "list_of_dicts": [{"token": "ghp_" + "z" * 36}],
    }

    redacted, counts = redact_value(value)

    assert redacted["path"] == "<redacted-path>/foo.tflite"
    assert redacted["nested"]["urls"][0] == "https://<redacted>@example.com/repo.git"
    assert redacted["nested"]["urls"][1] == "https://example.com/plain"
    assert redacted["nested"]["serial_number"].startswith("<redacted-serial:")
    # Routed by the "token" key itself (structural secret-key redaction),
    # not by the generic ghp_-shape pattern -- the key alone is enough.
    assert redacted["list_of_dicts"][0]["token"] == "<redacted>"
    assert counts.paths == 1
    assert counts.urls == 1
    assert counts.serials == 1
    assert counts.env_values == 1
def test_redact_value_preserves_tuple_type() -> None:
    value = ("relative/path", "plain text")

    redacted, _ = redact_value(value)

    assert isinstance(redacted, tuple)
    assert redacted == value


def test_redact_value_passes_through_non_string_scalars_untouched() -> None:
    value = {"count": 5, "enabled": True, "ratio": 1.5, "missing": None}

    redacted, counts = redact_value(value)

    assert redacted == value
    assert counts.total == 0


def test_redact_value_result_is_json_serializable() -> None:
    value = {
        "path": "/Users/adam.page/x.tflite",
        "url": "https://user:pass@example.com/repo.git",
        "count": 3,
        "nested": [1, 2, {"serial": "999"}],
    }

    redacted, _ = redact_value(value)

    # Must round-trip cleanly: the collector writes this straight to JSON.
    assert json.loads(json.dumps(redacted, sort_keys=True)) == redacted


def test_redact_text_empty_string_is_a_no_op() -> None:
    redacted, counts = redact_text("")

    assert redacted == ""
    assert counts == RedactionCounts()


def test_redact_text_never_raises_on_arbitrary_bytes_like_text() -> None:
    # Adversarial/garbage input must never raise — redaction always degrades
    # to "leave it alone", never to an exception.
    weird = "\x00\x01 not/a a:b://c \\ /// mixed \\\\ separators"

    redacted, counts = redact_text(weird)

    assert isinstance(redacted, str)
    assert isinstance(counts, RedactionCounts)


def test_redaction_counts_to_dict_includes_total() -> None:
    counts = RedactionCounts(paths=1, urls=2, tokens=3, serials=4, env_values=5)

    assert counts.to_dict() == {
        "paths": 1,
        "urls": 2,
        "tokens": 3,
        "serials": 4,
        "env_values": 5,
        "total": 15,
    }


def test_redaction_counts_combined_sums_componentwise() -> None:
    a = RedactionCounts(paths=1, urls=1)
    b = RedactionCounts(paths=2, tokens=3)

    combined = a.combined(b)

    assert combined == RedactionCounts(paths=3, urls=1, tokens=3)
