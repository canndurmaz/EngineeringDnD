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
          phase ${room.phase_index + 1} of 5 · ${room.player_count} seated</span>
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
  let options = {};
  let look = {};

  const refresh = async () => {
    const state = await api(`/api/rooms/${roomId}/state`);
    taken = new Set(Object.values(state.characters).map((c) => c.class_id));
    document.getElementById("roster").innerHTML =
      Object.values(state.characters).map((c) => `
        <div class="member"><div class="member-row">
          ${avatarTag(c.appearance)}
          <div class="lines"><div class="who">
            <span>${esc(c.name)}</span>
            <span class="stats">${esc(c.class_id.replace(/_/g, " "))}</span>
          </div></div>
        </div></div>`).join("") || "<p class='roll'>Nobody seated yet.</p>";
    if (state.room.premise) show("genesis", state.room.premise);
    if (state.you) location.href = `/room/${roomId}`;
    if (state.room.status === "active") location.href = `/room/${roomId}`;
    return state;
  };

  const drawClasses = ({ classes }) => {
    document.getElementById("classes").innerHTML = classes.map((cls) => `
      <button type="button" class="pick" data-class="${esc(cls.id)}"
              ${taken.has(cls.id) ? "disabled" : ""}>
        <span class="name">${esc(cls.name)}</span>
        <span class="stats">${esc(cls.primary)} / ${esc(cls.secondary)}</span>
        <span class="role">${esc(cls.role)}</span>
        <span class="role" style="margin-top:6px;display:block">${esc(cls.blurb)}</span>
      </button>`).join("");

    document.getElementById("classes").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-class]");
      if (!button || button.disabled) return;
      const name = document.getElementById("display-name").value.trim();
      if (!name) { show("join-error", "Enter your name first."); return; }
      try {
        await api(`/api/rooms/${roomId}/join`, {
          method: "POST",
          body: JSON.stringify({ display_name: name, class_id: button.dataset.class,
                                 appearance: look }),
        });
        location.href = `/room/${roomId}`;
      } catch (error) { show("join-error", error.message); }
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

  document.getElementById("start").addEventListener("click", async () => {
    try {
      await api(`/api/rooms/${roomId}/start`, { method: "POST" });
      location.href = `/room/${roomId}`;
    } catch (error) { show("join-error", error.message); }
  });

  refresh().then(() => api("/api/classes")).then(drawClasses);
  setInterval(refresh, 3000);
}
