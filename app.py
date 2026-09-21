# -*- coding: utf-8 -*-
"""HELM — 한 창 안에서 Claude Code 세션 여러 개를 탭·분할로 다루는 데스크톱 앱.

기존 agent_console.py와 다른 점: conhost 창을 밖에서 조종하지 않는다.
ConPTY로 claude를 직접 물고 화면은 xterm.js가 그리므로, 창을 찾거나
테두리를 벗기거나 좌표를 계산할 일이 없다.
"""
import io
import os
import re
import json
import time
import queue
import shutil
import sys
import sqlite3
import ctypes
import ctypes.wintypes
import threading
import subprocess
from pathlib import Path

import winpty
from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
ICON = HERE / "helm.ico"          # 창·작업표시줄 아이콘 (make_icon.py로 굽는다)
CONFIG = HERE / "config.json"
CACHE = HERE / ".session_cache.json"
CLAUDE_HOME = Path.home() / ".claude" / "projects"
LEGACY_PROJECTS = Path.home() / "Desktop" / "ClaudeCode" / "projects.json"

# 실제로 띄워보고 확인한 세부버전들(2026-08-13 기준, claude v2.1.229).
# effort=False 인 모델은 강도 개념이 없다(배너에 effort가 안 뜬다).
# 새 모델이 나오면 config.json 의 models 배열에 한 줄 추가하면 된다 — 코드 수정 불필요.
MODELS = [
    {"id": "claude-fable-5",    "label": "Fable 5",    "family": "Fable",  "effort": True},
    {"id": "claude-opus-5",     "label": "Opus 5",     "family": "Opus",   "effort": True},
    {"id": "claude-opus-4-8",   "label": "Opus 4.8",   "family": "Opus",   "effort": True},
    {"id": "claude-opus-4-5",   "label": "Opus 4.5",   "family": "Opus",   "effort": True},
    {"id": "claude-opus-4-1",   "label": "Opus 4.1",   "family": "Opus",   "effort": True},
    {"id": "claude-opus-4",     "label": "Opus 4",     "family": "Opus",   "effort": True},
    {"id": "claude-sonnet-5",   "label": "Sonnet 5",   "family": "Sonnet", "effort": True},
    {"id": "claude-sonnet-4-5", "label": "Sonnet 4.5", "family": "Sonnet", "effort": False},
    {"id": "claude-sonnet-4",   "label": "Sonnet 4",   "family": "Sonnet", "effort": False},
    {"id": "claude-3-7-sonnet", "label": "Sonnet 3.7", "family": "Sonnet", "effort": False},
    {"id": "claude-3-5-sonnet", "label": "Sonnet 3.5", "family": "Sonnet", "effort": False},
    {"id": "claude-haiku-4-5",  "label": "Haiku 4.5",  "family": "Haiku",  "effort": False},
    {"id": "claude-3-5-haiku",  "label": "Haiku 3.5",  "family": "Haiku",  "effort": False},
]
MODELS_VERSION = 2      # 이 숫자가 바뀌면 config.json의 models에 새 항목을 합쳐 넣는다
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# claude --permission-mode 가 받는 값들. 라벨은 사람이 읽는 용도.
PERM_MODES = [
    {"id": "manual",            "label": "매번 확인",      "warn": False},
    {"id": "acceptEdits",       "label": "파일수정 승인",  "warn": False},
    {"id": "plan",              "label": "계획만",         "warn": False},
    {"id": "auto",              "label": "자동",           "warn": False},
    {"id": "dontAsk",           "label": "안 물어봄",      "warn": True},
    {"id": "bypassPermissions", "label": "전체 우회",      "warn": True},
]

# 세션마다 --append-system-prompt 로 넣는 안내.
# SendUserFile은 파일을 '클라이언트'에 넘겨 카드로 그리게 하는 도구인데, HELM 화면은
# xterm.js라 그릴 데가 없다. 그런데도 도구는 "delivered"라고 답해서 claude는 보낸 줄 안다
# (실제 사고: 2026-08-16 이미지 4장을 보냈다고 했는데 호윤 화면엔 아무것도 안 떴음).
# 셸로 여는 건 정상 동작하므로 그쪽으로 돌린다.
HELM_NOTE = (
    "이 세션은 HELM 안에서 돌고 있다. 화면이 xterm.js 터미널이라 "
    "SendUserFile 도구는 여기서 무용지물이다 — 도구는 'delivered'라고 답하지만 "
    "사용자 화면에는 아무것도 표시되지 않는다. 파일이나 이미지를 사용자에게 보여줘야 할 때는 "
    "SendUserFile을 쓰지 말고 셸로 기본 앱에 띄울 것: "
    "powershell -c \"Invoke-Item '<경로>'\" (여러 장이면 한 장으로 붙여서 한 번에). "
    "띄운 뒤에는 경로도 함께 적어줘서 사용자가 직접 찾아갈 수 있게 한다."
)

app = Flask(__name__, static_folder=None)
sock = Sock(app)


# ============================== 설정 ==============================
_cfg = None


def load_config():
    global _cfg
    if _cfg is not None:
        return _cfg
    if CONFIG.exists():
        try:
            _cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            changed = False
            for key, default in (("models", MODELS), ("efforts", EFFORTS),
                                 ("last_effort", "high"),
                                 ("perm_modes", PERM_MODES),
                                 ("last_perm_mode", "manual")):
                if key not in _cfg:            # 예전 config에 새 항목 채워넣기
                    _cfg[key] = default
                    changed = True
            # 새 모델이 추가된 버전이면 없는 것만 합쳐 넣는다.
            # (통째로 덮으면 직접 추가/삭제한 게 날아가므로 merge)
            if _cfg.get("models_version") != MODELS_VERSION:
                have = {m["id"] for m in _cfg["models"]}
                for m in MODELS:
                    if m["id"] not in have:
                        _cfg["models"].append(m)
                order = {m["id"]: i for i, m in enumerate(MODELS)}
                _cfg["models"].sort(key=lambda m: order.get(m["id"], 999))
                _cfg["models_version"] = MODELS_VERSION
                changed = True

            ids = [m["id"] for m in _cfg["models"]]
            if _cfg.get("last_model") not in ids:
                # 예전에 저장된 별칭("opus")을 세부버전으로 올려준다
                alias = (_cfg.get("last_model") or "").lower()
                _cfg["last_model"] = next(
                    (i for i in ids if alias and alias in i.lower()), ids[0])
                changed = True
            if changed:
                save_config(_cfg)
            return _cfg
        except Exception:
            pass
    projects = []
    if LEGACY_PROJECTS.exists():  # 예전 앱에 등록해둔 폴더를 그대로 물려받는다
        try:
            projects = json.loads(LEGACY_PROJECTS.read_text(encoding="utf-8"))
        except Exception:
            projects = []
    if not projects:
        projects = [{"name": "홈", "path": str(Path.home())}]
    cfg = {"projects": projects, "models": MODELS, "models_version": MODELS_VERSION,
           "efforts": EFFORTS, "perm_modes": PERM_MODES,
           "last_project": projects[0]["path"], "last_model": "claude-opus-5",
           "last_effort": "high", "last_perm_mode": "manual"}
    save_config(cfg)
    return cfg


def save_config(cfg):
    global _cfg
    _cfg = cfg
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================== 세션 색인 ==============================
# ~/.claude/projects/<인코딩된 경로>/<session-uuid>.jsonl
# 파일명이 곧 --resume 에 넣는 세션 ID다.

# 캐시는 파일의 mtime·size 가 그대로면 다시 안 읽는다. 그래서 읽는 항목을 늘려도
# 옛 세션은 영영 갱신되지 않는다 — 파일이 안 변하니까. 항목을 늘릴 땐 이 숫자를 올려서
# 한 번 다시 훑게 한다.
_CACHE_VER = 2

_cache_lock = threading.Lock()
_cache = {}
if CACHE.exists():
    try:
        _cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}


def _flush_cache():
    try:
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _text_of(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _head_scan(path, limit=60):
    """앞부분만 읽어 제목(첫 사용자 발화)과 cwd를 얻는다. 5MB짜리도 순식간."""
    title, cwd = "", ""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= limit:
                    break
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not cwd and d.get("cwd"):
                    cwd = d["cwd"]
                if not title and d.get("type") == "user":
                    t = _text_of(d.get("message")).strip()
                    # 훅/명령 출력으로 시작하는 세션은 제목으로 쓰기에 부적절
                    if t and not t.startswith("<"):
                        title = " ".join(t.split())[:80]
                if title and cwd:
                    break
    except Exception:
        pass
    return title, cwd


def _tail_scan(path, size, window=65536):
    """끝부분만 읽어 마지막 모델·마지막 시각과 세션이 갈라졌는지를 얻는다.

    세션이 갈라지면 CLI 가 옛 파일 끝에 자취를 남긴다:
        {"type":"continued-in", "continuedInSessionId":"<새 세션 id>"}
    갈라진 세션은 앞부분을 통째로 복사해가므로 제목·프로젝트가 원본과 똑같다.
    이걸 안 보면 목록에 구분 불가능한 두 줄이 뜨고, 죽은 쪽을 골라 이어받으면
    작업이 사라진 것처럼 보인다(2026-09-21 실제로 두 번 겪음).

    `after` 는 그 자취 뒤에 실제 대화가 더 쌓였는지다.
      0  — 갈라진 뒤 아무것도 안 썼다. 버려진 가지다
      >0 — 양쪽 다 계속 썼다. 진짜로 갈라진 것이라 둘 다 살려둬야 한다
    자취가 창(window) 밖으로 밀려났으면 그만큼 뒤에 내용이 많다는 뜻이라,
    못 찾은 경우는 자연히 "갈라지지 않음"으로 떨어진다 — 그래도 결론은 맞다.
    """
    model, ts, cont, after = "", "", "", 0
    try:
        with io.open(path, "rb") as f:
            if size > window:
                f.seek(size - window)
                f.readline()  # 잘린 줄 버리기
            for line in f:
                try:
                    d = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if d.get("timestamp"):
                    ts = d["timestamp"]
                m = (d.get("message") or {}).get("model")
                if m:
                    model = m
                t = d.get("type")
                if t == "continued-in":
                    cont = d.get("continuedInSessionId") or ""
                    after = 0            # 자취가 여러 번이면 마지막 것 기준
                elif cont and t in ("user", "assistant"):
                    after += 1
    except Exception:
        pass
    return model, ts, cont, after


def _pretty_project(cwd, fallback):
    """등록해둔 폴더면 그 이름으로, 아니면 폴더명으로 보여준다."""
    if not cwd:
        return fallback
    norm = os.path.normcase(os.path.normpath(cwd))
    for p in load_config().get("projects", []):
        if os.path.normcase(os.path.normpath(p["path"])) == norm:
            return p["name"]
    if norm == os.path.normcase(str(Path.home())):
        return "홈"
    return Path(cwd).name or fallback


def _model_takes_effort(mid):
    for m in load_config().get("models", MODELS):
        if m["id"] == mid:
            return bool(m.get("effort"))
    return True          # 모르는(직접 추가한) 모델은 일단 허용


def _pretty_model(m):
    m = (m or "").strip()
    if not m or m.startswith("<"):     # <synthetic> 같은 내부 표기는 버린다
        return ""
    for known in load_config().get("models", MODELS):
        if known["id"] == m:
            return known["label"]
    return m.replace("claude-", "").split("-2")[0]


def scan_sessions(limit=60):
    out = []
    if not CLAUDE_HOME.exists():
        return out
    files = []
    for d in CLAUDE_HOME.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, f))
    files.sort(reverse=True)

    dirty = False
    twins = set()        # 진짜로 갈라진 세션들 — 양쪽 다 목록에 남기되 표시해준다
    with _cache_lock:
        for mtime, size, f in files:
            if len(out) >= limit:
                break
            key = str(f)
            hit = _cache.get(key)
            if (not hit or hit.get("mtime") != mtime or hit.get("size") != size
                    or hit.get("v") != _CACHE_VER):
                title, cwd = _head_scan(f)
                model, ts, cont, after = _tail_scan(f, size)
                hit = {"mtime": mtime, "size": size, "title": title,
                       "cwd": cwd, "model": model, "ts": ts,
                       "cont": cont, "after": after, "v": _CACHE_VER}
                _cache[key] = hit
                dirty = True
            if not hit["title"]:
                continue          # 사용자 발화가 없는 세션(열자마자 닫은 것)은 목록에서 뺀다
            cont = hit.get("cont") or ""
            if cont and not hit.get("after"):
                continue          # 이어받은 세션한테 자리를 넘기고 끝난 가지다 (_tail_scan 참고)
            if cont:
                twins.add(f.stem)
                twins.add(cont)
            out.append({
                "id": f.stem,
                "title": hit["title"],
                "cwd": hit["cwd"],
                "project": _pretty_project(hit["cwd"], f.parent.name),
                "model": _pretty_model(hit["model"]),
                "mtime": mtime,
                "size": size,
            })
        if dirty:
            _flush_cache()
    # 갈라진 세션은 앞부분을 복사해가니 제목도 프로젝트도 똑같다. 목록에서 구분이 안 되는
    # 게 문제의 본체이므로, 마커를 못 본 경우까지 잡으려면 증상 쪽을 본다.
    # (갈라진 뒤에도 한참 더 쓴 세션은 마커가 _tail_scan 의 창 밖으로 밀려나 안 잡힌다)
    seen = {}
    for row in out:
        seen.setdefault((row["title"], row["project"]), []).append(row)
    for group in seen.values():
        if len(group) > 1:
            for row in group:
                row["branched"] = True
    for row in out:
        if row["id"] in twins:
            row["branched"] = True
    return out


# ============================== 파일 (코드 편집기) ==============================
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
             ".vscode", "dist", "build", ".next", ".pytest_cache"}
TEXT_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".htm", ".css",
            ".md", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".ps1",
            ".bat", ".c", ".h", ".cpp", ".cs", ".java", ".go", ".rs", ".sql",
            ".xml", ".env", ".gitignore", ".csv", ".log"}
MAX_EDIT = 2_000_000        # 2MB 넘는 파일은 편집기로 안 연다


def _roots():
    r = [Path(p["path"]).resolve() for p in load_config().get("projects", [])]
    r.append(Path.home().resolve())
    return r


def _safe(path):
    """등록된 폴더(또는 홈) 안쪽인지 확인. 바깥이면 None.
    빈 문자열은 Path('').resolve()가 서버 실행 폴더로 풀려버리므로 먼저 막는다."""
    if not path or not str(path).strip():
        return None
    try:
        p = Path(path).resolve()
    except Exception:
        return None
    for root in _roots():
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    return None


def _is_texty(p: Path):
    return p.suffix.lower() in TEXT_EXT or p.name in (".gitignore", "CLAUDE.md", "Makefile")


@app.route("/api/tree")
def tree():
    p = _safe(request.args.get("path", ""))
    if not p or not p.is_dir():
        return jsonify({"error": "열 수 없는 경로"}), 400
    dirs, files = [], []
    try:
        for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if c.name.startswith(".") and c.name not in (".gitignore", ".env"):
                continue
            if c.is_dir():
                if c.name not in SKIP_DIRS:
                    dirs.append({"name": c.name, "path": str(c), "dir": True})
            elif _is_texty(c):
                files.append({"name": c.name, "path": str(c), "dir": False})
    except PermissionError:
        return jsonify({"error": "권한 없음"}), 403
    parent = str(p.parent) if _safe(str(p.parent)) and p.parent != p else None
    return jsonify({"path": str(p), "parent": parent, "entries": dirs + files})


@app.route("/api/file", methods=["GET"])
def read_file():
    p = _safe(request.args.get("path", ""))
    if not p or not p.is_file():
        return jsonify({"error": "없는 파일"}), 400
    if p.stat().st_size > MAX_EDIT:
        return jsonify({"error": "너무 큰 파일(2MB 초과)"}), 400
    try:
        return jsonify({"path": str(p), "name": p.name,
                        "content": p.read_text(encoding="utf-8")})
    except UnicodeDecodeError:
        return jsonify({"error": "텍스트 파일이 아님"}), 400


@app.route("/api/file", methods=["POST"])
def write_file():
    body = request.get_json(force=True)
    p = _safe(body.get("path", ""))
    if not p:
        return jsonify({"error": "저장할 수 없는 경로"}), 400
    if p.exists() and not p.is_file():
        return jsonify({"error": "파일이 아님"}), 400
    try:
        p.write_text(body.get("content", ""), encoding="utf-8", newline="")
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    return jsonify({"ok": True, "saved": time.strftime("%H:%M:%S")})


# ============================== PTY 패널 ==============================
# HELM 자체를 claude 세션 안에서 실행하면 자식 세션 표식이 상속돼
# transcript 저장이 꺼진다 → 세션 파일이 안 남아 '최근 작업'이 비어버린다.
# 여기서 띄우는 건 항상 독립 세션이어야 하므로 표식을 지운다.
_DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
             "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_PID", "CLAUDE_EFFORT")


def child_env():
    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
    env["TERM"] = "xterm-256color"
    env["FORCE_COLOR"] = "3"
    return env



class Pane:
    """claude 프로세스 하나 = 패널 하나. 웹소켓이 끊겨도 살아있다."""

    MAX_BUF = 400_000

    def __init__(self, pane_id, cwd, model, resume=None, cols=120, rows=30,
                 argv=None, effort=None, perm=None, agent=False):
        self.id = pane_id
        self.agent = agent         # hcom 에이전트(codex 등) 탭 — claude 세션이 아니다
        self.cwd = cwd
        self.model = model
        self.effort = effort
        self.perm = perm
        self.resume = resume
        self.started = time.time()
        self.session_file = None   # 컨텍스트 사용량을 읽을 세션 jsonl (뜬 뒤에 찾는다)
        self.buf = []          # 탭 전환/재연결 시 되돌려줄 출력 기록
        self.buflen = 0
        self.subs = []         # 살아있는 웹소켓들의 큐
        self.lock = threading.Lock()
        self.alive = True
        self.exit_note = ""

        if not argv:                      # argv 직접 지정은 점검용 통로
            argv = ["claude", "--append-system-prompt", HELM_NOTE]
            if resume:
                argv += ["--resume", resume]
            if model:
                argv += ["--model", model]
            if effort:
                argv += ["--effort", effort]
            if perm:
                argv += ["--permission-mode", perm]
                # bypassPermissions는 이 짝 플래그가 있어야 열린다
                if perm == "bypassPermissions":
                    argv += ["--allow-dangerously-skip-permissions"]

        self.proc = winpty.PtyProcess.spawn(
            argv, cwd=cwd, dimensions=(rows, cols), env=child_env(),
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                data = self.proc.read(8192)
            except EOFError:
                break
            except Exception:
                break
            if not data:
                if not self.proc.isalive():
                    break
                continue
            self._emit(data)
        self.alive = False
        code = None
        try:
            code = self.proc.exitstatus
        except Exception:
            pass
        self.exit_note = f"\r\n\x1b[90m— 세션 종료 (코드 {code}) —\x1b[0m\r\n"
        self._emit(self.exit_note)

    def _emit(self, data):
        with self.lock:
            self.buf.append(data)
            self.buflen += len(data)
            while self.buflen > self.MAX_BUF and len(self.buf) > 1:
                self.buflen -= len(self.buf.pop(0))
            for q in list(self.subs):
                q.put(data)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            backlog = "".join(self.buf)
            self.subs.append(q)
        return q, backlog

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def write(self, data):
        try:
            self.proc.write(data)
        except Exception:
            pass

    def resize(self, cols, rows):
        try:
            self.proc.setwinsize(max(rows, 5), max(cols, 20))
        except Exception:
            pass

    def set_model(self, model=None, effort=None):
        """돌고 있는 세션은 슬래시 명령으로 바꾼다.
        두 개를 연달아 붙여 쓰면 앞 명령이 처리되기 전에 섞이므로 사이를 띄운다."""
        def go():
            if model:
                self.model = model
                self.write(f"/model {model}\r")
            if effort:
                if model:
                    time.sleep(1.2)
                self.effort = effort
                self.write(f"/effort {effort}\r")
        threading.Thread(target=go, daemon=True).start()

    def close(self):
        self.alive = False
        try:
            self.proc.terminate(force=True)
        except Exception:
            pass


PANES = {}
_pane_seq = 0
_pane_lock = threading.Lock()


# ============================== 컨텍스트 사용량 ==============================
# CLI는 "지금 몇 토큰 물고 있나"를 밖으로 안 내준다. 대신 세션 jsonl의 마지막
# assistant 메시지 usage가 곧 그 시점의 컨텍스트 크기다.
#   input + 캐시읽기 + 캐시생성 + output
# 압축(compact)이 일어나면 다음 응답의 usage가 알아서 작아지므로 따로 처리 안 해도 된다.
DEFAULT_CONTEXT = 200_000
CONTEXT_LIMITS = {}          # 모델별 예외가 생기면 {"모델id": 토큰수}


def _proj_dir(cwd):
    """~/.claude/projects 는 cwd의 영숫자 아닌 글자를 전부 '-'로 바꿔 폴더명을 만든다."""
    return CLAUDE_HOME / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _context_of(path, window=262144):
    """파일 끝만 읽어 마지막 assistant usage를 뽑는다."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    last = None
    try:
        with io.open(path, "rb") as f:
            if size > window:
                f.seek(size - window)
                f.readline()          # 잘린 줄 버리기
            for line in f:
                try:
                    d = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                # 서브에이전트(sidechain)의 usage는 이 대화의 컨텍스트가 아니다
                if d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                msg = d.get("message") or {}
                if msg.get("usage"):
                    last = (msg["usage"], msg.get("model"), d.get("timestamp"))
    except Exception:
        return None
    if not last:
        return None
    u, model, ts = last
    tok = ((u.get("input_tokens") or 0)
           + (u.get("cache_read_input_tokens") or 0)
           + (u.get("cache_creation_input_tokens") or 0)
           + (u.get("output_tokens") or 0))
    return {"tokens": tok, "model": model, "ts": ts}


def _epoch(iso):
    """'2026-08-14T09:45:11.937Z' → epoch 초. 못 읽으면 0."""
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(
            (iso or "").replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return 0


def _session_file(p):
    """이 패널이 쓰고 있는 세션 파일. 한 번 찾으면 붙잡아 둔다.

    새 세션은 ID를 미리 알 수 없으니 패널이 뜬 뒤에 갱신된 파일부터 살펴본다.
    다만 mtime만 믿으면 안 된다 — claude는 새로 뜰 때 남의 세션 파일 끝에
    `bridge-session` 줄을 붙여 mtime을 건드린다(한도 긁기 세션이 그렇다).
    그래서 **패널이 뜬 뒤에 찍힌 assistant 응답이 실제로 들어있는 파일**만 잡는다.
    같은 폴더에 패널이 여럿이면 남이 이미 잡은 파일은 건너뛴다.
    """
    if p.session_file:
        return p.session_file if p.session_file.exists() else None
    d = _proj_dir(p.cwd)
    taken = {str(o.session_file) for o in PANES.values()
             if o is not p and o.session_file}
    cands = []
    if d.is_dir():
        for f in d.glob("*.jsonl"):
            if str(f) in taken:
                continue
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m < p.started - 2:     # 패널보다 먼저 끝난 파일은 이 패널 것이 아니다
                continue
            cands.append((m, f))
    for _, f in sorted(cands, reverse=True)[:3]:
        info = _context_of(f)
        if info and _epoch(info["ts"]) >= p.started - 2:
            p.session_file = f
            return f
    if p.resume:                      # 아직 아무것도 안 쓴 이어받기 세션
        f = d / f"{p.resume}.jsonl"
        if f.exists():
            return f
    return None


# ============================== API ==============================
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


@app.route("/api/bootstrap")
def bootstrap():
    cfg = load_config()
    return jsonify({
        "projects": cfg["projects"],
        "models": cfg.get("models", MODELS),
        "efforts": cfg.get("efforts", EFFORTS),
        "perm_modes": cfg.get("perm_modes", PERM_MODES),
        "last_project": cfg.get("last_project"),
        "last_model": cfg.get("last_model", "claude-opus-5"),
        "last_effort": cfg.get("last_effort", "high"),
        "last_perm_mode": cfg.get("last_perm_mode", "manual"),
    })


@app.route("/api/sessions")
def sessions():
    return jsonify(scan_sessions(int(request.args.get("limit", 60))))


@app.route("/api/panes", methods=["POST"])
def create_pane():
    global _pane_seq
    body = request.get_json(force=True)
    cwd = body.get("cwd") or str(Path.home())
    model = body.get("model") or "claude-opus-5"
    effort = body.get("effort")
    perm = body.get("perm")
    resume = body.get("resume")
    title = body.get("title") or Path(cwd).name
    if not _model_takes_effort(model):
        effort = None
    if perm not in [m["id"] for m in load_config().get("perm_modes", PERM_MODES)]:
        perm = None

    if not Path(cwd).is_dir():
        return jsonify({"error": f"폴더가 없어: {cwd}"}), 400

    # hcom 에이전트 탭: 임의 argv 대신 hcom launch 폴더 안의 .ps1 만 받아 서버가 argv 를 만든다
    argv, agent = body.get("argv"), False
    if body.get("hcom_script"):
        sp = Path(body["hcom_script"])
        try:
            ok = (sp.suffix.lower() == ".ps1" and sp.is_file()
                  and sp.resolve().parent == (HCOM_DIR / ".tmp" / "launch").resolve())
        except OSError:
            ok = False
        if not ok:
            return jsonify({"error": f"hcom 실행 스크립트가 아니야: {sp}"}), 400
        argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(sp)]
        agent = True
        effort = body.get("effort")          # codex 강도는 claude 모델표와 무관하다
        perm = None

    with _pane_lock:
        _pane_seq += 1
        pid = f"p{_pane_seq}"
    try:
        PANES[pid] = Pane(pid, cwd, model, resume,
                          cols=int(body.get("cols", 120)), rows=int(body.get("rows", 30)),
                          argv=argv, effort=effort, perm=perm, agent=agent)
    except Exception as ex:
        return jsonify({"error": f"실행 실패: {ex}"}), 500

    if agent:        # 에이전트 탭은 claude 새 세션 기본값(last_model 등)을 건드리지 않는다
        return jsonify({"id": pid, "title": title, "cwd": cwd, "model": model,
                        "effort": effort, "perm": None, "resumed": False, "agent": True})

    cfg = load_config()
    # 등록된 폴더일 때만 기억한다 (아니면 다음 부팅 때 select에 없는 값이 들어간다)
    if any(os.path.normcase(os.path.normpath(p["path"])) ==
           os.path.normcase(os.path.normpath(cwd)) for p in cfg.get("projects", [])):
        cfg["last_project"] = cwd
    cfg["last_model"] = model
    if effort:
        cfg["last_effort"] = effort
    if perm:
        cfg["last_perm_mode"] = perm
    save_config(cfg)

    return jsonify({"id": pid, "title": title, "cwd": cwd, "model": model,
                    "effort": effort, "perm": perm, "resumed": bool(resume)})


@app.route("/api/panes/<pid>", methods=["DELETE"])
def kill_pane(pid):
    p = PANES.pop(pid, None)
    if p:
        p.close()
    return jsonify({"ok": True})


@app.route("/api/panes/<pid>/model", methods=["POST"])
def switch_model(pid):
    p = PANES.get(pid)
    if not p:
        return jsonify({"error": "없는 패널"}), 404
    body = request.get_json(force=True)
    model = body.get("model")
    effort = body.get("effort")
    if effort and not _model_takes_effort(model or p.model):
        effort = None
    p.set_model(model, effort)
    return jsonify({"ok": True, "model": p.model, "effort": effort})


# ============================== hcom (멀티 에이전트 채팅) ==============================
# hcom(hook-comms)이 ~/.hcom/hcom.db 에 이벤트를 쌓는다. 우리는 그걸 읽어 한 패널에 뿌리고,
# 보내는 건 hcom CLI 에 맡긴다 — DB 에 직접 쓰면 훅 전달 규약을 우리가 재구현해야 한다.
#
#   events.type = life(입퇴장) / status(툴 실행) / message(대화)
#   ⚠ status 의 detail 에는 에이전트가 실행한 명령어 전문이 들어간다 — 화면에 뿌리지 않는다.
#   보내려면 identity 가 있어야 한다("hcom send" 는 이름 없이는 거부). 사람 자리는 bigboss.
HCOM_DIR = Path(os.environ.get("HCOM_DIR") or Path.home() / ".hcom")
HCOM_DB = HCOM_DIR / "hcom.db"
HCOM_ME = "bigboss"
_HCOM_LIVE = ("active", "listening", "blocked")          # hcom 이 권위자로 취급하는 이름이라 사람 자리로 쓴다


def _hcom_exe():
    return shutil.which("hcom") or (
        str(Path.home() / ".local" / "bin" / "hcom.exe")
        if (Path.home() / ".local" / "bin" / "hcom.exe").exists() else None)


def _hcom_run(args, timeout=20):
    exe = _hcom_exe()
    if not exe:
        return 1, "hcom 이 설치돼 있지 않아"
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, env=child_env(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as ex:
        return 1, str(ex)


def _hcom_conn():
    """읽기 전용으로 연다. hcom 이 쓰는 중일 수 있어 busy_timeout 을 준다."""
    con = sqlite3.connect(f"file:{HCOM_DB.as_posix()}?mode=ro", uri=True, timeout=3)
    con.execute("PRAGMA busy_timeout=3000")
    return con


# hcom hooks 출력의 라벨 → 실행 이름. 훅이 붙은 도구만 불러올 수 있다
# (훅이 없으면 떠도 메시지를 못 받는다). 설치만 된 도구는 꺼진 칩으로 보여준다.
_HCOM_TOOLS = [("Claude", "claude"), ("Gemini", "gemini"), ("Codex", "codex"),
               ("OpenCode", "opencode"), ("Kilo Code", "kilo"), ("Pi", "pi"),
               ("Oh My Pi", "omp"), ("Antigravity", "antigravity"), ("Cursor", "cursor"),
               ("Kimi", "kimi"), ("Copilot", "copilot")]
_hcom_tools_cache = {"at": 0.0, "val": []}


def _hcom_tools():
    """[{tool, ready}] — 30초 캐시. 폴링마다 CLI 를 태우지 않으려고."""
    if time.time() - _hcom_tools_cache["at"] < 30:
        return _hcom_tools_cache["val"]
    code, out = _hcom_run(["hooks"])
    hooked = set()
    for line in out.splitlines():
        m = re.match(r"\s*([^:]+):\s+installed", line)   # "not installed" 은 안 걸린다
        if m:
            hooked.add(m.group(1).strip())
    choices = load_config().get("hcom_choice", {})
    val = []
    for label, tool in _HCOM_TOOLS:
        if label not in hooked and not shutil.which(tool):
            continue
        models = _codex_models() if tool == "codex" else []
        val.append({"tool": tool, "ready": label in hooked, "models": models,
                    "choice": _pick_choice(models, choices.get(tool), tool)})
    _hcom_tools_cache.update(at=time.time(), val=val)
    return val


CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _codex_models():
    """codex 가 받아 둔 모델 목록(models_cache.json)에서 목록에 보이는 것만.
    이름을 코드에 박지 않는다 — codex 가 갱신하면 따라간다."""
    try:
        d = json.loads((CODEX_HOME / "models_cache.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for m in sorted(d.get("models") or [], key=lambda m: m.get("priority", 999)):
        if m.get("visibility") != "list" or not m.get("slug"):
            continue
        efforts = [e.get("effort") if isinstance(e, dict) else e
                   for e in (m.get("supported_reasoning_levels") or [])]
        out.append({"id": m["slug"], "label": m.get("display_name") or m["slug"],
                    "efforts": [e for e in efforts if e],
                    "default_effort": m.get("default_reasoning_level") or ""})
    return out


def _codex_defaults():
    """~/.codex/config.toml 맨 위의 model / model_reasoning_effort (섹션 전까지만 본다)."""
    try:
        head = (CODEX_HOME / "config.toml").read_text(encoding="utf-8").split("\n[", 1)[0]
    except Exception:
        return None, None
    m = re.search(r'^model\s*=\s*"([^"]+)"', head, re.M)
    e = re.search(r'^model_reasoning_effort\s*=\s*"([^"]+)"', head, re.M)
    return (m.group(1) if m else None), (e.group(1) if e else None)


def _pick_choice(models, saved, tool):
    """마지막으로 고른 값 → 없으면 codex 기본 설정 → 없으면 목록 첫 번째. 목록에 없는 값은 버린다."""
    if not models:
        return {"model": "", "effort": ""}
    saved = saved or {}
    dm, de = _codex_defaults() if tool == "codex" else (None, None)
    ids = {m["id"]: m for m in models}
    mid = next((x for x in (saved.get("model"), dm) if x in ids), models[0]["id"])
    m = ids[mid]
    eff = next((x for x in (saved.get("effort"), de if mid == dm else None, m["default_effort"])
                if x in m["efforts"]), m["efforts"][0] if m["efforts"] else "")
    return {"model": mid, "effort": eff}


def _hcom_spawn(args, cwd=None, wait=20, env=None):
    """에이전트를 띄우는 명령은 새 터미널을 열고 오래 붙어 있을 수 있다.
    wait 초 안에 끝나면 그 결과를, 넘기면 '시작됨'으로 본다 — 죽이지 않는다."""
    exe = _hcom_exe()
    if not exe:
        return False, "hcom 이 설치돼 있지 않아"
    try:
        pr = subprocess.Popen([exe] + args, cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", env=env or child_env(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as ex:
        return False, str(ex)
    try:
        out, _ = pr.communicate(timeout=wait)
        return pr.returncode == 0, (out or "").strip()
    except subprocess.TimeoutExpired:
        # 파이프가 차서 자식이 멈추지 않게 뒤에서 계속 비워 준다
        threading.Thread(target=pr.communicate, daemon=True).start()
        return True, "시작됨"


def _iso_epoch(ts):
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _hcom_ensure_me():
    """bigboss 가 instances 에 없으면 한 번 등록한다 (없으면 send 가 거부된다)."""
    try:
        with _hcom_conn() as con:
            if con.execute("select 1 from instances where name=?", (HCOM_ME,)).fetchone():
                return True, ""
    except Exception:
        pass
    code, out = _hcom_run(["start", "--as", HCOM_ME])
    return code == 0, out


@app.route("/api/hcom/state")
def hcom_state():
    after = int(request.args.get("after", 0))
    if not HCOM_DB.exists():
        return jsonify({"ok": False, "error": "hcom 이 아직 없어 (설치/실행 전)",
                        "messages": [], "agents": [], "last_id": 0})
    msgs, agents, stopped, last = [], [], [], after
    try:
        with _hcom_conn() as con:
            for eid, ts, typ, inst, data in con.execute(
                    "select id,timestamp,type,instance,data from events "
                    "where id>? and type in ('message','life') order by id limit 500", (after,)):
                last = max(last, eid)
                try:
                    d = json.loads(data)
                except Exception:
                    d = {}
                if typ == "message":
                    msgs.append({"id": eid, "ts": ts, "kind": "msg",
                                 "from": d.get("from") or inst,
                                 "text": d.get("text", ""),
                                 "scope": d.get("scope", ""),
                                 "to": d.get("delivered_to") or []})
                elif d.get("action") in ("created", "ready", "stopped", "killed"):
                    msgs.append({"id": eid, "ts": ts, "kind": "life",
                                 "from": inst, "text": d.get("action")})
            for (name, tool, status, directory, created,
                 ctx, detail, stime, seen) in con.execute(
                    "select name,tool,status,directory,created_at,"
                    "status_context,status_detail,status_time,last_event_id "
                    "from instances order by created_at"):
                agents.append({"name": name, "tool": tool, "status": status,
                               "dir": directory or "", "created": created or 0,
                               "ctx": ctx or "", "detail": (detail or "")[:200],
                               "at": stime or 0, "seen": seen or 0})
            # 멈춘 에이전트 = 마지막 life 이벤트가 stopped/killed 이고 지금 instances 에 없는 것.
            # stopped 이벤트의 snapshot 에 tool·session_id 가 들어 있다 (session_id 가 있어야 되살린다)
            current = {a["name"] for a in agents}
            latest = {}
            for ts, inst, data in con.execute(
                    "select timestamp,instance,data from events where type='life' order by id"):
                latest[inst] = (ts, data)
            for inst, (ts, data) in latest.items():
                if inst in current or inst == HCOM_ME:
                    continue
                try:
                    d = json.loads(data)
                except Exception:
                    continue
                if d.get("action") not in ("stopped", "killed"):
                    continue
                if _iso_epoch(ts) < time.time() - 86400:      # 하루 지난 건 안 보여준다
                    continue
                snap = d.get("snapshot") or {}
                stopped.append({"name": inst, "tool": snap.get("tool") or "",
                                "resumable": bool(snap.get("session_id"))})
    except Exception as ex:
        return jsonify({"ok": False, "error": f"hcom.db 읽기 실패: {ex}",
                        "messages": [], "agents": [], "last_id": after})
    agents, stopped = _drop_hidden(agents, stopped)
    return jsonify({"ok": True, "messages": msgs, "agents": agents, "stopped": stopped,
                    "tools": _hcom_tools(), "spawns": _take_spawns(),
                    "last_id": last, "me": HCOM_ME})


def _drop_hidden(agents, stopped):
    """목록에서 지운 애들을 빼고 준다. 같은 이름이 다시 살아 돌아오면 숨김을 푼다
    (hcom 은 이름을 돌려쓴다 — 계속 숨겨 두면 새로 부른 애가 안 보인다)."""
    hidden = set(load_config().get("hcom_hidden") or [])
    if not hidden:
        return agents, stopped
    back = hidden & {a["name"] for a in agents if a["status"] in _HCOM_LIVE}
    if back:
        cfg = load_config()
        cfg["hcom_hidden"] = [n for n in (cfg.get("hcom_hidden") or []) if n not in back]
        save_config(cfg)
        hidden -= back
    return ([a for a in agents if a["name"] not in hidden],
            [x for x in stopped if x["name"] not in hidden])


def _hcom_name(body):
    name = (body.get("name") or "").strip()
    return name if re.fullmatch(r"[a-z0-9_]+", name) and name != HCOM_ME else None


@app.route("/api/hcom/kill", methods=["POST"])
def hcom_kill():
    """켜져 있는 애를 방에서 내보낸다. 세션은 남아서 나중에 다시 깨울 수 있다."""
    name = _hcom_name(request.get_json(force=True))
    if not name:
        return jsonify({"error": "이름이 이상해"}), 400
    ok, out = _hcom_spawn(["kill", name], wait=15)
    if not ok:
        return jsonify({"error": out or "내보내기 실패"}), 500
    return jsonify({"ok": True, "out": out})


@app.route("/api/hcom/hide", methods=["POST"])
def hcom_hide():
    """이미 꺼진 애를 목록에서만 지운다. hcom.db 는 건드리지 않는다."""
    name = _hcom_name(request.get_json(force=True))
    if not name:
        return jsonify({"error": "이름이 이상해"}), 400
    cfg = load_config()
    lst = [n for n in (cfg.get("hcom_hidden") or []) if n != name]
    lst.append(name)
    cfg["hcom_hidden"] = lst[-200:]
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/hcom/send", methods=["POST"])
def hcom_send():
    body = request.get_json(force=True)
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "빈 메시지"}), 400
    ok, out = _hcom_ensure_me()
    if not ok:
        return jsonify({"error": f"hcom identity 등록 실패: {out}"}), 500
    args = ["send", "--name", HCOM_ME]
    for t in (body.get("targets") or []):
        args.append(t if t.startswith("@") else "@" + t)
    args += ["--", text]
    code, out = _hcom_run(args)
    if code != 0:
        return jsonify({"error": out or "전송 실패"}), 500
    return jsonify({"ok": True, "out": out})


HELM_SPOOL = HCOM_DIR / "helm_spool"
HCOM_OPEN = HERE / "hcom_open.py"


def _helm_terminal_cmd():
    """hcom 이 에이전트 창을 여는 대신 부를 명령. 역슬래시는 hcom 의 명령줄 쪼개기에
    먹힐 수 있어 슬래시로 쓴다. 콘솔 창이 번쩍이지 않게 pythonw 를 쓴다."""
    py = Path(sys.executable)
    if py.with_name("pythonw.exe").exists():
        py = py.with_name("pythonw.exe")
    # {script} 는 빼면 안 된다 — hcom 은 {script} 없는 명령을 거부하고 조용히 기본 동작으로
    # 빠져서(= 창 없이 실행) 에이전트가 화면에 안 보인다. cwd 는 공백이 있을 수 있어 맨 뒤에.
    return (f"{py.as_posix()} {HCOM_OPEN.as_posix()} "
            f"{{script}} {{instance_name}} {{tool}} {{cwd}}")


def _hcom_env_for_helm():
    env = child_env()
    env["HCOM_TERMINAL"] = _helm_terminal_cmd()   # 이 실행에만. 전역 config.toml 은 그대로
    env["HCOM_DIR"] = str(HCOM_DIR)
    return env


def _take_spawns():
    """hcom_open.py 가 남긴 '이거 열어줘'를 집는다. 여러 패널이 동시에 물어도 한 번만 나가게
    os.replace 로 먼저 차지한 쪽만 쓴다. 2분 지난 건 버린다(HELM 이 꺼져 있던 때 것)."""
    out = []
    if not HELM_SPOOL.is_dir():
        return out
    for f in sorted(HELM_SPOOL.glob("*.json")):
        claim = f.with_suffix(".taken")
        try:
            os.replace(f, claim)
        except OSError:
            continue
        try:
            d = json.loads(claim.read_text(encoding="utf-8"))
        except Exception:
            d = None
        try:
            claim.unlink()
        except OSError:
            pass
        if not d or time.time() - float(d.get("at") or 0) > 120:
            continue
        pending = _launch_notes.pop(d.get("tool") or "", None) or {}
        out.append({"script": d.get("script", ""), "name": d.get("name", ""),
                    "tool": d.get("tool", ""), "cwd": d.get("cwd", ""),
                    "model": pending.get("model", ""), "effort": pending.get("effort", "")})
    return out


_launch_notes = {}      # tool → 방금 부를 때 고른 모델·강도 (탭 머리에 표시용)


@app.route("/api/hcom/launch", methods=["POST"])
def hcom_launch():
    body = request.get_json(force=True)
    tool = body.get("tool") or ""
    t = next((x for x in _hcom_tools() if x["tool"] == tool), None)
    if not t:
        return jsonify({"error": f"설치 안 된 도구: {tool}"}), 400
    if not t["ready"]:
        return jsonify({"error": f"{tool} 은 hcom 훅이 없어 불러와도 대화를 못 받아 "
                                 f"(hcom hooks add {tool})"}), 400
    cwd = body.get("cwd") or ""
    if not Path(cwd).is_dir():
        cwd = str(Path.home())

    args = ["1", tool, "--dir", cwd]
    model, effort = body.get("model") or "", body.get("effort") or ""
    if t["models"]:
        m = next((x for x in t["models"] if x["id"] == model), None)
        if not m:
            return jsonify({"error": f"목록에 없는 모델: {model}"}), 400
        if effort and effort not in m["efforts"]:
            return jsonify({"error": f"{model} 은 강도 {effort} 를 못 받아"}), 400
        args += ["--model", model]
        if effort and tool == "codex":
            args += ["-c", f'model_reasoning_effort="{effort}"']
        cfg = load_config()
        cfg.setdefault("hcom_choice", {})[tool] = {"model": model, "effort": effort}
        save_config(cfg)
        _hcom_tools_cache["at"] = 0.0
    _launch_notes[tool] = {"model": model, "effort": effort}

    ok, out = _hcom_spawn(args, cwd=cwd, env=_hcom_env_for_helm())
    if not ok:
        _launch_notes.pop(tool, None)
        return jsonify({"error": out or "불러오기 실패"}), 500
    return jsonify({"ok": True, "out": out, "cwd": cwd})


@app.route("/api/hcom/resume", methods=["POST"])
def hcom_resume():
    name = (request.get_json(force=True).get("name") or "").strip()
    if not re.fullmatch(r"[a-z0-9_]+", name) or name == HCOM_ME:
        return jsonify({"error": f"이름이 이상해: {name}"}), 400
    ok, out = _hcom_spawn(["r", name], env=_hcom_env_for_helm())
    if not ok:
        return jsonify({"error": out or "깨우기 실패"}), 500
    return jsonify({"ok": True, "out": out})


# ============================== 클립보드 ==============================
# WebView2 안에서는 navigator.clipboard 읽기가 권한 요청에 막혀 Ctrl+V가 죽는다.
# 우리는 어차피 로컬에서 도는 앱이니 윈도우 클립보드를 서버가 직접 읽어 넘겨준다.
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _clip_open(tries=8):
    """다른 앱이 클립보드를 쥐고 있으면 잠깐 실패한다 — 몇 번 다시 해본다."""
    u32 = ctypes.windll.user32
    for _ in range(tries):
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_get():
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    u32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]
    u32.GetClipboardData.restype = ctypes.wintypes.HANDLE
    k32.GlobalLock.argtypes = [ctypes.wintypes.HANDLE]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.wintypes.HANDLE]
    if not _clip_open():
        return ""
    try:
        h = u32.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return ""                      # 텍스트가 아닌 것(이미지 등)
        ptr = k32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def clipboard_set(text):
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.wintypes.HANDLE
    k32.GlobalLock.argtypes = [ctypes.wintypes.HANDLE]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.wintypes.HANDLE]
    u32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.wintypes.HANDLE]
    u32.SetClipboardData.restype = ctypes.wintypes.HANDLE
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    if not _clip_open():
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(_GMEM_MOVEABLE, size)
        if not h:
            return False
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, buf, size)
        k32.GlobalUnlock(h)
        # 넘겨준 뒤로는 클립보드가 주인이라 우리가 풀면 안 된다
        return bool(u32.SetClipboardData(_CF_UNICODETEXT, h))
    finally:
        u32.CloseClipboard()


@app.route("/api/clipboard", methods=["GET"])
def clip_read():
    try:
        return jsonify({"text": clipboard_get()})
    except Exception as ex:
        return jsonify({"error": str(ex), "text": ""}), 500


@app.route("/api/clipboard", methods=["POST"])
def clip_write():
    body = request.get_json(force=True)
    try:
        return jsonify({"ok": clipboard_set(body.get("text", ""))})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ============================== 구독 한도 (/usage 긁기) ==============================
# 5시간·주간 한도는 CLI가 플래그로도 파일로도 안 내준다. TUI의 `/usage` 화면에만 있다.
# 그래서 안 보이는 세션을 하나 띄워 `/usage`만 치고 화면을 긁어온다.
# 모델 호출이 아니라 계정 조회라 토큰 비용은 0이다(패널의 Total cost가 $0.0000).
# ⚠ 화면을 긁는 방식이라 claude가 레이아웃을 바꾸면 깨진다 → 그때는 조용히 숨긴다.
LIMIT_REFRESH = 600        # 초. 매번 claude를 새로 띄우므로 너무 자주 하지 말 것
LIMIT_READY = 9            # TUI가 뜰 때까지
LIMIT_DRAW = 6             # /usage 패널이 그려질 때까지

_limits = {"at": 0, "ok": False, "data": {}}
_limit_lock = threading.Lock()
_limit_thread = None
_limit_kick = threading.Event()

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]")


def _parse_usage_screen(txt):
    """`/usage` 화면 텍스트 → {"session": {pct, resets}, "week": {...}, "opus": {...}}

    화면이 여러 번 다시 그려지므로 뒤에 나온 것이 앞을 덮어쓰게 그냥 훑는다.
    막대와 숫자가 붙어 나온다(`37%used`)는 점에 유의.
    """
    out, cur, note = {}, None, ""
    for ln in txt.splitlines():
        low = ln.lower()
        if "current session" in low:
            cur = "session"
            continue
        if "current week" in low:
            cur = "opus" if "opus" in low else "week"
            continue
        if "promo" in low and "%" in ln:
            note = " ".join(ln.split())
        if not cur:
            continue
        m = re.search(r"(\d+)\s*%\s*used", ln)
        if m:
            out.setdefault(cur, {})["pct"] = int(m.group(1))
            continue
        m = re.search(r"Resets\s+(.+?)\s*$", ln)
        if m and cur in out:
            out[cur]["resets"] = m.group(1).strip()
            cur = None
    if note:
        out["note"] = note
    return out


def _read_usage_screen():
    """숨은 claude 세션을 띄워 /usage 화면을 받아온다. 실패하면 None."""
    cwd = str(HERE if HERE.is_dir() else Path.home())
    proc = None
    try:
        proc = winpty.PtyProcess.spawn(["claude"], cwd=cwd, dimensions=(32, 100),
                                       env=child_env())
        buf = []

        def pump():                      # read()는 막히므로 따로 돌린다 (Pane과 같은 이유)
            while True:
                try:
                    d = proc.read(8192)
                except Exception:
                    return
                if d:
                    buf.append(d)

        threading.Thread(target=pump, daemon=True).start()
        time.sleep(LIMIT_READY)
        # ⚠ 앞이 안 보이는 채로 Enter를 치는 짓이다. 신뢰 프롬프트가 떠 있으면
        #   그 Enter가 "이 폴더를 신뢰함"을 눌러버린다(실제로 겪음). 그래서 먼저 본다.
        if re.search(r"trust this folder|신뢰", _ANSI.sub("", "".join(buf)), re.I):
            return None
        proc.write("/usage\r")
        time.sleep(LIMIT_DRAW)
        return _ANSI.sub("", "".join(buf))
    except Exception:
        return None
    finally:
        if proc is not None:
            try:
                proc.terminate(force=True)   # 우리가 띄운 자식 프로세스만 닫는다
            except Exception:
                pass


def _limit_loop():
    while True:
        txt = _read_usage_screen()
        data = _parse_usage_screen(txt) if txt else {}
        with _limit_lock:
            if data.get("session") or data.get("week"):
                _limits.update({"at": time.time(), "ok": True, "data": data})
            else:
                _limits["at"] = time.time()      # 실패해도 직전 값은 남겨둔다
                _limits["ok"] = bool(_limits["data"])
        _limit_kick.wait(LIMIT_REFRESH)
        _limit_kick.clear()


def _ensure_limit_thread():
    """첫 요청 때부터 돌린다(앱을 import만 해도 claude가 뜨는 일이 없게)."""
    global _limit_thread
    with _limit_lock:
        if _limit_thread and _limit_thread.is_alive():
            return
        _limit_thread = threading.Thread(target=_limit_loop, daemon=True)
        _limit_thread.start()


@app.route("/api/limits")
def limits():
    _ensure_limit_thread()
    if request.args.get("refresh"):
        _limit_kick.set()
    with _limit_lock:
        d = dict(_limits["data"])
        at, ok = _limits["at"], _limits["ok"]
    return jsonify({"ok": ok, "age": int(time.time() - at) if at else None, **d})


@app.route("/api/usage")
def usage():
    """패널마다 지금 컨텍스트를 얼마나 먹었는지. 프론트가 몇 초마다 물어본다."""
    out = {}
    for pid, p in list(PANES.items()):
        if p.agent:          # 폴더만 보고 찾으면 같은 폴더의 남의 claude 세션을 잡는다
            out[pid] = {"tokens": None}
            continue
        f = _session_file(p)
        info = _context_of(f) if f else None
        if not info:
            out[pid] = {"tokens": None}      # 아직 첫 응답 전이면 잴 게 없다
            continue
        limit = CONTEXT_LIMITS.get(info["model"] or p.model, DEFAULT_CONTEXT)
        out[pid] = {"tokens": info["tokens"], "limit": limit,
                    "pct": round(100.0 * info["tokens"] / limit, 1),
                    "model": info["model"], "session": f.stem}
    return jsonify(out)


@sock.route("/ws/<pid>")
def ws_pane(ws, pid):
    p = PANES.get(pid)
    if not p:
        ws.close()
        return
    q, backlog = p.subscribe()
    if backlog:
        ws.send(json.dumps({"t": "o", "d": backlog}))

    stop = threading.Event()

    def push():
        while not stop.is_set():
            try:
                data = q.get(timeout=0.3)
            except queue.Empty:
                # 패널이 죽었고 남은 출력도 다 보냈으면 서버 쪽에서 정상 종료한다.
                # (클라이언트가 먼저 끊으면 종료 메시지가 깨진 프레임으로 나간다)
                if not p.alive:
                    break
                continue
            try:
                ws.send(json.dumps({"t": "o", "d": data}))
            except Exception:
                break
        stop.set()
        try:
            ws.close()
        except Exception:
            pass

    t = threading.Thread(target=push, daemon=True)
    t.start()
    try:
        while True:
            raw = ws.receive(timeout=None)
            if raw is None:
                break
            m = json.loads(raw)
            if m["t"] == "i":
                p.write(m["d"])
            elif m["t"] == "r":
                p.resize(int(m["cols"]), int(m["rows"]))
    except Exception:
        pass
    finally:
        stop.set()
        p.unsubscribe(q)


# ============================== 실행 ==============================
def run_server(port):
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, app, threaded=True)
    srv.serve_forever()


def set_app_id(app_id):
    """작업표시줄에서 pythonw.exe가 아니라 독립된 앱으로 잡히게 한다.
    이걸 안 하면 다른 파이썬 창들과 한 덩어리로 묶이고 아이콘도 그쪽을 따라간다."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass          # 아이콘 문제일 뿐이니 실패해도 앱은 그대로 뜬다


def main():
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    threading.Thread(target=run_server, args=(port,), daemon=True).start()
    time.sleep(0.4)

    set_app_id("Hoyoon.HELM.Main")

    import webview
    win = webview.create_window("HELM", f"http://127.0.0.1:{port}/",
                                width=1500, height=940, min_size=(900, 600),
                                background_color="#101013")

    def on_closing():
        for p in list(PANES.values()):
            p.close()

    win.events.closing += on_closing
    webview.start(icon=str(ICON) if ICON.exists() else None)


if __name__ == "__main__":
    import sys
    if "--server" in sys.argv:        # 창 없이 서버만 (점검용)
        run_server(int(sys.argv[sys.argv.index("--server") + 1]))
    else:
        main()
