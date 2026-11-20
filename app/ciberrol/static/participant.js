// Vista móvil de participantes.
const CODE = window.CIBERROL_CODE;
const API = `/api/a/${CODE}`;
const root = document.getElementById("app");
let state = null;
let editing = false;     // mostrando formulario para cambiar nombre/perfil
let viewKey = null;      // evita re-render mientras se escribe la propuesta
let poller;

// ---------- unirse ----------
function renderJoin(d) {
  const profiles = d.profiles;
  const cur = d.joined ? d.profile : null;
  root.innerHTML = `
    <h1>${esc(d.activity.name)}</h1>
    ${d.activity.status === "finished" ? `<div class="waiting">Esta actividad ya terminó.</div>` : `
    <form id="join">
      <label for="name">Tu nombre</label>
      <input id="name" type="text" maxlength="60" required autocomplete="name" value="${esc(d.name || "")}">
      <label>¿Cuál es tu rol?</label>
      <div class="profile-picker">
        ${Object.entries(profiles).map(([k, p]) => `
          <label class="profile-btn ${k === cur ? "selected" : ""}" style="${cssVars(p)}">
            <input type="radio" name="profile" value="${k}" ${k === cur ? "checked" : ""} required> ${p.emoji} ${esc(p.name)}
          </label>`).join("")}
      </div>
      <p class="error" id="err"></p>
      <button class="btn-primary btn-block" type="submit">${d.joined ? "Guardar" : "Unirme"}</button>
      ${d.joined ? `<button class="btn-block" type="button" id="cancel">Cancelar</button>` : ""}
    </form>`}`;
  const form = root.querySelector("#join");
  if (!form) return;
  form.querySelectorAll("input[name=profile]").forEach((r) => r.addEventListener("change", () => {
    form.querySelectorAll(".profile-btn").forEach((l) => l.classList.toggle("selected", l.contains(r) && r.checked));
  }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = form.querySelector("#name").value.trim();
    const profile = (form.querySelector("input[name=profile]:checked") || {}).value;
    if (!name || !profile) return;
    const r = await postJSON(`${API}/join`, { name, profile });
    if (!r.ok) { form.querySelector("#err").textContent = r.data.detail || "Error"; return; }
    editing = false;
    viewKey = null;
    poller.force();
  });
  const cancel = root.querySelector("#cancel");
  if (cancel) cancel.addEventListener("click", () => { editing = false; viewKey = null; render(state); });
}

// ---------- propuesta ----------
let saveTimer = null;
let flushed = false;

async function saveProposal(final) {
  const ta = root.querySelector("#proposal");
  if (!ta) return;
  clearTimeout(saveTimer);
  const r = await postJSON(`${API}/proposal`, { text: ta.value, final });
  if (final) { viewKey = null; poller.force(); }
  return r;
}

onTick(() => {
  // Al acabarse el tiempo se envía lo que alcanzó a escribir.
  if (!state || state.step !== "propose" || !state.deadline) return;
  const ta = root.querySelector("#proposal");
  if (ta && !flushed && secondsLeft(state.deadline) === 0) {
    flushed = true;
    ta.disabled = true;
    saveProposal(true);
  }
});

function proposeHtml(d) {
  const c = d.card;
  return `<div class="card" style="${cssVars(c)}">
    <div class="card-head"><span>${c.emoji}</span><span>¡Te toca proponer!</span><span class="count">${timerHtml(d.deadline)}</span></div>
    ${c.text ? `<div class="card-text small md">${md(c.text)}</div>` : ""}
    ${questionHtml(c.question)}
    <label for="proposal" class="question" style="color:inherit">${c.question ? "Tu propuesta" : "¿Qué pasos propones seguir?"}</label>
    <textarea id="proposal" maxlength="400" placeholder="Escribe tu propuesta…">${esc(d.my_proposal.text)}</textarea>
    <div class="row-between"><span class="muted-ink" id="chars"></span>
      <button class="btn-primary" id="send">Enviar propuesta</button></div>
    <p class="note-ink">Si se acaba el tiempo, se envía lo que alcanzaste a escribir.</p>
  </div>`;
}

// ---------- vista principal ----------
function header(d) {
  const p = d.profiles[d.profile];
  return `<div class="me-bar">
    <span class="me-chip" style="${cssVars(p)}">${p.emoji} ${esc(d.name)}</span>
    <button class="btn-sm" id="change">Cambiar</button></div>`;
}

function render(d) {
  state = d;
  if (!d.joined || editing) { viewKey = null; return renderJoin(d); }

  // Mientras se escribe la propuesta no se re-renderiza (no perder el foco ni el texto).
  const proposing = d.step === "propose" && d.is_proposer && d.my_proposal && !d.my_proposal.submitted;
  const key = proposing ? `propose-${d.slide.id}` : null;
  if (key && key === viewKey) {
    // el admin pudo adelantar el cierre: actualizar solo el temporizador
    root.querySelectorAll("[data-deadline]").forEach((el) => { el.dataset.deadline = d.deadline; });
    return;
  }
  viewKey = key;
  if (!proposing) flushed = false;

  let html = header(d);
  if (d.activity.status === "finished") {
    html += `<div class="waiting"><h2>🏁 Ejercicio finalizado</h2><p>¡Gracias por participar!</p></div>`;
  } else if (d.activity.status === "lobby" || !d.slide) {
    html += `<div class="waiting"><h2>${esc(d.activity.name)}</h2><p>Esperando que comience el ejercicio…</p></div>`;
  } else {
    const c = d.card;
    html += phasesHtml(d.slide.category) + stepsHtml(d.step);
    if (d.slide.title) html += `<h2>${esc(d.slide.title)}</h2>`;
    const cardOpen = (title, right = "") => `<div class="card" style="${cssVars(c)}">
      <div class="card-head"><span>${c.emoji}</span><span>${title}</span><span class="count">${right}</span></div>`;

    const ctx = d.context && d.context.text && (d.step === "texts" || (d.step === "propose" && !proposing))
      ? contextCardHtml(d.context) + `<div style="height:10px"></div>` : "";
    html += ctx;
    if (d.step === "texts") {
      html += cardOpen(esc(c.name)) + `<div class="card-text md">${md(c.text) || "<span class='empty'>(sin texto)</span>"}</div>${questionHtml(c.question)}</div>`;
    } else if (d.step === "propose") {
      if (proposing) {
        html += proposeHtml(d);
      } else {
        html += cardOpen(esc(c.name), timerHtml(d.deadline)) + `<div class="card-text small md">${md(c.text)}</div>${questionHtml(c.question)}`;
        if (d.is_proposer) html += `<p class="note-ink">✅ Propuesta enviada:</p><div class="quote">${esc(d.my_proposal.text) || "(vacía)"}</div>`;
        else html += `<p class="note-ink">${d.proposers.length ? `Están proponiendo: <b>${d.proposers.map(esc).join(", ")}</b>`
          : d.n_proposers ? `${plural(d.n_proposers, "persona de tu rol está", "personas de tu rol están")} proponiendo.` : "Nadie de tu rol está proponiendo."}</p>`;
        html += `</div>`;
      }
    } else if (d.step === "proposed") {
      html += cardOpen("Propuestas") + questionHtml(c.question) + (d.alternatives.length ? altsHtml(d.alternatives) : `<div class="empty">Sin propuestas para tu rol</div>`)
        + `<p class="note-ink">La votación comenzará en breve…</p></div>`;
    } else if (d.step === "vote") {
      if (!d.alternatives.length) {
        html += cardOpen("Votación") + `<div class="empty">No hay propuestas para tu rol en esta diapositiva.</div></div>`;
      } else if (d.my_vote || d.can_vote) {
        const voted = !!d.my_vote;
        html += cardOpen(voted ? "Tu voto" : "Vota una alternativa", timerHtml(d.deadline)) + questionHtml(c.question) + `<div class="vote-btns">
          ${d.alternatives.map((a) => `
            <button class="vote-btn ${d.my_vote === a.letter ? "chosen" : ""}" data-id="${a.id}" data-letter="${esc(a.letter)}" ${voted ? "disabled" : ""}>
              <span class="letter">${esc(a.letter)}</span><span>${esc(a.text)}</span>
            </button>`).join("")}</div>
          ${voted ? `<p class="note-ink">✅ Voto registrado (${esc(d.my_vote)}). Espera los resultados.</p>` : ""}</div>`;
      } else {
        html += cardOpen("Votación", timerHtml(d.deadline)) + altsHtml(d.alternatives)
          + `<p class="note-ink">${d.is_proposer ? "Como propusiste, en esta diapositiva no votas." : "No puedes votar en esta ronda."}</p></div>`;
      }
    } else if (d.step === "results") {
      // Resultados de las 4 tarjetas (la propia primero)
      html += (d.my_vote ? `<p class="vote-note">Votaste: ${esc(d.my_vote)}</p>` : "")
        + `<div class="results-stack">${(d.all_results || []).map((r) => cardHtml(r, "results")).join("")}</div>`;
    }
  }
  root.innerHTML = html;
  bind(d);
}

function bind(d) {
  root.querySelector("#change")?.addEventListener("click", () => { editing = true; render(state); });

  const ta = root.querySelector("#proposal");
  if (ta) {
    const chars = root.querySelector("#chars");
    const upd = () => { chars.textContent = `${ta.value.length}/400`; };
    upd();
    ta.addEventListener("input", () => {
      upd();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveProposal(false), 600);
    });
    root.querySelector("#send").addEventListener("click", async () => {
      if (!ta.value.trim() && !confirm("Tu propuesta está vacía. ¿Enviar igual?")) return;
      flushed = true;
      ta.disabled = true;
      const r = await saveProposal(true);
      if (r && !r.ok) alert(r.data.detail || "No se pudo enviar");
    });
  }

  root.querySelectorAll(".vote-btn[data-id]:not(:disabled)").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm(`¿Confirmas tu voto por la alternativa ${b.dataset.letter}? No podrás cambiarlo.`)) return;
      root.querySelectorAll(".vote-btn").forEach((x) => (x.disabled = true));
      const r = await postJSON(`${API}/vote`, { proposal_id: +b.dataset.id });
      if (!r.ok) alert(r.data.detail || "No se pudo registrar el voto");
      poller.force();
    }));
}

poller = poll(`${API}/state`, render, 1500);
