# Changelog

Quant Toolbox's own changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the package uses [Semantic Versioning](https://semver.org/): while it is 0.x, a minor release may change its
public API, always with an entry here and a rewritten `tests/api.lock.json`.

## [Unreleased]

### Changed

- **Quant Toolbox is its own distribution, `quant-toolbox`**, in `packages/quant-toolbox` of the Quant-Agent-OS
  repository, with its own version, changelog, and tests (decision 0027). It needs only numpy; its extras are
  `charts` (pygal), `interactive` (plotly), `excel` (openpyxl), `pandas`, `empyrical`, and `all`. A missing
  optional library's error names the quant-toolbox extra that brings it.

## [0.5.0] - 2026-11-17

First release, inside the `quant-agent-os` 0.5.0 wheel. See that release's notes.
