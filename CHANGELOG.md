# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]

### Changed

- **The transcript probes are calibrated.** fleet-guards 2f1edb1 taught the shared data boundary guard the Claude Code transcript shapes, so the `guards/` pin moved to it and the five transcript probes left `_run_shape_probes_pending` for `_run_shape_probes`; `data_boundary.py --calibration` now reports this repository calibrated. The exported Markdown name stays pending, because no shape separates it from documentation. The `.gitignore` rules for transcripts remain as defence in depth.
- **Enrolled in fleet sync.** The repository now has its `FLEET_SYNC_TOKEN` and is listed in both kits' subscriber lists, so `fleet-sync.yml` is back to the shared template, daily `schedule:` included, and the `guards/` and `style/` pins advance through the verified sync workflow instead of by hand.

## [0.3.0]

The current package and `__version__` declare 0.3.0. No release date is recorded here; the implementation commits do not establish a publication date.

### Added

- Preview-bound permanent session deletion, including sidecars and native index entries, with interrupted-operation recovery and request replay receipts.
- Transactional rename and move, project metadata for resume directories, and optional request identity for retrying a fork. These capabilities were added after the documented 0.1.0 baseline.

## [0.1.0] - 2026-09-27

### Added

- **Initial extraction from task-console.** The conversation chain engine (`convtree.py`, written on an unpublished task-console feature branch for its conversation chain panel) moved here as the `convo_chain` package, behaviour unchanged: byte offset index with an in-process LRU cache, the shape gate, `locate`, `chain`, `node`, `export_md`, `fork` with exclusive create, and the resume command.
- **Its own error types.** `ConvoChainError` (with a stable `.code`) replaces task-console's `maint.Refused`, and `Unavailable` stays a separate class so "not checked" can never be caught as a refusal.
- **The transcript rules, in one place.** `typed_text` and `looks_injected` moved here from task-console's `convos.py`, so the console and this library share one implementation instead of two.
- **A CLI**, `convo-chain` (and `python -m convo_chain`): `chain`, `node`, `export` (with `--out` for a Markdown file) and `fork`, JSON on stdout for every outcome, stable exit codes (`4` with code `internal` when something other than a refusal goes wrong, naming only the exception class). An explicit `--root`, even an empty one, is never replaced by `CONVO_CHAIN_ROOT`.
- Repository scaffolding: the `guards/` and `style/` submodules, `.githooks` forwarders, the pii-guard, dash-guard, tests and fleet-sync workflows, `.dataclass.json` and `.pii-allow`.

### Changed

- **The root is always passed in.** The engine used to fall back to the `TASK_CONSOLE_SESSIONS` environment variable when no root was given. The library now reads no environment variable at all; only the CLI reads `CONVO_CHAIN_ROOT`, and only as the default for `--root`.

### Fixed

- **A user line whose `message` is not an object no longer breaks indexing.** `typed_text` used to call `.get` on it and raise, which took the whole session down; it now answers "not typed text" and the line is indexed as a meta line.
