from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime

import pytest

from hooks_helpers import PHASES_YAML, RECORD, unix_home

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def start_payload(**kw):
    p = {"session_id": "s1", "hook_event_name": "SubagentStart",
         "agent_id": "agent-1", "agent_type": "agent-workbench:pm-tracker", "cwd": "."}
    p.update(kw)
    return p


def make_transcript(path, rows):
    """rows: list of (requestId|None, message_id, model, input, output[, cache_creation, cache_read])."""
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})]
    for row in rows:
        req, mid, model, i, o = row[:5]
        cc, cr = row[5:7] if len(row) >= 7 else (999, 999)
        entry = {"type": "assistant",
                 "message": {"id": mid, "role": "assistant", "model": model,
                             "usage": {"input_tokens": i, "output_tokens": o,
                                       "cache_creation_input_tokens": cc,
                                       "cache_read_input_tokens": cr},
                             "content": []}}
        if req:
            entry["requestId"] = req
        lines.append(json.dumps(entry))
    lines.insert(2, "not json at all")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def stop_with_transcript(project, tr):
    project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                         "agent_transcript_path": str(tr), "last_assistant_message": "ok"})
    return project.events()[-1]["usage"]


def test_start_event_fields(project):
    r = project.run(RECORD, start_payload())
    assert r.returncode == 0 and r.stdout == ""
    (ev,) = project.events()
    assert list(ev) == ["v", "ts", "event", "session_id", "agent_id", "agent",
                        "phase", "gate", "summary", "rev", "usage"]
    assert ev["v"] == 1 and ev["event"] == "agent_start"
    assert TS_RE.match(ev["ts"])
    assert ev["agent"] == "agent-workbench:pm-tracker"  # namespace kept
    assert ev["agent_id"] == "agent-1" and ev["session_id"] == "s1"
    assert ev["gate"] is None and ev["summary"] is None
    assert ev["phase"] is None and ev["rev"] is None and ev["usage"] is None


@pytest.mark.parametrize("agent_type", ["", "   ", None])
def test_unnamed_helper_agents_are_not_recorded(project, agent_type):
    # observed live: Claude Code's internal helper agents stop with an empty agent_type
    payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "h-1",
               "agent_type": agent_type, "last_assistant_message": "free text"}
    if agent_type is None:
        del payload["agent_type"]
    r = project.run(RECORD, payload)
    assert r.returncode == 0 and r.stdout == ""
    assert project.events() == []
    project.run(RECORD, start_payload(agent_id="a-9", agent_type="data-engineer"))
    assert [e["agent"] for e in project.events()] == ["data-engineer"]


def test_file_name_uses_local_month(project):
    project.run(RECORD, start_payload())
    names = [p.name for p in (project.root / ".workbench").glob("events-*.jsonl")]
    assert names == [f"events-{datetime.now().strftime('%Y-%m')}.jsonl"]


def test_start_stop_pair_by_agent_id(project):
    project.run(RECORD, start_payload(agent_id="a-1", agent_type="data-engineer"))
    project.run(RECORD, start_payload(agent_id="a-2", agent_type="reviewer"))
    project.run(RECORD, {"hook_event_name": "SubagentStop", "session_id": "s1",
                         "agent_id": "a-1", "agent_type": "data-engineer",
                         "last_assistant_message": "done", "stop_hook_active": False})
    evs = project.events()
    assert [e["event"] for e in evs] == ["agent_start", "agent_start", "agent_stop"]
    assert evs[2]["agent_id"] == evs[0]["agent_id"] == "a-1"
    assert evs[2]["summary"] == "done"


def test_summary_mask_collapse_and_cap(project):
    msg = f"Wrote   {unix_home()} and\n\tC:\\Users\\bob\\x.csv then " + "x" * 400
    project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                         "last_assistant_message": msg})
    s = project.events()[0]["summary"]
    assert s.startswith("Wrote <path> and <path> then xxx")
    assert len(s) == 200
    assert "Users" not in s and "\n" not in s and "\t" not in s


def test_mask_leaves_relative_paths_and_urls(project):
    project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                         "last_assistant_message": "see docs/research/a.md and https://example.com/x"})
    assert project.events()[0]["summary"] == "see docs/research/a.md and https://example.com/x"


def test_usage_deduplicates_requests(project, tmp_path):
    tr = make_transcript(tmp_path / "agent.jsonl", [
        ("req_A", "msg_1", "claude-sonnet-x", 10, 5, 100, 0),
        ("req_A", "msg_1", "claude-sonnet-x", 10, 5, 100, 0),
        ("req_A", "msg_1", "claude-sonnet-x", 10, 5, 100, 0),
        ("req_B", "msg_2", "claude-sonnet-x", 100, 1, 50, 100),   # streaming: counters grow
        ("req_B", "msg_2", "claude-sonnet-x", 100, 40, 60, 100),
        ("req_B", "msg_2", "claude-sonnet-x", 100, 25, 55, 90),   # later entry is lower: max wins
        (None, "msg_3", "claude-sonnet-x", 7, 3, 20, 160),         # fallback to message.id
        (None, "msg_3", "claude-sonnet-x", 7, 3, 20, 160),
    ])
    assert stop_with_transcript(project, tr) == {
        "model": "claude-sonnet-x",
        "input_tokens": 10 + 100 + 7,
        "output_tokens": 5 + 40 + 3,
        "cache_creation_input_tokens": 100 + 60 + 20,
        "cache_read_input_tokens": 0 + 100 + 160,
    }


def test_usage_shape_mirrors_real_subagent_transcript(project, tmp_path):
    # Same shape as a measured real transcript: 6 requests, each repeated 2-7 times,
    # input_tokens tiny, nearly everything in the cache counters.
    reqs = [("req_1", 2, 2, 27224, 0, 7), ("req_2", 2, 8, 30187, 27224, 5),
            ("req_3", 2, 2, 26752, 57411, 4), ("req_4", 2, 4, 17496, 84163, 2),
            ("req_5", 2, 6, 3952, 101659, 3), ("req_6", 2, 2, 17534, 105611, 2)]
    rows = [(r, "m" + r, "claude-haiku-x", i, o, cc, cr)
            for r, i, o, cc, cr, n in reqs for _ in range(n)]
    usage = stop_with_transcript(project, make_transcript(tmp_path / "real-shape.jsonl", rows))
    assert usage == {"model": "claude-haiku-x", "input_tokens": 12, "output_tokens": 24,
                     "cache_creation_input_tokens": 123145, "cache_read_input_tokens": 376068}
    assert list(usage) == ["model", "input_tokens", "output_tokens",
                           "cache_creation_input_tokens", "cache_read_input_tokens"]


def test_usage_missing_cache_fields_count_as_zero(project, tmp_path):
    tr = tmp_path / "old.jsonl"
    tr.write_text(json.dumps({"requestId": "r", "message": {
        "role": "assistant", "model": "m", "usage": {"input_tokens": 4, "output_tokens": 9}}}) + "\n")
    assert stop_with_transcript(project, tr) == {
        "model": "m", "input_tokens": 4, "output_tokens": 9,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_usage_ignores_non_assistant_entries(project, tmp_path):
    tr = tmp_path / "mixed.jsonl"
    rows = [
        {"requestId": "u", "message": {"role": "user", "model": "m",
                                       "usage": {"input_tokens": 500, "output_tokens": 500}}},
        {"requestId": "a", "message": {"role": "assistant", "model": "m",
                                       "usage": {"input_tokens": 1, "output_tokens": 2,
                                                 "cache_creation_input_tokens": 3,
                                                 "cache_read_input_tokens": 4}}},
    ]
    tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert stop_with_transcript(project, tr) == {
        "model": "m", "input_tokens": 1, "output_tokens": 2,
        "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}


@pytest.mark.parametrize("kind", ["missing", "garbage", "empty", "no-usage"])
def test_usage_null_on_bad_transcript(project, tmp_path, kind):
    tr = tmp_path / "t.jsonl"
    if kind == "garbage":
        tr.write_bytes(b"\xff\xfe not json \x00\n{{{")
    elif kind == "empty":
        tr.write_text("")
    elif kind == "no-usage":
        tr.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant"}}) + "\n")
    r = project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                             "agent_transcript_path": str(tr), "last_assistant_message": "ok"})
    assert r.returncode == 0
    (ev,) = project.events()
    assert ev["event"] == "agent_stop" and ev["usage"] is None


def test_agent_error_from_agent_tool_failure(project):
    project.run(RECORD, {"hook_event_name": "PostToolUseFailure", "session_id": "s1",
                         "tool_name": "Agent",
                         "tool_input": {"subagent_type": "data-engineer", "prompt": "SECRET PROMPT"},
                         "error": f"boom at {unix_home()}", "is_interrupt": False})
    (ev,) = project.events()
    assert ev["event"] == "agent_error" and ev["agent"] == "data-engineer"
    assert ev["summary"] == "boom at <path>"
    assert "SECRET PROMPT" not in json.dumps(ev)


def test_other_tool_failures_are_ignored(project):
    project.run(RECORD, {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                         "tool_input": {"command": "false"}, "error": "Exit code 1"})
    assert project.events() == []


@pytest.mark.parametrize("simple", [False, True], ids=["pyyaml", "fallback-parser"])
def test_phase_from_project_yaml(project, simple):
    project.write(".workbench/project.yaml", PHASES_YAML)
    env = {"WORKBENCH_SIMPLE_YAML": "1"} if simple else None
    project.run(RECORD, start_payload(), env_extra=env)
    assert project.events()[0]["phase"] == "p3-data"


def test_phase_none_when_nothing_doing(project):
    project.write(".workbench/project.yaml",
                  "phases:\n  - id: p1\n    status: done\n  - id: p2\n    status: todo\n")
    project.run(RECORD, start_payload())
    project.run(RECORD, start_payload(), env_extra={"WORKBENCH_SIMPLE_YAML": "1"})
    assert [e["phase"] for e in project.events()] == [None, None]


def _phases_yaml(*pairs):
    """project.yaml text with phases given as (id, status) pairs, one nested task each."""
    lines = ["project:", "  id: demo", "  name: Demo", "agents: []", "phases:"]
    for pid, status in pairs:
        lines += [f"  - id: {pid}", f"    name: {pid}", f"    status: {status}",
                  "    tasks:", "      - {id: t1, title: x, status: doing, agent: a}"]
    return "\n".join(lines) + "\n"


# decision 12-1 (1): doing > the single review phase > null
PHASE_RULE_CASES = [
    pytest.param([("p1", "done"), ("p2", "review"), ("p3", "todo")], "p2", id="exactly-one-review"),
    pytest.param([("p1", "done"), ("p2", "review"), ("p3", "doing")], "p3", id="doing-preferred-over-review"),
    pytest.param([("p1", "review"), ("p2", "doing"), ("p3", "review")], "p2", id="doing-wins-with-two-reviews"),
    pytest.param([("p1", "review"), ("p2", "review"), ("p3", "todo")], None, id="two-reviews-is-null"),
    pytest.param([("p1", "done"), ("p2", "todo"), ("p3", "blocked")], None, id="none-is-null"),
    pytest.param([("p1", "done"), ("p2", "REVIEW")], "p2", id="status-case-insensitive"),
]


@pytest.mark.parametrize("simple", [False, True], ids=["pyyaml", "fallback-parser"])
@pytest.mark.parametrize("phases, expected", PHASE_RULE_CASES)
def test_phase_rule_doing_then_single_review(project, simple, phases, expected):
    project.write(".workbench/project.yaml", _phases_yaml(*phases))
    env = {"WORKBENCH_SIMPLE_YAML": "1"} if simple else None
    project.run(RECORD, start_payload(), env_extra=env)
    assert project.events()[0]["phase"] == expected


def test_phase_rule_helper_in_process(monkeypatch):
    """The pure rule, independent of any parser; a lone malformed `doing` still wins."""
    import importlib
    import sys as _sys

    from hooks_helpers import HOOKS

    monkeypatch.syspath_prepend(str(HOOKS))
    _sys.modules.pop("wbcommon", None)
    wb = importlib.import_module("wbcommon")
    assert wb.pick_phase([("a", "review"), ("b", "review")]) is None
    assert wb.pick_phase([("a", "review")]) == "a"
    assert wb.pick_phase([("a", "review"), ("b", "doing")]) == "b"
    assert wb.pick_phase([(None, "doing"), ("b", "review")]) is None
    assert wb.pick_phase([]) is None
    text = _phases_yaml(("p1", "review"), ("p2", "review"))
    assert wb.phase_from_text(text) is None
    assert wb.phases_from_text(text) == [("p1", "review"), ("p2", "review")]


def test_phase_survives_broken_yaml_via_fallback(project):
    # unparseable for PyYAML (tab indentation), still readable for the tolerant parser
    project.write(".workbench/project.yaml",
                  "phases:\n  - id: p2\n    status: doing\n\tbroken: [\n")
    project.run(RECORD, start_payload())
    assert project.events()[0]["phase"] == "p2"


def test_rev_in_git_repo(project):
    def git(*a):
        return subprocess.run(["git", "-C", str(project.root), "-c", "user.name=t",
                               "-c", "user.email=t@example.com", *a],
                              capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q")
    project.run(RECORD, start_payload())          # no commits yet -> null
    (project.root / "f.txt").write_text("x")
    git("add", "f.txt")
    git("commit", "-q", "-m", "init")
    project.run(RECORD, start_payload())
    evs = project.events()
    assert evs[0]["rev"] is None
    assert evs[1]["rev"] == git("rev-parse", "--short", "HEAD")


@pytest.mark.parametrize("raw", ["", "not json", "[1,2]", "\xff\xfe", "{"])
def test_fail_soft_on_garbage_stdin(project, raw):
    r = project.run(RECORD, None, raw=raw)
    assert r.returncode == 0
    assert project.events() == []


def test_fail_soft_logs_error_without_payload_text(project):
    r = project.run(RECORD, None, raw="SECRET-TEXT {")
    assert r.returncode == 0
    log = (project.root / ".workbench" / "hook-errors.log").read_text()
    assert "record_event" in log and "SECRET-TEXT" not in log


def test_fail_soft_when_events_file_unwritable(project):
    # the events file name for this month is occupied by a directory -> os.open fails (EISDIR)
    month = datetime.now().astimezone().strftime("%Y-%m")
    (project.root / ".workbench" / f"events-{month}.jsonl").mkdir()
    r = project.run(RECORD, start_payload())
    assert r.returncode == 0
    assert r.stdout == ""
    log = (project.root / ".workbench" / "hook-errors.log").read_text()
    assert "record_event" in log


def test_concurrent_appends_stay_whole_lines(project):
    n = 20
    big = "word " * 400
    procs = []
    for i in range(n):
        payload = {"hook_event_name": "SubagentStop", "agent_id": f"a-{i}", "agent_type": "t",
                   "last_assistant_message": big, "session_id": "s"}
        p = subprocess.Popen([sys.executable, str(RECORD)], stdin=subprocess.PIPE, text=True,
                             env=project.env(), cwd=str(project.root))
        p.stdin.write(json.dumps(payload))
        p.stdin.close()
        procs.append(p)
    assert all(p.wait(timeout=60) == 0 for p in procs)
    evs = project.events()  # json.loads on every line: interleaving would raise
    assert len(evs) == n
    assert {e["agent_id"] for e in evs} == {f"a-{i}" for i in range(n)}
