"""Small, recoverable filesystem transactions for explicitly requested session edits.

Journals live beside the caller's sessions, never in a package checkout. A root
lock serializes cooperating clients; Windows file reservations also reject open
writers. Recovery compares expected identities and bytes before changing anything.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
import ctypes
import json
import os
from pathlib import Path
import stat
import threading
import uuid

from .errors import ConvoChainError

_LOCK = threading.RLock()
_JOURNAL_MAX = 48 << 20


def ordinary(path):
    """Reject symlinks, junctions and other reparse points, including broken ones."""
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise ConvoChainError("会话路径包含链接或目录联接，不能安全修改", "unsafe_path")


def _flush_dir(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path, data):
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        ordinary(path)
        if os.name == "nt":
            _win_rename(tmp, path, replace=True)
        else:
            os.replace(tmp, path)
        _flush_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def locked(base):
    folder = base / ".convo-chain-ops"
    ordinary(folder)
    folder.mkdir(exist_ok=True)
    lock = folder / ".lock"
    ordinary(lock)
    with _LOCK, lock.open("a+b") as stream:
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ConvoChainError("另一个会话操作正在进行，请稍后重试", "busy") from error
        try:
            yield folder
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _win_handle(path, write=False, rename=False):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Deny write sharing while permitting reads and our own same-volume rename.
    access = 0x10000 if rename else 0x80000000 | (0x40000000 if write else 0)
    handle = create(str(path), access, 7 if rename else 1 | 4, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel, handle


def _win_rename(source, target, replace=False):
    """Windows 10+ rename with share-delete handles kept open during the edit."""
    from ctypes import wintypes
    class RenameInfo(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]
    encoded = str(target.absolute()).encode("utf-16-le")
    buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
    info = RenameInfo.from_buffer(buffer)
    info.Flags = 2 | (1 if replace else 0)  # POSIX_SEMANTICS, optional REPLACE_IF_EXISTS.
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
    kernel, handle = _win_handle(source, rename=True)
    try:
        call = kernel.SetFileInformationByHandle
        call.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        call.restype = wintypes.BOOL
        if not call(handle, 22, buffer, len(buffer)):  # FileRenameInfoEx
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def reserve(path, write=False):
    ordinary(path)
    try:
        if os.name == "nt":
            kernel, handle = _win_handle(path, write)
            stream = None
            try:
                if write:
                    import msvcrt
                    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                    handle = None
                    stream = os.fdopen(fd, "r+b")
                yield stream
            finally:
                if stream is not None:
                    stream.close()
                if handle is not None:
                    kernel.CloseHandle(handle)
        else:
            import fcntl
            with path.open("r+b" if write else "rb") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield stream if write else None
    except PermissionError as error:
        raise ConvoChainError("会话或关联文件正在使用，请关闭写入它的会话后重试", "busy") from error


def snapshot(path):
    ordinary(path)
    st = path.stat()
    if path.is_file() and st.st_nlink != 1:
        raise ConvoChainError("会话文件有硬链接，不能安全修改", "unsafe_path")
    result = {"device": st.st_dev, "inode": st.st_ino, "directory": path.is_dir()}
    if path.is_file():
        result.update(size=st.st_size, mtime=st.st_mtime_ns)
    else:
        result["children"] = {p.name: snapshot(p) for p in sorted(path.iterdir())}
    return result


def reserve_tree(stack, path):
    ordinary(path)
    if path.is_dir():
        for child in path.iterdir():
            reserve_tree(stack, child)
    else:
        stack.enter_context(reserve(path))


def checked_snapshot(path, stack):
    if path.is_dir() and os.name == "nt":
        # Windows refuses to rename a directory while its children have handles.
        # Check writers before the rename and recheck the complete tree afterwards;
        # never copy/delete or overwrite a tree that changed in that interval.
        with ExitStack() as children:
            reserve_tree(children, path)
            return snapshot(path)
    reserve_tree(stack, path)
    return snapshot(path)


def rename_no_replace(source, target):
    """Atomic same-volume rename, with no overwrite or copy/delete fallback."""
    if os.name == "nt":
        _win_rename(source, target)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(libc, "renameat2"):
            call = libc.renameat2
            call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            code = call(-100, os.fsencode(source), -100, os.fsencode(target), 1)
        elif hasattr(libc, "renamex_np"):
            call = libc.renamex_np
            call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            code = call(os.fsencode(source), os.fsencode(target), 4)
        else:
            raise ConvoChainError("当前系统不支持安全的不覆盖迁移", "unsupported")
        if code:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(source))
    _flush_dir(source.parent)
    if target.parent != source.parent:
        _flush_dir(target.parent)


def encode(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def decode(data):
    return None if data is None else base64.b64decode(data, validate=True)


def read_optional(path):
    ordinary(path)
    return path.read_bytes() if path.exists() else None


def index_change(path, before, after, base):
    return {"path": str(path.relative_to(base)), "before": encode(before), "after": encode(after)}


def journal(folder, data):
    path = folder / (uuid.uuid4().hex + ".json")
    data.update(version=1, status="prepared")
    save(path, data)
    return path


def save(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def commit(path, data):
    data["status"] = "committed"
    save(path, data)
    try:
        path.unlink()
        _flush_dir(path.parent)
        return []
    except OSError:
        return ["操作已完成；清理记录将在下次会话操作时重试"]


def safe_path(base, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts):
        raise ConvoChainError("恢复记录中的路径不安全", "recovery_conflict")
    current = base
    for part in path.parts:
        current = current / part
        ordinary(current)
    if current.resolve().is_relative_to(base) and current != base:
        return current
    raise ConvoChainError("恢复记录指向会话目录之外", "recovery_conflict")


def apply_indexes(base, changes):
    for change in changes:
        path = safe_path(base, change["path"])
        if read_optional(path) != decode(change["before"]):
            raise ConvoChainError("会话索引刚被其他程序更新，请刷新后重试", "conflict")
        atomic_write(path, decode(change["after"]))


def recover_one(base, journal_path, rename=rename_no_replace):
    ordinary(journal_path)
    if journal_path.stat().st_size > _JOURNAL_MAX:
        raise ConvoChainError("恢复记录过大，需要先检查", "recovery_conflict")
    try:
        data = json.loads(journal_path.read_text(encoding="utf-8"))
        if data["version"] != 1 or data["kind"] not in ("move", "rename"):
            raise ValueError("unknown transaction")
        if data["status"] == "committed":
            journal_path.unlink()
            return
        if data["status"] != "prepared":
            raise ValueError("unknown transaction status")
        changes = data["indexes"]
        moves = data.get("moves", [])
        with ExitStack() as stack:
            undo_moves = []
            for item in reversed(moves):
                source, target = safe_path(base, item["source"]), safe_path(base, item["target"])
                if source.exists() == target.exists():
                    raise ConvoChainError("迁移中断后源或目标发生变化，已保留恢复记录", "recovery_conflict")
                actual = source if source.exists() else target
                if checked_snapshot(actual, stack) != item["snapshot"]:
                    raise ConvoChainError("迁移中断后文件被写入，不能自动回退", "recovery_conflict")
                if actual == target:
                    undo_moves.append((target, source))
            undo_indexes = []
            for change in changes:
                path = safe_path(base, change["path"])
                before, after, current = decode(change["before"]), decode(change["after"]), read_optional(path)
                if current != before:
                    if current != after:
                        raise ConvoChainError("恢复时发现索引已被更新，已保留原始备份", "recovery_conflict")
                    undo_indexes.append((path, before, current))
            if data["kind"] == "rename":
                path = safe_path(base, data["file"])
                stream = stack.enter_context(reserve(path, write=True))
                st = os.fstat(stream.fileno())
                if [st.st_dev, st.st_ino] != data["identity"]:
                    raise ConvoChainError("重命名中断后会话文件被替换", "recovery_conflict")
                size, appended = data["size"], decode(data["append"])
                stream.seek(size)
                tail = stream.read(len(appended) + 1)
                # An interrupted append may have written only a prefix.
                if st.st_size < size or not appended.startswith(tail):
                    raise ConvoChainError("重命名中断后会话继续写入，不能自动回退", "recovery_conflict")
                stream.truncate(size)
                stream.flush()
                os.fsync(stream.fileno())
            for target, source in undo_moves:
                rename(target, source)
            for path, before, expected in undo_indexes:
                if read_optional(path) != expected:
                    raise ConvoChainError("回退期间索引被更新，已保留备份", "recovery_conflict")
                if before is None:
                    path.unlink()
                else:
                    atomic_write(path, before)
        journal_path.unlink()
        _flush_dir(journal_path.parent)
    except (KeyError, TypeError, ValueError) as error:
        raise ConvoChainError("恢复记录损坏，已停止修改并保留文件", "recovery_conflict") from error


def recover_all(base, folder):
    pending = sorted(folder.glob("*.json"))
    for path in pending:
        recover_one(base, path)
    return len(pending)
