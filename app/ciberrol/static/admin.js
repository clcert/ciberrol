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

async function act(action, body) {
  if (action === "finish" && !confirm("¿Finalizar el ejercicio? Se mostrarán los resultados por persona.")) return;
  if (action === "reset" && !confirm("Se borrarán propuestas y votos de esta diapositiva y volverá al paso 1. ¿Continuar?")) return;
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
    status = `📱 Mostrando QR · ${plural(t.joined, "persona unida", "personas unidas")}`;
    primary = btn("▶️ Comenzar ejercicio", "start", "btn-primary btn-lg", !d.n_slides);
    if (!d.n_slides) status += " · <span class='error'>agrega diapositivas para comenzar</span>";
  } else if (a.status === "finished") {
    status = "🏁 Ejercicio finalizado";
    primary = `<a class="btn btn-primary btn-lg" href="/admin/a/${ACT}/resultados">📋 Resultados por persona</a>`;
    secondary = btn("↩️ Reabrir ejercicio", "reopen");
  } else if (s) {
    status = `Diapositiva <b>${s.index}/${s.total}</b> · <b>Paso ${STEP_NUM[d.step]} de 4</b>: ${STEP_LABEL[d.step]} ${timerHtml(d.deadline)}`;
    if (d.step === "texts") primary = btn(`✍️ Paso 2: iniciar propuestas (${s.propose_seconds} s · ${s.num_alternatives} por rol)`, "propose", "btn-primary btn-lg");
    else if (d.step === "propose") primary = btn("⏹️ Cerrar propuestas ahora", "end_now", "btn-lg");
    else if (d.step === "proposed") primary = btn(`🗳️ Paso 3: iniciar votación (${s.vote_seconds} s)`, "vote", "btn-primary btn-lg");
    else if (d.step === "vote") primary = btn("⏹️ Cerrar votación ahora", "end_now", "btn-lg");
    else if (d.step === "results") primary = s.is_last
      ? btn("🏁 Finalizar ejercicio", "finish", "btn-primary btn-lg")
      : btn("⏭️ Siguiente diapositiva", "next", "btn-primary btn-lg");
    secondary = btn("⏮️ Anterior", "prev", "", s.index === 1) + btn("Siguiente ⏭️", "next", "", s.is_last)
      + btn("🔄 Reiniciar diapositiva", "reset", "btn-danger") + btn("🏁 Finalizar", "finish");
  }
  if (a.status !== "finished") secondary += btn(a.show_qr ? "🙈 Ocultar QR" : "📱 Mostrar QR", "toggle_qr", a.show_qr ? "btn-active" : "");
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

function renderPeople(d) {
  const parts = d.participants || [];
  els.people.innerHTML = d.progress.profiles.map((p) => {
    const list = parts.filter((x) => x.profile === p.profile);
    return `<div class="lobby-role" style="${cssVars(p)}">
      <div class="lobby-role-head">${p.emoji} ${esc(p.name)} <b>${list.length}</b></div>
      <div class="lobby-names">${list.map((x) => `<span class="${x.online ? "" : "offline"}">${esc(x.name)}</span>`).join(" · ") || "<span class='muted'>—</span>"}</div>
    </div>`;
  }).join("");
}

function render(d) {
  renderControls(d);
  renderPeople(d);
  els.board.innerHTML = boardHtml(d, { alwaysText: true, qrSrc: `/admin/a/${ACT}/qr.svg` });
  const cur = d.activity.status === "running" && d.slide ? d.slide.id : null;
  document.querySelectorAll(".tl-item").forEach((el) => el.classList.toggle("current", +el.dataset.slideId === cur));
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (b && !b.disabled) { e.preventDefault(); act(b.dataset.action); }
  const g = e.target.closest("[data-goto]");
  if (g) { e.preventDefault(); act("goto", { slide_id: +g.dataset.goto }); }
});

poller = poll(LIVE, render, 1000);
