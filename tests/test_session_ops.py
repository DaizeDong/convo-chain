"""Real filesystem transactions against generated temporary conversations."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.make_fixtures import SID, REPLY, write_index, write_session
from convo_chain import core
from convo_chain import session_ops as S
from convo_chain.errors import ConvoChainError


def test_rename_preserves_history_and_native_index(tmp_path):
    source = write_session(tmp_path)
    index = write_index(source.parent)
    original = source.read_bytes()
    result = S.rename(SID, "新的会话标题 🧪", root=tmp_path)
    assert result["title"] == "新的会话标题 🧪" and result["id"] == SID
    assert source.read_bytes().startswith(original)
    assert json.loads(source.read_text(encoding="utf-8").splitlines()[-1])["customTitle"] == result["title"]
    saved = json.loads(index.read_text(encoding="utf-8"))
    assert saved["extra"] == "preserve-me"
    assert saved["entries"][0]["unknownField"] == {"keep": True}
    assert saved["entries"][0]["summary"] == result["title"]
    assert core.chain(SID, root=tmp_path)["title"] == result["title"]
    size = source.stat().st_size
    S.rename(SID, result["title"], root=tmp_path)
    assert source.stat().st_size == size


@pytest.mark.parametrize("title", ["", "  ", "bad\nname", "x" * 201, "\x00"])
def test_invalid_name_does_not_write(tmp_path, title):
    source = write_session(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ConvoChainError):
        S.rename(SID, title, root=tmp_path)
    assert source.read_bytes() == original


def test_rename_refuses_an_incomplete_tail(tmp_path):
    source = write_session(tmp_path)
    with source.open("ab") as stream:
        stream.write(b'{"type":')
    original = source.read_bytes()
    with pytest.raises(ConvoChainError, match="完整|写入|尾"):
        S.rename(SID, "Example new name", root=tmp_path)
    assert source.read_bytes() == original


def test_move_carries_sidecars_and_index_without_changing_transcript(tmp_path):
    source = write_session(tmp_path)
    old_index = write_index(source.parent)
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    sidecar = source.parent / SID / "subagents" / "agent-a1example.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{"type":"user","message":{"content":"Synthetic child"}}\n', encoding="utf-8")
    original, child = source.read_bytes(), sidecar.read_bytes()
    result = S.move(SID, target.name, root=tmp_path)
    dest = target / source.name
    assert result["id"] == SID and result["projectDir"] == target.name
    assert dest.read_bytes() == original and not source.exists()
    assert (target / SID / "subagents" / sidecar.name).read_bytes() == child
    assert not (source.parent / SID).exists()
    assert json.loads(old_index.read_text(encoding="utf-8"))["entries"] == []
    moved_entry = json.loads((target / "sessions-index.json").read_text(encoding="utf-8"))["entries"][0]
    assert moved_entry["sessionId"] == SID and moved_entry["fullPath"] == str(dest)
    assert moved_entry["unknownField"] == {"keep": True}
    assert core.locate(SID, root=tmp_path)["main"] == dest
    again = S.move(SID, target.name, root=tmp_path)
    assert again["unchanged"] is True


def test_move_collision_leaves_both_files_untouched(tmp_path):
    source = write_session(tmp_path)
    target = write_session(tmp_path, project="C--Acme-target")
    old = source.read_bytes(), target.read_bytes()
    with pytest.raises(ConvoChainError):
        S.move(SID, target.parent.name, root=tmp_path)
    assert (source.read_bytes(), target.read_bytes()) == old


def test_move_sidecar_collision_refuses_before_moving_anything(tmp_path):
    source = write_session(tmp_path)
    target = tmp_path / "C--Acme-target"
    (target / SID).mkdir(parents=True)
    with pytest.raises(ConvoChainError):
        S.move(SID, target.name, root=tmp_path)
    assert source.exists() and not (target / source.name).exists()


def test_malformed_index_cannot_partially_rename_or_move(tmp_path):
    source = write_session(tmp_path)
    (source.parent / "sessions-index.json").write_text("broken", encoding="utf-8")
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    original = source.read_bytes()
    with pytest.raises(ConvoChainError):
        S.rename(SID, "Example name", root=tmp_path)
    with pytest.raises(ConvoChainError):
        S.move(SID, target.name, root=tmp_path)
    assert source.read_bytes() == original


def test_interrupted_move_is_recovered_without_losing_sidecars(tmp_path, monkeypatch):
    source = write_session(tmp_path)
    sidecar = source.parent / SID
    sidecar.mkdir()
    (sidecar / "example.txt").write_text("synthetic", encoding="utf-8")
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    original = source.read_bytes()
    rename = S._rename_no_replace
    calls = 0
    def interrupted(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("synthetic interruption")
        return rename(src, dst)
    monkeypatch.setattr(S, "_rename_no_replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        S.move(SID, target.name, root=tmp_path)
    monkeypatch.setattr(S, "_rename_no_replace", rename)
    S.recover_pending(tmp_path)
    assert source.read_bytes() == original
    assert (sidecar / "example.txt").read_text(encoding="utf-8") == "synthetic"
    assert not (target / source.name).exists() and not (target / SID).exists()


def test_project_info_prefers_explicit_index_over_historical_cwd(tmp_path):
    source = write_session(tmp_path, cwd="C:/Acme/old")
    write_index(source.parent, cwd="C:/Acme/current")
    info = S.project_info(source.parent, root=tmp_path)
    assert info["cwd"] == "C:/Acme/current" and info["locationSource"] == "index"


def test_target_escape_is_rejected(tmp_path):
    source = write_session(tmp_path)
    for target in ("../other", "C:/outside", "..", "//server/share"):
        with pytest.raises(ConvoChainError):
            S.move(SID, target, root=tmp_path)
    assert source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows writer sharing contract")
def test_an_open_writer_prevents_a_move(tmp_path):
    source = write_session(tmp_path)
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    with source.open("ab"):
        with pytest.raises(ConvoChainError, match="使用|写入|关闭"):
            S.move(SID, target.name, root=tmp_path)
    assert source.exists()


def test_fork_uses_storage_index_and_retry_returns_the_same_session(tmp_path):
    source = write_session(tmp_path, cwd="C:/Acme/historical")
    write_index(source.parent, cwd="C:/Acme/current")
    request = "00000001-0000-4000-8000-000000000099"
    first = core.fork(SID, REPLY, root=tmp_path, request_id=request)
    again = core.fork(SID, REPLY, root=tmp_path, request_id=request)
    assert first["cwd"] == "C:/Acme/current"
    assert Path(first["file"]).parent == source.parent
    assert again["newId"] == first["newId"] and again["reused"]
    S.rename(first["newId"], "Synthetic branch", root=tmp_path)
    assert core.fork(SID, REPLY, root=tmp_path, request_id=request)["title"] == "Synthetic branch"
    assert len(list(tmp_path.glob("*/*.jsonl"))) == 2


def test_failed_index_write_rolls_back_transcript_and_sidecars(tmp_path, monkeypatch):
    from convo_chain import transactions as T
    source = write_session(tmp_path)
    index = write_index(source.parent)
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    original, original_index = source.read_bytes(), index.read_bytes()
    apply = T.atomic_write
    failed = False
    def fail_once(path, data):
        nonlocal failed
        if path.name == "sessions-index.json" and not failed:
            failed = True
            raise PermissionError("synthetic index busy")
        return apply(path, data)
    monkeypatch.setattr(T, "atomic_write", fail_once)
    with pytest.raises(ConvoChainError):
        S.move(SID, target.name, root=tmp_path)
    assert source.read_bytes() == original and index.read_bytes() == original_index
    assert not (target / source.name).exists()
    failed = False
    with pytest.raises(ConvoChainError):
        S.rename(SID, "Synthetic rename", root=tmp_path)
    assert source.read_bytes() == original and index.read_bytes() == original_index


@pytest.mark.skipif(os.name != "nt", reason="Windows writer sharing contract")
def test_open_subagent_writer_prevents_moving_the_parent(tmp_path):
    source = write_session(tmp_path)
    target = tmp_path / "C--Acme-target"
    target.mkdir()
    child = source.parent / SID / "subagents" / "agent-example.jsonl"
    child.parent.mkdir(parents=True)
    with child.open("ab"):
        with pytest.raises(ConvoChainError) as refused:
            S.move(SID, target.name, root=tmp_path)
    assert refused.value.code == "busy" and source.exists()
