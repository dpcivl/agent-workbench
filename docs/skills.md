# 스킬 (Skills)

근거는 `DECISIONS.md` 5절(스킬과 단계 흐름)이다. 스킬은 플러그인 이름 공간으로 호출한다: `/agent-workbench:<skill>`.

| 스킬 | 호출 | 인자 | 실행 주체 | 쓰는 것 |
|---|---|---|---|---|
| `project-init` | 사용자만 | `<project-id> "<name>"` | 메인 세션 (`skills/project-init/init_project.py`) | `.workbench/project.yaml`, `.gitignore`, `~/.agent-workbench/workspace.yaml` |
| `run-phase` | 사용자만 | `<phase-id> [approve \| revise <reason> \| revise confirm]` | **메인 세션** (오케스트레이터) | 이벤트(`emit`)만. 상태·산출물은 서브에이전트가 쓴다 |
| `weekly-report` | `pm-tracker`가 `skills:`로 로드, 사용자 호출도 가능 | `[YYYY-MM-DD]` (선택) | `pm-tracker` 또는 메인 세션 | `docs/reports/weekly-YYYY-MM-DD.md` 하나 |

`project-init`과 `run-phase`는 `disable-model-invocation: true`라서 모델이 스스로 호출하지 않는다. `weekly-report`는 서브에이전트에 사전 로드되어야 하므로 이 설정을 쓰지 않는다.

## project-init

- `project.yaml`이 이미 있으면 아무것도 바꾸지 않고 중단한다. 인자가 모자라면 사용법만 출력한다. 중간에 묻지 않는다.
- 템플릿은 계약을 만족한다 (예시 단계 1개, 자리표시자 에이전트). `.gitignore`에 `.workbench/events*.jsonl`, `.workbench/*.log`를 보장하고, 등록부에 `id`와 절대경로를 중복 없이 추가한다.
- `uv`가 있으면 계약 검증기를 실행하고, 없으면 "검증 생략"이라고 출력한다.

## run-phase 상태 기계

`status`는 `project.yaml`의 단계 상태다. 표에 없는 조합은 아무것도 바꾸지 않고 안내만 한다.

| status \ 모드 | `start` (인자 없음) | `approve` | `revise <reason>` | `revise confirm` |
|---|---|---|---|---|
| `todo` | 실행 → `doing` → `review` | 중단 | 중단 | 중단 |
| `blocked` | 실행 (직전 사유 보고) | 중단 | 중단 | 중단 |
| `doing` | 이어서 실행 | 중단 | 중단 | 중단 |
| `review` | 중단 (승인 대기 안내) | `done` + 챕터 초안 | 상태 유지, 변경 영향 보고 | `doing`으로 되돌려 처음부터 재실행 → `review` |
| `done` | 중단 | 중단 | 중단 | 중단 |

흐름 (괄호는 쓰는 주체):

- **start**: 사전 점검(단계 존재, 선행 단계 `done`, `doing` 단계 없음, 에이전트 명세 존재, P3는 `data/raw`에 파일) → `doing`(pm-tracker) → 단계 에이전트를 `project.yaml` 순서대로 **순차 실행** → `reviewer` → `review`(pm-tracker) → `gate_requested`(emit) → 보고 후 **멈춤**.
- **approve**: `gate_approved`(emit, **먼저**) → `done`(pm-tracker) → 챕터 초안(`fieldbook-writer`) → 보고. 게이트가 `dataset-selection`이면 사용자가 데이터셋을 `data/raw`에 직접 내려받도록 안내한다.
- **revise `<reason>`**: `change_requested`(emit) → 명세·범위·스키마 영향 보고 → 사용자 확인 대기. 아무것도 다시 실행하지 않는다.
- **revise confirm**: `change_approved`(emit) → `doing`(pm-tracker) → start의 실행 절차를 처음부터 다시 수행.

보고는 결론·추천 → 근거(리뷰 판정, 심각도별 지적 수, 미해결 `high`, 산출물, 확인하지 못한 것) → 되돌릴 수 있는지 순서이고, 결정 요청은 3개 이하다.

## 누가 어떤 이벤트를 쓰는가

| 이벤트 | 기록자 | 방법 |
|---|---|---|
| `agent_start`, `agent_stop`, `agent_error` | hook | `SubagentStart`/`SubagentStop`/`PostToolUseFailure` (`record_event.py`) |
| `gate_requested`, `gate_approved`, `change_requested`, `change_approved` | 메인 세션 (`agent`=`orchestrator`) | `python3 -m agent_workbench.emit <event> --gate <gate> --phase <phase-id>` |

`agent_error`는 `PostToolUseFailure` hook으로 기록하도록 구현되어 있지만, 실패한 서브에이전트에 대해 이 hook이 실제로 발화하는지는 **아직 검증되지 않았다**(W3 실사용 세션에서 실험 예정, DECISIONS 11-1). 발화하지 않으면 `agent_error`는 생기지 않고, 종료 이벤트가 없는 `agent_start`를 정체로 표시하는 폴백이 적용된다. 정체 임계값 기본값은 30분(사용자 확인, 대시보드에서 변경 가능)이다.

- 이벤트 파일은 hook과 `emit`만 쓴다. 에이전트와 스킬은 `.workbench/events*.jsonl`에 직접 쓰지 않는다.
- 게이트 이벤트는 단계가 이미 `review`인 시점에 기록되므로 `--phase`를 항상 명시한다 (생략하면 `doing` 단계에서 파생되어 null이 된다).

## 상태의 단일 기록자

- 단계·태스크 `status`는 `pm-tracker`만 쓴다 (Edit으로 해당 줄만). `run-phase`는 전이를 **요청**하고, 요청 뒤 `project.yaml`을 다시 읽어 결과를 확인한다.
- 메인 세션은 산출물, `project.yaml`, 리뷰 파일을 직접 고치지 않는다. 근거가 부족하면 `pm-tracker`가 바꾸지 않고 보고하며, 그러면 `run-phase`도 중단한다.
- 예외는 `project-init`의 최초 생성뿐이다 (`docs/contract.md` 1절).

## 안전 규칙

`git commit`·`git push` 없음, 데이터 다운로드 없음, `data/raw/**` 변경 없음, 사용자 대신 승인 없음.
