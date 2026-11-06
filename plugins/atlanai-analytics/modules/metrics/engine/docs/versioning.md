# Versioning

This page says what counts as the agent's public interface, what a release may change, and how
you hear about it.

## Releases

A release is a git tag `vX.Y.Z` with a GitHub Release. The release notes are the changelog:
there is no separate changelog file. Nothing is published to a package index; you install a
release from a clone of the repository at its tag (see the
[getting-started guide](getting-started.md#1-install)). The core and the plug packages in
`packages/` are released together, with one version number.

Version numbers follow [semantic versioning](https://semver.org/).

## The public interface

These are what a release promises about, and what the release notes report on:

| Surface | Where it is defined |
|---|---|
| `run.yml`, the file that describes a run (`version: 1`) | `src/metrics_agent/data/schemas/run_config.schema.json` |
| the command line: `metrics-agent run`, `stage`, `stages`, `setup`, `plugs`, and each stage's options | `metrics-agent --help`, [`PIPELINE.md`](../PIPELINE.md) |
| the entry-point groups a package registers in: `metrics_agent.plugs`, `metrics_agent.readers`, `metrics_agent.stages`, `metrics_agent.artifacts` | [`design/writing-a-plug.md`](design/writing-a-plug.md) |
| the socket contracts every plug follows | [`design/sockets.md`](design/sockets.md), `src/metrics_agent/sockets.py` |
| the common formats and the artifacts a run writes, including the prompt bundles at each gate | `src/metrics_agent/data/schemas/` |
| the conformance kit a plug runs in its own tests | `metrics_agent.testing.conformance` |
| the environment variables the agent and its plugs read | `metrics-agent plugs` |

Anything else, such as a module under `metrics_agent.lib`, the layout of a run directory beyond
the artifacts above, or the wording of a message, may change in any release.

## Before 1.0

The agent is in **0.x**. While it is:

- a **minor** release (`0.Y.0`) may break any part of the public interface. Every break is
  listed under **Breaking changes** at the top of the release notes, with what to change on
  your side;
- a **patch** release (`0.Y.Z`) never breaks the public interface: it fixes bugs and documents.

A pull request that breaks the public interface says so in its title (a `!` after the type, as
in `feat!: …`) and in a `BREAKING CHANGE:` footer, which is how it reaches the release notes
(see [`CONTRIBUTING.md`](../CONTRIBUTING.md)).

## From 1.0

From 1.0 a break to the public interface needs a major release (`X.0.0`). A minor release adds
to it without breaking it, and a patch release only fixes. A surface that is to be removed is
deprecated first, in a minor release that says so in its notes, and removed in the next major
one. From 1.0, CI also compares the public schemas and socket signatures with the last release
and fails on a break nobody announced.

## Plugs outside this repository

A plug package declares the range of `metrics-agent` versions it works with in its own
`dependencies`, and runs the conformance kit in its tests. Before 1.0, pin it to one minor
release (for example `metrics-agent>=0.3,<0.4`) and read the release notes before moving it.
