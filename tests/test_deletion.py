"""Deletion uses generated conversations and never touches a live profile."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.make_fixtures import SID, write_index, write_session, write_sidecars
from convo_chain import deletion as D, transactions as T, session_ops as S
from convo_chain.errors import ConvoChainError

REQUEST = "00000001-0000-4000-8000-000000000099"


def prepare(root):
    source = write_session(root)
    write_index(source.parent)
    return source, D.delete_plan(SID, root=root, expected_project=source.parent.name)


def apply(root, plan, **kwargs):
    return D.delete(SID, root=root, expected_project=plan["projectDir"],
                    fingerprint=plan["fingerprint"], request_id=REQUEST, **kwargs)


def test_plan_is_bound_to_files_and_delete_preserves_other_index_fields(tmp_path):
    source, plan = prepare(tmp_path)
    before = json.loads((source.parent / "sessions-index.json").read_bytes())
    assert source.exists() and plan["files"] == 1 and plan["indexEntries"] == 1
    result = apply(tmp_path, plan)
    assert result["deleted"] is True and not source.exists()
    after = json.loads((source.parent / "sessions-index.json").read_bytes())
    assert after["entries"] == []
    assert after["extra"] == before["extra"]
    assert source.parent.is_dir()
    assert not list((tmp_path / ".convo-chain-ops").glob("*.json"))


def test_changed_file_invalidates_preview_without_deleting(tmp_path):
    source, plan = prepare(tmp_path)
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ConvoChainError) as exc:
        apply(tmp_path, plan)
    assert exc.value.code == "conflict" and source.exists()


def test_changed_index_invalidates_preview(tmp_path):
    source, plan = prepare(tmp_path)
    index = source.parent / "sessions-index.json"
    index.write_bytes(index.read_bytes() + b"\n")
    with pytest.raises(ConvoChainError) as exc:
        apply(tmp_path, plan)
    assert exc.value.code == "conflict" and source.exists()


def test_replay_never_deletes_a_recreated_session(tmp_path):
    source, plan = prepare(tmp_path)
    original = source.read_bytes()
    apply(tmp_path, plan)
    source.write_bytes(original)
    result = apply(tmp_path, plan)
    assert result["unchanged"] is True and source.read_bytes() == original


def test_interruption_before_commit_can_be_rolled_back(tmp_path, monkeypatch):
    source, plan = prepare(tmp_path)
    before = source.read_bytes()
    original = T.apply_indexes
    monkeypatch.setattr(T, "apply_indexes", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, plan)
    monkeypatch.setattr(T, "apply_indexes", original)
    S.recover_pending(tmp_path)
    assert source.read_bytes() == before
    assert apply(tmp_path, plan)["deleted"] is True


def test_cleanup_failure_retains_committed_journal_and_retry_finishes(tmp_path, monkeypatch):
    source, plan = prepare(tmp_path)
    unlink = Path.unlink
    def fail_payload(path, *args, **kwargs):
        if path.suffix == ".jsonl" and "deleting-" in str(path):
            raise PermissionError("synthetic locked payload")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_payload)
    with pytest.raises(ConvoChainError) as exc:
        apply(tmp_path, plan)
    assert exc.value.code == "cleanup_pending" and not source.exists()
    assert list((tmp_path / ".convo-chain-ops").glob("*.json"))
    monkeypatch.setattr(Path, "unlink", unlink)
    assert apply(tmp_path, plan)["deleted"] is True
    assert not list((tmp_path / ".convo-chain-ops").glob("deleting-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows write-sharing contract")
def test_open_writer_blocks_deletion(tmp_path):
    source, plan = prepare(tmp_path)
    with source.open("ab"):
        with pytest.raises(ConvoChainError) as exc:
            apply(tmp_path, plan)
    assert exc.value.code == "busy" and source.exists()


@pytest.mark.parametrize("key,value,code", [
    ("fingerprint", "bad", "bad_fingerprint"),
    ("request_id", "../outside", "bad_request_id"),
    ("expected_project", "../outside", "bad_project"),
])
def test_invalid_shape_never_modifies_files(tmp_path, key, value, code):
    source, plan = prepare(tmp_path)
    args = dict(root=tmp_path, expected_project=plan["projectDir"],
                fingerprint=plan["fingerprint"], request_id=REQUEST)
    args[key] = value
    with pytest.raises(ConvoChainError) as exc:
        D.delete(SID, **args)
    assert exc.value.code == code and source.exists()


def test_nested_sidecars_are_deleted_and_other_session_is_preserved(tmp_path):
    source, _ = prepare(tmp_path)
    sidecars = write_sidecars(source.parent)
    other = write_session(tmp_path, sid=REQUEST)
    other_bytes = other.read_bytes()
    plan = D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    assert plan["files"] == 3 and plan["directories"] == 3
    assert apply(tmp_path, plan)["deleted"] is True
    assert not sidecars.exists() and other.read_bytes() == other_bytes


def test_partial_purge_is_resumed_without_requiring_removed_children(tmp_path, monkeypatch):
    source, _ = prepare(tmp_path)
    write_sidecars(source.parent)
    plan = D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    unlink = Path.unlink
    def interrupt(path, *args, **kwargs):
        if path.name == "agent-example.jsonl" and "deleting-" in str(path):
            raise KeyboardInterrupt()
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, plan)
    stage = tmp_path / ".convo-chain-ops" / ("deleting-" + REQUEST)
    assert not (stage / (SID + ".jsonl")).exists()
    assert (stage / SID / "subagents/agent-example.jsonl").exists()
    monkeypatch.setattr(Path, "unlink", unlink)
    assert apply(tmp_path, plan)["deleted"] is True
    assert not stage.exists()


def test_changed_staged_payload_is_retained_on_recovery(tmp_path, monkeypatch):
    source, plan = prepare(tmp_path)
    purge = D._purge
    monkeypatch.setattr(D, "_purge", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        apply(tmp_path, plan)
    stage = tmp_path / ".convo-chain-ops" / ("deleting-" + REQUEST)
    staged = stage / source.name
    staged.write_bytes(staged.read_bytes() + b"\n")
    expected = staged.read_bytes()
    monkeypatch.setattr(D, "_purge", purge)
    with pytest.raises(ConvoChainError) as exc:
        apply(tmp_path, plan)
    assert exc.value.code == "recovery_conflict" and staged.read_bytes() == expected


def test_request_cannot_be_rebound_to_a_new_fingerprint(tmp_path):
    source, plan = prepare(tmp_path)
    apply(tmp_path, plan)
    write_session(tmp_path)
    fresh = D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    with pytest.raises(ConvoChainError) as exc:
        apply(tmp_path, fresh)
    assert exc.value.code == "conflict" and source.exists()


def test_sidecar_hardlink_refuses_the_whole_plan(tmp_path):
    source, _ = prepare(tmp_path)
    sidecars = write_sidecars(source.parent)
    os.link(source, sidecars / "linked.jsonl")
    with pytest.raises(ConvoChainError) as exc:
        D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    assert exc.value.code == "unsafe_path" and source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows write-sharing contract")
def test_sidecar_open_writer_blocks_before_moving_any_file(tmp_path):
    source, _ = prepare(tmp_path)
    sidecars = write_sidecars(source.parent)
    plan = D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    with (sidecars / "notes/example.txt").open("ab"):
        with pytest.raises(ConvoChainError) as exc:
            apply(tmp_path, plan)
    assert exc.value.code == "busy" and source.exists() and sidecars.exists()


def test_malformed_index_refuses_preview(tmp_path):
    source, _ = prepare(tmp_path)
    (source.parent / "sessions-index.json").write_bytes(b"{")
    with pytest.raises(ConvoChainError):
        D.delete_plan(SID, root=tmp_path, expected_project=source.parent.name)
    assert source.exists()
