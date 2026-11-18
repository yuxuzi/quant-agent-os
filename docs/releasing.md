# Releasing

The platform is one wheel, `quant-agent-os`, at one version ([decision 0018](decisions/0018-one-wheel.md)).
Quant Toolbox is a second distribution, `quant-toolbox`, a workspace member with its own version, released
by the same tag ([decision 0027](decisions/0027-quant-toolbox-package.md)); the platform's extras pin it
exactly, and `tests/contract/test_release.py` holds the pin to the toolbox's version.
`tests/contract/test_release.py` checks that the plugin and the changelog name it, and the
server's `serverInfo.version` is this version.

## Scope

Releases are for local use (stdio, the Claude Code plugin) and for a firm's own server behind its
reverse proxy. There is no multi-tenant service ([decision 0023](decisions/0023-no-hosted-service.md)).
Before a server faces the public internet, do the steps in
[security-review.md](security-review.md#before-a-public-endpoint).

## Where releases go

A `v*` tag always creates a **GitHub release** with the wheel and sdist, and notes taken from the changelog. Publishing to **PyPI** is opt-in: the `publish` job runs only when
the repository variable `PUBLISH_TO_PYPI` is `true`. Releases are GitHub-only: the owner decided
not to publish to PyPI, so the package names are not registered there.

Without PyPI, install from the tag, for example
`uv tool install --python 3.13 "quant-agent-os[analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@vX.Y.Z"`,
or use the Claude Code plugin, which runs from the repository.

## One-time setup for PyPI

Publishing registers the package names on PyPI. The names are provisional (see AGENTS.md),
so confirm them before the first PyPI release.

1. On PyPI, add a [trusted publisher](https://docs.pypi.org/trusted-publishers/) for
   `quant-agent-os`: this repository, workflow `release.yml`, environment `pypi`.
2. In GitHub, create the `pypi` environment, ideally with a required reviewer, and set the
   repository variable `PUBLISH_TO_PYPI` to `true`.

No API token is stored in the repository.

## The toolbox wheel

uv resolves `quant-toolbox` from the same checkout when it installs the platform from a git tag, so the
install above, and generated projects (which depend on the toolbox alone, by `subdirectory`), need nothing
more. A wheel file installed on its own, or a PyPI release, also needs the `quant-toolbox` wheel. Until
`release.yml` builds it (`uv build --all-packages`, expecting two wheels), a tag's release carries only the
platform's wheel; changing the workflow needs the owner's approval.

## Cutting a release

1. **Check main.** CI is green on every OS and Python version, and
   `uv run python tests/evaluation/agent_efficiency.py` shows no unintended delta.
2. **Set the version** in `pyproject.toml` and `.claude-plugin/plugin.json`, and the toolbox's in
   `packages/quant-toolbox/pyproject.toml` with the four pins in the platform's extras. Then run `uv lock` and
   `uv run pytest tests/contract/test_release.py tests/server/test_client_bundles.py`.
3. **Changelogs.** Rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD`
   (0.1.0's is already dated; correct the date if the tag is later), add a new empty
   `## [Unreleased]` above it, do the same in `packages/quant-toolbox/CHANGELOG.md`, and add the release's section to `docs/tools-changelog.md`.
4. **Merge, then tag main.** `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow
   checks that the tag matches the version, runs the tests, builds the wheel and sdist, checks
   them with `twine check --strict`, creates the GitHub release, and publishes to PyPI if
   that is enabled.

## Compatibility promises

- **Tools** follow semver, and each change is in the [tools changelog](tools-changelog.md). For
  now, a tool may change or be removed in any release; decision 0015's deprecation window is
  suspended until the project is mature enough.
- **Before 1.0**, a minor platform release may change Python APIs, configuration, and storage.
  The run store and knowledge store upgrade in place, and an older binary refuses a newer
  store.
- **Skills** state the platform major they were written for, and warn the user when the
  server's `qaos://server/info` reports a different one.
