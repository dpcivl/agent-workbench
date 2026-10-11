"""Formatting helpers and the CLI launcher (the app itself is not started here)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_workbench.dashboard import __main__ as cli
from agent_workbench.dashboard.fmt import NA, fmt_dt, fmt_duration, fmt_int, fmt_number
from agent_workbench.dashboard.loader import PROJECT_ENV, WORKSPACE_ENV

from conftest import DEMO


@pytest.mark.parametrize(
    "td, text",
    [
        (None, NA),
        (timedelta(seconds=45), "45초"),
        (timedelta(minutes=26, seconds=12), "26분 12초"),
        (timedelta(hours=1, minutes=22), "1시간 22분"),
        (timedelta(hours=2), "2시간"),
        (timedelta(days=1, hours=2, minutes=3), "1일 2시간 3분"),
        (timedelta(days=3), "3일"),
    ],
)
def test_fmt_duration(td, text):
    assert fmt_duration(td) == text


def test_fmt_number_dt_int():
    assert fmt_number(None) == NA and fmt_int(None) == NA
    assert fmt_number(18240) == "18,240" and fmt_number(6.8) == "6.8" and fmt_number(0.91) == "0.91"
    assert fmt_number(842.5) == "842.5" and fmt_number(2.0) == "2"
    jst = timezone(timedelta(hours=9))
    assert fmt_dt(datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc), jst) == "2026-10-07 09:00"
    assert fmt_dt(None) == NA


def test_build_launch_for_a_single_project(tmp_path):
    pytest.importorskip("streamlit")
    result = cli.build_launch(["--project", str(DEMO)], environ={"PATH": "/usr/bin"})
    assert not isinstance(result, str)
    command, env = result
    assert command[1:4] == ["-m", "streamlit", "run"]
    assert command[4].endswith("dashboard/app.py")
    assert ["--browser.gatherUsageStats", "false"] == command[5:7]
    assert ["--server.address", "127.0.0.1"] == command[7:9]
    assert env[PROJECT_ENV] == str(DEMO.resolve())
    assert env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"
    assert str(cli.APP_PATH.parents[2]) in env["PYTHONPATH"].split(":")


def test_build_launch_passes_streamlit_args_and_workspace(tmp_path):
    pytest.importorskip("streamlit")
    ws = tmp_path / "ws.yaml"
    ws.write_text(f"projects:\n  - id: demo-project\n    path: {DEMO}\n")
    command, env = cli.build_launch(["--workspace", str(ws), "--", "--server.port", "8765"], environ={})
    assert command[-2:] == ["--server.port", "8765"]
    assert env[WORKSPACE_ENV] == str(ws.resolve()) and PROJECT_ENV not in env


def test_build_launch_reports_missing_inputs_in_korean(tmp_path):
    pytest.importorskip("streamlit")
    missing_project = cli.build_launch(["--project", str(tmp_path / "nope")], environ={})
    assert isinstance(missing_project, str) and "프로젝트 폴더를 찾을 수 없습니다" in missing_project
    no_yaml = tmp_path / "plain"
    no_yaml.mkdir()
    assert "project.yaml" in cli.build_launch(["--project", str(no_yaml)], environ={})
    missing_registry = cli.build_launch(["--workspace", str(tmp_path / "none.yaml")], environ={})
    assert isinstance(missing_registry, str) and "등록부" in missing_registry and "--project" in missing_registry
    assert "Traceback" not in missing_registry


def test_main_prints_message_and_does_not_exec(tmp_path, capsys):
    pytest.importorskip("streamlit")
    called = []
    code = cli.main(["--project", str(tmp_path / "nope")], exec_fn=lambda *a: called.append(a))
    assert code == cli.EXIT_USAGE and called == []
    assert "프로젝트 폴더" in capsys.readouterr().err


def test_main_execs_streamlit(tmp_path):
    pytest.importorskip("streamlit")
    called = []
    assert cli.main(["--project", str(DEMO)], exec_fn=lambda *a: called.append(a)) == 0
    assert called and called[0][1][1:4] == ["-m", "streamlit", "run"]
