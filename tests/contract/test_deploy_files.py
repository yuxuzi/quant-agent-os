"""The team-server image must see what `uv sync --locked` needs, or its build fails."""

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from quant_platform.cli import app

ROOT = Path(__file__).resolve().parents[2]
NOT_QAOS = {"backup", "restore"}  # they stream the volumes, and run no platform code
# Every image recipe in the repository: the platform's, the firm's, and the examples' and funds'.
DOCKERFILES = sorted(
    {*(ROOT / "deploy").glob("Dockerfile*"), *(ROOT / "examples").glob("**/Dockerfile*")}
)
# Bare pip, as in `python -m pip install`; `uv pip install` is the allowed form.
PIP_INSTALL = re.compile(r"(?<!uv )\bpip3?\s+install\b")


def _stages(text: str) -> list[str]:
    """Return a Dockerfile's stages, each from its FROM line to the next."""
    return re.split(r"^FROM ", text, flags=re.MULTILINE)[1:]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda item: item.relative_to(ROOT).as_posix())
def test_no_image_step_hides_a_failed_install(path: Path) -> None:
    text = path.read_text("utf-8")

    assert "|| true" not in text, f"{path} hides a failed step"
    assert not PIP_INSTALL.search(text), f"{path} installs with pip; use uv"


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda item: item.relative_to(ROOT).as_posix())
def test_a_uv_install_runs_where_uv_is_and_the_final_image_keeps_no_uv(path: Path) -> None:
    stages = _stages(path.read_text("utf-8"))

    for stage in stages:
        if "uv pip install" in stage:
            assert "/usr/local/bin/uv" in stage, f"{path} runs uv in a stage without it"
    assert "/usr/local/bin/uv" not in stages[-1], f"{path} keeps uv in its final image"


def test_the_firm_recipe_installs_its_requirements_with_uv_and_names_no_default_fallback() -> None:
    recipe = (ROOT / "deploy" / "Dockerfile.firm").read_text("utf-8")

    install = re.compile(
        r"^RUN uv pip install .*--python /opt/venv/bin/python "
        r".*--requirement /tmp/firm-requirements\.txt$",
        re.MULTILINE,
    )
    assert install.search(recipe)
    assert "COPY --from=firm-packages /opt/venv /opt/venv" in recipe
    assert "|| true" not in recipe and not PIP_INSTALL.search(recipe)


def test_the_dockerfile_copies_and_dockerignore_admits_the_project() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    ignore = (ROOT / ".dockerignore").read_text("utf-8").splitlines()

    for line in ("COPY pyproject.toml uv.lock ./", "COPY README.md LICENSE ./", "COPY src src"):
        assert line in dockerfile
    assert {"!pyproject.toml", "!uv.lock", "!README.md", "!LICENSE", "!src/"} <= set(ignore)


@pytest.mark.parametrize("name", ["compose.yaml", "compose.postgres.yaml"])
def test_every_compose_service_runs_a_real_qaos_command(name: str) -> None:
    services = yaml.safe_load((ROOT / "deploy" / name).read_text("utf-8"))["services"]
    commands = {
        (*service.get("entrypoint", ["qaos"]), *service.get("command", ()))
        for name, service in services.items()
        if name not in NOT_QAOS
        and ("build" in service or ("image" in service and "QAOS_IMAGE" in service["image"]))
    }

    assert commands
    for command in commands:
        assert command[0] == "qaos", command
        words = [word for word in command[1:] if not word.startswith("-")][:2]
        assert CliRunner().invoke(app, [*words[:1], "--help"]).exit_code == 0, command
