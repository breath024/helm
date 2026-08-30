/* 채팅 프로토타입 프론트.
   서버가 보내는 납작한 이벤트를 그대로 카드로 그린다.
   세션은 여러 개가 동시에 살아있고, 화면만 갈아탄다 — 다른 대화를 눌러도
   쓰던 세션은 안 죽는다(예전엔 하나뿐이라 서로 밀어냈다). */

const $ = (s) => document.querySelector(s);

const S = new Map();     // key -> 세션 상태 (DOM·소켓·비용까지 세션마다 따로)
let active = null;       // 지금 보고 있는 세션 key
let convs = [];          // 지난 대화 목록
let permQueue = [];      // 권한 요청 (배경 세션 것도 온다)
let curPerm = null;
let maxSessions = 6;

const PERM_LABEL = {
  default: "매번 확인", acceptEdits: "파일수정 승인", plan: "계획만",
  auto: "자동", dontAsk: "안 물어봄", bypassPermissions: "전체 우회",
};
const PERM_WARN = ["dontAsk", "bypassPermissions"];

// ============================== 부팅 ==============================
async function boot() {
  const b = await (await fetch("/api/bootstrap")).json();
  maxSessions = b.max_sessions || 6;

  $("#proj").innerHTML = b.projects
    .map((p) => `<option value="${esc(p.path)}">${esc(p.name)}</option>`).join("");
  const modelOpts = b.models.map((m) => `<option value="${m}">${m}</option>`).join("");
  const permOpts = b.perm_modes
    .map((p) => `<option value="${p}">${PERM_LABEL[p] || p} · ${p}</option>`).join("");
  $("#model").innerHTML = modelOpts; $("#model2").innerHTML = modelOpts;
  $("#perm").innerHTML = permOpts;   $("#perm2").innerHTML = permOpts;

  $("#perm").onchange = syncPermNote;
  syncPermNote();

  $("#startBtn").onclick = start;
  $("#send").onclick = send;
  $("#stopBtn").onclick = () => post("/api/control", { key: active, interrupt: true });
  $("#closeBtn").onclick = () => closeSess(active);
  $("#model2").onchange = (e) => post("/api/control", { key: active, model: e.target.value });
  $("#perm2").onchange = (e) => post("/api/control", { key: active, perm: e.target.value });

  const ta = $("#input");
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  });

  $("#pmAllow").onclick = () => answerPerm(true);
  $("#pmDeny").onclick = () => answerPerm(false);

  $("#cfilter").oninput = renderConvs;
  $("#newChat").onclick = showSetup;
  $("#setupBack").onclick = () => { if (S.size) showApp(); };

  await loadConvs();
  // 새로고침해도 살아있는 세션들을 그대로 되살린다
  for (const info of b.sessions || []) await adopt(info);
  if (S.size) activate([...S.keys()][0]); else showSetup();
}

function showSetup() {
  $("#setup").classList.remove("hidden");
  $("#app").classList.add("hidden");
  $("#setupBack").classList.toggle("hidden", S.size === 0);
  $("#setupErr").textContent = S.size >= maxSessions
    ? `세션이 이미 ${maxSessions}개 열려있어 — 하나 닫고 열어줘` : "";
}
function showApp() {
  $("#setup").classList.add("hidden");
  $("#app").classList.remove("hidden");
}

// ============================== 세션 ==============================
function mkSess(info) {
  const feed = document.createElement("div");
  feed.className = "fs hidden";
  // 과거 기록과 실시간을 나눠 담는다. 소켓이 끊겼다 붙으면 서버가 기록을 통째로
  // 다시 보내주므로, 실시간 칸만 비우고 다시 그리면 중복 없이 복구된다.
  const hist = document.createElement("div");
  const now = document.createElement("div");
  feed.append(hist, now);
  $("#feed").appendChild(feed);
  const s = {
    key: info.key, cwd: info.cwd, model: info.model, perm: info.perm,
    conv: info.conv, title: info.title || "", cost: info.cost || 0,
    busy: false, feed, hist, now, tools: new Map(), live: null, ws: null,
    workWhat: "", workStart: 0, dead: false, limit: info.limit || null,
  };
  S.set(s.key, s);
  connect(s);
  renderConvs();
  return s;
}

/** 서버에 이미 있던 세션을 화면으로 되살린다 (새로고침 대응) */
async function adopt(info) {
  const s = mkSess(info);
  if (info.conv) await renderHistory(s, info.conv, true);
  return s;
}

function activate(key) {
  const s = S.get(key);
  if (!s) return;
  active = key;
  showApp();
  for (const o of S.values()) o.feed.classList.toggle("hidden", o !== s);
  $("#ctx").textContent = s.title || s.cwd || "";
  if (s.model) $("#model2").value = s.model;
  if (s.perm) $("#perm2").value = s.perm;
  $("#statusDot").classList.toggle("busy", s.busy);
  $("#send").disabled = s.busy || s.dead;
  paintWorking(s);
  paintCost(s);
  renderConvs();
  $("#feed").scrollTop = $("#feed").scrollHeight;
  $("#input").focus();
}

async function closeSess(key) {
  const s = S.get(key);
  if (!s) return;
  // 소켓부터 우리 쪽에서 정리한다. 서버 응답을 기다리는 사이에 서버가 먼저
  // 끊으면 onclose가 재접속을 걸어버리고, 브라우저 콘솔에 프레임 에러가 남는다.
  S.delete(key);
  if (s.ws) { s.ws._bye = true; try { s.ws.close(); } catch (_) {} }
  s.feed.remove();
  await post("/api/close", { key });
  const next = [...S.keys()][0];
  if (next) activate(next); else { active = null; showSetup(); }
  renderConvs();
}

function connect(s) {
  const ws = new WebSocket(`ws://${location.host}/ws?key=${encodeURIComponent(s.key)}`);
  s.ws = ws;
  ws.onopen = () => {          // 서버가 기록을 처음부터 다시 보낸다 → 실시간 칸 비우기
    s.now.innerHTML = "";
    s.tools.clear();
    s.live = null;
  };
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.t === "gone") { ws._bye = true; markDead(s); return; }
    handle(s, ev);
  };
  ws.onclose = () => { if (!ws._bye && S.has(s.key)) setTimeout(() => connect(s), 1500); };
}

function markDead(s) {
  s.dead = true;
  sys(s, "이 세션은 닫혔어", "bad");
  if (s.key === active) $("#send").disabled = true;
  renderConvs();
}

// ============================== 대화 목록 ==============================
async function loadConvs() {
  convs = await (await fetch("/api/conversations?limit=40")).json();
  renderConvs();
}

/** 위: 지금 열려있는 세션 / 아래: 지난 대화 */
function renderConvs() {
  const q = ($("#cfilter").value || "").trim().toLowerCase();
  const liveConvs = new Set([...S.values()].map((s) => s.conv).filter(Boolean));

  const openRows = [...S.values()].map((s) => {
    const label = s.title || (s.cwd || "").split(/[\\/]/).pop() || "새 대화";
    return `<div class="conv live ${s.key === active ? "on" : ""}" data-k="${esc(s.key)}">
        <div class="ct"><span class="ldot ${s.busy ? "busy" : ""}"></span>${esc(label)}</div>
        <div class="cm">${esc((s.cwd || "").split(/[\\/]/).pop())}${s.cost ? ` · $${s.cost.toFixed(4)}` : ""}</div>
        <button class="x" data-close="${esc(s.key)}" title="세션 닫기">✕</button>
      </div>`;
  }).join("");

  const list = convs.filter((c) => !liveConvs.has(c.id))
    .filter((c) => !q || c.title.toLowerCase().includes(q)
                     || (c.project || "").toLowerCase().includes(q));
  const pastRows = list.map((c, i) => `
    <div class="conv" data-i="${i}">
      <div class="ct">${esc(c.title)}</div>
      <div class="cm">${esc(c.project || "")} · ${ago(c.mtime)}</div>
    </div>`).join("") || `<div class="loading">없음</div>`;

  $("#clist").innerHTML =
    (openRows ? `<div class="ghead">열려있음 ${S.size}/${maxSessions}</div>${openRows}` : "")
    + `<div class="ghead">지난 대화</div>${pastRows}`;

  $("#clist").onclick = (e) => {
    const x = e.target.closest("[data-close]");
    if (x) { e.stopPropagation(); closeSess(x.dataset.close); return; }
    const el = e.target.closest(".conv"); if (!el) return;
    if (el.dataset.k) activate(el.dataset.k);
    else openConv(list[+el.dataset.i]);
  };
}

/** 지난 대화를 말풍선으로 되살리고, 이어서 말할 수 있게 세션을 연다 */
async function openConv(c) {
  if (S.size >= maxSessions) { showSetup(); return; }
  const r = await post("/api/start", { cwd: c.cwd, resume: c.id,
                                       model: $("#model").value, perm: $("#perm").value });
  if (r.error) {
    showApp();
    if (active) sys(S.get(active), r.error, "bad");
    else { $("#setupErr").textContent = r.error; showSetup(); }
    return;
  }
  if (S.has(r.key)) { activate(r.key); return; }       // 이미 열려있던 대화
  const s = mkSess({ ...r, title: c.title, conv: c.id });
  activate(r.key);
  await renderHistory(s, c.id, false);
}

/** 지난 기록은 과거 칸에만 그린다 (실시간 칸은 소켓이 채운다) */
async function renderHistory(s, convId, quiet) {
  const load = document.createElement("div");
  load.className = "loading"; load.textContent = "불러오는 중…";
  s.hist.appendChild(load);
  const h = await (await fetch(`/api/history?id=${encodeURIComponent(convId)}`)).json();
  load.remove();
  if (h.error) { if (!quiet) sys(s, h.error, "bad"); return; }

  const keep = s.now;
  s.now = s.hist;                      // 그리는 곳을 잠깐 과거 칸으로 돌린다
  s.hist.innerHTML = "";
  if (h.trimmed) sys(s, `이전 내용 ${h.total - h.events.length}개는 생략했어 (뒤쪽만 표시)`);
  for (const ev of h.events) handle(s, { ...ev, replay: true });
  sys(s, "여기서부터 이어서 말하면 돼");
  s.now = keep;
  if (s.key === active) $("#feed").scrollTop = $("#feed").scrollHeight;
}

function syncPermNote() {
  const v = $("#perm").value;
  const warn = PERM_WARN.includes(v);
  $("#permNote").classList.toggle("warn", warn);
  $("#permNote").textContent = warn
    ? "확인창 없이 파일을 고칩니다."
    : "툴을 쓸 때마다 확인창이 뜹니다.";
}

async function start() {
  $("#setupErr").textContent = "";
  $("#startBtn").disabled = true;
  $("#startBtn").textContent = "여는 중…";
  const body = { cwd: $("#proj").value, model: $("#model").value, perm: $("#perm").value };
  const r = await post("/api/start", body);
  $("#startBtn").disabled = false;
  $("#startBtn").textContent = "세션 시작";
  if (r.error) { $("#setupErr").textContent = r.error; return; }
  mkSess(r);
  activate(r.key);
  loadConvs();
}

async function send() {
  const s = S.get(active);
  if (!s) return;
  const ta = $("#input");
  const text = ta.value.trim();
  if (!text) return;
  ta.value = ""; ta.style.height = "auto";
  if (!s.title) { s.title = text.slice(0, 40); $("#ctx").textContent = s.title; renderConvs(); }
  const r = await post("/api/ask", { key: s.key, text });
  if (r.error) { sys(s, r.error, "bad"); if (r.gone) markDead(s); }
}

// ============================== 이벤트 수신 ==============================
function handle(s, ev) {
  switch (ev.t) {
    case "ready":
      s.cwd = ev.cwd; s.model = ev.model; s.perm = ev.perm;
      if (s.key === active) activate(s.key);
      sys(s, ev.resumed ? "이전 대화를 이어서 열었어" : "세션이 열렸어");
      break;
    case "closed": markDead(s); break;
    case "user":     bubble(s, ev.text, "me"); break;
    case "delta_start": startLive(s); break;
    case "delta":       pushLive(s, ev.text); break;
    case "text":
      // 흘려 그리던 조각이 있으면 완성본(마크다운)으로 갈아끼운다.
      // 이때 streaming 클래스를 반드시 떼야 한다 — 안 떼면 답변 끝에 커서가
      // 계속 깜빡이고 pre-wrap이 걸린 채라 목록·줄바꿈이 벌어져 보인다.
      if (s.live) {
        s.live.className = "msg bot";
        s.live.innerHTML = md(ev.text);
        s.live = null;
      } else bubble(s, ev.text, "bot");
      if (!ev.replay) working(s, "답하는 중");
      break;
    case "thinking":
      finishLive(s);
      thinking(s, ev.text);
      if (!ev.replay) working(s, "생각하는 중");
      break;
    case "tool_use":
      finishLive(s);
      toolCard(s, ev);
      if (!ev.replay) working(s, `${toolVerb(ev.name)} · ${peek(ev.input)}`);
      break;
    case "tool_result": toolResult(s, ev); break;
    case "permission": permQueue.push({ s, ev }); pumpPerm(); break;
    case "permission_done":
      markTool(s, ev.id, ev.allow ? "허용됨" : "거절됨", ev.allow ? "ok" : "no");
      break;
    case "busy":
      s.busy = ev.on;
      if (!ev.on) finishLive(s);
      if (ev.on) { s.workStart = Date.now(); s.workWhat = "시작하는 중"; }
      if (s.key === active) {
        $("#statusDot").classList.toggle("busy", ev.on);
        $("#send").disabled = ev.on || s.dead;
        paintWorking(s);
      }
      renderConvs();
      break;
    case "result": {
      const bits = [`끝 · ${ev.turns}턴 · ${(ev.ms / 1000).toFixed(1)}초`];
      if (ev.cost) bits.push(`이번 $${ev.cost.toFixed(4)}`);
      if (ev.cost_total) bits.push(`누적 $${ev.cost_total.toFixed(4)}`);
      if (ev.tok_out) bits.push(`${fmtTok(ev.tok_in, ev.tok_out, ev.tok_cache)}`);
      sys(s, bits.join(" · "), ev.is_error ? "bad" : "");
      if (typeof ev.cost_total === "number") s.cost = ev.cost_total;
      if (s.key === active) paintCost(s);
      renderConvs();
      break;
    }
    case "ratelimit": rateLimit(s, ev); break;
    case "note":  sys(s, ev.text); break;
    case "error": sys(s, ev.text, "bad"); break;
  }
  if (s.key === active) { const f = $("#feed"); f.scrollTop = f.scrollHeight; }
}

function ago(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}분 전`;
  if (d < 86400) return `${Math.floor(d / 3600)}시간 전`;
  return `${Math.floor(d / 86400)}일 전`;
}

const fmtTok = (i, o, c) => {
  const k = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
  return `토큰 ${k(i || 0)}→${k(o || 0)}${c ? ` (캐시 ${k(c)})` : ""}`;
};

function paintCost(s) {
  $("#cost").textContent = s.cost ? `$${s.cost.toFixed(4)}` : "";
}

// ============================== 작동중 표시 ==============================
const TOOL_VERB = {
  Read: "읽는 중", Write: "쓰는 중", Edit: "고치는 중", Bash: "실행 중",
  Glob: "찾는 중", Grep: "훑는 중", WebFetch: "가져오는 중", WebSearch: "검색 중",
  Task: "맡기는 중", TodoWrite: "정리 중", NotebookEdit: "고치는 중",
};
const toolVerb = (n) => TOOL_VERB[n] || `${n} 실행 중`;

let workTimer = null;

function working(s, what) {
  s.workWhat = what;
  if (s.key === active) $("#workWhat").textContent = what;
}

/** 배너는 지금 보고 있는 세션 것만 그린다 */
function paintWorking(s) {
  const el = $("#working");
  clearInterval(workTimer); workTimer = null;
  if (!s.busy) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  $("#workWhat").textContent = s.workWhat || "작동 중";
  const tick = () => {
    const sec = Math.floor((Date.now() - s.workStart) / 1000);
    $("#workSec").textContent = sec < 60 ? `${sec}초`
      : `${Math.floor(sec / 60)}분 ${sec % 60}초`;
  };
  tick();
  workTimer = setInterval(tick, 1000);
}

// ============================== 그리기 ==============================
function add(s, el) { s.now.appendChild(el); return el; }

/** 내 답변은 마크다운이라 해석해서 그린다. 안 그리면 **굵게** 같은 기호가 그대로 보인다.
 *  모델이 뱉은 HTML이 섞일 수 있으니 DOMPurify로 한 번 거른다. */
function md(text) {
  try {
    return DOMPurify.sanitize(marked.parse(String(text), { breaks: true, gfm: true }));
  } catch (_) {
    return esc(text);
  }
}

function startLive(s) {
  s.live = document.createElement("div");
  s.live.className = "msg bot streaming";
  add(s, s.live);
}

/** 흘리던 말풍선을 마무리한다. 빈 채로 남으면 커서만 깜빡이는 유령 말풍선이 된다. */
function finishLive(s) {
  if (!s.live) return;
  if (!s.live.textContent.trim()) s.live.remove();
  else s.live.classList.remove("streaming");
  s.live = null;
}

function pushLive(s, chunk) {
  if (!s.live) startLive(s);
  s.live.textContent += chunk;        // 흘리는 동안은 날것 그대로 (마크다운은 완성 후)
  if (s.key === active) { const f = $("#feed"); f.scrollTop = f.scrollHeight; }
}

function bubble(s, text, cls) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  if (cls === "bot") d.innerHTML = md(text);   // 사용자가 친 말은 그대로 둔다
  else d.textContent = text;
  add(s, d);
}

function thinking(s, text) {
  const d = document.createElement("div");
  d.className = "think folded";
  d.innerHTML = `<span class="tw">생각</span><span class="tb"></span>`;
  d.querySelector(".tb").textContent = text;
  d.onclick = () => d.classList.toggle("folded");
  add(s, d);
}

function sys(s, text, cls = "") {
  const d = document.createElement("div");
  d.className = "sys " + cls;
  d.textContent = text;
  add(s, d);
}

function toolCard(s, ev) {
  const d = document.createElement("div");
  d.className = "tool folded";
  d.innerHTML = `<div class="th"><span class="caret">▸</span>
      <span class="nm">${esc(ev.name)}</span>
      <span class="peek">${esc(peek(ev.input))}</span>
      <span class="spacer"></span><span class="badge">대기</span></div>
    <div class="tbody"><pre>${esc(pretty(ev.input))}</pre></div>`;
  d.querySelector(".th").onclick = () => d.classList.toggle("folded");
  add(s, d);
  s.tools.set(ev.id, d);
}

function markTool(s, id, label, cls) {
  const d = s.tools.get(id);
  if (!d) return;
  const b = d.querySelector(".badge");
  b.textContent = label;
  b.className = "badge " + cls;
}

function toolResult(s, ev) {
  markTool(s, ev.id, ev.is_error ? "실패" : "완료", ev.is_error ? "no" : "ok");
  let d = s.tools.get(ev.id);
  if (!d) {
    // 짝이 되는 tool_use가 없는 경우가 있다 — 긴 대화라 앞부분이 잘렸을 때.
    // 그냥 버리면 출력이 사라져 보이므로 빈 카드라도 만들어 붙인다.
    toolCard(s, { id: ev.id, name: "결과", input: "" });
    d = s.tools.get(ev.id);
    markTool(s, ev.id, ev.is_error ? "실패" : "완료", ev.is_error ? "no" : "ok");
  }
  const body = pretty(ev.content);
  const pre = document.createElement("pre");
  pre.textContent = body.length > 4000 ? body.slice(0, 4000) + "\n… (생략)" : body;
  d.appendChild(pre);
}

/** 사용 한도. 예전엔 객체를 문자열로 넘겨서 글자 하나하나가 항목처럼 찍혔다. */
function rateLimit(s, ev) {
  s.limit = ev;
  const pct = typeof ev.utilization === "number"
    ? `${Math.round(ev.utilization * 100)}% 씀` : "";
  const reset = ev.resets_at ? `${new Date(ev.resets_at * 1000)
    .toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 초기화` : "";
  const label = [pct, reset].filter(Boolean).join(" · ");
  if (s.key === active) {
    $("#limit").textContent = label ? `한도 ${label}` : "";
    $("#limit").className = "cost " + (ev.status === "allowed" ? "" : "warnpill");
  }
  if (ev.status && ev.status !== "allowed") {
    const what = ev.status === "rejected" ? "한도에 걸렸어" : "한도에 가까워졌어";
    sys(s, `${what}${label ? ` — ${label}` : ""}`, "warn");
  }
}

// ============================== 권한 확인 ==============================
function pumpPerm() {
  if (curPerm || !permQueue.length) return;
  curPerm = permQueue.shift();
  const { s, ev } = curPerm;
  $("#pmTool").textContent = ev.title || ev.tool;
  $("#pmInput").textContent = pretty(ev.input);
  $("#pmWhy").textContent = ev.description || ev.reason || "";
  // 배경 세션이 물어보는 걸 수도 있으니 어느 세션인지 밝힌다
  $("#pmWho").textContent = (s.title || s.cwd || "") + (s.key === active ? "" : " (다른 세션)");
  $("#pmMsg").value = "";
  $("#permModal").classList.remove("hidden");
  $("#pmAllow").focus();
}

async function answerPerm(allow) {
  if (!curPerm) return;
  const { s, ev } = curPerm;
  const message = $("#pmMsg").value.trim();
  $("#permModal").classList.add("hidden");
  curPerm = null;
  await post("/api/permission", { key: s.key, id: ev.id, allow, message });
  pumpPerm();
}

// ============================== 유틸 ==============================
async function post(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch (e) { return { error: String(e) }; }
}
/** 접힌 상태에서 한 줄로 보여줄 요약 */
function peek(v) {
  const o = (v && typeof v === "object") ? v : {};
  const val = o.file_path || o.path || o.command || o.pattern || o.description
              || (typeof v === "string" ? v : "");
  // 경로면 파일명만. 정규식 escape 사고를 피하려고 lastIndexOf로 자른다
  const s = String(val);
  const cut = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"));
  return s.slice(cut + 1).slice(0, 46);
}

function pretty(v) {
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

boot();
