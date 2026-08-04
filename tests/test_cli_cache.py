from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from typer.testing import CliRunner

from helia_profiler.cli.app import app
from helia_profiler.cli import cache_cmd as cli


def test_cache_purge_removes_workspace_cache(tmp_path: Path, monkeypatch, capsys) -> None:
    workspaces_root = tmp_path / ".cache" / "helia-profiler" / "workspaces"
    (workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot").mkdir(parents=True)
    (workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot" / "nsx.lock").write_text("lock")

    fake_neuralspotx = ModuleType("neuralspotx")
    fake_neuralspotx.clean_cache = lambda **_kwargs: SimpleNamespace(removed_count=3)

    monkeypatch.setitem(sys.modules, "neuralspotx", fake_neuralspotx)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))

    cli._cmd_cache_purge()

    out = capsys.readouterr().out
    assert "Purged 3 neuralSPOT-X cache item(s)." in out
    assert "Purged 1 cached workspace(s)." in out
    assert workspaces_root.exists() is False


def test_cache_info_reports_workspace_cache(tmp_path: Path, monkeypatch, capsys) -> None:
    workspaces_root = tmp_path / ".cache" / "helia-profiler" / "workspaces"
    workspace = workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot"
    workspace.mkdir(parents=True)
    (workspace / "nsx.lock").write_text("lock")

    fake_neuralspotx = ModuleType("neuralspotx")
    fake_neuralspotx.cache_info = lambda: SimpleNamespace(
        entry_count=1,
        total_size_bytes=4,
    )
    clean_calls: list[bool] = []

    def clean_cache(*, dry_run: bool = False) -> SimpleNamespace:
        clean_calls.append(dry_run)
        return SimpleNamespace(root=str(tmp_path / "nsx"), removed_count=4)

    fake_neuralspotx.clean_cache = clean_cache

    monkeypatch.setitem(sys.modules, "neuralspotx", fake_neuralspotx)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))

    cli._cmd_cache_info()

    out = capsys.readouterr().out
    assert "neuralSPOT-X cache:" in out
    assert "Purgeable items: 4" in out
    assert "Workspace cache:" in out
    assert "Entries: 1" in out
    assert clean_calls == [True]


def test_cache_purge_removes_hash_caches_but_preserves_unrelated_files(
    tmp_path: Path, monkeypatch
) -> None:
    nsx_root = tmp_path / "nsx"
    nsx_root.mkdir()
    stale = [
        nsx_root / "git-artifact-hashes.json",
        nsx_root / "git-artifact-hashes.json.lock",
        nsx_root / "git-artifact-hashes-v2.json",
        nsx_root / "git-artifact-hashes-v2.json.lock",
    ]
    for path in stale:
        path.write_text("stale")
    unrelated = nsx_root / "customer-cache.json"
    unrelated.write_text("keep")

    monkeypatch.setenv("NSX_CACHE_DIR", str(nsx_root))
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))

    cli._cmd_cache_purge()

    assert all(not path.exists() for path in stale)
    assert unrelated.read_text() == "keep"


def test_cache_help_describes_every_purged_cache_class() -> None:
    result = CliRunner().invoke(app, ["cache", "--help"])

    assert result.exit_code == 0
    assert "module artifacts" in result.stdout
    assert "git-artifact hashes" in result.stdout
    assert "resolved refs" in result.stdout
    assert "generated workspaces" in result.stdout
