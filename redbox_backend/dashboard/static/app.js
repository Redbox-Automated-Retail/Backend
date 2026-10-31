"use strict";
// Redbox control center: hash-routed single page over /api (see dashboard/server.py and dashboard/control.py).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = () => $("#view");
let ME = { user: "", role: "viewer" };
const can = (what) => ME.role === "admin" || (what !== "admin" && ME.role === "operator");

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Requested-With": "rb-admin" }, credentials: "same-origin" };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  if (opts.body !== undefined || opts.form) init.method = opts.method || "POST";
  const r = await fetch("/api" + path, init);
  if (r.status === 401 && path !== "/login") { showLogin(); throw new Error("login required"); }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || r.statusText);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), bad ? 7000 : 3500);
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg); return r; }
  catch (e) { toast(e.message, true); throw e; }
}

const fmtDate = (v) => {
  if (!v) return "";
  const d = new Date(v);
  return isNaN(d) ? esc(v) : d.toLocaleString();
};
const ago = (v) => {
  if (!v) return "never";
  const s = (Date.now() - new Date(v)) / 1000;
  if (s < 60) return Math.max(0, Math.round(s)) + " s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
};
const num = (v) => Number(v || 0).toLocaleString();
const pill = (text, cls) => `<span class="pill ${cls || ""}">${esc(text)}</span>`;
const short = (h) => (h ? esc(String(h).slice(0, 10)) : "");
const cell = (v) => {
  if (v === null || v === undefined) return '<span class="dim">-</span>';
  if (typeof v === "object") return `<code>${esc(JSON.stringify(v)).slice(0, 160)}</code>`;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(v)) return fmtDate(v);
  return esc(v);
};

function table(rows, cols, opts = {}) {
  if (!rows || !rows.length) return `<p class="muted" style="padding:${opts.pad ? "0 18px 16px" : "0"}">${esc(opts.empty || "Nothing here yet.")}</p>`;
  const head = cols.map((c) => `<th class="${c.right ? "r" : ""}">${esc(c.label ?? c.key)}</th>`).join("");
  const body = rows.map((r, i) => `<tr class="${opts.onclick ? "click" : ""}" data-i="${i}">` +
    cols.map((c) => `<td class="${c.wrap ? "wrap" : ""} ${c.right ? "r" : ""}">${c.render ? c.render(r[c.key], r) : cell(r[c.key])}</td>`).join("") + "</tr>").join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function bindRows(root, rows, fn) { $$("tr.click", root).forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("button,a,input")) fn(rows[+tr.dataset.i]); })); }

function modal(html) { $("#modal-content").innerHTML = html; $("#modal").classList.remove("hidden"); return $("#modal-content"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ------------------------------------------------------------------------------------------- icons
const ICON = {
  home: "M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  kiosk: "M6 2h12a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm2 3v6h8V5zm0 9v2h3v-2z",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  cart: "M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6.2M9 20.5a1 1 0 1 0 0-.01M17 20.5a1 1 0 1 0 0-.01",
  ticket: "M3 8a2 2 0 0 0 0 4v4a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-4a2 2 0 0 1 0-4V5a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1zM14 4v13",
  link: "M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1M14 10a4 4 0 0 0-5.6 0l-3 3A4 4 0 0 0 11 18.6l1-1",
  users: "M16 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 20v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  gift: "M20 12v9H4v-9M2 7h20v5H2zM12 21V7M12 7H7.5a2.5 2.5 0 1 1 0-5C11 2 12 7 12 7zM12 7h4.5a2.5 2.5 0 1 0 0-5C13 2 12 7 12 7z",
  film: "M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM7 3v18M17 3v18M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4",
  disc: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  box: "M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
  rocket: "M5 15c-1.5 1.3-2 5-2 5s3.7-.5 5-2M9 15l-3-3a14 14 0 0 1 10-9 3 3 0 0 1 3 3 14 14 0 0 1-9 10zM9 9H5l-2 3h5M15 15v4l-3 2v-5",
  layers: "M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  folder: "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  terminal: "M4 17l6-5-6-5M12 19h8",
  wrench: "M14.7 6.3a4 4 0 0 0 5 5l-9.4 9.4a2.1 2.1 0 0 1-3-3zM14.7 6.3 17 4",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  db: "M12 8c5 0 9-1.3 9-3s-4-3-9-3-9 1.3-9 3 4 3 9 3zM3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  ping: "M5 12.55a11 11 0 0 1 14 0M8.5 16.4a6 6 0 0 1 7 0M12 20h.01M2 8.8a16 16 0 0 1 20 0",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  undo: "M3 7v6h6M21 17a9 9 0 0 0-15-6.7L3 13",
  plus: "M12 5v14M5 12h14",
  check: "M20 6 9 17l-5-5",
  audit: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
};
const ic = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${ICON[n]}"/></svg>`;

const NAV = [
  ["Control center", [["overview", "home", "Overview"], ["kiosks", "kiosk", "Kiosks"], ["system", "pulse", "System health"]]],
  ["Kiosk activity", [["rentals", "cart", "Rentals & returns"], ["data/reservations", "ticket", "Reservations"], ["broker", "link", "Reservation broker"],
    ["data/customers", "users", "Customers"], ["pricing", "gift", "Pricing & offers"], ["tickets", "ticket", "Event tickets"]]],
  ["Content", [["catalog", "film", "Catalog"], ["data/inventory", "disc", "Inventory"], ["packages", "box", "Data zips"]]],
  ["Software", [["releases", "rocket", "Releases"], ["updates", "layers", "Update Service"], ["filesets", "folder", "File sets"], ["config", "sliders", "Configuration"]]],
  ["Operations", [["jobs", "terminal", "Remote commands"], ["provisioning", "wrench", "Provisioning"]]],
  ["Admin", [["access", "shield", "Users & access"], ["audit", "audit", "Audit log"], ["logs", "list", "Logs & events"], ["tables", "db", "All tables"]]],
];
function drawNav(counts = {}) {
  $("#nav").innerHTML = NAV.map(([g, items]) => `<div class="nav-group">${esc(g)}</div><nav>` + items.map(([id, icon, label]) =>
    `<a href="#/${id}" data-nav="${id}">${ic(icon)}${esc(label)}${counts[id] ? `<span class="badge">${esc(counts[id])}</span>` : ""}</a>`).join("") + "</nav>").join("");
  markNav();
}
function markNav() {
  const hash = location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const name = hash.split("/")[0];
  const key = name === "kiosk" ? "kiosks" : hash.startsWith("data/") ? hash : name;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === key));
}

// ------------------------------------------------------------------------------------------ login
function showLogin() { $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); $("#login-pass").focus(); }
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/login", { body: { username: $("#login-user").value, password: $("#login-pass").value } });
    $("#login-pass").value = "";
    start();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/logout", { body: {} }); showLogin(); });

async function start() {
  try { ME = await api("/me"); } catch { return; }
  $("#me").textContent = ME.user; $("#me-role").textContent = ME.role; $("#me-avatar").textContent = ME.user.slice(0, 1);
  $("#login").classList.add("hidden"); $("#shell").classList.remove("hidden");
  drawNav();
  route();
  health();
}

// Service health line in the sidebar, refreshed every 30 s.
async function health() {
  clearTimeout(health.timer);
  try {
    const s = await api("/system");
    const up = s.services.filter((x) => x.ok).length;
    const cls = up === s.services.length ? "" : up >= s.services.length - 1 ? "warn" : "bad";
    $("#health").innerHTML = `<span class="dot ${cls}"></span>${up === s.services.length ? "All services healthy" : `${up} of ${s.services.length} services healthy`}`;
  } catch { $("#health").innerHTML = '<span class="dot bad"></span>Health unknown'; }
  health.timer = setTimeout(health, 30000);
}

// ----------------------------------------------------------------------------------------- router
const routes = {};
let pageTitle = "";
function route() {
  const hash = location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const [name, ...rest] = hash.split("/");
  const key = routes[hash] ? hash : name;
  markNav();
  const fn = routes[key] || routes.overview;
  pageTitle = "";
  view().innerHTML = '<p class="muted">Loading...</p>';
  fn(...(routes[hash] ? [] : rest)).then(() => {
    // pages that do not draw their own header get one from title()
    if (pageTitle && !$(".head", view()) && !$(".crumb", view())) view().insertAdjacentHTML("afterbegin", `<div class="head"><h1>${esc(pageTitle)}</h1></div>`);
  }).catch((e) => { view().innerHTML = `<div class="head"><h1>${esc(pageTitle || "Error")}</h1></div><p class="error">${esc(e.message)}</p>`; });
}
window.addEventListener("hashchange", () => { closeModal(); route(); });
const title = (t) => { pageTitle = t; document.title = (t ? t + " - " : "") + "Redbox Control Center"; };
const head = (t, lede = "", actions = "") => { title(t); return `<div class="head"><div><h1>${esc(t)}</h1>${lede ? `<div class="lede">${lede}</div>` : ""}</div><span class="grow"></span>${actions}</div>`; };

// tiny SVG line for stat tiles
function spark(vals) {
  if (!vals || vals.length < 2) return "";
  const w = 200, h = 30, max = Math.max(...vals) || 1, step = w / (vals.length - 1);
  const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - 3 - (v / max) * (h - 8)).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="#6A626F" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
const statusOf = (k) => (k.mqtt_connected || k.status === "online" ? ["ok", "Online"] : k.status === "recent" || k.online ? ["ok", "Recent"]
  : k.status === "stale" ? ["warn", "Stale"] : ["bad", "Offline"]);

// --------------------------------------------------------------------------------------- overview
routes.overview = async () => {
  const d = await api("/fleet");
  const s = d.status, c = d.counts;
  const up = s.online + s.recent;
  const now = new Date();
  view().innerHTML = head("Overview", `${up} of ${s.total} kiosks online · ${esc(now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }))}, ${esc(now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}`,
    can() ? `<a class="button" href="#/jobs">${ic("terminal")}Send a command</a><a class="button primary" href="#/releases">${ic("rocket")}Roll out a release</a>` : "") + `
    <div class="grid4">
      <a class="card stat" href="#/kiosks"><div class="l">Kiosks online</div><div class="n">${up}<small> / ${s.total}</small></div>
        <div class="s">${[s.stale && `${s.stale} stale`, s.offline && `${s.offline} offline`].filter(Boolean).join(" · ") || "all reporting"}</div></a>
      <a class="card stat" href="#/rentals"><div class="l">Rentals today</div><div class="n">${num(c.rentals_today)}</div><div class="s">${num(c.returns_24h)} returns in 24 h</div>${spark(d.hourly.map((h) => +h.n))}</a>
      <a class="card stat" href="#/data/rentals"><div class="l">Discs out</div><div class="n">${num(c.rentals_out)}</div><div class="s">${num(c.discs)} discs in kiosks</div></a>
      <a class="card stat" href="#/data/reservations"><div class="l">Open reservations</div><div class="n">${num(c.reservations_open)}</div><div class="s">${num(c.broker_kiosks)} kiosks on the broker</div></a>
    </div>`;
};

// ----------------------------------------------------------------------------------------- kiosks
routes.kiosks = async () => {
  const rows = await api("/kiosks");
  const q = query();
  const rank = (r) => ({ bad: 0, warn: 1, ok: 2 }[statusOf(r)[0]]);
  rows.sort((a, b) => rank(a) - rank(b) || a.kiosk_id - b.kiosk_id);
  view().innerHTML = head("Kiosks", `${rows.length} kiosks · ${rows.filter((r) => statusOf(r)[0] === "ok").length} online`,
    can() ? `<button id="rpv">Reprovision a kiosk</button><button id="bulk">${ic("terminal")}Command to selected</button>` : "") + `
    <div class="toolbar"><div class="search grow">${ic("search")}<input id="kf" placeholder="Filter by id, market, city, version" value="${esc(q.q || "")}"></div>
      <select id="ks"><option value="">any status</option><option value="ok">online</option><option value="warn">stale</option><option value="bad">offline</option></select></div>
    <div class="card flush" id="kt"></div>`;
  if (q.status) $("#ks").value = { offline: "bad", stale: "warn", online: "ok" }[q.status] || "";
  const draw = () => {
    const f = $("#kf").value.toLowerCase(), st = $("#ks").value;
    const list = rows.filter((r) => (!st || statusOf(r)[0] === st) && (!f || JSON.stringify([r.kiosk_id, r.market, r.city, r.label, r.ucs_version, r.versions]).toLowerCase().includes(f)));
    $("#kt").innerHTML = table(list, [
      { key: "_sel", label: "", render: (v, r) => `<input type="checkbox" data-k="${esc(r.kiosk_id)}">` },
      { key: "kiosk_id", label: "Kiosk", render: (v) => `<b>${esc(v)}</b>` },
      { key: "label", label: "Location", render: (v, r) => `<span class="muted">${esc([r.market, [v, r.city].filter(Boolean).join(", ")].filter(Boolean).join(" · ") || "-")}</span>` },
      { key: "online", label: "Status", render: (v, r) => { const [c, t] = statusOf(r); return `<span class="st ${c}"><span class="dot ${c}"></span>${t}</span>`; } },
      { key: "last_seen_at", label: "Last seen", render: (v) => `<span class="muted">${esc(ago(v))}</span>` },
      { key: "versions", label: "Engine", render: (v) => `<span class="muted">${esc(v && (v.KioskEngine || v["Kiosk Engine"] || Object.values(v)[0]) || "-")}</span>` },
      { key: "ucs_version", label: "Update Client", render: (v) => `<span class="muted">${esc(v || "-")}</span>` },
      { key: "config_version", label: "Config", render: (v) => `<span class="muted">${v ? "v" + esc(v) : "-"}</span>` },
      { key: "discs", label: "Discs", right: true },
    ], { onclick: true, pad: true, empty: "No kiosk has contacted the backend yet." });
    bindRows($("#kt"), list, (r) => (location.hash = "#/kiosk/" + r.kiosk_id));
  };
  $("#kf").addEventListener("input", draw); $("#ks").onchange = draw; draw();
  if ($("#rpv")) $("#rpv").onclick = () => reprovisionDialog("");
  if ($("#bulk")) $("#bulk").onclick = () => {
    const ids = $$("input[data-k]:checked").map((c) => c.dataset.k);
    if (!ids.length) return toast("Tick the kiosks first", true);
    commandDialog(ids);
  };
};

// One IoT command to one or more kiosks.
async function commandDialog(ids, preset) {
  const cmds = await api("/jobs/commands");
  const m = modal(`<h3>Command to ${ids.length === 1 ? "kiosk " + esc(ids[0]) : ids.length + " kiosks"}</h3><form id="cf">
    <label>Command <select name="command">${Object.keys(cmds).map((c) => `<option ${c === preset ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
    <label>Version <input name="version" type="number"></label><label>Payload (JSON) <textarea name="payload" rows="5"></textarea></label>
    <div class="toolbar mt"><button class="primary">Send</button><button type="button" id="cl">Cancel</button></div></form><pre id="co" class="mt hidden"></pre>`);
  const f = $("#cf", m);
  const fill = () => { const c = cmds[f.command.value]; f.version.value = c.version; f.payload.value = c.payload === null ? "" : JSON.stringify(c.payload, null, 1); };
  f.command.onchange = fill; fill();
  $("#cl", m).onclick = closeModal;
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await act(() => api("/kiosks/command", { body: { kiosks: ids, command: f.command.value, version: f.version.value, payload: f.payload.value } }),
      (x) => `${f.command.value}: delivered to ${x.delivered} of ${ids.length}`);
    $("#co", m).classList.remove("hidden"); $("#co", m).textContent = JSON.stringify(r.results, null, 2);
  });
}

// ----------------------------------------------------------------------------------- kiosk detail
const KIOSK_TABS = [["overview", "Overview"], ["software", "Software"], ["inventory", "Inventory"], ["rentals", "Rentals"], ["reservations", "Reservations"],
  ["commands", "Commands"], ["files", "Files"], ["events", "Events"], ["settings", "Settings"]];
const FS_STATE = ["Active", "InProgress", "NeedsDependency", "Error"];

routes.kiosk = async (id, tab = "overview") => {
  const [d, a] = await Promise.all([api("/kiosks/" + id), api(`/kiosks/${id}/activity`)]);
  const k = d.kiosk, loc = d.location || {};
  const [sc, st] = d.mqtt ? ["ok", "Online"] : (Date.now() - new Date(k.last_seen_at)) < 20 * 60000 ? ["ok", "Recent"]
    : (Date.now() - new Date(k.last_seen_at)) < 86400000 ? ["warn", "Stale"] : ["bad", "Offline"];
  const engine = k.info && k.info.Versions ? (k.info.Versions.KioskEngine || k.info.Versions["Kiosk Engine"] || "") : "";
  title("Kiosk " + id);
  view().innerHTML = `<div class="crumb"><a href="#/kiosks">Kiosks</a>${k.market ? " / " + esc(k.market) : ""}</div>
    <div class="head"><div><h1>${esc(id)} <span class="dim" style="font-weight:600">${esc(loc.label || loc.city || "")}</span></h1>
      <div class="lede row" style="gap:16px"><span class="st ${sc}"><span class="dot ${sc}"></span>${st}</span>${k.market ? `<span>${esc(k.market)} market</span>` : ""}
        ${engine ? `<span>Kiosk Engine ${esc(engine)}</span>` : ""}${d.mqtt ? `<span class="dim">MQTT from ${esc(d.mqtt.peer)}</span>` : ""}</div></div>
      <span class="grow"></span>${can() ? `<button data-cmd="Ping">${ic("ping")}Ping</button><button data-cmd="TriggerGetConfigChanges">${ic("sliders")}Push config</button>
      <select id="more" class="button" style="width:auto;padding-right:8px"><option value="">More...</option><option value="TriggerReportFileSetVersions">Check file sets</option>
        <option value="SyncTimestampAndTimezone">Sync clock</option><option value="_peek">Peek logs</option><option value="_upload">Upload logs</option>
        <option value="_cmd">Other command...</option><option value="_reprovision">Reprovision file</option><option value="_reboot">Reboot...</option></select>` : ""}</div>
    <div class="tabs">${KIOSK_TABS.map(([t, l]) => `<a href="#/kiosk/${esc(id)}/${t}" class="${t === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="kv"></div>`;
  const send = async (cmd, payload, version, wait = 0) => {
    const cmds = await api("/jobs/commands"); const c = cmds[cmd] || {};
    return act(() => api("/jobs/iot", { body: { target: id, command: cmd, version: version || c.version, payload: payload === undefined ? c.payload : payload, wait } }),
      (r) => `${cmd}: ${r.delivered ? "delivered" : "not delivered (not connected)"}` + (r.response !== undefined ? (r.response ? ", kiosk answered" : ", no answer") : ""));
  };
  $$("button[data-cmd]").forEach((b) => (b.onclick = () => send(b.dataset.cmd, undefined, undefined, b.dataset.cmd === "Ping" ? 10 : 0)));
  if ($("#more")) $("#more").onchange = async (e) => {
    const v = e.target.value; e.target.value = "";
    if (v === "_peek") return peekDialog(id);
    if (v === "_upload") return uploadDialog(id);
    if (v === "_cmd") return commandDialog([id]);
    if (v === "_reprovision") return reprovisionDialog(id, k.market);
    if (v === "_reboot") {
      if (!confirm(`Reboot kiosk ${id} now? Anyone at the kiosk is interrupted.`)) return;
      return send("ExecuteHttpRequest", { Url: "http://localhost:6001/api/kernel/shutdown?shutdownType=Reboot", HttpMethod: "POST", JsonBody: "" }, 2);
    }
    if (v) send(v);
  };
  const root = $("#kv");
  const repoTable = (rows) => table(rows, [{ key: "repo", label: "Repository", render: (v) => `<b>${esc(v)}</b>` },
    { key: "reported", label: "Runs", render: (v, r) => v ? `<code>${esc(r.reported_label || short(v))}</code>` : '<span class="dim">-</span>' },
    { key: "server_head", label: "Newest on the server", render: (v) => `<code>${short(v)}</code>` },
    { key: "current", label: "", right: true, render: (v, r) => !r.subscribed ? '<span class="st mute">Not subscribed</span>' : !r.reported ? '<span class="st warn"><span class="dot warn"></span>No report</span>'
      : v ? '<span class="st mute">Current</span>' : '<span class="st warn"><span class="dot warn"></span>Behind</span>' }], { pad: true });
  const evTable = (rows) => table(rows, [{ key: "created_at", label: "Time", render: (v) => `<span class="dim">${esc(fmtDate(v))}</span>` }, { key: "source" }, { key: "kind" },
    { key: "payload", label: "Details", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v)).slice(0, 300)}</code>` : "" }], { pad: true, empty: "No events." });

  if (tab === "overview") {
    const today = a.daily.length ? a.daily[a.daily.length - 1] : { n: 0 };
    root.innerHTML = `<div class="card"><div class="facts">
        <div><span>Last seen</span><b>${esc(ago(k.last_seen_at))}</b></div>
        <div><span>Configuration</span><b>${d.config ? `v${esc(d.config.activated_version)}${d.config.activated_version >= d.config_current ? ", current" : `, current is v${esc(d.config_current)}`}` : "never pulled"}</b></div>
        <div><span>Discs</span><b>${num(a.inventory.discs)} in kiosk · ${num(a.out)} out</b></div>
        <div><span>Rentals today</span><b>${num(today.n)}</b></div>
        <div><span>Update Service</span><b>${esc(d.store ? ago(d.store.last_check_in) : "never")}</b></div></div></div>
      <div class="grid-main mt"><div class="card flush"><div class="card-h"><h2>Software</h2><a class="more" href="#/kiosk/${esc(id)}/software">All ${d.repositories.length} →</a></div>${repoTable(d.repositories.filter((r) => r.reported).slice(0, 8)).replace("Nothing here yet.", "This kiosk has not reported any Update Service repository yet.")}</div>
        <div class="card flush"><div class="card-h"><h2>Recent activity</h2><a class="more" href="#/kiosk/${esc(id)}/events">All events →</a></div>
          ${table(d.events.slice(0, 8), [{ key: "created_at", label: "", render: (v) => `<span class="dim">${esc(new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span>` },
            { key: "kind", label: "", render: (v, r) => `${esc(r.source)} · ${esc(v)}` }], { pad: true, empty: "Nothing yet." })}</div></div>`;
  } else if (tab === "software") {
    const v = k.info && k.info.Versions;
    root.innerHTML = `<div class="card flush"><div class="card-h"><h2>Update Service repositories (updatemgrd)</h2><a class="more" href="#/releases">Releases →</a></div>${repoTable(d.repositories)}</div>
      <div class="grid2 mt"><div class="card flush"><div class="card-h"><h2>File sets</h2></div>${table(d.filesets, [{ key: "fileset_id", label: "Set" }, { key: "name" }, { key: "channel" },
        { key: "version" }, { key: "state", render: (s) => s === 0 ? '<span class="st mute">Active</span>' : `<span class="st ${s === 1 ? "warn" : "bad"}"><span class="dot ${s === 1 ? "warn" : "bad"}"></span>${esc(FS_STATE[s] || s)}</span>` },
        { key: "updated_at", label: "Reported", render: (x) => esc(ago(x)) }], { pad: true, empty: "No file-set report." })}</div>
        <div class="card"><div class="card-h"><h2>Versions the Kiosk Client reports</h2></div>${v ? `<div class="kv">${Object.entries(v).map(([n, x]) => `<div>${esc(n)}</div><div>${esc(x)}</div>`).join("")}</div>` : '<p class="muted">No report yet.</p>'}
          <div class="kv mt"><div>Update Client</div><div>${esc(k.ucs_version || "-")}${d.registration && d.registration.last_ping_at ? `, ping ${esc(ago(d.registration.last_ping_at))}` : ""}</div></div></div></div>
      <div class="card flush mt"><div class="card-h"><h2>Deployments for this kiosk</h2></div>${table(d.deployments, [{ key: "id", label: "Deployment" }, { key: "fileset_id", label: "File set" },
        { key: "revision_id", label: "Revision" }, { key: "channel" }, { key: "target_type", label: "Target" }, { key: "created_at", label: "Created" }], { pad: true, empty: "None." })}</div>`;
  } else if (tab === "inventory") {
    const inv = await api(`/tables/inventory?kiosk_id=${id}&limit=500`);
    root.innerHTML = `<div class="card"><div class="facts"><div><span>Discs</span><b>${num(a.inventory.discs)}</b></div><div><span>Titles</span><b>${num(a.inventory.titles)}</b></div>
        <div><span>Rented out from here</span><b>${num(a.out)}</b></div><div><span>Last inventory sync</span><b>${esc(ago(a.inventory.last_sync))}</b></div></div></div>
      <div class="card flush mt">${table(inv.rows, [{ key: "barcode" }, { key: "deck" }, { key: "slot" }, { key: "status" }, { key: "updated_at", label: "Updated" }], { pad: true, empty: "No inventory reported." })}</div>`;
  } else if (tab === "rentals") {
    const r = await api(`/tables/rentals?kiosk_id=${id}&limit=200`);
    r.rows.sort((x, y) => new Date(y.rented_at) - new Date(x.rented_at));
    root.innerHTML = `<div class="card flush">${table(r.rows, [{ key: "rented_at", label: "Rented" }, { key: "barcode" }, { key: "product_id", label: "Title" },
      { key: "product_type", label: "Type" }, { key: "status" }, { key: "returned_at", label: "Returned" }, { key: "return_kiosk_id", label: "Returned to" }], { pad: true, empty: "No rentals yet." })}</div>`;
  } else if (tab === "reservations") {
    root.innerHTML = `<div class="card flush">${table(a.reservations, [{ key: "reference_number", label: "Reference" }, { key: "status" }, { key: "items", label: "Titles", render: (v) => esc((v || []).map((x) => x.TitleID).join(", ")) },
      { key: "created_at", label: "Created" }, { key: "expires_at", label: "Expires" }], { pad: true, empty: "No reservations." })}</div>
      ${a.broker ? `<p class="muted mt">Registered with the reservation broker since ${esc(fmtDate(a.broker.registered_at))}. <a href="#/broker">Send a reservation</a></p>` : ""}`;
  } else if (tab === "commands") {
    root.innerHTML = `<div class="grid-main"><div class="card"><div class="card-h"><h2>Command history</h2></div><div class="console">${a.requests.length ? a.requests.map((r) =>
        `<div><span class="p">›</span> ${esc(r.command)} <span class="d">v${esc(r.version)} · ${esc(fmtDate(r.created_at))}</span></div>` +
        (r.responded_at ? `<div class="o">✓ ${esc(JSON.stringify(r.response)).slice(0, 220)}</div>` : '<div class="x">… no answer</div>')).join("") : '<span class="d">No commands sent yet.</span>'}</div></div>
      <div class="card"><div class="card-h"><h2>Send</h2></div><p class="muted">Pick any Update Client command; the payload is filled in for you.</p>${can() ? '<button class="primary" id="nc">New command...</button>' : ""}</div></div>`;
    if ($("#nc")) $("#nc").onclick = () => commandDialog([id]);
  } else if (tab === "files") {
    root.innerHTML = `<div class="toolbar">${can() ? '<button id="pk">Peek files...</button><button id="up">Upload files...</button>' : ""}</div>
      <div class="card flush">${table(a.uploads, [{ key: "key", label: "Uploaded file", render: (v) => `<a class="link" href="/api/uploads/file?key=${encodeURIComponent(v)}">${esc(v.split("/").pop())}</a>` },
        { key: "size", render: (v) => esc((v / 1024).toFixed(0)) + " KB" }, { key: "modified", label: "When" }], { pad: true, empty: "Nothing uploaded from this kiosk yet." })}</div>`;
    if ($("#pk")) { $("#pk").onclick = () => peekDialog(id); $("#up").onclick = () => uploadDialog(id); }
  } else if (tab === "events") {
    root.innerHTML = `<div class="card flush">${evTable(d.events)}</div><p class="mt"><a href="#/logs?kiosk=${esc(id)}">Search all events of this kiosk →</a></p>`;
  } else if (tab === "settings") {
    const fld = (n, l, v) => `<div class="field"><span>${esc(l)}</span><input name="${n}" value="${esc(v ?? "")}" ${can() ? "" : "disabled"}></div>`;
    root.innerHTML = `<div class="grid2"><form id="kf" class="card"><div class="card-h"><h2>Kiosk</h2></div>
        ${fld("k.market", "Market", k.market)}${fld("k.banner", "Banner", k.banner)}${fld("k.timezone", "Time zone", k.timezone)}${fld("k.state", "State", k.state)}
        <div class="card-h mt"><h2>Location</h2></div>${fld("l.label", "Label", loc.label)}${fld("l.address", "Address", loc.address)}${fld("l.city", "City", loc.city)}
        ${fld("l.state", "State", loc.state)}${fld("l.zip", "ZIP", loc.zip)}${fld("l.latitude", "Latitude", loc.latitude)}${fld("l.longitude", "Longitude", loc.longitude)}
        ${can() ? '<div class="toolbar mt"><button class="primary">Save</button></div>' : ""}</form>
      <div><div class="card"><div class="card-h"><h2>Provisioning</h2></div><p class="muted">Files for setting up or re-pointing this kiosk.</p>
          <div class="row"><button id="rp">Reprovision file...</button><a class="button" href="/api/provisioning/installer/${esc(id)}.json">Installer settings.json</a><a class="button" href="/api/provisioning/ca.crt">Backend CA</a></div></div>
        <div class="card flush mt"><div class="card-h"><h2>IoT certificates</h2></div>${table(a.certificates, [{ key: "certificate_id", label: "Certificate", render: (v) => `<code>${short(v)}</code>` },
          { key: "created_at", label: "Issued" }, { key: "revoked_at", label: "", right: true, render: (v, r) => v ? '<span class="st mute">Revoked</span>'
            : can() ? `<button class="sm danger" data-rev="${esc(r.certificate_id)}">Revoke</button>` : '<span class="st ok">Active</span>' }], { pad: true, empty: "None issued yet." })}</div></div></div>`;
    $("#rp").onclick = () => reprovisionDialog(id, k.market);
    $$("button[data-rev]").forEach((b) => (b.onclick = async () => {
      if (!confirm("Revoke this certificate? The kiosk's MQTT connection is refused until it gets a new one.")) return;
      await act(() => api("/provisioning/certificates/revoke", { body: { certificate_id: b.dataset.rev } }), "Revoked"); route();
    }));
    $("#kf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { kiosk: {}, location: {} };
      for (const [n, v] of new FormData(e.target)) { const [g, f] = n.split("."); body[g === "k" ? "kiosk" : "location"][f] = v; }
      await act(() => api("/kiosks/" + id, { method: "PUT", body }), "Saved");
    });
  }
};

// Settings file for kiosk/reprovision: run `rb-reprovision apply <file>` on the kiosk (elevated).
function reprovisionDialog(id, market) {
  const root = modal(`<h3>Reprovision a kiosk</h3>
    <p class="muted">Downloads the settings file for <code>rb-reprovision apply</code> (kiosk/reprovision in the repository):
    hosts entries, the backend CA, store ID, engine registry, Update Manager / Update Client / Kiosk Client / Device Service URLs.</p>
    <form id="rpf">
      <label>Kiosk (store) ID <input name="kiosk" required value="${esc(id)}" size="8"></label>
      <label>Backend address for the hosts file <input name="address" placeholder="BACKEND_PUBLIC_IP"></label>
      <label>or your own DNS domain <input name="base_domain" placeholder="e.g. kiosk.example.net (no hosts entries)"></label>
      <label>Market <input name="market" value="${esc(market || "")}"></label>
      <label>URL rewrites, one OLD=NEW per line <textarea name="rewrite" rows="3" placeholder="https://old-host=https://dataservice.kiosk.prod.redbox.com"></textarea></label>
      <div class="toolbar mt"><button class="primary">Download</button><button type="button" id="cl">Cancel</button></div></form>`);
  $("#cl", root).onclick = closeModal;
  $("#rpf", root).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const q = new URLSearchParams();
    for (const k of ["address", "base_domain", "market"]) if (f.get(k)) q.append(k, f.get(k));
    for (const r of String(f.get("rewrite") || "").split("\n").map((x) => x.trim()).filter(Boolean)) q.append("rewrite", r);
    location.href = `/api/reprovision/${encodeURIComponent(f.get("kiosk"))}.json?` + q;
  });
}

const FILE_TYPES = ["UpdateClientServiceLogs", "KioskClientServiceLogs", "KioskEngineLogs", "UpdateManagerLogs", "DeviceServiceLogs",
  "HALLogs", "ErrorLogs", "Configuration", "KioskEngineDataDirectory", "Certificates"];
function peekDialog(id) {
  const m = modal(`<h3>Peek files on kiosk ${esc(id)}</h3><label>File type <select id="pt">${FILE_TYPES.map((t) => `<option>${t}</option>`).join("")}</select></label>
    <label>Or any folder path <input id="pp" placeholder="C:\\ProgramData\\Redbox"></label>
    <div class="toolbar mt"><button class="primary" id="go">Peek</button><button id="cl">Close</button></div><div id="out" class="mt"></div>`);
  $("#cl", m).onclick = closeModal;
  $("#go", m).onclick = async () => {
    $("#out", m).textContent = "Waiting for the kiosk...";
    const r = await act(() => api("/jobs/peek", { body: { kiosk_id: id, fileType: $("#pt", m).value, path: $("#pp", m).value || undefined } }));
    $("#out", m).innerHTML = `<pre>${esc(JSON.stringify(r.response, null, 2) || "no answer (is the kiosk connected?)")}</pre>`;
  };
}
function uploadDialog(id) {
  const m = modal(`<h3>Upload files from kiosk ${esc(id)}</h3><label>File type <select id="ut">${FILE_TYPES.map((t) => `<option>${t}</option>`).join("")}</select></label>
    <label>File names (comma separated, from Peek) <input id="un"></label>
    <div class="toolbar mt"><button class="primary" id="go">Upload</button><button id="cl">Close</button></div><div id="out" class="mt"></div>`);
  $("#cl", m).onclick = closeModal;
  $("#go", m).onclick = async () => {
    $("#out", m).textContent = "Waiting for the kiosk...";
    const names = $("#un", m).value.split(",").map((s) => s.trim()).filter(Boolean);
    const r = await act(() => api("/jobs/upload", { body: { kiosk_id: id, fileType: $("#ut", m).value, fileNames: names, zip: true } }));
    $("#out", m).innerHTML = `<pre>${esc(JSON.stringify(r.response, null, 2) || "no answer")}</pre><p>Stored as <code>${esc(r.s3Key)}</code>; it is listed on the kiosk's Files tab.</p>`;
  };
}

// ------------------------------------------------------------------------------ generic table editor
const query = () => Object.fromEntries(new URLSearchParams((location.hash.split("?")[1]) || ""));
async function dataTable(name, opts = {}) {
  const q = Object.assign(query(), opts.query || {});
  const params = new URLSearchParams();
  if (q.kiosk) params.set("kiosk_id", q.kiosk);
  if (q.q) params.set("q", q.q);
  params.set("limit", "200");
  const d = await api(`/tables/${name}?${params}`);
  return d;
}

async function editorView(name, root, opts = {}) {
  const q = query();
  root.innerHTML = `<div class="toolbar"><input id="tq" placeholder="Search" value="${esc(q.q || "")}" class="grow">
    <input id="tk" placeholder="Kiosk id" value="${esc(q.kiosk || "")}" size="9"><button id="tg">Search</button>
    <button class="primary" id="tn">New row</button></div><div id="tt"></div>`;
  const load = async () => {
    const params = new URLSearchParams({ limit: "200" });
    if ($("#tk", root).value) params.set("kiosk_id", $("#tk", root).value);
    if ($("#tq", root).value) params.set("q", $("#tq", root).value);
    const d = await api(`/tables/${name}?${params}`);
    if (opts.setTitle) title(d.title);
    const cols = d.columns.map((c) => ({ key: c.column_name, label: c.column_name }));
    $("#tt", root).innerHTML = `<p class="muted">${esc(d.total)} rows${d.total > d.rows.length ? `, showing ${d.rows.length}` : ""}. Click a row to edit.</p>` +
      table(d.rows, cols, { onclick: true });
    bindRows($("#tt", root), d.rows, (r) => rowDialog(name, d, r, load));
    $("#tn", root).onclick = () => rowDialog(name, d, null, load);
  };
  $("#tg", root).onclick = load;
  $("#tq", root).addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  await load();
}

function rowDialog(name, meta, row, reload) {
  const isNew = !row;
  const fields = meta.columns.map((c) => {
    const k = c.column_name; let v = row ? row[k] : "";
    const ro = c.type === "bytea" || (!isNew && meta.key.includes(k));
    if (v !== null && typeof v === "object") v = JSON.stringify(v, null, 1);
    if (v === null) v = "";
    const hint = `${c.type}${c.nullable ? "" : ", required"}${c.column_default ? ", default " + c.column_default : ""}`;
    const big = ["jsonb", "json", "text"].includes(c.type) && String(v).length > 60 || c.type === "jsonb" || c.type.endsWith("[]");
    const input = c.type === "boolean"
      ? `<select data-k="${esc(k)}" ${ro ? "disabled" : ""}><option value="">(null)</option><option ${v === true || v === "true" ? "selected" : ""}>true</option><option ${v === false || v === "false" ? "selected" : ""}>false</option></select>`
      : big ? `<textarea data-k="${esc(k)}" ${ro ? "readonly" : ""}>${esc(v)}</textarea>` : `<input data-k="${esc(k)}" value="${esc(v)}" ${ro ? "readonly" : ""}>`;
    return `<label>${esc(k)} <span class="muted">(${esc(hint)})</span>${input}</label>`;
  }).join("");
  const m = modal(`<h3>${isNew ? "New row" : "Edit row"} - ${esc(meta.title)}</h3><form id="rf">${fields}
    <div class="toolbar mt"><button class="primary" type="submit">Save</button> <button type="button" id="cl">Cancel</button>
    <span class="grow"></span>${isNew ? "" : '<button type="button" class="danger" id="del">Delete</button>'}</div></form>`);
  $("#cl", m).onclick = closeModal;
  const collect = () => {
    const vals = {};
    $$("[data-k]", m).forEach((el) => {
      const k = el.dataset.k; const c = meta.columns.find((x) => x.column_name === k);
      if (el.readOnly || el.disabled || c.type === "bytea") return;
      const orig = row ? (row[k] !== null && typeof row[k] === "object" ? JSON.stringify(row[k], null, 1) : String(row[k] ?? "")) : "";
      if (isNew ? el.value !== "" : el.value !== orig) vals[k] = el.value === "" ? null : el.value;
    });
    return vals;
  };
  const keyOf = () => Object.fromEntries(meta.key.map((k) => [k, row[k]]));
  $("#rf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const vals = collect();
    if (!Object.keys(vals).length) return closeModal();
    await act(() => isNew ? api(`/tables/${name}`, { body: vals }) : api(`/tables/${name}`, { method: "PUT", body: { key: keyOf(), values: vals } }), "Saved");
    closeModal(); reload();
  });
  if (!isNew) $("#del", m).onclick = async () => {
    if (!confirm("Delete this row?")) return;
    await act(() => api(`/tables/${name}/delete`, { body: keyOf() }), "Deleted"); closeModal(); reload();
  };
}

routes.data = async (name) => { title(""); await editorView(name.split("?")[0], view(), { setTitle: true }); };
routes.tables = async () => {
  title("All tables");
  const list = await api("/tables");
  view().innerHTML = `<p class="muted">Every editable table. Changes are live: kiosks get them on their next call (pricing and campaigns also on the Kiosk Client's refresh schedule; use Remote jobs to refresh now).</p>` +
    table(list, [{ key: "title", label: "Table" }, { key: "table", label: "Database table" }, { key: "key", label: "Key", render: (v) => esc(v.join(", ")) }], { onclick: true });
  bindRows(view(), list, (r) => (location.hash = "#/data/" + r.name));
};

routes.pricing = async () => {
  title("Pricing & offers");
  const tabs = [["pricing_default", "Default rental"], ["pricing_rental", "Rental by price level"], ["pricing_purchase", "Purchase"],
    ["promo_codes", "Promo codes"], ["promo_redemptions", "Redemptions"], ["return_promo", "Return promo"], ["return_promo_issued", "Return codes issued"],
    ["customer_offers", "Customer offers"], ["campaigns", "Campaigns"], ["subscription_plans", "Subscriptions"], ["rbplus_campaigns", "Redbox+ campaigns"], ["ads", "Ads"]];
  view().innerHTML = `<div class="tabs">${tabs.map(([n, l], i) => `<button data-t="${n}" class="${i ? "" : "on"}">${esc(l)}</button>`).join("")}</div>
    <p class="muted">Kiosk Clients refresh pricing every 30 min-26 h and campaigns every 30 min; <a href="#/jobs">Remote jobs</a> can force a refresh.</p><div id="pt"></div>`;
  const show = (n) => { $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === n)); editorView(n, $("#pt")); };
  $$(".tabs button").forEach((b) => (b.onclick = () => show(b.dataset.t)));
  show(tabs[0][0]);
};

// ---------------------------------------------------------------------------------------- catalog
// The kiosk catalog (profile.data + inventory.data): edited here, published as a dvd-data revision.
const CATALOG_TABS = [["titles", "Titles"], ["groups", "Product groups"], ["barcodes", "Barcodes"], ["tables", "Other tables"], ["publish", "Import & publish"]];
let catalogLookups = null;
const csv = (s) => String(s || "").split(",").map((x) => x.trim()).filter(Boolean);
const thumb = (name) => name ? `<img src="/api/catalog/images/${encodeURIComponent(name)}" alt="" loading="lazy" style="height:48px">` : "";

routes.catalog = async (tab = "titles") => {
  if (!catalogLookups) catalogLookups = await api("/catalog/lookups");
  const s = await api("/catalog");
  const titles = (s.tables.find((t) => t.name === "ProductCatalog") || {}).rows || 0;
  const st = s.state || {};
  const dirty = st.changed_at && (!st.stamped_at || st.changed_at > st.stamped_at);
  view().innerHTML = head("Catalog", `${num(titles)} titles · ${num(s.images)} box-art images${dirty ? " · changes not yet published" : ""}`,
    can() ? `<a class="button" href="#/catalog/publish">${ic("upload")}Import</a><a class="button primary" href="#/catalog/publish">${ic("rocket")}Build &amp; publish</a>` : "") +
    `<div class="tabs">${CATALOG_TABS.map(([n, l]) => `<a href="#/catalog/${n}" class="${n === tab ? "on" : ""}">${esc(l)}</a>`).join("")}</div><div id="ct"></div>`;
  await ({ titles: catalogTitles, groups: catalogGroups, barcodes: catalogBarcodes, tables: catalogTables, publish: catalogPublish }[tab] || catalogTitles)($("#ct"));
};

// Catalog helpers: product type -> family (1 movies, 2 games), format pill, placeholder colours.
const typeInfo = (id) => (catalogLookups.types.find((t) => String(t.product_type_id) === String(id)) || { product_type_name: "?", product_family_id: 1 });
const isGameType = (id) => Number(typeInfo(id).product_family_id) === 2;
const ART_COLORS = ["#E42045,#380B44", "#F05C27,#8F0E15", "#792D8D,#060208", "#1D5FB8,#2F1E36", "#D92E6C,#481B54", "#555F64,#060208", "#2E7D5B,#12081A"];
function fmtPill(typeId, soon) {
  if (soon) return '<span class="fmt soon">Coming soon</span>';
  const t = typeInfo(typeId), n = t.product_type_name;
  const cls = isGameType(typeId) ? "game" : /blu|4k/i.test(n) ? "blu" : /digital/i.test(n) ? "digital" : "";
  return `<span class="fmt ${cls}">${esc(n)}</span>`;
}
function artBox(r, big) {
  const soon = r.release_date && r.release_date > new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  const c = ART_COLORS[Number(r.product_id) % ART_COLORS.length];
  // placeholder colours and name underneath; the box art covers them and removes itself if the image is missing
  return `<div class="art ph" style="background:linear-gradient(160deg,${c})">${fmtPill(r.product_type_id, soon)}<span class="nm">${esc(r.long_name)}</span>
    ${r.image_file ? `<img src="/api/catalog/images/${encodeURIComponent(r.image_file)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>`;
}

async function catalogTitles(root) {
  const q = query();
  const st = { q: q.q || "", chip: "all", type: "", offset: 0, sel: null, rows: [] };
  root.innerHTML = `<div class="cat-layout"><div>
      <div class="toolbar"><div class="search grow">${ic("search")}<input id="cq" placeholder="Title, product id or barcode" value="${esc(st.q)}"></div>
        ${can() ? `<button class="primary" id="cn">${ic("plus")}New title</button>` : ""}</div>
      <div class="chips" id="chips"></div><div id="cl"></div></div>
    <div class="card detail" id="cd"><p class="muted">Pick a title to see and edit it.</p></div></div>`;
  const chipDefs = (d) => {
    const games = d.by_type.filter((t) => isGameType(t.product_type_id));
    const movies = d.by_type.filter((t) => !isGameType(t.product_type_id));
    return [["all", "All"], ["newest", "New releases"], ...movies.slice(0, 4).map((t) => ["t" + t.product_type_id, typeInfo(t.product_type_id).product_type_name]),
      ["games", `Games · ${num(games.reduce((a, t) => a + Number(t.n), 0))}`],
      ...(st.chip === "games" || st.chip.startsWith("g") ? games.map((t) => ["g" + t.product_type_id, `${typeInfo(t.product_type_id).product_type_name} · ${num(t.n)}`]) : []),
      ["soon", `Coming soon · ${num(d.counts.soon)}`], ["changed", `Changed since build · ${num(d.counts.changed)}`], ["noart", `No box art · ${num(d.counts.noart)}`]];
  };
  const params = () => {
    const p = { q: $("#cq").value, limit: "60", offset: String(st.offset) };
    const c = st.chip;
    if (c === "newest") p.newest = "true";
    else if (c === "games") p.family = "2";
    else if (c === "soon") p.soon = "true";
    else if (c === "changed") p.changed = "true";
    else if (c === "noart") p.noart = "true";
    else if (/^[tg]\d+$/.test(c)) p.types = c.slice(1);
    return new URLSearchParams(p);
  };
  const load = async (more) => {
    if (!more) st.offset = 0;
    const d = await api("/catalog/browse?" + params());
    st.rows = more ? st.rows.concat(d.rows) : d.rows;
    $("#chips").innerHTML = chipDefs(d).map(([k, l]) => `<button class="chip ${st.chip === k || (k === "games" && st.chip.startsWith("g")) ? "on" : ""}" data-c="${k}">${esc(l)}</button>`).join("");
    $$("#chips .chip").forEach((b) => (b.onclick = () => { st.chip = b.dataset.c === st.chip && b.dataset.c !== "all" ? "all" : b.dataset.c; load(); }));
    $("#cl").innerHTML = st.rows.length ? `<div class="posters">${st.rows.map((r, i) => `<div class="poster ${st.sel === r.product_id ? "sel" : ""}" data-i="${i}">${artBox(r)}
        <div class="i"><b>${esc(r.long_name)}</b>${esc(ratingName(r.rating_id))}${r.release_date ? " · " + esc(r.release_date.slice(0, 4)) : ""} · ${r.discs ? num(r.discs) + " in kiosks" : "not stocked"}${r.changed ? ' · <span style="color:#FFC2CE">changed</span>' : ""}</div></div>`).join("")}</div>
      ${d.total > st.rows.length ? `<div class="toolbar mt"><button id="more">Show more (${num(d.total - st.rows.length)} left)</button></div>` : ""}`
      : `<p class="muted">${d.counts.total ? "No title matches." : "No catalog yet. Import one under Import & publish."}</p>`;
    $$(".poster", $("#cl")).forEach((p) => (p.onclick = () => { st.sel = st.rows[+p.dataset.i].product_id; $$(".poster.sel").forEach((x) => x.classList.remove("sel")); p.classList.add("sel"); detail(st.sel); }));
    if ($("#more")) $("#more").onclick = () => { st.offset = st.rows.length; load(true); };
  };
  const detail = async (pid) => {
    const box = $("#cd");
    const d = await api("/catalog/titles/" + pid);
    const v = d.value || {};
    const game = isGameType(v.product_type_id);
    const L = catalogLookups;
    const fam = game ? 2 : 1;
    const ratings = L.ratings.filter((r) => Number(r.product_family_id) === fam);
    const famRow = L.families.find((f) => Number(f.product_family_id) === fam);
    const allowed = new Set(Object.keys((famRow && famRow.genres) || {}));
    const genres = L.genres.filter((g) => !allowed.size || allowed.has(String(g.genre_id)));
    const on = new Set(Object.keys(v.genres || {}).filter((k) => v.genres[k]));
    const ro = can() ? "" : "disabled";
    const f = (k, l, val, ph = "") => `<div class="field"><span>${esc(l)}</span><input data-f="${k}" value="${esc(val ?? "")}" placeholder="${esc(ph)}" ${ro}></div>`;
    box.innerHTML = `${artBox({ ...v, product_id: pid }, true)}
      <div class="row mt"><h2 style="font-size:17px">${esc(v.long_name)}</h2><span class="grow"></span>${st.rows.find((r) => r.product_id === pid && r.changed) ? '<span class="tag red">changed</span>' : ""}</div>
      <div class="muted" style="font-weight:600;margin:4px 0 10px">Product ${esc(pid)} · ${num(d.groups.length)} groups · ${num(d.barcode_count)} barcodes</div>
      <form id="qf">${f("long_name", "Title", v.long_name)}${f("sort_name", "Sort name", v.sort_name)}${f("release_date", "Release date", v.release_date, "yyyyMMdd")}
        <div class="field"><span>${game ? "Platform" : "Format"}</span><select data-f="product_type_id" ${ro}>${L.types.filter((t) => Number(t.product_family_id) === fam || (!game && Number(t.product_family_id) === 3))
          .map((t) => `<option value="${esc(t.product_type_id)}" ${String(t.product_type_id) === String(v.product_type_id) ? "selected" : ""}>${esc(t.product_type_name)}</option>`).join("")}</select></div>
        <div class="field"><span>Rating</span><select data-f="rating_id" ${ro}>${ratings.map((r) => `<option value="${esc(r.rating_id)}" ${String(r.rating_id) === String(v.rating_id) ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></div>
        ${game ? `<div class="field"><span>Accessory</span><select data-f="sub_platform_id" ${ro}><option value="">none</option>${L.subplatforms.map((s) => `<option value="${esc(s.subplatform_id)}" ${String(s.subplatform_id) === String(v.sub_platform_id) ? "selected" : ""}>${esc(s.subplatform_name)}</option>`).join("")}</select></div>
          ${f("number_of_players_text", "Players", v.number_of_players_text, "e.g. 1-4 players")}` : `${f("running_time", "Running time", v.running_time, "h:mm")}`}
        ${f("studio", game ? "Publisher" : "Studio", v.studio)}
        <div class="field" style="align-items:start"><span>Genres</span><div class="genres">${genres.map((g) => `<label><input type="checkbox" data-g="${esc(g.genre_id)}" ${on.has(String(g.genre_id)) ? "checked" : ""} ${ro}><span>${esc(g.genre_name)}</span></label>`).join("")}</div></div>
        <div class="row mt"><button type="button" class="sm" id="full">Full editor</button><span class="grow"></span>${can() ? `<button class="sm primary">${ic("check")}Save</button>` : ""}</div></form>`;
    $("#full", box).onclick = () => titleDialog(pid, () => { load(); detail(pid); });
    $("#qf", box).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fields = {};
      $$("[data-f]", box).forEach((el) => {
        const k = el.dataset.f; let val = el.value;
        if (["product_type_id", "rating_id"].includes(k)) val = Number(val);
        else if (k === "sub_platform_id") { if (val === "" && v.sub_platform_id == null) return; val = val === "" ? null : Number(val); }
        fields[k] = val;
      });
      fields.genres = $$("[data-g]", box).filter((c) => c.checked).map((c) => +c.dataset.g);
      await act(() => api("/catalog/titles", { body: { product_id: pid, fields } }), "Saved; publish the catalog to send it to kiosks");
      load();
    });
  };
  $("#cq").addEventListener("keydown", (e) => e.key === "Enter" && load());
  if ($("#cn")) $("#cn").onclick = () => titleDialog(null, load);
  await load();
}
const ratingName = (id) => { const r = catalogLookups.ratings.find((x) => String(x.rating_id) === String(id)); return r ? r.name : ""; };

const TITLE_FIELDS = [["long_name", "Title"], ["sort_name", "Sort name"], ["release_date", "Release date (yyyyMMdd or yyyyMMddHHmmss)"],
  ["national_street_date", "Street date"], ["running_time", "Running time (h:mm)"], ["studio", "Studio"], ["stars", "Stars"],
  ["box_office_gross", "Box office (million $)"], ["coming_soon_days", "Coming soon days"], ["sellthru_price", "Sell-thru price"]];

async function titleDialog(pid, reload) {
  const d = pid ? await api("/catalog/titles/" + pid) : { value: {}, groups: [], barcodes: [], barcode_count: 0 };
  const v = d.value || {};
  const L = catalogLookups;
  const genres = new Set(Object.keys(v.genres || {}).filter((k) => v.genres[k]));
  const opt = (list, id, name, cur) => list.map((x) => `<option value="${esc(x[id])}" ${String(x[id]) === String(cur) ? "selected" : ""}>${esc(x[id])} ${esc(x[name])}</option>`).join("");
  const m = modal(`<h3>${pid ? "Title " + esc(pid) : "New title"}</h3>
    ${pid ? "" : `<form id="om" class="card inline"><b>OMDb</b><input name="title" placeholder="Title" required><input name="year" placeholder="Year" size="5"><input name="imdb_id" placeholder="or IMDb id" size="10">
      <button>Look up</button><span class="muted">${(await api("/catalog")).omdb ? "fills the form below" : "set OMDB_API_KEY to use this"}</span></form>`}
    <form id="tf" class="mt"><div class="grid2"><div>
      ${TITLE_FIELDS.map(([k, l]) => `<label>${esc(l)} <input data-f="${k}" value="${esc(v[k] ?? "")}"></label>`).join("")}
      <label>Product type <select data-f="product_type_id">${opt(L.types, "product_type_id", "product_type_name", v.product_type_id ?? 1)}</select></label>
      <label>Rating <select data-f="rating_id">${opt(L.ratings, "rating_id", "name", v.rating_id ?? 10)}</select></label>
      <label>Game accessory (games only) <select data-f="sub_platform_id"><option value="">none</option>${opt(L.subplatforms, "subplatform_id", "subplatform_name", v.sub_platform_id)}</select></label>
      <label>Players (games only) <input data-f="number_of_players_text" value="${esc(v.number_of_players_text || "")}" placeholder="e.g. 1-4 players"></label>
      <label>Starring (comma separated) <input data-f="starring" value="${esc((v.starring || []).join(", "))}"></label>
      <label>Directors (comma separated) <input data-f="directors" value="${esc((v.directors || []).join(", "))}"></label>
      <label><input type="checkbox" data-f="closed_captioned" ${v.closed_captioned ? "checked" : ""}> closed captioned</label>
      <label><input type="checkbox" data-f="sell_thru" ${v.sell_thru ? "checked" : ""}> sell-thru</label>
    </div><div>
      <div id="art">${thumb(v.image_file).replace('height:48px', 'height:180px')}</div><p class="muted">${esc(v.image_file || "no box art")}</p>
      <label>Description <textarea data-f="description" rows="6">${esc(v.description || "")}</textarea></label>
      <b>Genres</b><div class="genres">${L.genres.map((g) => `<label class="inline"><input type="checkbox" data-g="${esc(g.genre_id)}" ${genres.has(String(g.genre_id)) ? "checked" : ""}> ${esc(g.genre_name)}</label>`).join(" ")}</div>
    </div></div>
    <div class="toolbar mt"><button class="primary" type="submit">${pid ? "Save" : "Create"}</button><button type="button" id="cl">Close</button>
      <span class="grow"></span>${pid ? '<button type="button" class="danger" id="del">Delete title</button>' : ""}</div></form>
    ${pid ? `<div class="grid2 mt"><form id="imf" class="card"><b>Box art</b><p class="muted">JPEG/PNG; stored in the Cache table under its SHA-1.</p>
        <label>File <input type="file" name="file" accept="image/*"></label><label>or URL <input name="url"></label><button>Set box art</button></form>
      <div class="card"><b>Product groups</b>${table(d.groups, [{ key: "product_group_id", label: "Group" }, { key: "value", label: "Name / type / titles", wrap: true,
        render: (g) => esc(`${g.product_group_name} - type ${g.product_group_type_id} - ${(g.product_id_list || []).join(", ")}`) }], { onclick: true, empty: "In no group." })}
        <form id="rg" class="inline mt"><input name="other" placeholder="Product id of the other edition" size="14"><button>Roll up with it</button></form></div></div>
      <div class="card mt"><b>Barcodes (${esc(d.barcode_count)})</b>${table(d.barcodes, [{ key: "barcode" }, { key: "status" }, { key: "rental_count", label: "Rentals" }, { key: "source" },
        { key: "barcode", label: "", render: (b) => `<button type="button" data-delb="${esc(b)}">Delete</button>` }], { empty: "No barcodes." })}
        <form id="bf" class="inline mt"><input name="barcode" placeholder="Barcode (digits)" required><button>Add barcode</button></form></div>
      <details class="mt"><summary>Stored Lua value</summary><textarea id="lua" rows="8" style="width:100%">${esc(d.lua)}</textarea><button type="button" id="savelua">Save Lua</button></details>` : ""}`);
  $("#cl", m).onclick = closeModal;
  const done = async (msg, again) => { toast(msg); closeModal(); reload && reload(); if (again) titleDialog(again, reload); };
  const fill = (fields) => {
    for (const [k, val] of Object.entries(fields)) {
      const el = $(`[data-f="${k}"]`, m);
      if (el && el.type === "checkbox") el.checked = !!val; else if (el) el.value = Array.isArray(val) ? val.join(", ") : (val ?? "");
    }
    if (fields.genres) $$("[data-g]", m).forEach((c) => (c.checked = fields.genres.map(String).includes(c.dataset.g)));
  };
  let poster = null;
  if ($("#om", m)) $("#om", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await act(() => api("/catalog/omdb", { body: Object.fromEntries(new FormData(e.target)) }), (x) => `OMDb: ${x.omdb.Title} (${x.omdb.Year})`);
    fill(r.fields); poster = r.poster;
    if (poster) $("#art", m).innerHTML = `<img src="${esc(poster)}" alt="" style="height:180px"><p class="muted">poster is stored on Create</p>`;
  });
  $("#tf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const fields = {};
    $$("[data-f]", m).forEach((el) => {
      const k = el.dataset.f;
      let val = el.type === "checkbox" ? el.checked : el.value;
      if (["starring", "directors"].includes(k)) val = csv(val);
      else if (k === "sub_platform_id") { if (val === "" && v.sub_platform_id == null) return; val = val === "" ? null : Number(val); }
      else if (["stars", "coming_soon_days", "product_type_id", "rating_id", "box_office_gross"].includes(k)) val = val === "" ? 0 : Number(val);
      else if (k === "sellthru_price") val = val === "" ? 0 : Number(val);
      if (pid || val !== "") fields[k] = val;
    });
    fields.genres = $$("[data-g]", m).filter((c) => c.checked).map((c) => +c.dataset.g);
    const r = await act(() => api("/catalog/titles", { body: { product_id: pid, fields } }));
    if (!pid && poster) {
      const f = new FormData(); f.append("url", poster);
      await act(() => api(`/catalog/titles/${r.product_id}/image`, { form: f }));
    }
    done(pid ? "Saved" : `Created title ${r.product_id}`, pid ? null : r.product_id);
  });
  if (!pid) return;
  $("#del", m).onclick = async () => {
    const withBarcodes = d.barcode_count && confirm(`Also delete its ${d.barcode_count} barcodes? (Cancel keeps them)`);
    if (!confirm(`Delete title ${pid}?`)) return;
    await act(() => api(`/catalog/titles/${pid}/delete`, { body: { barcodes: !!withBarcodes } }));
    done("Deleted");
  };
  $("#imf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    await act(() => api(`/catalog/titles/${pid}/image`, { form: new FormData(e.target) }), (x) => `Box art ${x.image_file}`);
    done("Box art set", pid);
  });
  $("#rg", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const other = +new FormData(e.target).get("other");
    const rollup = d.groups.find((g) => g.value.product_group_type_id === 1);
    const theirs = rollup ? null : (await api(`/catalog/groups?product_id=${other}`)).find((g) => g.value.product_group_type_id === 1);
    const g = rollup || theirs;
    const ids = g ? [...new Set([...g.value.product_id_list, pid, other])] : [other, pid];
    await act(() => api("/catalog/groups", { body: { product_group_id: g ? g.product_group_id : null, name: g ? g.value.product_group_name : v.long_name, type: 1, product_ids: ids } }));
    done("Rolled up", pid);
  });
  bindRows(m, d.groups, (g) => groupDialog(g, () => titleDialog(pid, reload)));
  $("#bf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    await act(() => api("/catalog/barcodes", { body: { barcode: new FormData(e.target).get("barcode"), product_id: pid } }), "Barcode added");
    done("Barcode added", pid);
  });
  $$("button[data-delb]", m).forEach((b) => (b.onclick = async () => {
    await act(() => api("/catalog/barcodes/delete", { body: { barcode: b.dataset.delb } }), "Barcode deleted"); done("Barcode deleted", pid);
  }));
  $("#savelua", m).onclick = async () => { await act(() => api("/catalog/titles", { body: { product_id: pid, lua: $("#lua", m).value } }), "Saved"); done("Saved", pid); };
}

function groupDialog(g, reload) {
  const v = g ? g.value : { product_group_type_id: 1, product_id_list: [] };
  const m = modal(`<h3>${g ? "Product group " + esc(g.product_group_id) : "New product group"}</h3><form id="gf">
    <label>Name <input name="name" value="${esc(v.product_group_name || "")}" required></label>
    <label>Type <select name="type">${[[1, "title rollup (DVD / Blu-ray / 4K of one title)"], [3, "multi-disc"], [6, "multi-disc workaround"]].map(([t, l]) => `<option value="${t}" ${t === v.product_group_type_id ? "selected" : ""}>${t} ${l}</option>`).join("")}</select></label>
    <label>Product ids (comma separated) <input name="ids" value="${esc((v.product_id_list || []).join(", "))}" required></label>
    <div class="toolbar mt"><button class="primary">Save</button><button type="button" id="cl">Cancel</button><span class="grow"></span>${g ? '<button type="button" class="danger" id="del">Delete group</button>' : ""}</div></form>`);
  $("#cl", m).onclick = closeModal;
  $("#gf", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    await act(() => api("/catalog/groups", { body: { product_group_id: g ? g.product_group_id : null, name: f.get("name"), type: +f.get("type"), product_ids: csv(f.get("ids")).map(Number) } }), "Saved");
    closeModal(); reload();
  });
  if (g) $("#del", m).onclick = async () => {
    if (!confirm("Delete this product group?")) return;
    await act(() => api(`/catalog/groups/${g.product_group_id}/delete`, { body: {} }), "Deleted"); closeModal(); reload();
  };
}

async function catalogGroups(root) {
  root.innerHTML = `<div class="toolbar"><input id="gq" class="grow" placeholder="Group name or id"><button id="gg">Search</button><button class="primary" id="gn">New group</button></div><div id="gl"></div>`;
  const load = async () => {
    const rows = await api("/catalog/groups?" + new URLSearchParams({ q: $("#gq").value }));
    $("#gl").innerHTML = table(rows, [{ key: "product_group_id", label: "Group" }, { key: "value", label: "Name", render: (v) => esc(v.product_group_name) },
      { key: "value", label: "Type", render: (v) => esc(v.product_group_type_id) }, { key: "value", label: "Titles", wrap: true, render: (v) => esc((v.product_id_list || []).join(", ")) }], { onclick: true });
    bindRows($("#gl"), rows, (g) => groupDialog(g, load));
  };
  $("#gg").onclick = load; $("#gq").addEventListener("keydown", (e) => e.key === "Enter" && load());
  $("#gn").onclick = () => groupDialog(null, load);
  await load();
}

async function catalogBarcodes(root) {
  root.innerHTML = `<form id="ba" class="card inline"><b>Add or change</b><input name="barcode" placeholder="Barcode (digits)" required><input name="product_id" placeholder="Product id" required size="10">
      <select name="status"><option value="0">0 Known</option><option value="1">1 WrongTitle</option><option value="2">2 Damaged</option><option value="3">3 Thinned</option><option value="6">6 Fraud</option></select><button class="primary">Save</button></form>
    <div class="toolbar mt"><input id="bq" class="grow" placeholder="Barcode (shows it and the ones after it)"><button id="bg">Look up</button></div>
    <p class="muted">Without a search: barcodes added here or learned from kiosk transactions, newest first. Imported barcodes are listed per title.</p><div id="bl"></div>`;
  const load = async () => {
    const rows = await api("/catalog/barcodes?" + new URLSearchParams({ q: $("#bq").value, limit: "100" }));
    $("#bl").innerHTML = table(rows, [{ key: "barcode" }, { key: "product_id", label: "Product id" }, { key: "status" }, { key: "rental_count", label: "Rentals" }, { key: "source" }, { key: "updated_at", label: "Updated" },
      { key: "barcode", label: "", render: (b) => `<button data-delb="${esc(b)}">Delete</button>` }]);
    $$("button[data-delb]", root).forEach((b) => (b.onclick = async () => { await act(() => api("/catalog/barcodes/delete", { body: { barcode: b.dataset.delb } }), "Deleted"); load(); }));
  };
  $("#ba").addEventListener("submit", async (e) => { e.preventDefault(); await act(() => api("/catalog/barcodes", { body: Object.fromEntries(new FormData(e.target)) }), "Saved"); load(); });
  $("#bg").onclick = load;
  await load();
}

async function catalogTables(root) {
  const st = await api("/catalog");
  const names = st.tables.map((t) => t.name).filter((n) => n !== "Cache");
  root.innerHTML = `<div class="toolbar"><select id="tt">${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select><input id="tq" class="grow" placeholder="Key or text">
    <button id="tg">Show</button><button id="tnew">New row</button></div><p class="muted">Rows are Lua table literals, as the engine reads them.</p><div id="tr"></div>`;
  const edit = (tbl, row, load) => {
    const m = modal(`<h3>${esc(tbl)} ${row ? esc(row.key) : "(new row)"}</h3>${row ? "" : '<label>Key <input id="k"></label>'}
      <textarea id="lv" rows="10" style="width:100%">${esc(row ? row.lua : "{  }")}</textarea>
      <div class="toolbar mt"><button class="primary" id="sv">Save</button><button id="cl">Cancel</button><span class="grow"></span>${row ? '<button class="danger" id="dl">Delete row</button>' : ""}</div>`);
    $("#cl", m).onclick = closeModal;
    $("#sv", m).onclick = async () => {
      const key = row ? row.key : $("#k", m).value;
      await act(() => api(`/catalog/rows/${encodeURIComponent(tbl)}/${encodeURIComponent(key)}`, { method: "PUT", body: { lua: $("#lv", m).value } }), "Saved"); closeModal(); load();
    };
    if (row) $("#dl", m).onclick = async () => { if (!confirm("Delete this row?")) return; await act(() => api(`/catalog/rows/${encodeURIComponent(tbl)}/${encodeURIComponent(row.key)}/delete`, { body: {} }), "Deleted"); closeModal(); load(); };
  };
  const load = async () => {
    const tbl = $("#tt").value;
    const d = await api(`/catalog/rows/${encodeURIComponent(tbl)}?` + new URLSearchParams({ q: $("#tq").value, limit: "200" }));
    $("#tr").innerHTML = `<p class="muted">${esc(d.total)} rows</p>` + table(d.rows, [{ key: "key" }, { key: "lua", label: "Value", wrap: true, render: (v) => `<code>${esc(v.slice(0, 300))}</code>` }], { onclick: true });
    bindRows($("#tr"), d.rows, (r) => edit(tbl, r, load));
  };
  $("#tg").onclick = load; $("#tt").onchange = load; $("#tnew").onclick = () => edit($("#tt").value, null, load);
  if (names.length) await load();
}

async function catalogPublish(root) {
  const d = await api("/catalog");
  const s = d.state || {};
  const changed = s.changed_at && (!s.stamped_at || s.changed_at > s.stamped_at);
  root.innerHTML = `<div class="grid2"><div class="card"><b>Catalog</b>
      <p>${s.imported_at ? `Imported from <code>${esc(s.source)}</code> ${esc(fmtDate(s.imported_at))}.` : "Nothing imported yet."}
      ${s.changed_at ? ` Last change ${esc(fmtDate(s.changed_at))}${changed ? " (not built since)" : ""}.` : ""}</p>
      ${table(d.tables, [{ key: "name", label: "profile.data table" }, { key: "rows" }])}
      <p class="muted">${esc(d.images)} images; barcodes: ${d.barcodes.map((b) => `${esc(b.n)} ${esc(b.source)}`).join(", ") || "none"}.</p></div>
    <div><form id="pf" class="card"><b>Publish to kiosks</b><p class="muted">Builds profile.data and inventory.data and adds them to the
      <code>dvd-data</code> repository (head ${short(d.repo && d.repo.head)}) as VCDIFF deltas. Kiosks get it on their next Update Service poll.</p>
      <label>Kiosks <select name="scope"><option value="all">every kiosk</option><option value="market">one market</option><option value="store">one kiosk</option></select></label>
      <label>Market / kiosk <input name="target" list="mkts" placeholder="market name or kiosk id"><datalist id="mkts">${d.markets.map((x) => `<option>${esc(x)}</option>`).join("")}</datalist></label>
      <label>Label <input name="label" placeholder="catalog-yyyymmdd-hhmm"></label><button class="primary">Build and publish</button>
      ${s.last_build ? `<p class="muted">Last: ${esc(s.last_build.revision ? s.last_build.revision.slice(0, 10) + " " + (s.last_build.label || "") : "no change")} for ${esc(s.last_build.scope)} ${esc(s.last_build.target || "")} (${esc(fmtDate(s.last_build.at))})</p>` : ""}</form>
    <form id="if" class="card mt"><b>Import</b><p class="muted">Replaces the catalog (titles, tables, box art and imported barcodes;
      barcodes added here or learned from kiosks are kept). About 3 minutes for a full kiosk catalog.</p>
      <p><b>Normally leave both fields empty:</b> the catalog is imported from the dvd-data served to kiosks, which is the
      one the seed zips you imported on <a href="#/packages">Update packages</a> brought in. Do not enter a seed zip here.</p>
      <label>dvd-data revision (optional) <input name="revision" placeholder="empty = the served head; or a hash / label"></label>
      <label>or a folder (optional, advanced) inside the rb-admin container holding loose profile.data and inventory.data files
        <input name="folder" placeholder="empty; e.g. /packages/catalog - not a .zip"></label>
      <button>Import</button></form></div></div>
    <h3>Catalog jobs</h3><div id="cj"></div>`;
  const drawJobs = (jobs) => {
    $("#cj").innerHTML = table(jobs, [{ key: "id" }, { key: "source", wrap: true }, { key: "status", render: (v) => pill(v, v === "done" ? "ok" : v === "failed" ? "bad" : "warn") },
      { key: "progress", label: "Last step", wrap: true, render: (v) => esc(v.length ? v[v.length - 1].m : "") }, { key: "started_at", label: "Started" }, { key: "finished_at", label: "Finished" }], { empty: "No catalog jobs yet." });
  };
  drawJobs(d.jobs);
  const poll = async () => {
    clearTimeout(catalogPublish.timer);
    if (!location.hash.startsWith("#/catalog/publish")) return;
    const p = await api("/catalog");
    drawJobs(p.jobs);
    if (p.jobs.some((j) => j.status === "running")) catalogPublish.timer = setTimeout(poll, 3000);
  };
  $("#pf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (f.scope !== "all" && !f.target) return toast("Name the market or kiosk", true);
    if (!confirm(f.scope === "all" ? "Publish the catalog to every kiosk?" : `Publish the catalog to ${f.scope} ${f.target}?`)) return;
    await act(() => api("/catalog/publish", { body: f }), (x) => `Publishing (job ${x.job})`); poll();
  });
  $("#if").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!confirm("Replace the catalog with this import? Unpublished edits are lost.")) return;
    await act(() => api("/catalog/import", { body: Object.fromEntries(new FormData(e.target)) }), (x) => `Importing (job ${x.job})`); poll();
  });
  if (d.jobs.some((j) => j.status === "running")) poll();
}

// --------------------------------------------------------------------------------- rentals & returns
// What kiosks rented out and took back. No money: the kiosks cannot take real payments (PAYMENT_MODE approves).
routes.rentals = async (tab = "rentals") => {
  const q = query();
  view().innerHTML = head("Rentals & returns", "Discs rented, bought and returned at the kiosks") + `
    <div class="tabs"><a href="#/rentals/rentals" class="${tab === "rentals" ? "on" : ""}">Rentals</a><a href="#/rentals/returns" class="${tab === "returns" ? "on" : ""}">Returns</a></div>
    <div class="toolbar"><div class="search grow">${ic("search")}<input id="rq" placeholder="Barcode or title" value="${esc(q.q || "")}"></div>
      <input id="rk" placeholder="Kiosk" size="8" value="${esc(q.kiosk || "")}">
      ${tab === "rentals" ? '<select id="rs"><option value="">any status</option><option>out</option><option>returned</option><option>purchased</option><option>not_vended</option></select>' : ""}</div>
    <div class="card flush" id="rt"></div>`;
  const load = async () => {
    const p = new URLSearchParams({ kind: tab, q: $("#rq").value, kiosk_id: $("#rk").value, status: $("#rs") ? $("#rs").value : "" });
    const d = await api("/rentals?" + p);
    const titleCell = (v, r) => r.title ? `${esc(r.title)} <span class="dim">${esc(r.type || "")}</span>` : `<span class="dim">product ${esc(v ?? "?")}</span>`;
    $("#rt").innerHTML = `<p class="muted" style="padding:14px 16px 0">${num(d.total)} ${tab}</p>` + (tab === "rentals" ? table(d.rows, [
      { key: "rented_at", label: "Rented", render: (v) => `<span class="dim">${esc(fmtDate(v))}</span>` }, { key: "kiosk_id", label: "Kiosk", render: (v) => `<a class="link" href="#/kiosk/${esc(v)}">${esc(v)}</a>` },
      { key: "product_id", label: "Title", render: titleCell }, { key: "barcode" },
      { key: "status", render: (v) => v === "out" ? '<span class="st warn"><span class="dot warn"></span>Out</span>' : `<span class="st mute">${esc(v)}</span>` },
      { key: "returned_at", label: "Returned", render: (v, r) => v ? `${esc(ago(v))}${r.return_kiosk_id && r.return_kiosk_id !== r.kiosk_id ? ` to ${esc(r.return_kiosk_id)}` : ""}` : "" }], { pad: true, empty: "No rentals yet." })
      : table(d.rows, [{ key: "return_date", label: "Returned", render: (v, r) => `<span class="dim">${esc(fmtDate(v || r.created_at))}</span>` },
        { key: "kiosk_id", label: "Kiosk", render: (v) => `<a class="link" href="#/kiosk/${esc(v)}">${esc(v)}</a>` }, { key: "product_id", label: "Title", render: titleCell },
        { key: "barcode" }, { key: "deck", render: (v, r) => v != null ? `deck ${esc(v)}, slot ${esc(r.slot)}` : "" }], { pad: true, empty: "No returns yet." }));
  };
  $("#rq").addEventListener("keydown", (e) => e.key === "Enter" && load()); $("#rk").addEventListener("change", load);
  if ($("#rs")) $("#rs").onchange = load;
  await load();
};
routes.transactions = () => routes.rentals();

// ---------------------------------------------------------------------------------------- updates
routes.updates = async () => {
  title("Update Service (updatemgrd)");
  const [rp, st] = await Promise.all([api("/updates/repos"), api("/updates/stores")]);
  view().innerHTML = `<h3>Repositories</h3><p class="muted">Kiosks poll every 15 minutes (<code>u p</code>); a kiosk without a repository gets a full snapshot, one at an older revision gets the later revisions.</p>
    ${table(rp, [{ key: "name", label: "Repository" }, { key: "revisions", label: "Revisions" }, { key: "head", label: "Head", render: short }, { key: "label", label: "Label" },
      { key: "serve_head", label: "Served", render: (v) => v ? pill("pinned " + v.slice(0, 10), "warn") : "newest" }, { key: "canonical_edition", label: "From edition" },
      { key: "head_created", label: "Head added" }, { key: "snapshot_files", label: "Snapshot files" },
      { key: "stores_current", label: "Stores current", render: (v, r) => `${esc(v)} / ${esc(r.stores_reporting)}` }, { key: "auto_subscribe", label: "Auto-subscribe" }], { onclick: true })}
    <h3>Stores and repository assignment</h3><p class="muted">Click a cell to link / unlink / reset to automatic. "auto" means every auto-subscribed repository unless the store has explicit links.</p>
    <div id="matrix"></div>`;
  bindRows(view(), rp, (r) => repoDialog(r));
  const repos = st.repositories;
  const cls = { current: "ok", behind: "warn", missing: "bad", "not subscribed": "" };
  $("#matrix").innerHTML = st.stores.length ? `<div class="table-wrap"><table><thead><tr><th>Store</th><th>Check-in</th>${repos.map((r) => `<th>${esc(r)}</th>`).join("")}</tr></thead><tbody>` +
    st.stores.map((s) => `<tr><td><a href="#/kiosk/${esc(s.store.number)}">${esc(s.store.number)}</a></td><td>${esc(ago(s.store.last_check_in))}</td>` +
      repos.map((r) => { const c = s.repos[r]; return `<td><button class="cellbtn" data-s="${esc(s.store.number)}" data-r="${esc(r)}" title="${esc(c.reported || "")}">${pill(c.state, cls[c.state])} <span class="muted">${esc(c.link)}</span></button></td>`; }).join("") + "</tr>").join("") +
    "</tbody></table></div>" : '<p class="muted">No store has polled yet.</p>';
  $$(".cellbtn").forEach((b) => (b.onclick = () => {
    const m = modal(`<h3>${esc(b.dataset.r)} for store ${esc(b.dataset.s)}</h3><div class="toolbar">
      <button data-m="linked" class="primary">Link</button><button data-m="unlinked">Unlink</button><button data-m="auto">Automatic</button><button id="cl">Cancel</button></div>`);
    $("#cl", m).onclick = closeModal;
    $$("button[data-m]", m).forEach((x) => (x.onclick = async () => {
      await act(() => api("/updates/links", { body: { number: b.dataset.s, repo: b.dataset.r, mode: x.dataset.m } }), "Saved; applies on the store's next poll");
      closeModal(); routes.updates();
    }));
  }));
};
async function repoDialog(r) {
  const d = await api("/updates/repos/" + encodeURIComponent(r.name));
  const m = modal(`<h3>${esc(r.name)}</h3>
    <form id="pub" class="card"><b>Publish a new revision</b><p class="muted">Unchanged files are skipped; changed ones go out as VCDIFF deltas when smaller. Sub-folders are kept when you pick a folder.
        A revision published here is served to every kiosk at once; to publish a build without serving it and roll it out in stages, use <a href="#/releases/${encodeURIComponent(r.name)}">Releases</a>.</p>
      <label>Target folder on the kiosk <input name="target_path" required value="${esc(d.files[0] ? d.files[0].target_path : "C:\\Program Files\\Redbox\\REDS\\")}"></label>
      <label>Label <input name="label" placeholder="${esc(r.name)}-release"></label>
      <label>Files <input type="file" name="files" multiple required></label>
      <label><input type="checkbox" id="folder"> pick a whole folder</label>
      <button class="primary">Publish</button></form>
    <form id="pin" class="card mt inline"><label>Serve to kiosks <select name="revision"><option value="">newest revision</option>
      ${d.revisions.map((v) => `<option value="${esc(v.hash)}" ${v.hash === r.serve_head ? "selected" : ""}>${esc(v.seq)} ${esc(v.hash.slice(0, 10))} ${esc(v.label || "")}</option>`).join("")}</select></label>
      <button class="primary">Apply</button><span class="muted">Kiosks below it are updated up to it, kiosks without the repository get its snapshot, kiosks past it are left alone.</span></form>
    <div class="toolbar mt"><button id="snap">Rebuild snapshot</button><label><input type="checkbox" id="auto" ${r.auto_subscribe ? "checked" : ""}> offer to every store automatically</label><span class="grow"></span><button id="cl">Close</button></div>
    <h3>Latest revisions</h3>${table(d.revisions, [{ key: "seq" }, { key: "hash", render: short }, { key: "label" }, { key: "changes" }, { key: "seeds" }, { key: "deltas" }, { key: "created_at", label: "Added" }])}
    <h3>Files at head (${d.files.length})</h3>${table(d.files.slice(0, 200), [{ key: "target_path", label: "Folder" }, { key: "target_name", label: "File" }, { key: "version_hash", label: "SHA-1", render: short }])}`);
  $("#cl", m).onclick = closeModal;
  $("#folder", m).onchange = (e) => { $("input[type=file]", m).webkitdirectory = e.target.checked; };
  $("#auto", m).onchange = (e) => act(() => api("/updates/repos/" + encodeURIComponent(r.name), { method: "PUT", body: { auto_subscribe: e.target.checked } }), "Saved");
  $("#pin", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const rev = new FormData(e.target).get("revision");
    await act(() => api(`/updates/repos/${encodeURIComponent(r.name)}/pin`, { body: { revision: rev || null } }),
      (x) => `Serving ${rev ? rev.slice(0, 10) : "the newest revision"}; snapshot ${x.files} files from ${x.source}, ${x.problems.length} problems`);
    closeModal(); routes.updates();
  });
  $("#snap", m).onclick = () => act(() => api(`/updates/repos/${encodeURIComponent(r.name)}/snapshot`, { body: {} }), (x) => `Snapshot: ${x.files} files, ${x.problems.length} problems`);
  $("#pub", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target); fd.delete("files");
    for (const f of $("input[type=file]", m).files) fd.append("files", f, f.webkitRelativePath ? f.webkitRelativePath.split("/").slice(1).join("/") : f.name);
    await act(() => api(`/updates/repos/${encodeURIComponent(r.name)}/publish`, { form: fd }), (x) => x.revision ? `Published revision ${x.revision.slice(0, 10)} (${x.files} files)` : "No file changed");
    closeModal(); routes.updates();
  });
}

// --------------------------------------------------------------------------------------- releases
// Our own builds (docs/releases.md): publish a release zip unreleased, roll it out to a store, a market or everyone,
// roll back, watch what every store runs; API tokens for CI.
const revName = (h, labels) => {
  if (!h) return '<span class="muted">-</span>';
  const l = labels && labels[h];
  return `<code title="${esc(h)}">${short(h)}</code>${l && (l.label || l.version) ? " " + esc(l.label || l.version) : ""}`;
};
const targetText = (t, n) => (t === "all" ? "everyone" : `${t} ${n}`);
const releasedPill = (v) => (v ? pill("released", "ok") : pill("unreleased", "warn"));

routes.releases = async (repoName) => {
  const d = await api("/releases");
  const name = repoName ? decodeURIComponent(repoName) : (d.repositories[0] || {}).name;
  view().innerHTML = head("Releases", "Builds are published unreleased. Nothing reaches a kiosk until you roll it out.",
    (can("admin") ? `<a class="button" href="#/access/tokens">CI tokens</a>` : "") + (can() ? `<button class="primary" id="pub">${ic("upload")}Publish a release</button>` : "")) + `
    <div class="split"><div class="card list">${d.repositories.map((r) => `<a href="#/releases/${encodeURIComponent(r.name)}" class="${r.name === name ? "on" : ""}">${esc(r.name)}
        ${r.unreleased ? '<span class="label red">new</span>' : `<span>${esc(r.served_label || short(r.served) || "")}</span>`}</a>`).join("") || '<p class="muted">No repositories yet.</p>'}</div>
      <div id="rr"></div></div>`;
  if ($("#pub")) $("#pub").onclick = () => {
    const m = modal(`<h3>Publish a release</h3><p class="muted">A zip from the Software repository's <code>build/release.ps1</code>. It is published
        <b>unreleased</b>; file-set releases become file-set revisions (deploy those on <a href="#/filesets">File sets</a>).</p>
      <form id="up"><label>Release zip <input type="file" name="file" accept=".zip" required></label>
        <label><input type="checkbox" name="allow_new" value="true"> also add files the repository does not have yet</label>
        <div class="toolbar mt"><button class="primary">Publish</button><button type="button" id="cl">Cancel</button></div></form><div id="upres"></div>`);
    $("#cl", m).onclick = closeModal;
    $("#up", m).addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      if (!fd.get("allow_new")) fd.set("allow_new", "false");
      const btn = $("button", e.target); btn.disabled = true; btn.textContent = "Publishing...";
      try {
        const x = await act(() => api("/releases/upload", { form: fd }), (x) => x.fileset ? `File set ${x.fileset} revision ${x.revision} created`
          : x.revision ? `Published ${x.label} (unreleased)` : "Nothing changed");
        $("#upres", m).innerHTML = `<div class="card mt"><div class="kv">${Object.entries(x).map(([k, v]) =>
          `<div>${esc(k)}</div><div>${Array.isArray(v) ? esc(v.join(", ") || "-") : cell(v)}</div>`).join("")}</div>
          ${x.repository && x.revision ? `<p><a href="#/releases/${encodeURIComponent(x.repository)}">Roll it out</a></p>` : ""}
          ${x.fileset ? `<p><a href="#/filesets">Deploy it</a></p>` : ""}</div>`;
      } finally { btn.disabled = false; btn.textContent = "Publish"; }
    });
  };
  if (name) await releaseRepo(name, $("#rr"));
};

async function releaseRepo(name, root) {
  const st = await api(`/releases/repo/${encodeURIComponent(name)}`);
  const L = st.labels;
  for (const r of st.revisions) L[r.hash] = L[r.hash] || { label: r.label, version: r.version };
  const stateCls = { current: "ok", behind: "warn", ahead: "bad", "unknown head": "bad", "no report": "" };
  const byKind = (k) => st.rollouts.filter((r) => r.target_type === k);
  const current = st.stores.filter((s) => s.state === "current").length;
  const newest = st.revisions[0];
  root.innerHTML = `<div class="card"><div class="card-h"><h2>${esc(name)}</h2><span class="grow"></span>${can() ? `<button class="sm" id="rbAll">${ic("undo")}Roll back...</button>
        ${newest ? `<button class="sm primary" data-roll="${esc(newest.hash)}">Roll out ${esc(newest.version || newest.label || short(newest.hash))}</button>` : ""}` : ""}</div>
      <div class="facts"><div><span>Everyone gets</span><b>${revName(st.default_head, L)}</b></div>
        <div><span>Market rollouts</span><b>${byKind("market").map((r) => `${esc(r.target)} → ${revName(r.head, L)}`).join("<br>") || '<span class="muted">None</span>'}</b></div>
        <div><span>Store rollouts</span><b>${byKind("store").map((r) => `${esc(r.target)} → ${revName(r.head, L)}`).join("<br>") || '<span class="muted">None</span>'}</b></div></div>
      ${st.stores.length ? `<div class="row mt" style="font-size:12px;font-weight:600"><span>Stores up to date</span><span class="grow"></span>
        <span class="muted">${current} of ${st.stores.length}${["behind", "ahead", "no report"].map((s) => { const n = st.stores.filter((x) => x.state === s).length; return n ? ` · ${n} ${s}` : ""; }).join("")}</span></div>
        <div class="bar" style="margin-top:8px"><i style="width:${(current / st.stores.length) * 100}%"></i></div>` : ""}
      <p class="muted" style="font-size:12px;margin:14px 0 0">A store gets its own rollout, else its market's, else the default (the pin, else the newest released revision).
        updatemgrd never moves back: a store past what it is served keeps its version until a revert reaches it.</p></div>
    <h3>Revisions</h3>${table(st.revisions, [{ key: "seq" }, { key: "hash", render: (v) => `<code title="${esc(v)}">${short(v)}</code>` },
      { key: "version" }, { key: "label" }, { key: "released", label: "", render: releasedPill }, { key: "origin" },
      { key: "created_at", label: "Added" }, { key: "_", label: "", render: (v, r) => `<button data-roll="${esc(r.hash)}">Roll out...</button>` }])}
    <h3>Store and market rollouts</h3>${table(st.rollouts, [{ key: "target_type", label: "Target", render: (v, r) => esc(targetText(v, r.target)) },
      { key: "head", label: "Served", render: (v) => revName(v, L) }, { key: "created_at", label: "Since" },
      { key: "_", label: "", render: (v, r) => `<button data-back="${esc(r.target_type)}" data-target="${esc(r.target)}">Roll back...</button>` }],
      { empty: "None: every store gets the default." })}
    <h3>Stores</h3><p class="muted">What each store last reported (<code>u p</code>, every 15 minutes) against what it is served now.</p>
    ${table(st.stores, [{ key: "store", render: (v) => `<a href="#/kiosk/${esc(v)}">${esc(v)}</a>` }, { key: "market" },
      { key: "state", render: (v) => pill(v, stateCls[v]) }, { key: "reported", label: "Runs", render: (v) => revName(v, L) },
      { key: "reported_at", label: "Reported", render: (v) => esc(ago(v)) }, { key: "served", label: "Served", render: (v) => revName(v, L) },
      { key: "_", label: "", render: (v, r) => `<button data-store-roll="${esc(r.store)}">Roll out...</button> <button data-back="store" data-target="${esc(r.store)}">Roll back...</button>` }],
      { empty: "No store has polled yet." })}
    <h3>History</h3>${table(st.log, [{ key: "created_at", label: "When" }, { key: "action" },
      { key: "target_type", label: "Target", render: (v, r) => esc(targetText(v, r.target)) },
      { key: "previous", label: "Before", render: (v) => v ? revName(v, L) : "the default" },
      { key: "head", label: "After", render: (v) => v ? revName(v, L) : "the default" }, { key: "actor", label: "By" }], { empty: "No rollouts yet." })}`;

  const targetFields = (pre = {}) => `
    <label>Target <select name="kind"><option value="store">one store</option><option value="market">a market</option><option value="all">every store</option></select></label>
    <label id="f-store">Store <select name="store">${st.stores.map((s) => `<option ${s.store === pre.store ? "selected" : ""}>${esc(s.store)}</option>`).join("")}</select></label>
    <label id="f-market" class="hidden">Market <select name="market">${st.markets.map((x) => `<option>${esc(x)}</option>`).join("")}</select></label>`;
  const wireTarget = (m, pre = {}) => {
    const kind = $("select[name=kind]", m);
    kind.value = pre.kind || "store";
    const sync = () => { $("#f-store", m).classList.toggle("hidden", kind.value !== "store"); $("#f-market", m).classList.toggle("hidden", kind.value !== "market"); };
    kind.onchange = sync; sync();
    if (pre.market) $("select[name=market]", m).value = pre.market;
  };
  const targetBody = (fd) => (fd.get("kind") === "all" ? { all: true } : fd.get("kind") === "market" ? { market: fd.get("market") } : { store: fd.get("store") });
  const check = (fd) => fd.get("kind") !== "all" || confirm(`This changes what EVERY store of ${name} is served. Continue?`);

  const rollout = (hash, pre = {}) => {
    const m = modal(`<h3>Roll out ${name}</h3><form id="rf">
      <label>Revision <select name="revision">${st.revisions.map((r) => `<option value="${esc(r.hash)}" ${r.hash === hash ? "selected" : ""}>${esc(r.seq)} ${short(r.hash)} ${esc(r.version || r.label || "")}${r.released ? "" : " (unreleased)"}</option>`).join("")}</select></label>
      ${targetFields(pre)}<p class="muted">Kiosks below it get every revision up to it; kiosks without the repository get its snapshot. Everyone: it is released and pinned, and store/market rollouts at or below it are removed.</p>
      <button class="primary">Roll out</button> <button type="button" id="cl">Cancel</button></form>`);
    wireTarget(m, pre);
    $("#cl", m).onclick = closeModal;
    $("#rf", m).addEventListener("submit", async (e) => {
      e.preventDefault(); const fd = new FormData(e.target);
      if (!check(fd)) return;
      await act(() => api(`/releases/repo/${encodeURIComponent(name)}/rollout`, { body: { revision: fd.get("revision"), ...targetBody(fd) } }),
        (x) => `${targetText(x.target, x.name)} now gets ${x.label || x.head.slice(0, 10)}`);
      closeModal(); route();
    });
  };
  const rollback = (pre = {}) => {
    const m = modal(`<h3>Roll back ${name}</h3><form id="bf">${targetFields(pre)}
      <label>Back to <select name="to"><option value="">what it had before its last rollout</option>${st.revisions.map((r) => `<option value="${esc(r.hash)}">${esc(r.seq)} ${short(r.hash)} ${esc(r.version || r.label || "")}</option>`).join("")}</select></label>
      <p class="muted">If no store of the target has taken the newer revision yet, the target is pointed back. If some have, a <b>revert revision</b>
        is published with the older files and served to the target, because updatemgrd only moves forward. Files a release added are not removed.</p>
      <button class="primary">Roll back</button> <button type="button" id="cl">Cancel</button></form><div id="bres"></div>`);
    wireTarget(m, pre);
    $("#cl", m).onclick = closeModal;
    $("#bf", m).addEventListener("submit", async (e) => {
      e.preventDefault(); const fd = new FormData(e.target);
      if (!check(fd)) return;
      const x = await act(() => api(`/releases/repo/${encodeURIComponent(name)}/rollback`, { body: { to: fd.get("to"), ...targetBody(fd) } }),
        (x) => x.revert ? `Published revert ${x.revert.slice(0, 10)} for ${x.stores_ahead.length} store(s) that had moved on` : "Pointed back; no store had taken it yet");
      if (x.not_removed && x.not_removed.length) {
        $("#bres", m).innerHTML = `<p class="error">These files were added after ${esc(x.to.slice(0, 10))} and stay on the kiosks:</p><pre>${esc(x.not_removed.join("\n"))}</pre>
          <button id="ok" class="primary">OK</button>`;
        $("#ok", m).onclick = () => { closeModal(); route(); };
      } else { closeModal(); route(); }
    });
  };
  $$("button[data-roll]").forEach((b) => (b.onclick = () => rollout(b.dataset.roll)));
  $$("button[data-store-roll]").forEach((b) => (b.onclick = () => rollout(st.revisions[0] && st.revisions[0].hash, { kind: "store", store: b.dataset.storeRoll })));
  $$("button[data-back]").forEach((b) => (b.onclick = () => rollback({ kind: b.dataset.back, store: b.dataset.back === "store" ? b.dataset.target : undefined,
    market: b.dataset.back === "market" ? b.dataset.target : undefined })));
  $("#rbAll").onclick = () => rollback({ kind: "all" });
}

// ----------------------------------------------------------------------------- reservation broker
routes.broker = async () => {
  title("Reservation broker");
  const d = await api("/broker");
  const live = d.kiosks.filter((k) => k.connection === "connected" || k.connection === "not answering");
  view().innerHTML = `<p class="muted">Kiosks with <code>UseLegacyBrokerService</code> register with the legacy broker
    (<code>gtcp://...:7890/RegistrationBroker/Default.rem</code>, hosted on Mono in rb-remoting) and stay connected; the broker then
    calls back into them. A reservation sent here makes the kiosk hold discs (Reserve3) and is stored like any other reservation,
    so the kiosk's pickup goes through the Transaction API. ${d.reachable ? "" : '<b class="error">rb-remoting is not reachable.</b>'}</p>
    <h3>Kiosks</h3>${table(d.kiosks, [{ key: "kiosk_id", label: "Kiosk", render: (v) => `<a href="#/kiosk/${esc(v)}">${esc(v)}</a>` },
      { key: "connection", label: "Connection", render: (v, r) => v === "connected" ? pill("connected", "ok")
          : v === "lost" || v === "not answering" ? `${pill(v, "warn")} <span class="muted">${esc(fmtDate(r.lost_at))} ${esc(r.lost_reason || "")}</span>`
          : v === "unknown" ? pill("unknown", "warn") : pill("not connected", "bad") },
      { key: "registered_at", label: "Registered" }, { key: "registrations", label: "Registrations" },
      { key: "version", label: "Version / pricing", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v).slice(0, 220))}</code>` : "" },
      { key: "disconnected_at", label: "Disconnected" }], { empty: "No kiosk has registered with the broker." })}
    <div class="grid2 mt">
      <form id="rf" class="card"><b>Send a reservation to a kiosk</b>
        <label>Kiosk <select name="kiosk_id">${live.map((k) => `<option>${esc(k.kiosk_id)}</option>`).join("")}</select></label>
        <label>Customer e-mail <input name="email" type="email" required></label>
        <label>Card last four <input name="last4" maxlength="4"></label>
        <label>Titles (one per line: title id, optional price, optional "purchase") <textarea name="titles" required placeholder="1234 1.99&#10;5678 2.99"></textarea></label>
        <label><input type="checkbox" name="authorize_at_pickup" checked> authorize at pickup</label>
        <button class="primary" ${live.length ? "" : "disabled"}>Reserve</button><pre id="ro" class="mt hidden"></pre></form>
      <form id="cf" class="card"><b>Cancel a reservation on a kiosk</b>
        <label>Kiosk <select name="kiosk_id">${live.map((k) => `<option>${esc(k.kiosk_id)}</option>`).join("")}</select></label>
        <label>Reference number <input name="reference_number" required></label>
        <div class="toolbar"><button class="primary" ${live.length ? "" : "disabled"}>Cancel reservation</button><button type="button" id="pg" ${live.length ? "" : "disabled"}>Ping kiosk</button></div>
        <pre id="co" class="mt hidden"></pre></form>
    </div>
    <h3>Broker calls</h3>${table(d.requests, [{ key: "created_at", label: "Time" }, { key: "kiosk_id", label: "Kiosk" }, { key: "kind" },
      { key: "reference_number", label: "Reference" }, { key: "success", render: (v) => v ? pill("ok", "ok") : pill("failed", "bad") },
      { key: "result", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v).slice(0, 300))}</code>` : "" }, { key: "error", wrap: true }])}`;
  $("#rf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const titles = String(f.get("titles")).split(/\n+/).map((l) => l.trim()).filter(Boolean).map((l) => {
      const p = l.split(/[\s,;]+/);
      return { title_id: +p[0], price: p[1] && !isNaN(+p[1]) ? +p[1] : undefined, type: /purchase/i.test(l) ? "purchase" : "rental" };
    });
    const r = await act(() => api("/broker/reserve", { body: { kiosk_id: f.get("kiosk_id"), email: f.get("email"), last4: f.get("last4"),
      titles, authorize_at_pickup: !!f.get("authorize_at_pickup") } }), (x) => `Reservation ${x.reference_number}: ${x.result.ReservationSucceeded ? "reserved" : "failed"}`);
    $("#ro").classList.remove("hidden"); $("#ro").textContent = JSON.stringify(r, null, 2);
  });
  $("#cf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const r = await act(() => api("/broker/cancel", { body: f }), (x) => x.result.CancellationSucceeded ? "Cancelled" : "Not cancelled");
    $("#co").classList.remove("hidden"); $("#co").textContent = JSON.stringify(r, null, 2);
  });
  $("#pg").onclick = () => act(() => api("/broker/ping", { body: { kiosk_id: $("#cf").kiosk_id.value } }), "Kiosk answered the ping");
};

// -------------------------------------------------------------------------------- update packages
routes.packages = async () => {
  title("Data zips");
  const d = await api("/packages");
  view().innerHTML = `<p class="muted">Redbox update-store seeds (<code>data-&lt;date&gt;.zip</code>, the data file the store installer
    activates on a new kiosk) from <code>${esc(d.dir || "(no PACKAGES_DIR mounted)")}</code>, read-only. Each import becomes an edition; every
    repository is served from the newest edition that has it, with content older editions kept. dvd-data is about 1 GB per seed.</p>
    <div class="toolbar"><label>dvd-data <select id="dvd"><option value="latest">only from the newest selected seed</option><option value="none">skip</option><option value="all">from every seed</option></select></label>
    <button class="primary" id="imp" ${d.dir ? "" : "disabled"}>Import selected</button><button id="all" ${d.dir ? "" : "disabled"}>Select all</button></div>
    <div class="table-wrap"><table><thead><tr><th></th><th>File</th><th>Seed date</th><th>Size</th><th>Imported as</th><th>Imported</th></tr></thead><tbody>
    ${d.files.map((f) => `<tr><td><input type="checkbox" data-f="${esc(f.file)}"></td><td>${esc(f.file)}</td><td>${esc(f.date)}</td><td>${esc((f.size / 2 ** 20).toFixed(0))} MB</td><td>${esc(f.edition || "")}</td><td>${f.imported_at ? fmtDate(f.imported_at) : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <h3>Import jobs</h3><div id="jobs"></div>
    <h3>Editions</h3>${table(d.editions, [{ key: "name", label: "Edition" }, { key: "edition_date", label: "Date" },
      { key: "revisions", label: "Repositories (revisions)", wrap: true, render: (v) => esc(Object.entries(v).map(([k, n]) => `${k} ${n}`).join(", ")) },
      { key: "source" }, { key: "imported_at", label: "Imported" }], { empty: "No edition imported yet." })}
    <h3>Build a seed zip</h3>
    <form id="bsf" class="card"><p class="muted">Writes <code>data-&lt;MMDDYYYY&gt;.zip</code> in the store format (docs/seed-zips.md) from the
      repositories here, at the chosen revisions. New package content is added with <code>admin publish</code> first, or with
      <code>admin build-seed --add</code>.</p>
      <label>History <select name="edition"><option value="">served history</option>${d.editions.map((e) => `<option>${esc(e.name)}</option>`).join("")}</select></label>
      <label><input type="checkbox" name="all" checked> every repository at its head</label>
      <label><input type="checkbox" name="catalog"> add the catalog as it is now (a new dvd-data revision; see Catalog)</label>
      <label>or these, one <code>name</code> or <code>name@revision-or-label</code> per line <textarea name="repos" rows="3" placeholder="kop-v2-engine@kop-v2-engine-Calvin"></textarea></label>
      <label>Date <input name="date" placeholder="MMDDYYYY (default today)" size="12"></label>
      <button class="primary">Build</button></form>
    <h3>Built seeds</h3><div id="exp"></div>`;
  const drawExports = async () => {
    const ex = await api("/packages/exports");
    $("#exp").innerHTML = table(ex, [{ key: "file", render: (v) => `<a href="/api/packages/exports/${encodeURIComponent(v)}">${esc(v)}</a>` },
      { key: "size", render: (v) => esc((v / 2 ** 20).toFixed(1)) + " MB" }, { key: "modified", label: "Built" },
      { key: "file", label: "", render: (v) => `<button data-del="${esc(v)}">Delete</button>` }], { empty: "None yet." });
    $$("button[data-del]").forEach((b) => (b.onclick = () => act(() => api("/packages/exports/" + encodeURIComponent(b.dataset.del), { method: "DELETE" }), () => "Deleted").then(drawExports)));
  };
  drawExports();
  $("#bsf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const repos = String(f.get("repos") || "").split("\n").map((x) => x.trim()).filter(Boolean)
      .map((x) => ({ name: x.split("@")[0], revision: x.includes("@") ? x.slice(x.indexOf("@") + 1) : null }));
    const all = f.get("all") === "on" && !repos.length;
    await act(() => api("/packages/build", { body: { all, repos, edition: f.get("edition") || null, date: f.get("date") || null, catalog: f.get("catalog") === "on" } }),
      (x) => `Building ${x.file} (job ${x.job})`);
    poll();
  });
  const drawJobs = (jobs) => {
    $("#jobs").innerHTML = table(jobs, [{ key: "id" }, { key: "source", wrap: true }, { key: "status", render: (v) => pill(v, v === "done" ? "ok" : v === "failed" ? "bad" : "warn") },
      { key: "progress", label: "Last step", wrap: true, render: (v) => esc(v.length ? v[v.length - 1].m : "") }, { key: "started_at", label: "Started" }, { key: "finished_at", label: "Finished" }]);
  };
  drawJobs(d.jobs);
  $("#all").onclick = () => $$("input[data-f]").forEach((c) => (c.checked = true));
  $("#imp").onclick = async () => {
    const files = $$("input[data-f]").filter((c) => c.checked).map((c) => c.dataset.f);
    if (!files.length) return toast("Select at least one file", true);
    await act(() => api("/packages/import", { body: { files, dvd_data: $("#dvd").value } }), (x) => `Import job ${x.job} started`);
    poll();
  };
  const poll = async () => {
    clearTimeout(routes.packages.timer);
    if (!location.hash.startsWith("#/packages")) return;
    const p = await api("/packages");
    drawJobs(p.jobs);
    if (p.jobs.some((j) => j.status === "running")) routes.packages.timer = setTimeout(poll, 3000);
    else drawExports();
  };
  if (d.jobs.some((j) => j.status === "running")) poll();
};

// --------------------------------------------------------------------------------------- file sets
routes.filesets = async () => {
  title("File sets");
  const sets = await api("/filesets");
  view().innerHTML = `<p class="muted">File sets go to the Update Client over IoT ("ucs" channel, pushed immediately) or to updatemgrd's server poll ("legacy").</p>
    <form id="nf" class="inline card"><label>Id <input name="id" type="number" required size="5"></label><label>Name <input name="name" required></label><button class="primary">Create file set</button></form>
    <div id="fs"></div>`;
  $("#nf").addEventListener("submit", async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    await act(() => api("/filesets", { body: f }), "Created"); routes.filesets();
  });
  $("#fs").innerHTML = sets.map((s) => `<div class="card mt"><div class="toolbar"><b>${esc(s.id)} - ${esc(s.name)}</b><span class="grow"></span>
      <button data-rev="${esc(s.id)}">New revision</button><button data-dep="${esc(s.id)}">Deploy</button></div>
      <h3>Revisions</h3>${table(s.revisions, [{ key: "id" }, { key: "version" }, { key: "name" }, { key: "files" }, { key: "created_at", label: "Created" }])}
      <h3>Deployments</h3>${table(s.deployments, [{ key: "id" }, { key: "revision_id", label: "Revision", render: (v) => v ? esc(v) : "remove" }, { key: "channel" }, { key: "target_type", label: "Target", render: (v, r) => esc(v + (r.target ? " " + r.target : "")) }, { key: "activate_start", label: "Window", render: (v, r) => esc(v ? v + "-" + r.activate_end : "any time") }, { key: "created_at", label: "Created" }])}
      <h3>Kiosk states</h3>${table(s.states, [{ key: "kiosk_id", label: "Kiosk" }, { key: "channel" }, { key: "revision_id", label: "Revision" }, { key: "state", render: (v) => ["Active", "InProgress", "NeedsDependency", "Error"][v] || esc(v) }, { key: "updated_at", label: "Reported" }])}</div>`).join("") || '<p class="muted mt">No file sets yet.</p>';
  $$("button[data-rev]").forEach((b) => (b.onclick = () => {
    const m = modal(`<h3>New revision of file set ${esc(b.dataset.rev)}</h3><form id="rf">
      <label>Version <input name="version" required placeholder="1.0.0.0"></label><label>Name <input name="name"></label>
      <label>Destination folder on the kiosk <input name="destination" required value="C:\\Program Files\\Redbox\\REDS\\"></label>
      <label>Dependencies (JSON, optional) <textarea name="dependencies" placeholder='[{"DependsOnFileSetId":2,"DependencyType":0,"MinimumVersion":"1.0.0.0"}]'></textarea></label>
      <label>Files <input type="file" name="files" multiple required></label><label><input type="checkbox" id="folder"> pick a whole folder</label>
      <p class="muted">Include a fileset.json to name a Windows service to stop/start, and beforeactivate.ps1 / afteractivate.ps1 scripts if needed.</p>
      <button class="primary">Create revision</button> <button type="button" id="cl">Cancel</button></form>`);
    $("#cl", m).onclick = closeModal;
    $("#folder", m).onchange = (e) => { $("input[type=file]", m).webkitdirectory = e.target.checked; };
    $("#rf", m).addEventListener("submit", async (e) => {
      e.preventDefault(); const fd = new FormData(e.target); fd.delete("files");
      for (const f of $("input[type=file]", m).files) fd.append("files", f, f.webkitRelativePath ? f.webkitRelativePath.split("/").slice(1).join("/") : f.name);
      await act(() => api(`/filesets/${b.dataset.rev}/revisions`, { form: fd }), (x) => `Revision ${x.revision} created`); closeModal(); routes.filesets();
    });
  }));
  $$("button[data-dep]").forEach((b) => (b.onclick = () => {
    const s = sets.find((x) => String(x.id) === b.dataset.dep);
    const m = modal(`<h3>Deploy file set ${esc(s.id)} - ${esc(s.name)}</h3><form id="df">
      <label>Revision <select name="revision_id">${s.revisions.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} - ${esc(r.version)}</option>`).join("")}<option value="">(remove the file set)</option></select></label>
      <label>Target <select name="target_type"><option value="kiosk">one kiosk</option><option value="market">a market</option><option value="all">all kiosks</option></select></label>
      <label>Kiosk id / market <input name="target"></label>
      <label>Channel <select name="channel"><option value="ucs">Update Client (IoT)</option><option value="legacy">updatemgrd (u pr)</option></select></label>
      <label>Activation window start (hh:mm, optional) <input name="activate_start"></label><label>Activation window end <input name="activate_end"></label>
      <button class="primary">Deploy</button> <button type="button" id="cl">Cancel</button></form>`);
    $("#cl", m).onclick = closeModal;
    $("#df", m).addEventListener("submit", async (e) => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
      await act(() => api(`/filesets/${s.id}/deploy`, { body: f }), (x) => `Deployment ${x.deployment.id}` + (x.pushed ? `, pushed to ${x.pushed.delivered} connected kiosk(s)` : ", sent on the next server poll"));
      closeModal(); routes.filesets();
    });
  }));
};

// ---------------------------------------------------------------------------------- configuration
routes.config = async () => {
  title("Configuration");
  view().innerHTML = `<div class="tabs"><button class="on" data-t="k">Kiosk configuration (Update Client)</button><button data-t="l">Engine config files (updatemgrd)</button></div><div id="cv"></div>`;
  const show = (t) => { $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === t)); (t === "k" ? kconfigView : configFilesView)($("#cv")); };
  $$(".tabs button").forEach((b) => (b.onclick = () => show(b.dataset.t)));
  show("k");
};
async function kconfigView(root) {
  const d = await api("/kconfig");
  root.innerHTML = `<p class="muted">Written to <code>configuration.json</code> on each kiosk by the Update Client (read by the Kiosk Client). Kiosk values beat market values beat defaults. Current version: <b>${esc(d.version)}</b>.</p>
    <form id="kf" class="card inline"><label>Category <input name="category" required list="cats"></label><label>Setting <input name="setting" required></label>
      <label>Value <input name="value"></label><label>Type <select name="data_type"><option>string</option><option>bool</option><option>int</option><option>long</option><option>decimal</option></select></label>
      <label>Scope <select name="scope"><option value="all">all kiosks</option><option value="market">market</option><option value="kiosk">kiosk</option></select></label>
      <label>Market / kiosk <input name="target" size="10"></label><label><input type="checkbox" name="push" checked> push now</label><button class="primary">Set value</button></form>
    <datalist id="cats">${[...new Set(d.values.map((v) => v.category))].map((c) => `<option>${esc(c)}</option>`).join("")}</datalist>
    <div class="toolbar mt"><button id="pa">Push to all kiosks</button><input id="pk" placeholder="kiosk id" size="9"><button id="pk1">Push to kiosk</button></div>
    <div id="vt"></div><h3>Kiosks</h3>${table(d.kiosks, [{ key: "kiosk_id", label: "Kiosk" }, { key: "reported_version", label: "Reported" }, { key: "activated_version", label: "Activated" }, { key: "activated_at", label: "Activated at" }])}`;
  $("#vt", root).innerHTML = table(d.values, [{ key: "category" }, { key: "name", label: "Setting" }, { key: "value", wrap: true }, { key: "data_type", label: "Type" },
    { key: "segmentation_name", label: "Scope", render: (v, r) => esc(v === "All" ? "all" : v + " " + r.segment_key) },
    { key: "value_id", label: "", render: (v) => `<button class="danger" data-del="${esc(v)}">Delete</button>` }]);
  $$("[data-del]", root).forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation(); if (!confirm("Delete this value?")) return;
    await act(() => api("/kconfig/delete", { body: { value_id: b.dataset.del } }), "Deleted"); kconfigView(root);
  }));
  $("#kf", root).addEventListener("submit", async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.push = !!f.push;
    await act(() => api("/kconfig", { body: f }), (x) => `Saved (version ${x.version})` + (x.pushed ? `, pushed to ${x.pushed.delivered}` : "")); kconfigView(root);
  });
  $("#pa", root).onclick = () => act(() => api("/kconfig/push", { body: { scope: "all" } }), (x) => `Pushed (${x.delivered} deliveries)`);
  $("#pk1", root).onclick = () => act(() => api("/kconfig/push", { body: { scope: "kiosk", target: $("#pk", root).value } }), (x) => `Pushed (${x.delivered} deliveries)`);
}
async function configFilesView(root) {
  const files = await api("/configfiles");
  root.innerHTML = `<p class="muted">Delivered by updatemgrd's server poll (every 17 min) into <code>Kiosk Engine\\data</code> and the Kiosk Client config folder. Saving creates a new generation.</p>` +
    table(files, [{ key: "oid", label: "OID" }, { key: "name" }, { key: "path", wrap: true }, { key: "generation" }, { key: "settings" }, { key: "stores_current", label: "Stores on it" }, { key: "updated_at", label: "Updated" }], { onclick: true, empty: "No config files imported (rb.ps1 import-store ... --config-files)." });
  bindRows(root, files, async (f) => {
    const d = await api("/configfiles/" + f.oid);
    const m = modal(`<h3>${esc(d.name)} (generation ${esc(d.generation)})</h3><input id="cf" placeholder="Filter settings" style="width:100%"><div id="ct" class="mt"></div>
      <div class="toolbar mt"><button class="primary" id="sv">Save new generation</button><button id="cl">Close</button></div>`);
    const changes = {};
    const draw = () => {
      const q = $("#cf", m).value.toLowerCase();
      $("#ct", m).innerHTML = `<div class="table-wrap"><table><thead><tr><th>Category</th><th>Setting</th><th>Scope</th><th>Value</th></tr></thead><tbody>` +
        d.data.map((s, i) => [s, i]).filter(([s]) => !q || (s.CategoryName + " " + s.SettingName).toLowerCase().includes(q)).map(([s, i]) =>
          `<tr><td>${esc(s.CategoryName)}</td><td>${esc(s.SettingName)}</td><td>${esc(s.DefaultTypeName)} ${esc(s.DefaultTypeValue)}</td><td><input data-i="${i}" value="${esc(i in changes ? changes[i] : s.Value)}" size="40"></td></tr>`).join("") + "</tbody></table></div>";
      $$("input[data-i]", m).forEach((el) => (el.oninput = () => (changes[el.dataset.i] = el.value)));
    };
    $("#cf", m).oninput = draw; draw();
    $("#cl", m).onclick = closeModal;
    $("#sv", m).onclick = async () => {
      const list = Object.entries(changes).map(([index, value]) => ({ index: +index, value }));
      if (!list.length) return closeModal();
      await act(() => api("/configfiles/" + f.oid, { method: "PUT", body: { changes: list } }), (x) => `Generation ${x.generation} published`);
      closeModal(); configFilesView(root);
    };
  });
}

// ---------------------------------------------------------------------------------------- jobs
routes.jobs = async () => {
  title("Remote commands");
  const [cmds, clients, reqs, uploads] = await Promise.all([api("/jobs/commands"), api("/jobs/clients"), api("/jobs/requests"), api("/uploads")]);
  view().innerHTML = `<div class="grid2">
    <form id="cf" class="card"><b>Send an IoT command</b> <span class="muted">(${clients.length} kiosk(s) connected)</span>
      <label>Target <input name="target" value="${esc(clients[0] ? clients[0].clientId : "all")}" placeholder="kiosk id, market:Name or all"></label>
      <label>Command <select name="command">${Object.keys(cmds).map((c) => `<option>${esc(c)}</option>`).join("")}<option value="_custom">other...</option></select></label>
      <label>Other command name <input name="custom" placeholder="e.g. CancelDownloadFileJob"></label>
      <label>Version <input name="version" type="number" value="2"></label>
      <label>Payload (JSON) <textarea name="payload"></textarea></label>
      <label>Wait for the answer (seconds) <input name="wait" type="number" value="10"></label>
      <p class="muted">ExecuteHttpRequest runs an HTTP call on the kiosk, e.g. POST http://localhost:9001/api/pricing/all/refresh, .../api/campaign/refresh, or http://localhost:6001/api/kernel/shutdown?shutdownType=Reboot.</p>
      <button class="primary">Send</button><pre id="co" class="mt hidden"></pre></form>
    <form id="df" class="card"><b>Download-file job (Update Client)</b>
      <label>Target <input name="target" value="all"></label><label>Name <input name="name" required></label>
      <label>Destination folder on the kiosk <input name="destination_path" placeholder="C:\\Temp"></label>
      <label>File <input type="file" name="file"></label><label>PowerShell script (.ps1, optional) <input type="file" name="script" accept=".ps1"></label>
      <button class="primary">Start job</button></form>
    <form id="lf" class="card"><b>Download file (updatemgrd)</b> <span class="muted">delivered on the next server poll</span>
      <label>Store (empty = every store) <input name="store"></label><label>Name <input name="name" required></label>
      <label>Destination folder <input name="destination_path" required placeholder="C:\\Program Files\\Redbox\\REDS\\Kiosk Engine\\data"></label>
      <label>File (optional for a script-only job) <input type="file" name="file"></label><label>Lua activation script (optional) <textarea name="lua" placeholder="kernel.log('hello from the backend')"></textarea></label>
      <button class="primary">Queue</button></form>
  </div>
  <h3>Recent IoT requests</h3>${table(reqs.requests, [{ key: "created_at", label: "Sent" }, { key: "topic" }, { key: "command" }, { key: "version" }, { key: "responded_at", label: "Answered" }, { key: "response", wrap: true }])}
  <h3>Download-file jobs</h3>${table(reqs.download_jobs, [{ key: "created_at", label: "Created" }, { key: "metadata", label: "Job", render: (v) => esc(v.Name) }, { key: "target" }, { key: "kiosk_id", label: "Kiosk" }, { key: "status" }, { key: "updated_at", label: "Updated" }])}
  <h3>updatemgrd download files</h3>${table(reqs.legacy_downloads, [{ key: "id" }, { key: "name" }, { key: "number", label: "Store" }, { key: "file_name", label: "File" }, { key: "destination_path", label: "Destination" }, { key: "sent_to", label: "Sent to" }, { key: "created_at", label: "Created" }])}
  <h3>Files uploaded by kiosks</h3>${table(uploads, [{ key: "key", label: "File", render: (v) => `<a href="/api/uploads/file?key=${encodeURIComponent(v)}">${esc(v)}</a>` }, { key: "size" }, { key: "modified" }], { empty: "None yet - use Upload logs on a kiosk page." })}`;
  const cf = $("#cf");
  const fill = () => { const c = cmds[cf.command.value]; if (c) { cf.version.value = c.version; cf.payload.value = c.payload === null ? "" : JSON.stringify(c.payload, null, 1); } };
  cf.command.onchange = fill; fill();
  cf.addEventListener("submit", async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(cf));
    if (f.command === "_custom") f.command = f.custom;
    const r = await act(() => api("/jobs/iot", { body: f }), (x) => `Sent (${x.delivered} deliveries)`);
    $("#co").classList.remove("hidden"); $("#co").textContent = JSON.stringify(r, null, 2);
  });
  $("#df").addEventListener("submit", async (e) => { e.preventDefault(); await act(() => api("/jobs/download", { form: new FormData(e.target) }), (x) => `Job sent (${x.delivered} deliveries)`); routes.jobs(); });
  $("#lf").addEventListener("submit", async (e) => { e.preventDefault(); await act(() => api("/jobs/legacy-download", { form: new FormData(e.target) }), (x) => `Queued as ${x.id}`); routes.jobs(); });
};

// ---------------------------------------------------------------------------------------- logs
routes.logs = async () => {
  title("Logs & events");
  const q = query();
  const kinds = await api("/events/kinds");
  const sources = [...new Set(kinds.map((k) => k.source))];
  view().innerHTML = `<div class="toolbar"><input id="lk" placeholder="Kiosk" size="8" value="${esc(q.kiosk || "")}">
    <select id="ls"><option value="">any source</option>${sources.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
    <select id="lkind"><option value="">any kind</option></select><input id="lq" placeholder="Search payload" class="grow">
    <button id="lg">Refresh</button><label><input type="checkbox" id="lauto"> auto-refresh</label></div><div id="lt"></div>
    <h3>Event kinds</h3>${table(kinds, [{ key: "source" }, { key: "kind" }, { key: "n", label: "Count" }, { key: "last", label: "Last" }])}`;
  const fillKinds = () => { $("#lkind").innerHTML = '<option value="">any kind</option>' + kinds.filter((k) => !$("#ls").value || k.source === $("#ls").value).map((k) => `<option>${esc(k.kind)}</option>`).join(""); };
  $("#ls").onchange = () => { fillKinds(); load(); }; fillKinds();
  let rows = [];
  const load = async (more) => {
    const p = new URLSearchParams({ limit: "200" });
    if ($("#lk").value) p.set("kiosk_id", $("#lk").value);
    if ($("#ls").value) p.set("source", $("#ls").value);
    if ($("#lkind").value) p.set("kind", $("#lkind").value);
    if ($("#lq").value) p.set("q", $("#lq").value);
    if (more && rows.length) p.set("before_id", rows[rows.length - 1].id);
    const got = await api("/events?" + p);
    rows = more ? rows.concat(got) : got;
    $("#lt").innerHTML = table(rows, [{ key: "id" }, { key: "created_at", label: "Time" }, { key: "kiosk_id", label: "Kiosk" }, { key: "source" }, { key: "kind" },
      { key: "payload", label: "Payload", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v).slice(0, 300))}</code>` : "" }], { onclick: true }) +
      (got.length === 200 ? '<button id="more" class="mt">Older</button>' : "");
    bindRows($("#lt"), rows, (r) => { const m = modal(`<h3>${esc(r.source)} / ${esc(r.kind)}</h3><p class="muted">${esc(fmtDate(r.created_at))}, kiosk ${esc(r.kiosk_id ?? "-")}</p><pre>${esc(JSON.stringify(r.payload, null, 2))}</pre><button id="cl" class="mt">Close</button>`); $("#cl", m).onclick = closeModal; });
    if ($("#more")) $("#more").onclick = () => load(true);
  };
  $("#lg").onclick = () => load(); $("#lkind").onchange = () => load(); $("#lq").addEventListener("keydown", (e) => e.key === "Enter" && load());
  clearInterval(routes.logs.timer);
  routes.logs.timer = setInterval(() => { if ($("#lauto") && $("#lauto").checked && location.hash.startsWith("#/logs")) load(); }, 5000);
  await load();
};

// ---------------------------------------------------------------------------------- system health
const bytes = (n) => { n = Number(n || 0); const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${u[i]}`; };
const dur = (s) => { s = Number(s || 0); const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
routes.system = async () => {
  const s = await api("/system");
  const up = s.services.filter((x) => x.ok).length;
  view().innerHTML = head("System health", `${up} of ${s.services.length} services healthy · dashboard up ${dur(s.runtime.uptime_s)}`, '<button id="rf">Refresh</button>') + `
    <div class="card flush">${table(s.services, [{ key: "name", label: "Service", render: (v, r) => `<b>${esc(v)}</b><div class="dim" style="font-size:12px;white-space:normal">${esc(r.role)}</div>` },
      { key: "ok", label: "Status", render: (v, r) => v ? '<span class="st ok"><span class="dot"></span>Healthy</span>' : `<span class="st bad"><span class="dot bad"></span>Down</span><div class="dim" style="font-size:12px;white-space:normal;max-width:360px">${esc(r.error || "HTTP " + r.status)}</div>` },
      { key: "ms", label: "Answer", right: true, render: (v) => v != null ? `${esc(v)} ms` : "" },
      { key: "detail", label: "Details", wrap: true, render: (v, r) => r.name === "rb-db" && v ? esc(`${String(v.version).split(" ").slice(0, 2).join(" ")} · ${bytes(v.size)} · ${v.connections} connections · up ${dur(v.uptime_s)}`)
        : r.name === "rb-iot" && v ? esc(`${v.clients} MQTT clients`) : r.name === "rb-remoting" && v ? esc(`${v.kiosks ?? 0} kiosks on the broker`)
        : r.name === "rb-admin" && v ? esc(`up ${dur(v.uptime_s)}`) : r.url ? `<span class="dim">${esc(r.url)}</span>` : "" }], { pad: true })}</div>
    <div class="grid2 mt">
      <div class="card"><div class="card-h"><h2>Certificates</h2><a class="more" href="/api/provisioning/ca.crt">Download the CA →</a></div>
        ${s.certificates.map((c) => `<div class="kv" style="margin-bottom:14px"><div>${esc(c.file)}</div><div><b>${esc(c.subject)}</b></div>
          <div>Expires</div><div>${c.days_left < 60 ? `<span class="st ${c.days_left < 14 ? "bad" : "warn"}"><span class="dot ${c.days_left < 14 ? "bad" : "warn"}"></span>` : ""}${esc(fmtDate(c.not_after))} (${esc(c.days_left)} days)${c.days_left < 60 ? "</span>" : ""}</div>
          ${c.names.length ? `<div>Names</div><div class="muted" style="white-space:normal">${esc(c.names.join(", "))}</div>` : ""}</div>`).join("") || '<p class="muted">No certificates found.</p>'}</div>
      <div class="card"><div class="card-h"><h2>Settings</h2><span class="dim" style="font-size:12px">from .env, read-only</span></div>
        <div class="kv">${Object.entries(s.settings).map(([k, v]) => `<div>${esc(k)}</div><div>${typeof v === "boolean" ? (v ? "yes" : "no") : esc(v)}</div>`).join("")}</div></div>
    </div>
    <div class="grid2 mt">
      <div class="card flush"><div class="card-h"><h2>Largest tables</h2></div>${table(s.tables, [{ key: "table" }, { key: "rows", right: true, render: num }, { key: "bytes", label: "Size", right: true, render: bytes }], { pad: true })}</div>
      <div class="card"><div class="card-h"><h2>Storage</h2></div>${s.disk ? `<div class="row" style="font-weight:600"><span>${esc(s.disk.path)}</span><span class="grow"></span><span class="muted">${bytes(s.disk.used)} of ${bytes(s.disk.total)} used</span></div>
        <div class="bar" style="margin-top:8px"><i style="width:${(s.disk.used / s.disk.total) * 100}%"></i></div><p class="muted">${bytes(s.disk.free)} free</p>` : '<p class="muted">Unknown.</p>'}
        <div class="kv mt"><div>Python</div><div>${esc(s.runtime.python)}</div><div>Platform</div><div class="muted" style="white-space:normal">${esc(s.runtime.platform)}</div></div></div>
    </div>`;
  $("#rf").onclick = () => { route(); health(); };
};

// --------------------------------------------------------------------------------- users & access
const ACCESS_TABS = [["users", "Dashboard users"], ["tokens", "CI tokens"], ["staff", "Field-staff logins"], ["kiosks", "Kiosk authentication"]];
routes.access = async (tab = "users") => {
  view().innerHTML = head("Users & access", "Who can sign in to the dashboard, the kiosks and the APIs") +
    `<div class="tabs">${ACCESS_TABS.map(([t, l]) => `<a href="#/access/${t}" class="${t === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="av"></div>`;
  const root = $("#av");
  if (tab === "users") {
    const d = await api("/users");
    const roleText = { admin: "Everything, including accounts and CI tokens", operator: "Every change except accounts and CI tokens", viewer: "Read only" };
    root.innerHTML = `<div class="card flush">${table([{ username: d.bootstrap.username, role: "admin", enabled: d.bootstrap.configured, last_login_at: d.bootstrap.last_login_at, bootstrap: true }, ...d.users], [
        { key: "username", label: "User", render: (v, r) => `<b>${esc(v)}</b>${r.display_name ? ` <span class="muted">${esc(r.display_name)}</span>` : ""}${r.bootstrap ? ' <span class="label">from .env</span>' : ""}` },
        { key: "role", render: (v) => `<span style="text-transform:capitalize">${esc(v)}</span>` },
        { key: "enabled", label: "Status", render: (v) => v ? '<span class="st ok"><span class="dot"></span>Active</span>' : '<span class="st mute"><span class="dot off"></span>Disabled</span>' },
        { key: "last_login_at", label: "Last login", render: (v) => `<span class="muted">${esc(ago(v))}</span>` },
        { key: "_", label: "", right: true, render: (v, r) => r.bootstrap || !can("admin") ? "" : `<button class="sm" data-edit="${esc(r.username)}">Edit</button>` }], { pad: true })}</div>
      ${can("admin") ? '<div class="toolbar mt"><button class="primary" id="nu">Add a user</button></div>' : ""}
      <div class="card mt"><div class="card-h"><h2>Roles</h2></div><div class="kv">${Object.entries(roleText).map(([r, t]) => `<div style="text-transform:capitalize">${r}</div><div>${t}</div>`).join("")}</div>
        <p class="muted" style="margin-bottom:0">The account from <code>.env</code> (ADMIN_USERNAME / ADMIN_PASSWORD_HASH) is always an admin; change its password with
        <code>scripts/set-admin-password.sh</code>. ${ME.bootstrap ? "" : '<a id="mypw" href="#/access/users">Change your password</a>'}</p></div>`;
    const userDialog = (u) => {
      const m = modal(`<h3>${u ? "Edit " + esc(u.username) : "Add a dashboard user"}</h3><form id="uf">
        ${u ? "" : '<label>User name <input name="username" required autocomplete="off"></label>'}
        <label>Display name <input name="display_name" value="${esc(u ? u.display_name || "" : "")}"></label>
        <label>Role <select name="role">${d.roles.map((r) => `<option ${u && u.role === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
        <label>${u ? "New password (leave empty to keep)" : "Password"} <input name="password" type="password" autocomplete="new-password" ${u ? "" : "required"} minlength="10"></label>
        ${u ? `<label><input type="checkbox" name="enabled" ${u.enabled ? "checked" : ""}> account enabled</label>` : ""}
        <div class="toolbar mt"><button class="primary">${u ? "Save" : "Add user"}</button><button type="button" id="cl">Cancel</button><span class="grow"></span>
          ${u ? '<button type="button" class="danger" id="del">Delete</button>' : ""}</div></form>`);
      $("#cl", m).onclick = closeModal;
      $("#uf", m).addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        if (u) { f.enabled = !!f.enabled; if (!f.password) delete f.password; await act(() => api("/users/" + encodeURIComponent(u.username), { method: "PUT", body: f }), "Saved"); }
        else await act(() => api("/users", { body: f }), "User added");
        closeModal(); route();
      });
      if (u) $("#del", m).onclick = async () => {
        if (!confirm(`Delete ${u.username}?`)) return;
        await act(() => api("/users/" + encodeURIComponent(u.username), { method: "DELETE" }), "Deleted"); closeModal(); route();
      };
    };
    if ($("#nu")) $("#nu").onclick = () => userDialog(null);
    $$("button[data-edit]").forEach((b) => (b.onclick = () => userDialog(d.users.find((u) => u.username === b.dataset.edit))));
    if ($("#mypw")) $("#mypw").onclick = (e) => {
      e.preventDefault();
      const m = modal(`<h3>Change your password</h3><form id="pf"><label>Current password <input name="current" type="password" required></label>
        <label>New password <input name="password" type="password" minlength="10" required></label>
        <div class="toolbar mt"><button class="primary">Change</button><button type="button" id="cl">Cancel</button></div></form>`);
      $("#cl", m).onclick = closeModal;
      $("#pf", m).addEventListener("submit", async (ev) => { ev.preventDefault(); await act(() => api("/me/password", { body: Object.fromEntries(new FormData(ev.target)) }), "Password changed"); closeModal(); });
    };
  } else if (tab === "tokens") {
    const tokens = await api("/releases/tokens");
    root.innerHTML = `<p class="muted">Bearer tokens for <code>/api/ci/*</code> (<code>scripts/rb-release.py</code>, the Software repository's release workflow).
        <b>publish</b> uploads releases, <b>deploy</b> rolls out and back; every token can read status. A token is shown once.</p>
      ${can("admin") ? `<form id="tk" class="card inline"><label>Name <input name="name" required placeholder="ci-publish"></label>
        <label class="inline"><input type="checkbox" name="scopes" value="publish" checked> publish</label>
        <label class="inline"><input type="checkbox" name="scopes" value="deploy"> deploy</label><button class="primary">Create token</button></form>` : ""}
      <div class="card flush mt">${table(tokens, [{ key: "name", render: (v) => `<b>${esc(v)}</b>` }, { key: "scopes", render: (v) => esc((v || []).join(", ")) },
        { key: "created_at", label: "Created" }, { key: "last_used_at", label: "Last used", render: (v) => esc(ago(v)) },
        { key: "revoked_at", label: "", right: true, render: (v, r) => v ? '<span class="st mute">Revoked</span>' : can("admin") ? `<button class="sm danger" data-revoke="${esc(r.name)}">Revoke</button>` : "" }],
        { pad: true, empty: "No tokens." })}</div>`;
    if ($("#tk")) $("#tk").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const x = await act(() => api("/releases/tokens", { body: { name: fd.get("name"), scopes: fd.getAll("scopes") } }));
      const m = modal(`<h3>Token ${esc(x.name)}</h3><p>Copy it now; it is not shown again. Store it as the CI secret <code>RB_API_TOKEN</code>.</p><pre>${esc(x.token)}</pre>
        <div class="toolbar mt"><button id="cp">Copy</button><span class="grow"></span><button id="cl" class="primary">Done</button></div>`);
      $("#cp", m).onclick = () => navigator.clipboard.writeText(x.token).then(() => toast("Copied"));
      $("#cl", m).onclick = () => { closeModal(); route(); };
    });
    $$("button[data-revoke]").forEach((b) => (b.onclick = async () => {
      if (!confirm(`Revoke token ${b.dataset.revoke}? CI using it stops working.`)) return;
      await act(() => api("/releases/tokens/revoke", { body: { name: b.dataset.revoke } }), "Revoked"); route();
    }));
  } else if (tab === "staff") {
    const a = await api("/access");
    root.innerHTML = `<p class="muted">Logins for the kiosk's staff screens (<code>POST api/kiosk/authenticate</code>). While the list is empty, any non-empty
        user name and password is accepted.</p>
      ${can() ? `<form id="sf" class="card inline"><label>User name <input name="username" required></label><label>Password <input name="password" type="password"></label>
        <label class="inline"><input type="checkbox" name="active" checked> active</label><button class="primary">Save</button></form>` : ""}
      <div class="card flush mt">${table(a.staff, [{ key: "username", render: (v) => `<b>${esc(v)}</b>` },
        { key: "active", label: "Status", render: (v) => v ? '<span class="st ok"><span class="dot"></span>Active</span>' : '<span class="st mute">Disabled</span>' },
        { key: "_", label: "", right: true, render: (v, r) => can() ? `<button class="sm" data-tog="${esc(r.username)}" data-on="${r.active ? "" : "1"}">${r.active ? "Disable" : "Enable"}</button> <button class="sm danger" data-del="${esc(r.username)}">Delete</button>` : "" }],
        { pad: true, empty: "No staff logins: every user name and password is accepted." })}</div>`;
    if ($("#sf")) $("#sf").addEventListener("submit", async (e) => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.active = !!f.active;
      await act(() => api("/staff", { body: f }), "Saved"); route();
    });
    $$("button[data-tog]").forEach((b) => (b.onclick = async () => { await act(() => api("/staff", { body: { username: b.dataset.tog, active: !!b.dataset.on } }), "Saved"); route(); }));
    $$("button[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm(`Delete ${b.dataset.del}?`)) return; await act(() => api("/staff/delete", { body: { username: b.dataset.del } }), "Deleted"); route(); }));
  } else {
    const a = await api("/access");
    root.innerHTML = `<div class="card"><div class="kv">
        <div>Cloud API keys</div><div>${a.cloud_api.enforced ? '<span class="st ok"><span class="dot"></span>Enforced</span>' : '<span class="st warn"><span class="dot warn"></span>Not enforced</span>'}
          · ${a.cloud_api.keys.length} configured ${a.cloud_api.keys.length ? `<code>${esc(a.cloud_api.keys.join(", "))}</code>` : ""}</div>
        <div>MQTT client certificates</div><div>${a.mqtt_client_cert ? "Required" : '<span class="st warn"><span class="dot warn"></span>Not required</span>'} · ${num(a.iot_certs.active)} active, ${num(a.iot_certs.revoked)} revoked
          (<a class="link" href="#/provisioning">manage</a>)</div>
        <div>Update Service credentials</div><div>${a.update_service_check ? "Checked (UPDATE_SERVICE_KEY1/2)" : '<span class="st warn"><span class="dot warn"></span>Every store accepted</span>'}</div></div>
      <p class="muted" style="margin-bottom:0">These come from <code>.env</code>; change them there and restart the services.</p></div>`;
  }
};

// ----------------------------------------------------------------------------------- provisioning
routes.provisioning = async (tab = "kiosks") => {
  const d = await api("/provisioning");
  const tabs = [["kiosks", "New kiosks"], ["certificates", "IoT certificates"], ["installer", "Installer sessions"]];
  view().innerHTML = head("Provisioning", "Setting up new kiosks and pointing existing ones at this backend",
    `<a class="button" href="/api/provisioning/ca.crt">Backend CA</a>${can() ? '<button class="primary" id="rp">Reprovision file...</button>' : ""}`) +
    `<div class="tabs">${tabs.map(([t, l]) => `<a href="#/provisioning/${t}" class="${t === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="pv"></div>`;
  if ($("#rp")) $("#rp").onclick = () => reprovisionDialog("");
  const root = $("#pv");
  if (tab === "kiosks") {
    root.innerHTML = `<div class="grid2"><form id="isf" class="card"><div class="card-h"><h2>Installer settings</h2></div>
        <p class="muted">The <code>settings.json</code> for <code>provisioning/installer/Install-Kiosk.ps1</code>. Put it in the installer payload with the kiosk's
        reprovision file (<code>kiosk-&lt;id&gt;.json</code>) and a data zip.</p>
        <label>Store ID <input name="id" required type="number"></label>
        <label>Kiosk type <select name="kiosk_type"><option>R630</option><option>R620</option><option>R610</option></select></label>
        <label>Data zip file name <input name="data_zip" value="data.zip"></label>
        <label class="inline"><input type="checkbox" name="test_mode"> test mode (VM or Windows Sandbox, no reboot)</label>
        <div class="toolbar mt"><button class="primary">Download settings.json</button><button type="button" id="rp2">Reprovision file...</button></div></form>
      <div class="card"><div class="card-h"><h2>Backend address</h2></div><div class="kv">
        <div>Public IP</div><div>${esc(d.backend_ip)}</div>
        <div>Hosts entries</div><div>${d.host_entries.length ? d.host_entries.map(esc).join("<br>") : '<span class="muted">from the public IP</span>'}</div></div>
        <p class="muted">Kiosks reach the services by these names: ${Object.keys(d.service_hosts || {}).length ? Object.values(d.service_hosts).map((h) => `<code>${esc(h)}</code>`).join(", ") : "see docs/deploy-linux.md"}.</p></div></div>
      <div class="card flush mt"><div class="card-h"><h2>Stores waiting for the kiosk installer</h2><a class="more" href="#/data/installer_kiosks">Edit →</a></div>
        ${table(d.installer_kiosks, [{ key: "id", label: "Store", render: (v) => `<b>${esc(v)}</b>` }, { key: "banner_name", label: "Banner" }, { key: "market_name", label: "Market" },
          { key: "address", label: "Address", render: (v, r) => esc([v, r.city, r.state_name, r.zip].filter(Boolean).join(", ")) },
          { key: "pending", label: "", right: true, render: (v) => v ? '<span class="st warn"><span class="dot warn"></span>Pending</span>' : '<span class="st mute">Installed</span>' }], { pad: true, empty: "None. Add stores here to offer them in the Redbox kiosk installer's store list." })}</div>`;
    $("#rp2").onclick = () => reprovisionDialog("");
    $("#isf").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      location.href = `/api/provisioning/installer/${encodeURIComponent(f.get("id"))}.json?` + new URLSearchParams({ kiosk_type: f.get("kiosk_type"), data_zip: f.get("data_zip"), test_mode: f.get("test_mode") ? "true" : "false" });
    });
  } else if (tab === "certificates") {
    root.innerHTML = `<p class="muted">Client certificates the Update Client gets from the IoT certificate service for its MQTT connection. Revoking one makes rb-iot refuse it;
        the kiosk asks for a new one when it reconnects.</p>
      <div class="card flush">${table(d.certificates, [{ key: "kiosk_id", label: "Kiosk", render: (v) => `<a class="link" href="#/kiosk/${esc(v)}">${esc(v)}</a>` },
        { key: "certificate_id", label: "Certificate", render: (v) => `<code title="${esc(v)}">${short(v)}</code>` }, { key: "market" }, { key: "created_at", label: "Issued" },
        { key: "revoked_at", label: "", right: true, render: (v, r) => v ? `<span class="st mute">Revoked ${esc(ago(v))}</span>` : can() ? `<button class="sm danger" data-rev="${esc(r.certificate_id)}">Revoke</button>` : '<span class="st ok">Active</span>' }],
        { pad: true, empty: "No certificates issued yet." })}</div>
      <div class="card flush mt"><div class="card-h"><h2>Update Client registrations</h2></div>${table(d.registrations, [{ key: "kiosk_id", label: "Kiosk" }, { key: "ucs_version", label: "Version" },
        { key: "broker_registered", label: "Broker", render: (v, r) => v ? "registered" : esc(r.broker_reason || "no") }, { key: "registered_at", label: "Registered" },
        { key: "last_ping_at", label: "Last ping", render: (v) => esc(ago(v)) }], { pad: true, empty: "None." })}</div>`;
    $$("button[data-rev]").forEach((b) => (b.onclick = async () => {
      if (!confirm("Revoke this certificate?")) return;
      await act(() => api("/provisioning/certificates/revoke", { body: { certificate_id: b.dataset.rev } }), "Revoked"); route();
    }));
  } else {
    root.innerHTML = `<p class="muted">Sessions of the original Redbox store installer against the Update Service (it downloads the store's data zip and reports progress).</p>
      <div class="card flush">${table(d.installer_sessions, [{ key: "created_at", label: "Started" }, { key: "version" }, { key: "guid", render: (v) => `<code>${short(v)}</code>` },
        { key: "log_lines", label: "Log", right: true }], { onclick: true, pad: true, empty: "No installer has connected yet." })}</div>`;
    bindRows(root, d.installer_sessions, (r) => { const m = modal(`<h3>Installer session ${esc(short(r.guid))}</h3><pre>${esc(JSON.stringify(r.log, null, 2))}</pre><div class="toolbar mt"><button id="cl">Close</button></div>`); $("#cl", m).onclick = closeModal; });
  }
};

// -------------------------------------------------------------------------------------- audit log
routes.audit = async () => {
  view().innerHTML = head("Audit log", "Every change made in the dashboard, and sign-ins") + `
    <div class="toolbar"><div class="search grow">${ic("search")}<input id="aq" placeholder="User, page or path"></div>
      <select id="ak"><option value="audit">changes</option><option value="dashboard">sign-ins and dashboard events</option></select></div><div class="card flush" id="at"></div>`;
  const load = async () => {
    const p = new URLSearchParams({ source: $("#ak").value, limit: "300" });
    if ($("#aq").value) p.set("q", $("#aq").value);
    const rows = await api("/events?" + p);
    $("#at").innerHTML = table(rows, [{ key: "created_at", label: "When", render: (v) => `<span class="dim">${esc(fmtDate(v))}</span>` },
      { key: "payload", label: "Who", render: (v) => `<b>${esc((v && (v.user || v.username || v.by)) || "-")}</b>${v && v.role ? ` <span class="dim">${esc(v.role)}</span>` : ""}` },
      { key: "kind", label: "What", wrap: true, render: (v, r) => `<code>${esc(v)}</code>` },
      { key: "payload", label: "Result", right: true, render: (v, r) => r.source === "audit" ? (v.status < 400 ? `<span class="st mute">${esc(v.status)}</span>` : `<span class="st bad">${esc(v.status)}</span>`)
        : r.kind === "login-failed" ? '<span class="st bad"><span class="dot bad"></span>failed</span>' : "" }], { pad: true, empty: "Nothing recorded yet." });
  };
  $("#aq").addEventListener("keydown", (e) => e.key === "Enter" && load()); $("#ak").onchange = load;
  await load();
};

// ---------------------------------------------------------------------------------- event tickets
routes.tickets = async (tab = "ticket_events") => {
  const tabs = [["ticket_events", "Events"], ["ticket_productions", "Productions"], ["ticket_groups", "Ticket groups"], ["ticket_group_prices", "Group prices"],
    ["ticket_price_codes", "Price codes"], ["ticket_orders", "Orders"], ["ticket_locks", "Seat locks"], ["ticket_delivery_methods", "Delivery"], ["ticket_notifications", "Notifications"]];
  view().innerHTML = head("Event tickets", "The kiosk's ticket sales: events, productions, seats and orders") +
    `<div class="tabs">${tabs.map(([t, l]) => `<a href="#/tickets/${t}" class="${t === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="tv"></div>`;
  await editorView(tab, $("#tv"));
};

// ------------------------------------------------------------------------------------------ boot
(async () => {
  try { await api("/me"); start(); }
  catch {
    showLogin();
    try { const s = await api("/status"); if (!s.configured) $("#login-error").textContent = "No admin password is configured yet: run scripts\\rb.ps1 set-admin-password."; } catch { /* ignore */ }
  }
})();
