# 0024: A firm registers its own functions in a few lines

- Status: accepted; amends [0020](0020-a-firms-own-server.md) without reversing it
- Date: 2026-11-08

## Context

Decision 0020 lets a firm serve its own tools, but each one costs too much. The example tool wraps a 10-line
calculation in about 60 lines: input and output models, a dataset lookup, provenance, and evidence. A firm with
twenty functions will not write that twenty times. Most of the value a firm gets from the platform comes from
serving code it already has, so the cost of registering a function decides whether it does.

## Decision

- **`FirmApp.expose` turns an existing, typed function into a tool.** It reads the input schema from the type
  hints, and the description and parameter descriptions from the docstring. A Pydantic model, a dataclass, or a
  plain value can come back, and NumPy and pandas values are converted.
- **Data travels by reference.** A parameter marked `Series`, `Frame`, or `Prices` takes a dataset reference.
  The platform reads the dataset through the store it serves, so the access guard applies, and passes the
  function plain values, NumPy arrays, or pandas objects, whichever its hints ask for. Such a tool declares
  `read_local_data`.
- **Every result carries provenance.** The fingerprints of the data read, the code's version, the convention the
  firm states, and `computed` evidence are added without the firm writing them.
- **`serve_apps` serves one or more apps** beside the platform's toolsets, taking its policy and access file from
  the usual settings. `compose(apps=...)` does the same for a firm that builds its own catalog.
- **`check_app` tests an app in the firm's own CI.** It runs `check_tool` on every tool with an example call,
  and can keep a schema lock that fails when a tool's schemas change without a big enough version bump.

What 0020 decided still holds:
- Registration stays explicit, in a file the firm owns and pins in its image. There is no manifest discovery, no
  plugin, and no `--tools-module` option.
- Every registered tool runs through the same executor, with permissions, per-caller access, limits, and audit.
- A served name still cannot be replaced. An app may prefix its tools' names with its own.

## Alternatives considered

- **Keep hand-written models only.** This is exact, but too slow for the volume of code a firm has. A model can
  still be given: a function that takes one Pydantic model and returns one works unchanged through `@tool`.
- **Discover functions from a module or a manifest.** That is convenient, but it would choose what runs from a
  setting, which decision 0020 rejects.
- **Infer permissions from the code.** The platform cannot tell that a function writes files or calls out. The
  firm declares permissions, and the guide says so plainly.

## Consequences

- A firm registers a typed function in about three lines. A function without type hints needs them added first.
- Long calculations still run in a thread with a timeout, not in a process that can be killed, and outputs over
  the limit are refused, not streamed.
- Two series from different datasets reach the function as stored, without being aligned by date. The function
  aligns them when it needs to.
- Running an existing script or executable, and calling an internal service, are separate adapters, and not yet
  built. A script needs `execute_code`, which needs an approval for every call by default.
