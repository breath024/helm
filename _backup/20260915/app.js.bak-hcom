/* HELM 프론트 — 패널마다 xterm 1개 + 웹소켓 1개.
   패널의 DOM은 한 번만 만들고, 분할이 바뀌면 슬롯으로 옮겨 끼운다.
   (다시 만들면 화면 내용이 날아가므로) */

const $ = (s) => document.querySelector(s);
const panes = new Map();     // id -> pane
let order = [];              // 탭 순서
let layout = 1;
let slots = [null];
let focusSlot = 0;
let models = [], efforts = [], projects = [], permModes = [];
let curModel = "claude-opus-5", curEffort = "high", curPerm = "manual";
let allSessions = [];
let usage = {};              // 패널 id -> {tokens, limit, pct}

const permOf = (id) => permModes.find((p) => p.id === id);

const modelOf = (id) => models.find((m) => m.id === id);
const takesEffort = (id) => { const m = modelOf(id); return m ? !!m.effort : true; };

/** 같은 계열끼리 묶어 <optgroup>으로 그린다 */
function modelOptions(sel) {
  const fams = [...new Set(models.map((m) => m.family || "기타"))];
  return fams.map((f) => `<optgroup label="${esc(f)}">` + models
    .filter((m) => (m.family || "기타") === f)
    .map((m) => `<option value="${esc(m.id)}" ${m.id === sel ? "selected" : ""}>${esc(m.label)}</option>`)
    .join("") + `</optgroup>`).join("");
}

function effortButtons(sel) {
  return efforts.map((e) => `<button data-e="${e}" class="${e === sel ? "on" : ""}">${e}</button>`).join("");
}

const TERM_THEME = {
  background: "#131318", foreground: "#dcdce4", cursor: "#e0a672",
  cursorAccent: "#131318", selectionBackground: "#3a4a66",
  black: "#2a2a33", red: "#e5766f", green: "#7fc9a0", yellow: "#dfb673",
  blue: "#7fa8dc", magenta: "#bf8fd0", cyan: "#72c0c5", white: "#cdcdd8",
  brightBlack: "#55555f", brightRed: "#f29088", brightGreen: "#9adfb6",
  brightYellow: "#f2cd85", brightBlue: "#9cc0ea", brightMagenta: "#d3a8e2",
  brightCyan: "#8dd6db", brightWhite: "#eeeef5",
};

const TERM_OPTS = {
  theme: TERM_THEME,
  fontFamily: `"JBM","Cascadia Code","Malgun Gothic",Consolas,monospace`,
  fontSize: 13.5,
  lineHeight: 1.35,
  letterSpacing: 0.2,
  cursorBlink: true,
  cursorStyle: "bar",
  cursorWidth: 2,
  scrollback: 8000,
  smoothScrollDuration: 90,
  allowProposedApi: true,
  drawBoldTextInBrightColors: false,
  minimumContrastRatio: 1,
};

// ============================== 부팅 ==============================
async function boot() {
  const b = await (await fetch("/api/bootstrap")).json();
  projects = b.projects; models = b.models; efforts = b.efforts;
  permModes = b.perm_modes || [];
  curModel = b.last_model || "claude-opus-5";
  curEffort = b.last_effort || "high";
  curPerm = b.last_perm_mode || "manual";
  if (!modelOf(curModel)) curModel = models[0].id;   // config에서 지운 모델이 남아있을 때

  $("#proj").innerHTML = projects
    .map((p) => `<option value="${esc(p.path)}">${esc(p.name)}</option>`).join("");
  // 등록 목록에 없는 폴더가 last_project로 저장돼 있으면 select.value가 ""가 되고,
  // 그 빈 값이 새 세션·편집기로 그대로 흘러간다. 안 먹으면 첫 항목으로 되돌린다.
  if (b.last_project) $("#proj").value = b.last_project;
  if (!$("#proj").value) $("#proj").value = projects[0].path;

  // --- 새 세션용 모델/강도 ---
  $("#modelPick").innerHTML = modelOptions(curModel);
  $("#modelPick").onchange = (e) => { curModel = e.target.value; syncEffortEnabled(); };
  $("#effortPick").innerHTML = effortButtons(curEffort);
  $("#effortPick").onclick = (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    curEffort = btn.dataset.e;
    [...$("#effortPick").children].forEach((c) => c.classList.toggle("on", c === btn));
  };
  syncEffortEnabled();

  // --- 권한 모드 (새 세션에만 적용 — 돌고 있는 세션은 TUI에서 Shift+Tab) ---
  $("#permPick").innerHTML = permModes
    .map((p) => `<option value="${esc(p.id)}" ${p.id === curPerm ? "selected" : ""}>
        ${esc(p.label)} · ${esc(p.id)}</option>`).join("");
  $("#permPick").onchange = (e) => { curPerm = e.target.value; syncPermNote(); };
  syncPermNote();

  // --- 지금 보고 있는 세션의 모델/강도 ---
  $("#paneModel").onchange = (e) => applyToPane({ model: e.target.value });
  $("#paneEffort").onclick = (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    applyToPane({ effort: btn.dataset.e });
  };

  $("#newBtn").onclick = () =>
    newPane({ cwd: $("#proj").value, model: curModel, effort: curEffort, perm: curPerm });
  $("#codeBtn").onclick = () => newEditorPane($("#proj").value);
  $("#reloadBtn").onclick = loadSessions;
  $("#filter").oninput = renderSessions;

  $("#layoutPick").onclick = (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    setLayout(+btn.dataset.n);
  };

  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey) return;
    if (e.key >= "1" && e.key <= "9") {
      const p = order[+e.key - 1];
      if (p) { assign(p); e.preventDefault(); }
    }
  });

  await loadSessions();
  render();
  setInterval(pollUsage, 4000);

  $("#limits").onclick = refreshLimits;
  pollLimits();
  scheduleLimits();
}

// ============================== 구독 한도 ==============================
// 서버가 안 보이는 세션으로 /usage 화면을 긁어 캐시해둔다(10분마다).
// 처음엔 긁는 데 15초쯤 걸리므로 붙을 때까지만 자주 물어본다.
let limits = null;

async function pollLimits() {
  try {
    limits = await (await fetch("/api/limits")).json();
  } catch (_) {
    return;
  }
  paintLimits();
}

function scheduleLimits() {
  setTimeout(async () => {
    await pollLimits();
    scheduleLimits();
  }, limits && limits.ok ? 60000 : 6000);
}

async function refreshLimits() {
  $("#limits").classList.add("busy");
  try { await fetch("/api/limits?refresh=1"); } catch (_) {}
  for (const wait of [8000, 8000, 8000]) {
    await new Promise((r) => setTimeout(r, wait));
    await pollLimits();
  }
  $("#limits").classList.remove("busy");
}

function limitPart(name, d) {
  if (!d || typeof d.pct !== "number") return "";
  const c = d.pct >= 90 ? "hot" : d.pct >= 70 ? "mid" : "";
  return `<span class="lname">${name}</span>
    <span class="ubar ${c}"><i style="width:${Math.min(100, d.pct)}%"></i></span>
    <span class="lpct ${c}">${d.pct}%</span>`;
}

function paintLimits() {
  const el = $("#limits");
  const L = limits;
  if (!L || !L.ok || !(L.session || L.week)) { el.className = "limits hidden"; return; }
  el.className = "limits";
  el.innerHTML = [limitPart("5시간", L.session), limitPart("주간", L.week),
                  limitPart("Opus", L.opus)].filter(Boolean).join(`<span class="sep">·</span>`);
  const line = (n, d) => (d && typeof d.pct === "number")
    ? `${n} ${d.pct}%${d.resets ? ` · ${d.resets} 리셋` : ""}\n` : "";
  el.title = line("5시간", L.session) + line("주간", L.week) + line("Opus", L.opus)
    + (L.note ? L.note + "\n" : "")
    + `이 기기의 로컬 세션 기준 근사치 (다른 기기·claude.ai 사용분은 빠짐)\n`
    + `${L.age != null ? Math.floor(L.age / 60) + "분 전 확인 · " : ""}클릭하면 새로고침`;
}

// ============================== 컨텍스트 사용량 ==============================
// 서버가 세션 jsonl의 마지막 usage를 읽어준다. 첫 응답 전에는 잴 게 없어서 숨긴다.
async function pollUsage() {
  if (![...panes.values()].some((p) => p.kind === "term")) return;
  try {
    usage = await (await fetch("/api/usage")).json();
  } catch (_) {
    return;
  }
  paintUsage();
}

function paintUsage() {
  const el = $("#usage");
  const p = panes.get(slots[focusSlot]);
  const u = p && p.kind === "term" ? usage[p.id] : null;
  if (!u || !u.tokens) { el.className = "usage hidden"; return; }
  const pct = Math.min(100, u.pct);
  el.classList.remove("hidden");
  el.classList.toggle("mid", pct >= 70 && pct < 90);
  el.classList.toggle("hot", pct >= 90);
  el.querySelector("i").style.width = pct + "%";
  el.querySelector(".utext").textContent =
    `${u.pct}% · ${num(u.tokens)} / ${num(u.limit)}`;
  el.title = `이 세션이 물고 있는 컨텍스트 — ${num(u.tokens)} / ${num(u.limit)} 토큰`
           + `\n(마지막 응답 기준. 압축하면 다시 내려간다)`;
}

/** 강도 없는 모델(Haiku 4.5 등)이면 강도 칸을 흐리게 잠근다 */
function syncEffortEnabled() {
  const on = takesEffort(curModel);
  $("#effortPick").classList.toggle("dim", !on);
  $("#effortLbl").textContent = on ? "강도" : "강도 (이 모델은 없음)";
}

/** 위험한 모드면 경고를 띄운다. 돌고 있는 세션엔 적용 안 된다는 것도 여기서 알린다. */
function syncPermNote() {
  const p = permOf(curPerm);
  const el = $("#permNote");
  el.classList.toggle("warn", !!(p && p.warn));
  el.textContent = p && p.warn
    ? "확인 없이 파일을 고칩니다. 새 세션부터 적용."
    : "새 세션부터 적용 (실행 중인 세션은 Shift+Tab).";
}

async function applyToPane({ model, effort }) {
  const p = panes.get(slots[focusSlot]); if (!p) return;
  const r = await (await fetch(`/api/panes/${p.id}/model`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, effort }),
  })).json();
  if (model) { p.model = model; if (!takesEffort(model)) p.effort = null; }
  if (r.effort) p.effort = r.effort;
  render();
}

// ============================== 최근 작업 ==============================
async function loadSessions() {
  allSessions = await (await fetch("/api/sessions?limit=60")).json();
  renderSessions();
}

function renderSessions() {
  const q = $("#filter").value.trim().toLowerCase();
  const list = allSessions.filter((s) =>
    !q || s.title.toLowerCase().includes(q) || (s.project || "").toLowerCase().includes(q));
  if (!list.length) { $("#sessions").innerHTML = `<div class="empty">없음</div>`; return; }
  $("#sessions").innerHTML = list.map((s, i) => `
    <div class="item" data-i="${i}">
      <div class="t">${esc(s.title)}</div>
      <div class="m">
        <span class="p">${esc(s.project || "")}</span>
        <span>${ago(s.mtime)}</span>
        ${s.model ? `<span>${esc(s.model)}</span>` : ""}
      </div>
    </div>`).join("");
  $("#sessions").onclick = (e) => {
    const el = e.target.closest(".item"); if (!el) return;
    const s = list[+el.dataset.i];
    newPane({ cwd: s.cwd || $("#proj").value, model: curModel, effort: curEffort,
              perm: curPerm, resume: s.id, title: s.title });
  };
}

// ============================== 클립보드 ==============================
// WebView2 창 안에서는 navigator.clipboard 읽기가 권한 요청에 막혀 조용히 실패한다.
// 그래서 브라우저 API를 먼저 시도하고, 안 되면 서버(윈도우 클립보드)로 넘어간다.
async function clipRead() {
  try {
    const t = await navigator.clipboard.readText();
    if (t) return t;
  } catch (_) {}
  try {
    const d = await (await fetch("/api/clipboard")).json();
    return d.text || "";
  } catch (_) { return ""; }
}

async function clipWrite(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (_) {}
  try {
    await fetch("/api/clipboard", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (_) {}
}

// 터미널 붙여넣기/복사 키. true를 돌려주면 xterm이 평소대로 처리한다.
function termClipboardKeys(term) {
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== "keydown") return true;
    const k = (e.key || "").toLowerCase();
    // Ctrl+V / Ctrl+Shift+V / Shift+Insert = 붙여넣기
    if ((e.ctrlKey && k === "v") || (e.shiftKey && k === "insert")) {
      e.preventDefault();
      clipRead().then((t) => { if (t) term.paste(t); });   // 괄호 붙여넣기 처리는 xterm이 함
      return false;
    }
    // Ctrl+Shift+C = 선택 복사 (Ctrl+C는 그대로 중단 신호로 둔다)
    if (e.ctrlKey && e.shiftKey && k === "c") {
      e.preventDefault();
      clipWrite(term.getSelection());
      return false;
    }
    return true;
  });
}

// ============================== 패널 ==============================
async function newPane(opt) {
  const res = await fetch("/api/panes", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...opt, cols: 120, rows: 30 }),
  });
  const d = await res.json();
  if (d.error) { alert(d.error); return; }

  const wrap = document.createElement("div");
  wrap.className = "term";
  const term = new Terminal(TERM_OPTS);
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(wrap);
  try { term.loadAddon(new WebglAddon.WebglAddon()); } catch (_) {}
  termClipboardKeys(term);

  const pane = { ...d, kind: "term", term, fit, el: wrap, alive: true, ws: null,
                 title: opt.title || d.title, resumed: d.resumed };
  panes.set(d.id, pane);
  order.push(d.id);

  const ws = new WebSocket(`ws://${location.host}/ws/${d.id}`);
  pane.ws = ws;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "o") term.write(m.d);
  };
  ws.onclose = () => { pane.alive = false; render(); };
  term.onData((s) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: "i", d: s })); });

  new ResizeObserver(() => sizePane(pane)).observe(wrap);

  assign(d.id);
  setTimeout(loadSessions, 4000);   // 새 세션 파일이 목록에 잡히도록
}

// ============================== 코드 편집기 패널 ==============================
const CM_MODE = {
  py: "python", js: "javascript", jsx: "javascript", ts: "javascript",
  tsx: "javascript", json: { name: "javascript", json: true },
  html: "htmlmixed", htm: "htmlmixed", xml: "xml", css: "css",
  md: "markdown", sh: "shell", ps1: "shell", bat: "shell",
  c: "text/x-csrc", h: "text/x-csrc", cpp: "text/x-c++src",
  cs: "text/x-csharp", java: "text/x-java",
};
const modeOf = (name) => CM_MODE[(name.split(".").pop() || "").toLowerCase()] || null;

let _editSeq = 0;

function newEditorPane(cwd) {
  const id = "e" + ++_editSeq;
  const wrap = document.createElement("div");
  wrap.className = "edit-body";
  wrap.innerHTML = `<div class="edit-tree"></div><div class="edit-pane"></div>`;

  const cm = CodeMirror(wrap.querySelector(".edit-pane"), {
    value: "", theme: "material-darker", lineNumbers: true,
    autoCloseBrackets: true, matchBrackets: true, styleActiveLine: true,
    indentUnit: 4, tabSize: 4, lineWrapping: false,
  });

  const pane = { id, kind: "edit", title: "코드", cwd, el: wrap, cm, alive: true,
                 file: null, dirty: false, savedAt: "", treeAt: cwd };

  cm.on("change", () => {
    if (!pane.dirty && pane.file) { pane.dirty = true; render(); }
  });
  cm.setOption("extraKeys", {
    "Ctrl-S": () => saveFile(pane),
    "Cmd-S": () => saveFile(pane),
    "Ctrl-V": (c) => clipRead().then((t) => { if (t) c.replaceSelection(t); }),
    "Ctrl-C": (c) => clipWrite(c.getSelection()),
    "Ctrl-X": (c) => { clipWrite(c.getSelection()); c.replaceSelection(""); },
  });

  panes.set(id, pane);
  order.push(id);
  new ResizeObserver(() => sizePane(pane)).observe(wrap);
  loadTree(pane, cwd);
  assign(id);
  return pane;
}

async function loadTree(pane, path) {
  const r = await (await fetch(`/api/tree?path=${encodeURIComponent(path)}`)).json();
  if (r.error) { alert(r.error); return; }
  pane.treeAt = r.path;
  const el = pane.el.querySelector(".edit-tree");
  el.innerHTML =
    (r.parent ? `<div class="tnode d" data-up="${esc(r.parent)}">.. 상위</div>` : "") +
    r.entries.map((e) =>
      `<div class="tnode ${e.dir ? "d" : ""} ${pane.file === e.path ? "sel" : ""}"
            data-${e.dir ? "dir" : "file"}="${esc(e.path)}"
            title="${esc(e.name)}">${e.dir ? "▸ " : ""}${esc(e.name)}</div>`).join("");
  el.onclick = (ev) => {
    const n = ev.target.closest(".tnode"); if (!n) return;
    if (n.dataset.up || n.dataset.dir) loadTree(pane, n.dataset.up || n.dataset.dir);
    else openFile(pane, n.dataset.file);
  };
}

async function openFile(pane, path) {
  if (pane.dirty && !confirm("저장 안 된 변경이 있어. 버리고 열까?")) return;
  const r = await (await fetch(`/api/file?path=${encodeURIComponent(path)}`)).json();
  if (r.error) { alert(r.error); return; }
  pane.file = r.path;
  pane.title = r.name;
  pane.cm.setOption("mode", modeOf(r.name));
  pane.cm.setValue(r.content);
  pane.cm.clearHistory();
  pane.dirty = false;
  pane.savedAt = "";
  loadTree(pane, pane.treeAt);
  render();
  setTimeout(() => pane.cm.refresh(), 0);
}

async function saveFile(pane) {
  if (!pane.file) return;
  const r = await (await fetch("/api/file", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: pane.file, content: pane.cm.getValue() }),
  })).json();
  if (r.error) { alert(r.error); return; }
  pane.dirty = false;
  pane.savedAt = r.saved;
  render();
}

function sizePane(p) {
  if (p.kind === "edit") { try { p.cm.refresh(); } catch (_) {} return; }
  try {
    p.fit.fit();
    if (p.ws && p.ws.readyState === 1)
      p.ws.send(JSON.stringify({ t: "r", cols: p.term.cols, rows: p.term.rows }));
  } catch (_) {}
}

async function closePane(id) {
  const p = panes.get(id); if (!p) return;
  if (p.kind === "edit") {
    if (p.dirty && !confirm("저장 안 된 변경이 있어. 닫을까?")) return;
  } else {
    // 서버에 먼저 알리고 소켓은 서버가 닫게 둔다 (여기서 먼저 닫으면 프레임이 깨진다)
    await fetch(`/api/panes/${id}`, { method: "DELETE" });
    p.term.dispose();
  }
  panes.delete(id);
  order = order.filter((x) => x !== id);
  slots = slots.map((s) => (s === id ? null : s));
  render();
}

function assign(id) {          // 포커스된 슬롯에 이 패널을 올린다
  const already = slots.indexOf(id);
  if (already >= 0) { focusSlot = already; render(); return; }
  slots[focusSlot] = id;
  render();
}

function setLayout(n) {
  layout = n;
  const next = slots.slice(0, n);
  while (next.length < n) next.push(null);
  // 빈 슬롯은 아직 안 걸린 패널로 자동으로 메운다
  const used = new Set(next.filter(Boolean));
  for (let i = 0; i < n; i++)
    if (!next[i]) next[i] = order.find((o) => !used.has(o) && (used.add(o), true)) || null;
  slots = next;
  if (focusSlot >= n) focusSlot = 0;
  [...$("#layoutPick").children].forEach((c) => c.classList.toggle("on", +c.dataset.n === n));
  render();
}

// ============================== 그리기 ==============================
function render() {
  $("#tabs").innerHTML = order.map((id, i) => {
    const p = panes.get(id);
    const mark = p.kind === "edit" ? `<span>✎</span>` : `<span class="dot"></span>`;
    return `<button class="tab ${slots.includes(id) ? "on" : ""} ${p.alive ? "" : "off"}" data-id="${id}">
      ${mark}<span>${i + 1}. ${esc(shorten(p.title))}${p.dirty ? " •" : ""}</span>
      <span class="x" data-x="${id}">×</span></button>`;
  }).join("");
  $("#tabs").onclick = (e) => {
    const x = e.target.closest(".x");
    if (x) { closePane(x.dataset.x); return; }
    const t = e.target.closest(".tab");
    if (t) assign(t.dataset.id);
  };

  const grid = $("#grid");
  grid.className = "n" + layout;
  grid.innerHTML = "";
  slots.forEach((id, i) => {
    const slot = document.createElement("div");
    slot.className = "slot" + (i === focusSlot ? " focus" : "");
    slot.onmousedown = () => { if (focusSlot !== i) { focusSlot = i; render(); } };
    const p = id && panes.get(id);
    if (p && p.kind === "edit") {
      slot.innerHTML = `<div class="head">
          <span class="nm">${esc(p.file ? p.title : "코드 편집기 — 왼쪽에서 파일 선택")}</span>
          ${p.dirty ? `<span class="chip dirty">저장 안 됨</span>` : ""}
          ${p.savedAt && !p.dirty ? `<span class="chip saved">저장됨 ${esc(p.savedAt)}</span>` : ""}
          <span class="chip">Ctrl+S</span>
          <span class="chip">${esc(shorten(p.treeAt, 30))}</span>
        </div>`;
      slot.appendChild(p.el);
      setTimeout(() => sizePane(p), 0);
    } else if (p) {
      const ml = (modelOf(p.model) || {}).label || p.model;
      slot.innerHTML = `<div class="head">
          <span class="nm">${esc(p.title)}</span>
          <span class="chip">${esc(ml)}${p.effort ? " · " + esc(p.effort) : ""}</span>
          ${p.perm && p.perm !== "manual"
            ? `<span class="chip ${(permOf(p.perm)||{}).warn ? "warn" : ""}">${esc((permOf(p.perm)||{}).label || p.perm)}</span>` : ""}
          ${p.resumed ? `<span class="chip">이어감</span>` : ""}
          <span class="chip">${esc(shorten(p.cwd, 28))}</span>
        </div>`;
      slot.appendChild(p.el);
      setTimeout(() => sizePane(p), 0);
    } else {
      slot.innerHTML = `<div class="hollow">비어 있음 — 왼쪽에서 세션을 시작하거나 탭을 눌러 올려놔</div>`;
    }
    grid.appendChild(slot);
  });

  paintUsage();
  const fp = panes.get(slots[focusSlot]);
  const isTerm = fp && fp.kind === "term";
  $("#paneModel").classList.toggle("hidden", !isTerm);
  $("#paneEffort").classList.toggle("hidden", !isTerm || !takesEffort(fp.model));
  if (isTerm) {
    $("#paneModel").innerHTML = modelOptions(fp.model);
    $("#paneEffort").innerHTML = effortButtons(fp.effort);
    setTimeout(() => fp.term.focus(), 0);
  }
}

// ============================== 유틸 ==============================
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function shorten(s, n = 22) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
function num(n) {
  return Number(n || 0).toLocaleString("en-US");
}
function ago(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}분 전`;
  if (d < 86400) return `${Math.floor(d / 3600)}시간 전`;
  return `${Math.floor(d / 86400)}일 전`;
}

// 웹폰트가 로드되기 전에 터미널을 만들면 글자 폭을 잘못 재서 줄이 어긋난다.
(document.fonts ? document.fonts.load('13.5px "JBM"').then(() => document.fonts.ready)
                : Promise.resolve()).then(boot, boot);
