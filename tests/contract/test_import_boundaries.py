"""Package boundaries (decision 0003): Core imports no product; products stay independent."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {
    "quant_agent_os": ROOT / "src" / "quant_agent_os",
    "quant_research": ROOT / "packages" / "quant-research" / "src" / "quant_research",
    "quant_app_factory": ROOT / "packages" / "quant-app-factory" / "src" / "quant_app_factory",
}
FORBIDDEN = {
    "quant_agent_os": {"quant_research", "quant_app_factory", "pytemplate"},
    "quant_research": {"quant_app_factory", "pytemplate"},
    "quant_app_factory": {"quant_research", "pytemplate"},
}


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
