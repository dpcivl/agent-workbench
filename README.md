# agent-workbench

프로젝트마다 갈아끼워 쓰는 에이전트 오케스트레이션 키트. 데이터 계약(스키마·검증기), hook, 스킬, 공통 에이전트, 로컬 Streamlit 대시보드로 이루어진다. Claude Code용이며 상태: 초기 버전(0.1.0).

## 무엇을 해 주는가

메인 세션이 오케스트레이터가 되어 `/agent-workbench:run-phase <phase-id>`로 단계 하나를 정해진 흐름대로 실행한다.

```
pm-tracker  단계를 doing으로 갱신
   -> 단계 에이전트들 (프로젝트가 정의, project.yaml 순서대로 실행)
   -> reviewer          검증, .workbench/reviews/*.json 기록
   -> pm-tracker        단계를 review로 갱신, gate_requested 기록
   -> 사용자            approve 또는 revise
        approve -> done 갱신, gate_approved 기록, fieldbook-writer가 챕터 초안
        revise  -> change_requested 기록, 영향 보고, 확인 후 재실행
```

- **상태의 단일 기록자**: 단계·태스크 상태는 `pm-tracker`만 쓴다. 다른 에이전트와 메인 세션은 이벤트만 남긴다.
- **게이트**: 승인은 사용자만 한다. 메인 세션은 대신 승인하지 않고, 데이터셋 선택·단계 종료에서 멈춘다.
- **이벤트 로그**: 추가 전용 JSONL. 에이전트 시작·종료·게이트는 hook과 `emit`이 기록하고, 대시보드가 소요 시간·정체·승인 대기·토큰 사용량을 계산한다.
- **쓰기 가드**: 서브에이전트는 에이전트 명세의 `write_scope` 안에만 쓸 수 있고, `data/raw/**`와 이벤트 로그·`.git/**`는 항상 보호된다 (한계는 아래 참조).

## 구성

| 경로 | 내용 |
|---|---|
| `agents/` | 공통 에이전트 3개: `pm-tracker`, `reviewer`, `fieldbook-writer` (도메인 에이전트는 각 프로젝트가 둔다) |
| `skills/` | `project-init`, `run-phase`, `weekly-report` |
| `hooks/` | Claude Code hook 설정 (`hooks.json`) |
| `src/agent_workbench/contract/` | 스키마 검증기, 글롭 해석, 검증 CLI |
| `src/agent_workbench/hooks/` | 이벤트 기록기, 쓰기 가드 (표준 라이브러리 위주, 내부 오류에도 세션을 깨지 않음) |
| `src/agent_workbench/dashboard/` | 로컬 전용 읽기 전용 Streamlit 대시보드 |
| `src/agent_workbench/emit.py` | 오케스트레이터 이벤트(게이트·수정 요청) 기록 CLI |
| `schema/` | `project`, `events`, `review`, `workspace` JSON Schema |
| `examples/` | 예시 파일과 가짜 데이터의 데모 프로젝트 `demo-project/` |
| `scripts/` | `pre-push` 점검 스크립트와 설치기 |
| `docs/` | 계약, hook, 스킬, 대시보드 상세 문서 |
| `.claude-plugin/` | 플러그인·마켓플레이스 매니페스트 |
| `tests/` | contract, hooks, dashboard 테스트 |

## 빠른 시작

Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)를 가정한다. 아래는 이 레포 루트에서 실행한다.

**(a) 데모 대시보드 열기**

```bash
PYTHONPATH=src uv run --no-project --with streamlit --with altair --with pandas --with pyyaml --with jsonschema python -m agent_workbench.dashboard --project examples/demo-project
```

**(b) 프로젝트 데이터 검증**

```bash
PYTHONPATH=src uv run --no-project --with pyyaml --with jsonschema python -m agent_workbench.contract validate <프로젝트>/.workbench/project.yaml
```

`events-*.jsonl`, `reviews/*.json`, `workspace.yaml`도 같은 방식으로 검증한다(파일 이름으로 종류를 감지). 종료 코드 0이면 모두 유효하다.

**(c) 푸시 전 점검 설치**

```bash
scripts/install-pre-push.sh <repo-path>
```

로컬 절대경로, 이메일, 비밀값, 금지 파일(`data/`, `.env*`, `*.pdf`)을 푸시되는 커밋 범위까지 검사한다. 이미 다른 `pre-push` hook이 있으면 덮어쓰지 않는다.

## 플러그인으로 사용하기

`.claude-plugin/plugin.json`이 플러그인 매니페스트이고, `.claude-plugin/marketplace.json`은 이 레포 하나를 담은 단일 플러그인 마켓플레이스다. 두 파일 모두 `claude plugin validate`를 통과한다.

**검증된 방법 (세션 한정, 설정을 바꾸지 않는다):**

```bash
git clone https://github.com/dpcivl/agent-workbench
cd <프로젝트 폴더>
claude --plugin-dir <agent-workbench 경로>
```

이 방식으로 임시 프로젝트에서 `/agent-workbench:run-phase`의 시작·승인 흐름, `agent-workbench:*` 에이전트 호출, 이벤트 기록(사용량 포함), 쓰기 가드 차단을 실제 세션에서 확인했다. 스킬은 `/agent-workbench:<skill>`, 에이전트는 `agent-workbench:<agent>` 이름으로 호출한다.

**아직 검증하지 않은 것:**

- 마켓플레이스를 등록해 `claude plugin install agent-workbench@agent-workbench`로 영구 설치하는 흐름
- 서브에이전트가 **실행 도중** 실패할 때 `agent_error`가 기록되는지. 존재하지 않는 에이전트 유형을 호출하는 실패는 확인했다. 발화하지 않는 경우에는 종료 이벤트가 없는 시작을 대시보드가 "정체"로 표시하는 폴백이 적용된다.
- 실제 KAMP 데이터와 도메인 에이전트로 끝까지 돌린 사례 (더미 단계로만 흐름을 시험했다)

## 알려진 한계

- **쓰기 범위 강제는 `Write`/`Edit`/`NotebookEdit`에 대해서만 완전하다.** `Bash`는 명령 문자열만 보는 best-effort 감지라 `cd data/raw && rm x`, `find ... -delete`, `git clean -fdx`, 스크립트 안의 파일 삭제를 놓친다. `Bash`가 있는 에이전트는 셸로 범위를 우회할 수 있고, 사후에 `reviewer`의 변경 파일 점검으로만 드러난다.
- **표본이 작다.** 사용자 1명, 공통 에이전트 3개 기준이며 운영 지표는 기술 통계 수준이다. 추세나 유의성을 말하지 않는다.
- **트랜스크립트 형식은 문서화되어 있지 않다.** 토큰·모델 추출은 이 형식에 의존해 깨질 수 있다. 실패하면 `usage`는 `null`로 두고, 핵심 스키마와 나머지 이벤트에는 영향이 없다(fail-soft).
- hook 내부 오류는 세션을 막지 않고 `.workbench/hook-errors.log`에만 남는다. 그만큼 가드의 내부 오류는 "열린 쪽"이다.
- 대시보드는 읽기 전용이다. 승인과 상태 변경은 대시보드에서 하지 않는다.

## 개인정보·데이터 정책

- 이벤트 로그(`.workbench/events*.jsonl`)는 커밋하지 않는다(gitignore). 프롬프트·응답 원문은 기록하지 않고, 요약은 앞 200자에 절대경로를 마스킹한다.
- 이 레포를 쓰는 프로젝트의 원본·가공 데이터(예: KAMP 제조 데이터)는 커밋하지 않는다. 프로젝트 레포는 `data/`를 gitignore하고 다운로드 안내만 둔다.
- `examples/`의 데이터는 모두 가짜이며 실제 로그·데이터가 아니다.
- 푸시 전 점검이 로컬 경로·이메일·비밀값·금지 파일이 공개 이력에 들어가는 것을 막는다(HEAD뿐 아니라 푸시 범위 포함).
- 대시보드는 로컬(`127.0.0.1`)에서만 열리고 네트워크 호출·사용 통계 수집이 없다.

## 문서

- [`docs/contract.md`](docs/contract.md): 데이터 계약(이벤트, `project.yaml`, 리뷰, 등록부)과 검증 방법
- [`docs/hooks.md`](docs/hooks.md): hook, 쓰기 가드, 푸시 전 점검과 한계
- [`docs/skills.md`](docs/skills.md): 스킬과 `run-phase` 상태 기계
- [`docs/dashboard.md`](docs/dashboard.md): 대시보드 화면, 지표 계산, 한계

이 키트의 첫 사용 프로젝트는 가짜연구소 13기 *Manufacturing AX Fieldbook*(`pseudolab-13th`)이다. 저장소: <https://github.com/dpcivl/pseudolab-13th>.

## 기여와 라이선스

이슈와 제안을 환영한다. 스키마·에이전트 명세·쓰기 범위를 바꾸는 변경은 먼저 이슈로 논의해 주길 바란다. 라이선스는 [MIT](LICENSE).
