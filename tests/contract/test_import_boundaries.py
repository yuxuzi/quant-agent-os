"""Package boundaries (decision 0003): Core imports no product; products stay independent.

The platform server (`quant_platform`) is the composition root: it imports every product, and
nothing imports it.

pytemplate-uv is a third-party library of the App Factory only, and only its scaffolder adapter
imports it, so another `ScaffoldProvider` can replace it.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {
    "quant_agent_os": ROOT / "src" / "quant_agent_os",
    "quant_analytics": ROOT / "src" / "quant_analytics",
    "quant_research": ROOT / "src" / "quant_research",
    "quant_app_factory": ROOT / "src" / "quant_app_factory",
    "quant_platform": ROOT / "src" / "quant_platform",
}
# quant_platform is the composition root: it may import every product, and nothing imports it.
PRODUCTS = {"quant_analytics", "quant_research", "quant_app_factory"}
FORBIDDEN = {
    "quant_agent_os": PRODUCTS | {"quant_platform", "pytemplate"},
    "quant_analytics": PRODUCTS - {"quant_analytics"} | {"quant_platform", "pytemplate"},
    "quant_research": PRODUCTS - {"quant_research"} | {"quant_platform", "pytemplate"},
    "quant_app_factory": PRODUCTS - {"quant_app_factory"} | {"quant_platform"},
    "quant_platform": {"pytemplate"},
}
PYTEMPLATE_ADAPTER = PACKAGES["quant_app_factory"] / "infrastructure" / "pytemplate_uv.py"


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("package", sorted(PACKAGES))
def test_package_imports_respect_ownership_boundaries(package: str) -> None:
    violations = [
        f"{path.relative_to(ROOT)} imports {name}"
        for path in sorted(PACKAGES[package].rglob("*.py"))
        for name in sorted(_imported_roots(path) & FORBIDDEN[package])
    ]

    assert not violations, "\n".join(violations)


def test_only_the_scaffolder_adapter_imports_pytemplate() -> None:
    importers = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(PACKAGES["quant_app_factory"].rglob("*.py"))
        if "pytemplate" in _imported_roots(path)
    ]

    assert importers == [PYTEMPLATE_ADAPTER.relative_to(ROOT).as_posix()]


def test_the_adapter_uses_only_pytemplates_public_api() -> None:
    tree = ast.parse(PYTEMPLATE_ADAPTER.read_text("utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pytemplate")
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("pytemplate")
    }
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "pytemplate"
        for alias in node.names
    }

    assert modules == {"pytemplate"}
    assert imported == {"api"}
