# HELM — 핸드오프

> **문서 규칙.** 이 파일은 **지금 상태와 다음에 할 일**만 담는다(계속 덮어씀).
> 날짜별로 무엇을 고쳤고 무엇이 터졌는지는 `logs/YYYY-MM-DD-log.md`에 쌓는다(고치지 않음).
> 마무리할 때 `/wrapup` 스킬이 이 둘을 갱신한다.

> **이 폴더엔 앱이 둘 있다.** 어느 쪽을 만지는지 먼저 확인할 것.
>
> | | 파일 | 뭐냐 | 문서 |
> |---|---|---|---|
> | **본체** | `app.py` | 터미널을 탭·분할로 (지금 문서) | 이 파일 |
> | **채팅형** | `chat_proto.py` | 터미널 없이 Claude 앱처럼 | `HANDOFF_CHAT.md` |
>
> 둘은 완전히 별개로 돌아간다. `config.json`(폴더 목록)만 같이 쓴다.
> **2026-08-17 본체로 정리됨** — 호윤이 며칠 써보고 "당분간 별 말 없으면 본체"라고 못박음.
> 채팅형은 "아직 쓸 수준 아니고 써보니 불편"이라 밀림. 아래 문서는 전부 본체 얘기다.

한 창 안에서 Claude Code 세션 여러 개를 탭·분할로 다루는 데스크톱 앱.
`Desktop/ClaudeCode/agent_console.py`(구 에이전트 관리 콘솔)의 후속이지만 **별개 앱**이고,
구 앱은 그대로 남아있다.

## 다음에 해야 할 것
- **창에서 Ctrl+V 실제로 눌러보기** — 서버 왕복과 콘솔은 확인했지만 pywebview 창 안에서
  실제로 붙여넣어 본 검증은 아직이다. 안 되면 `termClipboardKeys()`(`static/app.js`)가
  키를 먹는지부터 볼 것.
- **이미지 미리보기 패널** — 지금은 이미지를 보여줄 때 별도 뷰어 창(Photos)이 뜬다.
  편집기 패널처럼 `kind`를 하나 더 만들어 앱 안에서 보게 하면 창이 안 튄다.
  만들면 `HELM_NOTE`(`app.py:62`) 문구도 "패널에 띄워라"로 같이 고칠 것.
  (편집기는 `TEXT_EXT`에 있는 확장자만 열어서 png/jpg는 지금 못 연다)
- 분할 칸을 드래그로 크기 조절 (지금은 균등 분할 고정)
- 폴더 목록 인앱 추가/삭제 (지금은 config.json 직접 편집)
- 앱 껐다 켤 때 세션 자동 복구 (구 앱엔 있던 기능)
- 사용량 바(컨텍스트·한도)를 채팅형에도 붙일지 — 본체로 정리됐으니 사실상 보류
- (선택) 폴더명을 `Desktop/HELM`으로 — 폴더 이동 + `.lnk` 재생성이 한 세트다

## 실행
`pythonw app.py` (콘솔창 안 뜸). 바탕화면 바로가기를 만들어 두면 편하다.

점검할 땐 창 없이 서버만: `python app.py --server 8791` → 브라우저로 접속.

## 구 앱과 뭐가 다른가
구 앱은 `conhost.exe`로 **진짜 별개의 콘솔 창**을 띄운 뒤 Win32로 오너 지정·테두리 제거·좌표
계산을 해서 겹쳐놨다. 그래서 (1) 창을 EnumWindows로 찾을 때까지 늦게 뜨고 (2) conhost가
포커스마다 테두리를 되붙여서 앱과 싸우느라 화면이 깨지고 (3) 그 모든 게 tkinter 단일
스레드에 얹혀 있어 멈췄다.

HELM은 콘솔 창을 아예 안 만든다. ConPTY(`pywinpty`)로 claude를 직접 물고
화면은 xterm.js가 그린다. 찾을 창도, 벗길 테두리도, 계산할 좌표도 없다.

## 구조
```
app.py              Flask + 웹소켓 + PTY 관리 + 세션 색인, pywebview 창
static/index.html   레이아웃
static/app.css      다크 테마
static/app.js       패널(xterm+ws) 관리, 탭/분할/모델 전환
static/vendor/      xterm.js 5.5.0, addon-fit, addon-webgl (node 불필요, 파일만 씀)
config.json         폴더 목록·마지막 선택 (첫 실행 때 구 앱 projects.json에서 물려받음)
.session_cache.json 세션 제목 색인 캐시 (지워도 됨, 다시 만듦)
```

## 이름·로고 (2026-08-14 개명)
**ClaudeDock → HELM.** 옛 문서·경로에서 ClaudeDock을 보면 같은 물건이다.
**폴더명 `Desktop/ClaudeDock`만 그대로** — 바로가기 대상 경로가 깨지기 때문.
바로가기는 `HELM.lnk`(본체) / `HELM 채팅.lnk`(채팅형) — **로컬에만 둔다.**
`.lnk` 안에 만든 PC의 SID·호스트명·절대경로가 박혀서 저장소에서 뺐다(2026-08-31).

로고는 조타륜(ship's helm), 색은 액센트 `#c98f5a`. **원본이 두 벌이니 같이 고칠 것**:
- **화면** — `static/index.html`·`static_chat/index.html`의 인라인 SVG (파비콘도 같은 그림 data URI)
- **윈도우** — `helm.ico`. `make_icon.py`가 같은 좌표로 9개 크기를 굽는다.
  바로가기 아이콘과 창·작업표시줄 아이콘(`webview.start(icon=...)`)이 이걸 쓴다.

작업표시줄이 pythonw.exe에 묻히지 않게 `SetCurrentProcessExplicitAppUserModelID`를 준다
(본체 `Hoyoon.HELM.Main` / 채팅형 `Hoyoon.HELM.Chat`). 안 주면 다른 파이썬 창과 한 덩어리가 된다.
⚠ pywebview 문서엔 `icon`이 GTK/QT 전용이라 적혀 있지만 **틀렸다** — 6.2.1 winforms
백엔드가 `_state['icon']`을 읽어 창 아이콘으로 쓴다(실측 확인).

## 기능
- **붙여넣기/복사** — Ctrl+V · Ctrl+Shift+V · Shift+Insert = 붙여넣기, Ctrl+Shift+C = 선택 복사.
  (Ctrl+C는 TUI 중단 신호라 그대로 뒀다.) WebView2 안에서는 `navigator.clipboard` 읽기가 막혀서
  서버가 윈도우 클립보드를 직접 읽는 `/api/clipboard`로 넘어간다 — 이게 실제로 쓰이는 경로다.
- **탭 + 분할 1/2/3/4** — 분할을 바꿔도 패널 DOM을 옮겨 끼우기만 해서 **터미널 내용이 안 날아감**.
  (다시 만들면 날아간다 — `render()`에서 `slot.appendChild(p.el)` 하는 이유)
- **모델 세부버전 + 강도** — 계열별로 묶인 드롭다운(Fable 5 / Opus 5·4.8·4.1 / Sonnet 5·4.5 /
  Haiku 4.5)과 강도 버튼(low·medium·high·xhigh·max). 왼쪽은 새 세션용, 위쪽은 지금 보는 세션용.
  - 새 세션 = `--model` + `--effort`, 실행 중 = `/model` + `/effort`를 PTY에 밀어넣음
  - 두 슬래시 명령을 연달아 보내면 섞이므로 `set_model()`이 사이를 1.2초 띄운다
  - Haiku 4.5 / Sonnet 4.5는 강도 개념이 없어(배너에 effort가 안 뜸) 강도 칸이 잠긴다
  - ⚠ `/model`은 **새 세션 기본값까지** 바꾸고, `/effort`는 **그 세션만** 바꾼다 (claude 동작 차이)
  - 목록은 `config.json`의 `models` 배열 — 새 모델 나오면 여기 한 줄 추가, 코드 수정 불필요
- **최근 작업** — `~/.claude/projects/*/*.jsonl`을 읽어 목록화. 파일명이 곧 세션 ID라
  클릭하면 `--resume <id>`로 이어감. `-c`/`-r` 칠 일 없음.
  - 제목 = 첫 사용자 발화, 앞 60줄만 읽어서 5MB짜리도 빠름
  - 사용자 발화가 없는 세션(열자마자 닫은 것)은 목록에서 제외
- **코드 편집기 패널** — 사이드바 "코드 편집기 열기". 패널에 종류(`kind`)가 생겨서
  `term`(claude)과 `edit`(편집기)이 같은 탭/분할 체계를 공유한다.
  - CodeMirror 5 벤더링(node 불필요), 파일트리 + Ctrl+S 저장, 저장 안 된 변경은 탭에 `•`
  - 파일 접근은 `_safe()`가 **등록된 폴더와 홈 안쪽으로만** 제한 (`C:\Windows` 등 차단)
  - 저장은 `newline=""`로 써서 원본 줄바꿈(LF)을 CRLF로 바꾸지 않는다
- **권한 모드** — `--permission-mode`로 새 세션에 적용. 6개 값 전부 CLI가 받는 것 확인:
  `manual` / `acceptEdits` / `plan` / `auto` / `dontAsk` / `bypassPermissions`.
  - `dontAsk`·`bypassPermissions`는 경고색 + 안내문
  - **실행 중인 세션엔 못 바꾼다** — claude TUI에서 Shift+Tab으로 순환
  - ⚠️ `bypassPermissions`에 `--allow-dangerously-skip-permissions`를 같이 붙이는데,
    **이게 필수인지는 검증 안 됨**(짝 없이도 인자 검증은 통과). 도움말 문구상 맞다고 보고 남겨둔 것
- **컨텍스트 사용량 바** — 상단바 오른쪽. 지금 보는 세션이 물고 있는 토큰을
  막대 + `48.3% · 96,679 / 200,000`로. 70%부터 노랑, 90%부터 빨강. 4초마다 갱신.
  - CLI가 토큰 수를 안 내주므로 **세션 jsonl의 마지막 assistant `usage`**를 읽는다
    (`input + cache_read + cache_creation + output`) — `/api/usage`
  - 서브에이전트(`isSidechain`) usage는 본 대화 컨텍스트가 아니라 걸러낸다
  - **첫 메시지 전에는 세션 파일이 없어서 바가 안 뜬다**(고장 아님)
  - 한도는 `DEFAULT_CONTEXT`(200k). 모델별 예외는 `CONTEXT_LIMITS`에
  - **계정을 바꿔도 이 숫자는 안 변한다** — 컨텍스트는 세션 것이지 계정 것이 아니다
    (계정별 한도는 아래 "구독 한도 알약")
- **구독 한도 알약** — 상단바, 컨텍스트 바 왼쪽. `5시간 12% · 주간 69%`.
  툴팁에 리셋 시각·프로모·근사치 주의. **클릭하면 즉시 새로고침**, 평소엔 10분마다.
  - CLI에 이 수치를 주는 플래그도 파일도 없다(확인함). TUI `/usage` 화면에만 있다 →
    **안 보이는 세션을 띄워 `/usage`만 치고 화면을 긁는다**(`_read_usage_screen`).
    모델 호출이 아니라 계정 조회라 **토큰 비용 0**
  - 계정별 수치라 **계정을 바꾸면 같이 바뀐다**
  - claude 본인 표기대로 *이 기기의 로컬 세션 기준 근사치* — 다른 기기·claude.ai 사용분 제외
  - 화면을 긁는 방식이라 claude가 레이아웃을 바꾸면 파싱이 깨진다 → 그땐 조용히 숨는다
- **세션 안내문 주입** — 새 패널마다 `--append-system-prompt`로 `HELM_NOTE`(`app.py:62`)를 붙인다.
  지금 담긴 내용은 "`SendUserFile` 쓰지 말고 `Invoke-Item`으로 띄워라" 하나 (아래 함정 11).
  - 한도 긁는 숨은 세션(`app.py:774`)엔 **일부러 안 붙인다** — `/usage`만 치고 죽어서 낭비
  - 인자에 따옴표·한글이 있어도 안 깨진다(왕복 278자 일치 실측). 늘려도 됨
- **Ctrl+1~9** 로 해당 탭을 지금 포커스된 슬롯에 올림
- **외형** — JetBrains Mono 웹폰트 자체 포함(`static/vendor/font`), 둥근 패널·그림자·
  얇은 스크롤바·bar 커서. 웹폰트는 **로드 완료 후에 터미널을 만들어야** 글자 폭을
  잘못 재지 않는다(`app.js` 맨 아래 `document.fonts.load(...).then(boot)`).

## 함정 (다음에 건드릴 때)
1. **환경변수 세탁 필수** — HELM을 claude 세션 안에서 실행하면
   `CLAUDE_CODE_CHILD_SESSION`이 상속돼 transcript 저장이 꺼지고, 그러면 세션 파일이 안 남아
   **최근 작업 목록이 영원히 안 채워진다**. `child_env()`가 이걸 지운다. 지우지 말 것.
2. **웹소켓은 서버가 닫아야 한다** — 클라이언트가 먼저 `ws.close()` 하면 종료 메시지가
   깨진 프레임(`Invalid frame header`)으로 나간다. `closePane()`은 DELETE만 보내고
   소켓은 서버 `push()`가 닫는다.
3. **패널은 웹소켓과 수명이 다름** — Pane은 서버에 남고 ws는 붙었다 떨어진다.
   재연결하면 `subscribe()`가 그동안의 출력(backlog)을 돌려준다.
4. 포트는 매 실행마다 랜덤(0번 바인딩). 고정 아님.
5. **바로가기(`.lnk`)를 저장소에 넣지 말 것** — 윈도우 `.lnk` 는 만든 PC의 계정 SID·
   호스트명·절대경로를 형식상 같이 담는다. 공개 저장소라 그대로 노출된다.
   `*.lnk` 는 gitignore 에 있다. 2026-08-31 에 이력에서도 지웠다(logs 참고).
5. **모델 이름 유효성은 시작 배너로 판별 못 한다** — claude는 아무 문자열이나 받아서 뜬다
   (`claude-nonexistent-9`도 정상 기동). 진짜 판별은 *배너에 예쁜 이름으로 바뀌었는가*다.
   유효하면 `claude-opus-4-8` → "Opus 4.8", 가짜면 입력값이 그대로 되울린다.
   config.json에 모델 추가한 뒤엔 이걸로 확인할 것.
6. **처음 여는 폴더는 신뢰 프롬프트가 뜬다** — 그 상태에서 모델 버튼을 누르면 슬래시 명령이
   프롬프트로 먹힌다. 앱 버그 아님(claude 정상 동작).
7. **빈 경로 함정** — `Path("").resolve()`는 서버 실행 폴더로 풀린다. 등록 목록에 없는
   폴더가 `last_project`에 저장돼 있으면 `<select>`의 value가 ""가 되고, 그 빈 값이
   새 세션(400)과 편집기(엉뚱한 폴더를 조용히 열기)로 흘러갔다. 지금은 세 군데서 막는다:
   `_safe()`가 빈 문자열 거부 / 부팅 시 select 값 안 먹으면 첫 항목으로 / 등록된 폴더일
   때만 `last_project` 저장.
8. **패널 ↔ 세션파일 짝짓기는 추측이다** — 새 세션은 ID를 미리 알 수 없어서
   `_session_file()`이 *패널이 뜬 뒤 시각의 assistant 응답이 든 jsonl*을 잡고 붙들어 둔다.
   HELM 밖에서 같은 폴더로 claude를 따로 돌리고 있으면 그쪽 파일을 잘못 잡을 수 있다.
   - ⚠ **mtime만 보면 안 된다** — claude는 새로 뜰 때 남의 세션 파일 끝에 `bridge-session`
     줄을 붙여 mtime을 건드린다. 한도 긁기 세션이 10분마다 뜨므로 실제로 걸린다.
     그래서 timestamp까지 본다(`_epoch`). 이 조건을 풀지 말 것.
9. **한도 긁기는 눈 감고 Enter를 치는 짓이다** — 신뢰 안 된 폴더에서 하면 그 Enter가
   "이 폴더를 신뢰함"을 눌러버린다(테스트 중 실제로 폴더 하나가 신뢰 처리됨).
   지금은 버퍼에 신뢰 프롬프트가 보이면 안 치고 포기하고, 긁는 폴더도 HELM 폴더로 고정했다.
   **긁는 폴더를 옮기지 말 것** — 새 폴더는 신뢰 프롬프트가 뜬다.
10. **모델 목록 갱신은 merge** — `MODELS_VERSION`을 올리면 config.json의 `models`에
   *없는 것만* 합쳐 넣는다. 통째로 덮으면 직접 추가한 모델이 날아간다.
11. **클라이언트 렌더링에 기대는 기능은 여기서 조용히 죽는다** — HELM은 CLI 화면을 그대로
   그리는 껍데기라, claude가 *클라이언트에게 그려달라고 넘기는* 것은 표시될 데가 없다.
   - 실제 사고: `SendUserFile`이 `4 files delivered to user`로 **성공을 반환**하는데
     화면엔 아무것도 안 뜬다 → claude는 보낸 줄 알고, 호윤은 못 본다(2026-08-16)
   - 지금은 `HELM_NOTE`로 `Invoke-Item`을 쓰게 돌려놨다. **셸로 여는 건 정상 동작**
     (HELM 안에서 실측: Photos 창 뜸)
   - 비슷한 기능이 또 나오면 같은 함정을 의심할 것. 권한·관리자 문제로 보이지만 아니다

## 점검 방법 (스크린샷 금지)
`python app.py --server 8791` 띄우고 playwright로 DOM/콘솔만 읽는다. 이미지 토큰 0.
패널 만들 때 `argv:['cmd','/k',...]`를 넘기면 claude 대신 cmd로 PTY 경로만 검증 가능.

검증한 것: claude TUI 렌더(로고·박스문자 정상), 한글 입출력 왕복, 분할 전환 시 내용 유지,
모델 전환(Opus 5 → Haiku 4.5 배너 확인), 탭 닫기, pageerror 0건.

## 채팅형 UI로 가려면 (조사 결과)
"CLI 화면 말고 대화형 카드 UI"는 `claude -p --output-format stream-json --input-format stream-json`
경로인데, **권한 확인창을 직접 붙이는 CLI 플래그가 이 버전엔 없다** (`--permission-prompt-tool` 부재,
`claude --help | grep -i permission`으로 확인). 남는 선택지는 셋:
1. 권한을 모드로만 통제(`dontAsk`/`bypassPermissions`)하고 확인창은 포기
2. **Claude Agent SDK**(`claude-agent-sdk`, Claude Code를 라이브러리로 감싼 별도 패키지)의
   `can_use_tool` 콜백으로 확인창을 직접 구현 — 문서: code.claude.com/docs/en/agent-sdk
   ※ claude-api 스킬 범위 밖이라 API 코드로 대체하면 안 됨
3. 지금처럼 터미널 유지

## 기록
- [2026-08-31](logs/2026-08-31-log.md) — 바로가기 `.lnk` 안의 SID·호스트명 발견 → 추적 해제 + 이력 제거
- [2026-08-22](logs/2026-08-22-log.md) — Ctrl+V 안 되던 것: 클립보드 경로 신설(WebView2 권한 벽 → 서버가 직접 읽음)
- [2026-08-17](logs/2026-08-17-log.md) — 본체로 정리 / "파일 안 열림"의 진짜 원인=SendUserFile, `--append-system-prompt` 주입
- [2026-08-14](logs/2026-08-14-log.md) — 개명 ClaudeDock → HELM, 로고·아이콘 신규 / 컨텍스트 사용량 바
- [2026-08-13](logs/2026-08-13-log.md) — 채팅형 프로토타입 실사용 버그 4개 (창 번쩍임·세션 전환·비용)
