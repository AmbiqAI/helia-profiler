from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import zipfile
from importlib.util import resolve_name
from pathlib import Path

import pytest

MAX_SOURCE_LINES = 1000


def test_source_modules_stay_below_size_ceiling() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    oversized = {
        path.relative_to(repo_root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in (repo_root / "src" / "helia_profiler").rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_SOURCE_LINES
    }

    assert not oversized, (
        f"Source modules exceed {MAX_SOURCE_LINES} lines; extract a cohesive responsibility: "
        f"{oversized}"
    )


_ENGINE_ADAPTERS = frozenset({"litert", "executorch", "helia_rt", "helia_aot"})
_ENGINE_SHARED = frozenset({"base", "cmsis_nn", "ethos_u", "semver"})
_ENGINE_REGISTRY = "__init__"


def _engine_owner(path: Path) -> str:
    owner = path.parts[0] if len(path.parts) > 1 else path.stem
    assert owner in _ENGINE_ADAPTERS | _ENGINE_SHARED | {_ENGINE_REGISTRY}, (
        f"Unclassified engine module: {path}; classify its adapter or shared responsibility"
    )
    return owner


def _engine_import_offenders(path: Path, source: str) -> list[str]:
    owner = _engine_owner(path)
    package = ".".join(("helia_profiler", "engines", *path.parts[:-1]))
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for target in targets:
            parts = target.split(".")
            if parts[:2] != ["helia_profiler", "engines"] or len(parts) < 3:
                continue
            other = parts[2]
            if other in _ENGINE_ADAPTERS and owner not in {other, _ENGINE_REGISTRY}:
                offenders.append(f"{path.as_posix()}:{node.lineno}: {target}")
    return offenders


def test_no_engine_adapter_imports_out_of_another_engines_package() -> None:
    """Adapters own their internals; shared helpers cannot depend on adapters.

    Only the registry dispatches across adapters. Classify every Python module,
    including flat-file adapters, so a new owner requires an explicit decision.
    """
    engines_root = Path(__file__).resolve().parent.parent / "src/helia_profiler/engines"
    offenders = [
        offender
        for path in sorted(engines_root.rglob("*.py"))
        for offender in _engine_import_offenders(
            path.relative_to(engines_root), path.read_text(encoding="utf-8")
        )
    ]
    assert not offenders, "Move cross-adapter logic to a shared engine helper:\n  " + "\n  ".join(
        offenders
    )


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("litert.py", "from .helia_rt import HeliaRTAdapter"),
        ("executorch.py", "from . import litert as adapter"),
        ("helia_rt/adapter.py", "from ..executorch import ExecuTorchAdapter"),
        ("helia_aot/__init__.py", "from .. import helia_rt"),
        ("helia_rt/nested/helper.py", "from ...helia_aot.compile import compile_model"),
        ("litert.py", "import helia_profiler.engines.executorch as adapter"),
        ("helia_rt/adapter.py", "from helia_profiler.engines import helia_aot"),
        ("executorch.py", "from helia_profiler.engines.helia_rt import (\n    HeliaRTAdapter,\n)"),
        ("litert.py", "def lazy():\n    from . import executorch"),
        ("helia_rt/adapter.py", "if TYPE_CHECKING:\n    from .. import litert"),
        ("cmsis_nn.py", "from .helia_aot import HeliaAOTAdapter"),
    ],
)
def test_engine_import_guard_rejects_cross_adapter_imports(path: str, source: str) -> None:
    assert _engine_import_offenders(Path(path), source)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("litert.py", "from .cmsis_nn import ARM_CMSIS_NN_MODULE"),
        ("executorch.py", "from . import EngineType"),
        ("helia_rt/adapter.py", "from .artifacts import resolve"),
        ("helia_aot/__init__.py", "from .adapter import HeliaAOTAdapter"),
        ("helia_rt/nested/helper.py", "from .. import artifacts"),
        ("helia_rt/adapter.py", "from helia_profiler.engines.helia_rt import artifacts"),
        ("helia_aot/compile.py", "from helia_aot import AotConverter"),
        ("base.py", "from . import EngineType"),
        ("__init__.py", "from .litert import LiteRtAdapter"),
        ("__init__.py", "from .helia_rt import HeliaRTAdapter"),
        ("litert.py", "# from . import executorch\nmessage = 'from .helia_rt import x'"),
        ("helia_rt/adapter.py", "from ...modelcost import analyze_model"),
    ],
)
def test_engine_import_guard_allows_owned_and_shared_imports(path: str, source: str) -> None:
    assert _engine_import_offenders(Path(path), source) == []


@pytest.mark.parametrize("path", ["new_engine.py", "new_engine/adapter.py", "new_helper.py"])
def test_engine_import_guard_requires_classification(path: str) -> None:
    with pytest.raises(AssertionError, match="Unclassified engine module"):
        _engine_import_offenders(Path(path), "")


# Receivers whose ``.open()`` is not ``Path.open``: archive modules and the
# J-Link / Joulescope / serial device handles.
_NON_FILE_OPENERS = frozenset({"tarfile", "zipfile", "jlink", "drv", "dtr_holder"})
# ``importlib.metadata.Distribution.read_text`` takes a metadata file name, has
# no encoding parameter, and reads UTF-8 itself.
_NON_FILE_READERS = frozenset({"distribution"})


def _is_text_mode(mode: ast.expr | None) -> bool:
    return mode is None or not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def _text_io_without_encoding(call: ast.Call) -> bool:
    if any(keyword.arg == "encoding" for keyword in call.keywords):
        return False
    func = call.func
    mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if isinstance(func, ast.Name) and func.id == "open":
        return _is_text_mode(call.args[1] if len(call.args) > 1 else mode)
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "read_text":
        receiver = func.value
        return not (isinstance(receiver, ast.Name) and receiver.id in _NON_FILE_READERS)
    if func.attr == "write_text":
        return True
    if func.attr != "open":
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name) and receiver.id in _NON_FILE_OPENERS:
        return False
    return _is_text_mode(call.args[0] if call.args else mode)


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("p.read_text()", True),
        ('open(p, "w", newline="")', True),
        ("p.open()", True),
        ('p.open("w")', True),
        ('p.open(mode="w")', True),
        ('open(p, "rb")', False),
        ('p.open("rb")', False),
        ('p.open(mode="wb")', False),
        ('p.write_text(s, encoding="utf-8")', False),
        ('tarfile.open(p, mode="r:gz")', False),
        ("jlink.open()", False),
        ('distribution.read_text("direct_url.json")', False),
        ("path.read_text()", True),
        ("distribution.path.read_text()", True),
        ("distribution.write_text(s)", True),
    ],
)
def test_encoding_guard_classifies_calls(source: str, flagged: bool) -> None:
    call = ast.parse(source, mode="eval").body
    assert isinstance(call, ast.Call)
    assert _text_io_without_encoding(call) is flagged


def test_text_file_io_names_its_encoding() -> None:
    """Windows' locale default (cp1252) cannot round-trip what hpx writes."""
    repo_root = Path(__file__).resolve().parent.parent
    offenders = [
        f"{path.relative_to(repo_root).as_posix()}:{node.lineno}"
        for root in (repo_root / "src" / "helia_profiler", repo_root / "tools")
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _text_io_without_encoding(node)
    ]
    assert not offenders, 'text file I/O needs encoding="utf-8":\n  ' + "\n  ".join(offenders)


@pytest.mark.host_integration
def test_wheel_contains_only_canonical_evaluation_modules(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    # Build from a staged copy, never from the real checkout: with
    # cwd=repo_root, setuptools rewrites build/lib/** inside the working tree
    # on every run — gitignored, so invisible, and a stale copy of the package
    # can shadow imports (issue #151).  The version is static in pyproject.toml
    # (no SCM-derived versioning), so no git metadata needs staging; the build
    # reads pyproject.toml, the readme, the top-level license files, and src/
    # (which holds the third pyproject-listed license, the vendored SEGGER one).
    staged = tmp_path / "staged"
    staged.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(repo_root / name, staged / name)
    shutil.copytree(
        repo_root / "src",
        staged / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    wheel_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=staged,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "helia_profiler/evaluation/validity.py" in names
    assert "helia_profiler/evaluation/comparability.py" in names
    assert "helia_profiler/evaluation/comparison_profile.py" in names
    assert "helia_profiler/evaluation/compare.py" in names
    assert "helia_profiler/modelcost/model_analysis.py" in names
    assert "helia_profiler/results/models.py" in names
    assert "helia_profiler/results/artifacts.py" in names
    assert "helia_profiler/results/manifest.py" in names
    assert "helia_profiler/deps/compatibility.py" in names
    # #229 (lens on PR2): module moves must not resurrect old paths in the
    # wheel — pin FULL set equality between the staged source tree and the
    # wheel's python modules, so a stale or duplicated module ships loudly.
    wheel_modules = {n for n in names if n.startswith("helia_profiler/") and n.endswith(".py")}
    source_modules = {
        f"helia_profiler/{p.relative_to(staged / 'src' / 'helia_profiler').as_posix()}"
        for p in (staged / "src" / "helia_profiler").rglob("*.py")
    }
    assert wheel_modules == source_modules, (
        f"wheel/source module drift: only-in-wheel={sorted(wheel_modules - source_modules)} "
        f"only-in-source={sorted(source_modules - wheel_modules)}"
    )
    assert "helia_profiler/data/compatibility-baseline-v1.json" in names
    assert "helia_profiler/data/run_summary.schema.v1.json" in names
    assert "helia_profiler/data/run_metadata.schema.v1.json" in names
    assert "helia_profiler/data/profile_results.schema.v1.json" in names
    assert "helia_profiler/data/session_intent.schema.v1.json" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.c" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.h" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT_ConfDefaults.h" in names

    installed = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from helia_profiler.deps.compatibility import load_compatibility_baseline; "
                "baseline = load_compatibility_baseline(); "
                "print(baseline.neuralspotx_version); "
                "print(baseline.project('nsx-ambiq-sdk').ref); "
                "print(baseline.fingerprint)"
            ),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(installed)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.splitlines() == [
        "0.8.1",
        "aefce2ca858795e783c76726ebe7d14d9d4bde7c",
        "be0055f2e94024a186387e3897646530981455ba79517b3aebfb4747288e9f2c",
    ]


_SRC = Path(__file__).resolve().parent.parent / "src" / "helia_profiler"


def _hpx_import_targets(path: Path, *, module_level_only: bool) -> list[str]:
    """Intra-hpx import targets in *path*, one entry per imported name.

    Relative imports keep their dots (``"..config"``); absolute ones their
    module path — and ``from helia_profiler import config`` reports
    ``"helia_profiler.config"``, so package-attribute imports cannot slip
    past a module-name filter. ``module_level_only=True`` restricts to
    plain top-level statements, excluding only the *body* of
    ``if TYPE_CHECKING:`` (its ``else:`` branch runs at import time and is
    included); ``False`` walks everything, lazy and guarded alike.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if module_level_only:
        nodes: list[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
                nodes.extend(node.orelse)
            else:
                nodes.append(node)
    else:
        nodes = list(ast.walk(tree))

    targets: list[str] = []
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            base = "." * (node.level or 0) + (node.module or "")
            if node.level or (node.module or "").split(".")[0] == "helia_profiler":
                # `from . import x` has an empty module: join without adding
                # a dot, or a level-1 sibling import would encode as a
                # level-2 parent target (#235).
                joiner = "" if base.endswith(".") else "."
                targets.extend(f"{base}{joiner}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.extend(
                alias.name for alias in node.names if alias.name.split(".")[0] == "helia_profiler"
            )
    return targets


def _names_module(target: str, module: str) -> bool:
    return module in target.lstrip(".").split(".")


def test_vocab_is_a_leaf() -> None:
    """vocab.py is the bottom layer: any hpx import here — lazy, guarded,
    or otherwise — rebuilds the very cycles it exists to break (#229 D2)."""
    assert _hpx_import_targets(_SRC / "vocab.py", module_level_only=False) == []


def test_identity_is_a_leaf() -> None:
    """identity.py is shared vocabulary that results, config and RunIdentity
    build on, so it imports nothing from hpx."""
    assert _hpx_import_targets(_SRC / "identity.py", module_level_only=False) == []


def test_engines_package_init_stays_stdlib_light() -> None:
    """``from ..engines import EngineType`` is cycle-safe for wire/, results/
    and config/ ONLY while engines/__init__ imports nothing from hpx at
    module level (adapters load lazily). This pin is why EngineType did not
    need an engines/types.py split (#229 D2 refinement)."""
    hits = _hpx_import_targets(_SRC / "engines" / "__init__.py", module_level_only=True)
    assert hits == [], f"engines/__init__ gained module-level hpx imports: {hits}"


def test_platform_never_imports_the_config_layer() -> None:
    """The silicon-info package must not know the config resolver exists —
    platform owns the toolchain-name map (#229 D2). Lazy and guarded
    imports count too."""
    offenders = {
        path.name: hits
        for path in sorted((_SRC / "platform").glob("*.py"))
        if (
            hits := [
                target
                for target in _hpx_import_targets(path, module_level_only=False)
                if _names_module(target, "config")
            ]
        )
    }
    assert not offenders, f"platform/ imports the config layer: {offenders}"


def _hpx_target_head(target: str, package: str) -> str:
    """Resolve a target to the top-level hpx module it lands in.

    From a file in ``helia_profiler.<package>``: ``".sibling"`` is the
    package itself, ``"..other.name"`` is ``other``, and an absolute target
    contributes its second segment."""
    if target.startswith(".."):
        return target.lstrip(".").split(".")[0]
    if target.startswith("."):
        return package
    return target.split(".")[1] if "." in target else target


def _closure_offenders(package: str, allowed: set[str]) -> dict[str, list[str]]:
    """Per-file hpx-import targets outside *allowed*, over the WHOLE package
    tree (rglob: a future subpackage must not escape the wall)."""
    offenders: dict[str, list[str]] = {}
    for path in sorted((_SRC / package).rglob("*.py")):
        hits = [
            target
            for target in _hpx_import_targets(path, module_level_only=False)
            if _hpx_target_head(target, package) not in allowed
        ]
        if hits:
            offenders[path.name] = hits
    return offenders


def test_platform_closure_stays_extractable() -> None:
    """#229 D4: platform/ is the silicon-info extraction seam — its modules
    may import only siblings and the ``errors`` leaf. Anything else is a
    new extraction blocker to cut deliberately, not accrete silently."""
    offenders = _closure_offenders("platform", {"platform", "errors"})
    assert not offenders, f"platform/ gained extraction blockers: {offenders}"


def test_modelcost_closure_is_sibling_only() -> None:
    """#229 D4: the model-cost core must stay free of hpx imports — its
    optional readers (litert, helia-aot) are guarded third-party deps, and
    every hpx-side concern (engine dispatch, artifacts, reporting) lives
    outside. This is the wall a future shared package ships behind."""
    offenders = _closure_offenders("modelcost", {"modelcost"})
    assert not offenders, f"modelcost/ gained hpx imports: {offenders}"


def _run_contract_import_offenders(source: str) -> list[str]:
    tree = ast.parse(source)
    type_only = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
        for statement in node.body
        for child in ast.walk(statement)
    }
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, "helia_profiler")
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for target in targets:
            parts = target.split(".")
            if len(parts) < 2 or parts[0] != "helia_profiler":
                continue
            if parts[1] in {"compiled_build", "run_record"} or (
                id(node) not in type_only
                and parts[1] in {"fixture_capture", "_fixture_build", "target", "capture"}
            ):
                offenders.append(f"{node.lineno}: {target}")
    return offenders


def test_shared_run_rules_do_not_depend_on_their_callers_or_device_orchestration():
    path = Path(__file__).resolve().parent.parent / "src/helia_profiler/_run_contracts.py"
    assert not _run_contract_import_offenders(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "source",
    [
        "from .compiled_build import CompiledBuild",
        "from . import run_record",
        "if TYPE_CHECKING:\n    from .compiled_build import CompiledBuild",
        "def lazy():\n    from .fixture_capture import capture_fixture",
        "import helia_profiler.target.probe",
        "from ._fixture_build import build_fixed_fixture",
    ],
)
def test_run_contract_guard_rejects_reverse_and_device_dependencies(source):
    assert _run_contract_import_offenders(source)


@pytest.mark.parametrize(
    "source",
    [
        "from .fixture_power import FixtureRailPowerRequest",
        "from .identity import EnergyFacts",
        "if TYPE_CHECKING:\n    from .fixture_capture import FixtureCaptureResult",
        "if TYPE_CHECKING:\n    from ._fixture_build import FixtureBuild",
    ],
)
def test_run_contract_guard_allows_values_and_type_only_references(source):
    assert _run_contract_import_offenders(source) == []


def _fixture_io_import_offenders(source: str, *, consumer: bool = False) -> list[str]:
    policy_names = {
        "FixtureDType",
        "FixtureCapability",
        "FIXTURE_CAPABILITIES",
        "FIXTURE_READBACK_BUDGET",
        "_io_capabilities",
    }
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, "helia_profiler")
            targets = [f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        for target in targets:
            parts = target.split(".")
            if len(parts) < 2 or parts[0] != "helia_profiler":
                continue
            forbidden = (
                parts[1] == "_fixture_build" and parts[-1] in policy_names
                if consumer
                else parts[1]
                in {
                    "_fixture_build",
                    "_fixture_record",
                    "_fixture_facts",
                    "compiled_build",
                    "run_record",
                    "fixture_capture",
                    "pipeline",
                    "target",
                    "capture",
                }
            )
            if forbidden:
                offenders.append(f"{node.lineno}: {target}")
    return offenders


def test_fixture_io_policy_sits_below_its_consumers():
    root = Path(__file__).resolve().parent.parent / "src/helia_profiler"
    assert not _fixture_io_import_offenders((root / "_fixture_io.py").read_text(encoding="utf-8"))
    for name in ("_fixture_record.py", "_fixture_facts.py"):
        assert not _fixture_io_import_offenders(
            (root / name).read_text(encoding="utf-8"), consumer=True
        )


@pytest.mark.parametrize(
    ("source", "consumer", "bad"),
    [
        ("def lazy():\n    from ._fixture_build import FixtureDType", False, True),
        ("from . import _fixture_record", False, True),
        ("import helia_profiler.fixture_capture", False, True),
        ("from .fixture_analysis import FixtureTensor", False, False),
        ("from .engines import EngineType", False, False),
        ("from ._fixture_build import FIXTURE_CAPABILITIES", True, True),
        ("from helia_profiler._fixture_build import _io_capabilities", True, True),
        ("from ._fixture_build import FixtureBuild", True, False),
        ("from ._fixture_io import FixtureCapability", True, False),
    ],
)
def test_fixture_io_guard_covers_reverse_edges_and_allowed_values(source, consumer, bad):
    assert bool(_fixture_io_import_offenders(source, consumer=consumer)) is bad
