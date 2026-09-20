"""Count CLI registrations straight from the source text of ``cli/*.py``.

This is a deliberately independent second opinion on ``cli.json``: the
extractor imports the package and asks click, this module only parses the
AST. When the two disagree the JSON is wrong, the audit is wrong, or a
command is registered by a form neither side models. Registration reaches
the app two ways in this package, ``@app.command(...)`` as a decorator and
``app.command(...)(fn)`` as a call, so both are recognised.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "src" / "helia_profiler" / "cli"
ROOT_APP_VARS = {"app"}


@dataclass
class SourceCommand:
    path: tuple[str, ...]
    module: str
    function: str
    options: list[str] = field(default_factory=list)
    arguments: list[str] = field(default_factory=list)


@dataclass
class SourceAudit:
    commands: dict[tuple[str, ...], SourceCommand]
    groups: dict[str, str]
    modules: list[str]


def _const_name(call: ast.Call) -> str | None:
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _app_var(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _param_names(func: ast.FunctionDef) -> tuple[list[str], list[str]]:
    """Split the function signature into typer options and arguments.

    Every parameter is declared as ``Annotated[T, typer.Option(...)]`` or
    ``Annotated[T, typer.Argument(...)]``; the declaration decides which.
    """
    options: list[str] = []
    arguments: list[str] = []
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if arg.annotation is None:
            continue
        kind = None
        for node in ast.walk(arg.annotation):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"Option", "Argument"}:
                    kind = node.func.attr
                    break
        if kind == "Option":
            options.append(arg.arg)
        elif kind == "Argument":
            arguments.append(arg.arg)
    return options, arguments


def audit() -> SourceAudit:
    functions: dict[tuple[str, str], ast.FunctionDef] = {}
    registrations: list[tuple[str, str, str, str]] = []  # app var, cmd name, module, func
    mounts: dict[str, str] = {}
    modules: list[str] = []

    for source_path in sorted(CLI_DIR.glob("*.py")):
        module = source_path.name
        modules.append(module)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions[(module, node.name)] = node
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        var = _app_var(dec.func)
                        if var is None:
                            continue
                        if dec.func.attr == "command":
                            name = _const_name(dec) or node.name.strip("_").replace("_", "-")
                            registrations.append((var, name, module, node.name))
                        elif dec.func.attr == "callback":
                            registrations.append((var, "", module, node.name))
            elif isinstance(node, ast.Call):
                inner = node.func
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if inner.func.attr == "command" and node.args:
                        var = _app_var(inner.func)
                        target = node.args[0]
                        if var and isinstance(target, ast.Name):
                            name = _const_name(inner)
                            if name:
                                registrations.append((var, name, module, target.id))
                if isinstance(inner, ast.Attribute) and inner.attr == "add_typer" and node.args:
                    sub = node.args[0]
                    mount = _const_name(node)
                    if isinstance(sub, ast.Name) and mount:
                        mounts[sub.id] = mount

    commands: dict[tuple[str, ...], SourceCommand] = {}
    for var, name, module, func_name in registrations:
        prefix = () if var in ROOT_APP_VARS else (mounts.get(var, var),)
        path = prefix if name == "" else (*prefix, name)
        func = functions.get((module, func_name))
        options, arguments = _param_names(func) if func else ([], [])
        commands[path] = SourceCommand(
            path=path, module=module, function=func_name, options=options, arguments=arguments
        )
    return SourceAudit(commands=commands, groups=dict(mounts), modules=modules)
