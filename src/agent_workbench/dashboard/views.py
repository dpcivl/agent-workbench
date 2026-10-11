"""Streamlit UI (thin layer over ``loader`` and ``analysis``). Read-only, Korean text.

Nothing here writes to a project. Tables use ``st.table`` (real HTML tables with
header cells) rather than the canvas-based ``st.dataframe``; status is always
given as words plus a symbol, never by color alone.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import altair as alt
import pandas as pd
import streamlit as st

from . import analysis as an
from .fmt import NA, fmt_dt, fmt_duration, fmt_int, fmt_number
from .loader import (
    PHASE_STATUSES,
    PROJECT_ENV,
    LoadError,
    ProjectData,
    ProjectRef,
    Registry,
    display_path,
    load_project,
    load_registry,
    single_project_ref,
)
from .messages import (
    GATE_RESOLUTION_LABEL,
    PHASE_INFERRED_MARK,
    PHASE_INFERRED_NOTE,
    PHASE_STATUS_LABEL,
    RUN_OUTCOME_LABEL,
    RUN_OUTCOME_SYMBOL,
    SEVERITY_LABEL,
    TASK_STATUS_LABEL,
    VERDICT_LABEL,
    describe_error,
)

TABS = ("개요", "타임라인", "산출물", "리뷰", "운영 지표", "KPI")
CACHE_TTL_SECONDS = 15


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------


def _table(rows: Sequence[dict], empty: str = "표시할 항목이 없습니다.") -> None:
    """Accessible table: header cells, text-only values, meaningful first column as index."""
    if not rows:
        st.caption(empty)
        return
    frame = pd.DataFrame([{k: str(v) for k, v in row.items()} for row in rows])
    first = frame.columns[0]
    if frame[first].is_unique:
        frame = frame.set_index(first)
    else:
        frame.index = pd.RangeIndex(1, len(frame) + 1)
    st.table(frame)


def _show_chart(chart: alt.Chart) -> None:
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:  # streamlit < 1.49 has no `width` parameter
        st.altair_chart(chart, use_container_width=True)


def _label(phase_id: Optional[str]) -> str:
    return phase_id if phase_id else "(단계 없음)"


def _phase_text(phase_id: Optional[str], inferred: bool = False) -> str:
    """Phase for display; a reader-attributed phase (12-1) carries a visible text marker."""
    text = _label(phase_id)
    return f"{text} {PHASE_INFERRED_MARK}" if phase_id and inferred else text


def _inferred_note(count: int, short: bool = False) -> None:
    """Caption under a table that shows phases: explains the `(추정)` marker (once per section)."""
    if not count:
        return
    if short:
        st.caption(f"`{PHASE_INFERRED_MARK}`: 단계를 추정한 항목 {count}건 (위 설명 참고).")
    else:
        st.caption(f"{PHASE_INFERRED_NOTE} 이 표의 해당 항목: {count}건.")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _cached_project(path: str) -> ProjectData:
    return load_project(path)


# --------------------------------------------------------------------------
# sidebar and project selection
# --------------------------------------------------------------------------


def _show_error(error: LoadError) -> None:
    st.error("프로젝트를 열 수 없습니다.")
    st.markdown(describe_error(error))
    st.stop()


def _select_project() -> ProjectRef:
    st.sidebar.header("Agent Workbench")
    single = os.environ.get(PROJECT_ENV)
    if single:
        ref = single_project_ref(single)
        st.sidebar.markdown(f"**프로젝트** · {ref.id}")
        st.sidebar.caption("`--project`로 연 단일 프로젝트 (등록부 사용 안 함)")
        return ref
    try:
        registry: Registry = load_registry()
    except LoadError as exc:
        _show_error(exc)
        raise  # unreachable: st.stop() ends the run
    by_id = {p.id: p for p in registry.projects}
    chosen = st.sidebar.selectbox("프로젝트", list(by_id), help="등록부 파일의 프로젝트 목록")
    st.sidebar.caption(f"등록부: {display_path(registry.source)}")
    return by_id[chosen]


def _sidebar_settings() -> timedelta:
    minutes = st.sidebar.number_input(
        "정체 판단 기준 (분)",
        min_value=1,
        max_value=60 * 24 * 14,
        value=int(an.DEFAULT_STALLED_AFTER.total_seconds() // 60),
        step=5,
        help="종료 이벤트 없이 이 시간이 지난 에이전트 실행을 '정체'로 표시합니다.",
    )
    st.sidebar.caption("기본 30분, 사이드바에서 변경 가능")
    if st.sidebar.button("새로고침"):
        st.cache_data.clear()
    st.sidebar.divider()
    st.sidebar.caption("읽기 전용 · 로컬 전용 · 네트워크 호출과 사용 통계 수집 없음")
    return timedelta(minutes=int(minutes))


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------


def _completion(statuses) -> tuple[int, int]:
    return sum(1 for s in statuses if s.exists), len(statuses)


def _since_text(item: an.ActionItem, now: datetime) -> str:
    if item.since is None:
        return ""
    return f" · {fmt_duration(max(now - item.since, timedelta(0)))} 경과"


def _render_actions(items: list[an.ActionItem], now: datetime) -> None:
    st.subheader("지금 내가 해야 할 일")
    with st.container(border=True):
        if not items:
            st.markdown("**✓ 지금 처리할 항목이 없습니다.** 승인 대기, 차단, 정체 항목이 모두 없습니다.")
            return
        review = [i for i in items if i.kind == an.ACTION_REVIEW]
        blocked = [i for i in items if i.kind == an.ACTION_BLOCKED]
        stalled = [i for i in items if i.kind == an.ACTION_STALLED]
        st.markdown(f"총 **{len(items)}건** — 승인 대기 {len(review)}건 · 차단 {len(blocked)}건 · 정체 {len(stalled)}건")
        lines: list[str] = []
        for i in review:
            gate = f" (게이트: `{i.gate}`)" if i.gate else ""
            lines.append(f"- **◆ 승인 대기** · `{i.phase_id}` {i.phase_name}{gate}{_since_text(i, now)}")
        for i in blocked:
            lines.append(f"- **✕ 차단** · `{i.phase_id}` {i.phase_name} — 막힌 이유를 확인하고 풀어 주세요")
        for i in stalled:
            where = f"`{i.phase_id}` {PHASE_INFERRED_MARK}" if i.phase_id and i.phase_inferred else (f"`{i.phase_id}`" if i.phase_id else "단계 없음")
            lines.append(f"- **! 정체** · 에이전트 `{i.agent}` ({where}) — 종료 이벤트 없음{_since_text(i, now)}")
        st.markdown("\n".join(lines))
        if any(i.phase_inferred for i in stalled):
            st.caption(PHASE_INFERRED_NOTE)


def _phase_card(data: ProjectData, phase) -> None:
    done_tasks = sum(1 for t in phase.tasks if t.status == "done")
    found, total = _completion(data.deliverables.get(phase.id, ()))
    with st.container(border=True):
        st.markdown(f"**{phase.id}**  \n{phase.name}")
        parts = []
        parts.append(f"태스크 {done_tasks}/{len(phase.tasks)}" if phase.tasks else "태스크 없음")
        parts.append(f"산출물 {found}/{total}" if total else "산출물 선언 없음")
        st.caption(" · ".join(parts))
        if phase.agents:
            st.caption("에이전트: " + ", ".join(phase.agents))


def _render_board(data: ProjectData) -> None:
    st.subheader("단계 진행판")
    if not data.phases:
        st.info("`project.yaml`에 단계(phases)가 없습니다.")
        return
    columns = st.columns(len(PHASE_STATUSES))
    for column, status in zip(columns, PHASE_STATUSES):
        phases = [p for p in data.phases if p.status == status]
        with column:
            st.markdown(f"**{PHASE_STATUS_LABEL[status]}** · {len(phases)}")
            if not phases:
                st.caption("없음")
            for phase in phases:
                _phase_card(data, phase)
    unknown = [p for p in data.phases if p.status not in PHASE_STATUSES]
    if unknown:
        st.warning("알 수 없는 상태의 단계: " + ", ".join(f"{p.id}({p.status})" for p in unknown))


def _render_completion(data: ProjectData) -> None:
    st.subheader("산출물 완료 현황")
    rows = []
    for phase in data.phases:
        statuses = data.deliverables.get(phase.id, ())
        found, total = _completion(statuses)
        mtimes = [s.latest_mtime for s in statuses if s.latest_mtime]
        rows.append(
            {
                "단계": phase.id,
                "상태": PHASE_STATUS_LABEL.get(phase.status, phase.status),
                "산출물": f"{found}/{total} ({round(100 * found / total)}%)" if total else "선언 없음",
                "미완료 항목": ", ".join(f"`{s.pattern}`" for s in statuses if not s.exists) or "-",
                "최종 수정": fmt_dt(max(mtimes)) if mtimes else NA,
            }
        )
    _table(rows, "단계가 없습니다.")
    st.caption("완료 판단: 선언된 경로나 글롭에 일치하는 파일이 있어야 '있음'으로 셉니다. 내용의 품질은 보지 않습니다.")


def render_overview(data: ProjectData, result: an.Analysis) -> None:
    _render_actions(an.action_items(data.phases, result), result.now)
    _render_board(data)
    _render_completion(data)


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


def timeline_frame(data: ProjectData, result: an.Analysis, phase_id: Optional[str]) -> pd.DataFrame:
    order = {p.id: i for i, p in enumerate(data.phases)}
    rows = []
    for run in sorted(result.runs, key=lambda r: (order.get(r.phase or "", 10**6), r.start)):
        if phase_id is not None and run.phase != phase_id:
            continue
        end = run.end or max(result.now, run.start)
        who = run.agent or "(unknown)"
        phase_text = _phase_text(run.phase, run.phase_inferred)
        row_label = who if phase_id is not None else f"{phase_text} / {who}"
        rows.append(
            {
                "행": row_label,
                "단계": phase_text,
                "에이전트": who,
                "시작": run.start.astimezone(timezone.utc),
                "종료": end.astimezone(timezone.utc),
                "상태": RUN_OUTCOME_LABEL[run.outcome],
                "기호": RUN_OUTCOME_SYMBOL[run.outcome],
                "소요 시간": fmt_duration(run.duration) if run.duration is not None else f"{fmt_duration(run.age(result.now))} 경과 (종료 없음)",
            }
        )
    return pd.DataFrame(rows)


def timeline_chart(frame: pd.DataFrame) -> alt.Chart:
    """Bars from start to end; an end marker whose SHAPE (not only color) encodes the status."""
    order = list(dict.fromkeys(frame["행"]))
    domain = [RUN_OUTCOME_LABEL[k] for k in ("completed", "error", "running", "stalled")]
    color = alt.Color(
        "상태:N",
        title="상태",
        scale=alt.Scale(domain=domain, range=["#4C78A8", "#E45756", "#72B7B2", "#F58518"]),
    )
    shape = alt.Shape(
        "상태:N",
        title="상태",
        scale=alt.Scale(domain=domain, range=["circle", "cross", "triangle-up", "diamond"]),
    )
    y = alt.Y("행:N", sort=order, title=None, axis=alt.Axis(labelLimit=320))
    x = alt.X("시작:T", title="시각", axis=alt.Axis(format="%m-%d", tickCount="day"))
    tooltip = [
        "단계",
        "에이전트",
        "상태",
        "소요 시간",
        alt.Tooltip("시작:T", format="%Y-%m-%d %H:%M"),
        alt.Tooltip("종료:T", format="%Y-%m-%d %H:%M"),
    ]
    base = alt.Chart(frame)
    bars = base.mark_bar(size=12).encode(x=x, x2="종료:T", y=y, color=color, tooltip=tooltip)
    ends = base.mark_point(filled=True, size=110, opacity=1).encode(
        x=alt.X("종료:T"), y=y, color=color, shape=shape, tooltip=tooltip
    )
    return (bars + ends).properties(height=max(120, 34 * len(order) + 40))


def _render_error_causes(result: an.Analysis, phase_id: Optional[str]) -> None:
    """Error causes: the masked `summary` of `agent_error` events, as plain text (11-2).

    Rendered with `st.text`, so nothing in the summary is interpreted as markdown or HTML.
    Summaries of other event kinds are never loaded, so they cannot appear here.
    """
    items = an.error_items(result, phase_id, all_phases=phase_id is None)
    if not items:
        return
    st.markdown(f"#### 오류 원인 ({len(items)}건)")
    for item in items:
        who = item.agent or "(unknown)"
        where = _phase_text(item.phase, item.phase_inferred)
        pairing = "" if item.paired else " · 짝이 되는 시작 이벤트 없음"
        st.text(f"✗ {who} ({where}) · {fmt_dt(item.ts)}{pairing}\n    {item.cause}")
    st.caption("`agent_error` 이벤트의 `summary`(응답 앞 200자, 절대경로 가림)만 보여 줍니다. 다른 이벤트의 요약은 읽지도 보이지도 않습니다.")


def render_timeline(data: ProjectData, result: an.Analysis) -> None:
    st.subheader("에이전트 타임라인")
    if not result.runs:
        st.info("에이전트 실행 기록이 없습니다. `.workbench/events-*.jsonl`에 `agent_start` 이벤트가 쌓이면 표시됩니다.")
        return
    phase_ids = [p.id for p in data.phases]
    for run in result.runs:  # phases seen only in events
        if run.phase and run.phase not in phase_ids:
            phase_ids.append(run.phase)
    choice = st.selectbox("단계", ["전체"] + phase_ids, key="timeline_phase")
    frame = timeline_frame(data, result, None if choice == "전체" else choice)
    if frame.empty:
        st.info("선택한 단계에 에이전트 실행 기록이 없습니다.")
        return
    _show_chart(timeline_chart(frame))
    st.caption(
        "막대 = 시작부터 종료까지, 끝의 도형 = 상태 (원 완료 · 십자 오류 · 삼각형 진행 중 · 마름모 정체). 자세한 상태는 아래 표에 글자로 적혀 있습니다. "
        "종료 이벤트가 없는 실행은 현재 시각까지 그립니다. 병렬 실행은 막대가 겹칠 수 있습니다."
    )
    table_rows = [
        {
            "단계": r["단계"],
            "에이전트": r["에이전트"],
            "시작": fmt_dt(r["시작"]),
            "종료": fmt_dt(r["종료"]) if r["기호"] in ("✓", "✗") else "-",
            "소요 시간": r["소요 시간"],
            "상태": r["상태"],
        }
        for r in frame.to_dict("records")
    ]
    _table(table_rows)
    _inferred_note(sum(1 for r in result.runs if r.phase_inferred and (choice == "전체" or r.phase == choice)))
    _render_error_causes(result, None if choice == "전체" else choice)
    if result.unmatched:
        kinds = Counter(e.event for e in result.unmatched)
        st.caption(
            "짝을 찾지 못한 종료/오류 이벤트: "
            + ", ".join(f"{k} {v}건" for k, v in kinds.items())
            + " (대응하는 `agent_start`가 없음)"
        )


# --------------------------------------------------------------------------
# deliverables
# --------------------------------------------------------------------------


def render_deliverables(data: ProjectData) -> None:
    st.subheader("산출물")
    if not data.phases:
        st.info("단계가 없습니다.")
        return
    for phase in data.phases:
        statuses = data.deliverables.get(phase.id, ())
        found, total = _completion(statuses)
        summary = f"{found}/{total} 완료" if total else "선언된 산출물 없음"
        st.markdown(f"#### `{phase.id}` {phase.name} — {summary}")
        rows = [
            {
                "선언 경로/글롭": s.pattern,
                "종류": "글롭" if s.is_glob else "경로",
                "존재": "✓ 있음" if s.exists else "✗ 없음",
                "일치 수": f"{s.match_count}+" if s.truncated else str(s.match_count),
                "최종 수정": fmt_dt(s.latest_mtime),
            }
            for s in statuses
        ]
        _table(rows, "선언된 산출물이 없습니다.")
        listed = [s for s in statuses if s.is_glob and s.match_count]
        if listed:
            with st.expander("글롭에 일치한 파일"):
                for s in listed:
                    st.markdown(f"`{s.pattern}`")
                    st.markdown("\n".join(f"- `{m}`" for m in s.matches[:50]))
                    if s.match_count > 50:
                        st.caption(f"... 외 {s.match_count - 50}건")
    st.markdown("#### 챕터 초안 (`docs/fieldbook/`)")
    st.caption("챕터는 단계 산출물과 별도로 읽습니다.")
    _table(
        [{"파일": c.rel_path, "최종 수정": fmt_dt(c.mtime)} for c in data.chapters],
        "아직 챕터 초안이 없습니다.",
    )


# --------------------------------------------------------------------------
# reviews
# --------------------------------------------------------------------------


def render_reviews(data: ProjectData) -> None:
    st.subheader("리뷰")
    if not data.reviews:
        st.info("리뷰가 없습니다. `reviewer`가 `.workbench/reviews/<단계>-<n>.json`을 남기면 표시됩니다.")
        return
    rows = []
    for r in data.reviews:
        rows.append(
            {
                "파일": r.rel_path,
                "단계": r.phase,
                "회차": str(r.n),
                "판정": VERDICT_LABEL.get(r.verdict or "", NA),
                "지적": str(len(r.findings)),
                "미해결": str(len(r.unresolved)),
                "반대 증거": fmt_int(r.counter_evidence_count),
                "계약 검증": "✓ 유효" if r.valid else "✗ 위반 있음",
            }
        )
    _table(rows)

    latest = {p.id: data.latest_review(p.id) for p in data.phases}
    for review in data.reviews:
        is_latest = latest.get(review.phase) is review or review.phase not in latest
        title = f"{review.phase} · {review.n}회차 · {VERDICT_LABEL.get(review.verdict or '', NA)}"
        with st.expander(title + (" (최신)" if is_latest else ""), expanded=is_latest):
            if review.errors:
                st.warning("계약 검증 문제:\n\n" + "\n".join(f"- {e}" for e in review.errors[:8]))
            st.markdown(
                f"반대 증거 **{fmt_int(review.counter_evidence_count)}건** · "
                f"지적 **{len(review.findings)}건** (미해결 **{len(review.unresolved)}건**)"
            )
            if review.findings:
                counts = Counter((f.severity, f.resolved) for f in review.findings)
                _table(
                    [
                        {
                            "중요도": SEVERITY_LABEL[sev],
                            "해소됨": str(counts.get((sev, True), 0)),
                            "미해결": str(counts.get((sev, False), 0)),
                        }
                        for sev in ("high", "medium", "low")
                    ]
                )
                _table(
                    [
                        {
                            "분류": f.category or NA,
                            "중요도": SEVERITY_LABEL.get(f.severity, f.severity),
                            "상태": "✓ 해소됨" if f.resolved else "✗ 미해결",
                        }
                        for f in sorted(review.findings, key=lambda f: (f.resolved, ("high", "medium", "low").index(f.severity) if f.severity in ("high", "medium", "low") else 9))
                    ]
                )
            else:
                st.caption("지적이 없습니다.")
            if review.summary_rel_path:
                st.markdown(f"요약 파일: `{review.summary_rel_path}`")
                with st.expander("요약 미리보기"):
                    st.markdown(review.summary_text or "(비어 있음)")
            else:
                st.caption(f"요약 파일(`{review.phase}-{review.n}.md`)이 없습니다.")


# --------------------------------------------------------------------------
# operations metrics
# --------------------------------------------------------------------------


def _usage_row(key: str, usage: an.UsageSum, key_title: str) -> dict:
    missing = f" · 추출 실패 {usage.missing}건" if usage.missing else ""
    inferred = f" · 단계 {PHASE_INFERRED_MARK} {usage.inferred}건 포함" if usage.inferred else ""
    return {
        key_title: key,
        "종료 이벤트": f"{usage.stops}건{missing}{inferred}",
        "입력(캐시 제외)": fmt_int(usage.input_tokens),
        "캐시 생성 입력": fmt_int(usage.cache_creation_input_tokens),
        "캐시 읽기 입력": fmt_int(usage.cache_read_input_tokens),
        "총 입력": fmt_int(usage.total_input),
        "출력": fmt_int(usage.output_tokens),
        "모델": ", ".join(usage.models) or NA,
    }


def render_operations(data: ProjectData, result: an.Analysis) -> None:
    st.subheader("운영 지표 (오케스트레이션)")
    st.caption("이벤트 로그에서 계산한 실행 지표입니다. 모델 정확도·절감량 같은 프로젝트 지표는 KPI 탭에 따로 있습니다.")
    if not data.events:
        st.info("이벤트가 없어 계산할 지표가 없습니다.")
        return

    completed = result.completed
    first = st.columns(3)
    first[0].metric("에이전트 실행", len(result.runs))
    first[1].metric("완료 / 오류", f"{len(completed)} / {len(result.errors)}")
    first[2].metric("정체", len(result.stalled), help=f"종료 이벤트 없이 {fmt_duration(result.stalled_after)} 초과")
    mean_wait = None
    if result.approved_waits:
        mean_wait = sum((g.wait for g in result.approved_waits), timedelta(0)) / len(result.approved_waits)
    second = st.columns(3)
    second[0].metric("평균 승인 대기", fmt_duration(mean_wait), help="gate_requested → gate_approved, 승인된 건만")
    second[1].metric("재작업 (재실행)", sum(r.reruns for r in result.rework), help="같은 단계에서 같은 에이전트를 다시 시작한 횟수의 합")
    second[2].metric("현재 승인 대기", len(result.pending_gates), help="gate_requested 후 아직 응답이 없는 게이트")

    st.markdown("#### 에이전트별 소요 시간")
    _table(
        [
            {
                "에이전트": agent,
                "완료 횟수": str(s.count),
                "평균": fmt_duration(s.mean),
                "중앙값": fmt_duration(s.median),
                "최장": fmt_duration(s.longest),
                "합계": fmt_duration(s.total),
            }
            for agent, s in result.duration_by_agent.items()
        ],
        "완료된 실행이 없습니다.",
    )
    st.caption("완료(`agent_stop`)된 실행만 셉니다. 병렬 실행은 구간이 겹치므로 합계가 실제 경과 시간이 아닙니다.")

    st.markdown("#### 단계별 리드 타임")
    _table(
        [
            {
                "단계": _label(t.phase),
                "추정 포함": f"{t.inferred_events}건 {PHASE_INFERRED_MARK}" if t.inferred_events else "-",
                "첫 시작": fmt_dt(t.first_start),
                "승인": fmt_dt(t.approved_at),
                "리드 타임": fmt_duration(t.lead_time) if t.lead_time is not None else ("진행 중 / 미승인" if t.first_start else NA),
                "승인 대기 합": fmt_duration(t.gate_wait_total) if t.gate_wait_total else "-",
                "수정 요청": str(t.change_requested),
                "수정 승인": str(t.change_approved),
            }
            for t in result.phase_timings.values()
        ],
        "단계 정보가 있는 이벤트가 없습니다.",
    )
    st.caption("리드 타임 = 단계의 첫 `agent_start`부터 `gate_approved`까지 (승인 대기 시간 포함).")
    _inferred_note(sum(t.inferred_events for t in result.phase_timings.values()))

    st.markdown("#### 승인 대기 (게이트)")
    _table(
        [
            {
                "단계": _phase_text(g.phase, g.phase_inferred),
                "게이트": g.gate or NA,
                "요청": fmt_dt(g.requested),
                "응답": fmt_dt(g.resolved) if g.resolved else "-",
                "결과": GATE_RESOLUTION_LABEL[g.resolution],
                "대기 시간": fmt_duration(g.wait),
            }
            for g in result.gate_waits
        ],
        "게이트 요청 이벤트가 없습니다.",
    )
    _inferred_note(sum(1 for g in result.gate_waits if g.phase_inferred), short=True)

    st.markdown("#### 재작업")
    _table(
        [
            {
                "단계": _label(r.phase),
                "에이전트": r.agent,
                "시작 횟수": f"{r.starts} (추정 {r.inferred_starts}건 포함)" if r.inferred_starts else str(r.starts),
                "재실행": str(r.reruns),
            }
            for r in result.rework
        ],
        "같은 단계에서 같은 에이전트를 두 번 이상 시작한 기록이 없습니다.",
    )
    st.caption("재작업 = (단계, 에이전트)별 `agent_start`가 2회 이상인 경우. 재실행 횟수 = 시작 횟수 - 1.")
    _inferred_note(sum(r.inferred_starts for r in result.rework), short=True)

    st.markdown("#### 토큰 사용량")
    st.caption("총 입력 = 입력(캐시 제외) + 캐시 생성 입력 + 캐시 읽기 입력. 사용량이 없는 종료 이벤트는 합계에서 빼고 '추출 실패'로 셉니다.")
    _table([_usage_row("전체", result.usage_total, "구분")])
    left, right = st.columns(2)
    with left:
        st.markdown("**에이전트별**")
        _table([_usage_row(a, u, "에이전트") for a, u in result.usage_by_agent.items()], "종료 이벤트가 없습니다.")
    with right:
        st.markdown("**단계별**")
        _table([_usage_row(_label(p), u, "단계") for p, u in result.usage_by_phase.items()], "종료 이벤트가 없습니다.")
        _inferred_note(sum(u.inferred for u in result.usage_by_phase.values()), short=True)


# --------------------------------------------------------------------------
# KPI
# --------------------------------------------------------------------------

_KPI_TITLES = {"model": "모델 지표 (`model`)", "savings": "절감 지표 (`savings`)"}


def render_kpi(data: ProjectData) -> None:
    st.subheader("프로젝트 KPI")
    st.caption("`results/<단계 id>/metrics.json`의 `model`, `savings` 키에서 숫자 항목만 읽습니다. 운영 지표는 '운영 지표' 탭에 있습니다.")
    shown = False
    for metrics in data.metrics:
        st.markdown(f"#### `{metrics.phase_id}` — `{metrics.rel_path}` (수정 {fmt_dt(metrics.mtime)})")
        if metrics.error:
            st.warning("metrics.json을 읽을 수 없습니다 (JSON 형식을 확인하세요).")
            continue
        if not metrics.present_keys:
            st.caption("`model`, `savings` 키가 아직 없습니다.")
            continue
        for key in ("model", "savings"):
            if key not in metrics.present_keys:
                continue
            entries = metrics.sections.get(key, ())
            st.markdown(f"**{_KPI_TITLES[key]}**")
            if not entries:
                st.caption("숫자 항목이 없습니다.")
                continue
            shown = True
            for start in range(0, len(entries), 4):
                chunk = entries[start : start + 4]
                for column, (label, value) in zip(st.columns(4), chunk):
                    column.metric(label, fmt_number(value))
    if not data.metrics:
        st.info(
            "아직 KPI 데이터가 없습니다. 모델 학습과 절감 예측이 끝나 "
            "`results/<단계 id>/metrics.json`에 `model` 또는 `savings` 키가 기록되면 여기에 표시됩니다."
        )
    elif not shown:
        st.info("읽을 수 있는 숫자 항목이 아직 없습니다.")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def _header(data: ProjectData, result: an.Analysis) -> None:
    st.title(data.name)
    if data.description:
        st.caption(data.description)
    report = data.events_report
    inferred = sum(1 for e in data.events if e.phase_inferred)
    inferred_text = f" · 단계 추정 {inferred}건" if inferred else ""
    st.caption(
        f"기준 시각 {fmt_dt(result.now)} · 이벤트 {report.events_loaded}건 ({len(report.files)}개 파일){inferred_text} · "
        f"정체 기준 {fmt_duration(result.stalled_after)}"
    )
    if report.skipped:
        st.warning(
            f"이벤트 로그에서 읽을 수 없는 줄 {report.skipped}건을 건너뛰었습니다 "
            f"(JSON 형식 오류 {report.skipped_malformed}건, 알 수 없는 이벤트·시각 {report.skipped_invalid}건). "
            "나머지 이벤트로 계산했습니다."
        )
    if not report.files:
        st.info("이벤트 로그(`.workbench/events-*.jsonl`)가 아직 없습니다. 에이전트가 실행되면 쌓입니다.")
    problems = list(data.warnings)
    if report.schema_warnings:
        problems.append(f"이벤트 {report.schema_warnings}건이 계약 검증에 실패했지만 읽어서 사용했습니다.")
        problems.extend(report.warning_examples)
    if problems:
        with st.expander(f"계약 검증 경고 ({len(problems)})"):
            st.markdown("\n".join(f"- {p}" for p in problems[:20]))


def main() -> None:
    st.set_page_config(page_title="Agent Workbench", layout="wide")
    ref = _select_project()
    stalled_after = _sidebar_settings()
    try:
        data = _cached_project(str(ref.path))
    except LoadError as exc:
        _show_error(exc)
        return
    result = an.analyze(data.events, an.utcnow(), stalled_after)
    _header(data, result)
    tabs = st.tabs(list(TABS))
    with tabs[0]:
        render_overview(data, result)
    with tabs[1]:
        render_timeline(data, result)
    with tabs[2]:
        render_deliverables(data)
    with tabs[3]:
        render_reviews(data)
    with tabs[4]:
        render_operations(data, result)
    with tabs[5]:
        render_kpi(data)
