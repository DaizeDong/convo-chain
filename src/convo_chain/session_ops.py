"""Session locations and user-requested metadata operations under an explicit root."""
from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path
import unicodedata

from . import core
from . import transactions as T
from .errors import ConvoChainError

INDEX_MAX = 8 << 20


def _project(root, project):
    base = core._base(root).resolve()
    raw = os.fspath(project)
    path = Path(raw)
    if not path.is_absolute():
        if not raw or raw in (".", "..") or any(c in raw for c in "/\\:") or len(raw) > 255:
            raise ConvoChainError("项目位置不是有效的目录标识", "bad_project")
        path = base / raw
    if path.parent.resolve() != base or not core._inside(base, path):
        raise ConvoChainError("项目位置不在会话根目录内", "outside_root")
    if not path.is_dir() or path.resolve().parent != base:
        raise ConvoChainError("目标项目目录不存在或经过了目录联接", "bad_project")
    T.ordinary(path)
    if path.name.startswith("."):
        raise ConvoChainError("内部管理目录不能作为会话位置", "bad_project")
    return base, path


def _read_index(project):
    path = project / "sessions-index.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > INDEX_MAX:
        raise ConvoChainError("会话索引不是可安全更新的小型文件", "bad_index")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ConvoChainError("会话索引无法读取，请先修复索引", "bad_index") from error
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ConvoChainError("会话索引格式不正确", "bad_index")
    if any(not isinstance(entry, dict) for entry in data.get("entries", [])):
        raise ConvoChainError("会话索引包含无效条目", "bad_index")
    ids = [e.get("sessionId") for e in data.get("entries", []) if e.get("sessionId")]
    if any(not isinstance(sid, str) for sid in ids) or len(ids) != len(set(ids)):
        raise ConvoChainError("会话索引包含重复或无效的会话号", "bad_index")
    return data


def project_info(project_dir, *, root, candidates=None):
    """Resolve a storage directory without pretending its encoded name is reversible."""
    _base, project = _project(root, project_dir)
    index = _read_index(project)
    explicit = (index or {}).get("originalPath") or (index or {}).get("projectPath")
    if isinstance(explicit, str) and explicit.strip():
        return {"id": project.name, "cwd": explicit, "storagePath": str(project),
                "locationSource": "index", "locationInferred": False}
    if candidates is None:
        candidates = []
        # Metadata resolution is bounded even when an index is absent.
        for path in sorted(project.glob("*.jsonl"))[:8]:
            if not core._inside(_base, path):
                continue
            with path.open("rb") as stream:
                head = stream.read(160_000)
            for line in head.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("cwd"), str):
                    candidates.append(row["cwd"])
    candidates = list(dict.fromkeys(c for c in candidates if isinstance(c, str) and c))
    cwd = next((c for c in candidates if core._project_key(c).casefold() == project.name.casefold()), None)
    inferred = cwd is None
    if cwd is None:
        cwd = candidates[0] if candidates else None
    return {"id": project.name, "cwd": cwd, "storagePath": str(project),
            "locationSource": "recorded" if not inferred else "historical",
            "locationInferred": inferred}


def _writable(root):
    base = core._base(root).resolve()
    if core._enclosing_worktree(base) is not None:
        raise ConvoChainError("会话修改不能写入未验证的数据仓库，请使用仓库外的会话根目录", "inside_repo")
    return base


def _index(project, stack):
    value = _read_index(project)
    path = project / "sessions-index.json"
    if path.exists():
        stack.enter_context(T.reserve(path))
    before = T.read_optional(path)
    # Read again under the reservation, so an intervening writer is not lost.
    value = _read_index(project)
    value = value if value is not None else {"version": 1}
    value.setdefault("entries", [])
    return path, before, value


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _location_result(sid, project, base, **extra):
    info = project_info(project, root=base)
    cwd = info["cwd"] if not info["locationInferred"] else None
    warnings = extra.pop("warnings", [])
    if not cwd:
        warnings.append("该存储目录没有可靠的工作目录记录，恢复会话前请先进入对应的工作目录")
    return {"id": sid, "projectDir": project.name, "storagePath": str(project),
            "file": str(project / (sid + ".jsonl")), "cwd": cwd,
            "command": core.resume_command(cwd, sid, warnings), "warnings": warnings, **extra}


def _source(sid, base, expected_project=None):
    loc = core.locate(sid, root=base)
    _project(base, loc["projectDir"])
    if expected_project is not None and loc["projectDir"].name != expected_project:
        raise ConvoChainError("会话位置已改变，请刷新后再操作", "conflict")
    T.ordinary(loc["main"])
    if loc["main"].stat().st_nlink != 1:
        raise ConvoChainError("会话文件有硬链接，不能安全修改", "unsafe_path")
    return loc["main"]


def rename(sid, title, *, root, expected_project=None):
    """Append a native custom-title record and update the native session index."""
    core.shape(sid)
    if (not isinstance(title, str) or not title.strip() or len(title) > 200
            or any(unicodedata.category(c) in ("Cc", "Cs") for c in title)):
        raise ConvoChainError("标题须为 1 到 200 个字符，不能包含换行或控制字符", "bad_title")
    title = title.strip()
    base = _writable(root)
    pending = None
    with T.locked(base) as folder:
        T.recover_all(base, folder)
        try:
            with ExitStack() as stack:
                source = _source(sid, base, expected_project)
                stream = stack.enter_context(T.reserve(source, write=True))
                index_path, before, index = _index(source.parent, stack)
                # Native indexes are caches; the transcript's latest title is authoritative.
                _loc, ix, _cached = core._load(sid, None, base)
                entry = next((e for e in index["entries"] if e.get("sessionId") == sid), None)
                if entry is None:
                    entry = {"sessionId": sid, "fullPath": str(source)}
                    index["entries"].append(entry)
                entry["summary"] = title
                after = _json_bytes(index)
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                separator = b""
                if size:
                    stream.seek(max(0, size - core.RAW_MAX))
                    tail = stream.read()
                    if not tail.endswith(b"\n"):
                        try:
                            json.loads(tail.split(b"\n")[-1])
                        except ValueError as error:
                            raise ConvoChainError("会话尾部尚未写入完整，请关闭会话后重试", "busy") from error
                        separator = b"\n"
                appended = b""
                if ix.customTitle != title:
                    appended = separator + (json.dumps({"type": "custom-title", "customTitle": title,
                        "sessionId": sid}, ensure_ascii=False) + "\n").encode("utf-8")
                st = os.fstat(stream.fileno())
                data = {"kind": "rename", "file": str(source.relative_to(base)), "size": size,
                        "identity": [st.st_dev, st.st_ino], "append": T.encode(appended),
                        "indexes": [T.index_change(index_path, before, after, base)]}
                pending = T.journal(folder, data)
                if appended:
                    stream.seek(0, os.SEEK_END)
                    stream.write(appended)
                    stream.flush()
                    os.fsync(stream.fileno())
                T.apply_indexes(base, data["indexes"])
                warnings = T.commit(pending, data)
                pending = None
            return _location_result(sid, source.parent, base, title=title,
                                    unchanged=not appended, warnings=warnings)
        except Exception as error:
            if pending is not None:
                T.recover_one(base, pending)
            if isinstance(error, OSError):
                raise ConvoChainError("会话文件正在使用或无法写入，请关闭相关会话后重试", "busy") from error
            raise
        finally:
            core.clear_cache()


_rename_no_replace = T.rename_no_replace


def move(sid, target_project, *, root, expected_project=None):
    """Move the transcript and its complete sidecar tree, preserving their bytes."""
    core.shape(sid)
    if (not isinstance(target_project, str) or not target_project or target_project in (".", "..")
            or len(target_project) > 255 or any(c in target_project for c in "/\\:")):
        raise ConvoChainError("目标必须是列表里的项目目录标识", "bad_project")
    base = _writable(root)
    pending = None
    with T.locked(base) as folder:
        T.recover_all(base, folder)
        try:
            with ExitStack() as stack:
                source = _source(sid, base)
                _base, destination = _project(base, target_project)
                if source.parent == destination:
                    return _location_result(sid, destination, base, unchanged=True)
                if expected_project is not None and source.parent.name != expected_project:
                    raise ConvoChainError("会话位置已改变，请刷新后再移动", "conflict")
                if source.stat().st_dev != destination.stat().st_dev:
                    raise ConvoChainError("只能在同一磁盘卷内迁移会话", "cross_volume")
                sidecar = source.parent / sid
                paths = [source] + ([sidecar] if sidecar.exists() else [])
                # Refuse dangling links/collisions even when there is no source sidecar.
                for target in (destination / source.name, destination / sid):
                    T.ordinary(target)
                    if target.exists():
                        raise ConvoChainError("目标位置已有同名会话或关联目录，未覆盖任何文件", "exists")
                T.ordinary(sidecar)
                moves = []
                for path in paths:
                    moves.append({"source": str(path.relative_to(base)),
                                  "target": str((destination / path.name).relative_to(base)),
                                  "snapshot": T.checked_snapshot(path, stack)})
                old_path, old_before, old_index = _index(source.parent, stack)
                new_path, new_before, new_index = _index(destination, stack)
                if any(e.get("sessionId") == sid for e in new_index["entries"]):
                    raise ConvoChainError("目标索引已有这个会话，请先检查重复记录", "exists")
                info = project_info(destination, root=base)
                entry = next((e for e in old_index["entries"] if e.get("sessionId") == sid),
                             {"sessionId": sid})
                old_index["entries"] = [e for e in old_index["entries"] if e.get("sessionId") != sid]
                entry["fullPath"] = str(destination / source.name)
                if info["cwd"] and not info["locationInferred"]:
                    entry["projectPath"] = info["cwd"]
                else:
                    entry.pop("projectPath", None)
                new_index["entries"].append(entry)
                changes = [T.index_change(old_path, old_before, _json_bytes(old_index), base),
                           T.index_change(new_path, new_before, _json_bytes(new_index), base)]
                data = {"kind": "move", "moves": moves, "indexes": changes}
                pending = T.journal(folder, data)
                for item in moves:
                    _rename_no_replace(base / item["source"], base / item["target"])
                    target = base / item["target"]
                    if target.is_dir() and T.checked_snapshot(target, stack) != item["snapshot"]:
                        raise ConvoChainError("关联文件在迁移时发生变化，已保留恢复记录", "recovery_conflict")
                T.apply_indexes(base, changes)
                warnings = T.commit(pending, data)
                pending = None
            return _location_result(sid, destination, base, previousProject=source.parent.name,
                                    unchanged=False, warnings=warnings)
        except Exception as error:
            if pending is not None:
                T.recover_one(base, pending, _rename_no_replace)
            if isinstance(error, OSError):
                raise ConvoChainError("会话或关联文件正在使用，迁移已回退；请关闭写入它的会话后重试", "busy") from error
            raise
        finally:
            core.clear_cache()


def recover_pending(root):
    """Recover an interrupted edit before accepting another session mutation."""
    base = _writable(root)
    with T.locked(base) as folder:
        count = T.recover_all(base, folder)
    core.clear_cache()
    return {"recovered": count}
