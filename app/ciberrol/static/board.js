// Utilidades compartidas: reloj, temporizadores, fase/pasos, cards, QR, indicadores y polling.
const CATEGORIES = ["Preparar", "Contener", "Mitigar", "Recuperar", "Evaluar"];
const STEPS = [
  { n: 1, label: "Escenario y discusión" },
  { n: 2, label: "Propuestas" },
  { n: 3, label: "Votación" },
  { n: 4, label: "Resultados" },
];
const STEP_NUM = { texts: 1, propose: 2, proposed: 2, vote: 3, results: 4 };
const STEP_LABEL = {
  texts: "Escenario y discusión",
  propose: "Proponiendo",
  proposed: "Propuestas listas",
  vote: "Votación",
  results: "Resultados",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function cssVars(p) { return `--c:${p.color};--ink:${p.ink}`; }
function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

// ---------- reloj sincronizado con el servidor y temporizadores ----------
let clockOffset = 0;
function syncClock(serverNow) { clockOffset = serverNow - Date.now(); }
function serverNow() { return Date.now() + clockOffset; }
function secondsLeft(deadline) { return Math.max(0, Math.ceil((deadline - serverNow()) / 1000)); }

const tickHooks = [];
function onTick(fn) { tickHooks.push(fn); }
setInterval(() => {
  document.querySelectorAll("[data-deadline]").forEach((el) => {
    const s = secondsLeft(+el.dataset.deadline);
    el.textContent = s > 0 ? `${s} s` : "¡Tiempo!";
    el.classList.toggle("low", s <= 5);
  });
  tickHooks.forEach((fn) => fn());
}, 200);

// pausedMs: milisegundos que quedaban cuando se pausó (el temporizador queda detenido)
function timerHtml(deadline, pausedMs) {
  if (pausedMs !== null && pausedMs !== undefined) {
    return `<span class="timer paused" title="En pausa">⏸ ${Math.ceil(pausedMs / 1000)} s</span>`;
  }
  if (!deadline) return "";
  return `<span class="timer" data-deadline="${deadline}">${secondsLeft(deadline)} s</span>`;
}

// ---------- fase y pasos ----------
function phasesHtml(category) {
  const cur = CATEGORIES.indexOf(category);
  return `<div class="phases">${CATEGORIES.map((c, i) =>
    `<div class="phase ${i === cur ? "active" : i < cur ? "done" : ""}">${esc(c)}</div>`).join("")}</div>`;
}
function stepsHtml(step) {
  const cur = STEP_NUM[step] || 0;
  return `<div class="steps">${STEPS.map((s) =>
    `<span class="stepdot ${s.n === cur ? "active" : s.n < cur ? "done" : ""}"><b>${s.n}</b> ${s.label}</span>`).join("")}</div>`;
}

// ---------- alternativas y resultados ----------
function altsHtml(alts) {
  return `<ul class="alts">${alts.map((a) => `
    <li class="alt"><span class="letter">${esc(a.letter)}</span>
      <span>${esc(a.text)}${a.author ? ` <span class="author">— ${esc(a.author)}</span>` : ""}</span></li>`).join("")}</ul>`;
}
// opts.admin: muestra los botones para desempatar
function resultsHtml(alts, opts = {}) {
  const undecided = alts.some((a) => a.tie) && !alts.some((a) => a.chosen);
  return `<ul class="alts">${alts.map((a) => `
    <li class="alt ${a.winner ? "winner" : ""}">
      <span class="letter">${esc(a.letter)}</span>
      <span>${esc(a.text)}${a.author ? ` <span class="author">— ${esc(a.author)}</span>` : ""}${a.chosen ? ` <span class="chip ok" title="Elegida para desempatar">🏆 Ganadora</span>` : ""}</span>
      <div class="barrow"><div class="bar"><span style="width:${a.pct}%"></span></div><b>${plural(a.count, "voto", "votos")} · ${a.pct}%</b></div>
      ${opts.admin && a.tie && !a.chosen ? `<div class="barrow"><button class="btn-sm" data-choose="${a.id}">🏆 Elegir como ganadora</button></div>` : ""}
    </li>`).join("")}</ul>${undecided ? `<div class="muted-ink" style="margin-top:.4em">⚖️ Empate${opts.admin ? ": elige la ganadora" : ""}</div>` : ""}`;
}
function proposersHtml(proposers) {
  if (!proposers.length) return `<div class="empty">Nadie conectado en este rol para proponer</div>`;
  if (proposers[0].name === null) {  // nombres ocultos
    return `<div class="proposers">✍️ ${proposers.map((p) => `<span class="chip ${p.submitted ? "ok" : ""}">${p.submitted ? "✓" : "…"}</span>`).join(" ")}
      <span class="muted-ink">${plural(proposers.length, "persona proponiendo", "personas proponiendo")}</span></div>`;
  }
  return `<div class="proposers">✍️ ${proposers.map((p) =>
    `<span class="chip ${p.submitted ? "ok" : ""}">${esc(p.name)}${p.submitted ? " ✓" : ""}</span>`).join(" ")}</div>`;
}

// Markdown básico y seguro: se escapa el HTML primero y luego se aplican
// **negrita**, *cursiva* / _cursiva_, `código`, [enlace](https://…), # títulos, listas - y 1.
function mdInline(t) {
  return t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?![*\w])/g, "$1<em>$2</em>")
    .replace(/(^|[^_\w])_([^_\s](?:[^_]*[^_\s])?)_(?![_\w])/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const out = [];
  let para = [], list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(mdInline).join("<br>")}</p>`); para = []; } };
  const flushList = () => {
    if (list) { out.push(`<${list.type}>${list.items.map((i) => `<li>${mdInline(i)}</li>`).join("")}</${list.type}>`); list = null; }
  };
  for (const line of esc(src || "").split(/\r?\n/)) {
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^\s*(#{1,3})\s+(.*)$/))) {
      flushPara(); flushList();
      const n = m[1].length + 2;
      out.push(`<h${n}>${mdInline(m[2])}</h${n}>`);
      continue;
    }
    const ul = line.match(/^\s*[-*•]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const type = ul ? "ul" : "ol";
      if (!list || list.type !== type) { flushList(); list = { type, items: [] }; }
      list.items.push((ul || ol)[1]);
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara(); flushList();
  return out.join("");
}

function questionHtml(q) {
  return q ? `<div class="question md">${md(q)}</div>` : "";
}

// card de la presentación / administración según el paso
function cardHtml(card, step, opts = {}) {
  const hasCounts = card.total_votes !== undefined;
  let right = "";
  if (hasCounts) right = plural(card.total_votes, "voto", "votos");
  else if (step === "propose" && card.proposers.length)
    right = `${card.proposers.filter((p) => p.submitted).length}/${card.proposers.length} enviadas`;

  let body = "";
  const showText = step === "texts" || step === "propose" || opts.alwaysText;
  if (showText && card.text_hidden) {
    if (step === "texts" || step === "propose") body += `<div class="empty">📱 Escenario en los teléfonos de este rol</div>`;
  } else if (showText) {
    const cls = step === "texts" || step === "propose" ? "card-text" : "card-text small";
    body += card.text ? `<div class="${cls} md">${md(card.text)}</div>` : `<div class="empty">(sin texto)</div>`;
    body += questionHtml(card.question);
  }
  if (step === "propose") body += proposersHtml(card.proposers);
  if (card.alternatives.length) {
    body += hasCounts ? resultsHtml(card.alternatives, opts) : altsHtml(card.alternatives);
  } else if (["proposed", "vote", "results"].includes(step)) {
    body += `<div class="empty" style="margin-top:.6em">Sin propuestas</div>`;
  }
  return `<div class="card" style="${cssVars(card)}">
    <div class="card-head"><span>${card.emoji}</span><span>${esc(card.name)}</span><span class="count">${right}</span></div>${body}</div>`;
}

// Quinta tarjeta: contexto general (ancho completo, 1/4 del alto de las 4 tarjetas)
function contextCardHtml(ctx) {
  if (!ctx) return "";
  return `<div class="card card-context" style="${cssVars(ctx)}">
    <div class="card-head"><span>${ctx.emoji}</span><span>${esc(ctx.name)}</span></div>
    ${ctx.text ? `<div class="card-text md">${md(ctx.text)}</div>` : `<div class="empty">(sin contexto)</div>`}</div>`;
}

function indicatorsHtml(cards) {
  return `<div class="indicators">${cards.map((c) => `
    <div class="ind" style="${cssVars(c)}" title="${esc(c.name)}">
      <div class="ind-top"><span>${c.emoji}</span><span>${esc(c.name)}</span><span class="val">${c.indicator}</span></div>
      <div class="ind-track"><div class="ind-fill" style="width:${c.indicator}%"></div></div>
    </div>`).join("")}</div>`;
}

// ---------- pantalla de ingreso con QR ----------
function lobbyHtml(d, qrSrc) {
  const a = d.activity;
  const parts = d.participants || [];
  const shortUrl = a.join_url.replace(/^https?:\/\//, "");
  const byRole = d.progress.profiles.map((p) => {
    const names = parts.filter((x) => x.profile === p.profile).map((x) => esc(x.name));
    return `<div class="lobby-role" style="${cssVars(p)}">
      <div class="lobby-role-head">${p.emoji} ${esc(p.name)} <b>${names.length}</b></div>
      <div class="lobby-names">${names.join(" · ") || "<span class='muted'>—</span>"}</div></div>`;
  }).join("") + unassignedHtml(parts);
  return `<div class="lobby">
    <div class="lobby-qr"><img src="${qrSrc}" alt="Código QR para unirse"></div>
    <div class="lobby-info">
      <h1>${esc(a.name)}</h1>
      <p class="muted">Escanea el código o entra a</p>
      <div class="lobby-url">${esc(shortUrl)}</div>
      <p class="muted">Código: <b class="lobby-code">${esc(a.code)}</b></p>
      <div class="lobby-count">${plural(parts.length, "persona unida", "personas unidas")}</div>
      <div class="lobby-roles">${byRole}</div>
    </div></div>`;
}

// Participantes que aún no tienen rol asignado
const NO_ROLE = { name: "Sin rol asignado", emoji: "❔", color: "#64748b", ink: "#ffffff" };
function unassignedHtml(parts, withOnline = false) {
  const list = parts.filter((x) => !x.profile);
  if (!list.length) return "";
  return `<div class="lobby-role" style="${cssVars(NO_ROLE)}">
    <div class="lobby-role-head">${NO_ROLE.emoji} ${NO_ROLE.name} <b>${list.length}</b></div>
    <div class="lobby-names">${list.map((x) => `<span class="${withOnline && !x.online ? "offline" : ""}">${esc(x.name)}</span>`).join(" · ")}</div></div>`;
}

// Tablero completo: fase arriba, pasos, grilla 2x2, indicadores abajo.
function boardHtml(d, opts = {}) {
  const a = d.activity;
  if (a.status === "lobby" || a.show_qr) return lobbyHtml(d, opts.qrSrc);
  if (a.status === "finished") {
    return `<div class="waiting"><h1>🏁 Fin del ejercicio</h1><p>${esc(a.name)}</p></div>`;
  }
  if (!d.slide) return `${phasesHtml(null)}<div class="waiting">No hay ninguna diapositiva seleccionada.</div>`;
  const s = d.slide;
  // Con contextos separados, en los pasos 1 y 2 las tarjetas de rol no tienen nada que mostrar
  // en la presentación: solo se muestra el contexto general.
  const contextOnly = !opts.alwaysText && d.cards.length && d.cards[0].text_hidden
    && (d.step === "texts" || d.step === "propose");
  const grid = contextOnly
    ? `<div class="board-grid context-only">${contextCardHtml(d.context)}</div>`
    : `<div class="board-grid">${contextCardHtml(d.context)}${d.cards.map((c) => cardHtml(c, d.step, opts)).join("")}</div>`;
  return `${phasesHtml(s.category)}
    <div class="board-meta">
      <div class="board-title">${s.title ? esc(s.title) : esc(s.category)} <span class="muted">${s.index}/${s.total}</span></div>
      <div class="board-step">${stepsHtml(d.step)} ${timerHtml(d.deadline, d.paused_ms)}</div>
    </div>
    ${grid}
    ${indicatorsHtml(d.cards)}`;
}

// ---------- polling ----------
// Solo re-renderiza cuando cambian los datos (sin contar la hora del servidor).
function poll(url, onData, intervalMs = 1500) {
  let last = null, timer = null, running = false;
  async function tick() {
    if (running) return;
    running = true;
    clearTimeout(timer);
    try {
      const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (r.status === 401) { location.href = "/admin/login"; return; }
      if (r.ok) {
        const d = await r.json();
        if (d.now) syncClock(d.now);
        delete d.now;
        const key = JSON.stringify(d);
        if (key !== last) { last = key; onData(d); }
      }
    } catch (e) { /* red caída: se reintenta */ }
    running = false;
    timer = setTimeout(tick, intervalMs);
  }
  tick();
  return { force() { last = null; running = false; tick(); } };
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* sin cuerpo */ }
  return { ok: r.ok, status: r.status, data };
}
