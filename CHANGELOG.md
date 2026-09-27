# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [0.1.0] - 2026-09-27

### Added

- **Initial extraction from task-console.** The conversation chain engine (`convtree.py` on task-console's `feat/convo-chain-panel` branch at f3ca433) moved here as the `convo_chain` package, behaviour unchanged: byte offset index with an in-process LRU cache, the shape gate, `locate`, `chain`, `node`, `export_md`, `fork` with exclusive create, and the resume command.
- **Its own error types.** `ConvoChainError` (with a stable `.code`) replaces task-console's `maint.Refused`, and `Unavailable` stays a separate class so "not checked" can never be caught as a refusal.
- **The transcript rules, in one place.** `typed_text` and `looks_injected` moved here from task-console's `convos.py`, so the console and this library share one implementation instead of two.
- **A CLI**, `convo-chain` (and `python -m convo_chain`): `chain`, `node`, `export` (with `--out` for a Markdown file) and `fork`, JSON on stdout, stable exit codes.
- Repository scaffolding: the `guards/` and `style/` submodules, `.githooks` forwarders, the pii-guard, dash-guard, tests and fleet-sync workflows, `.dataclass.json` and `.pii-allow`.

### Changed

- **The root is always passed in.** The engine used to fall back to the `TASK_CONSOLE_SESSIONS` environment variable when no root was given. The library now reads no environment variable at all; only the CLI reads `CONVO_CHAIN_ROOT`, and only as the default for `--root`.

### Fixed

- **A user line whose `message` is not an object no longer breaks indexing.** `typed_text` used to call `.get` on it and raise, which took the whole session down; it now answers "not typed text" and the line is indexed as a meta line.
