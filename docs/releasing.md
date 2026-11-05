# Releasing

The project is one wheel, `quant-agent-os`, at one version ([decision 0018](decisions/0018-one-wheel.md)).
`tests/contract/test_release.py` checks that the plugin and the changelog name it, and the
server's `serverInfo.version` is this version.

## 0.1.0 scope

0.1.0 is for local use (stdio, the Claude Code plugin) and for self-hosted team servers behind
a reverse proxy. A public, multi-tenant endpoint is not in scope until the steps in
[security-review.md](security-review.md#before-a-public-endpoint) are done: a rate limit on
unauthenticated requests (F-1), a plan for every tenant (F-2), and F-1 and F-5 fixed or
accepted in writing.

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

## Cutting a release

1. **Check main.** CI is green on every OS and Python version, and
   `uv run python tests/evaluation/agent_efficiency.py` shows no unintended delta.
2. **Set the version** in `pyproject.toml` and `.claude-plugin/plugin.json`. Then run `uv lock` and
   `uv run pytest tests/contract/test_release.py tests/server/test_client_bundles.py`.
3. **Changelogs.** Rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD`
   (0.1.0's is already dated; correct the date if the tag is later), add a new empty
   `## [Unreleased]` above it, and add the release's section to `docs/tools-changelog.md`.
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
