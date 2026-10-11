"""Contract validators: valid examples pass, targeted invalid inputs fail."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from agent_workbench.contract import (
    glob_match,
    glob_matches,
    validate_event,
    validate_events_file,
    validate_project,
    validate_review,
    validate_workspace,
)
from agent_workbench.contract.__main__ import detect_type, main

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
EVENTS_FILE = EXAMPLES / "events-2026-10.jsonl"


def example_events() -> list[dict]:
    return [json.loads(line) for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines()]


def example_project() -> dict:
    return yaml.safe_load((EXAMPLES / "project.yaml").read_text(encoding="utf-8"))


def usage(**overrides) -> dict:
    data = {
        "model": "sonnet",
        "input_tokens": 12,
        "output_tokens": 340,
        "cache_creation_input_tokens": 41000,
        "cache_read_input_tokens": 480000,
    }
    data.update(overrides)
    return data


def base_event(**overrides) -> dict:
    event = {
        "v": 1,
        "ts": "2026-10-07T09:00:05+09:00",
        "event": "agent_start",
        "session_id": "s-1",
        "agent_id": "a-1",
        "agent": "data-prep",
        "phase": "p3-data",
        "gate": None,
        "summary": None,
        "rev": "9b2f4d6",
        "usage": None,
    }
    event.update(overrides)
    return event


# --- valid examples --------------------------------------------------------


def test_example_events_file_is_valid():
    assert validate_events_file(EVENTS_FILE) == []


def test_example_events_cover_the_scenarios():
    events = example_events()
    assert len(events) >= 15
    assert {e["event"] for e in events} == {
        "agent_start", "agent_stop", "agent_error",
        "gate_requested", "gate_approved", "change_requested", "change_approved",
    }
    assert any(e["event"] == "agent_error" for e in events)
    assert any(e["usage"] for e in events)
    for e in events:  # usage only on agent_stop, always with all five keys
        if e["usage"] is not None:
            assert e["event"] == "agent_stop"
            assert set(e["usage"]) >= set(usage())
    assert any(e["event"] == "agent_stop" and e["usage"] is None for e in events), "expected one failed extraction"
    started = {e["agent_id"] for e in events if e["event"] == "agent_start"}
    ended = {e["agent_id"] for e in events if e["event"] in ("agent_stop", "agent_error")}
    assert started - ended, "expected one agent_start without a stop (stalled)"


def test_example_project_is_valid():
    assert validate_project(EXAMPLES / "project.yaml") == []


@pytest.mark.parametrize("name", ["p3-data-1.json", "p3-data-2.json"])
def test_example_review_is_valid(name):
    assert validate_review(EXAMPLES / "reviews" / name) == []


def test_example_reviews_show_both_verdicts():
    verdicts = {
        json.loads(p.read_text(encoding="utf-8"))["verdict"] for p in (EXAMPLES / "reviews").glob("*.json")
    }
    assert verdicts == {"pass", "needs_change"}


def test_example_workspace_is_valid():
    assert validate_workspace(EXAMPLES / "workspace.example.yaml") == []


def test_validate_project_accepts_dict():
    assert validate_project(example_project()) == []


def test_minimal_event_is_valid():
    assert validate_event({"v": 1, "ts": "2026-10-11T00:00:00Z", "event": "gate_requested", "gate": "phase-end"}) == []


def test_usage_with_all_five_keys_is_valid_on_agent_stop():
    assert validate_event(base_event(event="agent_stop", usage=usage())) == []


def test_usage_model_may_be_null():
    assert validate_event(base_event(event="agent_stop", usage=usage(model=None))) == []


def test_usage_null_on_agent_stop_is_valid():
    assert validate_event(base_event(event="agent_stop", usage=None)) == []


def test_unknown_event_fields_are_allowed():
    assert validate_event(base_event(future_field=123)) == []


# --- invalid events --------------------------------------------------------


@pytest.mark.parametrize(
    "event, expected",
    [
        (base_event(event="agent_pause"), "$.event"),
        (base_event(v=2), "$.v"),
        (base_event(ts="2026-10-07 09:00:05"), "$.ts"),
        (base_event(ts="2026-10-07T09:00:05"), "$.ts"),  # no timezone
        (base_event(ts="2026-13-40T09:00:05+09:00"), "$.ts"),  # shape ok, not a real date
        (base_event(rev="HEAD"), "$.rev"),
        (base_event(summary="x" * 201), "$.summary"),
        (base_event(agent_id=None), "agent_id"),  # lifecycle event needs agent_id
        (base_event(usage=usage()), "$.usage"),  # usage on start
        (base_event(event="agent_stop", usage=usage(input_tokens=-1)), "$.usage.input_tokens"),
        (base_event(event="agent_stop", usage=usage(cache_read_input_tokens=-1)), "$.usage.cache_read_input_tokens"),
        (
            base_event(event="agent_stop", usage=usage(cache_creation_input_tokens=1.5)),
            "$.usage.cache_creation_input_tokens",
        ),
        (base_event(event="agent_stop", usage=usage(output_tokens="9")), "$.usage.output_tokens"),
        (base_event(event="agent_stop", usage=usage(model="")), "$.usage.model"),
        (base_event(event="agent_error", usage=usage()), "$.usage"),
        (base_event(event="gate_requested", gate="phase-end", usage=usage()), "$.usage"),
        (base_event(gate="phase-end"), "$.gate"),  # gate value on a non-gate event
        (
            {"v": 1, "ts": "2026-10-07T09:00:05+09:00", "event": "gate_requested"},
            "gate",
        ),  # gate event without gate
        (
            {"v": 1, "ts": "2026-10-07T09:00:05+09:00", "event": "gate_approved", "gate": None},
            "$.gate",
        ),
    ],
)
def test_invalid_event(event, expected):
    errors = validate_event(event)
    assert errors, "expected validation errors"
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize("missing", ["v", "ts", "event"])
def test_missing_required_event_field(missing):
    event = base_event()
    del event[missing]
    errors = validate_event(event)
    assert any(missing in e for e in errors), errors


@pytest.mark.parametrize(
    "missing",
    ["model", "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"],
)
def test_usage_requires_all_five_keys(missing):
    data = usage()
    del data[missing]
    errors = validate_event(base_event(event="agent_stop", usage=data))
    assert any(f"'{missing}' is a required property" in e for e in errors), errors


def test_old_three_key_usage_is_rejected():
    old = {"model": "sonnet", "input_tokens": 1, "output_tokens": 2}
    errors = validate_event(base_event(event="agent_stop", usage=old))
    assert any("cache_creation_input_tokens" in e for e in errors)
    assert any("cache_read_input_tokens" in e for e in errors)


@pytest.mark.parametrize("missing", ["session_id", "agent_id", "agent"])
def test_lifecycle_event_requires_hook_fields(missing):
    event = base_event(event="agent_stop")
    del event[missing]
    assert any(missing in e for e in validate_event(event))


def test_agent_error_may_have_null_agent_id():
    # observed live: an unknown subagent type fails before any agent starts, so there is no agent_id
    assert validate_event(base_event(event="agent_error", agent_id=None)) == []
    assert validate_event(base_event(event="agent_stop", agent_id=None)) != []
    assert validate_event(base_event(event="agent_start", agent_id=None)) != []


def test_non_object_event():
    assert validate_event(["not", "an", "object"])


def test_events_file_reports_line_numbers(tmp_path):
    good = json.dumps(base_event())
    bad = json.dumps(base_event(event="nope"))
    path = tmp_path / "events-2026-10.jsonl"
    path.write_text(f"{good}\n\n{bad}\n{{not json\n", encoding="utf-8")
    errors = validate_events_file(path)
    assert any(e.startswith("line 3:") and "$.event" in e for e in errors)
    assert any(e.startswith("line 4:") and "invalid JSON" in e for e in errors)
    assert not any(e.startswith(("line 1:", "line 2:")) for e in errors)


def test_events_file_missing(tmp_path):
    assert validate_events_file(tmp_path / "nope.jsonl")


# --- invalid project -------------------------------------------------------


def mutate_project(fn) -> list[str]:
    data = copy.deepcopy(example_project())
    fn(data)
    return validate_project(data)


def test_project_bad_status_enum():
    errors = mutate_project(lambda d: d["phases"][0].update(status="finished"))
    assert any("$.phases[0].status" in e for e in errors)


def test_project_bad_gate_enum():
    errors = mutate_project(lambda d: d["phases"][0].update(gate="whenever"))
    assert any("$.phases[0].gate" in e for e in errors)


def test_project_missing_required_field():
    errors = mutate_project(lambda d: d["phases"][1].pop("name"))
    assert any("'name' is a required property" in e for e in errors)


def test_project_unknown_field_rejected():
    errors = mutate_project(lambda d: d["phases"][0].update(owner="someone"))
    assert any("owner" in e for e in errors)


@pytest.mark.parametrize("path", ["../secrets.txt", "docs/../../x.md", "/etc/passwd", "C:\\x.md", "~/notes.md"])
def test_project_bad_deliverable_path(path):
    errors = mutate_project(lambda d: d["phases"][0]["deliverables"].append(path))
    assert any("deliverables" in e for e in errors), errors


@pytest.mark.parametrize("pattern", ["notebooks/*.ipynb", "results/p5-forecast/scenario-*", "docs/**", "docs/**/*.md"])
def test_project_glob_deliverables_are_valid(pattern):
    data = copy.deepcopy(example_project())
    data["phases"][0]["deliverables"].append(pattern)
    assert validate_project(data) == []


def test_project_unknown_agent_in_phase():
    errors = mutate_project(lambda d: d["phases"][0]["agents"].append("ghost"))
    assert any("unknown agent 'ghost'" in e and ".agents[" in e for e in errors)


def test_project_unknown_agent_in_task():
    errors = mutate_project(lambda d: d["phases"][2]["tasks"][0].update(agent="ghost"))
    assert any("unknown agent 'ghost'" in e and ".tasks[0].agent" in e for e in errors)


def test_project_duplicate_ids():
    def dup(d):
        d["agents"].append(dict(d["agents"][0]))
        d["phases"].append(dict(d["phases"][0], status="todo"))
        d["phases"][2]["tasks"].append(dict(d["phases"][2]["tasks"][0]))

    errors = mutate_project(dup)
    assert any("duplicate agent id" in e for e in errors)
    assert any("duplicate phase id" in e for e in errors)
    assert any("duplicate task id" in e for e in errors)


def test_project_two_doing_phases():
    errors = mutate_project(lambda d: d["phases"][3].update(status="doing"))
    assert any("at most one phase" in e for e in errors)


# --- optional review / fieldbook sections ---------------------------------


def test_example_projects_have_review_and_fieldbook_sections():
    data = example_project()
    assert data["review"]["checks"] and data["review"]["sources"]
    assert data["fieldbook"]["title"]
    demo = EXAMPLES / "demo-project" / ".workbench" / "project.yaml"
    assert validate_project(demo) == []


def test_project_without_review_and_fieldbook_is_valid():
    data = copy.deepcopy(example_project())
    del data["review"], data["fieldbook"]
    assert validate_project(data) == []


def test_project_empty_review_and_fieldbook_are_valid():
    data = copy.deepcopy(example_project())
    data["review"] = {}
    data["fieldbook"] = {}
    assert validate_project(data) == []


def test_project_review_check_without_phases_applies_everywhere_and_is_valid():
    data = copy.deepcopy(example_project())
    data["review"] = {"checks": [{"id": "c1", "text": "check something"}]}
    assert validate_project(data) == []


def test_project_review_unknown_keys_rejected():
    assert any("owner" in e for e in mutate_project(lambda d: d["review"].update(owner="x")))
    assert any("severity" in e for e in mutate_project(lambda d: d["review"]["checks"][0].update(severity="high")))
    assert any("subtitle" in e for e in mutate_project(lambda d: d["fieldbook"].update(subtitle="x")))
    assert any("extra" in e for e in mutate_project(lambda d: d.update(extra={})))


def test_project_review_check_requires_id_and_text():
    errors = mutate_project(lambda d: d["review"]["checks"][0].pop("id"))
    assert any("'id' is a required property" in e for e in errors)
    errors = mutate_project(lambda d: d["review"]["checks"][0].pop("text"))
    assert any("'text' is a required property" in e for e in errors)


@pytest.mark.parametrize("check_id", ["has space", "dot.id", "", "slash/id", "한글"])
def test_project_review_check_bad_id(check_id):
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(id=check_id))
    assert any("$.review.checks[0].id" in e for e in errors), errors


def test_project_review_check_id_may_start_with_underscore_or_dash():
    data = copy.deepcopy(example_project())
    data["review"]["checks"][0]["id"] = "_a-1"
    data["review"]["checks"][1]["id"] = "-b_2"
    assert validate_project(data) == []


def test_project_review_duplicate_check_ids():
    errors = mutate_project(lambda d: d["review"]["checks"][1].update(id=d["review"]["checks"][0]["id"]))
    assert any("duplicate check id" in e for e in errors)


def test_project_review_check_unknown_phase():
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(phases=["p4-analysis", "p9-ghost"]))
    assert any("$.review.checks[0].phases[1]" in e and "unknown phase 'p9-ghost'" in e for e in errors)


def test_project_review_check_duplicate_phase_and_empty_phases():
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(phases=["p4-analysis", "p4-analysis"]))
    assert any("duplicate phase id" in e for e in errors)
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(phases=[]))
    assert any("$.review.checks[0].phases" in e for e in errors)


def test_project_review_check_blank_text():
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(text="   "))
    assert any("$.review.checks[0].text" in e for e in errors)
    errors = mutate_project(lambda d: d["review"]["checks"][0].update(text=""))
    assert any("$.review.checks[0].text" in e for e in errors)


@pytest.mark.parametrize("path", ["../secrets/**", "docs/../../x.md", "/etc/passwd", "C:\\x\\y", "~/notes/**", "   "])
def test_project_review_bad_sources(path):
    errors = mutate_project(lambda d: d["review"]["sources"].append(path))
    assert any("$.review.sources[" in e for e in errors), errors


@pytest.mark.parametrize("pattern", ["docs/research/**", "docs/**/*.md", "notebooks/*.ipynb", "README.md"])
def test_project_review_good_sources(pattern):
    data = copy.deepcopy(example_project())
    data["review"]["sources"].append(pattern)
    assert validate_project(data) == []


def test_project_review_sources_must_be_strings():
    errors = mutate_project(lambda d: d["review"].update(sources=[1]))
    assert any("$.review.sources[0]" in e for e in errors)
    errors = mutate_project(lambda d: d["review"].update(sources="docs/**"))
    assert any("$.review.sources" in e for e in errors)


@pytest.mark.parametrize("title", ["", "   ", 3, None, ["x"]])
def test_project_fieldbook_title_must_be_non_empty_string(title):
    errors = mutate_project(lambda d: d["fieldbook"].update(title=title))
    assert any("$.fieldbook.title" in e for e in errors), errors


def test_project_review_errors_do_not_hide_schema_errors_elsewhere():
    def both(d):
        d["review"]["checks"][0]["phases"] = ["p9-ghost"]
        d["phases"][0].update(status="finished")

    errors = mutate_project(both)
    assert any("$.phases[0].status" in e for e in errors)


def test_project_invalid_yaml(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text("project: [unclosed", encoding="utf-8")
    assert any("invalid YAML" in e for e in validate_project(path))


# --- invalid review / workspace -------------------------------------------


def review(**overrides) -> dict:
    data = {"verdict": "pass", "findings": [], "counter_evidence_count": 1}
    data.update(overrides)
    return data


def test_review_bad_verdict_and_count():
    assert any("$.verdict" in e for e in validate_review(review(verdict="maybe")))
    assert any("counter_evidence_count" in e for e in validate_review(review(counter_evidence_count=-1)))


def test_review_finding_requires_fields():
    errors = validate_review(review(findings=[{"category": "x", "severity": "low"}]))
    assert any("'resolved' is a required property" in e for e in errors)


@pytest.mark.parametrize("severity", ["high", "medium", "low"])
def test_review_severity_values_are_valid(severity):
    finding = {"category": "free-form category", "severity": severity, "resolved": True}
    assert validate_review(review(findings=[finding])) == []


@pytest.mark.parametrize("severity", ["major", "minor", "critical", "High", "", None])
def test_review_severity_must_be_enum(severity):
    errors = validate_review(review(verdict="needs_change", findings=[{"category": "x", "severity": severity, "resolved": False}]))
    assert any("$.findings[0].severity" in e for e in errors), errors


def test_review_category_is_free_but_non_empty():
    ok = [{"category": "anything goes: 단위/시간대", "severity": "low", "resolved": True}]
    assert validate_review(review(findings=ok)) == []
    bad = [{"category": "", "severity": "low", "resolved": True}]
    assert any("$.findings[0].category" in e for e in validate_review(review(findings=bad)))


# pass rules: counter evidence >= 1 and no unresolved high finding; needs_change is always allowed


def test_review_pass_valid():
    findings = [
        {"category": "a", "severity": "high", "resolved": True},
        {"category": "b", "severity": "medium", "resolved": False},
        {"category": "c", "severity": "low", "resolved": False},
    ]
    assert validate_review(review(verdict="pass", findings=findings, counter_evidence_count=1)) == []


def test_review_pass_without_counter_evidence_is_invalid():
    errors = validate_review(review(verdict="pass", counter_evidence_count=0))
    assert any("$.verdict" in e and "counter_evidence_count >= 1" in e for e in errors), errors


def test_review_pass_with_unresolved_high_is_invalid():
    findings = [{"category": "leak", "severity": "high", "resolved": False}]
    errors = validate_review(review(verdict="pass", findings=findings))
    assert any("$.findings[0]" in e and "unresolved" in e for e in errors), errors


def test_review_pass_reports_both_violations():
    findings = [{"category": "leak", "severity": "high", "resolved": False}]
    assert len(validate_review(review(verdict="pass", findings=findings, counter_evidence_count=0))) == 2


def test_review_needs_change_is_always_allowed():
    assert validate_review(review(verdict="needs_change", findings=[], counter_evidence_count=0)) == []
    findings = [{"category": "leak", "severity": "high", "resolved": False}]
    assert validate_review(review(verdict="needs_change", findings=findings, counter_evidence_count=0)) == []
    resolved = [{"category": "leak", "severity": "high", "resolved": True}]
    assert validate_review(review(verdict="needs_change", findings=resolved, counter_evidence_count=3)) == []


def test_review_pass_rules_apply_to_files(tmp_path):
    path = tmp_path / "reviews" / "p3-data-1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(review(verdict="pass", counter_evidence_count=0)), encoding="utf-8")
    assert any("counter_evidence_count >= 1" in e for e in validate_review(path))


def test_review_phase_and_n_must_match_file_name(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    path = reviews / "p3-data-2.json"
    path.write_text(json.dumps(review(phase="p3-data", n=1)), encoding="utf-8")
    errors = validate_review(path)
    assert any("$.n" in e for e in errors)
    path.write_text(json.dumps(review(phase="p4-x", n=2)), encoding="utf-8")
    assert any("$.phase" in e for e in validate_review(path))
    path.write_text(json.dumps(review(phase="p3-data", n=2)), encoding="utf-8")
    assert validate_review(path) == []


def test_review_file_name_pattern(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(review()), encoding="utf-8")
    assert any("<phase>-<n>.json" in e for e in validate_review(path))


def test_workspace_errors():
    assert any("duplicate project id" in e for e in validate_workspace(
        {"projects": [{"id": "a", "path": "~/a"}, {"id": "a", "path": "~/b"}]}))
    assert any("'path' is a required property" in e for e in validate_workspace({"projects": [{"id": "a"}]}))
    assert any("'projects' is a required property" in e for e in validate_workspace({}))


# --- CLI -------------------------------------------------------------------


def test_detect_type():
    assert detect_type(Path("x/.workbench/events-2026-10.jsonl")) == "events"
    assert detect_type(Path("x/.workbench/project.yaml")) == "project"
    assert detect_type(Path("x/.workbench/reviews/p3-data-1.json")) == "review"
    assert detect_type(Path("~/.agent-workbench/workspace.yaml")) == "workspace"
    assert detect_type(Path("notes.txt")) is None


def test_cli_valid_examples(capsys):
    paths = [
        EXAMPLES / "project.yaml",
        EVENTS_FILE,
        EXAMPLES / "reviews" / "p3-data-1.json",
        EXAMPLES / "reviews" / "p3-data-2.json",
        EXAMPLES / "workspace.example.yaml",
    ]
    assert main(["validate", *map(str, paths)]) == 0
    assert capsys.readouterr().out.count("OK ") == 5


def test_cli_rejects_pass_without_counter_evidence(tmp_path, capsys):
    path = tmp_path / "reviews" / "p3-data-1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(review(verdict="pass", counter_evidence_count=0)), encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_reports_errors_and_exit_code(tmp_path, capsys):
    path = tmp_path / "events-2026-10.jsonl"
    path.write_text(json.dumps(base_event(event="nope")) + "\n", encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "line 1:" in out


def test_cli_unknown_type(tmp_path, capsys):
    path = tmp_path / "notes.txt"
    path.write_text("x", encoding="utf-8")
    assert main(["validate", str(path)]) == 2
    assert "cannot detect type" in capsys.readouterr().out


# --- glob_matches ----------------------------------------------------------

PATHS = [
    "docs/research/energy-structure.md",
    "docs/data/quality-report.md",
    "docs/data/raw/notes.md",
    "docs/models/model-card.md",
    "notebooks/01-eda.ipynb",
    "notebooks/old/02-eda.ipynb",
    "notebooks/.ipynb_checkpoints/x.ipynb",
    "results/p5-forecast/scenario-a.csv",
    "results/p5-forecast/scenario-b/summary.json",
    "results/p5-forecast/metrics.json",
    "src/pipeline/clean.py",
]


@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("docs/data/quality-report.md", ["docs/data/quality-report.md"]),  # literal
        ("docs/data/*.md", ["docs/data/quality-report.md"]),  # * stays within one segment
        ("docs/*/*.md", [
            "docs/research/energy-structure.md", "docs/data/quality-report.md", "docs/models/model-card.md",
        ]),
        ("notebooks/*.ipynb", ["notebooks/01-eda.ipynb"]),  # not notebooks/old/..., not nested
        ("notebooks/**/*.ipynb", [
            "notebooks/01-eda.ipynb", "notebooks/old/02-eda.ipynb", "notebooks/.ipynb_checkpoints/x.ipynb",
        ]),  # ** also matches zero segments
        ("results/p5-forecast/scenario-*", ["results/p5-forecast/scenario-a.csv"]),  # * is one segment: no scenario-b/summary.json
        ("results/p5-forecast/scenario-*/**", ["results/p5-forecast/scenario-b/summary.json"]),
        ("results/**", [
            "results/p5-forecast/scenario-a.csv", "results/p5-forecast/scenario-b/summary.json",
            "results/p5-forecast/metrics.json",
        ]),
        ("**/*.md", [
            "docs/research/energy-structure.md", "docs/data/quality-report.md", "docs/data/raw/notes.md",
            "docs/models/model-card.md",
        ]),
        ("docs/**/notes.md", ["docs/data/raw/notes.md"]),
        ("src/pipeline/clean.p?", ["src/pipeline/clean.py"]),
        ("src/pipeline/[a-c]lean.py", ["src/pipeline/clean.py"]),
        ("nothing/*", []),
    ],
)
def test_glob_matches(pattern, expected):
    assert glob_matches(pattern, PATHS) == expected


def test_glob_double_star_edge_cases():
    assert glob_match("docs/**/x.md", "docs/x.md")  # zero segments
    assert glob_match("docs/**/x.md", "docs/a/b/x.md")
    assert not glob_match("docs/**", "docs")  # trailing ** needs at least one segment
    assert glob_match("docs/**", "docs/a")
    assert glob_match("**", "any/thing/at/all.txt")
    assert glob_match("a/**/**/b", "a/b")
    assert glob_match("a**b", "axxb") and not glob_match("a**b", "ax/xb")  # ** inside a segment is plain *


def test_glob_is_relative_case_sensitive_and_normalized():
    assert glob_match("docs/*.md", "./docs/a.md")
    assert glob_match("./docs/*.md", "docs/a.md")
    assert glob_match("docs/*.md", "docs\\a.md")  # backslash separators
    assert not glob_match("docs/*.md", "Docs/a.md")
    assert not glob_match("*", "/abs/path.md")  # absolute paths are never project-relative
    assert not glob_match("/docs/*.md", "docs/a.md")
    assert not glob_match("docs/*.md", "docs//")  # no file name


def test_glob_matches_returns_input_entries_in_order_and_accepts_paths():
    got = glob_matches("docs/*.md", [Path("docs/b.md"), "docs/a.md", "x/docs/c.md"])
    assert got == [Path("docs/b.md"), "docs/a.md"]
    assert glob_matches("*.md", iter(["a.md", "b.txt"])) == ["a.md"]  # any iterable
