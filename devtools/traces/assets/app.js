// The trace viewer's page. It draws what the server sends and computes
// nothing about a run: no score, no ranking, only the log's own facts.

const state = {
  runs: [],
  steps: {}, // run id -> steps, fetched when a run is opened
  set: "working",
  query: "",
  tagFilter: new Set(),
  sel: null,
  cmp: null,
  focus: 0,
  opened: {},
  groups: new Set(),
  drawer: false,
  noteRun: null,
};

const $ = (s) => document.querySelector(s);
const byId = (id) => state.runs.find((r) => r.id === id);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const opened = (id) => (state.opened[id] ||= new Set());

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function when(iso) {
  if (!iso) return "";
  const [d, t] = iso.split("T");
  const [, m, day] = d.split("-");
  return `${+day} ${MONTHS[+m - 1]}${t ? `, ${t}` : ""}`;
}
const who = (r) => [r.harness === "portia" ? null : r.harness, r.provider, r.model].filter(Boolean).join(" · ");

// ---------- server ----------

async function loadRuns() {
  const res = await fetch("/api/runs");
  state.runs = await res.json();
}

async function loadSteps(id) {
  if (state.steps[id]) return state.steps[id];
  const res = await fetch(`/api/runs/${id}`);
  state.steps[id] = res.ok ? await res.json() : [];
  return state.steps[id];
}

async function save(run, change) {
  Object.assign(run, change);
  const res = await fetch(`/api/runs/${run.id}/note`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });
  if (res.ok) Object.assign(run, await res.json());
  return res.ok;
}

// ---------- sidebar ----------

const KIND_ICON = {
  chat: '<svg viewBox="0 0 16 16" class="kind" aria-label="Chat"><path d="M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 2.5v-2.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z"/></svg>',
  indexing: '<svg viewBox="0 0 16 16" class="kind" aria-label="Indexing"><ellipse cx="8" cy="4" rx="5" ry="1.8"/><path d="M3 4v8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V4M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8"/></svg>',
};
const KIND_LABEL = { chat: "Chat", indexing: "Indexing" };

function matches(r) {
  const q = state.query.trim().toLowerCase();
  const hay = `${r.title} ${r.project} ${r.model} ${r.harness} ${r.provider || ""} ${KIND_LABEL[r.kind]} ${r.tags.join(" ")}`;
  if (q && !hay.toLowerCase().includes(q)) return false;
  // Tags are OR: picking two shows runs with either.
  if (state.tagFilter.size && !r.tags.some((t) => state.tagFilter.has(t))) return false;
  return true;
}

function rowHTML(r) {
  return `<button class="row${r.id === state.sel ? " sel" : ""}${r.id === state.cmp ? " cmp" : ""}" data-run="${r.id}" title="${esc(`${KIND_LABEL[r.kind]} · ${r.project}`)}">
    ${KIND_ICON[r.kind] || KIND_ICON.chat}
    <span class="rt"><span class="t">${esc(r.title)}</span><span class="d">${r.errors ? '<span class="dot err"></span>' : ""}${esc(who(r))} · ${esc(when(r.date))}</span></span>
    <span class="star${r.starred ? " on" : ""}" data-star="${r.id}" title="${r.starred ? "Remove from" : "Add to"} working set">★</span>
  </button>`;
}

function renderList() {
  const list = $("#list");
  const tagging = state.tagFilter.size > 0;
  // A tag filter looks at every log: a tag is your own marker, wherever the run is.
  const pool = state.runs.filter((r) => (tagging || state.set === "all" ? true : r.starred)).filter(matches);
  $("#seg").classList.toggle("dim", tagging);
  document.querySelectorAll("#seg button").forEach((b) => b.classList.toggle("on", b.dataset.set === state.set));
  $("#count").textContent = tagging ? `${pool.length} tagged` : state.set === "working" ? `${pool.length} in working set` : `${pool.length} of ${state.runs.length} logs`;

  if (!pool.length) {
    const why = state.query.trim() ? "Nothing matches." : state.set === "working" ? "Star a run in All logs to add it here." : "No logs found.";
    list.innerHTML = `<div class="empty-list">${why}</div>`;
  } else if (tagging || state.set === "working") {
    list.innerHTML = pool.map(rowHTML).join("");
  } else {
    const groups = {};
    for (const r of pool) (groups[r.project] ||= []).push(r);
    const searching = state.query.trim();
    list.innerHTML = Object.keys(groups).sort().map((p) => {
      const open = searching || state.groups.has(p);
      return `<button class="group-h${open ? " open" : ""}" data-group="${esc(p)}"><span class="chev">›</span>${esc(p)}<span class="n">${groups[p].length}</span></button>
        ${open ? groups[p].map(rowHTML).join("") : ""}`;
    }).join("");
  }

  const tags = [...new Set(state.runs.flatMap((r) => r.tags))].sort();
  $("#tagbar").innerHTML = tags.map((t) => `<button class="tag${state.tagFilter.has(t) ? " on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("")
    + (tagging ? '<button class="tag clear" data-cleartags>clear</button>' : "");
}

// ---------- trace ----------

const SQL_KW = /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|PARTITION BY|LEFT JOIN|INNER JOIN|FULL OUTER JOIN|JOIN|ON|AND|OR|NOT|AS|WITH|CASE|WHEN|THEN|ELSE|END|DISTINCT|COUNT|SUM|AVG|MIN|MAX|COALESCE|CAST|LIMIT|HAVING|UNION ALL|UNION|OVER|DESC|ASC|IS NULL|IS NOT NULL|IN)\b/g;
function codeHTML(text) {
  return esc(text).replace(SQL_KW, '<span class="kw">$1</span>');
}
function jsonHTML(obj) {
  return esc(JSON.stringify(obj, null, 2)).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(:)/g, '<span class="jk">$1</span>$2');
}
function inline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+?)`/g, "<code>$1</code>");
}
function mdHTML(text) {
  const out = [];
  const blocks = text.replace(/\r/g, "").split(/```[a-z]*\n?/);
  blocks.forEach((block, i) => {
    if (i % 2) { out.push(`<pre class="block">${codeHTML(block.replace(/\n$/, ""))}</pre>`); return; }
    for (const p of block.split(/\n\n+/)) {
      if (!p.trim()) continue;
      const lines = p.split("\n");
      if (lines.every((l) => l.trim().startsWith("|"))) out.push(`<pre class="block mdtable">${esc(p)}</pre>`);
      else if (/^#{1,6} /.test(p)) out.push(`<p class="h">${inline(p.replace(/^#{1,6} /, ""))}</p>`);
      else if (lines.every((l) => /^\s*([-*]|\d+\.) /.test(l) || /^\s{2,}/.test(l))) out.push("<ul>" + lines.map((l) => `<li>${inline(l.replace(/^\s*([-*]|\d+\.) /, ""))}</li>`).join("") + "</ul>");
      else out.push(`<p>${lines.map(inline).join("<br>")}</p>`);
    }
  });
  return out.join("");
}
function tableHTML(tb) {
  const head = tb.columns.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = tb.rows.map((row) => "<tr>" + row.map((v) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 4 }) : v ?? "")}</td>`).join("") + "</tr>").join("");
  const counted = /^\d+ of \d+ /.test(tb.key); // a columnar block already says how many
  const label = counted ? tb.key : `${tb.key} · ${tb.rows.length} ${tb.rows.length === 1 ? "row" : "rows"}`;
  return `<div class="lab">${esc(label)}</div><div class="tablewrap"><table class="res"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function inputHTML(input) {
  const entries = Object.entries(input || {});
  if (!entries.length) return '<div class="kv"><span class="k">no arguments</span><span></span></div>';
  const cells = entries.map(([k, v]) => {
    if (typeof v === "string" && v.includes("\n")) return `<span class="k">${esc(k)}</span><pre class="block">${codeHTML(v)}</pre>`;
    if (v && typeof v === "object" && !(Array.isArray(v) && v.every((x) => typeof x !== "object"))) return `<span class="k">${esc(k)}</span><pre class="block">${jsonHTML(v)}</pre>`;
    const shown = Array.isArray(v) ? v.join(", ") : v;
    return `<span class="k">${esc(k)}</span><span class="v">${esc(shown)}</span>`;
  }).join("");
  return `<div class="kv">${cells}</div>`;
}
function resultHTML(s) {
  if (s.result === null || s.result === undefined) return '<div class="more">No result in the log.</div>';
  const parts = [];
  if (typeof s.result === "string") parts.push(`<pre class="block${s.error ? " err" : ""}">${esc(s.result)}</pre>`);
  else parts.push(`<pre class="block">${jsonHTML(s.result)}</pre>`);
  for (const tb of s.tables || []) parts.push(tableHTML(tb));
  return parts.join("");
}

function stepKind(s) {
  if (s.t === "you") return "k-you";
  if (s.t === "ask") return "k-ask";
  if (s.error || s.t === "error") return "k-err";
  if (s.write) return "k-write";
  if (s.t === "tool") return "k-tool";
  return "k-text";
}
const tickTitle = (s) => (s.t === "tool" ? s.name : s.t === "you" ? "You" : s.t === "ask" ? "Asked you" : "");

function foldHTML(i, cls, label, body, isOpen, cur, id) {
  return `<div class="step ${cls}${isOpen ? " open" : ""}${cur}" id="${id}" data-i="${i}">
    <button class="line" data-toggle="${i}"><span class="chev">▶</span><span class="name">${label}</span><span class="arg"></span></button>
    <div class="body">${isOpen ? body : ""}</div></div>`;
}

function stepHTML(run, i, s, pane) {
  const isOpen = opened(run.id).has(i);
  const cur = pane === 0 && i === state.focus ? " cur" : "";
  const id = `p${pane}-s${i}`;
  switch (s.t) {
    case "you":
      return `<div class="step you${cur}" id="${id}" data-i="${i}"><div class="who">You</div><div class="msg">${mdHTML(s.text)}</div></div>`;
    case "say":
      return `<div class="step say${cur}" id="${id}" data-i="${i}">${mdHTML(s.text)}</div>`;
    case "error":
      return `<div class="step say error${cur}" id="${id}" data-i="${i}"><pre class="block err">${esc(s.text)}</pre></div>`;
    case "think":
      return foldHTML(i, "think", "Thinking", `<pre class="block think">${esc(s.text)}</pre>`, isOpen, cur, id);
    case "hook":
      return foldHTML(i, "think", "Stop hook", `<pre class="block think">${esc(s.text)}</pre>`, isOpen, cur, id);
    case "context":
      return foldHTML(i, "think", "Added to its context", `<pre class="block think">${esc(s.text)}</pre>`, isOpen, cur, id);
    case "ask": {
      const qs = s.questions.map((q) => {
        const listed = q.options.includes(q.answer);
        const opts = q.options.map((o) => `<span class="opt${o === q.answer ? " chosen" : ""}">${esc(o)}</span>`).join("");
        const typed = q.answer && !listed ? `<span class="opt chosen">${esc(q.answer)}</span>` : "";
        const none = !q.answer ? '<span class="opt">no answer in the log</span>' : "";
        return `<div class="q">${esc(q.q)}</div><div class="opts">${opts}${typed}${none}</div>`;
      }).join('<div style="height:10px"></div>');
      return `<div class="step ask${cur}" id="${id}" data-i="${i}">
        <div class="line"><span class="chev"></span><span class="name">Asked you</span><span class="arg"></span><span class="secs">${s.waited ? `waited ${esc(s.waited)}` : ""}</span></div>
        <div class="qa">${qs}</div></div>`;
    }
    case "end": {
      const bits = ["Reply finished", s.how, s.secs, s.cost ? `$${s.cost.toFixed(2)}` : null].filter(Boolean);
      return `<div class="step end${cur}" id="${id}" data-i="${i}"><div class="line"><span class="chev"></span><span class="name">${esc(bits.join(" · "))}</span></div></div>`;
    }
    default: {
      const badges = [
        s.write ? `<span class="badge w">${s.write === "auto" ? "write, not asked" : `write ${s.write}`}</span>` : "",
        s.error ? '<span class="badge e">error</span>' : "",
      ].join("");
      const body = `<div class="lab">Input</div>${inputHTML(s.input)}<div class="lab">Result</div>${resultHTML(s)}`;
      return `<div class="step tool${s.error ? " error" : ""}${isOpen ? " open" : ""}${cur}" id="${id}" data-i="${i}">
        <button class="line" data-toggle="${i}"><span class="chev">▶</span><span class="name">${esc(s.name)}</span><span class="arg">${esc(s.arg)}</span>${badges}<span class="secs">${s.secs != null ? `${s.secs}s` : ""}</span></button>
        <div class="body">${isOpen ? body : ""}</div></div>`;
    }
  }
}

const PIN_FIELDS = ["harness", "provider", "model", "effort", "prompts"];

function metaHTML(run, other) {
  const bit = (f, text) => (text == null || text === "" ? null : other && other[f] !== run[f] ? `<span class="diff">${esc(text)}</span>` : esc(text));
  const parts = [
    esc(KIND_LABEL[run.kind]),
    bit("model", run.model),
    bit("effort", run.effort),
    bit("harness", run.harness),
    bit("provider", run.provider),
    bit("prompts", run.prompts ? `prompts ${run.prompts}` : other && other.prompts ? "prompts not recorded" : null),
    esc(when(run.date)),
    run.took ? esc(run.took) : null,
    run.cost ? `$${run.cost.toFixed(2)}` : null,
    `${run.calls} tool ${run.calls === 1 ? "call" : "calls"}`,
    run.errors ? `<span style="color:var(--error)">${run.errors} ${run.errors === 1 ? "error" : "errors"}</span>` : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

function paneHTML(run, pane, other) {
  const steps = state.steps[run.id];
  const noting = state.drawer && state.noteRun === run.id;
  const shared = `<button class="btn${run.starred ? " on" : ""}" data-act="star" title="Working set (s)">★</button>
       <button class="btn${noting ? " on" : ""}" data-act="note" title="Note (n)">${run.note || run.tags.length ? '<span class="has"></span>' : ""}Note</button>`;
  const acts = pane === 0
    ? `${shared}<button class="btn${state.cmp ? " on" : ""}" data-act="compare" title="Compare (c)">Compare</button>`
    : `${shared}<button class="btn" data-act="close-cmp" title="Close (x)">✕</button>`;
  const strip = steps ? steps.map((s, i) => `<i class="${stepKind(s)}${pane === 0 && i === state.focus ? " cur" : ""}" data-jump="${i}" title="${esc(tickTitle(s))}"></i>`).join("") : "";
  const trace = steps ? steps.map((s, i) => stepHTML(run, i, s, pane)).join("") || '<div class="blank">This log has no steps.</div>' : '<div class="blank">Loading…</div>';
  return `<section class="pane" data-pane="${pane}" data-run="${run.id}">
    <div class="head">
      <div class="head-line"><h1 title="${esc(run.path)}">${esc(run.title)}</h1><div class="acts">${acts}</div></div>
      <div class="meta">${metaHTML(run, other)}</div>
      <div class="strip">${strip}</div>
    </div>
    <div class="scroll"><div class="trace">${trace}</div></div>
  </section>`;
}

function renderMain(keepScroll = true) {
  const main = $("#main");
  const scrolls = [...main.querySelectorAll(".scroll")].map((el) => el.scrollTop);
  const run = byId(state.sel);
  if (!run) { main.innerHTML = '<div class="blank">Pick a run</div>'; return; }
  const other = state.cmp && byId(state.cmp);
  main.innerHTML = paneHTML(run, 0, other) + (other ? paneHTML(other, 1, run) : "");
  if (keepScroll) main.querySelectorAll(".scroll").forEach((el, i) => (el.scrollTop = scrolls[i] || 0));
}

function writeHash() {
  const parts = [];
  if (state.sel) parts.push(`r=${state.sel}`);
  if (state.cmp) parts.push(`c=${state.cmp}`);
  history.replaceState(null, "", parts.length ? `#${parts.join("&")}` : location.pathname);
}

// ---------- note drawer ----------

let saveTimer;
function renderDrawer() {
  const d = $("#drawer");
  const run = byId(state.noteRun);
  d.classList.toggle("open", state.drawer && !!run);
  if (!state.drawer || !run) { d.innerHTML = ""; return; }
  const all = [...new Set(state.runs.flatMap((r) => r.tags))].filter((t) => !run.tags.includes(t)).sort();
  d.innerHTML = `<div class="drawer-in">
    <div class="drawer-h"><span class="dt">Note<span class="for">${esc(run.title)} · ${esc(who(run))}</span></span><button class="ghost x" data-act="note" title="Close (Esc)">✕</button></div>
    <textarea id="note" placeholder="What went wrong, or what's interesting here">${esc(run.note)}</textarea>
    <div class="tags">${run.tags.map((t) => `<span class="tag on">${esc(t)}<button data-untag="${esc(t)}">✕</button></span>`).join("")}</div>
    <input class="taginput" id="taginput" placeholder="Add a tag and press Enter" autocomplete="off">
    ${all.length ? `<div class="sugg">${all.map((t) => `<span class="tag" data-addtag="${esc(t)}">+ ${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="saved" id="saved"></div>
  </div>`;
  $("#note").addEventListener("input", (e) => {
    run.note = e.target.value;
    $("#saved").textContent = "";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const ok = await save(run, { note: run.note });
      $("#saved") && ($("#saved").textContent = ok ? "Saved" : "Not saved: is the server still running?");
      renderMain(); renderList();
    }, 400);
  });
  $("#taginput").addEventListener("keydown", async (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && e.target.value.trim()) {
      await setTags(run, [...run.tags, e.target.value.trim()]);
      $("#taginput").focus();
    }
    if (e.key === "Escape") e.target.blur();
  });
}
async function setTags(run, tags) {
  await save(run, { tags });
  renderDrawer(); renderList(); renderMain();
}
function toggleNote(runId) {
  if (!runId) return;
  if (state.drawer && state.noteRun === runId) state.drawer = false;
  else { state.drawer = true; state.noteRun = runId; }
  renderDrawer(); renderMain();
  if (state.drawer) setTimeout(() => $("#note")?.focus());
}
function closeNote() { state.drawer = false; renderDrawer(); renderMain(); }
function paneRun(el) {
  const pane = el.closest(".pane");
  return pane ? pane.dataset.run : state.noteRun || state.sel;
}
async function toggleStar(id) {
  const run = byId(id);
  if (!run) return;
  await save(run, { starred: !run.starred });
  renderList(); renderMain();
}

// ---------- popovers ----------

function showPop(anchor, html) {
  const pop = $("#pop");
  pop.innerHTML = html;
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${r.bottom + 6}px`;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - 348, r.right - 340))}px`;
}
function hidePop() { $("#pop").hidden = true; }

function comparePicker(anchor) {
  const draw = (q) => {
    const pool = state.runs.filter((r) => r.id !== state.sel)
      .filter((r) => (q ? `${r.title} ${r.project} ${r.model} ${r.harness}`.toLowerCase().includes(q) : r.starred))
      .slice(0, 40);
    $("#pop .list").innerHTML = pool.map(rowHTML).join("") || `<div class="empty-list">${q ? "Nothing matches." : "Search, or star runs to see them here."}</div>`;
  };
  showPop(anchor, '<h3>Compare with</h3><input class="search" id="popq" placeholder="Search all logs" autocomplete="off"><div class="list" style="margin-top:6px"></div>');
  draw("");
  const q = $("#popq");
  q.focus();
  q.addEventListener("input", () => draw(q.value.trim().toLowerCase()));
  q.addEventListener("keydown", (e) => { if (e.key === "Escape") hidePop(); e.stopPropagation(); });
}

function keysPop(anchor) {
  const k = [
    ["↑ ↓  or  k j", "Move between steps"],
    ["→  or  Enter", "Open a step"],
    ["←", "Close it"],
    ["e", "Next error"],
    ["[  ]", "Previous or next run"],
    ["n", "Note"],
    ["c", "Compare"],
    ["s", "Add to working set"],
    ["/", "Search"],
    ["Esc", "Close"],
  ];
  showPop(anchor, `<h3>Keyboard</h3><div class="keys">${k.map(([a, b]) => `<span>${a.split("  ").map((x) => (x === "or" ? " or " : `<kbd>${x}</kbd>`)).join("")}</span><span>${b}</span>`).join("")}</div>`);
  const pop = $("#pop");
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${r.top - pop.offsetHeight - 6}px`;
  pop.style.left = `${r.left}px`;
}

// ---------- actions ----------

async function select(id) {
  if (!id || id === state.sel) return;
  state.sel = id;
  state.focus = 0;
  if (state.cmp === id) state.cmp = null;
  if (state.drawer && state.noteRun !== state.cmp) state.noteRun = id;
  writeHash();
  renderList(); renderMain(false); renderDrawer();
  await loadSteps(id);
  if (state.sel === id) renderMain(false);
}

async function compareWith(id) {
  state.cmp = id;
  writeHash();
  renderList(); renderMain(false);
  await loadSteps(id);
  if (state.cmp === id) renderMain(false);
}

function closeCompare() {
  if (state.noteRun === state.cmp) state.noteRun = state.sel;
  state.cmp = null;
  writeHash();
  renderList(); renderDrawer(); renderMain();
}

function focusStep(i, open) {
  const steps = state.steps[state.sel];
  if (!steps || !steps.length) return;
  state.focus = Math.max(0, Math.min(steps.length - 1, i));
  if (open !== undefined) {
    const set = opened(state.sel);
    open ? set.add(state.focus) : set.delete(state.focus);
  }
  renderMain();
  document.getElementById(`p0-s${state.focus}`)?.scrollIntoView({ block: "nearest" });
}

function toggleStep(pane, i) {
  const id = pane === 0 ? state.sel : state.cmp;
  const set = opened(id);
  set.has(i) ? set.delete(i) : set.add(i);
  if (pane === 0) state.focus = i;
  renderMain();
}

document.addEventListener("click", (e) => {
  const t = e.target;
  const pop = $("#pop");
  if (!pop.hidden && !pop.contains(t) && !t.closest("[data-act=compare]") && !t.closest("#keys-btn")) hidePop();

  const star = t.closest("[data-star]");
  if (star) { e.stopPropagation(); toggleStar(star.dataset.star); return; }
  const row = t.closest("[data-run].row");
  if (row) {
    if (pop.contains(row)) { hidePop(); compareWith(row.dataset.run); return; }
    select(row.dataset.run);
    return;
  }
  const g = t.closest("[data-group]");
  if (g) { const p = g.dataset.group; state.groups.has(p) ? state.groups.delete(p) : state.groups.add(p); renderList(); return; }
  const seg = t.closest("#seg button");
  if (seg) { state.tagFilter.clear(); state.set = seg.dataset.set; renderList(); return; }
  if (t.closest("[data-cleartags]")) { state.tagFilter.clear(); renderList(); return; }
  const tag = t.closest("[data-tag]");
  if (tag) { const x = tag.dataset.tag; state.tagFilter.has(x) ? state.tagFilter.delete(x) : state.tagFilter.add(x); renderList(); return; }
  const tog = t.closest("[data-toggle]");
  if (tog) { toggleStep(+tog.closest(".pane").dataset.pane, +tog.dataset.toggle); return; }
  const jump = t.closest("[data-jump]");
  if (jump) {
    const pane = +jump.closest(".pane").dataset.pane;
    const i = +jump.dataset.jump;
    if (pane === 0) focusStep(i, true);
    else { opened(state.cmp).add(i); renderMain(); document.getElementById(`p1-s${i}`)?.scrollIntoView({ block: "center" }); }
    return;
  }
  const untag = t.closest("[data-untag]");
  if (untag) { const r = byId(state.noteRun); setTags(r, r.tags.filter((x) => x !== untag.dataset.untag)); return; }
  const add = t.closest("[data-addtag]");
  if (add) { const r = byId(state.noteRun); setTags(r, [...r.tags, add.dataset.addtag]); return; }
  const act = t.closest("[data-act]");
  if (act) {
    const a = act.dataset.act;
    if (a === "star") toggleStar(paneRun(act));
    if (a === "note") (act.closest(".drawer") ? closeNote() : toggleNote(paneRun(act)));
    if (a === "compare") (state.cmp ? closeCompare() : comparePicker(act));
    if (a === "close-cmp") closeCompare();
    return;
  }
  if (t.closest("#keys-btn")) { keysPop(t.closest("#keys-btn")); return; }
  const step = t.closest(".pane[data-pane='0'] .step");
  if (step && !t.closest(".body") && !window.getSelection().toString()) { state.focus = +step.dataset.i; renderMain(); }
});

$("#search").addEventListener("input", (e) => { state.query = e.target.value; renderList(); });
$("#search").addEventListener("keydown", (e) => { if (e.key === "Escape") e.target.blur(); e.stopPropagation(); });

document.addEventListener("keydown", (e) => {
  if (e.target.matches("textarea, input")) {
    if (e.key === "Escape") { e.target.blur(); if (state.drawer) closeNote(); }
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const steps = state.steps[state.sel] || [];
  const k = e.key;
  if (k === "ArrowDown" || k === "j") focusStep(state.focus + 1);
  else if (k === "ArrowUp" || k === "k") focusStep(state.focus - 1);
  else if (k === "ArrowRight" || k === "Enter") focusStep(state.focus, true);
  else if (k === "ArrowLeft") focusStep(state.focus, false);
  else if (k === "e") {
    const isErr = (s) => s.error || s.t === "error";
    const next = steps.findIndex((s, i) => i > state.focus && isErr(s));
    const i = next >= 0 ? next : steps.findIndex(isErr);
    if (i >= 0) focusStep(i, true);
  } else if (k === "[" || k === "]") {
    const rows = [...document.querySelectorAll("#list .row")].map((r) => r.dataset.run);
    const to = rows[rows.indexOf(state.sel) + (k === "]" ? 1 : -1)];
    if (to) select(to);
  } else if (k === "n") toggleNote(state.sel);
  else if (k === "c") (state.cmp ? closeCompare() : comparePicker(document.querySelector("[data-act=compare]")));
  else if (k === "x" && state.cmp) closeCompare();
  else if (k === "s") toggleStar(state.sel);
  else if (k === "/") $("#search").focus();
  else if (k === "Escape") { hidePop(); if (state.drawer) closeNote(); }
  else if (k === "?") keysPop($("#keys-btn"));
  else return;
  e.preventDefault();
});

// New logs appear when you come back to the tab; what you have open stays open.
window.addEventListener("focus", async () => {
  const notes = Object.fromEntries(state.runs.map((r) => [r.id, r]));
  await loadRuns();
  for (const r of state.runs) if (notes[r.id] && state.drawer && state.noteRun === r.id) r.note = notes[r.id].note;
  for (const id of [state.sel, state.cmp]) if (id) delete state.steps[id];
  renderList();
  await Promise.all([state.sel, state.cmp].filter(Boolean).map(loadSteps));
  renderMain();
});

async function boot() {
  await loadRuns();
  const hash = new URLSearchParams(location.hash.slice(1));
  state.set = state.runs.some((r) => r.starred) ? "working" : "all";
  renderList();
  const first = byId(hash.get("r")) ? hash.get("r") : (state.runs.find((r) => r.starred) || state.runs[0])?.id;
  if (first) await select(first);
  else renderMain();
  if (byId(hash.get("c")) && hash.get("c") !== state.sel) await compareWith(hash.get("c"));
}

boot();
