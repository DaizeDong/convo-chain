# Roadmap

Current: **v0.3.0**

## v0.3.0 (current)

Design details are documented in the source and [README.md](README.md); released changes are recorded in [CHANGELOG.md](CHANGELOG.md).

- A byte offset index of one transcript, with parallel tool replies grouped by `message.id`, compaction boundaries crossed by a three level predecessor rule, and an in-process LRU cache of three indexes.
- `chain`: the display chain cut into turns, with real forks separated from parallel tool pseudo forks.
- `node`: one line's full content, capped per field and in total.
- `export_md`: a Markdown export of any range on the chain, capped and saying where it stopped.
- `fork`: a new session file holding exactly the context the model saw at a node, relinked the way Claude Code loads a compacted session, created exclusively and never inside a git work tree.
- The `convo-chain` CLI over the same four operations.
- Calibrated against the shared data boundary guard: the transcript names this tool writes are declared as run-shape probes and every one is recognised.

- Rename and move with native index updates, sidecar preservation and recovery journals.
- Preview-bound permanent deletion with request receipts and interrupted-operation recovery.
- Project metadata for resume directories and idempotent `fork` requests.

## Planned

- **A session split across files.** A resumed session can continue in a new transcript file; following that link would let `chain` show the whole conversation instead of one file of it.
