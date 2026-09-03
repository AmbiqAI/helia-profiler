"""Unit coverage for the pre-commit local hooks under tools/hooks/.

Imports each hook script as a module (matching the pattern used for
tools/gen_issue_code_reference.py) so these stay fast, local, and
subprocess-free -- the hooks themselves are exercised end-to-end in
CI's `pre-commit` job.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "tools" / "hooks"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


todo_needs_issue = _load("todo_needs_issue")
strip_ai_trailers = _load("strip_ai_trailers")


# --------------------------------------------------------------------------
# todo_needs_issue
# --------------------------------------------------------------------------


def test_todo_with_issue_reference_passes(tmp_path: Path) -> None:
    target = tmp_path / "ok.py"
    target.write_text("# TODO(#123): follow up later\n")
    assert todo_needs_issue.main([str(target)]) == 0


def test_todo_with_word_reference_passes(tmp_path: Path) -> None:
    target = tmp_path / "ok.py"
    target.write_text("# TODO(verify): double check this\n# HACK(alice): temp shim\n")
    assert todo_needs_issue.main([str(target)]) == 0


def test_bare_todo_fails(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text("# TODO: fix this\n")
    assert todo_needs_issue.main([str(target)]) == 1


def test_bare_fixme_fails(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text("# FIXME this is broken\n")
    assert todo_needs_issue.main([str(target)]) == 1


def test_empty_parens_treated_as_bare(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text("# TODO(): missing a reference\n")
    assert todo_needs_issue.main([str(target)]) == 1


def test_binary_file_skipped(tmp_path: Path) -> None:
    target = tmp_path / "bad.bin"
    target.write_bytes(b"\x00\x01TODO: nope\x00")
    assert todo_needs_issue.main([str(target)]) == 0


def test_non_utf8_file_skipped(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    # Latin-1 bytes that are not valid UTF-8 (e.g. 0xff 0xfe), no NUL byte.
    target.write_bytes(b"# TODO: \xff\xfe not valid utf-8\n")
    assert todo_needs_issue.main([str(target)]) == 0


def test_git_lfs_pointer_skipped(tmp_path: Path) -> None:
    target = tmp_path / "model.tflite"
    target.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\n"
        "size 123\n"
        "TODO: this is pointer metadata, not content\n"
    )
    assert todo_needs_issue.main([str(target)]) == 0


def test_missing_file_skipped(tmp_path: Path) -> None:
    assert todo_needs_issue.main([str(tmp_path / "does-not-exist.py")]) == 0


# --------------------------------------------------------------------------
# strip_ai_trailers
# --------------------------------------------------------------------------


def test_removes_claude_co_authored_by() -> None:
    raw = (
        b"Fix the thing\n\n"
        b"Co-authored-by: Claude Fable 5 <noreply@anthropic.com>\n"
        b"Co-authored-by: Jane Doe <jane@example.com>\n"
    )
    out = strip_ai_trailers.strip_ai_trailers(raw)
    assert b"Claude" not in out
    assert b"Jane Doe <jane@example.com>" in out


def test_keeps_human_co_authored_by() -> None:
    raw = b"Fix the thing\n\nCo-authored-by: Jane Doe <jane@example.com>\n"
    assert strip_ai_trailers.strip_ai_trailers(raw) == raw


def test_removes_claude_session_and_agent_assisted() -> None:
    raw = (
        b"Fix the thing\n\n"
        b"Claude-Session: https://claude.ai/chat/abc\n"
        b"Agent-Assisted: true\n"
        b"Generated-by: some-tool\n"
        b"Generated-with: another-tool\n"
    )
    out = strip_ai_trailers.strip_ai_trailers(raw)
    assert out == b"Fix the thing\n\n"


def test_idempotent() -> None:
    raw = (
        b"Fix the thing\n\n"
        b"Co-authored-by: Claude <noreply@anthropic.com>\n"
        b"Co-authored-by: Jane Doe <jane@example.com>\n"
    )
    once = strip_ai_trailers.strip_ai_trailers(raw)
    twice = strip_ai_trailers.strip_ai_trailers(once)
    assert once == twice


def test_crlf_preserved() -> None:
    raw = b"Fix the thing\r\n\r\nCo-authored-by: Claude <noreply@anthropic.com>\r\nMore body\r\n"
    out = strip_ai_trailers.strip_ai_trailers(raw)
    assert out == b"Fix the thing\r\n\r\nMore body\r\n"
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_scissors_region_untouched() -> None:
    raw = (
        b"Fix the thing\n\n"
        b"Co-authored-by: Claude <noreply@anthropic.com>\n"
        b"# ------------------------ >8 ------------------------\n"
        b"# Everything below is ignored by git; a stray trailer here\n"
        b"Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    out = strip_ai_trailers.strip_ai_trailers(raw)
    assert out.count(b"Co-authored-by: Claude") == 1
    assert out.endswith(b"Co-authored-by: Claude <noreply@anthropic.com>\n")


def test_empty_message_is_noop() -> None:
    assert strip_ai_trailers.strip_ai_trailers(b"") == b""


def test_main_empty_file_exits_zero(tmp_path: Path) -> None:
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_text("")
    assert strip_ai_trailers.main([str(target)]) == 0
    assert target.read_text() == ""


def test_main_missing_file_exits_zero(tmp_path: Path) -> None:
    assert strip_ai_trailers.main([str(tmp_path / "no-such-file")]) == 0


def test_main_handles_merge_source_args(tmp_path: Path) -> None:
    # git invokes prepare-commit-msg as: <file> [source] [sha1]; "merge" is
    # a valid source value and must not change how the file is processed.
    target = tmp_path / "MERGE_MSG"
    target.write_text("Merge branch 'main'\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    assert strip_ai_trailers.main([str(target), "merge"]) == 0
    assert "Claude" not in target.read_text()


def test_main_writes_only_when_changed(tmp_path: Path) -> None:
    target = tmp_path / "COMMIT_EDITMSG"
    target.write_text("Plain message\n")
    before = target.stat().st_mtime_ns
    assert strip_ai_trailers.main([str(target)]) == 0
    assert target.stat().st_mtime_ns == before
