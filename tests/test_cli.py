"""The `convo-chain` CLI. Every transcript here is synthetic and built in tmp_path."""
import json
import os
import subprocess
import sys

import pytest

from convo_chain import cli
from convo_chain import core as T

SID = "33333333-3333-4333-8333-333333333333"
CWD = "C:/work/example-project"


def U(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def _rec(**kw):
    kw.setdefault("sessionId", SID)
    kw.setdefault("cwd", CWD)
    kw.setdefault("isSidechain", False)
    kw.setdefault("timestamp", "2026-01-01T00:00:00Z")
    return kw


def _session(root, proj="C--work-example-project"):
    d = root / proj
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        _rec(type="user", uuid=U(1), parentUuid=None,
             message={"role": "user", "content": "first synthetic question"}),
        _rec(type="assistant", uuid=U(2), parentUuid=U(1),
             message={"id": "m1", "role": "assistant", "model": "model-x",
                      "content": [{"type": "text", "text": "first synthetic answer"}]}),
        _rec(type="user", uuid=U(3), parentUuid=U(2),
             message={"role": "user", "content": "second synthetic question"}),
        _rec(type="assistant", uuid=U(4), parentUuid=U(3),
             message={"id": "m2", "role": "assistant", "model": "model-x",
                      "content": [{"type": "text", "text": "second synthetic answer"}]}),
    ]
    f = d / f"{SID}.jsonl"
    f.write_bytes("".join(json.dumps(r) + "\n" for r in rows).encode("utf-8"))
    return f


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # setenv to "" rather than delenv: the empty string and "unset" take the same "not checked"
    # path, and a test must never be able to pick up a real root from the machine it runs on.
    monkeypatch.setenv(cli.ENV_ROOT, "")
    T.clear_cache()
    yield
    T.clear_cache()


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_chain_prints_the_library_result(tmp_path, capsys):
    _session(tmp_path)
    rc, out = run(capsys, "chain", SID, "--root", str(tmp_path))
    assert rc == 0 and out["available"] is True
    assert out["leaf"] == U(4) and out["pathLen"] == 4
    assert out == json.loads(json.dumps(T.chain(SID, root=str(tmp_path)) | {"cached": out["cached"]}))


def test_node_prints_one_node(tmp_path, capsys):
    _session(tmp_path)
    rc, out = run(capsys, "node", SID, U(2), "--root", str(tmp_path))
    assert rc == 0 and out["u"] == U(2) and out["text"] == "first synthetic answer"


def test_root_falls_back_to_the_environment_variable(tmp_path, capsys, monkeypatch):
    _session(tmp_path)
    monkeypatch.setenv(cli.ENV_ROOT, str(tmp_path))
    rc, out = run(capsys, "chain", SID)
    assert rc == 0 and out["available"] is True


def test_explicit_root_wins_over_the_environment(tmp_path, capsys, monkeypatch):
    real = tmp_path / "real"
    _session(real)
    monkeypatch.setenv(cli.ENV_ROOT, str(tmp_path / "does-not-exist"))
    rc, out = run(capsys, "chain", SID, "--root", str(real))
    assert rc == 0 and out["available"] is True


def test_no_root_is_not_checked_exit_3(capsys):
    rc, out = run(capsys, "chain", SID)
    assert rc == 3 and out["available"] is False
    rc, out = run(capsys, "node", SID, U(1))
    assert rc == 3 and out["available"] is False
    rc, out = run(capsys, "fork", SID, U(1))
    assert rc == 3 and out["error"]["code"] == "unavailable"
    rc, out = run(capsys, "export", SID, "--to", U(1))
    assert rc == 3 and out["error"]["code"] == "unavailable"


def test_an_explicit_empty_root_is_not_replaced_by_the_environment(tmp_path, capsys,
                                                                   monkeypatch):
    # The environment names a perfectly good root. `--root ""` is still the caller's answer, and
    # the answer is "no root": exit 3, not a quiet read of the environment's root.
    _session(tmp_path)
    monkeypatch.setenv(cli.ENV_ROOT, str(tmp_path))
    rc, out = run(capsys, "chain", SID, "--root", "")
    assert rc == 3 and out["available"] is False
    rc, out = run(capsys, "fork", SID, U(2), "--root", "")
    assert rc == 3 and out["error"]["code"] == "unavailable"
    assert sorted(p.name for p in (tmp_path / "C--work-example-project").iterdir()) == [f"{SID}.jsonl"]


def test_an_internal_failure_is_json_exit_4_not_a_bare_traceback(tmp_path, capsys):
    # A subagent transcript that claims to be gzip but is not: reading it raises something that
    # is not a refusal. The contract still owes JSON on stdout, with a code of its own so a script
    # never mistakes a crash for a refusal, and it must not echo the file's bytes back.
    f = _session(tmp_path)
    sd = f.parent / SID / "subagents"
    sd.mkdir(parents=True)
    (sd / "agent-badgz.jsonl.gz").write_bytes(b"synthetic-not-gzip-marker " * 8)
    rc, out = run(capsys, "chain", SID, "--sub", "badgz", "--root", str(tmp_path))
    assert rc == cli.EXIT_INTERNAL == 4
    assert out["error"]["code"] == "internal"
    assert out["error"]["message"].isidentifier()          # an exception class name, nothing else
    assert "synthetic-not-gzip-marker" not in json.dumps(out)


@pytest.mark.parametrize("argv,code", [
    (["chain", "../etc"], "bad_id"),
    (["chain", SID, "--sub", "../x"], "bad_sub"),
    (["chain", SID, "--leaf", "nope"], "bad_leaf"),
    (["node", SID, "nope"], "bad_uuid"),
    (["export", SID, "--to", "nope"], "bad_uuid"),
    (["fork", SID, "nope"], "bad_uuid"),
])
def test_refusals_exit_1_with_a_stable_code(tmp_path, capsys, argv, code):
    _session(tmp_path)
    rc, out = run(capsys, *argv, "--root", str(tmp_path))
    assert rc == 1 and out["error"]["code"] == code and out["error"]["message"]


def test_not_found_is_a_refusal_not_a_crash(tmp_path, capsys):
    rc, out = run(capsys, "chain", SID, "--root", str(tmp_path))
    assert rc == 1 and out["error"]["code"] == "not_found"


def test_usage_errors_exit_2(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["export", SID])          # --to is required
    assert ei.value.code == 2
    with pytest.raises(SystemExit) as ei:
        cli.main([])
    assert ei.value.code == 2


def test_export_without_out_returns_the_markdown_in_json(tmp_path, capsys):
    _session(tmp_path)
    rc, out = run(capsys, "export", SID, "--to", U(4), "--root", str(tmp_path))
    assert rc == 0 and out["nodes"] == 4 and out["turns"] == 2
    assert "second synthetic answer" in out["text"] and "out" not in out


def test_export_out_writes_markdown_exclusively(tmp_path, capsys):
    _session(tmp_path / "sessions")
    dest = tmp_path / "exports" / "chat.md"
    dest.parent.mkdir()
    rc, out = run(capsys, "export", SID, "--from", U(3), "--to", U(4),
                  "--root", str(tmp_path / "sessions"), "--out", str(dest))
    assert rc == 0 and out["out"] == str(dest) and "text" not in out
    body = dest.read_text(encoding="utf-8")
    assert "second synthetic question" in body and "first synthetic answer" not in body
    # A second export to the same path is refused and the first file is untouched.
    rc, out = run(capsys, "export", SID, "--to", U(2),
                  "--root", str(tmp_path / "sessions"), "--out", str(dest))
    assert rc == 1 and out["error"]["code"] == "exists"
    assert dest.read_text(encoding="utf-8") == body


def test_export_out_refuses_a_git_work_tree(tmp_path, capsys):
    """An export is real conversation text; inside a work tree it is one `git add` from public."""
    _session(tmp_path / "sessions")
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs").mkdir()
    dest = repo / "docs" / "chat.md"
    rc, out = run(capsys, "export", SID, "--to", U(4),
                  "--root", str(tmp_path / "sessions"), "--out", str(dest))
    assert rc == 1 and out["error"]["code"] == "inside_repo"
    assert not dest.exists()
    # Positive control: the same layout without .git is written.
    other = tmp_path / "not-a-repo" / "docs"
    other.mkdir(parents=True)
    rc, out = run(capsys, "export", SID, "--to", U(4),
                  "--root", str(tmp_path / "sessions"), "--out", str(other / "chat.md"))
    assert rc == 0 and (other / "chat.md").is_file()


def test_fork_writes_a_new_file_and_leaves_the_source_alone(tmp_path, capsys):
    src = _session(tmp_path)
    before = src.read_bytes()
    rc, out = run(capsys, "fork", SID, U(2), "--root", str(tmp_path))
    assert rc == 0
    new = tmp_path / "C--work-example-project" / f"{out['newId']}.jsonl"
    assert new.is_file() and out["file"] == str(new)
    assert src.read_bytes() == before
    assert out["command"] == f"cd '{CWD}'; claude --resume {out['newId']}"


def test_module_entry_point_emits_utf8_json(tmp_path):
    """The real process boundary: `python -m convo_chain` with non-ASCII in the transcript.
    A console code page that is not UTF-8 would mangle this if stdout were written as text."""
    d = tmp_path / "proj"
    d.mkdir()
    row = _rec(type="user", uuid=U(1), parentUuid=None,
               message={"role": "user", "content": "合成的问题 \u00e9"})
    (d / f"{SID}.jsonl").write_bytes((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="cp1252",
               PYTHONPATH=os.pathsep.join([os.path.join(os.path.dirname(os.path.dirname(
                   os.path.abspath(__file__))), "src")] + ([os.environ["PYTHONPATH"]]
                                                           if os.environ.get("PYTHONPATH") else [])))
    env[cli.ENV_ROOT] = ""
    p = subprocess.run([sys.executable, "-B", "-m", "convo_chain", "chain", SID,
                        "--root", str(tmp_path)], capture_output=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    out = json.loads(p.stdout.decode("utf-8"))
    assert out["turns"][0]["human"]["preview"] == "合成的问题 \u00e9"
    v = subprocess.run([sys.executable, "-B", "-m", "convo_chain", "--version"],
                       capture_output=True, env=env, timeout=60)
    assert v.returncode == 0 and v.stdout.decode().strip() == "convo-chain 0.1.0"
