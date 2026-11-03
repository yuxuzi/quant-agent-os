# Releasing

Every workspace package is released together at one version: `quant-agent-os` (core),
`quant-agent-os-research`, `quant-agent-os-analytics`, `quant-agent-os-factory`, and
`quant-agent-os-platform`. Each pins its siblings to that exact version, and
`tests/contract/test_release.py` checks this, so an installation never mixes versions and the
server's `serverInfo.version` is the platform version.

## One-time setup

Publishing registers the package names on PyPI. The names are provisional (see AGENTS.md),
so confirm them before the first release.

1. On PyPI, add a [trusted publisher](https://docs.pypi.org/trusted-publishers/) for each of
   the five packages: this repository, workflow `release.yml`, environment `pypi`.
2. In GitHub, create the `pypi` environment, ideally with a required reviewer.

No API token is stored in the repository.

## Cutting a release

1. **Check main.** CI is green on every OS and Python version, and
   `uv run python tests/evaluation/agent_efficiency.py` shows no unintended delta.
2. **Set the version** (for a release after 0.1.0) in all five `pyproject.toml` files, in the
   sibling pins, and in `.claude-plugin/plugin.json`. Then run `uv lock` and
   `uv run pytest tests/contract/test_release.py apps/quant-platform/tests/test_client_bundles.py`.
3. **Changelogs.** Rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD`,
   add a new empty `## [Unreleased]` above it, and add the release's section to
   `docs/tools-changelog.md`.
4. **Merge, then tag main.** `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow
   checks that the tag matches the version, runs the tests, builds all five packages, checks
   them with `twine check --strict`, and publishes them.
5. **Afterwards**, create a GitHub release from the changelog section.

## Compatibility promises

- **Tools** follow semver (decision 0015): a breaking change ships as a new tool name, and the
  old one stays, deprecated, for at least 90 days and one minor platform release.
- **Before 1.0**, a minor platform release may change Python APIs, configuration, and storage.
  The run store and knowledge store upgrade in place, and an older binary refuses a newer
  store.
- **Skills** state the platform major they were written for, and warn the user when the
  server's `qaos://server/info` reports a different one.
