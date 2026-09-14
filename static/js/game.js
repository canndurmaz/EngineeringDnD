/* The table. One EventSource, one state snapshot, plain DOM updates. */
const ROOM = window.ROOM_ID;
let lastSeq = 0;
let me = null;
/* The six stat names and each class's primary, both straight from
   /api/classes -- the client invents neither. */
let STATS = [];
let PRIMARY = {};
let CLASS_NAMES = {};          // class_id -> the display name /api/classes gives
let lastState = null;          // the most recent snapshot, for naming actors
let levelStat = null;          // what this player has chosen, if anything

/* Anything already committed when the stream opens is history, not news --
   see isLive(). */
let replayUntil = 0;
const ROLL_MS = 600;                   // must match the d20 tumble in theme.css
const isLive = (event) => event.seq > replayUntil;

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

/* "4 seated", and the split once bots are at the table. */
function seatedLabel(size) {
  if (!size) return "";
  const bots = size.bots || 0;
  return `${size.seated} seated` +
    (bots ? ` (${bots} bot${bots === 1 ? "" : "s"})` : "");
}

function renderParty(state) {
  const count = el("party-count");
  if (count) count.textContent = seatedLabel(state.party_size);
  el("party").innerHTML = Object.values(state.characters).map((c) => {
    const active = state.turn.active_player_id === c.player_id;
    const down = c.stamina <= 0;
    return `<div class="member ${active ? "active" : ""} ${down ? "down" : ""}">
      <div class="member-row">
        <img class="avatar" alt="" loading="lazy"
             src="${esc(avatarUrl(c.appearance))}">
        <div class="lines">
          <div class="who">
            <span>${esc(c.name)}${c.is_bot ? '<span class="chip-bot">BOT</span>' : ""}${down ? " · burned out" : ""}</span>
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

/* A gate boss carries one special rule and the player is always told it: the
   whole point is that they can see the trap before they step in it. The stats a
   rule punishes are read straight off the same payload by renderAbilities, so
   the card and the buttons can never disagree about what is being penalised. */
const bossRule = (state) =>
  (state && state.hazard && state.hazard.rule) ? state.hazard.rule : null;

const penalisedStats = (state) => {
  const rule = bossRule(state);
  const stats = rule && rule.params ? rule.params.stats : null;
  return Array.isArray(stats) ? stats : [];
};

function renderHazard(state) {
  const h = state.hazard;
  if (!h) { el("hazard-body").innerHTML = "<p class='roll'>None active.</p>"; return; }
  const rule = h.rule
    ? `<p class="boss-rule"><span class="boss-rule-tag">special rule</span>
       ${esc(h.rule.text || h.rule.id || "")}</p>` : "";
  el("hazard-body").innerHTML = `
    <p style="font-weight:600;margin:0">${esc(h.name)}${h.is_boss ? " ⚑" : ""}</p>
    <p class="role" style="margin:4px 0 10px">${esc(h.description)}</p>
    ${rule}
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

/* The table waits for the DM, and the server is the one that decides so -- two
   browsers must never disagree about whether the game is paused. /state carries
   the gate; the client only reads it. */
const dmWaiting = (state) => !!(state && state.dm_wait && state.dm_wait.waiting);
const DM_WRITING_WHY = "the DM is writing";

/* A move can only land when the room is running, the player holds a seat, and
   the turn is theirs. The buttons say the same thing the server would. */
const canAct = (state) =>
  state.room.status === "active" && !!state.you &&
  state.turn.active_player_id === state.you.player_id;

function renderAbilities(state) {
  me = state.you;
  const lobby = state.room.status === "lobby";
  const running = state.room.status === "active";
  const mine = canAct(state);
  const gated = dmWaiting(state);
  el("turn-hint").textContent = gated ? DM_WRITING_WHY
    : !me ? "you are watching"
    : lobby ? "the programme has not started"
    : !running ? "the programme has ended"
    : mine ? "it is your turn" : "waiting for another engineer";
  el("pass").disabled = !mine;
  if (gated) el("pass").disabled = true;      // nobody plays past the DM
  if (!me) {
    el("abilities").innerHTML =
      `<p class="watching">You are watching this table. Take a seat on the
       join page to play.</p>`;
    return;
  }

  const penalised = penalisedStats(state);
  el("abilities").innerHTML = me.abilities.map((a) => {
    const hit = penalised.includes(a.stat);
    const rule = bossRule(state);
    const penalty = hit && rule && rule.params && rule.params.penalty
      ? Number(rule.params.penalty) : 4;
    let why = "";
    if (gated) why = DM_WRITING_WHY;
    else if (lobby) why = "the programme hasn't started";
    else if (!running) why = "the programme has ended";
    else if (!mine) why = "not your turn";
    else if (me.focus < a.focus_cost) why = `needs ${a.focus_cost} focus, you have ${me.focus}`;
    else if (me.stamina <= 0) why = "you are burned out";
    return `<button class="ability${hit ? " penalised" : ""}" data-ability="${a.id}"
      ${why ? "disabled" : ""}>
      <span style="font-weight:600">${esc(a.name)}</span>
      <span class="cost"> ${a.focus_cost}F · ${esc(a.stat)}${
        hit ? `<span class="penalty">−${esc(String(penalty))}</span>` : ""}</span>
      <span class="why">${esc(why || a.flavor)}</span>
    </button>`;
  }).join("");
}

/* "+1 to a stat of choice" on the next level-up. The endpoint has always
   existed; without this row nobody could reach it and every player silently
   took their class primary. */
function renderLevelChoice(state) {
  const box = el("level-choice");
  if (!box) return;
  if (!state.you || !STATS.length) { box.innerHTML = ""; return; }
  const current = levelStat || PRIMARY[state.you.class_id] || STATS[0];
  box.innerHTML = `
    <div class="who"><span>Next level-up</span>
      <span class="num">+1 ${esc(current)}</span></div>
    <div class="stats">${STATS.map((stat) => `
      <button type="button" class="stat" data-stat="${esc(stat)}"
              aria-pressed="${stat === current ? "true" : "false"}">
        ${esc(stat)}</button>`).join("")}</div>
    <p class="roll" id="level-choice-error" role="alert"></p>`;
}

/* --- the system map ------------------------------------------------------- */

/* The dungeon is the architecture: one node per subsystem of the machine the
   party is designing, coloured by where this phase's work sits. The SVG is
   generated here rather than shipped as a file so it can follow the state, and
   drawn on a fixed two-column grid so a node never moves between renders --
   only its colour changes.

   Nothing here comes from a hazard: /state's `map` carries subsystem names and
   a status word, never a DC, a weakness or an unrevealed problem's name. */

const MAP_COLS = 2;          /* two columns reads at 400px; more does not */
const MAP_W = 140, MAP_H = 46, MAP_GX = 16, MAP_GY = 14;

/* At most two lines per node, so a long name wraps instead of overflowing the
   box. Words after the second line are dropped with an ellipsis. */
function nodeLines(name) {
  const words = String(name || "").split(/\s+/).filter(Boolean);
  if (words.length < 2) return [words.join(" ")];
  const half = Math.ceil(words.length / 2);
  return [words.slice(0, half).join(" "), words.slice(half).join(" ")];
}

function renderMap(state) {
  const box = el("map-body");
  if (!box) return;
  const nodes = ((state.map || {}).nodes) || [];
  const caption = el("map-active");
  if (!nodes.length) {
    box.innerHTML = "";
    if (caption) caption.textContent = "";
    return;
  }
  const rows = Math.ceil(nodes.length / MAP_COLS);
  const width = MAP_COLS * MAP_W + (MAP_COLS - 1) * MAP_GX;
  const height = rows * MAP_H + (rows - 1) * MAP_GY;

  const cells = nodes.map((n, i) => {
    const x = (i % MAP_COLS) * (MAP_W + MAP_GX);
    const y = Math.floor(i / MAP_COLS) * (MAP_H + MAP_GY);
    const lines = nodeLines(n.name);
    const first = lines.length > 1 ? MAP_H / 2 - 3 : MAP_H / 2 + 4;
    const text = lines.map((line, k) =>
      `<tspan x="${MAP_W / 2}" y="${first + k * 12}">${esc(line)}</tspan>`).join("");
    return `<g class="node ${esc(n.status)}" transform="translate(${x},${y})">
      <title>${esc(n.name)} — ${esc(n.blurb)}</title>
      <rect width="${MAP_W}" height="${MAP_H}" rx="4"></rect>
      <text text-anchor="middle">${text}</text>
    </g>`;
  }).join("");

  /* A faint spine down the middle, so it reads as one machine rather than a
     row of unrelated boxes. Decoration only; it carries no state. */
  const spine = `<line class="spine" x1="${width / 2}" y1="0"
    x2="${width / 2}" y2="${height}"></line>`;

  box.innerHTML = `<svg class="schematic-svg" viewBox="0 0 ${width} ${height}"
    role="img" aria-label="System map" preserveAspectRatio="xMidYMid meet">
    ${spine}${cells}</svg>`;

  if (caption) {
    const active = nodes.find((n) => n.status === "active");
    /* textContent, not innerHTML: the hazard name is written by the model. */
    caption.textContent = state.hazard
      ? `${state.hazard.name}${active ? ` · ${active.name}` : ""}`
      : "No open problem.";
  }
}

/* --- table talk ----------------------------------------------------------- */

let chatSince = 0;             /* the highest message id already on the page */

/* Chat bodies are player-typed and reach the DOM, so every field goes through
   esc() -- the same rule as every other panel here. */
function appendChat(message) {
  const log = el("chat-log");
  if (!log) return;
  const node = document.createElement("div");
  node.className = "chat-msg";
  node.innerHTML = `<span class="chat-who">${esc(message.name)}${
    message.is_bot ? '<span class="chip-bot">BOT</span>' : ""}</span>
    <span class="chat-body">${esc(message.body)}</span>`;
  log.append(node);
  log.scrollTop = log.scrollHeight;
}

function showChat(message) {
  const id = Number(message.id ?? message.message_id ?? 0);
  if (id && id <= chatSince) return;         /* already shown; SSE and the
                                                backfill can both deliver it */
  if (id) chatSince = id;
  appendChat(message);
}

/* The history, once, before the stream opens. */
async function pullChat() {
  try {
    const data = await api(`/api/rooms/${ROOM}/chat?since=${chatSince}`);
    (data.messages || []).forEach(showChat);
  } catch (error) { /* the stream will carry anything said from now on */ }
}

function renderChatSeat(state) {
  const input = el("chat-input"), send = el("chat-send"), note = el("chat-note");
  if (!input || !send) return;
  const seated = !!state.you;
  input.disabled = !seated;
  send.disabled = !seated;
  if (note) note.textContent = seated ? "" : "Take a seat to talk at this table.";
}

/* --- the annunciator ------------------------------------------------------ */

/* The one place that answers "what should I do right now?". Everything it says
   comes from room.status, whose turn it is, and whether the DM still owes us a
   line -- never from the player's own data alone. */

const ENDED = {
  lost_budget: "The budget ran out.",
  lost_schedule: "The schedule ran out.",
  lost_burnout: "The whole team burned out.",
};

/* A narration is outstanding when an action entry is still .pending: the log
   marks one the moment the action lands and clears it when the prose arrives.
   No new server state -- the signal is already on the page. */
const narrationOutstanding = () => !!el("log").querySelector(".entry.pending");

function renderAnnunciator(state) {
  const band = el("annunciator");
  if (!band) return;
  const status = state.room.status;
  const activeId = state.turn.active_player_id;
  const active = activeId ? state.characters[activeId] : null;
  const seated = (state.party_size || {}).seated ?? 0;

  let lamp = "other", word = "", say = "", offerStart = false, offerSkip = false;

  if (status === "lobby") {
    lamp = "lobby";
    word = "waiting";
    /* Only a seated engineer can start: /start answers 403 to anyone else, so
       telling a spectator to "start when everyone's in" is an instruction they
       cannot carry out. They are told what is actually true of them instead,
       and get no button. */
    offerStart = !!state.you;
    say = offerStart
      ? `${seated} seated. Start when everyone's in.`
      : `${seated} seated. Waiting for a seated engineer to start.`;
  } else if (status === "won") {
    lamp = "won";
    word = "shipped";
    say = `${state.room.name} passed qualification.`;
  } else if (status !== "active") {
    lamp = "lost";
    word = "over";
    say = ENDED[status] || "The programme is cancelled.";
  } else if (dmWaiting(state)) {
    lamp = "writing";
    word = "dm writing";
    say = "The table is waiting on the DM. Skip if you would rather play on.";
    offerSkip = true;
  } else if (narrationOutstanding()) {
    lamp = "writing";
    word = "dm writing";
    say = "Putting the last turn into words.";
  } else if (canAct(state)) {
    lamp = "you";
    word = "your turn";
    say = "Choose an ability below.";
  } else if (active) {
    word = active.name;
    say = `${active.name} is ${active.is_bot ? "working" : "deciding"}.`;
  } else {
    word = "standing by";
    say = "Waiting on the next turn.";
  }

  band.dataset.state = lamp;
  el("ann-word").textContent = word;        // textContent, never innerHTML
  el("ann-say").textContent = say;
  el("ann-start").hidden = !offerStart;
  /* Only a seated engineer may skip -- /skip-dm answers 403 to anyone else, so
     a spectator is shown the wait without a control they cannot use. */
  el("ann-skip").hidden = !(offerSkip && !!state.you);
  if (!offerStart && !offerSkip) el("ann-error").textContent = "";
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

/* One line of story per event. Every one of these payloads is untrusted --
   display names are player-typed and hazard names are written by the model --
   so every interpolation goes through esc(), exactly like the panels above.
   A kind with no entry here is silently dropped: `campaign_updated` is
   bookkeeping the player already sees, because the hazard names themselves
   change. */
const className = (id) =>
  CLASS_NAMES[id] || String(id ?? "").replace(/_/g, " ");

const actorName = (event) => {
  const char = lastState && lastState.characters
    ? lastState.characters[event.actor] : null;
  return char ? char.name : null;
};

const OVER = {
  win: "It ships. The programme is complete.",
  lose_budget: "The budget ran out. The programme is cancelled.",
  lose_schedule: "The schedule ran out. The programme is cancelled.",
  lose_burnout: "The whole team burned out. The programme stalls.",
};

const LABELS = {
  hazard_attack: () => "The problem bites back.",
  game_started: () => "The programme begins.",
  hazard_defeated: (e) =>
    e.name ? `${esc(e.name)} closed.` : "Problem closed.",
  phase_advanced: (e) => e.phase
    ? `${esc(String(e.phase).replace(/_/g, " "))} gate cleared.`
    : "Phase gate cleared.",
  game_over: (e) => esc(OVER[e.result] || "The programme has ended."),
  player_joined: (e) => e.name
    ? `${esc(e.name)} joins as ${esc(className(e.class_id))}.`
    : "A new engineer joins.",
  bot_added: (e) => e.name
    ? `${esc(e.name)} boots up as ${esc(className(e.class_id))}.`
    : "A bot takes a seat.",
  bot_removed: (e) => e.name ? `${esc(e.name)} powers down.` : "A bot stands up.",
  dm_skipped: () => "The table moves on without the DM.",
  hazard_rule: (e) => e.dc
    ? `The gate hardens. Difficulty is now ${esc(String(e.dc))}.`
    : "The gate hardens.",
  passed: (e) => {
    const who = actorName(e);
    return who ? `${esc(who)} passes.` : "Turn passed.";
  },
};

const logLine = (event) => {
  const build = LABELS[event.kind];
  return build ? build(event) : null;
};

function renderEvent(event) {
  if (event.seq > lastSeq) lastSeq = event.seq;

  if (event.kind === "premise") { el("premise").textContent = event.premise; return; }

  /* Chat lands in its own panel, not the story log: it is what the table said,
     not what happened in the game. */
  if (event.kind === "chat") { showChat(event); return; }

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
    if (lastState) renderAnnunciator(lastState);   // the DM is done writing
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
        <span class="nat">${event.natural}</span><span class="rest">
        ${event.stat_mod >= 0 ? "+" : ""}${event.stat_mod}
        ${event.roll_bonus ? `+${event.roll_bonus}` : ""}
        = ${event.total} ${sign} DC ${event.dc ?? "?"}
        · ${esc(event.ability_name)} · ${esc(event.outcome)}
        ${event.rerolled ? " · rerolled" : ""}</span>
      </div>`);
    const settle = () => {
      if (event.outcome === "crit") node.classList.add("flash-crit");
      if (event.outcome === "fumble") node.classList.add("flash-fumble");
    };
    /* Presentation only: the state is already committed, so the tumble never
       gates a turn. History replayed on reconnect settles instantly -- forty
       past rolls tumbling at once is noise, not drama. */
    if (lastState) renderAnnunciator(lastState);    // the DM now owes us a line
    if (isLive(event)) {
      const roll = node.querySelector(".roll");
      roll.classList.add("rolling");
      setTimeout(() => { roll.classList.remove("rolling"); settle(); }, ROLL_MS);
    } else {
      settle();
    }
    return;
  }

  const line = logLine(event);
  if (line) {
    const node = entryFor(event.seq);
    node.classList.remove("pending");
    node.insertAdjacentHTML("beforeend", `<div class="roll">${line}</div>`);
  }
}

/* --- wiring --------------------------------------------------------------- */

async function refresh() {
  const state = await api(`/api/rooms/${ROOM}/state`);
  lastState = state;
  renderParty(state); renderHazard(state); renderRail(state); renderAbilities(state);
  renderLevelChoice(state); renderMap(state); renderChatSeat(state);
  renderAnnunciator(state);
  el("dm-badge").textContent = `DM: ${state.narrator ?? "template"}`;
  if (state.room.premise) el("premise").textContent = state.room.premise;
  return state;
}

async function connect() {
  /* Whatever the room has already written is replay, however it reaches us:
     the first connection replays from zero, a reconnection replays everything
     that landed while the socket was down. Only what arrives after this mark
     is live. */
  try {
    const state = await api(`/api/rooms/${ROOM}/state`);
    replayUntil = Math.max(replayUntil, state.latest_seq || 0);
  } catch (error) { /* the stream still works; nothing will animate */ }
  const source = new EventSource(`/api/rooms/${ROOM}/stream?since=${lastSeq}`);
  source.onmessage = () => {};
  ["action", "narration", "narration_chunk", "premise", "hazard_attack", "hazard_defeated",
   "phase_advanced", "game_over", "player_joined", "game_started",
   "campaign_updated", "passed", "room_created", "interlude", "hazard_rule",
   "bot_added", "bot_removed", "dm_skipped", "chat"].forEach((kind) => {
    source.addEventListener(kind, (message) => {
      const event = JSON.parse(message.data);
      renderEvent(event);
      /* A line of talk changes no board state, so it is not worth a round trip
         for a fresh snapshot. */
      if (event.kind === "chat") return;
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

el("level-choice").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-stat]");
  if (!button || button.disabled) return;
  const stat = button.dataset.stat;
  const previous = levelStat;
  levelStat = stat;                    // optimistic; rolled back on failure
  for (const b of el("level-choice").querySelectorAll("[data-stat]")) {
    b.setAttribute("aria-pressed", b === button ? "true" : "false");
    b.disabled = true;
  }
  try {
    await api(`/api/rooms/${ROOM}/level-choice`, {
      method: "POST", body: JSON.stringify({ stat }),
    });
    await refresh();
  } catch (error) {
    levelStat = previous;
    await refresh();
    const box = el("level-choice-error");
    if (box) box.textContent = error.message;
  }
});

el("ann-start").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  el("ann-error").textContent = "";
  try { await api(`/api/rooms/${ROOM}/start`, { method: "POST" }); }
  catch (error) { el("ann-error").textContent = error.message; }
  button.disabled = false;
  await refresh();
});

el("ann-skip").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  el("ann-error").textContent = "";
  try { await api(`/api/rooms/${ROOM}/skip-dm`, { method: "POST" }); }
  catch (error) { el("ann-error").textContent = error.message; }
  button.disabled = false;
  await refresh();
});

el("pass").addEventListener("click", async () => {
  el("action-error").textContent = "";
  try { await api(`/api/rooms/${ROOM}/end-turn`, { method: "POST" }); }
  catch (error) { el("action-error").textContent = error.message; }
});

/* Enter sends, because the input is the form's only field. The keydown guard is
   not about this handler: it stops a key pressed while typing from ever
   reaching a document-level listener, so no future ability shortcut can fire
   because somebody typed a message containing its letter. */
el("chat-input").addEventListener("keydown", (event) => event.stopPropagation());

el("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = el("chat-input");
  const body = input.value.trim();
  if (!body || input.disabled) return;
  el("chat-note").textContent = "";
  input.value = "";                    /* cleared first: the line comes back
                                          over the stream, like everyone else's */
  try {
    await api(`/api/rooms/${ROOM}/chat`, {
      method: "POST", body: JSON.stringify({ body }),
    });
  } catch (error) {
    input.value = body;                /* nothing was said; give it back */
    el("chat-note").textContent = error.message;
  }
});

/* The six stat names come from the server, not a list hard-coded here. */
api("/api/classes").then((data) => {
  STATS = data.stats || [];
  PRIMARY = Object.fromEntries((data.classes || []).map((c) => [c.id, c.primary]));
  CLASS_NAMES = Object.fromEntries((data.classes || []).map((c) => [c.id, c.name]));
}).catch(() => {}).then(() => refresh()).then(pullChat).then(connect);
