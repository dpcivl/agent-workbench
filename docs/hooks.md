# Hook과 가드 (Hooks and Guards)

agent-workbench가 제공하는 hook, 쓰기 가드, `pre-push` 점검의 동작과 **정확한 한계**를 적는다. 근거는 `DECISIONS.md`의 2절(공개 정책), 4절(에이전트 쓰기 범위), 6절(이벤트 `usage`), 10절(알려진 한계), 12절(변경 이력, 개정 1)이며, 여기에 없는 동작은 약속하지 않는다.

## 1. 한눈에 보기

| 구성 | 파일 | 발화 시점 | 하는 일 | 막을 수 있나 |
|---|---|---|---|---|
| 이벤트 기록기 | `src/agent_workbench/hooks/record_event.py` | `SubagentStart`, `SubagentStop`, `PostToolUseFailure`(`Agent` 도구) | `.workbench/events-YYYY-MM.jsonl`에 한 줄 추가 | 아니오 (항상 exit 0) |
| 쓰기 가드 | `src/agent_workbench/hooks/write_guard.py` | `PreToolUse`: `Write`, `Edit`, `NotebookEdit`, `Bash` | 보호 경로·쓰기 범위·서브에이전트의 push/commit 차단 | 예 (exit 2) |
| 오케스트레이터 이벤트 | `python3 -m agent_workbench.emit` (`src/agent_workbench/emit.py`) | 메인 세션이 직접 실행 | 게이트·변경 이벤트 기록 | 아니오 |
| 푸시 전 점검 | `scripts/pre-push-check.sh`, `scripts/install-pre-push.sh` | `git push` (git `pre-push` hook) | 로컬 경로·이메일·비밀값·금지 파일 점검 | 예 (exit 1) |

Claude Code hook 설정은 `hooks/hooks.json`이다. 플러그인으로 설치하면 `${CLAUDE_PLUGIN_ROOT}`가 이 레포 루트를 가리킨다. 두 Python hook은 표준 라이브러리만 쓰고(PyYAML은 있으면 사용), `agent_workbench` 패키지를 import하지 않아 패키지가 깨져도 기록과 가드는 동작한다. 단, 두 hook은 **워크벤치 프로젝트에서만** 동작한다(다음 절).

## 옵트인: 언제 동작하는가

플러그인이 로드된 세션이라도 작업 폴더가 워크벤치 프로젝트가 아니면 두 hook(`record_event.py`, `write_guard.py`)은 **아무 일도 하지 않는다**. 모든 matcher(`Write|Edit|NotebookEdit`, `Bash`, `SubagentStart`, `SubagentStop`, `PostToolUseFailure`)에 같은 규칙이 적용된다.

**규칙**: 프로젝트 루트에 `.workbench/project.yaml` 파일이 있으면 워크벤치 프로젝트다. `.workbench/` 폴더만 있고 `project.yaml`이 없으면 프로젝트가 아니다.

프로젝트 루트는 다음 순서로 찾는다.

1. `$CLAUDE_PROJECT_DIR`가 설정되어 있으면 그 폴더만 검사한다. 거기에 `.workbench/project.yaml`이 없으면 상위 폴더로 올라가 찾지 않고 프로젝트가 아닌 것으로 본다.
2. 설정되어 있지 않으면 payload의 `cwd`(없으면 프로세스 현재 디렉터리)에서 올라가며 `.workbench/project.yaml`이 있는 가장 가까운 폴더를 쓴다.
3. 없으면 프로젝트가 아니다.

프로젝트가 아닐 때: exit 0, 출력 없음, 아무것도 쓰지 않는다. `.workbench/` 폴더를 만들지 않고, 이벤트도 `hook-errors.log`도 남기지 않는다. 가드는 이때 보호 경로(`data/raw/**`, `.git/**`)도 검사하지 않으며 `write_scope`가 없는 서브에이전트(내장 `general-purpose` 포함)의 쓰기도 막지 않는다. 판정 중 예외가 나면 기록기는 "프로젝트 아님"으로 처리하고, 가드는 기존대로 통과(exit 0)시킨다.

프로젝트 안에서는 아래 2~6절의 동작이 그대로다(fail-closed 가드, 보호 경로, 임시 폴더 예외, fail-soft 기록기).

```
~/notes/                      # .workbench/ 없음 -> hook 전부 no-op, 아무 파일도 안 생긴다
~/work/forge/                 # .workbench/project.yaml 있음 -> 기록기와 가드 동작
~/work/forge/docs/deep/       # 여기서 세션을 열어도 상위의 project.yaml을 찾아 동작
~/work/bare/.workbench/       # project.yaml 없음 -> 프로젝트가 아니므로 no-op
```

새 프로젝트는 `project-init`(`skills/project-init`)이 `.workbench/project.yaml`을 만들면서 옵트인된다. 이 규칙은 hook에만 적용되며 `emit`(오케스트레이터 이벤트)은 명시적으로 실행하는 명령이라 대상이 아니다.

## 2. 이벤트 기록기 (`record_event.py`)

stdin의 hook payload를 읽어 한 줄 JSON을 `<project>/.workbench/events-YYYY-MM.jsonl`에 덧붙인다. 필드 정의와 검증은 `docs/contract.md`를 따른다.

| hook | 기록하는 `event` | 채우는 것 |
|---|---|---|
| `SubagentStart` | `agent_start` | `session_id`, `agent_id`, `agent`(`agent_type` 그대로, 네임스페이스 유지), `phase`, `rev` |
| `SubagentStop` | `agent_stop` | 위 필드 + `summary`(`last_assistant_message` 앞 200자), `usage` |
| `PostToolUseFailure` | `agent_error` | `tool_name`이 `Agent`/`Task`일 때만. `agent`는 호출자가 아니라 `tool_input.subagent_type`, `summary`는 `error` |

### 필드를 만드는 규칙과 한계

- **`phase`** (DECISIONS 12-1): `.workbench/project.yaml`에서 다음 순서로 정한다.
  1. `status: doing`인 첫 단계의 `id` (`doing`이 둘 이상이면 첫 번째만).
  2. `doing`이 없으면 `status: review`인 단계의 `id`. **`review`가 정확히 하나일 때만** 쓴다. 둘 이상이면 어느 단계의 이벤트인지 알 수 없으므로 쓰지 않는다.
  3. 그 밖에는 `null`.

  `doing`이 `review`보다 항상 우선한다. 이 규칙으로 승인 대기 중(`review`)이거나 승인 직후 `pm-tracker`·`fieldbook-writer`가 남기는 이벤트가 단계를 잃지 않는다. 그래도 `null`로 남은 이벤트는 hook이 아니라 대시보드가 읽을 때 같은 세션의 직전 게이트 이벤트로 단계를 추정한다(`docs/contract.md` "단계 추정"). 파일은 다시 쓰지 않는다.

  PyYAML이 있으면 그것으로, 없거나 파싱이 실패하면 관대한 줄 단위 파서로 읽는다. 환경변수 `WORKBENCH_SIMPLE_YAML=1`이면 PyYAML을 건너뛰고 항상 줄 단위 파서를 쓴다. 줄 단위 파서는 블록 스타일(`phases:` 아래 `- id:` / `status:`)만 이해하며, 두 경로는 같은 규칙을 따른다.
- **`rev`**: `git rev-parse --short HEAD` (타임아웃 3초). 레포가 아니거나 커밋이 없으면 `null`.
- **`summary`**: 공백을 접고 200자로 자른 뒤 **절대경로만** `<path>`로 마스킹한다. 마스킹 대상은 `/Users`, `/home`, `/root`, `/tmp`, `/var`, `/private`, `/opt`, `/mnt`, `/srv`, `/Volumes`로 시작하는 경로와 `C:\...` 형태다. `~/...` 같은 홈 상대 표기, 이메일, 비밀값은 마스킹하지 않는다. 프롬프트·응답 전체는 기록하지 않는다.
- **`usage`** (`agent_stop`에만): 서브에이전트 트랜스크립트(`agent_transcript_path`)에서 추출한다.

  | 키 | 값 |
  |---|---|
  | `model` | 요청별 `message.model` 중 가장 많은 값 |
  | `input_tokens` | 요청별 최댓값의 합 |
  | `output_tokens` | 요청별 최댓값의 합 |
  | `cache_creation_input_tokens` | 요청별 최댓값의 합 |
  | `cache_read_input_tokens` | 요청별 최댓값의 합 |

  같은 API 요청이 트랜스크립트에 여러 항목으로 반복 기록되므로 `requestId`(없으면 `message.id`)로 묶고, 요청 안에서는 각 카운터의 최댓값을 취한 뒤 요청 간에 합산한다. `message.role`이 `assistant`인 항목만 센다. `input_tokens`는 캐시 밖의 입력만 세므로 입력량의 대부분은 두 캐시 필드에 있다(실측: input 12, cache_creation 123,145, cache_read 376,068). 캐시 필드가 트랜스크립트에 없으면 0으로 센다.
  - **한계**: 트랜스크립트 형식은 비공개이고 바뀔 수 있다. 파일이 없거나, 읽을 수 없거나, 사용량 항목이 하나도 없으면 `usage`는 `null`이다. 이때도 이벤트 자체는 기록된다.
- **`agent_error`의 한계**: 서브에이전트 실패가 `PostToolUseFailure`로 들어오는지는 공식 문서에 없다(DECISIONS 11-1). 발화하지 않으면 `agent_error`는 생기지 않고, 대시보드의 **정체** 표시(종료 이벤트 없는 `agent_start`)가 대신한다.
- **파일 이름의 월**은 로컬 시각 기준이다. 쓰기는 단일 `O_APPEND` write 한 번이라 병렬 서브에이전트가 동시에 기록해도 줄이 섞이지 않는다(테스트: 20개 동시 기록).

### 오케스트레이터 이벤트 (`emit`)

```
PYTHONPATH=<agent-workbench>/src python3 -m agent_workbench.emit gate_requested --gate dataset-selection
python3 -m agent_workbench.emit change_requested --phase p3-data --summary "..."
```

`gate_requested`, `gate_approved`, `change_requested`, `change_approved`만 받고, `gate_*`는 `--gate`가 필수다. `agent`는 `orchestrator`, `usage`는 `null`. 프로젝트 폴더는 `$CLAUDE_PROJECT_DIR`, 없으면 현재 디렉터리의 가장 가까운 `.workbench/` 상위 폴더, 없으면 현재 디렉터리다(hook의 옵트인 판정과 달리 `project.yaml`을 요구하지 않는다). 이 명령만 쓰기에 실패하면 exit 1을 돌려준다(hook이 아니므로 fail-soft 대상이 아니다).

## 3. 쓰기 가드 (`write_guard.py`)

`PreToolUse`에서 도구 호출을 검사해 차단하면 사유를 stderr에 쓰고 **exit 2**로 끝낸다. 허용이면 아무것도 출력하지 않고 exit 0이다.

### 3.1 규칙 표

| 대상 도구 | 호출자 | 규칙 |
|---|---|---|
| `Write`, `Edit`, `NotebookEdit` | 누구든 (메인 세션 포함) | **보호 경로**는 항상 거부 (3.2) |
| 〃 | 메인 세션 (`agent_type` 없음) | 보호 경로 외에는 제한 없음 |
| 〃 | 서브에이전트 | 선언된 `write_scope` 안, 또는 **OS 임시 폴더**(3.3)만 허용. `write_scope`를 찾지 못하면 거부(fail closed) |
| `Bash` | 누구든 | 보호 경로를 겨냥한 쓰기·삭제 명령을 문자열로 감지해 거부 (best-effort, 3.5) |
| `Bash` | 서브에이전트만 | `git push`, `git commit`, `gh repo create`, `gh pr create`, `gh release` 거부 |

### 3.2 보호 경로 (개정 1에서도 그대로)

| 규칙 | 해당 경로 |
|---|---|
| `data/raw/**` | 프로젝트 루트 기준 `data/raw` 및 그 아래 전부 |
| `.workbench/events*.jsonl` | 이벤트 로그 (hook만 기록) |
| `.git/**` | 경로 어디에든 `.git` 요소가 있으면 |

- 비교는 대소문자를 구분하지 않는다(macOS 볼륨 대응).
- 경로는 `realpath`로 풀어 판정하므로 `..` 이동과 심볼릭 링크로 우회할 수 없다. 상대 경로는 payload의 `cwd`를 기준으로 푼다.
- 프로젝트 밖의 임시 폴더 경로라도 위 규칙에 해당하는 요소(`.git`, `data/raw`, `.workbench/events*.jsonl`)가 경로 중간에 있으면 거부한다. 임시 폴더에 둔 다른 체크아웃의 보호 위치까지 지키기 위한 보수적 해석이다.

### 3.3 임시 폴더 허용 (개정 1)

쓰기 범위가 있는 서브에이전트도 **프로젝트 밖의** 다음 위치에는 쓸 수 있다.

- `tempfile.gettempdir()` (`$TMPDIR` 반영), `/tmp`, `/private/tmp`, `/var/folders`(macOS의 `/private/var/folders` 포함)
- hook payload의 `scratchpad_dir` 필드 (있을 때, 절대경로일 때, `/`가 아닐 때)

세부:

- 프로젝트 안의 경로에는 적용하지 않는다. 프로젝트 폴더 자체가 임시 폴더 아래에 있어도(테스트 환경) 안쪽 경로는 `write_scope`로만 판정한다.
- 보호 경로 규칙이 이긴다. 임시 폴더를 가리키는 심볼릭 링크가 `data/raw`로 이어지면 `realpath` 결과가 프로젝트 안이므로 거부된다.
- `write_scope`가 없는 에이전트와 있는 에이전트가 같은 임시 폴더 규칙을 쓴다(둘의 불일치 해소).

### 3.4 `write_scope` 찾는 순서

에이전트 이름은 payload의 `agent_type`이다. 네임스페이스가 있으면(`agent-workbench:pm-tracker`) 마지막 `:` 뒤만 쓴다. 이름은 `[A-Za-z0-9._-]+`이고 `.`로 시작하지 않아야 한다(경로 이동 방지).

1. `<project>/.claude/agents/<name>.md` (프로젝트 명세가 플러그인 명세보다 우선)
2. `<plugin root>/agents/<name>.md` (`$CLAUDE_PLUGIN_ROOT`, 없으면 이 파일 위치에서 계산한 레포 루트)
3. 파일 이름이 다르면 위 두 폴더에서(각 200개까지) frontmatter의 `name`이 같은 파일

찾은 명세의 frontmatter `write_scope`를 읽는다(문자열, `[a, b]`, 블록 목록 모두 가능, PyYAML 불필요). 명세가 없거나 `write_scope`가 비어 있으면 거부한다. 프로젝트 루트는 `$CLAUDE_PROJECT_DIR`, 없으면 `cwd`에서 올라가며 처음 만나는 `.workbench/project.yaml`이 있는 폴더다(옵트인 절). 프로젝트가 아니면 가드 자체가 동작하지 않는다.

글롭 문법: `*`는 `/`를 넘지 않고, `**`는 `/`를 포함해 모든 것에 맞으며, `?`는 한 글자다. 끝이 `/`이면 `/**`와 같다. 절대경로나 `..`가 든 패턴은 아무것도 허용하지 않는다.

### 3.5 Bash 가드의 사각지대

Bash 가드는 **명령 문자열만** 본다. 실행 중에 스크립트가 무엇을 하는지는 볼 수 없다. 아래 요소만 감지한다.

- 리다이렉션 `>`, `>>` 의 대상
- `rm`, `rmdir`, `unlink`, `shred`, `mv`, `tee`, `truncate`, `touch`, `sed -i`, `cp`(목적지), `dd of=` 의 대상
- `&&`, `||`, `;`, `|`, 백틱, 괄호로 나뉜 각 조각, `sudo`/`env`/`xargs` 등 래퍼, `bash -c '...'`(2단계까지)
- 삭제 명령의 광범위 대상(`data`, `data/*`, `.workbench`, `.workbench/*`, `.*`)

읽기(`cat`, `ls`, `grep`, `cp data/raw/a data/processed/a`의 원본 등)는 막지 않는다. `write_scope`는 Bash에 적용되지 **않는다**. Bash가 있는 에이전트(`data-engineer`, `energy-analyst`, `ml-modeler`, `savings-simulator`)는 셸로 범위 밖을 쓸 수 있다.

| 우회 예 | 왜 놓치나 |
|---|---|
| `cd data/raw && rm x` | 조각마다 따로 보므로 `rm x`의 대상 `x`는 보호 경로로 보이지 않는다. 앞 조각의 `cd`를 추적하지 않는다 |
| `find data/raw -name '*.csv' -delete` | `find`는 감지 대상 동사가 아니다. `-delete`, `-exec rm` 모두 놓친다 |
| `git clean -fdx` | 대상 경로가 명령에 나타나지 않는다 (`.gitignore`된 `data/raw`도 지워진다) |
| `python -c "import os; os.remove('data/raw/a.csv')"` | 인터프리터 안의 파일 조작은 보이지 않는다. 스크립트 파일, 노트북 셀, `make` 타깃도 같다 |
| `x=data/raw; rm $x/a.csv` | 변수 확장을 풀지 않는다. 글롭·명령 치환도 마찬가지다 |

보완책(DECISIONS 10절):

1. **`reviewer`의 변경 파일 점검**: 단계 종료 시 에이전트 `write_scope` 밖에서 바뀐 파일과 `data/raw/**` 변경 여부를 `Glob` 최신순과 이벤트 시각으로 대조한다. `reviewer`에는 `Bash`를 주지 않는다.
2. **이 가드 자체**: 문자열로 드러나는 직접적인 쓰기·삭제는 막는다(`rm data/raw/a.csv`, `echo x > data/raw/a.csv` 등).

위 두 가지가 전부이며, 사각지대를 통한 삭제나 변경은 **막지 못하고** 사후 점검으로만 드러난다. `pre-push` 점검(4절)은 원본 데이터가 커밋되어 공개되는 것만 막는다.

### 3.6 서브에이전트의 push/commit 금지

`git push`와 `git commit`은 `-C <dir>`, `-c k=v`, `--옵션`이 사이에 끼어도 감지한다(`git -C . push`, `git -c user.name=x commit`). `git status`, `git diff`, `git add`, `git stash push`, `gh pr list/view`, `gh repo view`는 허용한다. 메인 세션에는 이 금지가 없다. 문자열 기반이므로 `bash -c`가 아닌 간접 실행(스크립트 안의 `git push`)은 놓친다.

## 4. 푸시 전 점검 (`scripts/pre-push-check.sh`)

### 4.1 설치와 실행

```
scripts/install-pre-push.sh <repo-path>   # <repo>/.git/hooks/pre-push 로 복사
scripts/pre-push-check.sh [repo-path]     # 직접 실행 (기본: 현재 레포)
git push --no-verify                      # 한 번만 건너뛰기
```

- 설치 스크립트는 **복사**한다. 점검 스크립트를 고치면 각 레포에서 `install-pre-push.sh`를 다시 실행해야 한다. 같은 내용이면 변화 없음, 이 점검 스크립트의 이전 복사본이면 교체한다. 그 외의 기존 `pre-push` hook은 덮어쓰지 않고 exit 1로 알린다.
- 대상은 `pseudolab-13th`와 `agent-workbench` 두 레포다(DECISIONS 2절).
- 의존성은 bash, git, grep, sed, awk뿐이다(macOS 기본 도구).

### 4.2 무엇을 검사하나

| 분류 | 규칙 |
|---|---|
| `local-path` | `/Users/<이름>/`, `/home/<이름>/`, `C:\Users\<이름>\` |
| `email` | 이메일 주소. `*@users.noreply.github.com`, `*@example.com/org/net`은 제외 |
| `secret` | 개인 키 헤더, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` 토큰, `AKIA...`, `sk-...` 키, `password=...` 리터럴 (값은 출력하지 않고 `경로:줄`만 보여준다) |
| `forbidden` | 추적 중인 `.env*`, `data/` 아래 전부(`data/README.md` 제외), `*.pdf` |
| `range-error` | 푸시되는 ref의 커밋 목록을 만들 수 없음 (푸시 차단) |

커밋 **작성자 이메일은 검사하지 않는다**(DECISIONS 2절: 커밋 이메일은 git 전역 설정 유지).

### 4.3 검사 대상: HEAD와 푸시 범위

1. **HEAD**: 항상. 추적 파일 전체를 검사한다.
2. **푸시되는 커밋 범위**: git이 stdin으로 넘기는 `<local ref> <local sha> <remote ref> <remote sha>` 줄마다 다음 범위를 만든다.

   | 상황 | 범위 |
   |---|---|
   | 새 브랜치 (remote sha가 전부 0) | `git rev-list <local> --not --remotes` |
   | 기존 브랜치 | `<remote sha>..<local sha>` |
   | remote sha가 로컬에 없음 (강제 푸시 등) | 새 브랜치와 같이 처리 (보수적) |
   | 삭제 ref (local sha가 전부 0) | 건너뜀 |

   범위의 각 커밋에 대해 **추가된 줄**을 같은 패턴으로 검사한다(병합 커밋은 어느 부모에도 없는 줄만). 그리고 범위 안의 커밋이 **금지 파일을 추가했으면, 이후 커밋에서 지웠더라도 실패**한다. 이력에 남기 때문이다. 결과에는 해당 커밋의 앞 12자리가 표시된다.
3. **수동 실행**: stdin이 터미널이거나 비어 있으면 HEAD만 검사한다(범위 검사 없음).

한계:

- 범위 검사는 `pre-push`가 stdin으로 ref를 받을 때만 동작한다. `scripts/pre-push-check.sh <repo>`를 손으로 실행하면 HEAD만 본다.
- 비밀값이 이미 원격에 올라간 뒤에는 이 점검이 도움이 되지 않는다. 그 경우 값을 폐기(rotate)하고 이력을 고쳐야 한다.
- `git rev-list --not --remotes`는 로컬의 원격 추적 ref에 의존한다. `git fetch`를 오래 하지 않았다면 이미 올라간 커밋도 범위에 들어가 같은 내용이 다시 검사된다(안전한 쪽의 오차).
- HEAD 검사는 푸시하는 ref와 무관하게 항상 수행하므로, 다른 브랜치를 푸시하거나 원격 브랜치를 삭제할 때도 현재 HEAD의 문제로 막힐 수 있다.
- 바이너리 파일의 내용은 검사하지 않는다. 금지 파일 규칙(`data/`, `*.pdf`, `.env*`)만 이름으로 막는다.

### 4.4 예외: `.prepush-allow`

레포 루트(작업 트리)의 `.prepush-allow`. 한 줄에 확장 정규식(`grep -E`) 하나, 빈 줄과 `#`로 시작하는 줄은 무시한다. 어느 한 정규식이라도 발견 기록에 맞으면 그 발견은 건너뛴다.

| 발견 | 기록 형식 | 예 |
|---|---|---|
| 내용 (`local-path`, `email`, `secret`) | `<경로>:<줄>:<맞은 텍스트>` | `^docs/examples/` , `^docs/sample\.md:` |
| 금지 파일 | `<경로>` | `^\.env\.example$` |

- 범위 발견의 `<줄>`은 그 내용을 추가한 커밋에서의 줄 번호다. 줄 번호는 커밋마다 달라지므로 예외는 경로 기준으로 쓰는 편이 안전하다.
- 같은 규칙이 HEAD와 범위 검사에 모두 적용된다.
- `.prepush-allow` 파일 자체는 검사하지 않는다. 예외를 넓게 쓰면 점검이 무력해지므로 커밋할 때 리뷰 대상으로 본다.

## 5. fail-soft 동작과 `hook-errors.log`

- **hook은 세션을 깨지 않는다.** 워크벤치 프로젝트가 아니면 둘 다 처음부터 아무것도 하지 않는다(옵트인 절). 프로젝트 안에서 이벤트 기록기와 가드는 내부 오류(잘못된 JSON, 쓰기 실패, 파서 예외)가 나면 **exit 0**으로 끝낸다. Claude Code에서 도구 호출을 막는 것은 exit 2뿐이므로, 구현 버그나 `python3` 부재가 작업을 멈추지 않는다.
- 그 결과 가드의 **내부 오류는 열린 쪽**이다(차단 안 함). 반면 "명세나 `write_scope`가 없는 서브에이전트"는 오류가 아니라 정책이므로 닫힌 쪽(거부)이다.
- 오류는 (워크벤치 프로젝트일 때만) `<project>/.workbench/hook-errors.log`에 한 줄로 남긴다: `<시각>\t<hook 이름>\t<오류 클래스>: <메시지 200자 이내>`. **payload나 응답 내용은 기록하지 않는다.** 로그 쓰기 자체가 실패하면 조용히 넘어간다.
- 이벤트가 안 쌓이거나 가드가 안 먹는 것 같으면 먼저 이 파일을 확인한다. 이 로그는 커밋 대상이 아니다(레포 `.gitignore`에 추가하는 것을 권장한다. 현재 `.gitignore`는 `.workbench/events*.jsonl`만 제외한다).
- `pre-push` 점검은 hook이 아니라 게이트이므로 fail-soft가 아니다. 점검이 실패하면(저장소 아님 등) exit 1이고, 푸시되는 ref의 커밋을 나열하지 못하면 `range-error`로 차단한다.

## 6. 환경 변수

| 변수 | 효과 |
|---|---|
| `WORKBENCH_SIMPLE_YAML=1` | `project.yaml`의 `phase` 파생에서 PyYAML을 건너뛰고 줄 단위 파서만 쓴다. PyYAML이 환경마다 다른 결과를 낼 때 디버깅·재현용 |
| `CLAUDE_PROJECT_DIR` | 프로젝트 루트(이벤트 기록 위치, 가드의 프로젝트 경계). Claude Code가 설정. 이 폴더에 `.workbench/project.yaml`이 없으면 hook은 no-op이다(옵트인 절) |
| `CLAUDE_PLUGIN_ROOT` | 플러그인 루트(`agents/` 명세 위치). Claude Code가 설정 |
| `TMPDIR` | 임시 폴더 허용 목록에 포함 (3.3) |
| `CLAUDE_SESSION_ID` / `CLAUDE_CODE_SESSION_ID` | `emit`이 이벤트의 `session_id`로 사용. 없으면 `null` |

## 7. 테스트

```
PYTHONPATH=src uv run --no-project --with pytest --with pyyaml pytest tests/hooks -q
```

hook은 모두 subprocess로 실행해 검증한다(옵트인: `tests/hooks/test_hooks_optin.py` — 프로젝트 밖·빈 `.workbench/`·상위 폴더 탐색·`CLAUDE_PROJECT_DIR` 우선, 가드: 허용/거부/우회 시도, 기록기: 필드·중복 제거·동시 기록, 점검: 임시 git 레포에서 HEAD·푸시 범위·실제 `git push`).
