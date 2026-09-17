/* The office: a shared top-down floor drawn with Phaser 3 (vendored, no CDN).

   The server owns *zones*, not pixels. This file animates walking locally and
   tells the server only when the player's avatar crosses into a different zone
   (POST /office/move). Everyone else's avatar walks to wherever the server says
   they are. Place actions (E, or the Use button) go to POST /office/act and
   cost the turn exactly like an ability.

   Every string from the server reaches the page through Phaser text objects or
   DOM textContent -- never parsed as markup. Tiles and furniture are drawn at runtime,
   so there are no image files. */
(function () {
  "use strict";

  const ROOM = window.ROOM_ID;
  const $ = (id) => document.getElementById(id);
  const REDUCED = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  /* --- the map (world units; the canvas scales to its container) ---------- */

  const W = 960, H = 540;
  const R = 12;                              // avatar radius
  const SPEED = 190;                         // px per second
  const WALL_Y = 196, WALL_T = 12;           // the wall between rooms and floor
  const SPLIT_X = 470, SPLIT_T = 20;         // the wall between lab and break room
  const DOOR_HALF = 42;
  const DOOR_X = { lab: 230, break_room: 720 };
  const ROOMS = {
    lab: { x: 0, y: 0, w: SPLIT_X, h: WALL_Y },
    break_room: { x: SPLIT_X + SPLIT_T, y: 0, w: W - SPLIT_X - SPLIT_T, h: WALL_Y },
  };
  const WHITEBOARD = { x: 630, y: 430, w: 300, h: 100 };
  const DESK_TOP = 236, DESK_H = 150, DESK_W = 128;
  const deskCenter = (i) => 86 + i * 156;
  const deskRect = (i) => ({ x: deskCenter(i) - DESK_W / 2, y: DESK_TOP,
                             w: DESK_W, h: DESK_H });
  const WALLS = [
    { x: 0, y: WALL_Y, w: DOOR_X.lab - DOOR_HALF, h: WALL_T },
    { x: DOOR_X.lab + DOOR_HALF, y: WALL_Y,
      w: DOOR_X.break_room - DOOR_X.lab - 2 * DOOR_HALF, h: WALL_T },
    { x: DOOR_X.break_room + DOOR_HALF, y: WALL_Y,
      w: W - DOOR_X.break_room - DOOR_HALF, h: WALL_T },
    { x: SPLIT_X, y: 0, w: SPLIT_T, h: WALL_Y },
  ];

  const ZONE_NAMES = { floor: "the floor", lab: "the lab",
                       break_room: "the break room", whiteboard: "the whiteboard" };

  const DESK_COLOURS = { oak: 0x9c7a4f, walnut: 0x5e4330, white: 0xd4d9df,
                         graphite: 0x3a4350, teal: 0x2f8f8a, rust: 0xa4533a };

  const inRect = (p, r) => p.x >= r.x && p.x <= r.x + r.w &&
                           p.y >= r.y && p.y <= r.y + r.h;

  /* --- state shared between the scene and the panel ------------------------ */

  let state = null;            // the latest /state snapshot
  let game = null;             // the Phaser.Game, built on first open
  let scene = null;            // the running OfficeScene
  let localZone = null;        // where *this* browser's avatar is standing
  let sentZone = null;         // the last zone the server acknowledged
  let moveChain = Promise.resolve();
  const zones = {};            // player_id -> zone, from the server / stream

  const myId = () => (state && state.you ? state.you.player_id : null);

  function deskIndex(playerId) {
    const desks = (state && state.office && state.office.desks) || [];
    const found = desks.find((d) => d.player_id === playerId);
    return found ? found.index : -1;
  }

  function zoneAt(p) {
    if (inRect(p, ROOMS.lab)) return "lab";
    if (inRect(p, ROOMS.break_room)) return "break_room";
    const desks = (state && state.office && state.office.desks) || [];
    for (const d of desks) {
      if (inRect(p, deskRect(d.index))) return d.zone;
    }
    if (inRect(p, WHITEBOARD)) return "whiteboard";
    return "floor";
  }

  /* Where someone standing in `zone` is drawn. `slot` spreads a crowd. */
  function anchor(zone, slot) {
    let x = 480, y = 405;
    if (zone === "lab") { x = 150; y = 120; }
    else if (zone === "break_room") { x = 640; y = 120; }
    else if (zone === "whiteboard") { x = 780; y = 492; }
    else if (zone && zone.startsWith("desk:")) {
      const i = deskIndex(zone.slice(5));
      if (i >= 0) { x = deskCenter(i); y = 330; }
    }
    const s = slot || 0;
    return { x: x + ((s % 3) - 1) * 26, y: y + Math.floor(s / 3) * 22 };
  }

  function regionOf(p) {
    if (p.y < WALL_Y + WALL_T / 2) return p.x < SPLIT_X + SPLIT_T / 2 ? "lab" : "break_room";
    return "floor";
  }

  /* Straight lines, except through a door when the walk changes room. */
  function route(from, to) {
    const a = regionOf(from), b = regionOf(to);
    const points = [];
    if (a !== b) {
      if (a !== "floor") {
        points.push({ x: DOOR_X[a], y: WALL_Y - 22 }, { x: DOOR_X[a], y: WALL_Y + WALL_T + 22 });
      }
      if (b !== "floor") {
        points.push({ x: DOOR_X[b], y: WALL_Y + WALL_T + 22 }, { x: DOOR_X[b], y: WALL_Y - 22 });
      }
    }
    points.push(to);
    return points;
  }

  function blocked(x, y) {
    if (x < R || y < R || x > W - R || y > H - R) return true;
    return WALLS.some((w) => x > w.x - R && x < w.x + w.w + R &&
                             y > w.y - R && y < w.y + w.h + R);
  }

  /* --- talking to the server ----------------------------------------------- */

  async function post(path, body) {
    const response = await fetch(`/api/rooms/${ROOM}/office/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `request failed (${response.status})`);
    return data;
  }

  /* Only the newest zone matters; older ones are skipped, not replayed. */
  function sendZone() {
    moveChain = moveChain.then(async () => {
      const want = localZone;
      if (!myId() || !want || want === sentZone) return;
      try {
        await post("move", { zone: want });
        sentZone = want;
        zones[myId()] = want;
      } catch (error) {
        showError(error.message);
      }
    });
    return moveChain;
  }

  function showError(text) {
    const box = $("office-error");
    if (box) box.textContent = text || "";
  }

  /* --- the panel beside the canvas ----------------------------------------- */

  const running = () => state && state.room.status === "active";
  const gated = () => !!(state && state.dm_wait && state.dm_wait.waiting);
  const myTurn = () => running() && !!state.you &&
    state.turn.active_player_id === state.you.player_id;

  function actionFor(zone) {
    const me = myId();
    if (!zone || !me) return null;
    if (zone === "lab") return "bench_test";
    if (zone === "break_room") return "coffee_break";
    if (zone === "whiteboard") return "whiteboard";
    if (zone.startsWith("desk:")) return zone.slice(5) === me ? "customize" : "pair_up";
    return null;
  }

  function zoneLabel(zone) {
    if (zone && zone.startsWith("desk:")) {
      const owner = state && state.characters[zone.slice(5)];
      if (!owner) return "a desk";
      return zone.slice(5) === myId() ? "your desk" : `${owner.name}'s desk`;
    }
    return ZONE_NAMES[zone] || "the floor";
  }

  /* Why a turn-costing place action cannot be used right now, or "". */
  function whyNot(action) {
    if (!running()) return state && state.room.status === "lobby"
      ? "the programme has not started" : "the programme has ended";
    if (gated()) return "the DM is writing";
    if (!myTurn()) return "not your turn";
    const you = state.you;
    if (you.stamina <= 0) return "you are burned out";
    if (action === "coffee_break") {
      const limit = (state.office && state.office.coffee_limit) || 2;
      if ((you.coffee_used || 0) >= limit) return "no coffee breaks left this phase";
    }
    if (action === "pair_up") {
      const partner = state.characters[localZone.slice(5)];
      if (!partner || partner.stamina <= 0) return "they are burned out";
      const used = (you.used || {}).pair_up;
      if (used === `round:${state.turn.round}`) return "already paired this round";
    }
    return "";
  }

  function renderPanel() {
    const prompt = $("office-prompt"), use = $("office-use");
    const where = $("office-where"), deskPanel = $("desk-panel");
    if (!prompt || !use) return;
    if (!state) return;
    if (!state.you) {
      prompt.textContent = "You are watching this office. Take a seat on the join page to walk in.";
      use.hidden = true;
      if (deskPanel) deskPanel.hidden = true;
      if (where) where.textContent = "";
      return;
    }
    const zone = localZone || "floor";
    if (where) where.textContent = `· ${zoneLabel(zone)}`;
    const action = actionFor(zone);
    let text = "Walk to the lab, the break room, a colleague's desk or the whiteboard.";
    let label = "";
    let why = "";
    if (action === "bench_test") {
      const known = state.hazard && state.hazard.weakness;
      text = `Press E to run a bench test (${known
        ? "+2 to your next roll" : "reveals the problem's weakness"}). Uses your turn.`;
      label = "Run a bench test";
      why = whyNot(action);
    } else if (action === "coffee_break") {
      const limit = (state.office && state.office.coffee_limit) || 2;
      const left = Math.max(0, limit - (state.you.coffee_used || 0));
      text = `Press E to take a coffee break (restores stamina; ${left} left this phase). Uses your turn.`;
      label = "Take a coffee break";
      why = whyNot(action);
    } else if (action === "pair_up") {
      const partner = state.characters[zone.slice(5)];
      const name = partner ? partner.name : "them";
      text = `Press E to pair up with ${name} (+1 to both your next rolls). Uses your turn.`;
      label = `Pair up with ${name}`;
      why = whyNot(action);
    } else if (action === "customize") {
      text = "Press E to customize your desk. Free, any time.";
      label = "Customize your desk";
    } else if (action === "whiteboard") {
      text = "Press E to write on the whiteboard (opens table talk).";
      label = "Open table talk";
    }
    prompt.textContent = why ? `${text} — ${why}.` : text;
    use.hidden = !label;
    use.textContent = label || "Use";
    use.disabled = !!why;
    if (deskPanel) {
      const atDesk = action === "customize";
      if (atDesk && deskPanel.hidden) buildDeskForm();
      deskPanel.hidden = !atDesk;
    }
  }

  function buildDeskForm() {
    const form = $("desk-form");
    if (!form || !state || !state.you || !state.office) return;
    const options = state.office.desk_options || {};
    const current = state.you.desk || {};
    form.replaceChildren();
    for (const kind of Object.keys(options)) {
      const id = `desk-${kind}`;
      const label = document.createElement("label");
      label.htmlFor = id;
      label.textContent = kind.replace(/_/g, " ");
      const select = document.createElement("select");
      select.id = id;
      select.name = kind;
      for (const value of options[kind]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = String(value).replace(/_/g, " ");
        if (current[kind] === value) option.selected = true;
        select.append(option);
      }
      form.append(label, select);
    }
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "Save desk";
    form.append(save);
  }

  async function saveDesk(event) {
    event.preventDefault();
    const form = $("desk-form");
    const desk = {};
    for (const select of form.querySelectorAll("select")) desk[select.name] = select.value;
    const note = $("desk-note");
    try {
      await post("desk", { desk });
      if (note) note.textContent = "Saved.";
    } catch (error) {
      if (note) note.textContent = error.message;
    }
  }

  async function useHere() {
    showError("");
    const action = actionFor(localZone);
    if (!action) return;
    if (action === "whiteboard") {
      const input = $("chat-input");
      if (input && !input.disabled) input.focus();
      return;
    }
    if (action === "customize") {
      const panel = $("desk-panel");
      if (panel) {
        if (panel.hidden) { buildDeskForm(); panel.hidden = false; }
        const first = panel.querySelector("select");
        if (first) first.focus();
      }
      return;
    }
    if (whyNot(action)) { renderPanel(); return; }
    const use = $("office-use");
    if (use) use.disabled = true;
    try {
      await sendZone();                       // the server must agree where we are
      const body = { action };
      if (action === "pair_up") body.target_id = localZone.slice(5);
      await post("act", body);
    } catch (error) {
      showError(error.message);
      if (use) use.disabled = false;
    }
  }

  /* --- the scene ------------------------------------------------------------ */

  function cssColour(name, fallback) {
    try {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      if (/^#[0-9a-f]{6}$/i.test(raw)) return parseInt(raw.slice(1), 16);
    } catch (error) { /* fall through */ }
    return fallback;
  }

  const hex = (n) => `#${Number(n).toString(16).padStart(6, "0")}`;

  function hashColour(id, palette) {
    let h = 0;
    for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return palette[h % palette.length];
  }

  function makeSceneClass() {
    const Phaser = window.Phaser;
    return class OfficeScene extends Phaser.Scene {
      constructor() {
        super("office");
        this.avatars = new Map();
        this.keys = new Set();
        this.deskKey = "";
        this.deskLayer = null;
      }

      create() {
        this.C = {
          ground: cssColour("--ground", 0x0f141a), panel: cssColour("--panel", 0x161d26),
          panel2: cssColour("--panel-2", 0x1d2632), line: cssColour("--line", 0x2b3644),
          ink: cssColour("--ink", 0xdfe6ee), dim: cssColour("--ink-dim", 0x8b98a8),
          cyan: cssColour("--cyan", 0x4fd6e8), amber: cssColour("--amber", 0xf2b34b),
          red: cssColour("--red", 0xe5624d), green: cssColour("--green", 0x62c98a),
        };
        this.drawFloor();
        this.deskLayer = this.add.container(0, 0);
        this.input.on("pointerdown", (pointer) => {
          const box = $("office-canvas");
          if (box) box.focus({ preventScroll: true });
          const me = this.avatars.get(myId());
          if (!me) return;
          const to = { x: Phaser.Math.Clamp(pointer.worldX, R, W - R),
                       y: Phaser.Math.Clamp(pointer.worldY, R, H - R) };
          if (blocked(to.x, to.y)) to.y += to.y < WALL_Y + WALL_T / 2 ? -(R + WALL_T) : R + WALL_T;
          me.path = route(me, to);
        });
        scene = this;
        if (state) this.sync(true);
      }

      /* Procedural tiles: a generated texture, tiled; no image files. */
      drawFloor() {
        const C = this.C;
        const tile = this.make.graphics({ x: 0, y: 0, add: false });
        tile.fillStyle(C.panel, 1).fillRect(0, 0, 32, 32);
        tile.lineStyle(1, C.line, 0.55).strokeRect(0, 0, 32, 32);
        tile.generateTexture("office-tile", 32, 32);
        tile.destroy();
        this.add.tileSprite(0, 0, W, H, "office-tile").setOrigin(0, 0);

        const g = this.add.graphics();
        // room floors
        g.fillStyle(C.cyan, 0.05).fillRect(ROOMS.lab.x, ROOMS.lab.y, ROOMS.lab.w, ROOMS.lab.h);
        g.fillStyle(C.amber, 0.05).fillRect(ROOMS.break_room.x, ROOMS.break_room.y,
                                            ROOMS.break_room.w, ROOMS.break_room.h);
        // walls
        g.fillStyle(C.line, 1);
        for (const w of WALLS) g.fillRect(w.x, w.y, w.w, w.h);
        g.lineStyle(2, C.dim, 0.6);
        for (const x of Object.values(DOOR_X)) {
          g.lineBetween(x - DOOR_HALF, WALL_Y + WALL_T / 2, x - DOOR_HALF + 10, WALL_Y + WALL_T / 2);
          g.lineBetween(x + DOOR_HALF - 10, WALL_Y + WALL_T / 2, x + DOOR_HALF, WALL_Y + WALL_T / 2);
        }
        // lab: benches with instruments
        for (const [bx, by] of [[40, 30], [250, 30], [40, 140]]) {
          g.fillStyle(C.panel2, 1).fillRect(bx, by, 170, 28);
          g.lineStyle(1, C.cyan, 0.7).strokeRect(bx, by, 170, 28);
          g.fillStyle(C.ground, 1).fillRect(bx + 12, by + 5, 34, 18);
          g.lineStyle(1, C.green, 0.9).beginPath();
          g.moveTo(bx + 14, by + 14);
          for (let k = 0; k < 6; k += 1) g.lineTo(bx + 16 + k * 5, by + (k % 2 ? 8 : 20));
          g.strokePath();
          g.fillStyle(C.amber, 0.9).fillCircle(bx + 70, by + 14, 5);
          g.fillStyle(C.dim, 0.9).fillRect(bx + 100, by + 8, 50, 12);
        }
        // break room: counter, coffee machine, table, couch
        const bx = ROOMS.break_room.x;
        g.fillStyle(C.panel2, 1).fillRect(bx + 20, 20, 200, 26);
        g.fillStyle(C.ground, 1).fillRect(bx + 36, 10, 30, 34);
        g.fillStyle(C.red, 0.9).fillCircle(bx + 51, 22, 4);
        g.fillStyle(C.amber, 0.25).fillCircle(bx + 330, 110, 34);
        g.lineStyle(1, C.amber, 0.7).strokeCircle(bx + 330, 110, 34);
        g.fillStyle(C.panel2, 1).fillRoundedRect(bx + 280, 160, 150, 26, 8);
        // whiteboard
        g.fillStyle(C.ink, 0.9).fillRect(WHITEBOARD.x + 20, WHITEBOARD.y + 4, WHITEBOARD.w - 40, 26);
        g.lineStyle(2, C.cyan, 0.9).beginPath();
        g.moveTo(WHITEBOARD.x + 34, WHITEBOARD.y + 22);
        g.lineTo(WHITEBOARD.x + 90, WHITEBOARD.y + 12).lineTo(WHITEBOARD.x + 150, WHITEBOARD.y + 20);
        g.strokePath();
        g.lineStyle(1, C.line, 0.9).strokeRect(WHITEBOARD.x, WHITEBOARD.y, WHITEBOARD.w, WHITEBOARD.h);

        const label = (x, y, text) => this.add.text(x, y, text, {
          fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: "11px",
          color: hex(C.dim) }).setAlpha(0.9);
        label(10, 176, "LAB · bench test");
        label(ROOMS.break_room.x + 10, 176, "BREAK ROOM · coffee");
        label(WHITEBOARD.x + 8, WHITEBOARD.y + WHITEBOARD.h - 18, "WHITEBOARD · table talk");
      }

      drawDesks() {
        const C = this.C;
        const desks = (state && state.office && state.office.desks) || [];
        const key = JSON.stringify(desks.map((d) => [d.index, d.player_id, d.desk,
          (state.characters[d.player_id] || {}).name]));
        if (key === this.deskKey) return;
        this.deskKey = key;
        this.deskLayer.removeAll(true);
        for (const d of desks) {
          const cx = deskCenter(d.index);
          const look = d.desk || {};
          const g = this.add.graphics();
          const r = deskRect(d.index);
          g.lineStyle(1, C.line, 0.8).strokeRect(r.x, r.y, r.w, r.h);
          // poster on the partition behind
          if (look.poster && look.poster !== "none") {
            g.fillStyle(C.panel2, 1).fillRect(cx - 22, r.y + 4, 44, 22);
            g.lineStyle(1, C.dim, 1).strokeRect(cx - 22, r.y + 4, 44, 22);
            if (look.poster === "gantt") {
              g.fillStyle(C.cyan, 1).fillRect(cx - 18, r.y + 8, 18, 3)
                .fillRect(cx - 8, r.y + 14, 20, 3).fillRect(cx + 4, r.y + 20, 14, 3);
            } else if (look.poster === "schematic") {
              g.lineStyle(1, C.green, 1).strokeRect(cx - 14, r.y + 9, 12, 12)
                .lineBetween(cx - 2, r.y + 15, cx + 14, r.y + 15);
            } else {
              g.fillStyle(C.amber, 1).fillTriangle(cx - 10, r.y + 22, cx, r.y + 8, cx + 10, r.y + 22);
            }
          }
          // desk top
          g.fillStyle(DESK_COLOURS[look.desk_color] ?? DESK_COLOURS.oak, 1)
            .fillRoundedRect(cx - 52, r.y + 32, 104, 42, 4);
          // monitor
          g.fillStyle(C.ground, 1);
          if (look.monitor === "dual") {
            g.fillRect(cx - 40, r.y + 36, 36, 20).fillRect(cx + 2, r.y + 36, 36, 20);
            g.fillStyle(C.cyan, 0.5).fillRect(cx - 37, r.y + 39, 30, 14).fillRect(cx + 5, r.y + 39, 30, 14);
          } else if (look.monitor === "laptop") {
            g.fillRect(cx - 16, r.y + 44, 32, 18);
            g.fillStyle(C.cyan, 0.5).fillRect(cx - 13, r.y + 46, 26, 11);
          } else {
            g.fillRect(cx - 22, r.y + 36, 44, 24);
            g.fillStyle(C.cyan, 0.5).fillRect(cx - 19, r.y + 39, 38, 18);
          }
          // plant
          if (look.plant === "succulent") {
            g.fillStyle(C.dim, 1).fillRect(cx + 40, r.y + 56, 10, 10);
            g.fillStyle(C.green, 1).fillCircle(cx + 45, r.y + 53, 6);
          } else if (look.plant === "fern") {
            g.fillStyle(C.dim, 1).fillRect(cx - 52, r.y + 20, 12, 12);
            g.fillStyle(C.green, 1).fillTriangle(cx - 58, r.y + 22, cx - 46, r.y + 2, cx - 34, r.y + 22);
          }
          // mug
          if (look.mug === "coffee" || look.mug === "tea") {
            g.fillStyle(C.ink, 1).fillCircle(cx - 42, r.y + 64, 5);
            g.fillStyle(look.mug === "coffee" ? DESK_COLOURS.walnut : C.amber, 1)
              .fillCircle(cx - 42, r.y + 64, 3);
          }
          // chair
          g.fillStyle(C.panel2, 1).fillCircle(cx, r.y + 98, 12);
          const owner = state.characters[d.player_id] || {};
          const name = this.add.text(cx, r.y + r.h - 12, String(owner.name || ""), {
            fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: "10px",
            color: hex(C.dim) }).setOrigin(0.5, 0.5);
          this.deskLayer.add([g, name]);
        }
      }

      avatarFor(pid, char) {
        let a = this.avatars.get(pid);
        if (a) return a;
        const C = this.C;
        const body = this.add.circle(0, 0, R, hashColour(pid, [C.cyan, C.amber, C.green, C.red, C.ink]));
        const ring = this.add.circle(0, 0, R + 3).setStrokeStyle(2, C.amber, 1).setVisible(false);
        const name = this.add.text(0, R + 9, "", {
          fontFamily: "system-ui, sans-serif", fontSize: "11px", color: hex(C.ink),
          backgroundColor: `${hex(C.ground)}cc`, padding: { x: 3, y: 1 } }).setOrigin(0.5, 0.5);
        const container = this.add.container(0, 0, [ring, body, name]).setDepth(10);
        a = { pid, container, body, ring, name, x: -1, y: -1, path: [], placed: false };
        this.avatars.set(pid, a);
        return a;
      }

      place(a, p) {
        a.x = p.x; a.y = p.y; a.path = []; a.placed = true;
        a.container.setPosition(p.x, p.y);
      }

      /* Bring the scene in line with `state`. `first` snaps rather than walks. */
      sync(first) {
        if (!state) return;
        this.drawDesks();
        const me = myId();
        const seen = new Set();
        const crowd = {};
        const ids = Object.keys(state.characters).sort();
        for (const pid of ids) {
          const char = state.characters[pid];
          seen.add(pid);
          const a = this.avatarFor(pid, char);
          a.name.setText(`${char.name}${char.is_bot ? " [BOT]" : ""}`);
          a.body.setStrokeStyle(pid === me ? 3 : 1, pid === me ? this.C.ink : this.C.line);
          a.ring.setVisible(state.turn.active_player_id === pid);
          a.container.setAlpha(char.stamina > 0 ? 1 : 0.45);
          if (pid === me) {
            if (!a.placed) {
              const zone = char.office_zone || "floor";
              this.place(a, anchor(zone, 0));
              localZone = sentZone = zones[pid] = zone;
            }
            continue;
          }
          const zone = zones[pid] || char.office_zone || "floor";
          zones[pid] = zone;
          const slot = crowd[zone] = (crowd[zone] ?? -1) + 1;
          const target = anchor(zone, slot);
          if (!a.placed || first || REDUCED) this.place(a, target);
          else if (a.goal !== `${zone}#${slot}`) a.path = route(a, target);
          a.goal = `${zone}#${slot}`;
        }
        for (const [pid, a] of this.avatars) {
          if (!seen.has(pid)) { a.container.destroy(); this.avatars.delete(pid); }
        }
        renderPanel();
      }

      update(time, delta) {
        const dt = Math.min(delta, 50) / 1000;
        const me = myId();
        for (const [pid, a] of this.avatars) {
          let dx = 0, dy = 0;
          if (pid === me && this.keys.size) {
            a.path = [];
            if (this.keys.has("left")) dx -= 1;
            if (this.keys.has("right")) dx += 1;
            if (this.keys.has("up")) dy -= 1;
            if (this.keys.has("down")) dy += 1;
            const len = Math.hypot(dx, dy) || 1;
            dx = (dx / len) * SPEED * dt; dy = (dy / len) * SPEED * dt;
          } else if (a.path.length) {
            const next = a.path[0];
            const ex = next.x - a.x, ey = next.y - a.y;
            const dist = Math.hypot(ex, ey);
            const step = SPEED * dt;
            if (dist <= step) {
              dx = ex; dy = ey; a.path.shift();
            } else {
              dx = (ex / dist) * step; dy = (ey / dist) * step;
            }
          }
          if (!dx && !dy) continue;
          let moved = false;
          if (!blocked(a.x + dx, a.y)) { a.x += dx; moved = true; }
          if (!blocked(a.x, a.y + dy)) { a.y += dy; moved = true; }
          if (!moved) a.path = [];            // stuck on a wall: give up the walk
          a.container.setPosition(a.x, a.y);
          if (pid === me) {
            const zone = zoneAt(a);
            if (zone !== localZone) {
              localZone = zone;
              showError("");
              renderPanel();
              sendZone();
            }
          }
        }
      }
    };
  }

  /* --- keyboard: only while the canvas has focus --------------------------- */

  const KEYMAP = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
                   a: "left", d: "right", w: "up", s: "down",
                   A: "left", D: "right", W: "up", S: "down" };

  const typing = (target) => !!target && (target.isContentEditable ||
    /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || ""));

  function wireKeys() {
    const box = $("office-canvas");
    if (!box) return;
    box.addEventListener("keydown", (event) => {
      if (typing(event.target) || event.altKey || event.ctrlKey || event.metaKey) return;
      if (!scene || !myId()) return;
      const dir = KEYMAP[event.key];
      if (dir) {
        scene.keys.add(dir);
        event.preventDefault();               // arrows must not scroll the page
        return;
      }
      if ((event.key === "e" || event.key === "E") && !event.repeat) {
        event.preventDefault();
        useHere();
      }
    });
    box.addEventListener("keyup", (event) => {
      const dir = KEYMAP[event.key];
      if (dir && scene) scene.keys.delete(dir);
    });
    box.addEventListener("blur", () => { if (scene) scene.keys.clear(); });
  }

  /* --- tabs ------------------------------------------------------------------ */

  const TAB_KEY = `cp-view-${ROOM}`;

  function moveChat(toOffice) {
    const chat = $("chat-panel"), slot = $("office-chat-slot"), party = $("party-panel");
    if (!chat || !slot || !party) return;
    if (toOffice && chat.parentElement !== slot) slot.append(chat);
    if (!toOffice && chat.previousElementSibling !== party) party.after(chat);
  }

  function boot() {
    if (game || !window.Phaser) return;
    const OfficeScene = makeSceneClass();
    game = new window.Phaser.Game({
      type: window.Phaser.AUTO,
      parent: "office-canvas",
      backgroundColor: hex(cssColour("--ground", 0x0f141a)),
      banner: false,
      input: { keyboard: false },           // keys come from the focused box only
      scale: { mode: window.Phaser.Scale.FIT,
               autoCenter: window.Phaser.Scale.CENTER_HORIZONTALLY,
               width: W, height: H },
      scene: OfficeScene,
    });
  }

  function select(view, focus) {
    const office = view === "office";
    const board = $("view-board"), room = $("view-office");
    if (!board || !room) return;
    board.hidden = office;
    room.hidden = !office;
    for (const [id, on] of [["tab-board", !office], ["tab-office", office]]) {
      const tab = $(id);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    }
    moveChat(office);
    try { window.localStorage.setItem(TAB_KEY, view); } catch (error) { /* private mode */ }
    if (office) {
      if (!window.Phaser) {
        const prompt = $("office-prompt");
        if (prompt) prompt.textContent = "The office needs static/vendor/phaser.min.js. Run ./setup.sh to fetch it.";
        return;
      }
      boot();
      if (game && game.isBooted) game.scale.refresh();
      renderPanel();
    }
  }

  function wireTabs() {
    const tabs = ["tab-board", "tab-office"].map($);
    if (tabs.some((t) => !t)) return;
    tabs[0].addEventListener("click", () => select("board"));
    tabs[1].addEventListener("click", () => select("office"));
    for (const tab of tabs) {
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        select(tab.id === "tab-board" ? "office" : "board", true);
      });
    }
    let saved = null;
    try { saved = window.localStorage.getItem(TAB_KEY); } catch (error) { saved = null; }
    if (saved === "office") select("office");
  }

  /* --- the interface game.js calls ------------------------------------------ */

  window.Office = {
    update(next) {
      state = next;
      for (const [pid, char] of Object.entries(next.characters || {})) {
        if (pid !== myId()) zones[pid] = char.office_zone || zones[pid] || "floor";
      }
      if (scene) scene.sync(false);
      else renderPanel();
    },
    onMove(event, live) {
      if (!event || !event.player_id) return;
      if (event.player_id === myId() && scene) return;   // we walked it ourselves
      zones[event.player_id] = event.zone;
      if (!scene || !state) return;
      const a = scene.avatars.get(event.player_id);
      if (a && !live) a.placed = false;                   // history: snap, don't walk
      scene.sync(false);
    },
  };

  document.addEventListener("DOMContentLoaded", () => {
    wireTabs();
    wireKeys();
    const use = $("office-use");
    if (use) use.addEventListener("click", useHere);
    const form = $("desk-form");
    if (form) form.addEventListener("submit", saveDesk);
  });
})();
