# -*- coding: utf-8 -*-
"""HELM 채팅형 UI — 프로토타입.

터미널(xterm) 대신 claude를 Agent SDK로 물고, 대화·툴호출·권한요청을
카드 UI로 직접 그린다. HELM 본체(app.py)와 완전히 별개로 돌아간다.

핵심: 권한 확인창을 우리가 만든다 (options.can_use_tool 콜백).
      CLI에는 이걸 붙일 플래그가 없어서 SDK를 쓰는 것.
"""
import io
import os
import json
import uuid
import queue
import asyncio
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock

import claude_agent_sdk as sdk

# ---- CLI 창이 번쩍이는 것 막기 ----------------------------------------------
# .lnk가 pythonw로 띄우니 이 프로세스엔 콘솔이 없다. 그 상태에서 콘솔앱인
# claude.exe를 띄우면 윈도우가 새 콘솔(윈도우 터미널)을 만들어 주고, 그게 화면에
# 뜨면서 포커스를 뺏는다 — 세션을 열거나 갈아탈 때마다 창이 켜졌다 꺼졌다 한 이유.
# SDK는 creationflags를 넘길 통로가 없어서 anyio.open_process를 감싼다.
# (subprocess_cli.py가 호출 시점에 anyio.open_process를 찾으므로 이 교체가 먹는다)
if os.name == "nt":
    import anyio as _anyio

    CREATE_NO_WINDOW = 0x08000000
    _spawn = _anyio.open_process

    async def _spawn_no_window(*a, **kw):
        kw.setdefault("creationflags", CREATE_NO_WINDOW)
        return await _spawn(*a, **kw)

    _anyio.open_process = _spawn_no_window

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static_chat"
ICON = HERE / "helm.ico"          # 창·작업표시줄 아이콘 (make_icon.py로 굽는다)
SHARED = HERE / "static"          # 폰트 등은 본체 것을 같이 쓴다
CONFIG = HERE / "config.json"

# 프로토타입이라 세션 하나만 다룬다.
MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
PERM_MODES = ["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"]

app = Flask(__name__, static_folder=None)
sock = Sock(app)


def child_env():
    """API 키가 환경에 있으면 구독 대신 크레딧으로 과금될 수 있으니 지운다.
    자식 세션 표식도 지워야 transcript가 저장된다(본체 app.py와 같은 이유)."""
    drop = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_PID", "CLAUDE_EFFORT",
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    return {k: v for k, v in os.environ.items() if k not in drop}


WINDOW_TITLE = "HELM — 채팅 프로토타입"


def flash_taskbar():
    """다른 창을 쓰는 중이면 작업표시줄만 깜빡인다.
    포커스를 뺏지 않는 게 핵심 — 뺏으면 하던 일이 끊긴다."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            n = u.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                if buf.value == WINDOW_TITLE:
                    found.append(h)
                    return False
            return True

        u.EnumWindows(cb, 0)
        if not found or u.GetForegroundWindow() == found[0]:
            return                      # 이미 보고 있으면 깜빡일 이유가 없다

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD)]
        fw = FLASHWINFO(ctypes.sizeof(FLASHWINFO), found[0],
                        0x00000002 | 0x00000004, 3, 0)   # FLASHW_TRAY | FLASHW_TIMER
        u.FlashWindowEx(ctypes.byref(fw))
    except Exception:
        pass


def projects():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("projects", [])
    except Exception:
        return [{"name": "홈", "path": str(Path.home())}]


# ============================== 지난 대화 읽기 ==============================
# ~/.claude/projects/<인코딩된 경로>/<session-uuid>.jsonl
# claude가 남기는 기록. 여기서 말풍선으로 되살린다.
CLAUDE_HOME = Path.home() / ".claude" / "projects"
MAX_RENDER = 400          # 너무 긴 대화는 뒤쪽만 (5MB짜리도 있다)


def _blocks(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}] if c.strip() else []
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def conversations(limit=40):
    """대화 목록. 제목은 claude가 붙여둔 ai-title을 쓰고, 없으면 첫 발화."""
    out = []
    if not CLAUDE_HOME.exists():
        return out
    files = []
    for d in CLAUDE_HOME.iterdir():
        if d.is_dir():
            for f in d.glob("*.jsonl"):
                try:
                    files.append((f.stat().st_mtime, f))
                except OSError:
                    pass
    files.sort(reverse=True)

    for mtime, f in files[:limit]:
        title, cwd, first, real = "", "", "", False
        try:
            with io.open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    t = d.get("type")
                    if t in ("user", "assistant"):
                        real = True     # 제목만 있고 본문이 없는 껍데기 파일이 있다
                    if t == "ai-title" and d.get("aiTitle"):
                        title = d["aiTitle"]          # 뒤에 나온 게 최신이라 계속 덮어씀
                    elif not cwd and d.get("cwd"):
                        cwd = d["cwd"]
                    elif not first and t == "user":
                        for b in _blocks(d.get("message")):
                            if b.get("type") == "text" and not b["text"].startswith("<"):
                                first = " ".join(b["text"].split())[:70]
                                break
        except Exception:
            continue
        # 껍데기(제목 레코드만 있는 것)는 CLI가 이어 열지 못한다 — 목록에서 뺀다
        if not real or not (title or first):
            continue
        out.append({"id": f.stem, "title": title or first, "cwd": cwd,
                    "project": Path(cwd).name if cwd else f.parent.name,
                    "mtime": mtime})
    return out


def session_file(session_id):
    if not session_id:
        return None
    for d in CLAUDE_HOME.iterdir():
        if d.is_dir() and (d / f"{session_id}.jsonl").exists():
            return d / f"{session_id}.jsonl"
    return None


def _first_cwd(path, limit=200):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > limit:
                    break
                try:
                    c = json.loads(line).get("cwd")
                except Exception:
                    continue
                if c:
                    return c
    except OSError:
        pass
    return ""


def resolve_cwd(session_id, given=""):
    """이어 열 때 쓸 폴더를 정한다.
    CLI는 cwd로 세션을 찾기 때문에 여기가 틀리면 'No conversation found with
    session ID'로 터지고, 그 여파로 열려있던 세션까지 닫힌다(실제로 겪음)."""
    if given and Path(given).is_dir():
        return given
    p = session_file(session_id)
    if not p:
        return None
    # 1) 자기 파일 → 같은 폴더의 다른 세션이 적어둔 cwd (같은 폴더면 cwd도 같다)
    for f in [p] + sorted(p.parent.glob("*.jsonl")):
        c = _first_cwd(f)
        if c and Path(c).is_dir():
            return c
    # 2) 폴더 이름은 되돌릴 수 없다(한글·기호가 전부 '-'로 뭉개짐).
    #    대신 등록된 프로젝트 경로를 같은 규칙으로 인코딩해 이름이 맞는 걸 찾는다.
    import re
    enc = lambda s: re.sub(r"[^A-Za-z0-9]", "-", s)
    for proj in projects():
        if enc(proj["path"]) == p.parent.name and Path(proj["path"]).is_dir():
            return proj["path"]
    return None


def history(session_id):
    """세션 파일을 UI 이벤트 목록으로 바꾼다. translate()와 같은 모양."""
    path = session_file(session_id)
    if not path:
        return None, None

    evs, cwd = [], ""
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            t = d.get("type")
            if t not in ("user", "assistant"):
                continue
            # isMeta / sourceToolUseID 가 붙은 user 레코드는 스킬·훅이 끼워넣은 것.
            # 진짜 사람이 친 건 origin.kind == "human" 이 붙는다.
            is_injected = t == "user" and (d.get("isMeta") or d.get("sourceToolUseID"))
            for b in _blocks(d.get("message")):
                if is_injected and b.get("type") == "text":
                    continue
                bt = b.get("type")
                if bt == "text":
                    txt = b.get("text", "")
                    if not txt.strip():
                        continue
                    if t == "user":
                        # 훅/시스템이 끼워넣은 블록은 사람이 쓴 말이 아니다
                        if txt.lstrip().startswith(("<", "Caveat:")):
                            continue
                        evs.append({"t": "user", "text": txt})
                    else:
                        evs.append({"t": "text", "text": txt})
                elif bt == "thinking":
                    evs.append({"t": "thinking", "text": b.get("thinking", "")})
                elif bt == "tool_use":
                    evs.append({"t": "tool_use", "id": b.get("id"),
                                "name": b.get("name"), "input": _safe_json(b.get("input"))})
                elif bt == "tool_result":
                    evs.append({"t": "tool_result", "id": b.get("tool_use_id"),
                                "content": _safe_json(b.get("content")),
                                "is_error": bool(b.get("is_error"))})
    trimmed = len(evs) > MAX_RENDER
    return (evs[-MAX_RENDER:] if trimmed else evs), {"cwd": cwd, "trimmed": trimmed,
                                                     "total": len(evs)}


# ============================== 세션 ==============================
class ChatSession:
    """claude 하나 = 세션 하나. asyncio 루프를 전용 스레드에서 돌리고,
    웹소켓은 큐로 붙는다. 세션끼리는 완전히 독립이라 서로 죽이지 않는다."""

    def __init__(self, key):
        self.key = key
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.client = None
        self.subs = []
        self.lock = threading.Lock()
        self.log = []              # 새로고침/재연결 시 되돌려줄 이벤트 기록
        self.pending = {}          # tool_use_id -> asyncio.Future (권한 대기)
        self.busy = False
        self.cwd = None
        self.model = None
        self.perm = "default"
        self.resume_id = None
        self.claude_id = None      # CLI가 알려주는 실제 세션 id (목록 대조용)
        self.title = ""
        self.closed = False
        # 비용: ResultMessage.total_cost_usd 는 턴별이 아니라 '세션 누적'이다.
        # 그대로 더하면 2턴째부터 부풀려진다(사용량이 이상하게 보이던 이유).
        self.cost_now = 0.0        # 지금 연결이 보고한 누적
        self.cost_done = 0.0       # 압축/리셋 전까지 확정된 몫
        self.limit = None          # 마지막 rate limit 상태

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---------- 비용 ----------
    def note_cost(self, running):
        """CLI가 준 누적값을 그대로 받아 총액을 갱신하고, 이번 턴 몫을 돌려준다.
        압축(compact)이 일어나면 누적이 0으로 돌아가므로 그때만 확정분에 넘긴다."""
        before = self.cost_total()
        if running is not None:
            if running + 1e-9 < self.cost_now:      # 리셋됨
                self.cost_done += self.cost_now
            self.cost_now = running
        return self.cost_total(), max(0.0, self.cost_total() - before)

    def cost_total(self):
        return self.cost_done + self.cost_now

    # ---------- 이벤트 방출 ----------
    def emit(self, ev):
        ev.setdefault("key", self.key)
        with self.lock:
            # 글자 조각은 순간용 — 기록에 쌓으면 재연결 때 다시 흘러 중복된다
            if ev["t"] not in ("delta", "delta_start"):
                self.log.append(ev)
            if len(self.log) > 2000:
                del self.log[:500]
            for q in list(self.subs):
                q.put(ev)

    def subscribe(self):
        """재연결 시 지난 기록을 돌려주되, 이미 처리된 이벤트는 걸러낸다.
        안 그러면 답이 끝난 권한창이 다시 뜨고, 끝난 턴의 result가 또 찍힌다."""
        q = queue.Queue()
        with self.lock:
            resolved = {e["id"] for e in self.log if e["t"] == "permission_done"}
            backlog = []
            for e in self.log:
                if e["t"] == "permission" and e["id"] in resolved:
                    continue                    # 이미 답한 권한 요청은 다시 묻지 않는다
                if e["t"] in ("busy", "ratelimit"):
                    continue                    # 순간 상태라 되돌릴 의미 없음
                backlog.append({**e, "replay": True})
            # 지금 돌고 있는 중인지는 지난 기록이 아니라 현재값으로 알려준다
            backlog.append({"t": "busy", "on": self.busy, "key": self.key, "replay": True})
            self.subs.append(q)
        return q, backlog

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    # ---------- 권한 콜백 ----------
    async def _can_use_tool(self, tool_name, tool_input, ctx):
        """claude가 툴을 쓰려 할 때마다 여기로 온다. UI에 물어보고 답을 기다린다."""
        tuid = getattr(ctx, "tool_use_id", None) or f"t{len(self.pending)}"
        fut = self.loop.create_future()
        self.pending[tuid] = fut
        self.emit({
            "t": "permission",
            "id": tuid,
            "tool": tool_name,
            "input": _safe_json(tool_input),
            "title": getattr(ctx, "title", None) or getattr(ctx, "display_name", None),
            "description": getattr(ctx, "description", None),
            "reason": getattr(ctx, "decision_reason", None),
        })
        try:
            ok, msg = await asyncio.wait_for(fut, timeout=600)
        except asyncio.TimeoutError:
            ok, msg = False, "10분 안에 답이 없어서 자동 거절"
        finally:
            self.pending.pop(tuid, None)
        self.emit({"t": "permission_done", "id": tuid, "allow": ok, "message": msg})
        if ok:
            return sdk.PermissionResultAllow()
        return sdk.PermissionResultDeny(message=msg or "사용자가 거절했습니다")

    def answer_permission(self, tuid, allow, message=""):
        fut = self.pending.get(tuid)
        if fut and not fut.done():
            self.loop.call_soon_threadsafe(fut.set_result, (allow, message))
            return True
        return False

    # ---------- 수명주기 ----------
    async def _start(self, cwd, model, perm, resume=None):
        opts = sdk.ClaudeAgentOptions(
            cwd=cwd,
            model=model,
            permission_mode=perm,
            can_use_tool=self._can_use_tool,
            env=child_env(),
            fallback_model="claude-haiku-4-5" if model != "claude-haiku-4-5" else None,
            resume=resume,
            include_partial_messages=True,   # 글자 단위로 흘려보내려면 필요
        )
        client = sdk.ClaudeSDKClient(options=opts)
        try:
            await client.connect()
        except Exception:
            # 연결 실패한 클라이언트를 남겨두면 세션이 살아있는 척하면서
            # 이후 모든 요청이 'Not connected'로 터진다(좀비 상태).
            self.client = None
            raise
        self.client = client
        self.cwd, self.model, self.perm = cwd, model, perm
        self.emit({"t": "ready", "cwd": cwd, "model": model, "perm": perm,
                   "resumed": bool(resume)})

    def start(self, cwd, model, perm, resume=None):
        self.resume_id = resume
        self.claude_id = resume
        return self._submit(self._start(cwd, model, perm, resume)).result(timeout=180)

    async def _ask(self, text):
        self.busy = True
        self.emit({"t": "busy", "on": True})
        try:
            await self.client.query(text)
            async for msg in self.client.receive_response():
                for ev in translate(msg):
                    self.emit(self._enrich(ev, msg))
        except Exception as ex:
            self.emit({"t": "error", "text": f"{type(ex).__name__}: {ex}"})
        finally:
            self.busy = False
            self.emit({"t": "busy", "on": False})
            flash_taskbar()          # 다른 일 하는 중이면 끝난 걸 알린다

    def _enrich(self, ev, msg):
        """세션이 알아야 계산되는 것(누적 비용·한도)을 여기서 채운다."""
        if ev["t"] == "result":
            total, turn = self.note_cost(ev.pop("cost", None))
            ev["cost"] = turn                 # 이번 턴 몫
            ev["cost_total"] = total          # 세션 누적
            sid = getattr(msg, "session_id", None)
            if sid:
                self.claude_id = sid
        elif ev["t"] == "ratelimit":
            self.limit = ev
        return ev

    def ask(self, text):
        if not self.title:
            self.title = " ".join(text.split())[:60]
        self.emit({"t": "user", "text": text})
        self._submit(self._ask(text))

    def set_model(self, model):
        self.model = model
        self._submit(self.client.set_model(model)).result(timeout=30)
        self.emit({"t": "note", "text": f"모델을 {model} 로 바꿨어"})

    def set_perm(self, mode):
        self.perm = mode
        self._submit(self.client.set_permission_mode(mode)).result(timeout=30)
        self.emit({"t": "note", "text": f"권한 모드를 {mode} 로 바꿨어"})

    def interrupt(self):
        self._submit(self.client.interrupt()).result(timeout=30)
        self.emit({"t": "note", "text": "중단 요청을 보냈어"})

    def close(self):
        """이 세션만 닫는다. 다른 세션은 건드리지 않는다."""
        self.closed = True
        if self.client is not None:
            try:
                self._submit(self.client.disconnect()).result(timeout=30)
            except Exception:
                pass
            self.client = None
        self.emit({"t": "closed"})
        self.loop.call_soon_threadsafe(self.loop.stop)

    def info(self):
        return {"key": self.key, "cwd": self.cwd, "model": self.model,
                "perm": self.perm, "busy": self.busy, "conv": self.claude_id,
                "title": self.title, "cost": round(self.cost_total(), 6),
                "resumed": bool(self.resume_id), "limit": self.limit}


def _safe_json(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


def translate(msg):
    """SDK 메시지를 UI가 그릴 수 있는 납작한 이벤트로 바꾼다."""
    out = []
    if isinstance(msg, sdk.AssistantMessage):
        for b in msg.content:
            if isinstance(b, sdk.TextBlock):
                out.append({"t": "text", "text": b.text})
            elif isinstance(b, sdk.ThinkingBlock):
                out.append({"t": "thinking", "text": b.thinking})
            elif isinstance(b, sdk.ToolUseBlock):
                out.append({"t": "tool_use", "id": b.id, "name": b.name,
                            "input": _safe_json(b.input)})
    elif isinstance(msg, sdk.UserMessage):
        content = msg.content
        if isinstance(content, list):
            for b in content:
                if isinstance(b, sdk.ToolResultBlock):
                    out.append({"t": "tool_result", "id": b.tool_use_id,
                                "content": _safe_json(b.content),
                                "is_error": bool(b.is_error)})
    elif isinstance(msg, sdk.ResultMessage):
        u = msg.usage or {}
        out.append({"t": "result", "turns": msg.num_turns,
                    "ms": msg.duration_ms, "cost": msg.total_cost_usd,
                    "is_error": msg.is_error, "stop": msg.stop_reason,
                    "tok_in": u.get("input_tokens"), "tok_out": u.get("output_tokens"),
                    "tok_cache": (u.get("cache_read_input_tokens") or 0)
                                 + (u.get("cache_creation_input_tokens") or 0),
                    "errors": msg.errors or None})
    elif isinstance(msg, sdk.StreamEvent):
        # 원본 Anthropic 스트림 이벤트. 글자 조각만 뽑아 흘려보낸다.
        # 완성본은 뒤이어 AssistantMessage로 또 오므로, 화면에선 조각을 완성본으로 교체한다.
        e = msg.event or {}
        if e.get("type") == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                out.append({"t": "delta", "text": d["text"]})
        elif e.get("type") == "content_block_start":
            blk = (e.get("content_block") or {}).get("type")
            if blk == "text":
                out.append({"t": "delta_start"})
    elif isinstance(msg, sdk.RateLimitEvent):
        # __dict__ 를 통째로 넘기면 rate_limit_info가 데이터클래스라 JSON이 안 돼
        # 문자열로 떨어지고, 화면에선 그 문자열이 한 글자씩 쪼개져 나온다(실제로 겪음).
        # 필요한 필드만 꺼내서 납작하게 넘긴다.
        i = msg.rate_limit_info
        out.append({"t": "ratelimit", "status": i.status,
                    "utilization": i.utilization, "resets_at": i.resets_at,
                    "kind": i.rate_limit_type, "overage": i.overage_status})
    return out


# ============================== 세션 관리 ==============================
# 프로토타입 때는 세션이 하나뿐이라, 다른 대화를 열면 쓰던 게 닫혔다.
# 이제 대화별로 따로 살아있고 화면만 갈아탄다.
MAX_SESSIONS = 6              # claude 프로세스가 무한정 쌓이지 않게

SESSIONS = {}                 # key -> ChatSession
SLOCK = threading.Lock()


def get_session(key):
    with SLOCK:
        return SESSIONS.get(key)


def find_by_conv(conv_id):
    """이미 열려있는 대화면 그 세션을 그대로 쓴다 (다시 열어 닫지 않는다)."""
    if not conv_id:
        return None
    with SLOCK:
        for s in SESSIONS.values():
            if conv_id in (s.resume_id, s.claude_id):
                return s
    return None


def open_session(cwd, model, perm, resume=None):
    live = find_by_conv(resume)
    if live and live.client is not None:
        return live, True
    with SLOCK:
        alive = [s for s in SESSIONS.values() if s.client is not None]
        if len(alive) >= MAX_SESSIONS:
            raise RuntimeError(f"세션이 이미 {MAX_SESSIONS}개 열려있어 — 하나 닫고 다시 해줘")
        key = resume or f"new-{uuid.uuid4().hex[:8]}"
        while key in SESSIONS:
            key = f"new-{uuid.uuid4().hex[:8]}"
        s = ChatSession(key)
        SESSIONS[key] = s
    try:
        s.start(cwd, model, perm, resume)
    except Exception:
        with SLOCK:
            SESSIONS.pop(s.key, None)
        try:
            s.loop.call_soon_threadsafe(s.loop.stop)
        except Exception:
            pass
        raise
    return s, False


def close_session(key):
    with SLOCK:
        s = SESSIONS.pop(key, None)
    if s:
        s.close()
    return bool(s)


# ============================== API ==============================
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/static_chat/<path:p>")
def chat_static(p):
    return send_from_directory(STATIC, p)


@app.route("/static/<path:p>")
def shared_static(p):
    return send_from_directory(SHARED, p)


@app.route("/api/bootstrap")
def bootstrap():
    with SLOCK:
        live = [s.info() for s in SESSIONS.values() if s.client is not None]
    return jsonify({"projects": projects(), "models": MODELS,
                    "perm_modes": PERM_MODES, "sessions": live,
                    "max_sessions": MAX_SESSIONS})


@app.route("/api/sessions")
def api_sessions():
    with SLOCK:
        return jsonify([s.info() for s in SESSIONS.values() if s.client is not None])


@app.route("/api/conversations")
def api_conversations():
    return jsonify(conversations(int(request.args.get("limit", 40))))


@app.route("/api/history")
def api_history():
    evs, meta = history(request.args.get("id", ""))
    if evs is None:
        return jsonify({"error": "그런 대화가 없어"}), 404
    return jsonify({"events": evs, **meta})


@app.route("/api/start", methods=["POST"])
def start():
    b = request.get_json(force=True)
    resume = b.get("resume")
    if resume:
        # 대화가 적어둔 폴더를 쓴다. 여기가 틀리면 CLI가 세션을 못 찾는다.
        cwd = resolve_cwd(resume, b.get("cwd") or "")
        if not cwd:
            return jsonify({"error": "이 대화가 어느 폴더에서 열렸는지 알 수 없어 — "
                                     "폴더를 직접 골라서 새 대화로 시작해줘"}), 400
    else:
        cwd = b.get("cwd") or str(Path.home())
        if not Path(cwd).is_dir():
            return jsonify({"error": f"폴더가 없어: {cwd}"}), 400
    try:
        s, already = open_session(cwd, b.get("model") or "claude-opus-5",
                                  b.get("perm") or "default", resume)
    except Exception as ex:
        msg = str(ex)
        if "No conversation found" in msg:
            msg = ("이 대화는 claude가 이어 열지 못해 (기록이 비어있거나 다른 폴더에서 만들어졌어). "
                   "새 대화로 시작해줘")
        else:
            msg = f"{type(ex).__name__}: {ex}"
        return jsonify({"error": msg}), 500
    return jsonify({"ok": True, "already": already, **s.info()})


def _need(b):
    """요청이 가리키는 세션. 없으면 (None, 에러응답)."""
    s = get_session(b.get("key") or "")
    if s is None or s.client is None:
        return None, (jsonify({"error": "그 세션은 닫혀있어", "gone": True}), 400)
    return s, None


@app.route("/api/ask", methods=["POST"])
def ask():
    b = request.get_json(force=True)
    s, err = _need(b)
    if err:
        return err
    text = (b.get("text") or "").strip()
    if not text:
        return jsonify({"error": "빈 메시지"}), 400
    s.ask(text)
    return jsonify({"ok": True})


@app.route("/api/permission", methods=["POST"])
def permission():
    b = request.get_json(force=True)
    s = get_session(b.get("key") or "")
    if s is None:
        return jsonify({"ok": False, "gone": True}), 400
    ok = s.answer_permission(b.get("id"), bool(b.get("allow")), b.get("message", ""))
    return jsonify({"ok": ok})


@app.route("/api/control", methods=["POST"])
def control():
    b = request.get_json(force=True)
    s, err = _need(b)
    if err:
        return err
    try:
        if b.get("model"):
            s.set_model(b["model"])
        if b.get("perm"):
            s.set_perm(b["perm"])
        if b.get("interrupt"):
            s.interrupt()
    except Exception as ex:
        return jsonify({"error": f"{type(ex).__name__}: {ex}"}), 500
    return jsonify({"ok": True})


@app.route("/api/close", methods=["POST"])
def close():
    return jsonify({"ok": close_session(request.get_json(force=True).get("key") or "")})


@sock.route("/ws")
def ws_events(ws):
    s = get_session(request.args.get("key") or "")
    if s is None:
        ws.send(json.dumps({"t": "gone"}))
        return
    q, backlog = s.subscribe()
    try:
        for ev in backlog:
            ws.send(json.dumps(ev))
        while True:
            try:
                ev = q.get(timeout=0.5)
            except queue.Empty:
                if s.closed:
                    break
                continue
            ws.send(json.dumps(ev))
    except Exception:
        pass
    finally:
        s.unsubscribe(q)


def run_server(port):
    from werkzeug.serving import make_server
    make_server("127.0.0.1", port, app, threaded=True).serve_forever()


def main():
    import socket as _s, time, sys
    if "--server" in sys.argv:
        run_server(int(sys.argv[sys.argv.index("--server") + 1]))
        return
    s = _s.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    threading.Thread(target=run_server, args=(port,), daemon=True).start()
    time.sleep(0.5)
    # 작업표시줄에서 pythonw.exe에 묻히지 않게 앱 ID를 따로 준다 (본체와 다른 ID)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hoyoon.HELM.Chat")
    except Exception:
        pass

    import webview
    webview.create_window(WINDOW_TITLE, f"http://127.0.0.1:{port}/",
                          width=1180, height=860, min_size=(820, 560),
                          background_color="#101013")
    webview.start(icon=str(ICON) if ICON.exists() else None)


if __name__ == "__main__":
    main()
