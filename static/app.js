const QUEST_TEXT = 400;
const MODES = {
  stat: ["STATUS", "S.P.E.C.I.A.L."],
  inv: ["ALL", "NOTES", "PICS", "HOLO", "OTHER"],
  data: ["LOCAL", "WORLD", "QUESTS", "RADIO"],
};
const PLACES = {
  head: "grid-column:2;grid-row:1",
  torso: "grid-column:2;grid-row:2 / 4",
  larm: "grid-column:1;grid-row:2",
  rarm: "grid-column:3;grid-row:2",
  lleg: "grid-column:1;grid-row:3",
  rleg: "grid-column:3;grid-row:3",
};

let map = null;
let marker = null;
let follow = true;
let mapScope = "";

const state = {
  mode: "stat",
  sub: "STATUS",
  status: null,
  path: "",
  listing: null,
  selected: null,
  detail: null,
  quests: [],
  questId: null,
  gps: null,
  radio: null,
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function weight(bytes) {
  let value = bytes;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (value < 1024 || unit === "GB") {
      return unit === "B" ? `${value} B` : `${value.toFixed(1)}${unit}`;
    }
    value /= 1024;
  }
  return `${value.toFixed(1)}GB`;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function tick() {
  const now = new Date();
  const when = now.toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "2-digit", year: "numeric",
  }).toUpperCase();
  const hm = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  $("clock").innerHTML = `${when}<br>${hm}`;
}

function pips(score) {
  return Array.from({ length: 10 }, (_, i) => `<i class="tick${i < score ? " on" : ""}"></i>`).join("");
}

function drawChrome() {
  const names = MODES[state.mode];
  if (!names.includes(state.sub)) state.sub = names[0];
  $("modes").innerHTML = Object.keys(MODES).map((mode) =>
    `<button data-mode="${mode}" class="${mode === state.mode ? "on" : ""}">${mode.toUpperCase()}</button>`
  ).join("");
  $("subs").innerHTML = names.map((name) =>
    `<button data-sub="${name}" class="${name === state.sub ? "on" : ""}">${name}</button>`
  ).join("");
  if (state.status) $("host").textContent = state.status.host;
}

function statusView() {
  const snap = state.status;
  if (!snap) return `<p class="eff">LINKING…</p>`;
  const limbs = snap.limbs.map((limb) => `
    <div class="row">
      <span>${esc(limb.part)}</span><span>${esc(limb.name)}</span>
      <div class="track"><i style="--p:${limb.health}%"></i></div>
      <span>${esc(limb.readout)}</span>
    </div>`).join("");
  const chassis = snap.limbs.map((limb) => `
    <div class="node${limb.health < 35 ? " low" : ""}" style="${PLACES[limb.id]};--p:${limb.health}%">
      <i></i><b>${esc(limb.name)}</b>
    </div>`).join("");
  const clock = snap.clock ? `<span>CLK <b>${esc(snap.clock)}</b></span>` : "";
  return `
    <div class="strip">
      <span>HP <b>${snap.hp}/100</b></span>
      <span>TEMP <b>${snap.temp == null ? "—" : snap.temp + "°C"}</b></span>
      <span>UP <b>${esc(snap.uptime)}</b></span>
      ${clock}
    </div>
    <div class="status-grid">
      <div class="chassis">${chassis}</div>
      <div>
        <div class="readouts">${limbs}</div>
        <div class="eff"><span class="meter">RAD ${pips(snap.rad.level)}</span> ${esc(snap.rad.label)}</div>
        <div class="eff">EFF ${esc(snap.effects.join(" · "))}</div>
      </div>
    </div>`;
}

function specialView() {
  const snap = state.status;
  if (!snap) return `<p class="eff">LINKING…</p>`;
  const rows = snap.special.map((trait) => `
    <div class="row">
      <span>${esc(trait.key)}</span>
      <span>${esc(trait.name)} · ${esc(trait.trait)}</span>
      <span class="meter">${pips(trait.score)}</span>
    </div>`).join("");
  return `<div class="readouts special">${rows}</div>`;
}

function invView() {
  const listing = state.listing;
  if (!listing) return `<p class="eff">SEARCHING…</p>`;
  const crumbs = ["HOME", ...listing.path.split("/").filter(Boolean)];
  const crumbHtml = crumbs.map((part, index) => {
    const target = crumbs.slice(1, index + 1).join("/");
    return `<button data-path="${esc(index === 0 ? "" : target)}">${esc(part)}</button>`;
  }).join("<span>/</span>");
  const entries = listing.entries.filter((entry) => state.sub === "ALL" || entry.dir || entry.kind === state.sub);
  const rows = entries.map((entry) => {
    const full = listing.path ? `${listing.path}/${entry.name}` : entry.name;
    const on = state.selected === full ? "on" : "";
    const meta = entry.dir ? entry.kind : weight(entry.size);
    return `<button class="${on}" data-file="${esc(full)}" data-dir="${entry.dir ? "1" : "0"}"><span>${esc(entry.name)}</span><span>${esc(meta)}</span></button>`;
  }).join("") || `<p class="eff">NOTHING IN THIS FOLDER.</p>`;
  const detail = state.detail || `<div class="detail"><p>SELECT AN ITEM.</p></div>`;
  const more = listing.truncated ? `<p class="note">LIST TRUNCATED</p>` : "";
  return `<div class="crumbs">${crumbHtml}</div><div class="split"><div class="list">${rows}</div>${detail}</div>${more}`;
}

function detailFor(entry, full) {
  const lines = `
    <h3>${esc(entry.name)}</h3>
    <div class="kv">
      <span>TYPE</span><span>${esc(entry.kind)}</span>
      <span>WT</span><span>${entry.dir ? "—" : esc(weight(entry.size))}</span>
      <span>MOD</span><span>${esc(entry.mtime)}</span>
    </div>`;
  return lines;
}

async function openItem(full, isDir) {
  if (isDir) {
    state.path = full;
    state.selected = null;
    state.detail = null;
    await loadFiles();
    return;
  }
  state.selected = full;
  const entry = (state.listing?.entries || []).find((item) => {
    const itemPath = state.listing.path ? `${state.listing.path}/${item.name}` : item.name;
    return itemPath === full;
  });
  if (!entry) return;
  let extra = "";
  if (entry.kind === "NOTES" && !full.toLowerCase().endsWith(".pdf")) {
    try {
      const text = await api("/api/files/text?path=" + encodeURIComponent(full));
      extra = `<pre>${esc(text.text)}</pre>`;
    } catch {
      extra = `<p>CANNOT READ THIS NOTE.</p>`;
    }
  } else if (entry.kind === "PICS") {
    extra = `<img alt="" src="/api/files/image?path=${encodeURIComponent(full)}">`;
  } else if (entry.kind === "HOLO") {
    extra = `<p>HOLOTAPE. PLAYBACK STAYS WITH THE RADIO.</p>`;
  } else if (full.toLowerCase().endsWith(".pdf")) {
    extra = `<p>DOCUMENT. OPEN IT FROM THE DESKTOP FOR NOW.</p>`;
  }
  state.detail = `<div class="detail">${detailFor(entry, full)}${extra}</div>`;
  draw();
}

function questView() {
  if (!state.quests) return `<p class="eff">LINKING…</p>`;
  if (!state.quests.length) {
    return `<p class="eff">NO QUESTS.</p>${questForm()}`;
  }
  if (!state.quests.some((quest) => quest.id === state.questId)) state.questId = state.quests[0].id;
  const list = state.quests.map((quest) => `
    <button data-quest="${esc(quest.id)}" class="${quest.done ? "dim" : ""} ${quest.id === state.questId ? "on" : ""}">
      <span>${quest.done ? "☐" : "▣"} ${esc(quest.name)}</span>
    </button>`).join("");
  const quest = state.quests.find((item) => item.id === state.questId);
  const objectives = (quest.objectives || []).map((item) => `<div>▸ ${esc(item.text)}</div>`).join("") || `<p>NO OBJECTIVES.</p>`;
  return `
    <div class="split">
      <div class="list">${list}</div>
      <div class="detail">
        <h3>${esc(quest.name)}</h3>
        ${objectives}
        <p>${quest.done ? "COMPLETE" : "ACTIVE"}</p>
        <div class="compose">
          <button type="button" data-toggle="${esc(quest.id)}">${quest.done ? "REOPEN" : "COMPLETE"}</button>
          ${quest.done ? `<button type="button" class="quiet" data-remove="${esc(quest.id)}">REMOVE</button>` : ""}
        </div>
        <form class="compose" data-objective="${esc(quest.id)}">
          <input name="text" placeholder="OBJECTIVE" autocomplete="off" maxlength="${QUEST_TEXT}">
          <button type="submit">ADD</button>
        </form>
      </div>
    </div>
    ${questForm()}`;
}

function questForm() {
  return `<form class="compose" id="new-quest"><input name="name" placeholder="NEW QUEST" autocomplete="off" maxlength="${QUEST_TEXT}"><button type="submit">ADD</button></form>`;
}

function gpsLine(gps) {
  if (!gps || !gps.receiver) return "NO RECEIVER";
  if (!(gps.mode >= 2) || gps.lat == null) return "ACQUIRING SATELLITE";
  const alt = gps.alt_m == null ? "" : `  ${Math.round(gps.alt_m)}M`;
  const sats = gps.sats == null ? "" : `  ${gps.sats} SATS`;
  return `${gps.lat.toFixed(5)}  ${gps.lon.toFixed(5)}${sats}${alt}`;
}

function destroyMap() {
  if (map) {
    map.remove();
    map = null;
    marker = null;
  }
  mapScope = "";
}

function mapView() {
  return `
    <div class="map-page">
      <div id="map"></div>
      <div class="map-bar">
        <span id="map-meta">${esc(gpsLine(state.gps))}</span>
        <button type="button" data-recenter>CENTER</button>
      </div>
    </div>`;
}

function paintMap(world) {
  const meta = document.getElementById("map-meta");
  if (meta) meta.textContent = gpsLine(state.gps);
  const gps = state.gps;
  const host = document.getElementById("map");
  if (!host || !gps || gps.lat == null || !(gps.mode >= 2) || typeof L === "undefined") return;
  const zoom = world ? 12 : 17;
  if (!map) {
    map = L.map(host, { zoomControl: true, attributionControl: true }).setView([gps.lat, gps.lon], zoom);
    L.tileLayer("/tiles/{z}/{x}/{y}.png?v=2", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    marker = L.marker([gps.lat, gps.lon], {
      icon: L.divIcon({ className: "you-pin-wrap", html: "<i></i>", iconSize: [14, 14], iconAnchor: [7, 7] }),
      keyboard: false,
    }).addTo(map);
    map.on("dragstart", () => { follow = false; });
    mapScope = world ? "world" : "local";
  } else if ((world ? "world" : "local") !== mapScope) {
    map.setZoom(zoom);
    mapScope = world ? "world" : "local";
  }
  marker.setLatLng([gps.lat, gps.lon]);
  if (follow) map.panTo([gps.lat, gps.lon], { animate: false });
  map.invalidateSize();
}

function radioView() {
  const radio = state.radio;
  if (!radio) return `<p class="eff">TUNING…</p>`;
  const why = !radio.present
    ? "NO RECEIVER"
    : !radio.decoder
      ? "RECEIVER FOUND · RTL_FM NOT INSTALLED"
      : !radio.audio
        ? "RECEIVER FOUND · ALSA PLAYBACK MISSING"
        : radio.error
          ? esc(radio.error).toUpperCase()
          : radio.playing ? "CARRIER OPEN" : "STANDBY";
  return `
    <div class="freq">${radio.freq.toFixed(1)} MHZ</div>
    <div class="tune">
      <button type="button" data-tune="-0.2">−</button>
      <button type="button" data-tune="0.2">+</button>
      <button type="button" data-listen="${radio.playing ? "0" : "1"}">${radio.playing ? "CUT" : "LISTEN"}</button>
    </div>
    <p class="eff">${why}</p>
    <p class="note">${esc(radio.device || "FM BROADCAST · 87.5 TO 108.0")}</p>`;
}

function draw() {
  destroyMap();
  drawChrome();
  const view = $("view");
  if (state.mode === "stat") view.innerHTML = state.sub === "STATUS" ? statusView() : specialView();
  else if (state.mode === "inv") view.innerHTML = invView();
  else if (state.sub === "QUESTS") view.innerHTML = questView();
  else if (state.sub === "RADIO") view.innerHTML = radioView();
  else view.innerHTML = mapView();
  if (state.mode === "data" && (state.sub === "LOCAL" || state.sub === "WORLD")) {
    paintMap(state.sub === "WORLD");
  }
}

async function loadStatus() {
  state.status = await api("/api/status");
  if (state.mode === "stat") draw();
}

async function loadFiles() {
  state.listing = await api("/api/files?path=" + encodeURIComponent(state.path));
  if (state.mode === "inv") draw();
}

async function loadQuests() {
  const data = await api("/api/quests");
  state.quests = data.quests;
  if (state.mode === "data" && state.sub === "QUESTS") draw();
}

async function loadGps() {
  state.gps = await api("/api/gps");
  if (!(state.mode === "data" && (state.sub === "LOCAL" || state.sub === "WORLD"))) return;
  if (!document.getElementById("map")) draw();
  else paintMap(state.sub === "WORLD");
}

async function loadRadio() {
  state.radio = await api("/api/radio");
  if (state.mode === "data" && state.sub === "RADIO") draw();
}

let timer = 0;
function armPoll() {
  clearInterval(timer);
  const beat = async () => {
    try {
      if (state.mode === "stat") await loadStatus();
      else if (state.mode === "data" && (state.sub === "LOCAL" || state.sub === "WORLD")) await loadGps();
      else if (state.mode === "data" && state.sub === "RADIO") await loadRadio();
    } catch (err) {
      $("view").innerHTML = `<p class="eff">SIGNAL LOST</p>`;
    }
  };
  timer = setInterval(beat, 2000);
}

async function enter() {
  draw();
  try {
    if (state.mode === "stat") await loadStatus();
    else if (state.mode === "inv") await loadFiles();
    else if (state.sub === "QUESTS") await loadQuests();
    else if (state.sub === "RADIO") await loadRadio();
    else await loadGps();
  } catch (err) {
    $("view").innerHTML = `<p class="eff">SIGNAL LOST</p>`;
  }
  armPoll();
}

document.body.addEventListener("click", async (event) => {
  const mode = event.target.closest("[data-mode]");
  const sub = event.target.closest("[data-sub]");
  const file = event.target.closest("[data-file]");
  const path = event.target.closest("[data-path]");
  const quest = event.target.closest("[data-quest]");
  const toggle = event.target.closest("[data-toggle]");
  const remove = event.target.closest("[data-remove]");
  const tune = event.target.closest("[data-tune]");
  const listen = event.target.closest("[data-listen]");
  if (mode) {
    state.mode = mode.dataset.mode;
    state.sub = MODES[state.mode][0];
    await enter();
  } else if (sub) {
    state.sub = sub.dataset.sub;
    state.selected = null;
    state.detail = null;
    await enter();
  } else if (path && event.target.closest(".crumbs")) {
    state.path = path.dataset.path;
    state.selected = null;
    state.detail = null;
    await loadFiles();
  } else if (file) {
    await openItem(file.dataset.file, file.dataset.dir === "1");
  } else if (quest) {
    state.questId = quest.dataset.quest;
    draw();
  } else if (toggle) {
    const data = await api("/api/quests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "toggle", id: toggle.dataset.toggle }),
    });
    state.quests = data.quests;
    draw();
  } else if (remove) {
    const data = await api("/api/quests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "remove", id: remove.dataset.remove }),
    });
    state.quests = data.quests;
    if (!state.quests.some((item) => item.id === state.questId)) state.questId = state.quests[0]?.id || null;
    draw();
  } else if (tune) {
    const next = Math.round((state.radio.freq + Number(tune.dataset.tune)) * 10) / 10;
    const freq = Math.max(87.5, Math.min(108, next));
    state.radio = await api("/api/radio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ freq, play: state.radio.playing }),
    });
    draw();
  } else if (event.target.closest("[data-recenter]")) {
    follow = true;
    if (map && state.gps && state.gps.lat != null) {
      const zoom = state.sub === "WORLD" ? 12 : 17;
      map.setView([state.gps.lat, state.gps.lon], zoom);
    }
  } else if (listen) {
    state.radio = await api("/api/radio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ freq: state.radio.freq, play: listen.dataset.listen === "1" }),
    });
    draw();
  }
});

// labwc hides layer-shell "top" surfaces, including squeekboard, while Chromium
// is fullscreen. This keyboard is drawn in the page so a finger can still type.
const KEY_ROWS = {
  letters: [
    [..."qwertyuiop"],
    [..."asdfghjkl"],
    ["shift", ..."zxcvbnm", "back"],
    ["nums", "space", ".", "-", "hide"],
  ],
  nums: [
    [..."1234567890"],
    ["-", "/", ":", ";", "(", ")", "$", "&", "@", "\""],
    ["letters", ",", "'", "?", "!", "+", "=", "back"],
    ["letters", "space", ".", "-", "hide"],
  ],
};
const KEY_LABEL = {
  shift: () => (caps ? "CAPS" : "SHIFT"),
  back: () => "DEL",
  space: () => "SPACE",
  hide: () => "HIDE",
  nums: () => "123",
  letters: () => "ABC",
};

let caps = false;
let keyLayout = "letters";
let lastField = null;
state.keys = false;

function isField(el) {
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA") && !el.readOnly && !el.disabled;
}

function field() {
  if (isField(document.activeElement)) return document.activeElement;
  if (isField(lastField) && document.contains(lastField)) return lastField;
  return null;
}

function renderKeys() {
  const board = $("keyboard");
  board.innerHTML = KEY_ROWS[keyLayout].map((row) => `
    <div class="osk-row">${row.map((key) => {
      const label = KEY_LABEL[key] ? KEY_LABEL[key]() : (caps ? key.toUpperCase() : key);
      const cls = [KEY_LABEL[key] ? "fn" : "", key === "space" ? "wide" : "", key === "shift" && caps ? "on" : ""].filter(Boolean).join(" ");
      return `<button type="button" data-key="${esc(key)}" class="${cls}">${esc(label)}</button>`;
    }).join("")}</div>`).join("");
}

function placeCaret(el, value, caret) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value").set.call(el, value);
  const pos = Math.max(0, Math.min(caret, value.length));
  el.setSelectionRange(pos, pos);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function insertText(text) {
  const el = field();
  if (!el) return;
  el.focus();
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? start;
  placeCaret(el, el.value.slice(0, start) + text + el.value.slice(end), start + text.length);
}

function backspace() {
  const el = field();
  if (!el) return;
  el.focus();
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? start;
  if (start !== end) placeCaret(el, el.value.slice(0, start) + el.value.slice(end), start);
  else if (start > 0) placeCaret(el, el.value.slice(0, start - 1) + el.value.slice(end), start - 1);
}

function pressKey(key) {
  if (key === "hide") {
    setKeyboard(false);
    return;
  }
  if (key === "shift") {
    caps = !caps;
    renderKeys();
    return;
  }
  if (key === "nums" || key === "letters") {
    keyLayout = key === "nums" ? "nums" : "letters";
    renderKeys();
    return;
  }
  if (key === "back") {
    backspace();
    return;
  }
  insertText(key === "space" ? " " : (caps && keyLayout === "letters" ? key.toUpperCase() : key));
}

function setKeyboard(open) {
  state.keys = open;
  $("keyboard").hidden = !open;
  const button = $("key");
  button.classList.toggle("on", open);
  button.setAttribute("aria-pressed", open ? "true" : "false");
  const el = field();
  if (open && el) el.focus();
  requestAnimationFrame(() => {
    if (map) map.invalidateSize();
    if (open && el) el.scrollIntoView({ block: "center" });
  });
}

document.addEventListener("focusin", (event) => {
  if (!isField(event.target)) return;
  lastField = event.target;
  if (state.keys) requestAnimationFrame(() => event.target.scrollIntoView({ block: "center" }));
});

document.addEventListener("pointerdown", (event) => {
  if (event.target.closest("#keyboard [data-key], #key")) event.preventDefault();
});

document.addEventListener("pointerup", (event) => {
  const key = event.target.closest("#keyboard [data-key]");
  if (key) {
    pressKey(key.dataset.key);
    return;
  }
  if (event.target.closest("#key")) setKeyboard(!state.keys);
});

renderKeys();

document.body.addEventListener("submit", async (event) => {
  const form = event.target;
  if (form.id === "new-quest") {
    event.preventDefault();
    const name = new FormData(form).get("name").trim();
    if (!name) return;
    const data = await api("/api/quests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "add", name }),
    });
    state.quests = data.quests;
    state.questId = state.quests[0]?.id || null;
    draw();
  } else if (form.dataset.objective) {
    event.preventDefault();
    const text = new FormData(form).get("text").trim();
    if (!text) return;
    const data = await api("/api/quests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "objective", id: form.dataset.objective, text }),
    });
    state.quests = data.quests;
    draw();
  }
});

tick();
setInterval(tick, 1000);
setTimeout(() => {
  $("boot").hidden = true;
  $("app").hidden = false;
}, 700);
enter();
