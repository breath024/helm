# -*- coding: utf-8 -*-
"""hcom 이 에이전트 창을 열 때 부르는 다리 — 창은 안 열고 HELM 에 "이거 열어줘"만 남긴다.

HELM 이 에이전트를 부를 때 HCOM_TERMINAL 을 이 스크립트로 바꿔 hcom 을 실행한다(그 실행에만).
hcom 은 원래 새 터미널 창에서 launch 스크립트(.ps1)를 돌리는데, 여기선 그 경로를
~/.hcom/helm_spool/*.json 에 적고 바로 끝낸다. HELM 채팅 패널이 그걸 집어
HELM 안의 터미널 탭에서 스크립트를 돌린다.

인자: {script} {instance_name} {tool} {cwd}
  ★ {script} 는 반드시 있어야 한다. hcom 은 {script} 없는 사용자 명령을 아예 거부하고
    (Error: Custom terminal command must contain {script}) 조용히 기본 동작으로 빠진다.
    그러면 에이전트가 창 없이 떠서 화면에 아무것도 안 보인다. 2026-09-15 에 여기서 한 번 밟음.
  cwd 는 공백이 들어갈 수 있어 맨 뒤에 두고 남은 인자를 다시 붙인다.
"""
import os
import sys
import json
import time
import uuid
from pathlib import Path

HCOM_DIR = Path(os.environ.get("HCOM_DIR") or Path.home() / ".hcom")
SPOOL = HCOM_DIR / "helm_spool"
LAUNCH = HCOM_DIR / ".tmp" / "launch"


def log(msg):
    try:
        SPOOL.mkdir(parents=True, exist_ok=True)
        with open(SPOOL / "helm_open.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except Exception:
        pass


def newest_script(tool):
    """{script} 가 안 왔을 때의 대비책 — 방금 생긴 launch 스크립트를 찾는다.
    메인 스크립트에는 'hcom ... pty <tool>' 줄이 있다."""
    best, best_at = None, 0.0
    try:
        files = list(LAUNCH.glob("*.ps1"))
    except OSError:
        return None
    for f in files:
        try:
            at = f.stat().st_mtime
        except OSError:
            continue
        if time.time() - at > 30 or at <= best_at:
            continue
        try:
            body = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if " pty " in body:
            best, best_at = f, at
    return best


def cwd_from(script):
    """스크립트 안의 Set-Location 'X' 에서 시작 폴더를 읽는다."""
    try:
        # hcom 은 .ps1 을 BOM 붙여 쓴다 — utf-8 로 읽으면 첫 줄 앞에 BOM 이 남아
        # startswith 가 빗나간다 (utf-8-sig 로 읽어야 한다)
        body = Path(script).read_text(encoding="utf-8-sig", errors="replace")
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("Set-Location") and "'" in line:
                return line[line.index("'") + 1:line.rindex("'")]
    except Exception:
        pass
    return ""


def main():
    a = sys.argv[1:]
    script = a[0] if len(a) > 0 else ""
    name = a[1] if len(a) > 1 else ""
    tool = a[2] if len(a) > 2 else ""
    cwd = " ".join(a[3:]) if len(a) > 3 else ""

    # 치환이 안 됐거나(자리표시자 그대로) 없는 파일이면 직접 찾는다
    if not script or "{" in script or not Path(script).is_file():
        found = newest_script(tool)
        log("script 인자를 못 씀(" + repr(script) + ") → 최근 파일로: " + str(found))
        if not found:
            log("launch 스크립트를 못 찾음 name=" + name + " tool=" + tool)
            return 1
        script = str(found)

    if not cwd or "{" in cwd:
        cwd = cwd_from(script)

    rec = {"script": script, "name": name, "tool": tool, "cwd": cwd, "at": time.time()}
    SPOOL.mkdir(parents=True, exist_ok=True)
    tmp = SPOOL / (uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, tmp.with_suffix(".json"))     # 반쯤 쓰인 파일을 HELM 이 집지 않게
    except OSError as ex:
        log("spool 쓰기 실패: " + str(ex))
        return 1

    log("넘김 name=" + name + " tool=" + tool + " script=" + script + " cwd=" + cwd)
    try:
        if sys.stdout:
            print(name)          # hcom 이 첫 줄을 창 id 로 쓴다
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
