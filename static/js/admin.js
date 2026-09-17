/* The admin pane. Same no-framework rules as lobby.js. */
/* Every interpolation into innerHTML below goes through esc(): room names are
   player-chosen and the server stores them verbatim. Values that land in an
   attribute or a URL are also encodeURIComponent-ed. */
const esc = (text) => String(text ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));
const attr = (text) => esc(encodeURIComponent(String(text ?? "")));

const api = async (url, options) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
};

const $ = (id) => document.getElementById(id);
const say = (id, message) => { const n = $(id); if (n) n.textContent = message || ""; };

const showPane = (admin) => {
  $("admin-login").hidden = admin;
  $("admin-pane").hidden = !admin;
  if (admin) loadRooms();
};

const size = (bytes) => {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const when = (seconds) => {
  const t = Number(seconds);
  return t ? new Date(t * 1000).toLocaleString() : "";
};

const statusClass = (status) =>
  status === "active" ? "st-active" : status === "won" ? "st-won"
    : status === "lobby" ? "st-lobby" : "st-lost";

async function loadRooms() {
  say("admin-error", "");
  const body = $("admin-rooms");
  try {
    const { rooms } = await api("/api/admin/rooms");
    if (!rooms.length) {
      body.innerHTML = "<tr><td colspan='9' class='roll'>No rooms.</td></tr>";
      return;
    }
    body.innerHTML = rooms.map((r) => `
      <tr data-room="${attr(r.room_id)}">
        <td class="num">${esc(r.room_id)}</td>
        <td>${esc(r.name)}</td>
        <td>${esc(String(r.archetype ?? "").replace(/_/g, " "))}</td>
        <td><span class="st ${esc(statusClass(r.status))}">${esc(r.status)}</span></td>
        <td class="num">${esc(r.humans)}</td>
        <td class="num">${esc(r.bots)}</td>
        <td>${esc(when(r.last_active))}</td>
        <td class="num">${esc(size(r.size_bytes))}</td>
        <td class="actions">
          <a class="btn ghost small" href="/room/${attr(r.room_id)}">Open</a>
          <button type="button" class="small danger" data-delete="${attr(r.room_id)}">Delete</button>
        </td>
      </tr>`).join("");
  } catch (error) {
    if (error.status === 403) { showPane(false); return; }
    say("admin-error", error.message);
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  say("login-error", "");
  const input = $("admin-password");
  try {
    await api("/api/admin/login", {
      method: "POST", body: JSON.stringify({ password: input.value }),
    });
    input.value = "";
    showPane(true);
  } catch (error) { say("login-error", error.message); }
});

$("admin-logout").addEventListener("click", async () => {
  try { await api("/api/admin/logout", { method: "POST" }); } catch (e) { /* already out */ }
  showPane(false);
});

$("admin-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  say("admin-create-error", "");
  const result = $("create-result");
  result.textContent = "";
  try {
    const made = await api("/api/admin/rooms", {
      method: "POST",
      body: JSON.stringify({ name: $("admin-name").value,
                             archetype: $("admin-archetype").value }),
    });
    const link = `/room/${encodeURIComponent(made.room_id)}/join`;
    result.innerHTML = `Created <span class="num">${esc(made.room_id)}</span> ·
      join link: <a href="${esc(link)}">${esc(made.join_url || link)}</a>`;
    $("admin-name").value = "";
    loadRooms();
  } catch (error) { say("admin-create-error", error.message); }
});

$("admin-rooms").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete]");
  if (!button) return;
  const code = decodeURIComponent(button.dataset.delete);
  /* The typed code is the confirmation, and the server checks it again. */
  const typed = window.prompt(
    `This moves room ${code} to rooms/_trash and closes it for everyone at the table.\n` +
    "Type the room code to confirm:");
  if (typed === null) return;
  if (typed.trim() !== code) {
    say("admin-error", "The code did not match; nothing was deleted.");
    return;
  }
  button.disabled = true;
  try {
    await api(`/api/admin/rooms/${encodeURIComponent(code)}`, {
      method: "DELETE", body: JSON.stringify({ confirm: typed.trim() }),
    });
    loadRooms();
  } catch (error) {
    button.disabled = false;
    say("admin-error", error.message);
  }
});

showPane($("admin-root").dataset.admin === "yes");
