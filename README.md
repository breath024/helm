# HELM

한 창 안에서 Claude Code 세션 여러 개를 탭과 분할로 다루는 윈도우 데스크톱 앱.

## 왜 만들었나

Claude Code를 여러 프로젝트에 동시에 띄우면 콘솔 창이 그만큼 늘어난다.
창을 찾아다니는 게 번거로워서 한 창에 모았다.

앞서 만든 `agent_console.py`(별도 저장소)는 `conhost.exe`로 진짜 콘솔 창을 띄운 뒤
Win32로 오너를 지정하고 테두리를 벗겨 겹쳐놓는 방식이었다. 이게 세 가지로 걸렸다.

- 창을 `EnumWindows`로 찾을 때까지 기다려야 해서 늦게 떴다
- conhost가 포커스마다 테두리를 되붙여서 앱과 계속 싸웠다
- 그 전부가 tkinter 단일 스레드에 얹혀 있어서 멈췄다

HELM은 콘솔 창을 아예 만들지 않는다. ConPTY(`pywinpty`)로 claude를 직접 물고
화면은 xterm.js가 그린다. 찾을 창도, 벗길 테두리도, 계산할 좌표도 없다.

## 구성

| | |
|---|---|
| 백엔드 | Python + pywinpty (ConPTY) |
| 프론트 | xterm.js, CodeMirror (vendor 폴더에 동봉) |
| 창 | pywebview |

`app.py`가 본체다. `chat_proto.py`(터미널 없이 Claude 앱처럼 쓰는 채팅형)도
같은 폴더에 있지만 써보니 불편해서 본체 쪽으로 정리했다.
둘은 `config.json`(폴더 목록)만 공유하고 나머지는 별개로 돌아간다.

## 실행

`HELM.lnk` 실행. pythonw로 떠서 콘솔창은 안 뜬다.

창 없이 서버만 띄우려면 `python app.py --server 8791` 후 브라우저로 접속.

## 아직 안 된 것

- 분할 칸 드래그 크기 조절 (지금은 균등 분할 고정)
- 폴더 목록 인앱 추가·삭제 (지금은 `config.json` 직접 편집)
- 앱 재시작 시 세션 자동 복구
- 이미지 미리보기 패널 — 지금은 별도 뷰어 창이 뜬다
