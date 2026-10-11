"""Streamlit smoke tests (AppTest): every tab renders without exceptions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

pytest.importorskip("streamlit")
pd = pytest.importorskip("pandas")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent_workbench.dashboard.loader import PROJECT_ENV, WORKSPACE_ENV  # noqa: E402

from conftest import DEMO, event, make_project, phase, usage  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "src" / "agent_workbench" / "dashboard" / "app.py"
TAB_LABELS = ["개요", "타임라인", "산출물", "리뷰", "운영 지표", "KPI"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    import streamlit as st

    st.cache_data.clear()
    monkeypatch.delenv(PROJECT_ENV, raising=False)
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no real registry can leak in
    yield
    st.cache_data.clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Route run_app's environment changes through monkeypatch so nothing leaks between tests."""
    global _MONKEYPATCH
    _MONKEYPATCH = monkeypatch
    yield
    _MONKEYPATCH = None


_MONKEYPATCH = None


def run_app(**env) -> AppTest:
    for key, value in env.items():
        _MONKEYPATCH.setenv(key, str(value))
    at = AppTest.from_file(str(APP), default_timeout=60)
    return at.run()


def all_text(at: AppTest) -> str:
    parts: list[str] = []
    for root in (at.main, at.sidebar):
        for el in root:
            value = getattr(el, "value", None)
            if isinstance(value, pd.DataFrame):
                parts.append(value.to_string())
                parts.extend(map(str, value.columns))
            elif isinstance(value, str):
                parts.append(value)
            label = getattr(el, "label", None)
            if isinstance(label, str):
                parts.append(label)
    return "\n".join(parts)


def test_demo_project_renders_every_tab():
    at = run_app(**{PROJECT_ENV: DEMO})
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == TAB_LABELS
    assert not at.error
    subheaders = [s.value for s in at.subheader]
    for expected in ("지금 내가 해야 할 일", "단계 진행판", "에이전트 타임라인", "산출물", "리뷰", "운영 지표 (오케스트레이션)", "프로젝트 KPI"):
        assert expected in subheaders
    assert len(at.table) >= 10  # real HTML tables (st.table), not canvas dataframes
    assert len(at.dataframe) == 0
    text = all_text(at)
    assert "승인 대기" in text and "정체" in text and "차단" in text  # the to-do list
    assert "p5-forecast" in text and "mape_pct" in text  # KPI card from metrics.json


def test_overview_action_list_content():
    at = run_app(**{PROJECT_ENV: DEMO})
    md = "\n".join(m.value for m in at.markdown)
    assert "**◆ 승인 대기** · `p5-forecast`" in md
    assert "**✕ 차단** · `p6-report`" in md
    assert "**! 정체** · 에이전트 `savings-simulator`" in md


def test_kpi_cards_show_numeric_entries_only():
    at = run_app(**{PROJECT_ENV: DEMO})
    cards = {m.label: m.value for m in at.metric}
    assert cards["mape_pct"] == "6.8"
    assert cards["train_rows"] == "18,240"
    assert cards["kwh_per_month.point"] == "1,840"
    assert "split" not in cards and "unit_price_source" not in cards


def test_stalled_threshold_is_configurable_in_the_sidebar():
    at = run_app(**{PROJECT_ENV: DEMO})
    assert any("기본 30분" in c.value and "변경 가능" in c.value for c in at.sidebar.caption)
    assert "임시" not in all_text(at) and "미확인" not in all_text(at)
    stalled_before = next(m for m in at.metric if m.label == "정체").value
    assert stalled_before == "1"
    at.sidebar.number_input[0].set_value(60 * 24 * 14).run()  # two weeks
    assert not at.exception
    assert next(m for m in at.metric if m.label == "정체").value == "0"
    assert not any("정체" in m.value and "에이전트" in m.value for m in at.markdown if m.value.startswith("- "))


def _plain_texts(at: AppTest) -> list[str]:
    return [t.value for t in at.text]


def test_error_summary_is_shown_and_non_error_summaries_are_not():
    at = run_app(**{PROJECT_ENV: DEMO})
    assert not at.exception
    texts = "\n".join(_plain_texts(at))
    assert "✗ analyst (p3-data)" in texts
    assert "file not found" in texts  # the agent_error summary of the demo project
    assert "<project>" not in texts and "/Users/" not in texts
    everything = all_text(at) + "\n".join(m.value for m in at.markdown) + texts + "\n".join(c.value for c in at.caption)
    # summaries of agent_stop events in the demo log must never reach the screen
    assert "Wrote <path>" not in everything
    assert "Profiled missing values" not in everything
    assert "row counts logged" not in everything
    assert any("오류 원인" in m.value for m in at.markdown)


def test_error_without_summary_says_cause_not_recorded(tmp_path):
    root = make_project(
        tmp_path / "e",
        events=[
            json.dumps(event("2026-10-01T09:00:00+00:00", "agent_start", "w", "A", summary="normal start text")),
            json.dumps(event("2026-10-01T09:05:00+00:00", "agent_error", "w", None, summary=None)),
        ],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception
    texts = _plain_texts(at)
    assert any("✗ w (p1)" in t and "원인 미기록" in t for t in texts)
    assert "normal start text" not in "\n".join(texts) + all_text(at)


def test_error_summary_is_rendered_as_plain_text_not_markdown_or_html(tmp_path):
    hostile = "<script>alert(1)</script> **bold** [x](http://evil.example) <img src=x onerror=1>"
    root = make_project(
        tmp_path / "x",
        events=[
            json.dumps(event("2026-10-01T09:00:00+00:00", "agent_start", "w", "A")),
            json.dumps(event("2026-10-01T09:05:00+00:00", "agent_error", "w", None, summary=hostile)),
        ],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception
    assert any(hostile in t for t in _plain_texts(at))  # shown verbatim as text
    assert not any("evil.example" in m.value or "<script>" in m.value for m in at.markdown)  # never fed to markdown
    assert not at.exception  # nothing was interpreted as HTML or markdown


def test_timeline_phase_filter():
    at = run_app(**{PROJECT_ENV: DEMO})
    select = next(s for s in at.selectbox if s.label == "단계")
    select.select("p3-data").run()
    assert not at.exception
    assert any("file not found" in t for t in _plain_texts(at))
    select = next(s for s in at.selectbox if s.label == "단계")
    select.select("p4-analysis").run()
    assert not at.exception
    assert not any("file not found" in t for t in _plain_texts(at))  # error belongs to p3-data


def test_registry_mode_with_project_dropdown(tmp_path):
    other = make_project(tmp_path / "other", project_id="other")
    ws = tmp_path / "workspace.yaml"
    ws.write_text(yaml.safe_dump({"projects": [{"id": "demo-project", "path": str(DEMO)}, {"id": "other", "path": str(other)}]}))
    at = run_app(**{WORKSPACE_ENV: ws})
    assert not at.exception
    picker = at.sidebar.selectbox[0]
    assert picker.options == ["demo-project", "other"]
    assert at.title[0].value == "데모 프로젝트"
    picker.select("other").run()
    assert not at.exception
    assert at.title[0].value == "Synthetic"
    assert str(tmp_path) not in all_text(at)


def test_missing_registry_shows_korean_message_not_a_traceback():
    at = run_app()  # HOME is an empty temp dir: no default registry
    assert not at.exception
    assert at.error and "프로젝트를 열 수 없습니다" in at.error[0].value
    text = "\n".join(m.value for m in at.markdown)
    assert "등록부" in text and "--project" in text
    assert "/Users/" not in text and str(Path.home()) not in text


def test_missing_project_shows_korean_message(tmp_path):
    at = run_app(**{PROJECT_ENV: tmp_path / "nope"})
    assert not at.exception
    assert at.error and "프로젝트를 열 수 없습니다" in at.error[0].value
    assert "프로젝트 폴더를 찾을 수 없습니다" in "\n".join(m.value for m in at.markdown)


def test_project_without_project_yaml(tmp_path):
    (tmp_path / "plain").mkdir()
    at = run_app(**{PROJECT_ENV: tmp_path / "plain"})
    assert not at.exception
    assert "project.yaml" in "\n".join(m.value for m in at.markdown)


def test_empty_project_has_friendly_empty_states(tmp_path):
    root = make_project(tmp_path / "empty")
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception, [e.value for e in at.exception]
    info = "\n".join(i.value for i in at.info)
    assert "이벤트 로그" in info  # no events yet
    assert "아직 KPI 데이터가 없습니다" in info
    assert "리뷰가 없습니다" in info
    assert "에이전트 실행 기록이 없습니다" in info
    assert "✓ 지금 처리할 항목이 없습니다" in "\n".join(m.value for m in at.markdown)


def test_malformed_event_lines_surface_a_warning(tmp_path):
    root = make_project(
        tmp_path / "bad",
        events=[json.dumps(event("2026-10-01T09:00:00+00:00", "agent_start")), "{truncated", "[1]"],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception
    assert any("2건" in w.value and "건너뛰었습니다" in w.value for w in at.warning)


def test_no_absolute_home_path_is_displayed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "dev" / "proj"
    shutil.copytree(DEMO, project)
    (project / ".workbench" / "reviews" / "p3-data-1.md").write_text(f"see {project}/docs/x.md\n", encoding="utf-8")
    at = run_app(**{PROJECT_ENV: project})
    assert not at.exception
    text = all_text(at)
    assert str(home) not in text and str(project) not in text and "/Users/" not in text


def test_project_with_only_usage_nulls_shows_na(tmp_path):
    root = make_project(
        tmp_path / "na",
        events=[
            json.dumps(event("2026-10-01T09:00:00+00:00", "agent_start", "w", "A")),
            json.dumps(event("2026-10-01T09:10:00+00:00", "agent_stop", "w", "A", usage=None)),
        ],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception
    assert "n/a" in all_text(at)


# ---- decision 12-1: an attributed phase is marked in the UI as text -----------------

INFERRED = "(추정)"


def _table_records(at: AppTest) -> list[dict]:
    rows: list[dict] = []
    for table in at.table:
        frame = table.value.reset_index()
        rows.extend(frame.to_dict("records"))
    return rows


def test_inferred_phase_marker_is_rendered_for_attributed_events():
    at = run_app(**{PROJECT_ENV: DEMO})
    assert not at.exception, [e.value for e in at.exception]
    rows = _table_records(at)
    # timeline table: the null-phase runs of the demo log appear under the attributed phase, marked
    marked = [r for r in rows if r.get("단계") == f"p4-analysis {INFERRED}" and "에이전트" in r]
    assert {r["에이전트"] for r in marked} == {"pm-tracker", "fieldbook-writer"}
    # recorded phases carry no marker
    plain = [r for r in rows if r.get("단계") == "p4-analysis" and "에이전트" in r]
    assert {r["에이전트"] for r in plain} == {"analyst", "reviewer"}
    # explanation + counts are text too (never color only)
    captions = "\n".join(c.value for c in at.caption)
    assert "추정한 값" in captions and "파일에는 쓰지 않습니다" in captions
    assert "단계 추정 4건" in captions
    # operations tab: the lead-time table and the per-phase token table say how many were attributed
    assert any(r.get("단계") == "p4-analysis" and r.get("추정 포함") == f"4건 {INFERRED}" for r in rows)
    assert any(
        r.get("단계") == "p4-analysis" and f"단계 {INFERRED} 2건 포함" in str(r.get("종료 이벤트", "")) for r in rows
    )


def test_phase_filter_includes_attributed_runs():
    at = run_app(**{PROJECT_ENV: DEMO})
    select = next(s for s in at.selectbox if s.label == "단계")
    select.select("p4-analysis").run()
    assert not at.exception
    rows = [r for r in _table_records(at) if "에이전트" in r and "시작" in r]
    assert {r["에이전트"] for r in rows if r["단계"] == f"p4-analysis {INFERRED}"} == {"pm-tracker", "fieldbook-writer"}


def test_stalled_action_shows_the_marker_for_an_attributed_phase(tmp_path):
    root = make_project(
        tmp_path / "s",
        events=[
            json.dumps(event("2026-10-01T09:00:00+00:00", "gate_approved", "orchestrator", None, phase="p1", gate="phase-end")),
            json.dumps(event("2026-10-01T09:05:00+00:00", "agent_start", "pm-tracker", "A", phase=None)),
        ],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception, [e.value for e in at.exception]
    md = "\n".join(m.value for m in at.markdown)
    assert f"**! 정체** · 에이전트 `pm-tracker` (`p1` {INFERRED})" in md


def test_no_marker_when_every_phase_was_recorded(tmp_path):
    root = make_project(
        tmp_path / "r",
        events=[
            json.dumps(event("2026-10-01T09:00:00+00:00", "agent_start", "w", "A", phase="p1")),
            json.dumps(event("2026-10-01T09:10:00+00:00", "agent_stop", "w", "A", phase="p1", usage=usage())),
            json.dumps(event("2026-10-01T09:20:00+00:00", "gate_requested", "orchestrator", None, phase="p1", gate="phase-end")),
        ],
    )
    at = run_app(**{PROJECT_ENV: root})
    assert not at.exception
    assert INFERRED not in all_text(at) + "\n".join(c.value for c in at.caption) + "\n".join(m.value for m in at.markdown)
    assert not any(INFERRED in str(v) for r in _table_records(at) for v in r.values())
