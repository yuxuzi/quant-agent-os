"""B-26: forking built-in profiles, checking packs, and registering local or git packs."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pytemplate import api
from typer.testing import CliRunner

from quant_app_factory import AppFactory, AppSpec, Identity, PytemplateUvScaffolder, load_spec
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.planner import BUILT_IN_PACKS, PlanningError
from quant_app_factory.infrastructure.pack_files import (
    PackError,
    clone_pack,
    fork_pack,
    pack_digest,
)
from quant_app_factory.infrastructure.settings_file import load_settings


def _factory(**kwargs: Any) -> AppFactory:
    return AppFactory(PytemplateUvScaffolder(), **kwargs)


def _spec(tmp_path: Path, **fields: Any) -> AppSpec:
    base: dict[str, Any] = {"name": "my-app", "description": "d", "output_dir": str(tmp_path)}
    return AppSpec.model_validate(base | fields)


def _fork(tmp_path: Path, source: str = "quant-baseline/quant-lab", **kwargs: Any) -> Path:
    pack, _, profile = source.partition("/")
    return fork_pack(
        BUILT_IN_PACKS[pack], profile, tmp_path / "my-pack", pack_id="my-pack", **kwargs
    )


# Forks


def test_a_fork_keeps_one_profile_and_the_components_it_can_use(tmp_path: Path) -> None:
    pack = _fork(tmp_path)

    manifest = tomllib.loads((pack / "pytemplate-pack.toml").read_text())
    components = sorted(path.stem for path in (pack / "components").glob("*.toml"))
    profiles = sorted(p.name for p in pack.iterdir() if (p / "profile.toml").exists())
    assert manifest["id"] == "my-pack"
    assert profiles == ["quant-lab"]
    assert components == ["agent-tools", "data-csv", "data-database", "data-parquet", "metrics"]
    assert _factory().check_template(pack.as_posix()).ok


def test_a_renamed_profile_still_renders_its_own_files(tmp_path: Path) -> None:
    pack = _fork(tmp_path, profile_id="momentum-bt")
    out = tmp_path / "out"
    out.mkdir()

    _, preview = _factory().preview(
        _spec(out, template=pack.as_posix(), profile="momentum-bt", components=["data-database"])
    )

    assert {
        "my_app/study.py",
        "my_app/backtest.py",
        "my_app/report.py",
        "my_app/database.py",
    } <= set(preview.files)
    assert "my_app/tools.py" not in preview.files


def test_the_agent_tool_profile_forks_and_renames(tmp_path: Path) -> None:
    pack = _fork(tmp_path, "agent-tool/agent-tool", profile_id="signal-tool")

    check = _factory().check_template(pack.as_posix())

    assert check.ok, check
    assert [item.profile for item in check.profiles] == ["signal-tool"]


@pytest.mark.parametrize(
    ("profile", "kwargs", "message"),
    [
        ("options", {}, "offers quant-lab"),
        ("quant-lab", {"profile_id": "Bad_Id"}, "lowercase slug"),
    ],
)
def test_impossible_forks_are_explained(
    tmp_path: Path, profile: str, kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(PackError, match=message):
        fork_pack(BUILT_IN_PACKS["quant-baseline"], profile, tmp_path / "x", pack_id="x", **kwargs)


def test_a_fork_never_overwrites_a_directory(tmp_path: Path) -> None:
    (tmp_path / "my-pack").mkdir()

    with pytest.raises(PackError, match="already exists"):
        _fork(tmp_path)


# Registered names and checks


def test_a_registered_name_resolves_to_its_pack(tmp_path: Path) -> None:
    pack = _fork(tmp_path)
    factory = _factory(templates=lambda: {"my-pack": pack.as_posix()})

    plan = factory.plan(_spec(tmp_path, template="my-pack", profile="quant-lab"))

    assert plan.request.template == pack.as_posix()
    assert [p.id for p in factory.describe_template("my-pack").profiles] == ["quant-lab"]
    with pytest.raises(PlanningError, match="known templates"):
        factory.plan(_spec(tmp_path, template="nope", profile="quant-lab"))


def test_names_stay_names_and_paths_must_be_absolute(tmp_path: Path) -> None:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text('name = "x"\ndescription = "d"\ntemplate = "my-pack"\nprofile = "p"\n')

    assert load_spec(spec_file).template == "my-pack"
    with pytest.raises(ValueError, match="registered template name"):
        _spec(tmp_path, template="packs/mine", profile="p")


def test_every_built_in_template_checks_clean() -> None:
    for name in ("agent-tool", "quant-baseline", "bundled"):
        check = _factory().check_template(name)

        assert check.ok, (name, check)


def test_a_broken_pack_fails_its_check(tmp_path: Path) -> None:
    pack = _fork(tmp_path)
    (pack / "questions.yml").write_text("profile:\n  type: str\n")

    check = _factory().check_template(pack.as_posix())

    assert not check.ok and check.problem


# Git packs


def _git_pack(tmp_path: Path) -> Path:
    pack = _fork(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for command in (
        ["init", "--quiet"],
        ["add", "."],
        ["commit", "--quiet", "-m", "pack"],
        ["tag", "v1"],
    ):
        subprocess.run([*git, *command], cwd=pack, check=True, capture_output=True)
    return pack


def test_a_git_pack_is_fetched_once_at_its_ref_without_history(tmp_path: Path) -> None:
    repository = _git_pack(tmp_path)
    cache = tmp_path / "cache" / "my-pack" / "v1"

    clone_pack(repository.as_posix(), "v1", cache)
    clone_pack("/nowhere", "v1", cache)  # cached: no fetch at all

    assert (cache / "pytemplate-pack.toml").is_file()
    assert not (cache / ".git").exists()
    assert _factory().check_template(cache.as_posix()).ok


def test_a_bad_ref_fails_cleanly(tmp_path: Path) -> None:
    repository = _git_pack(tmp_path)
    cache = tmp_path / "cache" / "my-pack" / "v9"

    with pytest.raises(PackError, match="v9"):
        clone_pack(repository.as_posix(), "v9", cache)
    assert not cache.exists()


# CLI


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home" / "factory.toml"
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(path.parent))
    monkeypatch.setattr("quant_app_factory.bootstrap.git_identity", lambda: Identity())
    return path


def test_cli_fork_checks_and_registers_the_pack(tmp_path: Path, settings_file: Path) -> None:
    runner = CliRunner()
    destination = tmp_path / "packs" / "momentum"

    forked = runner.invoke(
        factory_cli.app,
        ["template", "fork", "quant-baseline/quant-lab", str(destination), "--profile-id", "momo"],
    )
    listed = runner.invoke(factory_cli.app, ["template", "list"])
    checked = runner.invoke(factory_cli.app, ["template", "check", "momentum"])

    assert forked.exit_code == 0, forked.output
    assert "ok momo" in " ".join(forked.output.split())
    assert (
        load_settings(settings_file).templates["momentum"].path == destination.resolve().as_posix()
    )
    assert "momentum 0.1.0 (fork of quant-baseline/quant-lab)" in listed.output
    assert "momentum/momo" in listed.output
    assert checked.exit_code == 0, checked.output


def test_cli_add_registers_local_and_pinned_git_packs(tmp_path: Path, settings_file: Path) -> None:
    runner = CliRunner()
    repository = _git_pack(tmp_path)

    local = runner.invoke(factory_cli.app, ["template", "add", str(repository), "--name", "local"])
    pinned = runner.invoke(
        factory_cli.app,
        ["template", "add", repository.as_posix(), "--name", "remote", "--ref", "v1"],
    )
    unpinned = runner.invoke(factory_cli.app, ["template", "add", "gh:someone/pack", "--name", "x"])

    templates = load_settings(settings_file).templates
    assert local.exit_code == 0, local.output
    assert pinned.exit_code == 0, pinned.output
    assert templates["remote"].ref == "v1"
    assert Path(templates["remote"].path) == settings_file.parent / "packs" / "remote" / "v1"
    assert unpinned.exit_code == 1 and "--ref" in unpinned.output
    assert templates["remote"].digest == pack_digest(Path(templates["remote"].path))
    assert templates["remote"].commit == _head(repository)


def _head(repository: Path) -> str:
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_the_digest_is_the_one_pytemplate_uv_records(tmp_path: Path) -> None:
    repository = _git_pack(tmp_path)

    fetched = clone_pack(repository.as_posix(), "v1", tmp_path / "cache" / "v1")
    with api.open_catalog(repository.as_posix(), "v1") as (_, source, _):
        assert (fetched.commit, fetched.digest) == (source.commit, source.digest)


def test_cli_add_refuses_a_pack_that_does_not_match_its_pins(
    tmp_path: Path, settings_file: Path
) -> None:
    runner = CliRunner()
    repository = _git_pack(tmp_path)
    commit = _head(repository)
    with api.open_catalog(repository.as_posix(), "v1") as (_, source, _):
        digest = source.digest
    add = ["template", "add", repository.as_posix(), "--name", "firm", "--ref", "v1"]

    moved = runner.invoke(factory_cli.app, [*add, "--commit", "0" * 40])
    changed = runner.invoke(factory_cli.app, [*add, "--commit", commit, "--digest", "f" * 64])
    no_ref = runner.invoke(
        factory_cli.app, ["template", "add", str(repository), "--name", "firm", "--commit", commit]
    )

    assert moved.exit_code == 1 and "not the pinned" in moved.output
    assert changed.exit_code == 1 and "digest" in changed.output
    assert no_ref.exit_code == 1 and "--ref" in no_ref.output
    assert "firm" not in load_settings(settings_file).templates
    assert not (settings_file.parent / "packs" / "firm" / "v1").exists()

    pinned = runner.invoke(factory_cli.app, [*add, "--commit", commit, "--digest", str(digest)])
    listed = runner.invoke(factory_cli.app, ["template", "list"])

    assert pinned.exit_code == 0, pinned.output
    entry = load_settings(settings_file).templates["firm"]
    assert (entry.commit, entry.digest) == (commit, digest)
    assert f"commit {commit[:12]}" in listed.output
    assert "quant-apps/risk-monitor" in listed.output and "bundled/python" in listed.output


def test_cli_check_fails_when_a_cached_pack_changed(tmp_path: Path, settings_file: Path) -> None:
    runner = CliRunner()
    repository = _git_pack(tmp_path)
    add = ["template", "add", repository.as_posix(), "--name", "firm", "--ref", "v1"]
    assert runner.invoke(factory_cli.app, add).exit_code == 0
    cached = Path(load_settings(settings_file).templates["firm"].path)

    before = runner.invoke(factory_cli.app, ["template", "check", "firm"])
    (cached / "questions.yml").write_text(
        (cached / "questions.yml").read_text("utf-8") + "# edited\n", "utf-8"
    )
    after = runner.invoke(factory_cli.app, ["template", "check", "firm"])

    assert before.exit_code == 0, before.output
    assert after.exit_code == 1 and "files changed" in " ".join(after.output.split())


def test_a_spec_can_use_a_registered_template(tmp_path: Path, settings_file: Path) -> None:
    runner = CliRunner()
    destination = tmp_path / "packs" / "momentum"
    assert (
        runner.invoke(
            factory_cli.app,
            ["template", "fork", "quant-baseline/quant-lab", str(destination)],
        ).exit_code
        == 0
    )
    spec = tmp_path / "app.toml"
    spec.write_text(
        'name = "momo-app"\ndescription = "d"\noutput_dir = "."\n'
        'template = "momentum"\nprofile = "quant-lab"\n'
    )

    planned = runner.invoke(factory_cli.app, ["plan", str(spec), "--verbose"])

    assert planned.exit_code == 0, planned.output
    assert "momo_app/backtest.py" in planned.output
