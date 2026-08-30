/* 채팅 프로토타입 프론트.
   서버가 보내는 납작한 이벤트를 그대로 카드로 그린다. */

const $ = (s) => document.querySelector(s);
const toolCards = new Map();   // tool_use_id -> {el, pre}
let permQueue = [];            // 동시에 여러 개 올 수 있어서 줄 세운다
let curPerm = null;
let totalCost = 0;             // 이번 화면에서 누적된 비용
let convs = [];                // 대화 목록
let openId = null;             // 지금 열어둔 대화 id

const PERM_LABEL = {
  default: "매번 확인", acceptEdits: "파일수정 승인", plan: "계획만",
  auto: "자동", dontAsk: "안 물어봄", bypassPermissions: "전체 우회",
};
const PERM_WARN = ["dontAsk", "bypassPermissions"];

// ============================== 부팅 ==============================
async function boot() {
  const b = await (await fetch("/api/bootstrap")).json();

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
  $("#stopBtn").onclick = () => post("/api/control", { interrupt: true });
  $("#model2").onchange = (e) => post("/api/control", { model: e.target.value });
  $("#perm2").onchange = (e) => post("/api/control", { perm: e.target.value });

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
  $("#newChat").onclick = () => {
    openId = null;
    $("#setup").classList.remove("hidden");
    $("#app").classList.add("hidden");
  };

  await loadConvs();
  if (b.started) enterApp(b);
  connect();
}

// ============================== 대화 목록 ==============================
async function loadConvs() {
  convs = await (await fetch("/api/conversations?limit=40")).json();
  renderConvs();
}

function renderConvs() {
  const q = ($("#cfilter").value || "").trim().toLowerCase();
  const list = convs.filter((c) => !q || c.title.toLowerCase().includes(q)
                                     || (c.project || "").toLowerCase().includes(q));
  $("#clist").innerHTML = list.map((c, i) => `
    <div class="conv ${c.id === openId ? "on" : ""}" data-i="${i}">
      <div class="ct">${esc(c.title)}</div>
      <div class="cm">${esc(c.project || "")} · ${ago(c.mtime)}</div>
    </div>`).join("") || `<div class="loading">없음</div>`;
  $("#clist").onclick = (e) => {
    const el = e.target.closest(".conv"); if (!el) return;
    openConv(list[+el.dataset.i]);
  };
}

/** 지난 대화를 말풍선으로 되살리고, 이어서 말할 수 있게 세션을 연다 */
async function openConv(c) {
  openId = c.id;
  renderConvs();
  $("#setup").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#feed").innerHTML = `<div class="loading">불러오는 중…</div>`;
  $("#ctx").textContent = c.title;

  const h = await (await fetch(`/api/history?id=${encodeURIComponent(c.id)}`)).json();
  if (h.error) { $("#feed").innerHTML = ""; sys(h.error, "bad"); return; }
  $("#feed").innerHTML = "";
  if (h.trimmed) sys(`이전 내용 ${h.total - h.events.length}개는 생략했어 (뒤쪽만 표시)`);
  for (const ev of h.events) handle({ ...ev, replay: true });
  sys("여기서부터 이어서 말하면 돼");

  // 이어서 대화하려면 이 대화 id로 세션을 연다
  const r = await post("/api/start", { cwd: c.cwd, resume: c.id,
                                       model: $("#model").value, perm: $("#perm").value });
  if (r.error && !r.error.includes("이미")) sys(r.error, "bad");
  $("#input").focus();
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
  enterApp(body);
  loadConvs();
}

function enterApp(b) {
  $("#setup").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#ctx").textContent = `${b.cwd}`;
  if (b.model) $("#model2").value = b.model;
  if (b.perm) $("#perm2").value = b.perm;
  $("#input").focus();
}

async function send() {
  const ta = $("#input");
  const text = ta.value.trim();
  if (!text) return;
  ta.value = ""; ta.style.height = "auto";
  const r = await post("/api/ask", { text });
  if (r.error) sys(r.error, "bad");
}

// ============================== 이벤트 수신 ==============================
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1500);
}

function handle(ev) {
  switch (ev.t) {
    case "ready":
      enterApp(ev);
      sys(ev.resumed ? "이전 대화를 이어서 열었어" : "세션이 열렸어");
      break;
    case "user":     bubble(ev.text, "me"); break;
    case "delta_start": startLive(); break;
    case "delta":       pushLive(ev.text); break;
    case "text":
      // 흘려 그리던 조각이 있으면 완성본(마크다운)으로 갈아끼운다
      if (live) { live.innerHTML = md(ev.text); live = null; }
      else bubble(ev.text, "bot");
      if (!ev.replay) working("답하는 중");
      break;
    case "thinking":
      thinking(ev.text);
      if (!ev.replay) working("생각하는 중");
      break;
    case "tool_use":
      toolCard(ev);
      if (!ev.replay) working(`${toolVerb(ev.name)} · ${peek(ev.input)}`);
      break;
    case "tool_result": toolResult(ev); break;
    case "permission": permQueue.push(ev); pumpPerm(); break;
    case "permission_done":
      markTool(ev.id, ev.allow ? "허용됨" : "거절됨", ev.allow ? "ok" : "no");
      break;
    case "busy":
      $("#statusDot").classList.toggle("busy", ev.on);
      $("#send").disabled = ev.on;
      setWorking(ev.on);
      if (!ev.on && live) { live.classList.remove("streaming"); live = null; }
      break;
    case "result":
      sys(`끝 · ${ev.turns}턴 · ${(ev.ms / 1000).toFixed(1)}초`
          + (ev.cost ? ` · $${ev.cost.toFixed(4)}` : ""), ev.is_error ? "bad" : "");
      if (!ev.replay) totalCost += ev.cost || 0;
      updateCost();
      break;
    case "ratelimit": rateLimit(ev.info); break;
    case "note":  sys(ev.text); break;
    case "error": sys(ev.text, "bad"); break;
  }
  const f = $("#feed");
  f.scrollTop = f.scrollHeight;
}

function ago(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}분 전`;
  if (d < 86400) return `${Math.floor(d / 3600)}시간 전`;
  return `${Math.floor(d / 86400)}일 전`;
}

function updateCost() {
  $("#cost").textContent = totalCost ? `$${totalCost.toFixed(4)}` : "";
}

// ============================== 작동중 표시 ==============================
const TOOL_VERB = {
  Read: "읽는 중", Write: "쓰는 중", Edit: "고치는 중", Bash: "실행 중",
  Glob: "찾는 중", Grep: "훑는 중", WebFetch: "가져오는 중", WebSearch: "검색 중",
  Task: "맡기는 중", TodoWrite: "정리 중", NotebookEdit: "고치는 중",
};
const toolVerb = (n) => TOOL_VERB[n] || `${n} 실행 중`;

let workTimer = null, workStart = 0;

function setWorking(on) {
  const el = $("#working");
  if (on) {
    workStart = Date.now();
    el.classList.remove("hidden");
    working("시작하는 중");
    clearInterval(workTimer);
    workTimer = setInterval(() => {
      const s = Math.floor((Date.now() - workStart) / 1000);
      $("#workSec").textContent = s < 60 ? `${s}초`
        : `${Math.floor(s / 60)}분 ${s % 60}초`;
    }, 1000);
  } else {
    clearInterval(workTimer);
    workTimer = null;
    el.classList.add("hidden");
  }
}

function working(what) {
  $("#workWhat").textContent = what;
}

// ============================== 그리기 ==============================
function add(el) { $("#feed").appendChild(el); return el; }

/** 내 답변은 마크다운이라 해석해서 그린다. 안 그리면 **굵게** 같은 기호가 그대로 보인다.
 *  모델이 뱉은 HTML이 섞일 수 있으니 DOMPurify로 한 번 거른다. */
function md(text) {
  try {
    return DOMPurify.sanitize(marked.parse(String(text), { breaks: true, gfm: true }));
  } catch (_) {
    return esc(text);
  }
}

let live = null;   // 지금 글자가 흘러들어가는 말풍선

function startLive() {
  live = document.createElement("div");
  live.className = "msg bot streaming";
  add(live);
}

function pushLive(chunk) {
  if (!live) startLive();
  live.textContent += chunk;          // 흘리는 동안은 날것 그대로 (마크다운은 완성 후)
  const f = $("#feed");
  f.scrollTop = f.scrollHeight;
}

function bubble(text, cls) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  if (cls === "bot") d.innerHTML = md(text);   // 사용자가 친 말은 그대로 둔다
  else d.textContent = text;
  add(d);
}

function thinking(text) {
  const d = document.createElement("div");
  d.className = "think folded";
  d.innerHTML = `<span class="tw">생각</span><span class="tb"></span>`;
  d.querySelector(".tb").textContent = text;
  d.onclick = () => d.classList.toggle("folded");
  add(d);
}

function sys(text, cls = "") {
  const d = document.createElement("div");
  d.className = "sys " + cls;
  d.textContent = text;
  add(d);
}

function toolCard(ev) {
  const d = document.createElement("div");
  d.className = "tool";
  d.className = "tool folded";
  d.innerHTML = `<div class="th"><span class="caret">▸</span>
      <span class="nm">${esc(ev.name)}</span>
      <span class="peek">${esc(peek(ev.input))}</span>
      <span class="spacer"></span><span class="badge">대기</span></div>
    <div class="tbody"><pre>${esc(pretty(ev.input))}</pre></div>`;
  d.querySelector(".th").onclick = () => d.classList.toggle("folded");
  add(d);
  toolCards.set(ev.id, d);
}

function markTool(id, label, cls) {
  const d = toolCards.get(id);
  if (!d) return;
  const b = d.querySelector(".badge");
  b.textContent = label;
  b.className = "badge " + cls;
}

function toolResult(ev) {
  markTool(ev.id, ev.is_error ? "실패" : "완료", ev.is_error ? "no" : "ok");
  const d = toolCards.get(ev.id);
  const body = pretty(ev.content);
  const pre = document.createElement("pre");
  pre.textContent = body.length > 4000 ? body.slice(0, 4000) + "\n… (생략)" : body;
  (d || add(document.createElement("div"))).appendChild(pre);
}

function rateLimit(info) {
  const bits = [];
  for (const [k, v] of Object.entries(info || {})) {
    if (v !== null && v !== undefined && typeof v !== "object") bits.push(`${k}=${v}`);
  }
  sys("사용 한도: " + (bits.join(" · ") || "정보 없음"), "warn");
}

// ============================== 권한 확인 ==============================
function pumpPerm() {
  if (curPerm || !permQueue.length) return;
  curPerm = permQueue.shift();
  $("#pmTool").textContent = curPerm.title || curPerm.tool;
  $("#pmInput").textContent = pretty(curPerm.input);
  $("#pmWhy").textContent = curPerm.description || curPerm.reason || "";
  $("#pmMsg").value = "";
  $("#permModal").classList.remove("hidden");
  $("#pmAllow").focus();
}

async function answerPerm(allow) {
  if (!curPerm) return;
  const id = curPerm.id;
  const message = $("#pmMsg").value.trim();
  $("#permModal").classList.add("hidden");
  curPerm = null;
  await post("/api/permission", { id, allow, message });
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
