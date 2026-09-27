"""convo-chain: rebuild the conversation a Claude Code session transcript actually held.

Public API (everything else is private and may change):

    chain(sid, leaf=None, sub=None, root=...)          the display chain, cut into turns
    node(sid, uuid, sub=None, root=...)                one node's full content
    export_md(sid, to, frm=None, leaf=None, sub=None,
              include_tools=False, include_thinking=False, root=...)
                                                       a Markdown export of [frm, to]
    fork(sid, at, leaf=None, sub=None, root=...)       write a NEW session file forked at `at`
    locate(sid, sub=None, root=...)                    resolve ids to a file under root
    shape(sid, sub=None, leaf=None, required=(), **nodes)
                                                       the id shape gate, no filesystem access
    resume_command(cwd, session_id, warnings=None)     the paste-able resume command
    typed_text(entry) / looks_injected(text)           "did a human type this" rules
    clear_cache()                                      drop the in-process index cache

    ConvoChainError(msg, code)                         a refused request, with a stable .code
    Unavailable                                        no usable root: "not checked", not an error

`root` is always passed in by the caller. The library never reads an environment variable.
"""

from .core import (CACHE_SLOTS, chain, clear_cache, export_md, fork, locate, node,
                   resume_command, shape)
from .errors import ConvoChainError, Unavailable
from .transcript import looks_injected, typed_text

__version__ = "0.1.0"

__all__ = [
    "CACHE_SLOTS", "ConvoChainError", "Unavailable", "__version__", "chain", "clear_cache",
    "export_md", "fork", "locate", "looks_injected", "node", "resume_command", "shape",
    "typed_text",
]
