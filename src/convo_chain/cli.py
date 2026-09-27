"""The `convo-chain` command line: the library's four operations, JSON on stdout.

    convo-chain chain  SID [--leaf U] [--sub AGENT]
    convo-chain node   SID UUID [--sub AGENT]
    convo-chain export SID --to U [--from U] [--leaf U] [--sub AGENT] [--tools] [--thinking]
                           [--out FILE]
    convo-chain fork   SID AT [--leaf U]

Every subcommand takes `--root DIR` (the directory holding one folder per project, each with
`<session-id>.jsonl` files). Without it the CLI falls back to the `CONVO_CHAIN_ROOT` environment
variable. That fallback lives HERE and only here: the library itself never reads the environment,
so a caller that embeds it (task-console does) decides where the root comes from.

Exit codes are part of the contract, so a script can branch without parsing prose:

    0  success, the result object on stdout
    1  refused: {"error": {"code": ..., "message": ...}} on stdout, `code` is stable
    2  usage error (argparse), message on stderr
    3  not checked: no usable root. chain/node print their {"available": false, ...} object,
       export/fork print the refusal with code "unavailable"

This CLI is for standalone and agent use. It builds the index from scratch on every call, because
the cache lives in the process; a long-lived caller should import the library instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .core import _enclosing_worktree, chain, export_md, fork, node
from .errors import ConvoChainError

ENV_ROOT = "CONVO_CHAIN_ROOT"

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_UNAVAILABLE = 0, 1, 2, 3


def _emit(obj) -> None:
    data = json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n"
    out = getattr(sys.stdout, "buffer", None)
    if out is not None:
        sys.stdout.flush()
        out.write(data.encode("utf-8"))
        out.flush()
    else:
        sys.stdout.write(data)


def _refused(e: ConvoChainError) -> int:
    _emit({"error": {"code": e.code, "message": str(e)}})
    return EXIT_UNAVAILABLE if e.code == "unavailable" else EXIT_REFUSED


def _write_out(out: str, text: str) -> str:
    """Write the Markdown export to `out`. Exclusive create, and never inside a git work tree.

    The export is real conversation text. A file written into a repository's work tree is one
    `git add` away from being published, so the same rule `fork` applies to its own output applies
    here: a target under any directory holding `.git` is refused, not written.
    """
    p = Path(out).expanduser()
    p = p if p.is_absolute() else Path.cwd() / p
    repo = _enclosing_worktree(p.parent)
    if repo is not None:
        raise ConvoChainError(f"--out is inside a git work tree ({repo}); an export is real "
                              "conversation text, refusing to write it into any repository",
                              "inside_repo")
    try:
        with open(p, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except FileExistsError:
        raise ConvoChainError(f"--out already exists, refusing to overwrite: {p}", "exists")
    except OSError as e:
        raise ConvoChainError(f"could not write --out ({type(e).__name__}): {p}", "write_failed")
    return str(p)


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None,
                        help=f"session root directory (absolute). Defaults to ${ENV_ROOT}.")
    ap = argparse.ArgumentParser(
        prog="convo-chain",
        description="Rebuild, export or fork the conversation a Claude Code transcript held.")
    ap.add_argument("--version", action="version", version=f"convo-chain {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)

    c = sp.add_parser("chain", parents=[common], help="the display chain, cut into turns")
    c.add_argument("sid")
    c.add_argument("--leaf")
    c.add_argument("--sub")

    n = sp.add_parser("node", parents=[common], help="one node's full content")
    n.add_argument("sid")
    n.add_argument("uuid")
    n.add_argument("--sub")

    x = sp.add_parser("export", parents=[common], help="Markdown export of a range on the chain")
    x.add_argument("sid")
    x.add_argument("--to", required=True)
    x.add_argument("--from", dest="frm")
    x.add_argument("--leaf")
    x.add_argument("--sub")
    x.add_argument("--tools", action="store_true", help="include tool calls and results")
    x.add_argument("--thinking", action="store_true", help="include thinking blocks")
    x.add_argument("--out", help="write the Markdown here (exclusive create) instead of "
                                 "returning it inside the JSON")

    f = sp.add_parser("fork", parents=[common], help="write a new session file forked at AT")
    f.add_argument("sid")
    f.add_argument("at")
    f.add_argument("--leaf")
    return ap


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = args.root if args.root else (os.environ.get(ENV_ROOT) or None)
    try:
        if args.cmd == "chain":
            res = chain(args.sid, leaf=args.leaf, sub=args.sub, root=root)
        elif args.cmd == "node":
            res = node(args.sid, args.uuid, sub=args.sub, root=root)
        elif args.cmd == "export":
            res = export_md(args.sid, args.to, frm=args.frm, leaf=args.leaf, sub=args.sub,
                            include_tools=args.tools, include_thinking=args.thinking, root=root)
            if args.out:
                res = dict(res)
                res["out"] = _write_out(args.out, res.pop("text"))
        else:
            res = fork(args.sid, args.at, leaf=args.leaf, root=root)
    except ConvoChainError as e:
        return _refused(e)
    _emit(res)
    if isinstance(res, dict) and res.get("available") is False:
        return EXIT_UNAVAILABLE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
