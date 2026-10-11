"""Data-layer tests: analysis (pairing, stalled, gates, rework, lead time, usage)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_workbench.dashboard import analysis as an
from agent_workbench.dashboard.loader import Event, infer_phases, load_events, load_project

from conftest import DEMO, event, make_project, phase, usage

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def events_of(tmp_path, *raw):
    root = make_project(tmp_path / "p", events=[json.dumps(e) for e in raw])
    evs, _ = load_events(root)
    return evs


def run_analysis(tmp_path, *raw, now_min=1000, stalled_after=an.DEFAULT_STALLED_AFTER):
    return an.analyze(events_of(tmp_path, *raw), T0 + timedelta(minutes=now_min), stalled_after)


# ---- pairing ----------------------------------------------------------------


def test_pairs_by_agent_id_even_when_runs_overlap(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "A1"),
        event(at(1), "agent_start", "a", "A2"),  # same agent name, parallel
        event(at(10), "agent_stop", "a", "A2"),
        event(at(30), "agent_stop", "a", "A1"),
    )
    by_id = {r.agent_id: r for r in result.runs}
    assert by_id["A1"].duration == timedelta(minutes=30)
    assert by_id["A2"].duration == timedelta(minutes=9)
    assert all(r.outcome == an.OUTCOME_COMPLETED for r in result.runs)


def test_stalled_threshold(tmp_path):
    raw = [event(at(0), "agent_start", "old", "O"), event(at(40), "agent_start", "recent", "R")]
    result = run_analysis(tmp_path, *raw, now_min=65)  # old: 65 min, recent: 25 min
    out = {r.agent: r.outcome for r in result.runs}
    assert out == {"old": an.OUTCOME_STALLED, "recent": an.OUTCOME_RUNNING}
    assert [r.agent for r in result.stalled] == ["old"]
    # configurable
    tight = run_analysis(tmp_path, *raw, now_min=65, stalled_after=timedelta(minutes=20))
    assert {r.outcome for r in tight.runs} == {an.OUTCOME_STALLED}
    loose = run_analysis(tmp_path, *raw, now_min=65, stalled_after=timedelta(hours=2))
    assert {r.outcome for r in loose.runs} == {an.OUTCOME_RUNNING}


def test_exactly_at_threshold_is_not_stalled(tmp_path):
    result = run_analysis(tmp_path, event(at(0), "agent_start", "a", "A"), now_min=30)
    assert result.runs[0].outcome == an.OUTCOME_RUNNING


def test_running_run_has_no_duration(tmp_path):
    run = run_analysis(tmp_path, event(at(0), "agent_start", "a", "A"), now_min=5).runs[0]
    assert run.duration is None and run.age(T0 + timedelta(minutes=5)) == timedelta(minutes=5)


def test_agent_error_pairs_by_name_with_nearest_preceding_start(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "analyst", "S1"),
        event(at(5), "agent_start", "other", "S2"),
        event(at(10), "agent_start", "analyst", "S3"),
        event(at(20), "agent_error", "analyst", "UNRELATED-ID"),  # id differs: must pair by name
    )
    by_id = {r.agent_id: r for r in result.runs}
    assert by_id["S3"].outcome == an.OUTCOME_ERROR  # nearest preceding of that name
    assert by_id["S3"].duration == timedelta(minutes=10)
    assert by_id["S1"].outcome == an.OUTCOME_STALLED  # earlier start is still unfinished
    assert by_id["S2"].outcome == an.OUTCOME_STALLED
    assert result.unmatched == []
    assert len(result.errors) == 1


def test_errored_start_is_not_counted_as_stalled(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "A"),
        event(at(3), "agent_error", "a", None),
        now_min=5000,
    )
    assert result.runs[0].outcome == an.OUTCOME_ERROR
    assert result.stalled == []
    assert result.completed == []  # error durations are not mixed into completed stats
    assert result.duration_by_agent == {}


def test_error_without_start_is_unmatched_standalone(tmp_path):
    result = run_analysis(tmp_path, event(at(0), "agent_error", "ghost", None), event(at(1), "agent_stop", "x", "NOSTART"))
    assert result.runs == []
    assert [e.event for e in result.unmatched] == ["agent_error", "agent_stop"]


def test_error_does_not_pair_with_an_already_stopped_start(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "A"),
        event(at(5), "agent_stop", "a", "A"),
        event(at(8), "agent_error", "a", None),
    )
    assert result.runs[0].outcome == an.OUTCOME_COMPLETED
    assert len(result.unmatched) == 1


def test_error_before_its_start_in_time_does_not_pair(tmp_path):
    result = run_analysis(tmp_path, event(at(10), "agent_start", "a", "A"), event(at(5), "agent_error", "a", None), now_min=12)
    assert result.runs[0].outcome == an.OUTCOME_RUNNING
    assert len(result.unmatched) == 1


def test_paired_error_carries_its_summary_and_missing_summary_is_no_cause(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "A", summary="start text must not leak"),
        event(at(3), "agent_error", "a", None, summary="disk full"),
        event(at(4), "agent_start", "b", "B"),
        event(at(6), "agent_error", "b", None),
    )
    by_agent = {r.agent: r for r in result.errors}
    assert by_agent["a"].error_summary == "disk full"
    assert by_agent["b"].error_summary is None
    items = an.error_items(result)
    assert [(i.agent, i.cause, i.paired) for i in items] == [("a", "disk full", True), ("b", "원인 미기록", True)]


def test_unmatched_error_is_listed_with_its_summary_and_phase_filter(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_error", "ghost", None, phase="p2", summary="no start for me"),
        event(at(1), "agent_start", "a", "A", phase="p1"),
        event(at(2), "agent_error", "a", None, phase="p1", summary="timeout"),
        event(at(3), "agent_stop", "z", "NOSTART", summary="stop text must not leak"),
    )
    everything = an.error_items(result)
    assert [(i.agent, i.phase, i.cause, i.paired) for i in everything] == [
        ("ghost", "p2", "no start for me", False),
        ("a", "p1", "timeout", True),
    ]
    assert [i.agent for i in an.error_items(result, "p1", all_phases=False)] == ["a"]
    assert an.error_items(result, "nope", all_phases=False) == []


# ---- gates, rework, lead time -----------------------------------------------


def test_gate_wait_approved_pending_and_change_requested(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(120), "change_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(300), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(360), "gate_approved", "orchestrator", None, "p1", "phase-end"),
        event(at(400), "gate_requested", "orchestrator", None, "p2", "phase-end"),
        now_min=500,
    )
    kinds = [(g.phase, g.resolution, g.wait) for g in result.gate_waits]
    assert kinds == [
        ("p1", "change_requested", timedelta(minutes=120)),
        ("p1", "approved", timedelta(minutes=60)),
        ("p2", "pending", timedelta(minutes=100)),
    ]
    assert [g.wait for g in result.approved_waits] == [timedelta(minutes=60)]
    assert [g.phase for g in result.pending_gates] == ["p2"]


def test_gate_wait_is_per_phase_and_gate(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(10), "gate_requested", "orchestrator", None, "p2", "phase-end"),
        event(at(50), "gate_approved", "orchestrator", None, "p2", "phase-end"),
        event(at(90), "gate_approved", "orchestrator", None, "p1", "phase-end"),
    )
    waits = {g.phase: g.wait for g in result.gate_waits}
    assert waits == {"p1": timedelta(minutes=90), "p2": timedelta(minutes=40)}


def test_gate_pairing_tolerates_a_null_phase_on_one_side(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(30), "gate_approved", "orchestrator", None, None, "phase-end"),
    )
    assert result.gate_waits[0].resolution == "approved"


def test_orphan_approval_is_ignored(tmp_path):
    result = run_analysis(tmp_path, event(at(0), "gate_approved", "orchestrator", None, "p1", "phase-end"))
    assert result.gate_waits == []


def test_rework_counts_starts_per_phase_and_agent(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "1", "p1"),
        event(at(1), "agent_start", "a", "2", "p1"),
        event(at(2), "agent_start", "a", "3", "p1"),
        event(at(3), "agent_start", "a", "4", "p2"),  # different phase: not rework
        event(at(4), "agent_start", "b", "5", "p1"),
    )
    assert [(r.phase, r.agent, r.starts, r.reruns) for r in result.rework] == [("p1", "a", 3, 2)]


def test_phase_lead_time_and_change_counts(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "1", "p1"),
        event(at(10), "agent_stop", "a", "1", "p1"),
        event(at(20), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(60), "change_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(70), "change_approved", "orchestrator", None, "p1", "phase-end"),
        event(at(80), "agent_start", "a", "2", "p1"),
        event(at(100), "gate_requested", "orchestrator", None, "p1", "phase-end"),
        event(at(130), "gate_approved", "orchestrator", None, "p1", "phase-end"),
        event(at(140), "agent_start", "b", "3", "p2"),
    )
    p1 = result.phase_timings["p1"]
    assert p1.lead_time == timedelta(minutes=130)
    assert p1.change_requested == 1 and p1.change_approved == 1
    assert p1.gate_wait_total == timedelta(minutes=40 + 30)
    p2 = result.phase_timings["p2"]
    assert p2.lead_time is None and p2.approved_at is None and p2.first_start is not None


# ---- durations and usage ----------------------------------------------------


def test_duration_stats(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "1"),
        event(at(10), "agent_stop", "a", "1"),
        event(at(20), "agent_start", "a", "2"),
        event(at(50), "agent_stop", "a", "2"),
    )
    stat = result.duration_by_agent["a"]
    assert stat.count == 2
    assert stat.mean == timedelta(minutes=20) and stat.median == timedelta(minutes=20)
    assert stat.longest == timedelta(minutes=30) and stat.total == timedelta(minutes=40)


def test_usage_sums_with_cache_and_na_for_null(tmp_path):
    result = run_analysis(
        tmp_path,
        event(at(0), "agent_start", "a", "1", "p1"),
        event(at(5), "agent_stop", "a", "1", "p1", usage=usage("sonnet", 10, 20, 100, 1000)),
        event(at(6), "agent_start", "a", "2", "p1"),
        event(at(9), "agent_stop", "a", "2", "p1", usage=usage("opus", 1, 2, 3, 4)),
        event(at(10), "agent_start", "b", "3", "p2"),
        event(at(12), "agent_stop", "b", "3", "p2", usage=None),  # extraction failed
    )
    total = result.usage_total
    assert (total.stops, total.with_usage, total.missing) == (3, 2, 1)
    assert total.input_tokens == 11 and total.output_tokens == 22
    assert total.cache_creation_input_tokens == 103 and total.cache_read_input_tokens == 1004
    assert total.total_input == 11 + 103 + 1004
    assert total.models == ("sonnet", "opus")
    only_a = result.usage_by_agent["a"]
    assert only_a.total_input == 1118 and only_a.missing == 0
    b = result.usage_by_agent["b"]
    assert b.total_input is None and b.input_tokens is None and b.output_tokens is None and b.missing == 1
    assert result.usage_by_phase["p2"].total_input is None


def test_usage_counts_stops_even_without_a_matching_start(tmp_path):
    result = run_analysis(tmp_path, event(at(5), "agent_stop", "a", "ORPHAN", usage=usage()))
    assert result.runs == [] and result.usage_total.with_usage == 1


def test_no_events(tmp_path):
    result = an.analyze([], T0)
    assert result.runs == [] and result.gate_waits == [] and result.usage_total.total_input is None
    assert result.duration_by_agent == {} and result.phase_timings == {}


# ---- action list ------------------------------------------------------------


def test_action_items_order(tmp_path):
    root = make_project(
        tmp_path / "p",
        phases=[phase("a", "blocked"), phase("b", "review"), phase("c", "done")],
        events=[
            json.dumps(event(at(0), "agent_start", "w", "S", "c")),
            json.dumps(event(at(5), "gate_requested", "orchestrator", None, "b", "phase-end")),
        ],
    )
    data = load_project(root)
    result = an.analyze(data.events, T0 + timedelta(hours=5))
    items = an.action_items(data.phases, result)
    assert [(i.kind, i.phase_id) for i in items] == [("review", "b"), ("blocked", "a"), ("stalled", "c")]
    assert items[0].since == T0 + timedelta(minutes=5) and items[0].gate == "phase-end"
    assert items[2].agent == "w"


# ---- the demo project, with a fixed clock -------------------------------------


def test_demo_numbers():
    data = load_project(DEMO)
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))
    result = an.analyze(data.events, now)
    assert len(result.runs) == 16  # + pm-tracker and fieldbook-writer after the p4 gate (phase null in the file)
    assert (len(result.completed), len(result.errors), len(result.stalled)) == (14, 1, 1)
    assert result.errors[0].agent == "analyst" and result.errors[0].phase == "p3-data"
    assert result.errors[0].error_summary and "file not found" in result.errors[0].error_summary
    assert [i.cause for i in an.error_items(result)] == [result.errors[0].error_summary]
    assert result.stalled[0].agent == "savings-simulator"
    assert {(g.phase, g.resolution) for g in result.gate_waits if g.resolution != "approved"} == {
        ("p3-data", "change_requested"),
        ("p5-forecast", "pending"),
    }
    assert result.usage_total.stops == 14 and result.usage_total.missing == 1
    assert result.phase_timings["p3-data"].change_requested == 1
    assert result.phase_timings["p5-forecast"].lead_time is None
    assert {(r.phase, r.agent) for r in result.rework} >= {("p3-data", "data-prep"), ("p3-data", "analyst")}
    items = an.action_items(data.phases, result)
    assert [(i.kind, i.phase_id) for i in items] == [("review", "p5-forecast"), ("blocked", "p6-report"), ("stalled", "p5-forecast")]


# ---- decision 12-1: reader-side phase attribution ----------------------------------


def test_demo_attributes_post_gate_events_and_marks_them(tmp_path):
    data = load_project(DEMO)
    inferred = [e for e in data.events if e.phase_inferred]
    assert [(e.agent, e.event, e.phase) for e in inferred] == [
        ("pm-tracker", "agent_start", "p4-analysis"),
        ("pm-tracker", "agent_stop", "p4-analysis"),
        ("fieldbook-writer", "agent_start", "p4-analysis"),
        ("fieldbook-writer", "agent_stop", "p4-analysis"),
    ]
    assert not any(e.phase_inferred for e in data.events if e.agent not in ("pm-tracker", "fieldbook-writer"))
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))
    result = an.analyze(data.events, now)
    assert {r.agent for r in result.runs if r.phase_inferred} == {"pm-tracker", "fieldbook-writer"}
    assert result.phase_timings["p4-analysis"].inferred_events == 4
    assert result.usage_by_phase["p4-analysis"].inferred == 2
    assert None not in result.phase_timings  # nothing is left unattributed


def test_null_phase_after_gate_in_same_session_is_attributed(tmp_path):
    evs = events_of(
        tmp_path,
        event(at(0), "agent_start", "w", "A", phase="p1"),
        event(at(5), "gate_requested", "orchestrator", None, phase="p1", gate="phase-end"),
        event(at(6), "agent_start", "pm-tracker", "B", phase=None),  # review phase was ambiguous
        event(at(7), "gate_approved", "orchestrator", None, phase="p1", gate="phase-end"),
        event(at(8), "agent_start", "fieldbook-writer", "C", phase=None),
        event(at(9), "agent_stop", "fieldbook-writer", "C", phase=None),
    )
    assert [(e.agent, e.phase, e.phase_inferred) for e in evs] == [
        ("w", "p1", False),
        ("orchestrator", "p1", False),
        ("pm-tracker", "p1", True),
        ("orchestrator", "p1", False),
        ("fieldbook-writer", "p1", True),
        ("fieldbook-writer", "p1", True),
    ]


def test_different_session_does_not_inherit(tmp_path):
    evs = events_of(
        tmp_path,
        event(at(0), "gate_approved", "orchestrator", None, phase="p1", gate="phase-end", session_id="s1"),
        event(at(1), "agent_start", "pm-tracker", "B", phase=None, session_id="s2"),
        event(at(2), "agent_start", "pm-tracker", "C", phase=None, session_id=None),  # no session: never inherits
        event(at(3), "agent_start", "pm-tracker", "D", phase=None, session_id="s1"),
    )
    assert [(e.session_id, e.phase, e.phase_inferred) for e in evs] == [
        ("s1", "p1", False),
        ("s2", None, False),
        (None, None, False),
        ("s1", "p1", True),
    ]


def test_event_before_any_gate_stays_null(tmp_path):
    evs = events_of(
        tmp_path,
        event(at(0), "agent_start", "pm-tracker", "A", phase=None),
        event(at(1), "agent_stop", "pm-tracker", "A", phase=None),
        event(at(2), "gate_requested", "orchestrator", None, phase="p1", gate="phase-end"),
    )
    assert [(e.phase, e.phase_inferred) for e in evs[:2]] == [(None, False), (None, False)]


def test_only_gate_events_with_a_phase_are_sources(tmp_path):
    evs = events_of(
        tmp_path,
        event(at(0), "agent_start", "w", "A", phase="p1"),  # an explicit phase on a non-gate event is no source
        event(at(1), "change_requested", "orchestrator", None, phase="p1", gate="phase-end"),  # neither is this
        event(at(2), "agent_start", "pm-tracker", "B", phase=None),
        event(at(3), "gate_requested", "orchestrator", None, phase=None, gate="phase-end"),  # gate, but phase null
        event(at(4), "agent_start", "pm-tracker", "C", phase=None),
    )
    assert [(e.phase, e.phase_inferred) for e in evs] == [
        ("p1", False),
        ("p1", False),
        (None, False),
        (None, False),
        (None, False),
    ]


def test_recorded_phase_is_never_overwritten_and_latest_gate_wins(tmp_path):
    evs = events_of(
        tmp_path,
        event(at(0), "gate_approved", "orchestrator", None, phase="p1", gate="phase-end"),
        event(at(1), "agent_start", "w", "A", phase="p2"),  # explicit phase differs from the gate: keep p2
        event(at(2), "gate_requested", "orchestrator", None, phase="p2", gate="phase-end"),
        event(at(3), "agent_start", "pm-tracker", "B", phase=None),  # the MOST RECENT gate (p2) decides
    )
    assert [(e.phase, e.phase_inferred) for e in evs] == [("p1", False), ("p2", False), ("p2", False), ("p2", True)]


def test_attribution_is_not_written_back_and_is_idempotent(tmp_path):
    root = make_project(
        tmp_path / "p",
        events=[
            json.dumps(event(at(0), "gate_approved", "orchestrator", None, phase="p1", gate="phase-end")),
            json.dumps(event(at(1), "agent_start", "pm-tracker", "B", phase=None)),
        ],
    )
    log = root / ".workbench" / "events-2026-10.jsonl"
    before = log.read_bytes()
    first, _ = load_events(root)
    assert first[1].phase == "p1" and first[1].phase_inferred
    assert log.read_bytes() == before  # the file still says phase: null
    assert json.loads(before.decode().splitlines()[1])["phase"] is None
    assert infer_phases(first) == first  # a second pass changes nothing


def test_analyze_attributes_hand_built_events_and_carries_the_mark(tmp_path):
    def ev(minutes, kind, **kw):
        base = dict(
            ts=T0 + timedelta(minutes=minutes), event=kind, session_id="s1", agent_id=None, agent="a",
            phase=None, gate=None, usage=None,
        )
        base.update(kw)
        return Event(**base)

    events = [
        ev(0, "gate_requested", agent="orchestrator", phase="p1", gate="phase-end"),
        ev(1, "agent_start", agent_id="X", agent="pm-tracker"),
        ev(2, "agent_stop", agent_id="X", agent="pm-tracker", usage=an.Usage("sonnet", 1, 2, 3, 4)),
        ev(3, "gate_approved", agent="orchestrator", phase=None, gate="phase-end"),  # null, still pairs with the request
        ev(4, "agent_start", agent_id="Y", agent="fieldbook-writer"),  # stays running (not stalled yet)
    ]
    result = an.analyze(events, T0 + timedelta(minutes=10))
    by_agent = {r.agent: r for r in result.runs}
    assert by_agent["pm-tracker"].phase == "p1" and by_agent["pm-tracker"].phase_inferred
    assert result.gate_waits[0].phase == "p1" and result.gate_waits[0].resolution == "approved"
    assert result.phase_timings["p1"].inferred_events == 4  # start, stop, gate_approved, start
    assert result.usage_by_phase["p1"].stops == 1 and result.usage_by_phase["p1"].inferred == 1
    assert result.duration_by_phase["p1"].count == 1
    stalled = an.analyze(events, T0 + timedelta(hours=3))
    item = [i for i in an.action_items([], stalled) if i.kind == an.ACTION_STALLED][0]
    assert item.phase_id == "p1" and item.phase_inferred
