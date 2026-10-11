# 대시보드

프로젝트 진행 상황과 에이전트 운영 지표를 보여 주는 **로컬 전용, 읽기 전용** Streamlit 화면이다. 근거는 `DECISIONS.md`의 8절(대시보드)과 6절(데이터 계약)이며, 데이터 형식은 [`contract.md`](contract.md)를 따른다.

- 프로젝트 폴더에 **아무것도 쓰지 않는다.** 읽기만 한다.
- 네트워크 호출과 사용 통계 수집이 없다. 서버는 `127.0.0.1`에만 열린다.
- 프롬프트·응답 원문은 읽지도 보여 주지도 않는다. 예외는 `agent_error` 이벤트의 `summary` 하나뿐이다 (아래 "오류 원인 표시"). 경로는 프로젝트 상대경로로만 표시한다.

## 설치와 실행

```bash
pip install -e '.[dashboard]'        # streamlit>=1.40, altair, pandas

python -m agent_workbench.dashboard                       # 등록부의 프로젝트 목록에서 선택
python -m agent_workbench.dashboard --workspace FILE      # 다른 등록부 파일 사용
python -m agent_workbench.dashboard --project PATH        # 등록부 없이 프로젝트 하나만 열기
python -m agent_workbench.dashboard --project examples/demo-project   # 데모
python -m agent_workbench.dashboard --project PATH -- --server.port 8600   # `--` 뒤는 streamlit run에 전달
```

| 입력 | 우선순위 |
|---|---|
| `--project PATH` | 가장 높다. 등록부를 읽지 않는다 |
| `--workspace FILE` | 등록부 파일 지정 |
| 환경 변수 `AGENT_WORKBENCH_WORKSPACE` | `--workspace`가 없을 때 |
| `~/.agent-workbench/workspace.yaml` | 기본값 |

- 등록부 형식은 저장소의 `examples/workspace.example.yaml`을 복사해 경로만 고치면 된다 (`projects[].id`, `projects[].path`). `path`가 상대경로이면 등록부 파일이 있는 폴더 기준이다.
- 등록부나 프로젝트를 찾지 못하면 트레이스백 대신 한국어 안내를 보여 준다 (실행 직전 CLI에서도, 화면에서도).
- `--` 뒤에 `--server.address`를 주면 로컬 전용 설정을 사용자가 직접 바꾸는 것이다. 권장하지 않는다.
- `streamlit`이 없으면 설치 안내를 출력하고 종료한다. 데이터 계층(`loader`, `analysis`)은 streamlit 없이 import된다.

## 읽는 데이터

| 무엇 | 어디서 | 비고 |
|---|---|---|
| 프로젝트 정의 | `<project>/.workbench/project.yaml` | `contract`의 `validate_project`로 검증. 문제는 경고로만 표시하고 읽을 수 있는 만큼 쓴다 |
| 이벤트 | `<project>/.workbench/events-*.jsonl` 전부 | 깨진 줄은 건너뛰고 건수를 화면에 표시한다 |
| 리뷰 | `<project>/.workbench/reviews/*.json`, 같은 이름의 `.md` | `validate_review`로 검증. 위반이면 표시하되 숨기지 않는다 |
| 산출물 | `phases[].deliverables`를 실제 파일 트리에 대조 | 글롭 해석은 `contract.glob_matches` |
| 프로젝트 KPI | `<project>/results/<단계 id>/metrics.json` | 최상위 `model`, `savings` 키만 읽는다 |
| 챕터 초안 | `<project>/docs/fieldbook/*.md` | 산출물 탭 맨 아래에 파일 이름과 수정 시각만 |

이벤트 한 줄은 다음 경우에 건너뛴다: JSON이 아니거나 객체가 아님, `event` 값이 알려진 7종이 아님, `ts`를 해석할 수 없음 또는 타임존 오프셋이 없음. 그 밖의 계약 위반(예: 구버전 `usage` 형식)은 읽되 "계약 검증 경고"에 건수를 보인다. 이벤트의 `summary`는 `agent_error` 이벤트에서만 읽는다 (아래 "오류 원인 표시"). 그 밖의 이벤트의 `summary`는 로더가 버리므로 화면에 나올 수 없다.

## 화면

사이드바: 프로젝트 선택, **정체 판단 기준(분)**, 새로고침. 목록은 15초 동안 캐시되며 `새로고침`으로 즉시 다시 읽는다.

상태는 색만으로 구분하지 않는다. 항상 글자와 기호를 함께 쓴다 (○ 대기, ◐ 진행 중, ◆ 승인 대기, ✕ 차단, ● 완료 / ✓ 완료, ✗ 오류, … 진행 중, ! 정체). 표는 머리글이 있는 HTML 표(`st.table`)이고, 타임라인의 막대 끝은 색과 도형을 함께 쓴다.

### 개요

- **지금 내가 해야 할 일**: `review` 상태의 단계(승인 대기), `blocked` 단계(차단), 정체한 에이전트. 승인 대기에는 게이트 요청 이후 경과 시간을 붙인다. 항목이 없으면 그렇다고 적는다.
- **단계 진행판**: 상태(todo / doing / review / blocked / done)별 열에 단계 카드. 태스크 완료 수와 산출물 완료 수를 보인다.
- **산출물 완료 현황**: 단계별 `존재하는 선언 수 / 선언 수`와 미완료 항목, 최종 수정 시각.

### 타임라인

단계별(또는 전체) 에이전트 실행을 시작에서 종료까지 막대로 그린다. 막대 끝의 도형: 원 = 완료, 십자 = 오류, 삼각형 = 진행 중, 마름모 = 정체. 종료 이벤트가 없는 실행은 현재 시각까지 그린다. 아래 표에 단계, 에이전트, 시작, 종료, 소요 시간, 상태를 글자로 적는다. 표 바로 아래 "오류 원인" 목록에 실패한 에이전트와 오류 이벤트의 `summary`를 보인다 (선택한 단계 기준). 대응하는 `agent_start`가 없는 오류 이벤트도 이 목록에 `짝이 되는 시작 이벤트 없음`으로 나오고, 종료 이벤트의 짝 없음은 건수만 알린다.

### 산출물

단계별로 선언된 경로나 글롭, 존재 여부, 일치 수, 최종 수정 시각. 글롭은 일치한 파일 목록을 펼쳐 볼 수 있다. 완료 판단은 **파일 존재**뿐이며 내용의 품질은 보지 않는다. 일치한 항목이 없으면 미완료이다.

- 글롭은 선언된 패턴의 고정 접두 폴더에서만 걷는다. `.git`, `.venv`, `node_modules` 등은 건너뛴다.
- 일치 수는 디렉터리 이름도 센다 (`scenario-*`가 디렉터리 `scenario-b`와 일치하는 것은 계약이 허용한 동작이다).
- 절대경로, `~`, `..`를 포함한 선언은 파일 시스템을 건드리지 않고 "없음"으로 처리한다.

### 리뷰

모든 리뷰의 판정, 지적 수, 미해결 수, 반대 증거 수, 계약 검증 결과를 표로 보인다. 단계별 최신 회차는 펼쳐 두며, 중요도별 해소/미해결 건수, 분류별 지적 목록, 같은 이름의 `.md` 요약 미리보기를 보인다 (`.md`의 절대경로는 가려서 표시). `pass`인데 계약 규칙(반대 증거 1건 이상, 미해결 `high` 없음)을 어기면 "위반 있음"으로 표시한다.

### 운영 지표 (오케스트레이션)

이벤트 로그에서 계산한 실행 지표이다. 프로젝트 KPI와 섞지 않는다. 계산 방법:

| 지표 | 계산 |
|---|---|
| 소요 시간 | `agent_start`와 `agent_stop`을 `agent_id`로 짝지어 `stop.ts - start.ts`. 완료된 실행만 센다. 병렬 실행은 구간이 겹치므로 합계가 벽시계 시간이 아니다 |
| 오류 | `agent_error`는 `agent_id`로 짝지을 수 없으므로 같은 `agent` 이름의 아직 끝나지 않은 `agent_start` 중 가장 가까운 앞선 것에 붙인다. 짝이 없으면 단독으로 센다. 오류가 난 실행은 정체로 세지 않고, 소요 시간 통계에도 넣지 않는다 |
| 정체 | 종료(stop, error) 이벤트가 없는 `agent_start` 중 `현재 시각 - ts`가 임계값을 **초과**한 것. 넘지 않으면 "진행 중" |
| 승인 대기 | `gate_requested` → `gate_approved`, (단계, 게이트)별. 같은 요청이 `change_requested`로 끝나면 "수정 요청"으로 따로 표시하고 평균에서는 뺀다. 응답이 없으면 "승인 대기 중"으로 현재까지의 시간을 보인다 |
| 리드 타임 | 단계의 첫 `agent_start` → 그 단계의 (마지막) `gate_approved`. 승인 대기 시간을 포함한다. 승인 전에는 "진행 중 / 미승인" |
| 수정 요청·승인 | 단계별 `change_requested`, `change_approved` 개수 |
| 재작업 | (단계, 에이전트)별 `agent_start` 횟수가 2 이상인 것. 재실행 = 시작 횟수 - 1 |
| 토큰 사용량 | `agent_stop`의 `usage` 합. **총 입력 = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`**. `usage`가 null이거나 필드가 빠진 종료 이벤트는 합계에서 빼고 "추출 실패 N건"으로 센다. 전부 없으면 `n/a` |

### KPI

`results/<단계 id>/metrics.json`의 `model`, `savings` 키에서 **숫자 항목만** 카드로 보인다 (문자열, 불리언, 배열은 건너뛴다). 값이 객체이면 `kwh.point`처럼 점으로 이어 3단계까지 펼친다. 그 밖의 최상위 키는 읽지 않는다. 파일이 없거나 숫자 항목이 없으면 안내 문구를 보인다. 단위와 의미는 `metrics.json`을 쓴 에이전트(`ml-modeler`, `savings-simulator`)가 정하며 대시보드는 스키마를 강제하지 않는다.

## 오류 원인 표시

`agent_error` 이벤트의 `summary`(응답 앞 200자, 절대경로 마스킹은 hook이 이미 적용)만 화면에 보인다 (결정 11-2). 다른 이벤트(`agent_start`, `agent_stop`, 게이트 등)의 `summary`는 읽지 않으며 보이지 않는다.

- 위치: 타임라인 탭, 표 아래 "오류 원인" 목록. `✗ <에이전트> (<단계>) · <시각>` 줄 아래에 원인 문장이 온다.
- `summary`가 null이거나 비어 있으면 `원인 미기록`으로 적는다.
- **일반 텍스트로만 그린다** (`st.text`). 마크다운·HTML로 해석하지 않으므로 `<script>`나 링크 문법이 들어 있어도 글자 그대로 보인다.
- 로더가 한 번 더 방어한다: 절대경로(프로젝트 루트, 홈, `/Users/...`)를 가리고, 줄바꿈·제어문자를 공백 하나로 접고, 200자로 자른다.
- 오류의 짝짓기 규칙은 "운영 지표" 절과 같다 (같은 이름의 가장 가까운 앞선 `agent_start`).

## 정체 임계값

기본값은 **30분**이며 (결정 11-1a로 확정) 사이드바의 "정체 판단 기준 (분)"에서 바꾼다. 사이드바와 헤더에는 현재 기준만 적는다. 기본값을 바꿀 때는 `DEFAULT_STALLED_AFTER`(`dashboard/analysis.py`)와 이 문서를 함께 고친다.

## 데모

`examples/demo-project/`는 실제 데이터 없는 가짜 프로젝트이다 (`examples/project.yaml`과 `examples/events-2026-10.jsonl`에서 파생). 6개 단계, 이벤트 43건(`agent_error` 1건 — `summary`가 있어 "오류 원인"이 보인다, `usage` 추출 실패 1건, 수정 요청 1건, 승인 대기 중 게이트 1건, 정체 1건, `phase: null`이라 단계가 추정되는 이벤트 4건), 리뷰 4건, `results/p5-forecast/metrics.json`을 담고 있다. 모든 숫자는 가짜이다.

```bash
python -m agent_workbench.dashboard --project examples/demo-project
```

정체 항목은 이벤트 시각이 과거로 고정되어 있어 언제 열어도 정체로 보인다.

## 단계 추정 (`phase`가 비어 있는 이벤트)

단계가 `doing`을 벗어난 뒤 기록된 이벤트(승인 후 상태 정리, 챕터 초안)는 `phase: null`일 수 있어 단계별 집계에서 빠진다. 대시보드는 읽을 때 다음 규칙으로 단계를 채운다 (DECISIONS 12-1, 규칙 원문은 [`contract.md`](contract.md)의 "대시보드 리더 메모").

- `phase`가 null인 이벤트는 **같은 `session_id`**에서 시각이 앞서고 가장 가까운 게이트 이벤트(`gate_requested`/`gate_approved`) 중 `phase`가 있는 것의 단계로 본다. 게이트 이전의 이벤트, 다른 세션, `session_id`가 없는 이벤트는 그대로 null이다.
- 파일에 기록된 `phase`는 덮어쓰지 않는다. 계산은 읽을 때만 하고 **파일에는 쓰지 않는다.**
- 데이터 모델에서는 `Event.phase_inferred`, `Run.phase_inferred` 등으로 구분한다.
- 화면에서는 단계 이름 뒤에 **`(추정)`** 글자를 붙인다 (색만으로 구분하지 않는다). 타임라인 표와 행 이름, 오류 원인, 개요의 정체 항목, 승인 대기(게이트) 표에 붙고, 단계 단위 표(리드 타임의 "추정 포함", 재작업의 시작 횟수, 단계별 토큰 사용량의 종료 이벤트)는 추정으로 채워진 건수를 따로 적는다. 헤더에는 추정한 이벤트의 총 건수를 보인다.

데모 프로젝트에는 `p4-analysis` 게이트 승인 직후 `phase: null`로 기록된 `pm-tracker`·`fieldbook-writer` 이벤트 4건이 있어 이 표시를 볼 수 있다.

## 테스트

```bash
PYTHONPATH=src uv run --no-project --with pytest --with pyyaml --with jsonschema \
  --with streamlit --with altair --with pandas pytest tests/dashboard tests/contract -q
```

데이터 계층(`loader`, `analysis`, `fmt`)은 합성 폴더와 데모 프로젝트로, 화면은 `streamlit.testing.v1.AppTest`로 모든 탭이 예외 없이 그려지는지 확인한다.

## 한계

- **표본이 작다.** 평균·중앙값은 기술 통계일 뿐 추세나 유의성을 말하지 않는다 (DECISIONS 10절).
- **`agent_error`가 실제로 들어오는지 미검증이다** (DECISIONS 11-1). 들어오지 않으면 오류는 정체로 보인다.
- 오류 짝짓기는 이름 기준이라, 같은 에이전트가 병렬로 여러 번 실행 중이면 어느 실행의 오류인지 틀릴 수 있다.
- `phase`는 이벤트 기록 시점에 `doing`이던 단계(없으면 단일 `review` 단계)로 파생된 값이다. 그래도 비는 이벤트가 있어(예: `review`가 둘 이상일 때), 대시보드는 읽을 때 단계를 추정한다(아래 "단계 추정"). 추정은 같은 세션의 직전 게이트 이벤트만 근거로 하므로 세션이 단계를 걸치면 틀릴 수 있다. 게이트 짝짓기는 한쪽이 비어 있으면 게이트 이름으로만 맞춘다.
- 시각은 이 컴퓨터의 로컬 타임존으로 표시한다 (이벤트의 오프셋은 계산에만 쓴다).
- 이벤트는 매 로드마다 전부 읽는다. 월 단위 파일 수십 개 규모까지를 가정한다.
- 오류 원인은 `agent_error`의 `summary`에만 있다. hook이 응답 앞 200자만 남기므로 긴 원인은 잘려 있고, 정상 실행의 응답 요약은 어디에도 보이지 않는다.
- 읽기 전용이다. 승인, 상태 변경은 대시보드에서 할 수 없다 (상태의 단일 기록자는 `pm-tracker`).
- 브라우저가 열린 동안 파일이 바뀌어도 자동으로 갱신하지 않는다. `새로고침`을 누르거나 위젯을 조작하면 다시 읽는다.
