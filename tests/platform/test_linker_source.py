"""Configured link metadata is matched separately for each executable and input state."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from helia_profiler.firmware.linker_source import (
    request_linker_information,
    verify_configured_linker,
)
from helia_profiler.platform.linker.data import characterized_input
from helia_profiler.platform.linker.files import source_sha256
from helia_profiler.platform.linker.model import LinkerFile
from helia_profiler.platform.memory_map import LinkFamily
from helia_profiler.results.dependencies import ContentDigest, DependencyModule


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _backend(app, build, layout, root, *, power_script=None, duplicate=False):
    reply = build / ".cmake/api/v1/reply"
    targets = []
    for target in ("hpx_profiler", "hpx_profiler_power"):
        script = (
            power_script
            if target.endswith("_power") and power_script
            else root / layout.script.path
        )
        fragments = [{"role": "flags", "fragment": f'-T"{script}"'}]
        if duplicate:
            fragments.append({"role": "flags", "fragment": f'-T"{script}"'})
        _json(reply / f"{target}.json", {"link": {"commandFragments": fragments}})
        targets.append({"name": target, "jsonFile": f"{target}.json"})
    _json(
        reply / "model.json",
        {
            "paths": {"source": str(app), "build": str(build)},
            "configurations": [{"targets": targets}],
        },
    )
    _json(
        reply / "index-1.json",
        {
            "reply": {
                "client-hpx-linker": {
                    "codemodel-v2": {"jsonFile": "model.json"},
                }
            }
        },
    )


@pytest.fixture
def configured(tmp_path):
    app, build, root = tmp_path / "app", tmp_path / "build", tmp_path / "app/modules/sdk's root"
    app.mkdir()
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    sources = []
    for value in layout.files:
        path = root / value.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source {value.path}\n", encoding="utf-8")
        sources.append(LinkerFile(value.path, source_sha256(path)))
    layout = replace(
        layout,
        files=tuple(sources),
        script=next(v for v in sources if v.path == layout.script.path),
    )
    modules = tuple(
        DependencyModule(
            name=name,
            project="nsx-ambiq-sdk",
            kind="git",
            requested_ref=layout.sdk_commit,
            requested_tag=None,
            peeled_commit=layout.sdk_commit,
            content_hash=ContentDigest("sha256", "a" * 64),
            url=None,
            vendored_at="modules/sdk's root",
        )
        for name in ("nsx-core", "nsx-soc-hal", "nsx-ambiqsuite")
    )
    _backend(app, build, layout, root)
    return app, build, root, layout, modules


def _verify(state, *, role="profile", modules=None):
    app, build, _, expected, original = state
    return verify_configured_linker(
        app_dir=app,
        build_dir=build,
        modules=original if modules is None else modules,
        expected=expected,
        role=role,
        target="hpx_profiler" if role == "profile" else "hpx_profiler_power",
    )


def test_both_targets_match_their_own_backend_selection_with_quoted_paths(configured):
    assert _verify(configured).verified
    power = _verify(configured, role="power")
    assert power.verified and power.target == "hpx_profiler_power"
    assert power.role == "power" and power.layout == configured[3]


def test_changed_input_between_profile_and_power_does_not_reuse_a_verdict(configured):
    assert _verify(configured).verified
    _, _, root, layout, _ = configured
    (root / layout.script.path).write_text("different source\n", encoding="utf-8")
    power = _verify(configured, role="power")
    assert not power.verified and "content differs" in power.reason


def test_power_target_can_select_a_different_script_than_profile(configured):
    app, build, root, layout, _ = configured
    other = root / "other.ld"
    other.write_text("other script\n", encoding="utf-8")
    _backend(app, build, layout, root, power_script=other)
    assert _verify(configured).verified
    power = _verify(configured, role="power")
    assert not power.verified and "another linker script" in power.reason


def test_a_different_sdk_revision_does_not_borrow_matching_baseline_files(configured):
    modules = tuple(replace(value, peeled_commit="b" * 40) for value in configured[4])
    result = _verify(configured, modules=modules)
    assert not result.verified and "SDK revision" in result.reason


def test_missing_or_ambiguous_backend_information_is_unavailable(configured):
    app, build, root, layout, _ = configured
    _backend(app, build, layout, root, duplicate=True)
    assert not _verify(configured).verified
    for path in (build / ".cmake/api/v1/reply").glob("index-*.json"):
        path.unlink()
    result = _verify(configured)
    assert not result.verified and "unavailable" in result.reason


@pytest.mark.parametrize("initial_newline", ["\n", "\r\n"])
def test_source_matching_is_independent_of_checkout_line_endings(configured, initial_newline):
    _, _, root, layout, _ = configured
    path = root / layout.script.path
    source = path.read_text(encoding="utf-8")
    path.write_bytes(source.replace("\n", initial_newline).encode("utf-8"))
    assert _verify(configured).verified
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert _verify(configured).verified


def test_request_is_present_even_in_an_existing_build_tree(tmp_path):
    build = tmp_path / "build"
    (build / "build.ninja").parent.mkdir(parents=True)
    (build / "build.ninja").touch()
    request_linker_information(build)
    root = build / ".cmake/api/v1/query/client-hpx-linker"
    assert (root / "codemodel-v2").is_file()


@pytest.fixture
def linked_pipeline(configured, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from helia_profiler.config import load_config
    from helia_profiler.deps.compatibility import QualificationState
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.platform.provenance import RegionExtent
    from helia_profiler.results import DependencyLockMode
    from helia_profiler.results.dependencies import DependencyLockProvenance, DependencyWorkspace
    from helia_profiler.stages.resolve_platform import ResolvePlatformStage

    app, _, root, raw_layout, modules = configured
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "power": {"enabled": True, "firmware": "dedicated"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    platform = ctx.run_metadata.platform_provenance
    assert platform is not None
    layout = replace(
        raw_layout,
        regions=tuple(replace(r, allowed=(r.app_window,)) for r in raw_layout.regions),
    )
    ctx.run_metadata.platform_provenance = replace(
        platform,
        expected_linker=layout,
        app_regions=tuple(RegionExtent(r.region, r.app_window) for r in layout.regions),
    )
    ctx.firmware_dir = app
    ctx.dependency_workspace = DependencyWorkspace(
        1, "a" * 64, "test", "b" * 64, ContentDigest("sha256", "c" * 64), {}, app.parent
    )
    build = app / "build" / ctx.resolved_board.name
    events = []
    inside = False
    current_modules = list(modules)

    @contextmanager
    def mutex(_workspace):
        nonlocal inside
        assert not inside
        inside = True
        events.append("mutex-enter")
        try:
            yield
        finally:
            inside = False
            events.append("mutex-exit")

    def prepare(_ctx):
        assert inside
        events.append("prepare")

    def configure(*args, **kwargs):
        assert inside and kwargs["frozen"]
        assert (build / ".cmake/api/v1/query/client-hpx-linker/codemodel-v2").is_file()
        events.append("configure")
        _backend(app, build, layout, root)

    def read_lock(_app):
        assert inside and events[-1] == "configure"
        events.append("current-lock")
        return DependencyLockProvenance(
            app / "nsx.lock",
            "d" * 64,
            "e" * 64,
            (),
            tuple(current_modules),
            (),
            QualificationState.QUALIFIED,
            "f" * 64,
            "a" * 64,
            DependencyLockMode.REUSED,
            False,
        )

    def compile_target(*args, **kwargs):
        assert inside and events[-1] == "current-lock"
        target = kwargs.get("target", "hpx_profiler")
        role = "power" if target.endswith("_power") else "profile"
        decision = next(v for v in ctx.run_metadata.linker_verifications if v.role == role)
        assert decision.target == target
        events.append(target)
        (build / target).write_bytes(b"new executable")

    monkeypatch.setattr("helia_profiler.deps.dependencies.workspace_mutex", mutex)
    monkeypatch.setattr("helia_profiler.deps.dependencies.prepare_locked_dependencies", prepare)
    monkeypatch.setattr("helia_profiler.deps.nsx.configure", configure)
    monkeypatch.setattr("helia_profiler.firmware.linker.read_dependency_lock_provenance", read_lock)
    monkeypatch.setattr("helia_profiler.deps.nsx.build", compile_target)
    monkeypatch.setattr("helia_profiler.firmware.render_power_source", lambda *a, **k: None)
    return ctx, root, layout, current_modules, events


@pytest.mark.parametrize("change", ["unchanged", "source", "module"])
def test_profile_and_each_power_rebuild_refresh_sources_under_the_mutex(linked_pipeline, change):
    from helia_profiler.firmware import build_app
    from helia_profiler.results import PowerRunPlan
    from helia_profiler.stages.build_power_firmware import BuildPowerFirmwareStage
    from tests.pipeline_context_helpers import set_profile_firmware

    ctx, root, layout, modules, events = linked_pipeline
    build, binary = build_app(ctx)
    set_profile_firmware(ctx, build_dir=build, binary_path=binary)
    profile = ctx.run_metadata.linker_verifications[0]
    assert profile.verified
    if change == "source":
        (root / layout.script.path).write_text("changed script", encoding="utf-8")
    elif change == "module":
        modules[:] = [replace(m, peeled_commit="b" * 40) for m in modules]

    for count in (11, 17):
        ctx.publish_power_plan(PowerRunPlan("dedicated", count, count_source="configured"))
        BuildPowerFirmwareStage().run(ctx)
        decisions = ctx.run_metadata.linker_verifications
        assert decisions[0] == profile
        assert len(decisions) == 2 and decisions[1].role == "power"
        assert decisions[1].verified is (change == "unchanged")
        assert ctx.power_firmware is not None
    assert events == [
        "mutex-enter",
        "prepare",
        "configure",
        "current-lock",
        "hpx_profiler",
        "mutex-exit",
        "mutex-enter",
        "configure",
        "current-lock",
        "hpx_profiler_power",
        "mutex-exit",
        "mutex-enter",
        "configure",
        "current-lock",
        "hpx_profiler_power",
        "mutex-exit",
    ]


def test_actual_source_admission_refuses_before_link(linked_pipeline):
    from helia_profiler.engines import EngineType
    from helia_profiler.errors import PlatformError
    from helia_profiler.firmware import build_app
    from helia_profiler.platform.placement import MemoryRegion
    from helia_profiler.results import MemoryConsumer, MemoryPlan, MemoryRegionUsage

    ctx, _, _, _, events = linked_pipeline
    ctx.memory_plan = MemoryPlan(
        EngineType.HELIA_RT,
        (
            MemoryRegionUsage(
                MemoryRegion.MRAM, 4194304, 4130000, (MemoryConsumer("model_flatbuffer", 4130000),)
            ),
        ),
    )
    ctx.declared_memory_plan = ctx.memory_plan
    with pytest.raises(PlatformError, match="application budget of 4128768 B"):
        build_app(ctx)
    assert events == ["mutex-enter", "prepare", "configure", "current-lock", "mutex-exit"]


@pytest.mark.parametrize("role", ["profile", "power"])
@pytest.mark.parametrize(
    ("fragment_role", "fragment"),
    [
        ("flags", "-Wl,@override.rsp"),
        ("flags", "@override.rsp"),
        ("flags", "--via=override.rsp"),
        ("flags", "alternate.ld"),
        ("flags", '"alternate.lds"'),
        ("flags", "alternate.sct"),
        ("libraries", "-Wl,-T,alternate.ld"),
        ("libraries", "--scatter=alternate.sct"),
        ("libraries", '"alternate.ld"'),
        ("libraries", "alternate.ld libhelper.a"),
        ("flags", "-Wl,alternate.ld,-Map,out.map"),
        ("libraries", '"path with spaces/alternate.lds" libhelper.a'),
        ("flags", "-Wl,--start-group,alternate.sct,libhelper.a,--end-group"),
        ("flags", "-Wl,--default-script=alternate.txt"),
        ("flags", "-Wl,-dTalternate.txt"),
        ("flags", "-Wl,-script=alternate.txt"),
        ("flags", "-Wl,--scr=alternate.txt"),
        ("flags", "-Wl,-default-script=alternate.txt"),
        ("flags", "-Wl,--default-scr=alternate.txt"),
    ],
)
def test_opaque_or_additional_script_inputs_never_qualify(
    configured, role, fragment_role, fragment
):
    _, build, _, _, _ = configured
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text())
    data["link"]["commandFragments"].append({"role": fragment_role, "fragment": fragment})
    path.write_text(json.dumps(data))
    result = _verify(configured, role=role)
    assert not result.verified
    assert result.reason is not None


@pytest.mark.parametrize("fragment", ["libnot.ld.a", "path.ld/libhelper.a"])
@pytest.mark.parametrize("role", ["profile", "power"])
def test_library_names_are_not_mistaken_for_script_inputs(configured, fragment, role):
    _, build, _, _, _ = configured
    library = build / fragment
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"!<arch>\n")
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append({"role": "libraries", "fragment": fragment})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(configured, role=role).verified


@pytest.mark.parametrize("role", ["profile", "power"])
@pytest.mark.parametrize("name", ["extra.txt", "extra", "looks_like_archive.a"])
@pytest.mark.parametrize("forwarding", ["libraries", "flags", "-Wl,", "-Xlinker "])
def test_implicit_scripts_are_refused_independent_of_filename(configured, role, name, forwarding):
    _, build, _, _, _ = configured
    build.mkdir(parents=True, exist_ok=True)
    (build / name).write_text("SECTIONS { .extra : { *(.extra) } }", encoding="utf-8")
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fragment = name if forwarding in {"libraries", "flags"} else forwarding + name
    data["link"]["commandFragments"].append(
        {
            "role": forwarding if forwarding in {"libraries", "flags"} else "flags",
            "fragment": fragment,
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    result = _verify(configured, role=role)
    assert not result.verified
    assert "implicit script" in result.reason


@pytest.mark.parametrize("header", [b"\x7fELF", b"!<arch>\n", b"BC\xc0\xde"])
@pytest.mark.parametrize("role", ["profile", "power"])
def test_binary_inputs_need_no_recognized_filename_suffix(configured, header, role):
    _, build, _, _, _ = configured
    name = "binary input.txt"
    (build / name).write_bytes(header)
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append({"role": "libraries", "fragment": f'"{name}"'})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(configured, role=role).verified


@pytest.mark.parametrize("existing", [False, True])
def test_only_pending_declared_binary_products_can_qualify_before_build(configured, existing):
    _, build, _, _, _ = configured
    reply = build / ".cmake/api/v1/reply"
    product = build / "future library.a"
    if existing:
        product.write_text("SECTIONS {}", encoding="utf-8")
    path = reply / "hpx_profiler.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append(
        {
            "role": "libraries",
            "fragment": '"future library.a"',
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not _verify(configured).verified
    model = json.loads((reply / "model.json").read_text(encoding="utf-8"))
    model["configurations"][0]["targets"].append({"name": "future", "jsonFile": "future.json"})
    _json(reply / "model.json", model)
    _json(reply / "future.json", {"type": "STATIC_LIBRARY", "artifacts": [{"path": product.name}]})
    assert _verify(configured).verified is (not existing)


@pytest.mark.parametrize("role", ["profile", "power"])
@pytest.mark.parametrize(
    "search",
    [
        "-L{path}",
        "-Wl,-L,{path}",
        "-L {path}",
        "--library-path={path}",
        "--userlibpath={path}",
        "-Wl,-library-path={path}",
        "-Wl,--library-pa={path}",
        "-library-path {path}",
        "--userlibpa={path}",
    ],
)
def test_explicit_library_search_cannot_hide_implicit_scripts(configured, role, search):
    _, build, _, _, _ = configured
    directory = build / "local"
    directory.mkdir()
    (directory / "libcustom.a").write_text("SECTIONS {}", encoding="utf-8")
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].extend(
        [
            {"role": "libraryPath", "fragment": search.format(path=directory)},
            {"role": "libraries", "fragment": "-lcustom"},
        ]
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    result = _verify(configured, role=role)
    assert not result.verified
    assert "search paths" in result.reason


@pytest.mark.parametrize("fragment", ["-lm", "-l m", "--library=m"])
def test_default_toolchain_library_search_remains_supported(configured, fragment):
    _, build, _, _, _ = configured
    path = build / ".cmake/api/v1/reply/hpx_profiler.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append({"role": "libraries", "fragment": fragment})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(configured).verified


@pytest.mark.parametrize("role", ["profile", "power"])
@pytest.mark.parametrize(
    "fragment",
    [
        "-Wl,--mri-script=alternate.mri",
        "-Wl,-calternate.mri",
        "-Wl,-mri-script=alternate.mri",
        "-Wl,--mri-scr=alternate.mri",
        "-library=:extra.txt",
        "-Wl,-library=:extra.txt",
        "-Wl,--,-extra.txt",
        "-- -extra.txt",
        "-Wl,--unrecognized-input-option=extra.txt",
        "-Wl,--defsym=region_size=0x100000",
        "--config=custom.cfg",
    ],
)
def test_unknown_linker_option_syntax_cannot_qualify(configured, role, fragment):
    _, build, _, _, _ = configured
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append({"role": "flags", "fragment": fragment})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not _verify(configured, role=role).verified


@pytest.mark.parametrize(
    "fragment",
    [
        "-Wl,-u,SystemCoreClock,--entry,Reset_Handler,--wrap=_write_r",
        "--diag_suppress 6236 --map --symbols --cpu=cortex-m55",
        "--target=arm-none-eabi --config=newlib.cfg -mthumb -mcpu=cortex-m55 -mfloat-abi=hard",
        "-Wl,--start-group,-lm,--end-group",
        "-undefined SystemCoreClock -library m -o out.elf",
    ],
)
def test_supported_option_operands_are_not_positional_inputs(configured, fragment):
    _, build, _, _, _ = configured
    path = build / ".cmake/api/v1/reply/hpx_profiler.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"].append({"role": "flags", "fragment": fragment})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(configured).verified


@pytest.mark.parametrize("role", ["profile", "power"])
@pytest.mark.parametrize("option", ["--entry", "-u", "-Map", "--library", "-Wl,-e"])
def test_script_fragment_cannot_complete_a_previous_option(configured, role, option):
    _, build, root, layout, _ = configured
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if option == "-Wl,-e":
        data["link"]["commandFragments"][0]["fragment"] = (
            f'-Wl,--script="{root / layout.script.path}"'
        )
    data["link"]["commandFragments"].insert(0, {"role": "flags", "fragment": option})
    data["link"]["commandFragments"].append({"role": "libraries", "fragment": "extra.ld"})
    (build / "extra.ld").write_text("SECTIONS {}", encoding="utf-8")
    path.write_text(json.dumps(data), encoding="utf-8")
    result = _verify(configured, role=role)
    assert not result.verified
    assert "consumed as another option" in result.reason


@pytest.mark.parametrize("role", ["profile", "power"])
def test_completed_split_operand_before_script_keeps_its_position(configured, role):
    _, build, _, _, _ = configured
    target = "hpx_profiler" if role == "profile" else "hpx_profiler_power"
    path = build / ".cmake/api/v1/reply" / f"{target}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link"]["commandFragments"][:0] = [
        {"role": "flags", "fragment": "--entry"},
        {"role": "flags", "fragment": "entry_symbol.ld"},
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _verify(configured, role=role).verified
