from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from helia_profiler.cli import cache_cmd as cli


def test_cache_purge_removes_workspace_cache(tmp_path: Path, monkeypatch, capsys) -> None:
    workspaces_root = tmp_path / ".cache" / "helia-profiler" / "workspaces"
    (workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot").mkdir(parents=True)
    (workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot" / "nsx.lock").write_text("lock")

    module_cache = SimpleNamespace(
        clear=lambda: 2,
        module_cache_root=lambda: tmp_path / "module-cache",
        iter_entries=lambda: [],
    )
    resolve_cache = SimpleNamespace(invalidate_all=lambda: None)
    fake_neuralspotx = ModuleType("neuralspotx")
    fake_neuralspotx.module_cache = module_cache
    fake_neuralspotx._resolve_cache = resolve_cache

    monkeypatch.setitem(sys.modules, "neuralspotx", fake_neuralspotx)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))

    cli._cmd_cache_purge()

    out = capsys.readouterr().out
    assert "Purged 2 cached module(s)." in out
    assert "Purged resolve-ref cache." in out
    assert "Purged 1 cached workspace(s)." in out
    assert workspaces_root.exists() is False


def test_cache_info_reports_workspace_cache(tmp_path: Path, monkeypatch, capsys) -> None:
    module_root = tmp_path / "module-cache"
    module_entry = module_root / "entry"
    module_entry.mkdir(parents=True)
    (module_entry / "blob.bin").write_bytes(b"1234")

    resolve_path = tmp_path / "resolve-cache.json"
    resolve_path.write_text("{}")

    workspaces_root = tmp_path / ".cache" / "helia-profiler" / "workspaces"
    workspace = workspaces_root / "apollo510_evb-arm-none-eabi-gcc-helia-aot"
    workspace.mkdir(parents=True)
    (workspace / "nsx.lock").write_text("lock")

    module_cache = SimpleNamespace(
        module_cache_root=lambda: module_root,
        iter_entries=lambda: [module_entry],
    )
    resolve_cache_mod = ModuleType("neuralspotx._resolve_cache")
    resolve_cache_mod._cache_path = lambda: resolve_path
    fake_neuralspotx = ModuleType("neuralspotx")
    fake_neuralspotx.module_cache = module_cache

    monkeypatch.setitem(sys.modules, "neuralspotx", fake_neuralspotx)
    monkeypatch.setitem(sys.modules, "neuralspotx._resolve_cache", resolve_cache_mod)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))

    cli._cmd_cache_info()

    out = capsys.readouterr().out
    assert "Module cache:" in out
    assert "Resolve-ref cache:" in out
    assert "Workspace cache:" in out
    assert "Entries: 1" in out
