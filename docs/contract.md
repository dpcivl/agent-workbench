# 데이터 계약 (Data Contract)

agent-workbench가 쓰는 프로젝트 데이터의 형식과 검증 방법을 정의한다. 근거는 `DECISIONS.md`의 6절(데이터 계약)과 12절(변경 이력, 개정 1)이며, 여기에 없는 필드는 만들지 않는다.

## 1. 파일 구성

| 파일 | 위치 | 스키마 | 기록자 | 커밋 |
|---|---|---|---|---|
| `project.yaml` | `<project>/.workbench/` | `schema/project.schema.json` | `pm-tracker`(상태 필드), `/project-init`(최초 생성) | 한다 |
| `events-YYYY-MM.jsonl` | `<project>/.workbench/` | `schema/events.schema.json` (한 줄 = 한 이벤트) | hook | 하지 않는다 (gitignore) |
| `reviews/<phase>-<n>.json` | `<project>/.workbench/reviews/` | `schema/review.schema.json` | `reviewer` | 미정 (DECISIONS 11-5) |
| `workspace.yaml` | `~/.agent-workbench/` | `schema/workspace.schema.json` | 사용자 | 하지 않는다 (레포에는 `workspace.example.yaml`만) |

스키마는 JSON Schema draft 2020-12이다. 스키마가 표현하지 못하는 규칙(id 중복, 참조 무결성, 경로 안전성, 시각 값 검증)은 `src/agent_workbench/contract/validate.py`가 추가로 검사한다.

## 2. `events-YYYY-MM.jsonl`

### 규약

- **추가 전용(append-only)**: 한 줄 JSON을 파일 끝에 덧붙이기만 한다. 기존 줄을 고치거나 지우지 않는다.
- **월 단위 회전**: 파일 이름의 `YYYY-MM`은 이벤트를 기록한 달이다. 새 달이 되면 새 파일에 쓴다. 이전 달 파일은 건드리지 않는다.
- 프롬프트·응답 원문은 기록하지 않는다. `summary`는 응답 앞 200자이며 절대경로는 마스킹한다.
- 소요 시간은 `agent_start`와 `agent_stop`/`agent_error`를 `agent_id`로 짝지어 대시보드가 계산한다. 종료 이벤트가 없는 `agent_start`는 **정체**(stalled)로 표시한다.
- 파일 이름의 월과 `ts`의 월이 항상 같다고 가정하지 않는다(타임존 경계). 검증기도 이를 검사하지 않는다.

### 필드

필수는 `v`, `ts`, `event`뿐이다. `agent_start`/`agent_stop`/`agent_error`는 `session_id`, `agent_id`, `agent`가 추가로 필수다. 단 `agent_error`의 `agent_id`는 `null`일 수 있다(시작되지 못한 호출의 실패 — 실제 세션에서 확인).

| 필드 | 타입 | 필수 | 값 | 출처 |
|---|---|---|---|---|
| `v` | int | 예 | `1` 고정 | 기록 코드의 상수 |
| `ts` | string | 예 | ISO 8601, 타임존 오프셋 필수 (`2026-10-07T09:00:05+09:00`, `Z` 허용) | 파생: 기록 시점의 시계 |
| `event` | string | 예 | `agent_start`, `agent_stop`, `agent_error`, `gate_requested`, `gate_approved`, `change_requested`, `change_approved` | hook이 이벤트 종류로 결정. 게이트·수정 이벤트는 오케스트레이터 |
| `session_id` | string\|null | agent_* 이벤트에서 예 | 세션 식별자 | hook 제공 |
| `agent_id` | string\|null | agent_* 이벤트에서 예 | 서브에이전트 실행 식별자. 시작·종료 짝짓기 키 | hook 제공 |
| `agent` | string\|null | agent_* 이벤트에서 예 | 에이전트 이름. 오케스트레이터 이벤트는 `orchestrator` | hook 제공 (오케스트레이터는 고정값) |
| `phase` | string\|null | 아니오 | 기록 시점의 단계 id. 파생 순서: (1) `project.yaml`에서 `status: doing`인 단계의 id, (2) 없으면 `review`인 단계가 **정확히 하나일 때만** 그 id, (3) 그 밖에는(`review`가 둘 이상 포함) null. 파일에 쓰인 값은 이 규칙의 결과 그대로이며, null을 사후에 채우지 않는다. 리더의 보충 규칙은 아래 "대시보드 리더 메모"의 "단계 추정" | 파생: `project.yaml` (hook) |
| `gate` | string\|null | `gate_requested`/`gate_approved`에서 예(non-null) | 예: `dataset-selection`, `phase-end` | 오케스트레이터 |
| `summary` | string\|null | 아니오 | 최대 200자, 절대경로 마스킹 | 파생: 응답 앞 200자 |
| `rev` | string\|null | 아니오 | 프로젝트 git 짧은 해시(소문자 16진 4~40자), 없으면 null | 파생: `git rev-parse --short HEAD` |
| `usage` | object\|null | 아니오 | `agent_stop`에서만 non-null. 있으면 5개 키 모두 필수 (아래 `usage` 절) | 파생: 서브에이전트 트랜스크립트 (`requestId`로 중복 제거 후 합산). 추출 실패 시 null |

추가 규칙:

- `usage`는 `agent_stop` 이외의 이벤트에서 non-null이면 오류이다. `agent_stop`이라도 추출에 실패하면 `null`이며, 이는 유효하다.
- `gate`는 `agent_*` 이벤트에서 non-null이면 오류이다 ("게이트 이벤트에만 값"). `change_*` 이벤트에는 값을 둘 수 있다(어느 게이트에서의 수정인지 표시).
- `agent_error`는 `PostToolUseFailure` hook이 기록하도록 구현되어 있지만, 실패한 서브에이전트에 대해 이 hook이 **실제로 발화하는지는 아직 검증되지 않았다**(W3 실사용 세션에서 실험 예정, DECISIONS 11-1). 발화하지 않으면 `agent_error`는 생기지 않고, 종료 이벤트가 없는 `agent_start`를 정체로 표시하는 폴백이 적용된다. 스키마는 이를 지원하도록 정의만 해 둔다.

### `usage`

개정 1(DECISIONS 12절)에서 캐시 토큰 두 필드를 필수 키로 추가했다. `usage`가 null이 아니면 아래 5개 키가 모두 있어야 한다.

| 키 | 타입 | 의미 |
|---|---|---|
| `model` | string\|null | 트랜스크립트의 모델 이름. 알 수 없으면 null |
| `input_tokens` | int >= 0 | 캐시를 거치지 않은 입력 토큰 |
| `output_tokens` | int >= 0 | 출력 토큰 |
| `cache_creation_input_tokens` | int >= 0 | 프롬프트 캐시에 새로 쓴 입력 토큰 |
| `cache_read_input_tokens` | int >= 0 | 프롬프트 캐시에서 읽은 입력 토큰 |

**캐시 토큰이 필요한 이유.** `input_tokens`는 캐시에 걸리지 않은 부분만 센다. 실제 서브에이전트 트랜스크립트에서 `input_tokens`가 12인데 캐시 토큰이 약 50만이었다(DECISIONS 12절 근거). 총 입력량은 `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`이며, `input_tokens`만 보면 입력을 몇 자릿수 과소평가해 비용과 사용량 분석이 불가능하다. 캐시 쓰기와 읽기는 단가도 달라 따로 기록한다.

- 구버전 형식(`model`, `input_tokens`, `output_tokens` 3개 키만 있는 `usage`)은 이제 유효하지 않다. 리더는 이미 기록된 로그를 읽을 때 이 점에 유의한다.
- 알 수 없는 추가 키는 `usage` 안에서도 허용한다(이벤트와 같은 이유).
- 추출은 비공개 트랜스크립트 형식에 의존하므로 실패하면 `usage` 전체를 null로 둔다. 일부 키만 채우지 않는다.

### 대시보드 리더 메모

이벤트 로그를 읽어 지표를 만드는 쪽(대시보드)을 위한 메모이다. 계약이 직접 강제하는 규칙이 아니라 읽는 방법에 대한 권고이다.

- **시작·종료 짝짓기**: `agent_start`와 `agent_stop`을 `agent_id`로 짝짓는다.
- **소요 시간**: 짝이 맞은 쌍의 `ts` 차이(`stop.ts - start.ts`)이다. 병렬 에이전트는 구간이 겹칠 수 있으므로 합이 벽시계 시간이 아니다.
- **정체(stalled)**: 종료 이벤트가 없는 `agent_start`이다. 현재 시각과의 차이가 임계값을 넘으면 정체로 표시하고, 넘지 않으면 진행 중으로 본다. 임계값은 대시보드 설정이며 **기본값 30분은 사용자가 확인한 값**이고 사이드바에서 바꿀 수 있다(DECISIONS에는 값이 적혀 있지 않다).
- **`agent_error`는 `agent_id`로 짝지을 수 없다.** `agent_error`는 서브에이전트 자신이 아니라 호출 쪽의 도구 실패 hook에서 기록되어, `agent_id`가 실패한 실행의 `agent_start`와 같다고 볼 수 없다(`agent`는 실패한 에이전트 이름으로 채운다). 실패한 서브에이전트에 대해 이 hook이 발화하는지 자체가 아직 검증되지 않았고 W3 실사용 세션의 검증 대상이다(DECISIONS 11-1). 발화하지 않으면 정체 표시 폴백이 적용된다. `agent`(에이전트 이름)와 `ts`로 짝짓는다: 같은 `agent`의 아직 종료되지 않은 `agent_start` 중 `ts`가 `agent_error`보다 앞서고 가장 가까운 것. 짝을 못 찾으면 오류 이벤트 단독으로 표시한다. 오류가 난 시작은 정체로 세지 않는다.
- **단계 추정(`phase`가 null인 이벤트)**: 단계가 `doing`을 벗어난 뒤(승인 후 `done` 처리, 챕터 초안 등) 기록되는 `pm-tracker`·`fieldbook-writer` 이벤트는 `review`가 둘 이상이거나 하나도 없으면 `phase: null`로 남는다(DECISIONS 12-1에서 확인). 리더는 이런 이벤트를 단계별로 집계할 때 다음 규칙으로 단계를 **추정**한다.
  - 대상: `phase`가 null인 이벤트. 파일에 기록된 non-null `phase`는 절대 덮어쓰지 않는다.
  - 값: **같은 `session_id`**에서 시각이 앞서고 가장 가까운 **게이트 이벤트**(`gate_requested` 또는 `gate_approved`) 중 `phase`가 non-null인 것의 `phase`. 그런 이벤트가 없으면(게이트 이전, 다른 세션, `session_id`가 null) null로 둔다. 다른 세션은 상속하지 않는다.
  - 계산은 **읽는 쪽에서만** 한다. 파일에는 다시 쓰지 않는다(로그는 추가 전용).
  - 추정한 값은 리더의 데이터 모델에서 **추정 표시**(대시보드 구현: `phase_inferred: true`)를 달아 기록된 값과 구분하고, 화면에 "(추정)" 같은 글자로 드러낸다. 이 표시는 이벤트 필드가 아니므로 스키마와 파일에는 없다.
  - 한계: 세션 하나가 여러 단계를 걸치고 중간에 게이트 없이 단계가 바뀌면 직전 게이트의 단계로 잘못 추정할 수 있다. 그래서 추정치는 기록된 값과 따로 센다.
- **사용량 합산**: `usage`가 null인 `agent_stop`은 합계에서 빼고 "추출 실패 N건"으로 센다. 총 입력은 위의 세 입력 필드의 합이다.

### 알 수 없는 필드: 허용

이벤트는 `additionalProperties: true`이다. 이유: 로그는 hook이 쓰고 대시보드가 읽으며, 두 쪽의 버전이 어긋날 수 있다. 필드가 추가되어도 기존 줄과 구버전 리더가 깨지지 않아야 한다. 호환이 깨지는 변경은 `v`를 올린다. 반대로 `project.yaml`, `review`, `workspace`는 한 에이전트가 쓰고 오타를 잡는 것이 중요하므로 알 수 없는 키를 거부한다.

### 예시

```json
{"v":1,"ts":"2026-10-07T09:00:05+09:00","event":"agent_start","session_id":"b83e5f10-9c26-47a1-a6d3-71e0c4a2d958","agent_id":"d2e6a8f04b7c1593","agent":"data-prep","phase":"p3-data","gate":null,"summary":null,"rev":"9b2f4d6","usage":null}
{"v":1,"ts":"2026-10-07T09:26:12+09:00","event":"agent_stop","session_id":"b83e5f10-9c26-47a1-a6d3-71e0c4a2d958","agent_id":"d2e6a8f04b7c1593","agent":"data-prep","phase":"p3-data","gate":null,"summary":"Wrote <path>/src/pipeline/clean.py and processed 3 files; ...","rev":"9b2f4d6","usage":{"model":"sonnet","input_tokens":312,"output_tokens":9254,"cache_creation_input_tokens":67318,"cache_read_input_tokens":902655}}
{"v":1,"ts":"2026-10-07T10:56:30+09:00","event":"gate_requested","session_id":"b83e5f10-9c26-47a1-a6d3-71e0c4a2d958","agent_id":null,"agent":"orchestrator","phase":"p3-data","gate":"phase-end","summary":null,"rev":"9b2f4d6","usage":null}
```

전체 예시는 `examples/events-2026-10.jsonl`(20줄: 7개 이벤트 종류 전부, 병렬 에이전트 2개, `agent_error` 1건, 정체 1건, `usage` 추출 실패(null) 1건)에 있다.

## 3. `project.yaml`

프로젝트의 단계·에이전트·산출물·태스크를 선언한다. 상태의 단일 기록자는 `pm-tracker`이다. 다른 에이전트는 이 파일의 상태를 바꾸지 않고 이벤트만 남긴다. 태스크는 얇게 유지한다(담당자·마감·우선순위 없음).

| 경로 | 타입 | 필수 | 값 |
|---|---|---|---|
| `project.id` | string | 예 | 식별자 (`[A-Za-z0-9][A-Za-z0-9_-]*`) |
| `project.name` | string | 예 | 표시 이름 |
| `project.description` | string | 예 | 한 줄 설명 (빈 문자열 허용) |
| `agents[].id` | string | 예 | 식별자 |
| `agents[].role` | string | 예 | 역할 설명 |
| `phases[].id` | string | 예 | 식별자. `reviews/<phase>-<n>.json` 파일 이름에도 쓰이므로 파일 이름에 안전해야 함 |
| `phases[].name` | string | 예 | 표시 이름 |
| `phases[].status` | enum | 예 | `todo`, `doing`, `review`(사용자 승인 대기), `blocked`, `done` |
| `phases[].agents` | string[] | 예 | `agents[].id` 참조 |
| `phases[].deliverables` | string[] | 예 | 프로젝트 상대경로 또는 글롭(`*`, `**`). 완료 판단은 파일 존재 + 수정 시각. 아래 "산출물 배치 규칙" 참고 |
| `phases[].gate` | enum | 예 | `dataset-selection`, `phase-end` |
| `phases[].tasks[].id` | string | 예 | 단계 안에서 유일 |
| `phases[].tasks[].title` | string | 예 | 제목 |
| `phases[].tasks[].status` | enum | 예 | `todo`, `doing`, `done` |
| `phases[].tasks[].agent` | string | 예 | `agents[].id` 참조 |

의미 검사(`validate_project`):

- `agents[].id`, `phases[].id`는 프로젝트 안에서 유일, `tasks[].id`는 단계 안에서 유일.
- `phases[].agents`와 `tasks[].agent`는 최상위 `agents[]`에 있어야 한다.
- `deliverables`는 상대경로여야 하고 절대경로(POSIX/Windows), `~` 시작, `..` 구성요소를 허용하지 않는다. 글롭 문자는 허용한다. 글롭 문법 자체는 검사하지 않는다.
- `doing` 단계는 최대 1개. 이벤트의 `phase`를 "`doing`인 단계"로 먼저 파생하기 때문이다(없으면 단일 `review` 단계, 필드 설명 참고).

예시: `examples/project.yaml`.

### 산출물 배치 규칙

DECISIONS 6절 "산출물 배치 규칙"을 그대로 옮긴 것이다.

- `deliverables`에는 구체 경로나 글롭(`*`, `**`)을 쓸 수 있다. 대시보드가 글롭을 펼쳐 존재 여부와 수정 시각으로 단계의 완료를 판단한다.
- 확정한 파일 이름: `docs/research/energy-structure.md`, `docs/data/quality-report.md`, `docs/models/model-card.md`, `notebooks/*.ipynb`, `results/p5-forecast/scenario-*`.
- `results/p5-forecast/metrics.json`은 `ml-modeler`가 최상위 `model` 키, `savings-simulator`가 최상위 `savings` 키만 쓴다. KPI 카드는 이 두 키를 읽으며 별도 스키마는 두지 않는다(이 레포도 `metrics.json` 스키마를 정의하지 않는다).
- 리뷰(`.workbench/reviews/`)와 챕터(`docs/fieldbook/`)는 단계 산출물 목록(`deliverables`)에 **넣지 않는다.** 대시보드가 각각 별도로 읽는다.

**글롭 해석.** 구현은 `agent_workbench.contract.glob_match(pattern, path)`와 `glob_matches(pattern, paths)`이다(순수 Python, 의존성 없음). 대시보드는 이 함수로 자기가 모은 파일 목록에 패턴을 대조한다.

| 규칙 | 설명 |
|---|---|
| 기준 | 프로젝트 루트 상대경로, `/` 구분, 대소문자 구분. 절대경로는 일치하지 않는다. 앞의 `./`는 무시한다 |
| `*` | 한 경로 구성요소(segment) 안의 임의 문자열. `/`를 넘지 않는다. `?`는 한 글자, `[abc]`는 문자 집합 |
| `**` | 구성요소 단위로 쓸 때 0개 이상의 구성요소. `docs/**/x.md`는 `docs/x.md`와 `docs/a/b/x.md`에 모두 일치 |
| 끝의 `**` | 1개 이상의 구성요소. `results/**`는 `results` 아래 모든 파일에 일치하고 `results` 자체에는 일치하지 않는다 |
| 구성요소 안의 `**` | `a**b`처럼 다른 문자와 붙으면 `*`와 같다 |
| 점 파일 | 특별 취급하지 않는다. `*`가 `.hidden`에도 일치한다 |
| 완료 판단 | 패턴이 하나라도 일치하는 파일이 있어야 존재로 본다. 패턴이 파일을 하나도 못 찾으면 미완료 |

예: `notebooks/*.ipynb`는 `notebooks/01-eda.ipynb`에 일치하고 `notebooks/old/02-eda.ipynb`에는 일치하지 않는다. `results/p5-forecast/scenario-*`는 `scenario-a.csv`와 `scenario-b`(디렉터리 이름)에 일치하지만 `scenario-b/summary.json`에는 일치하지 않으므로, 디렉터리 안 파일을 보려면 `scenario-*/**`를 쓴다.

## 4. `reviews/<phase>-<n>.json`

`reviewer`가 단계 종료 시 남기는 검증 결과이다. 파일 이름의 `<phase>`는 `phases[].id`, `<n>`은 1부터 시작하는 반복 횟수이다(예: `p3-data-1.json`).

| 필드 | 타입 | 필수 | 값 |
|---|---|---|---|
| `verdict` | enum | 예 | `pass`, `needs_change` |
| `findings[].category` | string | 예 | 지적 분류. 자유 문자열(빈 문자열 불가) |
| `findings[].severity` | enum | 예 | `high`, `medium`, `low` |
| `findings[].resolved` | bool | 예 | 해소 여부 |
| `counter_evidence_count` | int >= 0 | 예 | 결론에 대해 찾은 반대 증거 수 |
| `phase`, `n` | string, int >= 1 | 아니오 | 파일 이름의 복사본. 있으면 파일 이름과 일치해야 한다 |

`phase`, `n`은 DECISIONS에 없는 선택 필드이다(파일이 자기 자신을 설명하도록). 필수가 아니며, 쓰지 않아도 유효하다.

### 판정 규칙 (개정 1)

스키마가 `severity` 값을 제한하고, `validate_review`가 `verdict`와 나머지 필드의 관계를 검사한다.

| `verdict` | 유효 조건 |
|---|---|
| `pass` | `counter_evidence_count >= 1` **그리고** `severity`가 `high`이면서 `resolved`가 `false`인 지적이 없다 |
| `needs_change` | 항상 허용 (지적이 없어도, 반대 증거가 0이어도) |

- `medium`, `low` 지적은 미해결이어도 `pass`를 막지 않는다. 막는 것은 미해결 `high`뿐이다.
- 두 조건을 모두 어기면 오류가 둘 모두 나온다(`$.verdict`, `$.findings[i]`).
- 지적 상세 설명은 같은 폴더의 `<phase>-<n>.md` 요약에 둔다. JSON에는 상세 필드가 없다.
- 커밋 여부는 미정이다(DECISIONS 11-5).

예시: `examples/reviews/p3-data-1.json`(`needs_change`: 미해결 `high` 있음), `examples/reviews/p3-data-2.json`(`pass`: `high`는 해소, 반대 증거 2건).

## 5. `workspace.yaml`

대시보드가 열 수 있는 프로젝트 목록이다. 사용자 홈(`~/.agent-workbench/workspace.yaml`)에만 두고 레포에는 올리지 않는다.

| 경로 | 타입 | 필수 | 값 |
|---|---|---|---|
| `projects[].id` | string | 예 | 해당 프로젝트의 `project.id`와 같아야 한다 (파일 간 일치는 검사하지 않음) |
| `projects[].path` | string | 예 | 프로젝트 루트. `~`로 시작할 수 있다 |

`projects[].id`는 목록 안에서 유일해야 한다. 예시: `examples/workspace.example.yaml`.

## 6. 프로젝트 KPI 위치 (규약)

프로젝트 고유 지표(모델 정확도, 절감량 등)는 `results/<phase-id>/metrics.json`에 둔다는 규약만 있다. P5의 `results/p5-forecast/metrics.json`은 최상위 `model`(`ml-modeler`)과 `savings`(`savings-simulator`) 키만 쓴다(3절 "산출물 배치 규칙"). 이 문서는 `metrics.json`의 스키마를 정의하지 않는다. 운영 지표(이벤트에서 계산)와 프로젝트 지표는 구분한다.

## 7. 검증 방법

```bash
# 단일 파일 (파일 이름/위치로 종류를 자동 감지)
python -m agent_workbench.contract validate .workbench/project.yaml
python -m agent_workbench.contract validate .workbench/events-2026-10.jsonl
python -m agent_workbench.contract validate .workbench/reviews/p3-data-1.json
python -m agent_workbench.contract validate ~/.agent-workbench/workspace.yaml

# 종류를 직접 지정
python -m agent_workbench.contract validate --type events some-log.jsonl
```

- 리뷰는 스키마 검사 뒤 `pass` 규칙(4절)까지 검사한다. 파일 경로뿐 아니라 파싱된 dict도 같은 검사를 받는다.
- 감지 규칙: `events[-YYYY-MM].jsonl` → events, `project.yaml` → project, `workspace[.example].yaml` → workspace, `reviews/` 아래 `.json` → review.
- 종료 코드: `0` 모두 유효, `1` 하나 이상 무효, `2` 종류 감지 실패 등 사용 오류.
- 오류는 JSONPath 형태 위치와 함께 출력된다 (이벤트 파일은 `line N:` 접두).

Python에서 직접 호출할 수도 있다. 각 함수는 오류 문자열 리스트를 반환하며 빈 리스트가 유효를 뜻한다.

```python
from agent_workbench.contract import glob_matches, validate_event, validate_events_file, validate_project

errors = validate_event({"v": 1, "ts": "2026-10-07T09:00:00+09:00", "event": "gate_requested"})
# ["$: 'gate' is a required property"]

glob_matches("notebooks/*.ipynb", ["notebooks/a.ipynb", "notebooks/old/b.ipynb"])
# ["notebooks/a.ipynb"]
```

의존성은 표준 라이브러리, `pyyaml`, `jsonschema`뿐이다.
