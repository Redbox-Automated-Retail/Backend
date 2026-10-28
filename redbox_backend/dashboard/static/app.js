"use strict";
// Redbox admin dashboard: hash-routed single page over /api (see dashboard/server.py).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = () => $("#view");

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
  if (s < 60) return Math.round(s) + " s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
};
const pill = (text, cls) => `<span class="pill ${cls || ""}">${esc(text)}</span>`;
const short = (h) => (h ? esc(String(h).slice(0, 10)) : "");
const cell = (v) => {
  if (v === null || v === undefined) return '<span class="muted">-</span>';
  if (typeof v === "object") return `<code>${esc(JSON.stringify(v)).slice(0, 160)}</code>`;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(v)) return fmtDate(v);
  return esc(v);
};

function table(rows, cols, opts = {}) {
  if (!rows || !rows.length) return `<p class="muted">${esc(opts.empty || "Nothing here yet.")}</p>`;
  const head = cols.map((c) => `<th>${esc(c.label ?? c.key)}</th>`).join("");
  const body = rows.map((r, i) => `<tr class="${opts.onclick ? "click" : ""}" data-i="${i}">` +
    cols.map((c) => `<td class="${c.wrap ? "wrap" : ""}">${c.render ? c.render(r[c.key], r) : cell(r[c.key])}</td>`).join("") + "</tr>").join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function bindRows(root, rows, fn) { $$("tr.click", root).forEach((tr) => tr.addEventListener("click", () => fn(rows[+tr.dataset.i]))); }

function modal(html) { $("#modal-content").innerHTML = html; $("#modal").classList.remove("hidden"); return $("#modal-content"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

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
  try { const me = await api("/me"); $("#me").textContent = me.user; } catch { return; }
  $("#login").classList.add("hidden"); $("#shell").classList.remove("hidden");
  route();
}

// ----------------------------------------------------------------------------------------- router
const routes = {};
function route() {
  const hash = location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const [name, ...rest] = hash.split("/");
  const key = routes[hash] ? hash : name;
  $$(".side a").forEach((a) => a.classList.toggle("active", a.dataset.nav === hash || a.dataset.nav === name));
  const fn = routes[key] || routes.overview;
  view().innerHTML = '<p class="muted">Loading...</p>';
  fn(...(routes[hash] ? [] : rest)).catch((e) => { view().innerHTML = `<p class="error">${esc(e.message)}</p>`; });
}
window.addEventListener("hashchange", () => { closeModal(); route(); });
const title = (t) => { $("#page-title").textContent = t; document.title = t + " - Redbox Admin"; };

// --------------------------------------------------------------------------------------- overview
routes.overview = async () => {
  title("Overview");
  const d = await api("/overview");
  const c = d.counts;
  const stat = (n, l, href) => `<a class="card stat" href="${href}" style="text-decoration:none;color:inherit"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></a>`;
  view().innerHTML = `<div class="stats">
    ${stat(c.kiosks, "kiosks known", "#/kiosks")}${stat(c.mqtt_online, "connected over MQTT", "#/kiosks")}
    ${stat(c.recent, "seen in the last 20 min", "#/kiosks")}${stat(c.transactions_24h, "transactions (24 h)", "#/transactions")}
    ${stat(c.rentals_out, "discs rented out", "#/data/rentals")}${stat(c.discs, "discs in kiosks", "#/data/inventory")}
    ${stat(c.repositories, "update repositories", "#/updates")}${stat(c.filesets, "file sets", "#/filesets")}
    ${stat(c.events_1h, "events (1 h)", "#/logs")}</div>
    <h3>Latest events</h3>${eventsTable(d.events)}`;
};

function eventsTable(rows) {
  return table(rows, [
    { key: "created_at", label: "Time" }, { key: "kiosk_id", label: "Kiosk", render: (v) => v ? `<a href="#/kiosk/${esc(v)}">${esc(v)}</a>` : "-" },
    { key: "source", label: "Source" }, { key: "kind", label: "Kind" },
    { key: "payload", label: "Payload", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v)).slice(0, 400)}</code>` : "" }]);
}

// ----------------------------------------------------------------------------------------- kiosks
routes.kiosks = async () => {
  title("Kiosks");
  const rows = await api("/kiosks");
  view().innerHTML = `<div class="toolbar"><input id="kf" placeholder="Filter by id, market, city" class="grow">
    <a class="button" href="#/data/kiosks">Edit kiosk records</a></div><div id="kt"></div>`;
  const draw = () => {
    const f = $("#kf").value.toLowerCase();
    const list = rows.filter((r) => !f || JSON.stringify([r.kiosk_id, r.market, r.city, r.label]).toLowerCase().includes(f));
    $("#kt").innerHTML = table(list, [
      { key: "kiosk_id", label: "Kiosk" }, { key: "online", label: "Status", render: (v, r) => r.mqtt_connected ? pill("MQTT online", "ok") : v ? pill("recent", "warn") : pill("offline", "bad") },
      { key: "last_seen_at", label: "Last seen", render: (v) => ago(v) }, { key: "last_check_in", label: "Update Service check-in", render: (v) => ago(v) },
      { key: "market", label: "Market" }, { key: "label", label: "Location", render: (v, r) => esc([v, r.city, r.location_state].filter(Boolean).join(", ")) },
      { key: "ucs_version", label: "Update Client" },
      { key: "versions", label: "Versions (Kiosk Client report)", wrap: true, render: (v) => v ? `<code>${esc(Object.entries(v).slice(0, 4).map(([k, x]) => k + " " + x).join(", "))}</code>` : '<span class="muted">-</span>' },
      { key: "config_version", label: "Config ver." }, { key: "discs", label: "Discs" }, { key: "last_transaction", label: "Last transaction", render: (v) => v ? ago(v) : "-" },
    ], { onclick: true, empty: "No kiosk has contacted the backend yet." });
    bindRows($("#kt"), list, (r) => (location.hash = "#/kiosk/" + r.kiosk_id));
  };
  $("#kf").addEventListener("input", draw); draw();
};

routes.kiosk = async (id) => {
  title("Kiosk " + id);
  const d = await api("/kiosks/" + id);
  const k = d.kiosk;
  const kv = (o) => `<div class="kv">${Object.entries(o).map(([a, b]) => `<div>${esc(a)}</div><div>${b}</div>`).join("")}</div>`;
  const dev = d.device ? d.device.payload : null;
  view().innerHTML = `<div class="toolbar">
      <button data-cmd="Ping">Ping</button><button data-cmd="TriggerGetConfigChanges">Push configuration</button>
      <button data-cmd="TriggerReportFileSetVersions">Check file sets</button><button data-cmd="SyncTimestampAndTimezone">Sync clock</button>
      <button id="peek">Peek logs</button><button id="upl">Upload logs</button>
      <a class="button" href="#/data/inventory?kiosk=${esc(id)}">Inventory</a><a class="button" href="#/transactions?kiosk=${esc(id)}">Transactions</a>
      <a class="button" href="#/logs?kiosk=${esc(id)}">Events</a></div>
    <div class="grid2">
      <div class="card"><h3>Status</h3>${kv({
        "MQTT": d.mqtt ? pill("connected from " + d.mqtt.peer, "ok") : pill("not connected", "bad"),
        "Last seen": esc(ago(k.last_seen_at)), "Market": esc(k.market || "-"), "Banner": esc(k.banner || "-"), "Time zone": esc(k.timezone || "-"),
        "Update Client": esc(k.ucs_version || "-") + (d.registration && d.registration.last_ping_at ? `, last ping ${esc(ago(d.registration.last_ping_at))}` : ""),
        "Broker registered": d.registration ? esc(d.registration.broker_registered) : "-",
        "Update Service check-in": esc(d.store ? ago(d.store.last_check_in) : "never"),
        "Configuration": d.config ? `reported v${esc(d.config.reported_version)}, activated v${esc(d.config.activated_version)} (current v${esc(d.config_current)})` : `never pulled (current v${esc(d.config_current)})`,
        "Card reader": dev ? esc(`RBA ${dev.RBA || "-"}, serial ${dev.Serial || "-"}, error state ${dev.ErrorState}`) : "-",
        "Bluefin": d.bluefin.length ? esc(d.bluefin.map((b) => b.reader_serial + " " + b.status).join(", ")) : "-",
      })}</div>
      <div class="card"><h3>Versions reported by the Kiosk Client</h3>${k.info && k.info.Versions ? kv(Object.fromEntries(Object.entries(k.info.Versions).map(([a, b]) => [a, esc(b)]))) : '<p class="muted">No kioskversions report yet.</p>'}</div>
    </div>
    <h3>Legacy update repositories (updatemgrd)</h3>${table(d.repositories, [
      { key: "repo", label: "Repository" }, { key: "subscribed", label: "Subscribed" },
      { key: "current", label: "State", render: (v, r) => !r.subscribed ? pill("not subscribed") : !r.reported ? pill("never reported", "warn") : v ? pill("current", "ok") : pill("behind", "warn") },
      { key: "reported", label: "Kiosk head", render: (v, r) => short(v) + (r.reported_label ? " " + esc(r.reported_label) : "") },
      { key: "server_head", label: "Server head", render: short }, { key: "reported_at", label: "Reported" }])}
    <h3>File sets (Update Client / updatemgrd)</h3>${table(d.filesets, [
      { key: "fileset_id", label: "File set" }, { key: "name", label: "Name" }, { key: "channel", label: "Channel" },
      { key: "revision_id", label: "Revision" }, { key: "version", label: "Version" },
      { key: "state", label: "State", render: (v) => [pill("Active", "ok"), pill("InProgress", "warn"), pill("NeedsDependency", "bad"), pill("Error", "bad")][v] || esc(v) },
      { key: "updated_at", label: "Reported" }], { empty: "No file-set report from this kiosk." })}
    <h3>Deployments that apply to this kiosk</h3>${table(d.deployments, [
      { key: "id", label: "Deployment" }, { key: "fileset_id", label: "File set" }, { key: "revision_id", label: "Revision" },
      { key: "channel", label: "Channel" }, { key: "target_type", label: "Target" }, { key: "created_at", label: "Created" }])}
    <h3>Recent transactions</h3>${table(d.transactions, [{ key: "reference_number", label: "Reference" }, { key: "kind" }, { key: "status" }, { key: "amount" }, { key: "created_at", label: "Time" }])}
    <h3>Recent events</h3>${eventsTable(d.events)}`;
  $$("button[data-cmd]").forEach((b) => b.addEventListener("click", async () => {
    const cmds = await api("/jobs/commands"); const c = cmds[b.dataset.cmd];
    await act(() => api("/jobs/iot", { body: { target: id, command: b.dataset.cmd, version: c.version, payload: c.payload, wait: b.dataset.cmd === "Ping" ? 10 : 0 } }),
      (r) => `${b.dataset.cmd}: ${r.delivered} delivery` + (r.response !== undefined ? (r.response ? ", kiosk answered" : ", no answer") : ""));
  }));
  $("#peek").addEventListener("click", () => peekDialog(id));
  $("#upl").addEventListener("click", () => uploadDialog(id));
};

const FILE_TYPES = ["UpdateClientServiceLogs", "KioskClientServiceLogs", "KioskEngineLogs", "UpdateManagerLogs", "DeviceServiceLogs",
  "HALLogs", "ErrorLogs", "Configuration", "KioskEngineDataDirectory", "Certificates"];
function peekDialog(id) {
  const m = modal(`<h3>Peek files on kiosk ${esc(id)}</h3><label>File type <select id="pt">${FILE_TYPES.map((t) => `<option>${t}</option>`).join("")}</select></label>
    <label>Or any folder path <input id="pp" placeholder="C:\\ProgramData\\Redbox"></label>
    <button class="primary" id="go">Peek</button> <button id="cl">Close</button><div id="out" class="mt"></div>`);
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
    <button class="primary" id="go">Upload</button> <button id="cl">Close</button><div id="out" class="mt"></div>`);
  $("#cl", m).onclick = closeModal;
  $("#go", m).onclick = async () => {
    $("#out", m).textContent = "Waiting for the kiosk...";
    const names = $("#un", m).value.split(",").map((s) => s.trim()).filter(Boolean);
    const r = await act(() => api("/jobs/upload", { body: { kiosk_id: id, fileType: $("#ut", m).value, fileNames: names, zip: true } }));
    $("#out", m).innerHTML = `<pre>${esc(JSON.stringify(r.response, null, 2) || "no answer")}</pre><p>Stored as <code>${esc(r.s3Key)}</code>; see Remote jobs &gt; Uploaded files.</p>`;
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
  title("Pricing & promotions");
  const tabs = [["pricing_default", "Default rental"], ["pricing_rental", "Rental by price level"], ["pricing_purchase", "Purchase"],
    ["promo_codes", "Promo codes"], ["return_promo", "Return promo"], ["campaigns", "Campaigns"], ["subscription_plans", "Subscriptions"], ["ads", "Ads"]];
  view().innerHTML = `<div class="tabs">${tabs.map(([n, l], i) => `<button data-t="${n}" class="${i ? "" : "on"}">${esc(l)}</button>`).join("")}</div>
    <p class="muted">Kiosk Clients refresh pricing every 30 min-26 h and campaigns every 30 min; <a href="#/jobs">Remote jobs</a> can force a refresh.</p><div id="pt"></div>`;
  const show = (n) => { $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === n)); editorView(n, $("#pt")); };
  $$(".tabs button").forEach((b) => (b.onclick = () => show(b.dataset.t)));
  show(tabs[0][0]);
};

// ----------------------------------------------------------------------------------- transactions
routes.transactions = async () => {
  title("Transactions");
  const q = query();
  view().innerHTML = `<div class="toolbar"><input id="xq" placeholder="Reference, e-mail, CPN, last four" class="grow" value="${esc(q.q || "")}">
    <input id="xk" placeholder="Kiosk" size="8" value="${esc(q.kiosk || "")}">
    <select id="xkind"><option value="">any kind</option><option>authorize</option><option>giftcard</option><option>pickup</option><option>reservation</option><option>offline</option></select>
    <select id="xs"><option value="">any status</option><option>authorized</option><option>declined</option><option>reconciled</option></select>
    <button id="xg">Search</button></div>
    <div class="tabs"><a class="button" href="#/data/rentals">Rentals</a><a class="button" href="#/data/returns">Returns</a><a class="button" href="#/data/reservations">Reservations</a><a class="button" href="#/data/cards">Cards</a></div><div id="xt"></div>`;
  const load = async () => {
    const p = new URLSearchParams();
    if ($("#xq").value) p.set("q", $("#xq").value);
    if ($("#xk").value) p.set("kiosk_id", $("#xk").value);
    if ($("#xkind").value) p.set("kind", $("#xkind").value);
    if ($("#xs").value) p.set("status", $("#xs").value);
    const d = await api("/transactions?" + p);
    $("#xt").innerHTML = `<p class="muted">${esc(d.total)} transactions</p>` + table(d.rows, [
      { key: "reference_number", label: "Reference" }, { key: "created_at", label: "Time" }, { key: "kiosk_id", label: "Kiosk" },
      { key: "kind", label: "Kind" }, { key: "status", label: "Status", render: (v) => pill(v, v === "declined" ? "bad" : v === "reconciled" ? "ok" : "warn") },
      { key: "amount", label: "Amount" }, { key: "items", label: "Items" }, { key: "last_four", label: "Card", render: (v, r) => esc((r.is_gift_card ? "gift " : "") + (v ? "..." + v : "")) },
      { key: "email", label: "Customer" }, { key: "errors", label: "Errors", wrap: true, render: (v) => v && v.length ? `<code>${esc(JSON.stringify(v))}</code>` : "" }], { onclick: true });
    bindRows($("#xt"), d.rows, (r) => txDialog(r.id));
  };
  $("#xg").onclick = load; $("#xq").addEventListener("keydown", (e) => e.key === "Enter" && load());
  await load();
};
async function txDialog(id) {
  const d = await api("/transactions/" + id);
  const m = modal(`<h3>Transaction ${esc(d.transaction.reference_number)}</h3>
    ${table(d.rentals, [{ key: "barcode" }, { key: "product_id", label: "Product" }, { key: "product_type", label: "Type" }, { key: "status" }, { key: "vend_status", label: "Vend" }, { key: "rented_at", label: "Rented" }, { key: "returned_at", label: "Returned" }, { key: "extra_charge", label: "Extra" }])}
    <h3>Returns</h3>${table(d.returns, [{ key: "barcode" }, { key: "kiosk_id", label: "Kiosk" }, { key: "return_date", label: "Date" }, { key: "deck" }, { key: "slot" }])}
    <h3>Customer / card</h3><pre>${esc(JSON.stringify({ customer: d.customer, card: d.card, promos: d.promos }, null, 2))}</pre>
    <h3>Authorize request</h3><pre>${esc(JSON.stringify(d.transaction.request, null, 2))}</pre>
    <h3>Reconcile</h3><pre>${esc(JSON.stringify(d.transaction.reconcile, null, 2))}</pre><div class="mt"><button id="cl">Close</button></div>`);
  $("#cl", m).onclick = closeModal;
}

// ---------------------------------------------------------------------------------------- updates
routes.updates = async () => {
  title("Update Service (updatemgrd)");
  const [rp, st] = await Promise.all([api("/updates/repos"), api("/updates/stores")]);
  view().innerHTML = `<h3>Repositories</h3><p class="muted">Kiosks poll every 15 minutes (<code>u p</code>); a kiosk without a repository gets a full snapshot, one at an older revision gets the later revisions.</p>
    ${table(rp, [{ key: "name", label: "Repository" }, { key: "revisions", label: "Revisions" }, { key: "head", label: "Head", render: short }, { key: "label", label: "Label" },
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
    <form id="pub" class="card"><b>Publish a new revision</b><p class="muted">Unchanged files are skipped; changed ones go out as VCDIFF deltas when smaller. Sub-folders are kept when you pick a folder.</p>
      <label>Target folder on the kiosk <input name="target_path" required value="${esc(d.files[0] ? d.files[0].target_path : "C:\\Program Files\\Redbox\\REDS\\")}"></label>
      <label>Label <input name="label" placeholder="${esc(r.name)}-release"></label>
      <label>Files <input type="file" name="files" multiple required></label>
      <label><input type="checkbox" id="folder"> pick a whole folder</label>
      <button class="primary">Publish</button></form>
    <div class="toolbar mt"><button id="snap">Rebuild snapshot</button><label><input type="checkbox" id="auto" ${r.auto_subscribe ? "checked" : ""}> offer to every store automatically</label><span class="grow"></span><button id="cl">Close</button></div>
    <h3>Latest revisions</h3>${table(d.revisions, [{ key: "seq" }, { key: "hash", render: short }, { key: "label" }, { key: "changes" }, { key: "seeds" }, { key: "deltas" }, { key: "created_at", label: "Added" }])}
    <h3>Files at head (${d.files.length})</h3>${table(d.files.slice(0, 200), [{ key: "target_path", label: "Folder" }, { key: "target_name", label: "File" }, { key: "version_hash", label: "SHA-1", render: short }])}`);
  $("#cl", m).onclick = closeModal;
  $("#folder", m).onchange = (e) => { $("input[type=file]", m).webkitdirectory = e.target.checked; };
  $("#auto", m).onchange = (e) => act(() => api("/updates/repos/" + encodeURIComponent(r.name), { method: "PUT", body: { auto_subscribe: e.target.checked } }), "Saved");
  $("#snap", m).onclick = () => act(() => api(`/updates/repos/${encodeURIComponent(r.name)}/snapshot`, { body: {} }), (x) => `Snapshot: ${x.files} files, ${x.problems.length} problems`);
  $("#pub", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target); fd.delete("files");
    for (const f of $("input[type=file]", m).files) fd.append("files", f, f.webkitRelativePath ? f.webkitRelativePath.split("/").slice(1).join("/") : f.name);
    await act(() => api(`/updates/repos/${encodeURIComponent(r.name)}/publish`, { form: fd }), (x) => x.revision ? `Published revision ${x.revision.slice(0, 10)} (${x.files} files)` : "No file changed");
    closeModal(); routes.updates();
  });
}

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
  title("Remote jobs");
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
      { key: "payload", label: "Payload", wrap: true, render: (v) => v ? `<code>${esc(JSON.stringify(v)).slice(0, 300)}</code>` : "" }], { onclick: true }) +
      (got.length === 200 ? '<button id="more" class="mt">Older</button>' : "");
    bindRows($("#lt"), rows, (r) => { const m = modal(`<h3>${esc(r.source)} / ${esc(r.kind)}</h3><p class="muted">${esc(fmtDate(r.created_at))}, kiosk ${esc(r.kiosk_id ?? "-")}</p><pre>${esc(JSON.stringify(r.payload, null, 2))}</pre><button id="cl" class="mt">Close</button>`); $("#cl", m).onclick = closeModal; });
    if ($("#more")) $("#more").onclick = () => load(true);
  };
  $("#lg").onclick = () => load(); $("#lkind").onchange = () => load(); $("#lq").addEventListener("keydown", (e) => e.key === "Enter" && load());
  clearInterval(routes.logs.timer);
  routes.logs.timer = setInterval(() => { if ($("#lauto") && $("#lauto").checked && location.hash.startsWith("#/logs")) load(); }, 5000);
  await load();
};

// ------------------------------------------------------------------------------------------ boot
(async () => {
  try { await api("/me"); start(); }
  catch {
    showLogin();
    try { const s = await api("/status"); if (!s.configured) $("#login-error").textContent = "No admin password is configured yet: run scripts\\rb.ps1 set-admin-password."; } catch { /* ignore */ }
  }
})();
