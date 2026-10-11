"""Opt-in behaviour: both hooks are a complete no-op outside a workbench project.

A workbench project is a folder whose `.workbench/project.yaml` exists. Everything
else (no `.workbench/`, or a bare `.workbench/` without project.yaml) is "not a
project": exit 0, no output, nothing written, no directory created.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hooks_helpers import GUARD, RECORD, clean_env


def snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def run(script, payload, cwd, project_dir=None, raw=None):
    """Run a hook with CLAUDE_PROJECT_DIR set to `project_dir` or unset (None)."""
    extra = {"CLAUDE_PROJECT_DIR": str(project_dir)} if project_dir is not None else {}
    return subprocess.run(
        [sys.executable, str(script)], input=raw if raw is not None else json.dumps(payload),
        capture_output=True, text=True, env=clean_env(**extra), cwd=str(cwd), timeout=30,
    )


def noop(r):
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""


# `env`: how the hook learns the project root (CLAUDE_PROJECT_DIR vs. only the cwd).
@pytest.fixture(params=["env", "cwd"])
def plain(request, tmp_path):
    """A folder that is not a workbench project, plus a runner bound to it."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "data" / "raw").mkdir(parents=True)
    (root / ".git").mkdir()

    def runner(script, payload, raw=None):
        pd = root if request.param == "env" else None
        return run(script, payload, cwd=root, project_dir=pd, raw=raw)

    runner.root = root
    return runner


def recorder_payloads(root):
    base = {"session_id": "s", "agent_id": "a-1", "agent_type": "agent-workbench:pm-tracker",
            "cwd": str(root)}
    return [
        {**base, "hook_event_name": "SubagentStart"},
        {**base, "hook_event_name": "SubagentStop", "last_assistant_message": "done"},
        {**base, "hook_event_name": "PostToolUseFailure", "tool_name": "Agent",
         "tool_input": {"subagent_type": "x"}, "error": "boom"},
    ]


# --------------------------------------------------------------- (1) / (6) recorder

def test_recorder_noop_without_project_yaml(plain):
    before = snapshot(plain.root)
    for payload in recorder_payloads(plain.root):
        noop(plain(RECORD, payload))
    assert snapshot(plain.root) == before
    assert not (plain.root / ".workbench").exists()


def test_recorder_noop_on_garbage_stdin_outside_project(plain):
    for raw in ("", "not json", "[1,2]", "{"):
        noop(plain(RECORD, None, raw=raw))
    assert not (plain.root / ".workbench").exists()


def test_post_tool_use_failure_noop_outside_project(plain):
    payload = recorder_payloads(plain.root)[2]
    noop(plain(RECORD, payload))
    assert not (plain.root / ".workbench").exists()


# --------------------------------------------------------------- (1) / (5) guard

def _write(root, rel, agent=None, tool="Write", **extra):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(root),
               "tool_input": {"file_path": str(root / rel), "content": "x"}, **extra}
    if agent:
        payload.update(agent_id="a-1", agent_type=agent)
    return payload


def _bash(root, cmd, agent=None):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(root),
               "tool_input": {"command": cmd}}
    if agent:
        payload.update(agent_id="a-1", agent_type=agent)
    return payload


def test_guard_allows_unscoped_subagent_write_outside_project(plain):
    for agent in ("general-purpose", "no-such-agent", "agent-workbench:reviewer"):
        noop(plain(GUARD, _write(plain.root, "src/x.py", agent)))
    assert not (plain.root / ".workbench").exists()


@pytest.mark.parametrize("rel", ["data/raw/x", ".git/x", ".workbench/events-2026-10.jsonl"])
def test_guard_does_not_enforce_protected_paths_outside_project(plain, rel):
    noop(plain(GUARD, _write(plain.root, rel)))                      # main session
    noop(plain(GUARD, _write(plain.root, rel, "general-purpose")))   # subagent
    assert not (plain.root / ".workbench").exists()


@pytest.mark.parametrize("cmd", [
    "rm -rf data/raw",
    "echo x > data/raw/a.csv",
    "rm -rf .git",
    "git push origin main",
    "git commit -m x",
])
def test_bash_guard_noop_outside_project(plain, cmd):
    noop(plain(GUARD, _bash(plain.root, cmd)))
    noop(plain(GUARD, _bash(plain.root, cmd, "general-purpose")))
    assert not (plain.root / ".workbench").exists()


def test_guard_noop_on_garbage_stdin_outside_project(plain):
    noop(plain(GUARD, None, raw="{{ not json"))
    assert not (plain.root / ".workbench").exists()


# --------------------------------------------------------------- (4) bare .workbench/

def test_workbench_dir_without_project_yaml_is_still_noop(tmp_path):
    root = tmp_path / "bare"
    (root / ".workbench").mkdir(parents=True)
    (root / "data" / "raw").mkdir(parents=True)
    before = snapshot(root)
    for pd in (root, None):
        for payload in recorder_payloads(root):
            noop(run(RECORD, payload, cwd=root, project_dir=pd))
        noop(run(GUARD, _write(root, "data/raw/x", "general-purpose"), cwd=root, project_dir=pd))
        noop(run(GUARD, _bash(root, "rm -rf data/raw"), cwd=root, project_dir=pd))
        noop(run(RECORD, None, cwd=root, project_dir=pd, raw="{ broken"))
    assert snapshot(root) == before  # no events file, no hook-errors.log


# --------------------------------------------------------------- (2) / (3) inside a project

@pytest.fixture()
def wb_project(tmp_path):
    root = tmp_path / "wbproj"
    (root / ".workbench").mkdir(parents=True)
    (root / ".workbench" / "project.yaml").write_text(
        "phases:\n  - id: p1-research\n    status: doing\n", encoding="utf-8")
    (root / "docs" / "deep").mkdir(parents=True)
    (root / "data" / "raw").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return root, elsewhere


def test_root_found_via_ancestor_of_payload_cwd_when_env_unset(wb_project):
    root, elsewhere = wb_project
    sub = root / "docs" / "deep"

    payload = {"hook_event_name": "SubagentStart", "agent_id": "a-1", "agent_type": "t",
               "session_id": "s", "cwd": str(sub)}
    noop(run(RECORD, payload, cwd=elsewhere, project_dir=None))
    events = [json.loads(line) for f in (root / ".workbench").glob("events-*.jsonl")
              for line in f.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "agent_start" and events[0]["phase"] == "p1-research"
    assert not (sub / ".workbench").exists()

    # guard: protected path denied, unscoped subagent fail-closed (behaviour unchanged)
    r = run(GUARD, {**_write(root, "data/raw/x"), "cwd": str(sub)}, cwd=elsewhere, project_dir=None)
    assert r.returncode == 2 and "data/raw" in r.stderr
    r = run(GUARD, {**_write(root, "docs/a.md", "general-purpose"), "cwd": str(sub)},
            cwd=elsewhere, project_dir=None)
    assert r.returncode == 2 and "write_scope" in r.stderr
    assert not (elsewhere / ".workbench").exists()


def test_root_found_via_process_cwd_when_payload_has_no_cwd(wb_project):
    root, _ = wb_project
    payload = {"hook_event_name": "SubagentStart", "agent_id": "a-1", "agent_type": "t"}
    noop(run(RECORD, payload, cwd=root / "docs" / "deep", project_dir=None))
    assert len(list((root / ".workbench").glob("events-*.jsonl"))) == 1


def test_env_project_dir_without_project_yaml_has_no_ancestor_fallback(wb_project, tmp_path):
    root, _ = wb_project
    not_project = tmp_path / "not-a-project"
    not_project.mkdir()
    payload = {"hook_event_name": "SubagentStart", "agent_id": "a-1", "agent_type": "t",
               "cwd": str(root / "docs" / "deep")}  # cwd lies inside a real project
    noop(run(RECORD, payload, cwd=root, project_dir=not_project))
    noop(run(GUARD, {**_write(root, "data/raw/x"), "cwd": str(root)}, cwd=root,
             project_dir=not_project))
    assert list((root / ".workbench").glob("events-*.jsonl")) == []
    assert snapshot(not_project) == []


def test_guard_still_fail_closed_and_errors_logged_inside_project(wb_project):
    root, _ = wb_project
    r = run(GUARD, _write(root, "src/x.py", "general-purpose"), cwd=root, project_dir=root)
    assert r.returncode == 2 and "write_scope" in r.stderr
    r = run(GUARD, None, cwd=root, project_dir=root, raw="{{ not json")
    assert r.returncode == 0
    assert "write_guard" in (root / ".workbench" / "hook-errors.log").read_text(encoding="utf-8")
