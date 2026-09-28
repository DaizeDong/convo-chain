# convo-chain

Rebuild the conversation a Claude Code session transcript actually held, then export any stretch of it to Markdown or fork a new session from any node.

[![Python Library](https://img.shields.io/badge/Python-Library%20%2B%20CLI-orange?style=flat)](src/convo_chain/__init__.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green?style=flat)](pyproject.toml)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

## ⭐ Read this first, the design philosophy

A transcript is not a list of messages, and walking `parentUuid` upward from the last line does not give you the conversation. Three shapes break the naive walk, and each one was measured on real transcripts before the code was written:

1. **One assistant reply is split into one line per content block.** Parallel tool calls make a node look like it has two children (the next block of the same `message.id`, and the first block's tool result). That is one reply, not a branch. This library groups replies by `message.id` and pairs every `tool_use` with exactly one result.
2. **A compaction boundary has `parentUuid: null`,** so a naive walk stops there and the whole history before it vanishes. Its `logicalParentUuid` often points at a line written after the boundary, so following it loops back. The predecessor is chosen by a three level rule, and the one level that is a guess is reported as a guess.
3. **Preserved messages stay where they were written, before the boundary,** and only the boundary's `compactMetadata` links them into the new context. A fork relinks them the way Claude Code does at load time, so the forked file holds exactly the context the model saw at that node, nothing more.

Two rules follow from "this reads real conversations". The library never guesses where transcripts live: the caller passes `root`, and without it the answer is "not checked", never a default directory. And there is exactly one write, `fork`, which creates a new file exclusively, never touches the source, and refuses outright when the target directory is inside any git work tree.

## What it is (and isn't)

It is a pure Python library (no dependencies) plus a small CLI. It indexes a transcript by byte offsets, keeps only a slim record per line in memory, and reads full lines back on demand, so a transcript of several hundred megabytes costs one pass to index and very little to browse. A process keeps an LRU cache of the last three indexes, keyed on path, mtime and size.

It is not a transcript viewer and it has no UI. The engine was extracted from a conversation chain panel being built for [task-console](https://github.com/DaizeDong/task-console), and that panel is its intended consumer. It does not list sessions either; it answers "what did this one session hold".

## Install

```
pip install "convo-chain @ git+https://github.com/DaizeDong/convo-chain"
```

Or from a checkout: `pip install .`. Python 3.11 or newer. The wheel is `py3-none-any` and has no dependencies.

## Quick start

```
convo-chain chain 00000000-0000-4000-8000-000000000000 --root C:/Users/you/.claude/projects
```

`--root` is the directory that holds one folder per project, each with `<session-id>.jsonl` files. Set `CONVO_CHAIN_ROOT` instead of passing it every time. Output is JSON on stdout.

## Library API

```python
import convo_chain as cc

root = "C:/Users/you/.claude/projects"          # always passed in, never read from the environment
cc.shape(sid, leaf=leaf)                          # id shape gate, raises before any filesystem access
r = cc.chain(sid, leaf=None, sub=None, root=root) # the display chain cut into turns, with forks
n = cc.node(sid, uuid, sub=None, root=root)       # one node's full content, read back by offset
md = cc.export_md(sid, to=uuid, frm=None, include_tools=False, include_thinking=False, root=root)
f = cc.fork(sid, at=uuid, leaf=None, root=root)   # writes <newId>.jsonl next to the source
print(f["command"])                               # cd '<cwd>'; claude --resume <newId>
```

Also exported: `locate`, `resume_command`, `clear_cache`, `CACHE_SLOTS`, and the transcript rules `typed_text(entry)` and `looks_injected(text)` that decide whether a user line is something a human actually typed.

Errors come in two kinds that do not inherit from each other. `ConvoChainError` is a refused request and carries a stable `.code` (`bad_id`, `bad_sub`, `bad_leaf`, `bad_uuid`, `not_found`, `ambiguous`, `outside_root`, `not_on_path`, `bad_range`, `stale_index`, `exists`, `inside_repo`, `unrelinkable`, `empty_fork`, `no_sub_fork`, `unavailable`, and a few more). `Unavailable` means no usable root: `chain` and `node` return `{"available": false, "reason": ...}` for it, while `export_md` and `fork` raise `ConvoChainError` with code `unavailable`.

## CLI

```
convo-chain chain  SID [--leaf U] [--sub AGENT]                  [--root DIR]
convo-chain node   SID UUID [--sub AGENT]                        [--root DIR]
convo-chain export SID --to U [--from U] [--leaf U] [--sub AGENT]
                   [--tools] [--thinking] [--out FILE]           [--root DIR]
convo-chain fork   SID AT [--leaf U]                             [--root DIR]
convo-chain --version
```

Exit codes: `0` success, `1` refused (`{"error": {"code", "message"}}` on stdout), `2` usage error, `3` not checked (no usable root), `4` internal failure (`{"error": {"code": "internal", "message": <exception class name>}}` on stdout, a bug or an unreadable file rather than a refusal). An explicit `--root`, even `--root ""`, is never replaced by `CONVO_CHAIN_ROOT`. `export --out` writes the Markdown to a file with exclusive create and refuses a target inside a git work tree, for the same reason `fork` does. `python -m convo_chain` works the same way.

The CLI rebuilds the index on every call. A long lived caller should import the library so the cache survives between requests.

## How task-console consumes it

This describes the task-console change that consumes it, which is on task-console's `master`. task-console depends on `convo-chain` as a pinned library, the same standing as `fleet-guards` and `llmcall`, and imports it in process so the index cache lives in the console process. The console keeps everything that is about serving a browser: the four HTTP routes with their token, host and shape gates, the conversation chain panel and its UI tests, and the `TASK_CONSOLE_SESSIONS` setting, whose value it passes in as `root`. This repository owns the transcript semantics and nothing else, and it never reads a `TASK_CONSOLE_*` variable.

## Where the data lives

Nowhere in this repository. Transcripts stay under the root the caller passes. A fork is one new `<uuid>.jsonl` in the source transcript's own project directory. An export goes where `--out` says, or back to the caller in memory. The test suite builds every transcript synthetically inside pytest's temporary directory, and `.gitignore` excludes `*.jsonl` and `*.jsonl.gz` everywhere. `.dataclass.json` records how that was checked.

## Tests

```
python -B -m pytest tests/ -q -p no:cacheprovider
```

The suite runs on `windows-latest` with Python 3.11 and 3.13 under a collected count floor, and the key guards (the shape gate, the no environment rule, exclusive create, the work tree refusal, the injected message rule) were each poisoned once to confirm a test turns red.

## Limitations

The index is per file, so a session split across several transcript files is several sessions here. The fork's resume command assumes Claude Code's current project directory naming. The data boundary guard does not yet recognise the transcript file shape, so the ignore rules above are the stop gap until that lands upstream.

## Languages

This README exists in [English](README.md) and [中文](README_CN.md), section for section. Code comments and the messages the library returns are in Chinese.

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) and [CHANGELOG.md](CHANGELOG.md). Issues and pull requests are welcome; every example in them must be synthetic. MIT licensed, see [LICENSE](LICENSE).
