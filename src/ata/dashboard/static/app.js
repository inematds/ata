// Ata dashboard — vanilla JS, sem CDN. Todo texto de reunião entra por textContent (nunca innerHTML).
"use strict";

const I18N = {
  "pt-BR": {
    nav_overview: "Visão geral", nav_meetings: "Reuniões", nav_search: "Busca", nav_live: "Ao vivo",
    nav_settings: "Configurações", status: "Estado", record: "Gravar", stop: "Parar", latest: "Últimas reuniões",
    title_ph: "Título (opcional)", filter_ph: "Filtrar por título", more: "Mais", pick: "Escolha uma reunião.",
    search_ph: "O que foi dito sobre…", search_btn: "Buscar", live_title: "Ao vivo",
    live_wait: "Aguardando turnos…", settings: "Configurações",
    settings_ro: "Somente leitura. Edite o arquivo de config ou use `ata setup`.", config_file: "Arquivo",
    doctor: "Rodar diagnóstico (doctor)", idle: "Parado", recording: "Gravando", since: "desde",
    unavailable: "gravador indisponível", summary: "Resumo", decisions: "Decisões", actions: "Ações",
    questions: "Perguntas em aberto", topics: "Tópicos", transcript: "Transcrição", speakers: "Falantes",
    save_names: "Salvar nomes", saved: "Nomes salvos", listen_far: "Ouvir: outros", listen_mic: "Ouvir: você",
    no_summary: "(sem resumo)", not_processed: "ainda não processada", results: "resultados",
    no_results: "Nada encontrado.", owner: "responsável", due: "prazo", turns: "turnos", empty: "Nenhuma reunião ainda.",
    error: "Erro", live_of: "Reunião ao vivo", damaged: "problemas na gravação",
  },
  en: {
    nav_overview: "Overview", nav_meetings: "Meetings", nav_search: "Search", nav_live: "Live",
    nav_settings: "Settings", status: "Status", record: "Record", stop: "Stop", latest: "Latest meetings",
    title_ph: "Title (optional)", filter_ph: "Filter by title", more: "More", pick: "Pick a meeting.",
    search_ph: "What was said about…", search_btn: "Search", live_title: "Live", live_wait: "Waiting for turns…",
    settings: "Settings", settings_ro: "Read-only. Edit the config file or run `ata setup`.", config_file: "File",
    doctor: "Run diagnostics (doctor)", idle: "Idle", recording: "Recording", since: "since",
    unavailable: "recorder unavailable", summary: "Summary", decisions: "Decisions", actions: "Action items",
    questions: "Open questions", topics: "Topics", transcript: "Transcript", speakers: "Speakers",
    save_names: "Save names", saved: "Names saved", listen_far: "Listen: others", listen_mic: "Listen: you",
    no_summary: "(no summary)", not_processed: "not processed yet", results: "results", no_results: "No results.",
    owner: "owner", due: "due", turns: "turns", empty: "No meetings yet.", error: "Error",
    live_of: "Live meeting", damaged: "recording problems",
  },
  es: {
    nav_overview: "Resumen general", nav_meetings: "Reuniones", nav_search: "Búsqueda", nav_live: "En vivo",
    nav_settings: "Configuración", status: "Estado", record: "Grabar", stop: "Detener", latest: "Últimas reuniones",
    title_ph: "Título (opcional)", filter_ph: "Filtrar por título", more: "Más", pick: "Elige una reunión.",
    search_ph: "Qué se dijo sobre…", search_btn: "Buscar", live_title: "En vivo", live_wait: "Esperando turnos…",
    settings: "Configuración", settings_ro: "Solo lectura. Edita el archivo de configuración o usa `ata setup`.",
    config_file: "Archivo", doctor: "Ejecutar diagnóstico (doctor)", idle: "Detenido", recording: "Grabando",
    since: "desde", unavailable: "grabador no disponible", summary: "Resumen", decisions: "Decisiones",
    actions: "Acciones", questions: "Preguntas abiertas", topics: "Temas", transcript: "Transcripción",
    speakers: "Hablantes", save_names: "Guardar nombres", saved: "Nombres guardados", listen_far: "Escuchar: otros",
    listen_mic: "Escuchar: tú", no_summary: "(sin resumen)", not_processed: "aún no procesada",
    results: "resultados", no_results: "Sin resultados.", owner: "responsable", due: "plazo", turns: "turnos",
    empty: "Aún no hay reuniones.", error: "Error", live_of: "Reunión en vivo", damaged: "problemas de grabación",
  },
};

const $ = (s) => document.querySelector(s);
const state = { lang: "pt-BR", cursor: null, current: null, offsets: { far: 0, mic: 0 }, playing: null };

function pref(key, val) {
  try { if (val === undefined) return localStorage.getItem("ata." + key); localStorage.setItem("ata." + key, val); }
  catch (e) { return null; }
  return null;
}
function tr(key) { return (I18N[state.lang] || I18N["pt-BR"])[key] || I18N["pt-BR"][key] || key; }
function applyI18n() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = tr(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = tr(el.dataset.i18nPh); });
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}
function mmss(t) {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ credentials: "same-origin" }, opts || {}));
  const data = await r.json().catch(() => ({ ok: false, error: r.statusText }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

// ---- navegação ------------------------------------------------------------------------------------------
function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "meetings") loadMeetings(true);
  if (view === "settings") loadConfig();
}

// ---- estado / gravação ----------------------------------------------------------------------------------
function renderStatus(st) {
  const rec = st && st.recorder ? st.recorder : {};
  const on = rec.phase === "recording";
  $("#rec-dot").classList.toggle("on", on);
  let txt = on ? `${tr("recording")} — ${rec.bundle ? rec.bundle.split(/[\\/]/).pop() : ""}` : tr("idle");
  if (on && rec.elapsed_s != null) txt += ` (${mmss(rec.elapsed_s)})`;
  if (rec.available === false) txt += ` · ${tr("unavailable")}`;
  $("#status-text").textContent = txt;
  $("#rec-start").disabled = on || rec.available === false;
  $("#rec-stop").disabled = !on;
}
async function refreshStatus() { try { renderStatus(await api("/api/status")); } catch (e) { /* offline */ } }

function meetingItem(m) {
  const li = el("li");
  li.appendChild(el("span", "", m.title || m.id));
  if (m.damage && m.damage.length) li.appendChild(el("span", "badge warn", tr("damaged")));
  const sub = [m.date ? m.date.slice(0, 16).replace("T", " ") : "", m.duration_s ? mmss(m.duration_s) : "",
    m.processed ? `${m.turns} ${tr("turns")}` : tr("not_processed")].filter(Boolean).join(" · ");
  li.appendChild(el("small", "", sub));
  li.addEventListener("click", () => { show("meetings"); openMeeting(m.id); });
  return li;
}
async function loadLatest() {
  const ul = $("#latest"); ul.textContent = "";
  try {
    const d = await api("/api/meetings?limit=5");
    if (!d.items.length) ul.appendChild(el("li", "muted", tr("empty")));
    d.items.forEach((m) => ul.appendChild(meetingItem(m)));
  } catch (e) { ul.appendChild(el("li", "muted", `${tr("error")}: ${e.message}`)); }
}
async function loadMeetings(reset) {
  if (reset) { state.cursor = null; $("#meeting-list").textContent = ""; }
  const q = encodeURIComponent($("#filter").value || "");
  const c = state.cursor ? `&cursor=${encodeURIComponent(state.cursor)}` : "";
  const d = await api(`/api/meetings?limit=30&q=${q}${c}`);
  d.items.forEach((m) => $("#meeting-list").appendChild(meetingItem(m)));
  state.cursor = d.next_cursor;
  $("#more").classList.toggle("hidden", !d.next_cursor);
}

// ---- detalhe --------------------------------------------------------------------------------------------
function section(title, items, fmt) {
  const box = el("div");
  box.appendChild(el("h3", "", title));
  const ul = el("ul");
  (items || []).forEach((it) => ul.appendChild(el("li", "", fmt(it))));
  box.appendChild(ul);
  return box;
}
function evT(it) { const e = (it.evidence || [])[0]; return e && e.t != null ? ` [${mmss(e.t)}]` : ""; }

async function openMeeting(id) {
  const box = $("#detail"); box.textContent = "";
  let d;
  try { d = await api(`/api/meetings/${encodeURIComponent(id)}`); }
  catch (e) { box.appendChild(el("p", "muted", `${tr("error")}: ${e.message}`)); return; }
  state.current = id;
  state.offsets = d.offsets || { far: 0, mic: 0 };
  const m = d.meta || {};
  box.appendChild(el("h2", "", m.title || id));
  box.appendChild(el("p", "muted", [m.date, m.language, m.duration_s ? mmss(m.duration_s) : ""].filter(Boolean).join(" · ")));

  const players = el("div", "players");
  ["far", "mic"].forEach((tk) => {
    if (!d.audio || !d.audio[tk]) return;
    const b = el("button", "ghost", tr(tk === "far" ? "listen_far" : "listen_mic"));
    b.addEventListener("click", () => play(tk, 0));
    players.appendChild(b);
  });
  box.appendChild(players);

  const s = d.summary;
  const sum = el("div", "sum");
  sum.appendChild(el("h3", "", tr("summary")));
  sum.appendChild(el("p", "", s && s.tldr ? s.tldr : tr("no_summary")));
  if (s) {
    if (s.topics && s.topics.length) sum.appendChild(section(tr("topics"), s.topics, (x) => `${x.title}${x.start != null ? " [" + mmss(x.start) + "]" : ""}`));
    if (s.decisions && s.decisions.length) sum.appendChild(section(tr("decisions"), s.decisions, (x) => x.text + evT(x)));
    if (s.actions && s.actions.length) sum.appendChild(section(tr("actions"), s.actions, (x) =>
      `${x.text}${x.owner ? " — " + tr("owner") + ": " + ((d.names || {})[x.owner] || x.owner) : ""}${x.due ? " · " + tr("due") + ": " + x.due : ""}${evT(x)}`));
    if (s.questions && s.questions.length) sum.appendChild(section(tr("questions"), s.questions, (x) => x.text + evT(x)));
  }
  box.appendChild(sum);

  const turns = (d.transcript && d.transcript.items) || [];
  const labels = [...new Set(turns.map((t) => t.label))];
  if (labels.length) {
    box.appendChild(el("h3", "", tr("speakers")));
    const form = el("form", "speakers");
    labels.forEach((lab) => {
      const l = el("label", "", lab);
      const inp = el("input"); inp.name = lab; inp.value = (d.names || {})[lab] || ""; inp.placeholder = lab;
      l.appendChild(inp); form.appendChild(l);
    });
    const save = el("button", "primary", tr("save_names")); save.type = "submit";
    form.appendChild(save);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const names = {};
      form.querySelectorAll("input").forEach((i) => { names[i.name] = i.value.trim(); });
      try { await post(`/api/meetings/${encodeURIComponent(id)}/speakers`, { names }); save.textContent = tr("saved"); openMeeting(id); }
      catch (e) { save.textContent = `${tr("error")}: ${e.message}`; }
    });
    box.appendChild(form);
  }
  box.appendChild(el("h3", "", tr("transcript")));
  const ol = el("ol", "turns");
  turns.forEach((t) => {
    const li = el("li");
    li.dataset.start = t.start; li.dataset.end = t.end; li.dataset.track = t.track;
    li.appendChild(el("span", "t", mmss(t.start)));
    li.appendChild(el("span", "who", t.speaker));
    li.appendChild(el("span", "txt", t.text));
    li.addEventListener("click", () => play(t.track, t.start));
    ol.appendChild(li);
  });
  box.appendChild(ol);
}

function play(track, bundleT) {
  const p = $("#player");
  const src = `/api/meetings/${encodeURIComponent(state.current)}/audio/${track}`;
  const fileT = Math.max(0, bundleT - ((state.offsets || {})[track] || 0));
  const go = () => { p.currentTime = fileT; p.play().catch(() => {}); };
  if (state.playing !== src) { p.src = src; state.playing = src; p.addEventListener("loadedmetadata", go, { once: true }); p.load(); }
  else go();
  p.dataset.track = track;
}
$("#player").addEventListener("timeupdate", () => {
  const p = $("#player"), tk = p.dataset.track;
  const t = p.currentTime + ((state.offsets || {})[tk] || 0);
  document.querySelectorAll("#detail .turns li").forEach((li) => {
    li.classList.toggle("playing", li.dataset.track === tk && t >= +li.dataset.start && t <= +li.dataset.end);
  });
});

// ---- busca ----------------------------------------------------------------------------------------------
$("#search-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim(); if (!q) return;
  const ul = $("#results"); ul.textContent = "";
  try {
    const d = await api(`/api/search?q=${encodeURIComponent(q)}&limit=30`);
    $("#search-meta").textContent = `${d.total} ${tr("results")} · ${d.engine}`;
    if (!d.items.length) ul.appendChild(el("li", "muted", tr("no_results")));
    d.items.forEach((h) => {
      const li = el("li");
      li.appendChild(el("span", "", `${h.speaker || ""}: ${h.text || ""}`));
      li.appendChild(el("small", "", `${h.title || h.meeting} · ${h.t || ""}`));
      li.addEventListener("click", async () => { show("meetings"); await openMeeting(h.meeting); });
      ul.appendChild(li);
    });
  } catch (e) { $("#search-meta").textContent = `${tr("error")}: ${e.message}`; }
});

// ---- ao vivo (SSE) --------------------------------------------------------------------------------------
function startEvents() {
  if (!window.EventSource) return;
  const es = new EventSource("/api/events");
  let liveOf = null;
  es.addEventListener("status", (ev) => {
    const d = JSON.parse(ev.data);
    renderStatus(d);
    if (d.live_bundle !== liveOf) { liveOf = d.live_bundle; $("#live-turns").textContent = ""; }
    $("#live-meta").textContent = liveOf ? `${tr("live_of")}: ${liveOf}` : tr("live_wait");
  });
  es.addEventListener("turn", (ev) => {
    const t = JSON.parse(ev.data);
    const li = el("li");
    li.appendChild(el("span", "t", mmss(t.start)));
    li.appendChild(el("span", "who", t.speaker));
    li.appendChild(el("span", "txt", t.text));
    $("#live-turns").appendChild(li);
    li.scrollIntoView({ block: "nearest" });
  });
}

// ---- configurações --------------------------------------------------------------------------------------
async function loadConfig() {
  try { const d = await api("/api/config"); $("#config-src").textContent = d.source || "(padrões)"; $("#config").textContent = JSON.stringify(d.config, null, 2); }
  catch (e) { $("#config").textContent = e.message; }
}
$("#doctor-btn").addEventListener("click", async () => {
  $("#doctor").textContent = "…";
  try { $("#doctor").textContent = JSON.stringify(await api("/api/doctor"), null, 2); }
  catch (e) { $("#doctor").textContent = e.message; }
});

// ---- inicialização --------------------------------------------------------------------------------------
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$("#more").addEventListener("click", () => loadMeetings(false));
let filterTimer = null;
$("#filter").addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(() => loadMeetings(true), 250); });
$("#rec-start").addEventListener("click", async () => {
  try { await post("/api/record/start", { title: $("#rec-title").value.trim() || null }); } catch (e) { $("#status-text").textContent = e.message; }
  refreshStatus();
});
$("#rec-stop").addEventListener("click", async () => {
  try { await post("/api/record/stop", { process: true }); } catch (e) { $("#status-text").textContent = e.message; }
  refreshStatus(); loadLatest();
});
$("#lang").addEventListener("change", () => {
  state.lang = $("#lang").value; pref("lang", state.lang); applyI18n();
  loadLatest(); if (document.querySelector('[data-view="meetings"].active')) loadMeetings(true);
});
$("#theme").addEventListener("click", () => {
  const order = ["auto", "dark", "light"], cur = document.documentElement.dataset.theme || "auto";
  const next = order[(order.indexOf(cur) + 1) % order.length];
  document.documentElement.dataset.theme = next; pref("theme", next);
});

(function init() {
  const saved = pref("lang");
  const nav = (navigator.language || "pt-BR").toLowerCase();
  state.lang = saved && I18N[saved] ? saved : nav.startsWith("es") ? "es" : nav.startsWith("en") ? "en" : "pt-BR";
  $("#lang").value = state.lang;
  document.documentElement.dataset.theme = pref("theme") || "auto";
  applyI18n(); refreshStatus(); loadLatest(); startEvents();
})();
