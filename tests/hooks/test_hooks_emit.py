from __future__ import annotations

import subprocess
import sys

from hooks_helpers import PHASES_YAML, SRC, unix_home


def emit(project, *args, **env):
    e = project.env(PYTHONPATH=str(SRC), **env)
    return subprocess.run([sys.executable, "-m", "agent_workbench.emit", *args],
                          capture_output=True, text=True, env=e, cwd=str(project.root), timeout=30)


def test_gate_requested_line(project):
    project.write(".workbench/project.yaml", PHASES_YAML)
    r = emit(project, "gate_requested", "--gate", "dataset-selection",
             "--summary", f"candidates in {unix_home()}", CLAUDE_SESSION_ID="sess-9")
    assert r.returncode == 0, r.stderr
    (ev,) = project.events()
    assert list(ev) == ["v", "ts", "event", "session_id", "agent_id", "agent",
                        "phase", "gate", "summary", "rev", "usage"]
    assert ev["event"] == "gate_requested" and ev["agent"] == "orchestrator"
    assert ev["gate"] == "dataset-selection" and ev["agent_id"] is None
    assert ev["session_id"] == "sess-9"
    assert ev["phase"] == "p3-data"
    assert ev["summary"] == "candidates in <path>"
    assert ev["usage"] is None and ev["v"] == 1


def test_phase_override_and_optional_gate_for_change_events(project):
    for event in ("change_requested", "change_approved"):
        r = emit(project, event, "--phase", "p2-ax")
        assert r.returncode == 0, r.stderr
    evs = project.events()
    assert [e["event"] for e in evs] == ["change_requested", "change_approved"]
    assert all(e["phase"] == "p2-ax" and e["gate"] is None and e["session_id"] is None for e in evs)


def test_gate_events_require_gate(project):
    for event in ("gate_requested", "gate_approved"):
        r = emit(project, event)
        assert r.returncode != 0 and "--gate" in r.stderr
    assert project.events() == []


def test_unknown_event_is_rejected(project):
    r = emit(project, "agent_start")
    assert r.returncode != 0
    assert project.events() == []


def test_project_found_from_subdirectory_without_env(project):
    sub = project.root / "docs" / "deep"
    sub.mkdir(parents=True)
    env = project.env(PYTHONPATH=str(SRC))
    env.pop("CLAUDE_PROJECT_DIR")
    r = subprocess.run([sys.executable, "-m", "agent_workbench.emit", "change_requested"],
                       capture_output=True, text=True, env=env, cwd=str(sub), timeout=30)
    assert r.returncode == 0, r.stderr
    assert len(project.events()) == 1
    assert not (sub / ".workbench").exists()
