---
name: project-init
description: 프로젝트에 .workbench/project.yaml을 만들고 .gitignore를 맞추고 ~/.agent-workbench/workspace.yaml에 프로젝트를 등록한다. 사용자가 직접 호출할 때만 쓴다.
argument-hint: '<project-id> "<name>"'
disable-model-invocation: true
allowed-tools: Read Glob Bash(python3 ${CLAUDE_SKILL_DIR}/init_project.py *)
---

# project-init

호출 인자: `$ARGUMENTS`

현재 프로젝트(`${CLAUDE_PROJECT_DIR}`)를 agent-workbench에 맞게 초기화한다. 중간에 사용자에게 묻지 않는다. 인자가 모자라면 아래 사용법만 출력하고 끝낸다.

사용법: `/agent-workbench:project-init <project-id> "<name>"`

## 절차

1. **인자 확인**. 위 호출 인자를 셸 규칙으로 나눠 첫째를 `project-id`, 둘째를 `name`으로 쓴다. 둘 중 하나라도 없거나 `project-id`가 `^[A-Za-z0-9][A-Za-z0-9_-]*$`에 맞지 않거나 `name`에 줄바꿈이 있으면 사용법을 출력하고 **아무것도 만들지 않고** 끝낸다.
2. **헬퍼 실행**. 값의 작은따옴표는 `'\''`로 바꿔 각각 단일 따옴표로 감싼다.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/init_project.py" --id '<project-id>' --name '<name>' --root "${CLAUDE_PROJECT_DIR}" --plugin-root "${CLAUDE_PLUGIN_ROOT}"
   ```

   헬퍼가 하는 일은 아래 "헬퍼의 동작"과 같고, 다른 파일은 만들거나 고치지 않는다.
3. **결과 보고**. 헬퍼 출력을 근거로 짧게 보고한다. 순서: 결론(만든 것 / 중단한 이유), 근거(만들거나 바꾼 파일과 검증 결과), 되돌릴 수 있는지(`.workbench/project.yaml` 삭제, `.gitignore`에 추가된 줄 제거, 등록부에서 항목 제거). 종료 코드별 의미:

   | 코드 | 의미 | 사용자에게 할 말 |
   |---|---|---|
   | 0 | 완료 | 다음 단계: `project.yaml`의 예시 에이전트·단계를 프로젝트에 맞게 고치고 `.claude/agents/`에 명세를 둔다 |
   | 1 | 검증 실패 | 검증기 출력을 그대로 인용한다. 파일은 남아 있다 |
   | 2 | 인자 오류 | 사용법 |
   | 3 | `project.yaml`이 이미 있음 | 덮어쓰지 않았고 아무것도 바꾸지 않았다 |
   | 4 | 프로젝트는 만들었으나 등록 보류 | 등록부를 안전하게 고칠 수 없었던 이유와 직접 추가할 줄을 그대로 전한다 |

## 헬퍼의 동작

1. `.workbench/project.yaml`이 있으면 **아무것도 바꾸지 않고** 중단한다 (덮어쓰기 없음).
2. `.workbench/` 폴더와 `project.yaml`을 만든다. 템플릿은 계약(`docs/contract.md` 3절)을 만족한다: 예시 단계 1개(`p1-example`, `todo`, 게이트 `phase-end`), 자리표시자 에이전트 `example-agent`와 공통 에이전트 3개(`pm-tracker`, `reviewer`, `fieldbook-writer`).
3. `.gitignore`에 `.workbench/events*.jsonl`, `.workbench/*.log` 두 줄이 없으면 덧붙인다 (파일이 없으면 만든다. 이미 있는 줄은 그대로 둔다).
4. `~/.agent-workbench/workspace.yaml`에 `id`와 프로젝트 루트 절대경로를 등록한다 (파일이 없으면 만든다, 같은 `id`나 같은 경로가 있으면 추가하지 않는다, 형식을 안전하게 편집할 수 없으면 바꾸지 않고 추가할 줄을 출력한다).
5. 검증: `uv`가 있으면 아래 명령을 `project.yaml`(과 등록부)에 대해 실행한다. `uv`나 플러그인 소스가 없으면 "검증 생략"이라고 출력한다.

   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" uv run --no-project --with pyyaml --with jsonschema python -m agent_workbench.contract validate <file>
   ```

## 하지 않는 것

- 기존 `project.yaml` 덮어쓰기, 사용자 질문, 커밋·푸시, 데이터 다운로드
- `data/raw/**`, `.workbench/events*.jsonl`, `.git/**` 변경
- "헬퍼의 동작"에 없는 파일 생성. 공통 에이전트·스킬·hook은 플러그인이 제공하므로 프로젝트에 복사하지 않는다.
