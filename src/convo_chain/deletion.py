"""Confirmed deletion of one transcript, its sidecars and native index entry."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import re

from . import core, session_ops as S, transactions as T
from .errors import ConvoChainError


def _project_id(value):
    if (not isinstance(value, str) or not value or value.startswith(".")
            or len(value) > 255 or any(c in value for c in "/\\:")):
        raise ConvoChainError("来源项目标识不正确", "bad_project")
    return value


def _binding(sid, expected_project, fingerprint, request_id):
    core.shape(sid)
    _project_id(expected_project)
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ConvoChainError("删除预览无效，请重新查看删除范围", "bad_fingerprint")
    if not isinstance(request_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", request_id):
        raise ConvoChainError("删除请求标识无效", "bad_request_id")
    return {"id": sid, "projectDir": expected_project, "fingerprint": fingerprint,
            "requestId": request_id.lower()}


def _counts(snapshot):
    if not snapshot["directory"]:
        return 1, 0, snapshot["size"]
    files, directories, size = 0, 1, 0
    for child in snapshot["children"].values():
        f, d, b = _counts(child)
        files, directories, size = files + f, directories + d, size + b
    return files, directories, size


def _prepare(sid, base, expected, stack):
    source = S._source(sid, base, expected)
    sidecar = source.parent / sid
    T.ordinary(sidecar)
    paths = [source] + ([sidecar] if sidecar.exists() else [])
    items = [{"source": str(path.relative_to(base)), "snapshot": T.checked_snapshot(path, stack)}
             for path in paths]
    index_path, before, index = S._index(source.parent, stack)
    entries = [e for e in index["entries"] if e.get("sessionId") == sid]
    index["entries"] = [e for e in index["entries"] if e.get("sessionId") != sid]
    changes = ([T.index_change(index_path, before, S._json_bytes(index), base)] if entries else [])
    signature = {"id": sid, "projectDir": source.parent.name, "items": items,
                 "index": hashlib.sha256(before or b"").hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    counts = [_counts(item["snapshot"]) for item in items]
    summary = entries[0].get("summary") if entries else None
    plan = {"id": sid, "projectDir": source.parent.name,
            "title": summary if isinstance(summary, str) else sid,
            "files": sum(c[0] for c in counts), "directories": sum(c[1] for c in counts),
            "bytes": sum(c[2] for c in counts), "indexEntries": len(entries), "fingerprint": fingerprint}
    return plan, items, changes


def delete_plan(sid, *, root, expected_project):
    """Preview a bounded set of files; the fingerprint expires on any observed change."""
    core.shape(sid)
    _project_id(expected_project)
    base = S._writable(root)
    try:
        with T.locked(base), ExitStack() as stack:
            return _prepare(sid, base, expected_project, stack)[0]
    except OSError as error:
        raise ConvoChainError("会话或关联文件正在使用或无法读取，请关闭后重试", "busy") from error


def _receipt_path(base, request_id):
    directory = T.safe_path(base, ".convo-chain-ops/delete-receipts")
    directory.mkdir(exist_ok=True)
    return directory / (request_id + ".json")


def _replayed(base, binding):
    path = _receipt_path(base, binding["requestId"])
    T.ordinary(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 64_000:
            raise ValueError("receipt size")
        record = json.loads(path.read_bytes())
        if record["version"] != 1 or record["binding"] != binding:
            raise ValueError("receipt binding")
        result = record["result"]
        if result["deleted"] is not True or result["id"] != binding["id"]:
            raise ValueError("receipt result")
    except (ValueError, KeyError, TypeError) as error:
        raise ConvoChainError("删除请求与已有记录不一致，未删除任何新文件", "conflict") from error
    return {**result, "unchanged": True}


def _remaining_matches(path, expected):
    """Missing payloads are allowed after commit; changed or added payloads are not."""
    T.ordinary(path)
    if not path.exists():
        return
    current = T.snapshot(path)
    for key in ("device", "inode", "directory"):
        if current[key] != expected[key]:
            raise ConvoChainError("待删除文件已被替换，已保留现场", "recovery_conflict")
    if not current["directory"]:
        if current != expected:
            raise ConvoChainError("待删除文件发生变化，已保留现场", "recovery_conflict")
        return
    if not current["children"].keys() <= expected["children"].keys():
        raise ConvoChainError("待删除目录出现新文件，已保留现场", "recovery_conflict")
    for name in current["children"]:
        _remaining_matches(path / name, expected["children"][name])


def _purge(path, expected):
    _remaining_matches(path, expected)
    if not path.exists():
        return
    if expected["directory"]:
        for name, child in expected["children"].items():
            _purge(path / name, child)
        T.ordinary(path)
        if T.snapshot(path) != {**expected, "children": {}}:
            raise ConvoChainError("待删除目录发生变化，已保留现场", "recovery_conflict")
        path.rmdir()
    else:
        with T.reserve(path):
            _remaining_matches(path, expected)
            path.unlink()


def finish_committed(base, journal_path, data):
    """Finish only the private staging tree of this committed deletion."""
    try:
        meta = data["deletion"]
        binding = _binding(meta["id"], meta["projectDir"], meta["fingerprint"], meta["requestId"])
        stage_relative = ".convo-chain-ops/deleting-" + binding["requestId"]
        stage = T.safe_path(base, stage_relative)
        moves = data["moves"]
        expected_names = {binding["id"] + ".jsonl", binding["id"]}
        names = set()
        if not 1 <= len(moves) <= 2:
            raise ValueError("payload count")
        for item in moves:
            source = T.safe_path(base, item["source"])
            name = source.name
            if (name not in expected_names or name in names or source.parent.name != binding["projectDir"]
                    or source.parent.parent != base):
                raise ValueError("source shape")
            target = T.safe_path(base, item["target"])
            if target != stage / name:
                raise ValueError("target shape")
            names.add(name)
        if binding["id"] + ".jsonl" not in names:
            raise ValueError("missing main payload")
        if stage.exists():
            st = stage.stat()
            if not stage.is_dir() or [st.st_dev, st.st_ino] != meta["stageIdentity"]:
                raise ValueError("staging identity")
            if not {p.name for p in stage.iterdir()} <= names:
                raise ValueError("unexpected staged file")
            for item in moves:
                _remaining_matches(T.safe_path(base, item["target"]), item["snapshot"])
            for item in moves:
                _purge(T.safe_path(base, item["target"]), item["snapshot"])
            stage.rmdir()
        result = {"id": binding["id"], "projectDir": binding["projectDir"], "deleted": True,
                  "files": meta["files"], "bytes": meta["bytes"], "unchanged": False, "warnings": []}
        T.save(_receipt_path(base, binding["requestId"]),
               {"version": 1, "binding": binding, "result": result})
        try:
            journal_path.unlink()
        except OSError:
            result["warnings"].append("会话文件已删除，操作记录将在下次操作时清理")
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise ConvoChainError("删除恢复记录不完整或路径不安全，已保留文件", "recovery_conflict") from error
    except ConvoChainError as error:
        if error.code != "busy":
            raise
        raise ConvoChainError("会话已从列表移除，但部分文件尚未清理。请关闭相关程序，再重试此删除请求", "cleanup_pending") from error
    except OSError as error:
        raise ConvoChainError("会话已从列表移除，但部分文件尚未清理。请关闭相关程序，再重试此删除请求", "cleanup_pending") from error


def delete(sid, *, root, expected_project, fingerprint, request_id):
    """Permanently delete the exact preview; replay never acts on a recreated session."""
    binding = _binding(sid, expected_project, fingerprint, request_id)
    base = S._writable(root)
    pending = stage = None
    committed = False
    with T.locked(base) as folder:
        try:
            replay = _replayed(base, binding)
            if replay:
                return replay
            T.recover_all(base, folder)
            replay = _replayed(base, binding)
            if replay:
                return replay
            with ExitStack() as stack:
                plan, items, changes = _prepare(sid, base, expected_project, stack)
                if plan["fingerprint"] != fingerprint:
                    raise ConvoChainError("会话或索引已改变，请重新预览后确认删除", "conflict")
                stage = T.safe_path(base, ".convo-chain-ops/deleting-" + binding["requestId"])
                stage.mkdir(exist_ok=True)
                if any(stage.iterdir()):
                    raise ConvoChainError("该删除请求已有待恢复的文件，已停止修改", "recovery_conflict")
                st = stage.stat()
                for item in items:
                    name = (base / item["source"]).name
                    item["target"] = str((stage / name).relative_to(base))
                data = {"kind": "move", "moves": items, "indexes": changes,
                        "deletion": {**binding, "files": plan["files"], "bytes": plan["bytes"],
                                     "stageIdentity": [st.st_dev, st.st_ino]}}
                pending = T.journal(folder, data)
                for item in items:
                    source, target = base / item["source"], base / item["target"]
                    T.rename_no_replace(source, target)
                    if T.checked_snapshot(target, stack) != item["snapshot"]:
                        raise ConvoChainError("会话在删除过程中发生变化，已保留恢复记录", "recovery_conflict")
                T.apply_indexes(base, changes)
                data.update(kind="delete", status="committed")
                T.save(pending, data)
                committed = True
            return finish_committed(base, pending, data)
        except Exception as error:
            if pending is not None and not committed:
                T.recover_one(base, pending)
            if isinstance(error, OSError):
                raise ConvoChainError("会话文件正在使用或无法修改，请关闭相关程序后重试", "busy") from error
            raise
        finally:
            if stage is not None and not committed and stage.exists():
                try:
                    stage.rmdir()
                except OSError:
                    pass  # A nonempty stage remains protected by its recovery journal.
            core.clear_cache()
