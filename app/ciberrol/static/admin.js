// Panel de administración de una actividad.
const ACT = window.CIBERROL_ACT;
const LIVE = `/admin/api/a/${ACT}/live?admin_view=1`;
const CTRL = `/admin/api/a/${ACT}/control`;
const els = {
  status: document.getElementById("ctl-status"),
  primary: document.getElementById("ctl-primary"),
  secondary: document.getElementById("ctl-secondary"),
  stats: document.getElementById("stats"),
  progress: document.getElementById("progress"),
  board: document.getElementById("board"),
  people: document.getElementById("people"),
};
let poller;
let lastData = null;

async function act(action, body) {
  if (action === "finish" && !confirm("¿Finalizar el ejercicio? Se mostrarán los resultados por persona.")) return;
  if (action === "reset" && !confirm("Se borrarán propuestas y votos de esta diapositiva y volverá al paso 1. ¿Continuar?")) return;
  if (action === "assign_roles" && lastData && lastData.progress.totals.joined > lastData.progress.unassigned
      && !confirm("Se volverán a asignar los roles de todas las personas. ¿Continuar?")) return;
  const r = await postJSON(`${CTRL}/${action}`, body);
  if (!r.ok) { alert(r.data.detail || "Error"); return; }
  if (action === "finish") { location.href = `/admin/a/${ACT}/resultados`; return; }
  poller.force();
}

function btn(label, action, cls = "", disabled = false) {
  return `<button class="${cls}" data-action="${action}" ${disabled ? "disabled" : ""}>${label}</button>`;
}

function renderControls(d) {
  const a = d.activity, s = d.slide, t = d.progress.totals;
  let status = "", primary = "", secondary = "";
  if (a.status === "lobby") {
    const missing = d.progress.unassigned, assigned = t.joined - missing;
    status = `📱 Mostrando QR · ${plural(t.joined, "persona unida", "personas unidas")}`;
    if (missing) status += ` · <b>${plural(missing, "persona sin rol", "personas sin rol")}</b>`;
    primary = btn(assigned ? "🎲 Reasignar roles a todos" : "🎲 Asignar roles", "assign_roles", missing && !assigned ? "btn-primary btn-lg" : "btn-lg", !t.joined);
    if (missing && assigned) primary += btn(`🎲 Asignar rol a ${plural(missing, "persona nueva", "personas nuevas")}`, "assign_missing", "btn-primary btn-lg");
    primary += btn("▶️ Comenzar ejercicio", "start", missing ? "btn-lg" : "btn-primary btn-lg", !d.n_slides);
    if (!d.n_slides) status += " · <span class='error'>agrega diapositivas para comenzar</span>";
    else if (missing) status += " <span class='muted small'>(al comenzar, quienes no tengan rol reciben uno automáticamente)</span>";
  } else if (a.status === "finished") {
    status = "🏁 Ejercicio finalizado";
    primary = `<a class="btn btn-primary btn-lg" href="/admin/a/${ACT}/resultados">📋 Resultados por persona</a>`;
    secondary = btn("↩️ Reabrir ejercicio", "reopen");
  } else if (s) {
    status = `Diapositiva <b>${s.index}/${s.total}</b> · <b>Paso ${STEP_NUM[d.step]} de 4</b>: ${STEP_LABEL[d.step]} ${timerHtml(d.deadline, d.paused_ms)}`;
    if (d.step === "texts") primary = btn(`✍️ Paso 2: iniciar propuestas (${a.propose_seconds} s · ${s.num_alternatives} por rol)`, "propose", "btn-primary btn-lg");
    else if (d.step === "propose") primary = btn("⏹️ Cerrar propuestas ahora", "end_now", "btn-lg");
    else if (d.step === "proposed") primary = btn(`🗳️ Paso 3: iniciar votación (${a.vote_seconds} s)`, "vote", "btn-primary btn-lg");
    else if (d.step === "vote") primary = btn("⏹️ Cerrar votación ahora", "end_now", "btn-lg");
    else if (d.step === "results") primary = s.is_last
      ? btn("🏁 Finalizar ejercicio", "finish", "btn-primary btn-lg")
      : btn("⏭️ Siguiente diapositiva", "next", "btn-primary btn-lg");
    if (["texts", "propose", "vote"].includes(d.step)) {
      const paused = d.paused_ms !== null && d.paused_ms !== undefined;
      if (paused) primary += btn("▶️ Reanudar", "resume", "btn-lg");
      else if (d.deadline) primary += btn("⏸️ Pausar", "pause", "btn-lg");
      primary += btn("➕ 15 s", "add15", "btn-lg");
    }
    secondary = btn("⏮️ Anterior", "prev", "", s.index === 1) + btn("Siguiente ⏭️", "next", "", s.is_last)
      + btn("🔄 Reiniciar diapositiva", "reset", "btn-danger") + btn("🏁 Finalizar", "finish");
  }
  if (a.status !== "finished") secondary += btn(a.show_qr ? "🙈 Ocultar QR" : "📱 Mostrar QR", "toggle_qr", a.show_qr ? "btn-active" : "");
  secondary += btn(a.split_contexts ? "🖥️📱 Contextos separados (general en pantalla, rol en teléfonos)" : "🖥️ Todos los contextos en pantalla",
    "toggle_split", a.split_contexts ? "btn-active" : "");
  secondary += btn(a.show_authors ? "👤 Nombres de proponentes: visibles" : "🕶️ Nombres de proponentes: ocultos", "toggle_authors",
    a.show_authors ? "btn-active" : "");
  els.status.innerHTML = status;
  els.primary.innerHTML = primary;
  els.secondary.innerHTML = secondary;

  els.stats.innerHTML = `
    <div><span class="muted">Unidos</span><b>${t.joined}</b></div>
    <div><span class="muted">Conectados</span><b>${t.online}</b></div>
    <div><span class="muted">Propuestas</span><b>${t.submitted}/${t.proposers}</b></div>
    <div><span class="muted">Han votado</span><b>${t.voted}</b></div>
    <div><span class="muted">Faltan por votar</span><b>${t.pending}</b></div>`;
  els.progress.innerHTML = d.progress.profiles.map((p) => `
    <tr style="${cssVars(p)}">
      <td><span class="dot"></span>${p.emoji} ${esc(p.name)}</td>
      <td class="num">${p.joined}</td><td class="num">${p.online}</td>
      <td class="num">${p.submitted}/${p.proposers}</td>
      <td class="num">${p.voted}</td><td class="num">${p.pending}</td>
    </tr>`).join("");
}

function personHtml(x) {
  const cls = x.pending ? "pending" : x.online ? "" : "offline";
  const tip = x.pending ? "Agregado por el admin: aún no entra desde su teléfono" : x.online ? "Conectado" : "Desconectado";
  return `<span class="person ${cls}" title="${tip}">${esc(x.name)}${x.pending ? " ⏳" : ""}<button class="person-x" type="button"
    data-remove="${esc(x.id)}" data-name="${esc(x.name)}" title="Quitar a ${esc(x.name)}" aria-label="Quitar a ${esc(x.name)}">✕</button></span>`;
}
function renderPeople(d) {
  const parts = d.participants || [];
  const groups = [...d.progress.profiles, { profile: "", ...NO_ROLE }];
  els.people.innerHTML = groups.map((p) => {
    const list = parts.filter((x) => x.profile === p.profile);
    if (!p.profile && !list.length) return "";
    return `<div class="lobby-role" style="${cssVars(p)}">
      <div class="lobby-role-head">${p.emoji} ${esc(p.name)} <b>${list.length}</b></div>
      <div class="lobby-names">${list.map(personHtml).join(" ") || "<span class='muted'>—</span>"}</div>
    </div>`;
  }).join("");
}

// ---------- agregar / quitar participantes ----------
const addForm = document.getElementById("add-person");
addForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("add-person-err");
  const r = await postJSON(`/admin/api/a/${ACT}/participants`, { name: addForm.elements.name.value, profile: addForm.elements.profile.value });
  if (!r.ok) { err.textContent = r.data.detail || "No se pudo agregar"; return; }
  err.textContent = "";
  addForm.elements.name.value = "";
  addForm.elements.name.focus();
  poller.force();
});
async function removePerson(id, name) {
  if (!confirm(`¿Quitar a ${name} de la actividad?\nSe borrarán sus propuestas (con los votos que recibieron) y sus votos. Podrá volver a unirse desde su teléfono.`)) return;
  const r = await postJSON(`/admin/api/a/${ACT}/participants/${encodeURIComponent(id)}/delete`);
  if (!r.ok) alert(r.data.detail || "No se pudo quitar");
  poller.force();
}

function render(d) {
  lastData = d;
  renderControls(d);
  renderPeople(d);
  els.board.innerHTML = boardHtml(d, { alwaysText: true, admin: true, qrSrc: `/admin/a/${ACT}/qr.svg` });
  const cur = d.activity.status === "running" && d.slide ? d.slide.id : null;
  document.querySelectorAll(".tl-item").forEach((el) => el.classList.toggle("current", +el.dataset.slideId === cur));
}

// ---------- vista previa (no cambia lo que ven los participantes) ----------
const pv = {
  modal: document.getElementById("preview"),
  board: document.getElementById("pv-board"),
  prev: document.getElementById("pv-prev"),
  next: document.getElementById("pv-next"),
  show: document.getElementById("pv-show"),
  id: null,
};
const slideIds = () => [...document.querySelectorAll(".tl-item")].map((el) => +el.dataset.slideId);

async function openPreview(id) {
  pv.id = id;
  const ids = slideIds(), i = ids.indexOf(id);
  pv.prev.disabled = i <= 0;
  pv.next.disabled = i < 0 || i >= ids.length - 1;
  pv.board.innerHTML = `<div class="waiting">Cargando…</div>`;
  pv.modal.classList.remove("hidden");
  const r = await fetch(`/admin/api/a/${ACT}/preview/${id}`, { credentials: "same-origin", cache: "no-store" });
  if (pv.id !== id) return;
  pv.board.innerHTML = r.ok ? boardHtml(await r.json(), { alwaysText: true }) : `<div class="waiting error">No se pudo cargar la vista previa.</div>`;
}
function closePreview() { pv.modal.classList.add("hidden"); pv.id = null; }
function stepPreview(delta) {
  const ids = slideIds(), i = ids.indexOf(pv.id) + delta;
  if (i >= 0 && i < ids.length) openPreview(ids[i]);
}
pv.prev.addEventListener("click", () => stepPreview(-1));
pv.next.addEventListener("click", () => stepPreview(1));
pv.show.addEventListener("click", () => { const id = pv.id; closePreview(); act("goto", { slide_id: id }); });
document.getElementById("pv-close").addEventListener("click", closePreview);
pv.modal.addEventListener("click", (e) => { if (e.target === pv.modal) closePreview(); });
document.addEventListener("keydown", (e) => {
  if (pv.id === null) return;
  if (e.key === "Escape") closePreview();
  else if (e.key === "ArrowLeft") stepPreview(-1);
  else if (e.key === "ArrowRight") stepPreview(1);
});

// ---------- tiempos de los pasos ----------
const timersForm = document.getElementById("timers");
timersForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(timersForm), note = document.getElementById("timers-note");
  const r = await postJSON(`/admin/api/a/${ACT}/timers`, {
    discuss_seconds: +f.get("discuss_seconds") || 0,
    propose_seconds: +f.get("propose_seconds") || 0,
    vote_seconds: +f.get("vote_seconds") || 0,
  });
  if (!r.ok) { note.textContent = r.data.detail || "No se pudo guardar"; return; }
  for (const k of ["discuss_seconds", "propose_seconds", "vote_seconds"]) timersForm.elements[k].value = r.data[k];
  note.textContent = "✅ Guardado. Se aplica desde el próximo paso que se inicie.";
  poller.force();
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (b && !b.disabled) { e.preventDefault(); act(b.dataset.action); }
  const g = e.target.closest("[data-goto]");
  if (g) { e.preventDefault(); act("goto", { slide_id: +g.dataset.goto }); }
  const p = e.target.closest("[data-preview]");
  if (p) { e.preventDefault(); openPreview(+p.dataset.preview); }
  const rm = e.target.closest("[data-remove]");
  if (rm) { e.preventDefault(); removePerson(rm.dataset.remove, rm.dataset.name); }
  const c = e.target.closest("[data-choose]");
  if (c && !c.disabled) {
    e.preventDefault();
    if (confirm("¿Elegir esta alternativa como ganadora del empate?")) act("choose_winner", { proposal_id: +c.dataset.choose });
  }
});

poller = poll(LIVE, render, 1000);
