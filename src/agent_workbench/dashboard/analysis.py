"""Operations metrics computed from the event log (pure Python, no streamlit).

Definitions follow ``docs/contract.md`` ("대시보드 리더 메모") and DECISIONS 6/8:

* run          = ``agent_start`` paired with ``agent_stop`` by ``agent_id``.
* error        = ``agent_error`` paired by agent *name* with the nearest preceding
                 unfinished ``agent_start`` (``agent_id`` is not trusted for errors).
* stalled      = unfinished start older than a threshold (default 30 minutes,
                 confirmed by the user in decision 11-1a; adjustable in the sidebar).
* gate wait    = ``gate_requested`` -> ``gate_approved`` per (phase, gate).
* rework       = ``agent_start`` count per (phase, agent) above 1.
* lead time    = first ``agent_start`` of a phase -> its ``gate_approved``.
* usage        = sums over ``agent_stop`` events; null usage is excluded and counted.
* phase        = the recorded ``phase``; when it is null the reader attributes the
                 phase of the nearest earlier gate event of the same session and marks
                 the event ``phase_inferred`` (decision 12-1, ``loader.infer_phases``).
                 Every per-phase figure below carries that mark or a count of such events.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .loader import Event, Phase, Usage, infer_phases

DEFAULT_STALLED_AFTER = timedelta(minutes=30)  # confirmed (11-1a); the sidebar can override it

OUTCOME_COMPLETED = "completed"
OUTCOME_ERROR = "error"
OUTCOME_RUNNING = "running"
OUTCOME_STALLED = "stalled"


@dataclass(frozen=True)
class Run:
    agent_id: Optional[str]
    agent: Optional[str]
    phase: Optional[str]
    session_id: Optional[str]
    start: datetime
    end: Optional[datetime]
    outcome: str
    usage: Optional[Usage] = None
    error_summary: Optional[str] = None  # masked `summary` of the paired agent_error (11-2); else None
    phase_inferred: bool = False  # `phase` was attributed by the reader (12-1), not recorded

    @property
    def duration(self) -> Optional[timedelta]:
        """Start-to-end time; ``None`` while the run has no end event."""
        return None if self.end is None else self.end - self.start

    def age(self, now: datetime) -> timedelta:
        return max(now - self.start, timedelta(0))


@dataclass(frozen=True)
class GateWait:
    phase: Optional[str]
    gate: Optional[str]
    requested: datetime
    resolved: Optional[datetime]
    resolution: str  # "approved" | "change_requested" | "pending"
    wait: timedelta  # for pending: time since the request (as of ``now``)
    phase_inferred: bool = False  # the request event's phase was attributed by the reader (12-1)


@dataclass(frozen=True)
class ReworkRow:
    phase: Optional[str]
    agent: str
    starts: int
    inferred_starts: int = 0  # how many of the starts have an attributed (12-1) phase

    @property
    def reruns(self) -> int:
        return self.starts - 1


@dataclass(frozen=True)
class PhaseTiming:
    phase: Optional[str]
    first_start: Optional[datetime]
    approved_at: Optional[datetime]
    lead_time: Optional[timedelta]  # None until the phase has a gate_approved
    gate_wait_total: timedelta
    change_requested: int
    change_approved: int
    inferred_events: int = 0  # events counted for this phase whose phase was attributed (12-1)


@dataclass(frozen=True)
class DurationStat:
    count: int
    total: timedelta
    mean: timedelta
    median: timedelta
    longest: timedelta


@dataclass
class UsageSum:
    """Token totals; every token field is ``None`` ('n/a') when no run had usable usage."""

    stops: int = 0  # agent_stop events seen
    with_usage: int = 0  # ... with complete usage (counted in the sums)
    missing: int = 0  # ... with null or incomplete usage (excluded)
    inferred: int = 0  # ... whose phase was attributed by the reader (12-1)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    models: tuple[str, ...] = ()

    @property
    def total_input(self) -> Optional[int]:
        parts = (self.input_tokens, self.cache_creation_input_tokens, self.cache_read_input_tokens)
        return None if None in parts else sum(parts)  # type: ignore[arg-type]


@dataclass
class Analysis:
    now: datetime
    stalled_after: timedelta
    runs: list[Run] = field(default_factory=list)
    unmatched: list[Event] = field(default_factory=list)  # agent_stop/agent_error without a start
    gate_waits: list[GateWait] = field(default_factory=list)
    rework: list[ReworkRow] = field(default_factory=list)  # starts > 1 only
    phase_timings: dict[Optional[str], PhaseTiming] = field(default_factory=dict)
    duration_by_agent: dict[str, DurationStat] = field(default_factory=dict)
    duration_by_phase: dict[Optional[str], DurationStat] = field(default_factory=dict)
    usage_by_agent: dict[str, UsageSum] = field(default_factory=dict)
    usage_by_phase: dict[Optional[str], UsageSum] = field(default_factory=dict)
    usage_total: UsageSum = field(default_factory=UsageSum)

    @property
    def stalled(self) -> list[Run]:
        return [r for r in self.runs if r.outcome == OUTCOME_STALLED]

    @property
    def completed(self) -> list[Run]:
        return [r for r in self.runs if r.outcome == OUTCOME_COMPLETED]

    @property
    def errors(self) -> list[Run]:
        return [r for r in self.runs if r.outcome == OUTCOME_ERROR]

    @property
    def approved_waits(self) -> list[GateWait]:
        return [g for g in self.gate_waits if g.resolution == "approved"]

    @property
    def pending_gates(self) -> list[GateWait]:
        return [g for g in self.gate_waits if g.resolution == "pending"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# pairing
# --------------------------------------------------------------------------


@dataclass
class _Open:
    event: Event
    end: Optional[datetime] = None
    outcome: Optional[str] = None
    usage: Optional[Usage] = None
    error_summary: Optional[str] = None


def pair_runs(events: Iterable[Event], now: datetime, stalled_after: timedelta = DEFAULT_STALLED_AFTER) -> tuple[list[Run], list[Event]]:
    """Pair starts with stops/errors. Returns ``(runs, unmatched_end_events)``."""
    ordered = sorted(events, key=lambda e: e.ts)  # stable: file order breaks ties
    records: list[_Open] = []
    open_: list[_Open] = []  # unfinished starts, chronological
    unmatched: list[Event] = []

    for ev in ordered:
        if ev.event == "agent_start":
            rec = _Open(ev)
            records.append(rec)
            open_.append(rec)
        elif ev.event == "agent_stop":
            if ev.agent_id is not None:
                cands = [r for r in open_ if r.event.agent_id == ev.agent_id]
            else:  # no id to pair by: fall back to the agent name
                cands = [r for r in open_ if ev.agent is not None and r.event.agent == ev.agent]
            if not cands:
                unmatched.append(ev)
                continue
            rec = cands[-1]
            rec.end, rec.outcome, rec.usage = ev.ts, OUTCOME_COMPLETED, ev.usage
            open_.remove(rec)
        elif ev.event == "agent_error":
            cands = [r for r in open_ if ev.agent is not None and r.event.agent == ev.agent and r.event.ts <= ev.ts]
            if not cands:
                unmatched.append(ev)
                continue
            rec = cands[-1]  # nearest preceding start of the same agent name
            rec.end, rec.outcome, rec.error_summary = ev.ts, OUTCOME_ERROR, ev.summary
            open_.remove(rec)

    runs: list[Run] = []
    for rec in records:
        ev = rec.event
        outcome = rec.outcome
        if outcome is None:
            outcome = OUTCOME_STALLED if now - ev.ts > stalled_after else OUTCOME_RUNNING
        runs.append(
            Run(ev.agent_id, ev.agent, ev.phase, ev.session_id, ev.ts, rec.end, outcome, rec.usage, rec.error_summary, ev.phase_inferred)
        )
    return runs, unmatched


def pair_gates(events: Iterable[Event], now: datetime) -> list[GateWait]:
    """gate_requested -> gate_approved per (phase, gate); a change_requested also ends a wait."""
    ordered = sorted(events, key=lambda e: e.ts)
    pending: list[Event] = []
    waits: list[GateWait] = []

    def find(phase: Optional[str], gate: Optional[str]) -> Optional[Event]:
        exact = [p for p in pending if p.phase == phase and (gate is None or p.gate == gate)]
        if exact:
            return exact[-1]
        # the phase is derived from the `doing` phase when the event is written,
        # so it can legitimately be null on one side: fall back to the gate id.
        loose = [p for p in pending if (gate is None or p.gate == gate) and (p.phase is None or phase is None)]
        return loose[-1] if loose else None

    for ev in ordered:
        if ev.event == "gate_requested":
            if not any(p.phase == ev.phase and p.gate == ev.gate for p in pending):
                pending.append(ev)  # a duplicate request keeps the earliest wait start
        elif ev.event in ("gate_approved", "change_requested"):
            req = find(ev.phase, ev.gate)
            if req is None:
                continue
            pending.remove(req)
            resolution = "approved" if ev.event == "gate_approved" else "change_requested"
            waits.append(GateWait(req.phase, req.gate, req.ts, ev.ts, resolution, ev.ts - req.ts, req.phase_inferred))
    for req in pending:
        waits.append(GateWait(req.phase, req.gate, req.ts, None, "pending", max(now - req.ts, timedelta(0)), req.phase_inferred))
    waits.sort(key=lambda g: g.requested)
    return waits


# --------------------------------------------------------------------------
# aggregates
# --------------------------------------------------------------------------


def duration_stat(durations: list[timedelta]) -> Optional[DurationStat]:
    if not durations:
        return None
    seconds = [d.total_seconds() for d in durations]
    return DurationStat(
        count=len(durations),
        total=timedelta(seconds=sum(seconds)),
        mean=timedelta(seconds=sum(seconds) / len(seconds)),
        median=timedelta(seconds=statistics.median(seconds)),
        longest=timedelta(seconds=max(seconds)),
    )


def _sum_usage(usages: list[Optional[Usage]], inferred: int = 0) -> UsageSum:
    out = UsageSum(stops=len(usages), inferred=inferred)
    models: list[str] = []
    for usage in usages:
        if usage is None or not usage.complete:
            out.missing += 1
            continue
        out.with_usage += 1
        out.input_tokens = (out.input_tokens or 0) + usage.input_tokens  # type: ignore[operator]
        out.output_tokens = (out.output_tokens or 0) + usage.output_tokens  # type: ignore[operator]
        out.cache_creation_input_tokens = (out.cache_creation_input_tokens or 0) + usage.cache_creation_input_tokens  # type: ignore[operator]
        out.cache_read_input_tokens = (out.cache_read_input_tokens or 0) + usage.cache_read_input_tokens  # type: ignore[operator]
        if usage.model and usage.model not in models:
            models.append(usage.model)
    out.models = tuple(models)
    return out


def analyze(events: Iterable[Event], now: Optional[datetime] = None, stalled_after: timedelta = DEFAULT_STALLED_AFTER) -> Analysis:
    # decision 12-1: attribute null phases from the same session's latest gate event.
    # (load_events already did this; it is idempotent, so hand-built event lists work too.)
    events = infer_phases(sorted(events, key=lambda e: e.ts))
    now = now or utcnow()
    result = Analysis(now=now, stalled_after=stalled_after)
    result.runs, result.unmatched = pair_runs(events, now, stalled_after)
    result.gate_waits = pair_gates(events, now)

    # rework: agent_start per (phase, agent)
    starts: dict[tuple[Optional[str], str], int] = defaultdict(int)
    inferred_starts: dict[tuple[Optional[str], str], int] = defaultdict(int)
    for ev in events:
        if ev.event == "agent_start" and ev.agent:
            starts[(ev.phase, ev.agent)] += 1
            if ev.phase_inferred:
                inferred_starts[(ev.phase, ev.agent)] += 1
    result.rework = sorted(
        (ReworkRow(p, a, n, inferred_starts.get((p, a), 0)) for (p, a), n in starts.items() if n > 1),
        key=lambda r: (r.phase or "", r.agent),
    )

    # durations of completed runs (errors are timed separately and excluded here)
    by_agent: dict[str, list[timedelta]] = defaultdict(list)
    by_phase: dict[Optional[str], list[timedelta]] = defaultdict(list)
    for run in result.completed:
        assert run.duration is not None
        by_agent[run.agent or "(unknown)"].append(run.duration)
        by_phase[run.phase].append(run.duration)
    result.duration_by_agent = {k: s for k, v in sorted(by_agent.items()) if (s := duration_stat(v))}
    result.duration_by_phase = {k: s for k, v in sorted(by_phase.items(), key=lambda kv: kv[0] or "") if (s := duration_stat(v))}

    # phase timings
    phases: set[Optional[str]] = {e.phase for e in events if e.phase is not None}
    for phase in sorted(phases):
        mine = [e for e in events if e.phase == phase]
        first_start = next((e.ts for e in mine if e.event == "agent_start"), None)
        approved = [e.ts for e in mine if e.event == "gate_approved"]
        approved_at = max(approved) if approved else None
        lead = approved_at - first_start if (approved_at and first_start and approved_at >= first_start) else None
        waits = [g.wait for g in result.gate_waits if g.phase == phase and g.resolution != "pending"]
        result.phase_timings[phase] = PhaseTiming(
            phase=phase,
            first_start=first_start,
            approved_at=approved_at,
            lead_time=lead,
            gate_wait_total=sum(waits, timedelta(0)),
            change_requested=sum(1 for e in mine if e.event == "change_requested"),
            change_approved=sum(1 for e in mine if e.event == "change_approved"),
            inferred_events=sum(1 for e in mine if e.phase_inferred),
        )

    # usage: every agent_stop counts, whether or not its start was found
    stops = [e for e in events if e.event == "agent_stop"]
    result.usage_total = _sum_usage([e.usage for e in stops])
    agent_groups: dict[str, list[Optional[Usage]]] = defaultdict(list)
    phase_groups: dict[Optional[str], list[Optional[Usage]]] = defaultdict(list)
    phase_inferred_stops: dict[Optional[str], int] = defaultdict(int)
    for e in stops:
        agent_groups[e.agent or "(unknown)"].append(e.usage)
        phase_groups[e.phase].append(e.usage)
        if e.phase_inferred:
            phase_inferred_stops[e.phase] += 1
    result.usage_by_agent = {k: _sum_usage(v) for k, v in sorted(agent_groups.items())}
    result.usage_by_phase = {
        k: _sum_usage(v, phase_inferred_stops.get(k, 0)) for k, v in sorted(phase_groups.items(), key=lambda kv: kv[0] or "")
    }
    return result


# --------------------------------------------------------------------------
# error list (decision 11-2: the only place an event summary is shown)
# --------------------------------------------------------------------------

NO_CAUSE_RECORDED = "원인 미기록"


@dataclass(frozen=True)
class ErrorItem:
    """One ``agent_error``: which agent failed, when, and the masked ``summary`` (None = no cause recorded)."""

    ts: datetime
    agent: Optional[str]
    phase: Optional[str]
    summary: Optional[str]
    paired: bool  # False when no matching agent_start was found
    phase_inferred: bool = False  # `phase` attributed by the reader (12-1)

    @property
    def cause(self) -> str:
        return self.summary if self.summary else NO_CAUSE_RECORDED


def error_items(analysis: Analysis, phase_id: Optional[str] = None, all_phases: bool = True) -> list[ErrorItem]:
    """Errors in chronological order: paired runs plus unmatched ``agent_error`` events.

    With ``all_phases=False`` only errors whose phase equals ``phase_id`` are returned.
    """
    items: list[ErrorItem] = []
    for run in analysis.runs:
        if run.outcome == OUTCOME_ERROR and run.end is not None:
            items.append(ErrorItem(run.end, run.agent, run.phase, run.error_summary, True, run.phase_inferred))
    for ev in analysis.unmatched:
        if ev.event == "agent_error":
            items.append(ErrorItem(ev.ts, ev.agent, ev.phase, ev.summary, False, ev.phase_inferred))
    if not all_phases:
        items = [i for i in items if i.phase == phase_id]
    return sorted(items, key=lambda i: i.ts)


# --------------------------------------------------------------------------
# "what do I have to do now" list
# --------------------------------------------------------------------------

ACTION_REVIEW = "review"
ACTION_BLOCKED = "blocked"
ACTION_STALLED = "stalled"


@dataclass(frozen=True)
class ActionItem:
    kind: str  # "review" | "blocked" | "stalled"
    phase_id: Optional[str]
    phase_name: Optional[str]
    agent: Optional[str] = None
    gate: Optional[str] = None
    since: Optional[datetime] = None  # when the wait / stall started, if known
    phase_inferred: bool = False  # `phase_id` attributed by the reader (12-1); stalled items only


def action_items(phases: Iterable[Phase], analysis: Analysis) -> list[ActionItem]:
    """Phases awaiting approval, blocked phases, stalled agents - in that order."""
    phases = list(phases)
    names = {p.id: p.name for p in phases}
    pending = {g.phase: g for g in analysis.pending_gates}
    items: list[ActionItem] = []
    for p in phases:
        if p.status == "review":
            req = pending.get(p.id)
            items.append(ActionItem(ACTION_REVIEW, p.id, p.name, gate=p.gate, since=req.requested if req else None))
    for p in phases:
        if p.status == "blocked":
            items.append(ActionItem(ACTION_BLOCKED, p.id, p.name, gate=p.gate))
    for run in sorted(analysis.stalled, key=lambda r: r.start):
        items.append(
            ActionItem(
                ACTION_STALLED,
                run.phase,
                names.get(run.phase or ""),
                agent=run.agent,
                since=run.start,
                phase_inferred=run.phase_inferred,
            )
        )
    return items
