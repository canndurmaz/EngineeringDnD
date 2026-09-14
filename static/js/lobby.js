/* Lobby and character select. No framework, no build step. */
/* Every interpolation into innerHTML below goes through esc(): room and player
   names are player-chosen and the server stores them verbatim. */
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

/* "4 seated", and the split once bots are at the table. The lobby, the
   character select and the table all say it the same way. */
const seatedLabel = (seated, bots) =>
  `${seated} seated` + (bots ? ` (${bots} bot${bots === 1 ? "" : "s"})` : "");

const show = (id, message) => {
  const node = document.getElementById(id);
  if (node) node.textContent = message || "";
};

/* --- lobby ---------------------------------------------------------------- */
const createForm = document.getElementById("create");
if (createForm) {
  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    show("create-error", "");
    try {
      const { room_id } = await api("/api/rooms", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("name").value,
          archetype: document.getElementById("archetype").value,
        }),
      });
      location.href = `/room/${room_id}/join`;
    } catch (error) { show("create-error", error.message); }
  });

  document.getElementById("join-code").addEventListener("submit", (event) => {
    event.preventDefault();
    const code = document.getElementById("code").value.trim();
    if (code) location.href = `/room/${code}/join`;
  });

  api("/api/rooms").then(({ rooms }) => {
    const target = document.getElementById("rooms");
    if (!rooms.length) { target.innerHTML = "<p class='roll'>No rooms yet.</p>"; return; }
    target.innerHTML = rooms.map((room) => `
      <a class="pick" href="/room/${encodeURIComponent(room.room_id)}/join">
        <span class="name">${esc(room.name)}</span>
        <span class="role">${esc(room.archetype.replace(/_/g, " "))} ·
          phase ${room.phase_index + 1} of 5 ·
          ${esc(seatedLabel(room.player_count, room.bot_count))}</span>
        <span class="stats">${esc(room.room_id)}</span>
      </a>`).join("");
  });
}

/* --- avatars --------------------------------------------------------------- */
/* The order the pickers appear in. Labels and ids both come from the server,
   so a curated list can change without touching this file. */
const LOOK_KINDS = [["hair", "Hair"], ["face", "Face"], ["eyes", "Eyes"],
                    ["outfit", "Outfit"], ["skin", "Skin"]];

/* Builds /api/avatar.svg?... . This lands in an HTML *attribute*, so each part
   is URL-encoded here and the whole thing goes through esc() at the call site:
   encodeURIComponent stops a value breaking out of the query string, esc()
   stops it breaking out of the attribute. */
const avatarUrl = (look) => "/api/avatar.svg?" + LOOK_KINDS
  .map(([kind]) => `${encodeURIComponent(kind)}=${encodeURIComponent((look || {})[kind] ?? "")}`)
  .join("&");

const avatarTag = (look, cls = "avatar") =>
  `<img class="${esc(cls)}" alt="" src="${esc(avatarUrl(look))}" loading="lazy">`;

/* --- character select ------------------------------------------------------ */
const seatForm = document.getElementById("seat");
if (seatForm) {
  const roomId = window.ROOM_ID;
  let taken = new Set();
  let classList = [];            // /api/classes, kept so the cards can redraw
  let seated = 0;
  let seatsLeft = 1;
  /* Seat first, fill afterwards: /bots refuses anyone without a seat, so the
     control must not exist before this visitor has one. */
  let joined = false;
  let options = {};
  let look = {};

  /* True from the moment a join succeeds until the player clicks through the
     roll reveal. It holds the poll's redirect back so the reveal is readable. */
  let revealing = false;

  const startButton = document.getElementById("start");
  const seatFirst = document.getElementById("seat-first");
  const toTable = document.getElementById("to-table");

  /* The server already refuses to start an empty room (and refuses anyone who
     is not seated); this only stops the button from lying about it. */
  const gateStart = (seated) => {
    startButton.disabled = seated === 0;
    startButton.textContent = seated === 0
      ? "Waiting for engineers\u2026"
      : `Start the programme (${seated} seated)`;
  };

  const refresh = async () => {
    const state = await api(`/api/rooms/${roomId}/state`);
    taken = new Set(Object.values(state.characters).map((c) => c.class_id));
    joined = !!state.you;
    const size = state.party_size || {};
    seated = size.seated ?? Object.keys(state.characters).length;
    seatsLeft = (size.max ?? seated + 1) - seated;
    gateStart(seated);
    seatFirst.textContent = joined
      ? "You\u2019re seated. Add bots to the empty seats, then start."
      : "Pick your discipline first \u2014 you can add bots to the empty seats afterwards.";
    if (joined) seatForm.hidden = true;          // the picker has done its job
    toTable.hidden = !joined;
    show("party-count", seatedLabel(seated, size.bots || 0));
    document.getElementById("roster").innerHTML =
      Object.values(state.characters).map((c) => `
        <div class="member"><div class="member-row">
          ${avatarTag(c.appearance)}
          <div class="lines"><div class="who">
            <span>${esc(c.name)}${c.is_bot ? '<span class="chip-bot">BOT</span>' : ""}</span>
            <span class="stats">${esc(c.class_id.replace(/_/g, " "))}</span>
          </div></div>
          ${c.is_bot ? `<button type="button" class="bot-drop"
            data-drop="${esc(encodeURIComponent(c.player_id))}"
            aria-label="Remove ${esc(c.name)}">&times;</button>` : ""}
        </div></div>`).join("") || "<p class='roll'>Nobody seated yet.</p>";
    drawClasses();
    if (state.room.premise) show("genesis", state.room.premise);
    if (revealing) return state;          // let the player read their dice first
    /* A seated player stays here while the room is still filling up -- this is
       where bots are added and the programme is started. Once it is running,
       the table is the only place to be. */
    if (state.room.status === "active") location.href = `/room/${roomId}`;
    return state;
  };

  /* Redrawn on every poll, so a class someone else just took stops being
     clickable and its "Add bot" control goes with it. The bot button cannot
     live inside the class card: a <button> inside a <button> is invalid HTML
     and the inner one never receives a click. */
  const drawClasses = () => {
    const target = document.getElementById("classes");
    if (!classList.length || !target) return;
    target.innerHTML = classList.map((cls) => {
      const isTaken = taken.has(cls.id);
      const cta = isTaken ? "Taken" : joined ? "Open seat" : "Take this seat";
      return `
      <div class="pick-wrap">
        <button type="button" class="pick" data-class="${esc(cls.id)}"
                ${isTaken || joined ? "disabled" : ""}>
          <span class="name">${esc(cls.name)}</span>
          <span class="stats">${esc(cls.primary)} / ${esc(cls.secondary)}</span>
          <span class="role">${esc(cls.role)}</span>
          <span class="role" style="margin-top:6px;display:block">${esc(cls.blurb)}</span>
          <span class="cta">${esc(cta)}</span>
        </button>
        ${joined && !isTaken ? `<button type="button" class="bot-add"
          data-bot="${esc(encodeURIComponent(cls.id))}" ${seatsLeft > 0 ? "" : "disabled"}
          >Add bot</button>` : ""}
      </div>`;
    }).join("");
  };

  const loadClasses = ({ classes }) => { classList = classes; drawClasses(); };

  document.getElementById("classes").addEventListener("click", async (event) => {
    const bot = event.target.closest("[data-bot]");
    if (bot) {
      if (bot.disabled) return;
      bot.disabled = true;
      show("join-error", "");
      let failed = "";
      try {
        await api(`/api/rooms/${roomId}/bots`, {
          method: "POST",
          body: JSON.stringify({ class_id: decodeURIComponent(bot.dataset.bot) }),
        });
      } catch (error) {
        /* Say what the server said. Swallowing it is how "you are not seated
           in this room" became a mystery in the first place. */
        failed = error.message || "The bot could not take that seat.";
      }
      await refresh();
      if (failed) show("join-error", failed);
      return;
    }
    const button = event.target.closest("[data-class]");
    if (!button || button.disabled) return;
    const name = document.getElementById("display-name").value.trim();
    if (!name) { show("join-error", "Enter your name first."); return; }
    try {
      const joined = await api(`/api/rooms/${roomId}/join`, {
        method: "POST",
        body: JSON.stringify({ display_name: name, class_id: button.dataset.class,
                               appearance: look }),
      });
      revealTheRoll(joined.roll, joined.character);
    } catch (error) { show("join-error", error.message); }
  });

  document.getElementById("roster").addEventListener("click", async (event) => {
    const drop = event.target.closest("[data-drop]");
    if (!drop) return;
    drop.disabled = true;
    show("join-error", "");
    try {
      await api(`/api/rooms/${roomId}/bots/${drop.dataset.drop}`,
                { method: "DELETE" });
    } catch (error) { show("join-error", error.message); }
    await refresh();
  });

  /* --- the one-time 4d6 reveal --------------------------------------------- */
  /* No rerolling: this shows what was rolled, it does not offer a second go.
     Everything below is server-generated, but stat names come from the class
     data, so they go through esc() like anything else. */
  const dieTag = (value, isDropped) =>
    `<span class="die${isDropped ? " dropped" : ""}">${esc(value)}</span>`;

  const rollRow = (roll, detail) => {
    const seat = roll.stat === detail.primary ? "primary"
               : roll.stat === detail.secondary ? "secondary" : "";
    let dropped = false;                  // strike exactly one copy of the low die
    const dice = (roll.dice || []).map((value) => {
      const strike = !dropped && value === roll.dropped;
      if (strike) dropped = true;
      return dieTag(value, strike);
    }).join("");
    /* A raw total outside 8-16 is pulled back into the band. Showing the
       adjustment is the whole point: a 7 sitting next to a stat of 8 reads as
       the game lying unless the arrow is there to explain it. */
    const clamped = roll.value !== undefined && roll.value !== roll.total;
    return `
      <div class="roll-row${seat ? " seated" : ""}">
        <span class="dice">${dice}</span>
        <span class="total">= ${esc(roll.total)}</span>
        ${clamped ? `<span class="clamped" title="clamped to the 8-16 range"
          >&rarr; ${esc(roll.value)}</span>` : ""}
        <span class="lands">${esc(roll.stat)}</span>
        ${seat ? `<span class="seat">${esc(seat)}</span>` : ""}
      </div>`;
  };

  const revealTheRoll = (detail, character) => {
    revealing = true;
    seatForm.hidden = true;
    const target = document.getElementById("roll-reveal");
    if (!detail || !detail.rolls) { location.href = `/room/${roomId}`; return; }
    target.innerHTML = `
      <div class="roll-reveal">
        <h3>Your roll &mdash; ${esc(character ? character.name : "")}</h3>
        <p class="lede">Four d6, lowest dropped, six times. The two best totals
          seat in your class&rsquo;s primary and secondary stats. This roll stands.</p>
        <div class="roll-rows">${detail.rolls.map((r) => rollRow(r, detail)).join("")}</div>
        <button type="button" id="take-seat" style="margin-top:14px">Take your seat &rarr;</button>
      </div>`;
    target.querySelector("#take-seat").addEventListener("click", () => {
      revealing = false;
      target.innerHTML = "";
      refresh();          // seated now: bots, the party count, and the start
    });
  };

  /* --- appearance pickers -------------------------------------------------- */
  const preview = document.getElementById("avatar-preview");
  const pickers = document.getElementById("pickers");

  const labelFor = (kind) =>
    (options[kind].find((entry) => entry.id === look[kind]) || options[kind][0]).label;

  const paint = () => {
    preview.src = avatarUrl(look);
    LOOK_KINDS.forEach(([kind]) => {
      const node = pickers.querySelector(`[data-value="${kind}"]`);
      if (node) node.textContent = labelFor(kind);   // textContent, never innerHTML
    });
  };

  /* Wraps at both ends so every option is a few clicks away in either direction. */
  const step = (kind, delta) => {
    const list = options[kind];
    const at = Math.max(0, list.findIndex((entry) => entry.id === look[kind]));
    look[kind] = list[(at + delta + list.length) % list.length].id;
    paint();
  };

  const randomise = () => {
    LOOK_KINDS.forEach(([kind]) => {
      const list = options[kind];
      look[kind] = list[Math.floor(Math.random() * list.length)].id;
    });
    paint();
  };

  const drawPickers = (served) => {
    options = served;
    pickers.innerHTML = LOOK_KINDS.map(([kind, title]) => `
      <div class="picker">
        <button type="button" class="arrow" data-kind="${esc(kind)}" data-delta="-1"
                aria-label="Previous ${esc(title.toLowerCase())}">&lsaquo;</button>
        <span class="swatch">
          <span class="kind">${esc(title)}</span>
          <span class="value" data-value="${esc(kind)}"></span>
        </span>
        <button type="button" class="arrow" data-kind="${esc(kind)}" data-delta="1"
                aria-label="Next ${esc(title.toLowerCase())}">&rsaquo;</button>
      </div>`).join("");

    pickers.addEventListener("click", (event) => {
      const arrow = event.target.closest("[data-kind]");
      if (arrow) step(arrow.dataset.kind, Number(arrow.dataset.delta));
    });
    document.getElementById("randomise").addEventListener("click", randomise);
    randomise();          // nobody should have to build an avatar from scratch
  };

  api("/api/appearance-options").then(drawPickers);

  startButton.addEventListener("click", async () => {
    try {
      await api(`/api/rooms/${roomId}/start`, { method: "POST" });
      location.href = `/room/${roomId}`;
    } catch (error) { show("join-error", error.message); }
  });

  refresh().then(() => api("/api/classes")).then(loadClasses);
  setInterval(refresh, 3000);
}
