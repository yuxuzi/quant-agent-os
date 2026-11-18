"""How the toolbox is organized, held by tests: layers, public imports, and optional libraries.

- A subpackage imports only subpackages in earlier layers, and only their public names.
- Outside the standard library, numpy is the only import at module level; pygal, plotly,
  openpyxl, pandas, and empyrical load inside the function that needs them.
- With numpy alone, every module imports, and an optional feature raises `MissingExtraError`
  naming the extra that installs it.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import quant_toolbox
from quant_toolbox.performance import BACKEND_VARIABLE

ROOT = Path(quant_toolbox.__file__).parent
LAYERS = {
    "data": 1,
    "performance": 2,
    "risk": 3,
    "research": 3,
    "report": 4,
    "audit": 4,
    "testing": 5,
}
STDLIB = set(sys.stdlib_module_names) | {"__future__"}
MODULES = sorted(path for path in ROOT.rglob("*.py"))


def _name(path: Path) -> str:
    parts = path.relative_to(ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path, *, top_level: bool) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    nodes = tree.body if top_level else list(ast.walk(tree))
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_seven_subpackages_are_the_ones_documented() -> None:
    found = {path.parent.name for path in ROOT.glob("*/__init__.py")}

    assert found == set(LAYERS) == set(quant_toolbox.SUBPACKAGES)


@pytest.mark.parametrize("path", MODULES, ids=_name)
def test_a_subpackage_imports_only_earlier_layers_and_their_public_names(path: Path) -> None:
    here = _name(path).split(".")
    own = here[1] if len(here) > 1 and here[1] in LAYERS else None
    problems = []
    for module in _imports(path, top_level=False):
        parts = module.split(".")
        if parts[0] != "quant_toolbox" or len(parts) < 2 or parts[1] not in LAYERS:
            continue
        other = parts[1]
        if other == own:
            continue
        if own is None:
            problems.append(f"the package root imports {module}")
        elif LAYERS[other] >= LAYERS[own]:
            problems.append(f"{own} (layer {LAYERS[own]}) imports {module} (layer {LAYERS[other]})")
        elif len(parts) > 2 and parts[2].startswith("_"):
            problems.append(f"{own} imports the private {module}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", MODULES, ids=_name)
def test_numpy_is_the_only_library_imported_at_module_level(path: Path) -> None:
    roots = {module.split(".")[0] for module in _imports(path, top_level=True)}

    assert roots - STDLIB - {"numpy", "quant_toolbox"} == set()


def test_nothing_of_the_platform_is_imported() -> None:
    platform = {"quant_agent_os", "quant_analytics", "quant_research", "quant_app_factory"}
    imported = {
        module.split(".")[0] for path in MODULES for module in _imports(path, top_level=False)
    }

    assert not imported & (platform | {"quant_platform", "pytemplate", "pydantic"})


NUMPY_ONLY = r"""
import importlib, importlib.abc, sys

BLOCKED = {"pygal", "plotly", "openpyxl", "pandas", "empyrical", "scipy"}

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, Block())
import quant_toolbox
loaded = [m for m in sys.modules if m.split(".")[1:2] and m.split(".")[0] == "quant_toolbox"]
assert all(m.split(".")[1].startswith("_") for m in loaded), f"the root imported {loaded}"
for name in sys.argv[1:]:
    importlib.import_module(name)
assert not BLOCKED & {m.split(".")[0] for m in sys.modules}

import datetime as dt
from quant_toolbox import MissingExtraError, features, performance, report
from quant_toolbox.data import excel, frames
from quant_toolbox.report import svg, interactive
from quant_toolbox.report import excel as report_excel

assert performance.backend() == "numpy"
assert performance.sharpe_ratio([0.01, -0.02, 0.03], 252) == performance.sharpe_ratio(
    [0.01, -0.02, 0.03], 252, backend="numpy")
assert not any(features().values())
chart = report.line_chart("x", [dt.date(2024, 1, 1), dt.date(2024, 1, 2)], {"a": [1.0, 2.0]})
document = report.Document("t", blocks=[chart])
for call, extra in (
    (lambda: report.render_html(document), "charts"),
    (lambda: report.render_html(document, charts="interactive"), "interactive"),
    (lambda: excel.read_workbook(__import__("pathlib").Path("x.xlsx")), "excel"),
    (lambda: report_excel.write_workbook(document, __import__("pathlib").Path("x.xlsx")), "excel"),
    (lambda: performance.sharpe_ratio([0.01, -0.02, 0.03], 252, backend="empyrical"), "empyrical"),
):
    try:
        call()
    except MissingExtraError as error:
        assert f"quant-toolbox[{extra}]" in str(error), str(error)
    else:
        raise AssertionError(f"no MissingExtraError for {extra}")
try:
    frames.to_pandas(None)
except MissingExtraError as error:
    assert "quant-toolbox[pandas]" in str(error)
print("ok")
"""


def test_with_numpy_alone_everything_imports_and_extras_are_named() -> None:
    modules = [_name(path) for path in MODULES]

    environment = {key: value for key, value in os.environ.items() if key != BACKEND_VARIABLE}
    done = subprocess.run(
        [sys.executable, "-c", NUMPY_ONLY, *modules],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_importing_performance_loads_no_optional_library() -> None:
    code = (
        "import sys, quant_toolbox.performance, quant_toolbox.risk, quant_toolbox.research; "
        "print(sorted({'pygal','plotly','openpyxl','pandas','empyrical'} & "
        "{m.split('.')[0] for m in sys.modules}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )

    assert done.stdout.strip() == "[]", done.stderr
