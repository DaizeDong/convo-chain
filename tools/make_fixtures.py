"""Generate temporary conversation records from synthetic constants only."""
import json
from pathlib import Path

SID = "00000001-0000-4000-8000-000000000001"
USER = "00000001-0000-4000-8000-000000000002"
REPLY = "00000001-0000-4000-8000-000000000003"


def write_session(root, project="C--Acme-source", sid=SID, cwd="C:/Acme/source"):
    folder = Path(root) / project
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "user", "uuid": USER, "parentUuid": None, "sessionId": sid,
         "cwd": cwd, "message": {"role": "user", "content": "Synthetic request"}},
        {"type": "assistant", "uuid": REPLY, "parentUuid": USER, "sessionId": sid,
         "cwd": cwd, "message": {"role": "assistant", "content": [{"type": "text", "text": "Synthetic reply"}]}},
        {"type": "custom-title", "sessionId": sid, "customTitle": "Example conversation"},
    ]
    path = folder / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def write_index(project, sid=SID, cwd="C:/Acme/source"):
    obj = {"version": 1, "originalPath": cwd, "extra": "preserve-me", "entries": [
        {"sessionId": sid, "fullPath": str(Path(project) / f"{sid}.jsonl"),
         "projectPath": cwd, "summary": "Example conversation", "messageCount": 2,
         "unknownField": {"keep": True}}]}
    path = Path(project) / "sessions-index.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path
