"""Data-layer tests: loader (no streamlit needed)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import timezone
from pathlib import Path

import pytest
import yaml

from agent_workbench.contract import validate_events_file, validate_project, validate_review
from agent_workbench.dashboard import loader
from agent_workbench.dashboard.loader import (
    LoadError,
    display_path,
    load_project,
    load_registry,
    mask_paths,
    numeric_entries,
    parse_ts,
    resolve_deliverable,
    workspace_path,
)

from conftest import DEMO, event, make_project, phase, usage


# ---- demo project -----------------------------------------------------------


def test_demo_project_is_contract_valid():
    assert validate_project(DEMO / ".workbench" / "project.yaml") == []
    assert validate_events_file(DEMO / ".workbench" / "events-2026-10.jsonl") == []
    for review in (DEMO / ".workbench" / "reviews").glob("*.json"):
        assert validate_review(review) == [], review.name


def test_demo_project_loads():
    data = load_project(DEMO)
    assert data.id == "demo-project"
    assert [p.id for p in data.phases] == ["p1-research", "p2-design", "p3-data", "p4-analysis", "p5-forecast", "p6-report"]
    assert {p.status for p in data.phases} == {"done", "review", "blocked"}
    assert data.events_report.events_loaded == 43
    assert data.events_report.skipped == 0
    assert data.events_report.schema_warnings == 0
    assert data.warnings == []
    assert len(data.reviews) == 4 and all(r.valid for r in data.reviews)
    assert data.latest_review("p3-data").n == 2
    assert data.latest_review("p3-data").summary_rel_path == ".workbench/reviews/p3-data-2.md"
    assert [m.phase_id for m in data.metrics] == ["p5-forecast"]
    assert [c.rel_path for c in data.chapters] == ["docs/fieldbook/01-research.md"]


def test_demo_deliverables_are_expanded_against_the_real_tree():
    data = load_project(DEMO)
    p4 = {s.pattern: s for s in data.deliverables["p4-analysis"]}
    assert p4["docs/analysis/findings.md"].exists
    assert p4["notebooks/*.ipynb"].matches == ("notebooks/01-eda.ipynb",)
    assert p4["results/p4-analysis/scenario-*"].match_count == 0
    assert p4["results/p4-analysis/scenario-*"].latest_mtime is None
    p5 = {s.pattern: s for s in data.deliverables["p5-forecast"]}
    assert p5["results/p5-forecast/scenario-*"].match_count == 2
    assert p5["results/p5-forecast/scenario-*"].is_glob
    assert not p5["docs/simulation/assumptions.md"].exists


def test_demo_metrics_numeric_entries_only():
    metrics = load_project(DEMO).metrics[0]
    assert metrics.present_keys == ("model", "savings")
    model = dict(metrics.sections["model"])
    assert model["mape_pct"] == 6.8 and model["train_rows"] == 18240
    assert "split" not in model  # string value
    savings = dict(metrics.sections["savings"])
    assert savings["kwh_per_month.point"] == 1840
    assert "unit_price_source" not in savings
    assert metrics.rel_path == "results/p5-forecast/metrics.json"


def test_loading_never_writes(tmp_path):
    copy = tmp_path / "demo"
    shutil.copytree(DEMO, copy)

    def snapshot():
        return sorted((p.relative_to(copy).as_posix(), p.stat().st_mtime_ns, p.stat().st_size if p.is_file() else -1) for p in copy.rglob("*"))

    before = snapshot()
    data = load_project(copy)
    from agent_workbench.dashboard.analysis import analyze

    analyze(data.events)
    assert snapshot() == before


# ---- events -----------------------------------------------------------------


def test_malformed_lines_are_skipped_and_counted(tmp_path):
    good = event("2026-10-01T09:00:00+00:00", "agent_start")
    root = make_project(
        tmp_path / "p",
        events=[
            json.dumps(good),
            "{not json",  # malformed
            '["a", "list"]',  # JSON but not an object
            json.dumps(event("2026-10-01T09:05:00+00:00", "something_else")),  # unknown kind
            json.dumps({**good, "ts": "yesterday"}),  # unusable ts
            json.dumps({**good, "ts": "2026-10-01T09:30:00"}),  # no offset
            "",
            json.dumps(event("2026-10-01T09:10:00+00:00", "agent_stop", agent_id=good["agent_id"])),
            '{"v":1,"ts":"2026-10-01T10:00:00+00:00","event":"agent_sta',  # truncated last line
        ],
    )
    data = load_project(root)
    report = data.events_report
    assert report.events_loaded == 2
    assert report.skipped_malformed == 3
    assert report.skipped_invalid == 3
    assert report.skipped == 6
    assert report.lines_read == 8  # blank line not counted


def test_schema_invalid_but_parsable_events_are_kept_with_a_warning(tmp_path):
    legacy = event("2026-10-01T09:00:00+00:00", "agent_stop", usage={"model": "sonnet", "input_tokens": 1, "output_tokens": 2})
    root = make_project(tmp_path / "p", events=[json.dumps(legacy)])
    data = load_project(root)
    assert data.events_report.events_loaded == 1
    assert data.events_report.schema_warnings == 1
    assert data.events[0].usage is not None and not data.events[0].usage.complete
    assert data.events[0].usage.total_input is None


def test_no_events_is_fine(tmp_path):
    data = load_project(make_project(tmp_path / "p"))
    assert data.events == []
    assert data.events_report.files == []
    assert data.reviews == () and data.metrics == () and data.chapters == ()


def test_events_from_all_monthly_files_are_merged_in_time_order(tmp_path):
    root = make_project(tmp_path / "p", events=[event("2026-11-02T00:00:00+00:00", "agent_start")], events_name="events-2026-11.jsonl")
    (root / ".workbench" / "events-2026-10.jsonl").write_text(json.dumps(event("2026-10-31T23:00:00+00:00", "agent_start")) + "\n")
    (root / ".workbench" / "events.jsonl").write_text(json.dumps(event("2026-01-01T00:00:00+00:00", "agent_start")) + "\n")  # not events-*.jsonl
    data = load_project(root)
    assert data.events_report.files == ["events-2026-10.jsonl", "events-2026-11.jsonl"]
    assert [e.ts.month for e in data.events] == [10, 11]


def test_summary_is_not_carried_for_non_error_events(tmp_path):
    kinds = ("agent_start", "agent_stop", "gate_requested", "gate_approved", "change_requested", "change_approved")
    events = [event(f"2026-10-01T09:0{i}:00+00:00", kind, summary="secret words", usage=usage() if kind == "agent_stop" else None) for i, kind in enumerate(kinds)]
    loaded = load_project(make_project(tmp_path / "p", events=events)).events
    assert len(loaded) == len(kinds)
    assert all(ev.summary is None for ev in loaded)
    stop = next(ev for ev in loaded if ev.event == "agent_stop")
    assert stop.usage.total_input == 10 + 100 + 1000


def test_error_summary_is_carried_masked_and_single_line(tmp_path):
    root = tmp_path / "p"
    home = str(Path.home())
    raw = f"Cannot read {root}/data/x.csv\nfrom {home}/other/file\r\n\tand /Users/someone/secret: not found"
    ev = load_project(make_project(root, events=[event("2026-10-01T09:00:00+00:00", "agent_error", summary=raw)])).events[0]
    assert ev.summary is not None
    assert str(root) not in ev.summary and home not in ev.summary and "/Users/" not in ev.summary
    assert "\n" not in ev.summary and "\r" not in ev.summary and "\t" not in ev.summary
    assert ev.summary.startswith("Cannot read <project>/data/x.csv from ")


def test_error_summary_is_capped_and_null_or_blank_is_none(tmp_path):
    events = [
        event("2026-10-01T09:00:00+00:00", "agent_error", summary="x" * 500),
        event("2026-10-01T09:01:00+00:00", "agent_error", summary=None),
        event("2026-10-01T09:02:00+00:00", "agent_error", summary="  \n "),
        event("2026-10-01T09:03:00+00:00", "agent_error", summary=123),
    ]
    loaded = load_project(make_project(tmp_path / "p", events=events)).events
    assert len(loaded[0].summary) == 200
    assert [e.summary for e in loaded[1:]] == [None, None, None]


def test_demo_error_event_has_a_summary_and_other_events_do_not_carry_one():
    events = load_project(DEMO).events
    errors = [e for e in events if e.event == "agent_error"]
    assert len(errors) == 1 and errors[0].summary
    assert all(e.summary is None for e in events if e.event != "agent_error")


@pytest.mark.parametrize(
    "value, ok",
    [
        ("2026-10-07T09:00:05+09:00", True),
        ("2026-10-07T09:00:05Z", True),
        ("2026-10-07T09:00:05.123456789+00:00", True),
        ("2026-10-07T09:00:05", False),
        ("nope", False),
        (None, False),
        (5, False),
    ],
)
def test_parse_ts(value, ok):
    parsed = parse_ts(value)
    assert (parsed is not None) is ok
    if ok:
        assert parsed.tzinfo is not None


# ---- deliverables -----------------------------------------------------------


def test_deliverable_exact_path(tmp_path):
    root = make_project(tmp_path / "p", files=["docs/a.md"])
    present = resolve_deliverable(root, "docs/a.md")
    missing = resolve_deliverable(root, "docs/b.md")
    assert present.exists and present.matches == ("docs/a.md",) and not present.is_glob
    assert present.latest_mtime is not None and present.latest_mtime.tzinfo == timezone.utc
    assert not missing.exists and missing.latest_mtime is None


def test_deliverable_glob_semantics_follow_the_contract(tmp_path):
    root = make_project(
        tmp_path / "p",
        files=[
            "notebooks/01.ipynb",
            "notebooks/old/02.ipynb",
            "results/x/scenario-a.csv",
            "results/x/scenario-b/summary.json",
            "docs/deep/er/note.md",
            "docs/note.md",
        ],
    )
    assert resolve_deliverable(root, "notebooks/*.ipynb").matches == ("notebooks/01.ipynb",)
    # directory names match a single-segment glob, their contents do not
    scen = resolve_deliverable(root, "results/x/scenario-*")
    assert scen.matches == ("results/x/scenario-a.csv", "results/x/scenario-b")
    assert resolve_deliverable(root, "results/x/scenario-*/**").matches == ("results/x/scenario-b/summary.json",)
    # ** spans zero or more directories
    assert resolve_deliverable(root, "docs/**/note.md").matches == ("docs/deep/er/note.md", "docs/note.md")
    assert resolve_deliverable(root, "notebooks/**").match_count == 3  # old, old/02.ipynb, 01.ipynb
    assert not resolve_deliverable(root, "reports/*.md").exists


def test_deliverable_mtime_is_the_latest_match(tmp_path):
    root = make_project(tmp_path / "p", files=["out/a.txt", "out/b.txt"])
    os.utime(root / "out" / "a.txt", (1_000_000_000, 1_000_000_000))
    os.utime(root / "out" / "b.txt", (1_500_000_000, 1_500_000_000))
    status = resolve_deliverable(root, "out/*.txt")
    assert status.match_count == 2
    assert int(status.latest_mtime.timestamp()) == 1_500_000_000


def test_deliverable_unsafe_patterns_never_match(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    root = make_project(tmp_path / "p")
    for pattern in ("../secret.txt", "../*.txt", str(outside), "~/x", "a/../../secret.txt"):
        assert not resolve_deliverable(root, pattern).exists, pattern


def test_deliverable_scan_skips_vcs_and_env_dirs(tmp_path):
    root = make_project(tmp_path / "p", files=[".git/objects/a.md", ".venv/lib/a.md", "docs/a.md"])
    assert resolve_deliverable(root, "**/a.md").matches == ("docs/a.md",)


# ---- reviews, metrics, chapters ---------------------------------------------


def test_review_reading_and_summary_masking(tmp_path):
    root = make_project(tmp_path / "p")
    reviews = root / ".workbench" / "reviews"
    reviews.mkdir()
    (reviews / "p1-1.json").write_text(
        json.dumps(
            {
                "verdict": "needs_change",
                "findings": [
                    {"category": "x", "severity": "high", "resolved": False},
                    {"category": "y", "severity": "low", "resolved": True},
                ],
                "counter_evidence_count": 0,
            }
        )
    )
    foreign = "/".join(["", "Users", "someone", "secret", "file"])  # spelled out so the repo's own path scan stays quiet
    (reviews / "p1-1.md").write_text(f"see {root}/docs/a.md and {foreign}\n", encoding="utf-8")
    (reviews / "p1-2.json").write_text("{broken")
    data = load_project(root)
    first, second = data.reviews
    assert (first.phase, first.n, first.verdict) == ("p1", 1, "needs_change")
    assert len(first.unresolved) == 1 and first.valid
    assert str(root) not in first.summary_text and "someone" not in first.summary_text
    assert "<project>/docs/a.md" in first.summary_text
    assert second.verdict is None and not second.valid


def test_invalid_pass_review_is_flagged_not_hidden(tmp_path):
    root = make_project(tmp_path / "p")
    reviews = root / ".workbench" / "reviews"
    reviews.mkdir()
    (reviews / "p1-1.json").write_text(
        json.dumps({"verdict": "pass", "findings": [{"category": "x", "severity": "high", "resolved": False}], "counter_evidence_count": 0})
    )
    review = load_project(root).reviews[0]
    assert review.verdict == "pass" and not review.valid and review.errors


def test_metrics_reads_only_model_and_savings(tmp_path):
    root = make_project(tmp_path / "p", phases=[phase("p5-forecast")])
    results = root / "results" / "p5-forecast"
    results.mkdir(parents=True)
    (results / "metrics.json").write_text(
        json.dumps(
            {
                "model": {"mape": 5.5, "flag": True, "name": "gbm", "nested": {"a": 1, "b": {"c": 2.5}}, "series": [1, 2]},
                "savings": 12.5,
                "extra": {"private_number": 999},
                "token": 7,
            }
        )
    )
    metrics = load_project(root).metrics[0]
    model = dict(metrics.sections["model"])
    assert model == {"mape": 5.5, "nested.a": 1, "nested.b.c": 2.5}  # bool, str and list dropped
    assert dict(metrics.sections["savings"]) == {"savings": 12.5}
    flat = json.dumps({k: list(v) for k, v in metrics.sections.items()})
    assert "private_number" not in flat and "999" not in flat and "token" not in flat


def test_metrics_edge_cases(tmp_path):
    root = make_project(tmp_path / "p", phases=[phase("a"), phase("b"), phase("c")], files=[])
    for pid, text in (("a", "{oops"), ("b", "[1,2]"), ("c", '{"other": {"x": 1}}')):
        (root / "results" / pid).mkdir(parents=True)
        (root / "results" / pid / "metrics.json").write_text(text)
    by_phase = {m.phase_id: m for m in load_project(root).metrics}
    assert by_phase["a"].error and by_phase["b"].error
    assert by_phase["c"].error is None and by_phase["c"].present_keys == ()


def test_numeric_entries_rejects_nan_and_depth():
    assert numeric_entries({"a": float("nan"), "b": float("inf"), "c": 1}) == [("c", 1)]
    deep = {"l1": {"l2": {"l3": {"l4": 1}}}}
    assert numeric_entries(deep) == []


# ---- project.yaml tolerance and errors ---------------------------------------


def test_semantic_problems_become_warnings_not_errors(tmp_path):
    root = make_project(tmp_path / "p", phases=[phase("a", "doing"), phase("b", "doing")])
    data = load_project(root)
    assert len(data.phases) == 2
    assert any("doing" in w for w in data.warnings)


def test_project_load_errors_have_codes(tmp_path):
    with pytest.raises(LoadError) as missing_dir:
        load_project(tmp_path / "nope")
    assert missing_dir.value.code == "project_dir_missing"
    (tmp_path / "empty").mkdir()
    with pytest.raises(LoadError) as no_yaml:
        load_project(tmp_path / "empty")
    assert no_yaml.value.code == "project_yaml_missing"
    bad = tmp_path / "bad"
    (bad / ".workbench").mkdir(parents=True)
    (bad / ".workbench" / "project.yaml").write_text("a: [unclosed")
    with pytest.raises(LoadError) as invalid:
        load_project(bad)
    assert invalid.value.code == "project_yaml_invalid"
    (bad / ".workbench" / "project.yaml").write_text("- just\n- a list\n")
    with pytest.raises(LoadError) as notmap:
        load_project(bad)
    assert notmap.value.code == "project_yaml_invalid"


# ---- registry ---------------------------------------------------------------


def _registry(tmp_path: Path, projects) -> Path:
    path = tmp_path / "ws" / "workspace.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"projects": projects}), encoding="utf-8")
    return path


def test_registry_loads_and_expands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = _registry(tmp_path, [{"id": "a", "path": "~/dev/a"}, {"id": "b", "path": "rel/b"}, {"id": "c", "path": str(tmp_path / "c")}])
    registry = load_registry(path)
    a, b, c = registry.projects
    assert a.path == tmp_path / "home" / "dev" / "a" and a.declared == "~/dev/a"
    assert b.path == path.parent / "rel" / "b"  # relative to the registry file
    assert c.path == tmp_path / "c"


def test_registry_location_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert workspace_path(None, {}) == tmp_path / "home" / ".agent-workbench" / "workspace.yaml"
    assert workspace_path(None, {loader.WORKSPACE_ENV: str(tmp_path / "env.yaml")}) == tmp_path / "env.yaml"
    assert workspace_path(str(tmp_path / "arg.yaml"), {loader.WORKSPACE_ENV: str(tmp_path / "env.yaml")}) == tmp_path / "arg.yaml"


def test_registry_errors(tmp_path):
    with pytest.raises(LoadError) as missing:
        load_registry(tmp_path / "none.yaml", {})
    assert missing.value.code == "registry_missing"
    path = _registry(tmp_path, [{"id": "a", "path": "x"}, {"id": "a", "path": "y"}])
    with pytest.raises(LoadError) as dup:
        load_registry(path)
    assert dup.value.code == "registry_invalid" and dup.value.errors
    path.write_text("projects: []\n")
    with pytest.raises(LoadError) as empty:
        load_registry(path)
    assert empty.value.code in ("registry_empty", "registry_invalid")


# ---- path display -----------------------------------------------------------


def test_display_path_never_leaks_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "alice"
    monkeypatch.setenv("HOME", str(home))
    assert display_path(home / "dev" / "x") == "~/dev/x"
    assert display_path(home / "dev" / "x" / "a.md", root=home / "dev" / "x") == "a.md"
    assert display_path("/opt/elsewhere/project") == ".../project"
    assert str(home) not in display_path(home / ".agent-workbench" / "workspace.yaml")


def test_mask_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    mac, linux = "/".join(["", "Users", "bob", "x"]), "/".join(["", "home", "carol", "y"])
    text = f"{tmp_path / 'h'}/a and {mac} and {linux}"
    masked = mask_paths(text)
    assert "bob" not in masked and "carol" not in masked and str(tmp_path / "h") not in masked
