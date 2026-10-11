"""Korean UI strings that do not need streamlit (so the CLI can use them too)."""

from __future__ import annotations

from .loader import LoadError

# Status words always come with a symbol: meaning never rests on color alone.
PHASE_STATUS_LABEL = {
    "todo": "○ 대기 (todo)",
    "doing": "◐ 진행 중 (doing)",
    "review": "◆ 승인 대기 (review)",
    "blocked": "✕ 차단 (blocked)",
    "done": "● 완료 (done)",
}
TASK_STATUS_LABEL = {"todo": "○ 대기", "doing": "◐ 진행 중", "done": "● 완료"}
RUN_OUTCOME_LABEL = {
    "completed": "✓ 완료",
    "error": "✗ 오류",
    "running": "… 진행 중",
    "stalled": "! 정체",
}
RUN_OUTCOME_SYMBOL = {"completed": "✓", "error": "✗", "running": "…", "stalled": "!"}
SEVERITY_LABEL = {"high": "▲ 높음 (high)", "medium": "◆ 중간 (medium)", "low": "▽ 낮음 (low)"}
VERDICT_LABEL = {"pass": "✓ 통과 (pass)", "needs_change": "✗ 수정 필요 (needs_change)"}
# Decision 12-1: a `phase` the reader attributed (the event itself had phase null). Always text,
# never color alone, wherever a phase is shown.
PHASE_INFERRED_MARK = "(추정)"
PHASE_INFERRED_NOTE = (
    f"`{PHASE_INFERRED_MARK}` = 이벤트에 기록된 `phase`가 비어 있어, 같은 세션에서 바로 앞선 게이트 이벤트"
    "(`gate_requested`/`gate_approved`)의 단계로 대시보드가 추정한 값입니다. 읽을 때만 계산하며 파일에는 쓰지 않습니다."
)
GATE_RESOLUTION_LABEL = {
    "approved": "✓ 승인",
    "change_requested": "↺ 수정 요청",
    "pending": "… 승인 대기 중",
}


def describe_error(error: LoadError) -> str:
    """One Korean paragraph (plain text) for a load failure."""
    d = error.detail
    code = error.code
    if code == "registry_missing":
        return (
            f"프로젝트 등록부를 찾을 수 없습니다: {d}\n\n"
            "다음 중 하나를 하세요.\n"
            "- `~/.agent-workbench/workspace.yaml`을 만든다 (예시: 저장소의 `examples/workspace.example.yaml`).\n"
            "- `--workspace 파일경로` 또는 환경 변수 `AGENT_WORKBENCH_WORKSPACE`로 다른 등록부를 지정한다.\n"
            "- 등록부 없이 `--project 프로젝트경로`로 한 프로젝트만 연다."
        )
    if code == "registry_invalid":
        return f"등록부 파일 형식이 올바르지 않습니다: {d}\n\n" + "\n".join(f"- {e}" for e in error.errors[:8])
    if code == "registry_empty":
        return f"등록부에 프로젝트가 없습니다: {d}\n\n`projects:` 아래에 `id`와 `path`를 한 개 이상 적어 주세요."
    if code == "project_dir_missing":
        return f"프로젝트 폴더를 찾을 수 없습니다: {d}\n\n경로가 맞는지, 등록부의 `path` 값이 최신인지 확인하세요."
    if code == "project_yaml_missing":
        return (
            f"`{d}` 파일이 없습니다.\n\n"
            "프로젝트 루트에 `.workbench/project.yaml`이 있어야 합니다. `/project-init`으로 만들 수 있습니다."
        )
    if code == "project_yaml_invalid":
        return f"`.workbench/project.yaml`을 읽을 수 없습니다 ({d}).\n\n`python -m agent_workbench.contract validate`로 형식을 확인하세요."
    return f"불러오는 중 문제가 생겼습니다: {code} {d}".strip()
