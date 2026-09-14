/* Lobby and character select. No framework, no build step. */
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
      <a class="pick" href="/room/${room.room_id}/join">
        <span class="name">${room.name}</span>
        <span class="role">${room.archetype.replace(/_/g, " ")} ·
          phase ${room.phase_index + 1} of 5 · ${room.player_count} seated</span>
        <span class="stats">${room.room_id}</span>
      </a>`).join("");
  });
}

/* --- character select ------------------------------------------------------ */
const seatForm = document.getElementById("seat");
if (seatForm) {
  const roomId = window.ROOM_ID;
  let taken = new Set();

  const refresh = async () => {
    const state = await api(`/api/rooms/${roomId}/state`);
    taken = new Set(Object.values(state.characters).map((c) => c.class_id));
    document.getElementById("roster").innerHTML =
      Object.values(state.characters).map((c) => `
        <div class="member"><div class="who">
          <span>${c.name}</span><span class="stats">${c.class_id.replace(/_/g, " ")}</span>
        </div></div>`).join("") || "<p class='roll'>Nobody seated yet.</p>";
    if (state.room.premise) show("genesis", state.room.premise);
    if (state.you) location.href = `/room/${roomId}`;
    if (state.room.status === "active") location.href = `/room/${roomId}`;
    return state;
  };

  const drawClasses = ({ classes }) => {
    document.getElementById("classes").innerHTML = classes.map((cls) => `
      <button type="button" class="pick" data-class="${cls.id}"
              ${taken.has(cls.id) ? "disabled" : ""}>
        <span class="name">${cls.name}</span>
        <span class="stats">${cls.primary} / ${cls.secondary}</span>
        <span class="role">${cls.role}</span>
        <span class="role" style="margin-top:6px;display:block">${cls.blurb}</span>
      </button>`).join("");

    document.getElementById("classes").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-class]");
      if (!button || button.disabled) return;
      const name = document.getElementById("display-name").value.trim();
      if (!name) { show("join-error", "Enter your name first."); return; }
      try {
        await api(`/api/rooms/${roomId}/join`, {
          method: "POST",
          body: JSON.stringify({ display_name: name, class_id: button.dataset.class }),
        });
        location.href = `/room/${roomId}`;
      } catch (error) { show("join-error", error.message); }
    });
  };

  document.getElementById("start").addEventListener("click", async () => {
    try {
      await api(`/api/rooms/${roomId}/start`, { method: "POST" });
      location.href = `/room/${roomId}`;
    } catch (error) { show("join-error", error.message); }
  });

  refresh().then(() => api("/api/classes")).then(drawClasses);
  setInterval(refresh, 3000);
}
