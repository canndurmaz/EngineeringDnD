/* The table. One EventSource, one state snapshot, plain DOM updates. */
const ROOM = window.ROOM_ID;
let lastSeq = 0;
let me = null;

const el = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));

const api = async (url, options) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `request failed (${response.status})`);
  return body;
};

/* The party panel's avatar. The URL goes into an HTML attribute, so every part
   is URL-encoded and the result is escaped -- same rule as lobby.js. */
const LOOK_KINDS = ["hair", "face", "eyes", "outfit", "skin"];
const avatarUrl = (look) => "/api/avatar.svg?" + LOOK_KINDS
  .map((kind) => `${encodeURIComponent(kind)}=${encodeURIComponent((look || {})[kind] ?? "")}`)
  .join("&");

const bar = (value, max, kind) => `
  <div class="bar-track"><div class="bar-fill ${kind}"
    style="width:${max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0}%"></div></div>`;

/* --- rendering ------------------------------------------------------------ */

function renderParty(state) {
  el("party").innerHTML = Object.values(state.characters).map((c) => {
    const active = state.turn.active_player_id === c.player_id;
    const down = c.stamina <= 0;
    return `<div class="member ${active ? "active" : ""} ${down ? "down" : ""}">
      <div class="member-row">
        <img class="avatar" alt="" loading="lazy"
             src="${esc(avatarUrl(c.appearance))}">
        <div class="lines">
          <div class="who">
            <span>${esc(c.name)}${down ? " · burned out" : ""}</span>
            <span class="num">L${c.level}</span>
          </div>
          <div class="role">${esc(c.class_id.replace(/_/g, " "))}</div>
        </div>
      </div>
      <div class="num" style="font-size:12px">
        ${c.stamina}/${c.max_stamina} stamina</div>
      ${bar(c.stamina, c.max_stamina, "stamina")}
      <div class="num" style="font-size:12px">${c.focus}/${c.max_focus} focus</div>
      ${bar(c.focus, c.max_focus, "focus")}
    </div>`;
  }).join("");
}

function renderHazard(state) {
  const h = state.hazard;
  if (!h) { el("hazard-body").innerHTML = "<p class='roll'>None active.</p>"; return; }
  el("hazard-body").innerHTML = `
    <p style="font-weight:600;margin:0">${esc(h.name)}${h.is_boss ? " ⚑" : ""}</p>
    <p class="role" style="margin:4px 0 10px">${esc(h.description)}</p>
    <div class="num" style="font-size:12px">${h.severity}/${h.max_severity} severity</div>
    ${bar(h.severity, h.max_severity, "severity")}
    <div class="roll" style="margin-top:10px">
      weakness: ${h.weakness ? esc(h.weakness) : "unknown"}<br>
      difficulty: ${h.dc ?? "unknown"}<br>
      next: ${h.attack_type ? esc(h.attack_type.replace(/_/g, " ")) : "unknown"}
    </div>`;
}

function renderRail(state) {
  const p = state.party;
  el("rail-body").innerHTML = `
    <div class="rail-row"><span>Budget</span><span class="v">${p.budget}</span></div>
    <div class="rail-row"><span>Schedule</span><span class="v">${p.schedule} d</span></div>
    <div class="rail-row"><span class="debt">Technical Debt</span>
      <span class="v debt">${p.tech_debt}</span></div>
    <div class="debt-meter" style="width:${Math.min(100, p.tech_debt * 2)}%"></div>
    <p class="roll" style="margin-top:8px">
      DC penalty: +${Math.floor(p.tech_debt / 10)}</p>`;

  el("phase-track").innerHTML = Array.from({ length: state.phase.count }, (_, i) =>
    `<i class="${i < state.phase.index ? "done" : i === state.phase.index ? "now" : ""}"></i>`
  ).join("");
  el("phase-label").textContent =
    `${state.phase.name} · ${state.phase.index + 1}/${state.phase.count}`;
}

function renderAbilities(state) {
  me = state.you;
  const mine = state.turn.active_player_id === me?.player_id;
  el("turn-hint").textContent = me
    ? (mine ? "it is your turn" : "waiting for another engineer")
    : "you are watching";
  el("pass").disabled = !mine;
  if (!me) { el("abilities").innerHTML = ""; return; }

  el("abilities").innerHTML = me.abilities.map((a) => {
    let why = "";
    if (!mine) why = "not your turn";
    else if (me.focus < a.focus_cost) why = `needs ${a.focus_cost} focus, you have ${me.focus}`;
    else if (me.stamina <= 0) why = "you are burned out";
    return `<button class="ability" data-ability="${a.id}" ${why ? "disabled" : ""}>
      <span style="font-weight:600">${esc(a.name)}</span>
      <span class="cost"> ${a.focus_cost}F · ${esc(a.stat)}</span>
      <span class="why">${esc(why || a.flavor)}</span>
    </button>`;
  }).join("");
}

/* --- the log -------------------------------------------------------------- */

function entryFor(seq) {
  let node = el(`e${seq}`);
  if (!node) {
    node = document.createElement("div");
    node.className = "entry pending";
    node.id = `e${seq}`;
    el("log").append(node);
    el("log").scrollTop = el("log").scrollHeight;
  }
  return node;
}

function renderEvent(event) {
  if (event.seq > lastSeq) lastSeq = event.seq;

  if (event.kind === "premise") { el("premise").textContent = event.premise; return; }

  if (event.kind === "narration_chunk") {
    const node = entryFor(event.event_seq);
    let prose = node.querySelector(".prose");
    if (!prose) {
      prose = document.createElement("p");
      prose.className = "prose";
      prose.dataset.streaming = "1";
      node.append(prose);
    }
    if (prose.dataset.streaming === "1") prose.textContent += event.delta;
    el("log").scrollTop = el("log").scrollHeight;
    return;                       // no refresh(): chunks change no game state
  }

  if (event.kind === "narration") {
    const node = entryFor(event.event_seq ?? event.seq);
    node.classList.remove("pending");
    let prose = node.querySelector(".prose");
    if (!prose) { prose = document.createElement("p"); prose.className = "prose"; node.append(prose); }
    prose.dataset.streaming = "0";
    prose.textContent = event.text;
    el("log").scrollTop = el("log").scrollHeight;
    return;
  }

  if (event.kind === "interlude") {
    const node = entryFor(event.seq);
    node.classList.remove("pending");
    node.insertAdjacentHTML("beforeend",
      `<div class="roll">${esc(event.phase || "")} gate</div>
       <p class="prose">${esc(event.text || "")}</p>`);
    el("log").scrollTop = el("log").scrollHeight;
    return;
  }

  if (event.kind === "action") {
    const node = entryFor(event.seq);
    const hit = event.outcome === "success" || event.outcome === "crit";
    const sign = hit ? "≥" : "<";
    node.insertAdjacentHTML("afterbegin", `
      <div class="roll ${event.outcome}">
        <span class="nat">${event.natural}</span>
        ${event.stat_mod >= 0 ? "+" : ""}${event.stat_mod}
        ${event.roll_bonus ? `+${event.roll_bonus}` : ""}
        = ${event.total} ${sign} DC ${event.dc ?? "?"}
        · ${esc(event.ability_name)} · ${esc(event.outcome)}
        ${event.rerolled ? " · rerolled" : ""}
      </div>`);
    if (event.outcome === "crit") node.classList.add("flash-crit");
    if (event.outcome === "fumble") node.classList.add("flash-fumble");
    return;
  }

  const labels = {
    hazard_attack: "The problem bites back.",
    hazard_defeated: "Problem closed.",
    phase_advanced: "Phase gate cleared.",
    game_over: "The programme has ended.",
    player_joined: "A new engineer joins.",
    game_started: "The programme begins.",
    campaign_updated: "The plan is revised.",
    passed: "Turn passed.",
  };
  if (labels[event.kind]) {
    const node = entryFor(event.seq);
    node.classList.remove("pending");
    node.insertAdjacentHTML("beforeend",
      `<div class="roll">${labels[event.kind]}</div>`);
  }
}

/* --- wiring --------------------------------------------------------------- */

async function refresh() {
  const state = await api(`/api/rooms/${ROOM}/state`);
  renderParty(state); renderHazard(state); renderRail(state); renderAbilities(state);
  el("dm-badge").textContent = `DM: ${state.narrator ?? "template"}`;
  if (state.room.premise) el("premise").textContent = state.room.premise;
  return state;
}

function connect() {
  const source = new EventSource(`/api/rooms/${ROOM}/stream?since=${lastSeq}`);
  source.onmessage = () => {};
  ["action", "narration", "narration_chunk", "premise", "hazard_attack", "hazard_defeated",
   "phase_advanced", "game_over", "player_joined", "game_started",
   "campaign_updated", "passed", "room_created", "interlude"].forEach((kind) => {
    source.addEventListener(kind, (message) => {
      const event = JSON.parse(message.data);
      renderEvent(event);
      if (event.kind !== "narration_chunk") refresh();
    });
  });
  source.onerror = () => {
    source.close();
    setTimeout(connect, 2000);       // reconnect resumes from lastSeq
  };
}

el("abilities").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-ability]");
  if (!button || button.disabled) return;
  el("action-error").textContent = "";
  try {
    await api(`/api/rooms/${ROOM}/action`, {
      method: "POST",
      body: JSON.stringify({ ability_id: button.dataset.ability }),
    });
  } catch (error) { el("action-error").textContent = error.message; }
});

el("pass").addEventListener("click", async () => {
  el("action-error").textContent = "";
  try { await api(`/api/rooms/${ROOM}/end-turn`, { method: "POST" }); }
  catch (error) { el("action-error").textContent = error.message; }
});

refresh().then(connect);
