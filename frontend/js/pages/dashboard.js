import { api, esc, num, pct, ms, mbps, ico, lineChart, donut, COLORS, duration, bytes, ago, $, $$, on, toast, toastErr, modal, h } from "../lib.js";
import { t } from "../i18n.js";
import { loadPrefs, savePrefs } from "../theme.js";
import { brandIcon } from "../brands.js";

export const title = "Panel";
export const subtitle = "Resumen en tiempo real de tu red";

const RANGES = [[1, "1 h"], [24, "24 h"], [168, "7 días"]];

// Catálogo de widgets: id → {título, tamaño (columnas de 4), render}
const W = {
  st_queries: { name: "Consultas", size: 1, kind: "stat" },
  st_blocked: { name: "Bloqueadas", size: 1, kind: "stat" },
  st_response: { name: "Respuesta media", size: 1, kind: "stat" },
  st_latency: { name: "Latencia Internet", size: 1, kind: "stat" },
  st_devices: { name: "Dispositivos en línea", size: 1, kind: "stat" },
  st_domains: { name: "Dominios en listas", size: 1, kind: "stat" },
  st_qps: { name: "Consultas/seg", size: 1, kind: "stat" },
  st_upstream: { name: "DNS upstream", size: 1, kind: "stat" },
  st_uptime: { name: "Activo desde", size: 1, kind: "stat" },
  st_speed: { name: "Última velocidad", size: 1, kind: "stat" },
  st_threats: { name: "Amenazas (24 h)", size: 1, kind: "stat" },
  st_cache: { name: "Aciertos de caché", size: 1, kind: "stat" },
  timeline: { name: "Gráfico de consultas", size: 3 },
  donut: { name: "Tipo de respuesta", size: 1 },
  top_domains: { name: "Dominios más consultados", size: 2 },
  top_blocked: { name: "Más bloqueados", size: 2 },
  top_clients: { name: "Dispositivos más activos", size: 2 },
  live_devices: { name: "Dispositivos conectados", size: 2 },
  quick: { name: "Acciones rápidas", size: 2 },
  connection: { name: "Conexión", size: 1 },
  system: { name: "Sistema", size: 2 },
  events: { name: "Eventos recientes", size: 1 },
};
const DEFAULT_ORDER = ["st_queries", "st_blocked", "st_response", "st_latency", "st_devices", "st_domains", "st_qps", "st_uptime",
  "timeline", "donut", "top_domains", "top_blocked", "live_devices", "quick", "connection", "events", "system", "top_clients"];

export async function render(el, app) {
  let hours = 24;
  let editing = false;
  const charts = [];
  const prefs = loadPrefs();
  let layout = { order: DEFAULT_ORDER, hidden: [], ...(prefs.dashboard || {}) };
  layout.order = layout.order.filter((id) => W[id]);
  let data = null;

  el.innerHTML = `<div id="dash-alerts" class="col mb"></div>
    <div class="row mb" id="dash-head"><div class="btn-group" id="range">${RANGES.map(([v, l]) => `<button data-h="${v}" class="${v === hours ? "active" : ""}">${t(l)}</button>`).join("")}</div>
      <span class="grow"></span><button class="btn sm" id="edit">${ico("edit")}${t("Editar panel")}</button></div>
    <div id="edit-bar"></div><div class="dash" id="dash"></div>`;
  const dash = el.querySelector("#dash");

  on(el, "click", "[data-h]", (e, b) => { hours = Number(b.dataset.h); $$("#range button", el).forEach((x) => x.classList.toggle("active", x === b)); load(); });
  el.querySelector("#edit").onclick = () => setEditing(!editing);

  function save() {
    const p = loadPrefs();
    p.dashboard = layout;
    savePrefs(p);
    api.put("/system/prefs", { ui: { dashboard: layout } }).catch(() => {});
  }

  function setEditing(v) {
    editing = v;
    el.querySelector("#edit").innerHTML = editing ? `${ico("check")}${t("Terminar")}` : `${ico("edit")}${t("Editar panel")}`;
    el.querySelector("#edit-bar").innerHTML = editing ? `<div class="edit-bar">${ico("edit")}<span class="small grow">${t("Arrastra las tarjetas para moverlas, pulsa ✕ para quitarlas.")}</span>
      <button class="btn sm primary" id="add-w">${ico("plus")}${t("Añadir tarjeta")}</button><button class="btn sm ghost" id="reset-w">${t("Restablecer")}</button></div>` : "";
    if (editing) {
      el.querySelector("#add-w").onclick = addDialog;
      el.querySelector("#reset-w").onclick = () => { layout = { order: [...DEFAULT_ORDER], hidden: [] }; save(); draw(); };
    }
    dash.classList.toggle("editing", editing);
    draw();
  }

  function addDialog() {
    const avail = Object.keys(W).filter((id) => !layout.order.includes(id) || layout.hidden.includes(id));
    if (!avail.length) return toast(t("Ya están todas las tarjetas en el panel"), "info");
    const m = modal({ title: t("Añadir tarjeta"), body: `<div class="col">${avail.map((id) => `<button class="btn" data-add="${id}">${ico("plus")}${t(W[id].name)}</button>`).join("")}</div>` });
    $$("[data-add]", m.body).forEach((b) => b.onclick = () => {
      const id = b.dataset.add;
      layout.hidden = layout.hidden.filter((x) => x !== id);
      if (!layout.order.includes(id)) layout.order.push(id);
      save(); m.close(); draw();
    });
  }

  // ---------------------------------------------------------------- arrastrar y soltar
  let dragId = null;
  dash.addEventListener("dragstart", (e) => {
    const w = e.target.closest(".w"); if (!w || !editing) return;
    dragId = w.dataset.id; w.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
  });
  dash.addEventListener("dragend", () => { $$(".w", dash).forEach((x) => x.classList.remove("dragging", "drop-before", "drop-after")); dragId = null; });
  dash.addEventListener("dragover", (e) => {
    if (!dragId) return;
    e.preventDefault();
    const w = e.target.closest(".w"); $$(".w", dash).forEach((x) => x.classList.remove("drop-before", "drop-after"));
    if (!w || w.dataset.id === dragId) return;
    const r = w.getBoundingClientRect();
    w.classList.add(e.clientX < r.left + r.width / 2 ? "drop-before" : "drop-after");
  });
  dash.addEventListener("drop", (e) => {
    e.preventDefault();
    const w = e.target.closest(".w"); if (!w || !dragId || w.dataset.id === dragId) return;
    const after = w.classList.contains("drop-after");
    const order = layout.order.filter((x) => x !== dragId);
    order.splice(order.indexOf(w.dataset.id) + (after ? 1 : 0), 0, dragId);
    layout.order = order; save(); draw();
  });
  on(dash, "click", "[data-rm]", (e, b) => { layout.hidden.push(b.dataset.rm); save(); draw(); });
  on(dash, "click", "[data-mv]", (e, b) => {
    const [id, dir] = b.dataset.mv.split("|"); const i = layout.order.indexOf(id); const j = i + Number(dir);
    if (j < 0 || j >= layout.order.length) return;
    [layout.order[i], layout.order[j]] = [layout.order[j], layout.order[i]]; save(); draw();
  });

  // ---------------------------------------------------------------- datos
  async function load() {
    const [s, sys, events, devs, sec] = await Promise.all([api.get(`/stats/summary?hours=${hours}`), api.get("/system"), api.get("/events?limit=8"),
      api.get("/devices").catch(() => ({ devices: [] })), api.get("/security").catch(() => ({ anomalies: [] }))]);
    data = { s, sys, events, devs: devs.devices, sec };
    renderAlerts(sys, s);
    draw();
  }

  function stat(l, v, sub, icon, tint) {
    return `<div class="card stat"><div class="ic tint-${tint}">${ico(icon)}</div><div class="truncate"><div class="v">${esc(v)}</div><div class="l">${esc(t(l))} · <span class="small">${esc(sub)}</span></div></div></div>`;
  }

  function widget(id) {
    const { s, sys, events, devs, sec } = data;
    const mon = sys.monitor?.results || {};
    const inet = mon["1.1.1.1"]?.avg ?? mon["8.8.8.8"]?.avg;
    switch (id) {
      case "st_queries": return stat("Consultas", num(s.total), `${s.clients} ${t("clientes")}`, "activity", "blue");
      case "st_blocked": return stat("Bloqueadas", num(s.blocked), pct(s.blocked_pct) + " " + t("del total"), "shield", "red");
      case "st_response": return stat("Respuesta media", ms(sys.dns.avg_response_ms), `${pct(sys.dns.cache.hit_ratio)} ${t("desde caché")}`, "zap", "green");
      case "st_latency": return stat("Latencia Internet", ms(inet), sys.monitor?.online === false ? t("Sin conexión") : `${t("router")} ${ms(mon.gateway?.avg)}`, "globe", "cyan");
      case "st_devices": return stat("Dispositivos en línea", `${devs.filter((d) => d.online).length}/${devs.length}`, `${devs.filter((d) => d.internet_blocked).length} ${t("pausados")}`, "devices", "violet");
      case "st_domains": return stat("Dominios en listas", num(s.domains_blocked), t("bloqueo activo"), "list", "violet");
      case "st_qps": return stat("Consultas/seg", String(sys.dns.qps ?? 0), `${num(sys.dns.total)} ${t("desde el arranque")}`, "gauge", "amber");
      case "st_upstream": return stat("DNS upstream", ms(s.avg_upstream_ms), sys.dns.upstreams?.[0]?.server || "—", "server", "blue");
      case "st_uptime": return stat("Activo desde", duration(sys.app_uptime), `${t("Sistema")}: ${duration(sys.uptime)}`, "clock", "green");
      case "st_cache": return stat("Aciertos de caché", pct(sys.dns.cache.hit_ratio), `${num(sys.dns.cache.entries)} ${t("entradas")}`, "server", "cyan");
      case "st_threats": return stat("Amenazas (24 h)", String(sec.anomalies.filter((a) => a.ts > Date.now() / 1000 - 86400).length), t("anomalías detectadas"), "alert", "red");
      case "st_speed": return `<div class="card stat" id="w-speed"><div class="ic tint-green">${ico("gauge")}</div><div><div class="v">—</div><div class="l">${t("Última velocidad")}</div></div></div>`;
      case "timeline": return `<div class="card"><header><h2>${ico("activity")}${t("Consultas DNS")}</h2></header>
        <div class="legend mb"><span><i style="background:${COLORS.blue}"></i>${t("Permitidas")}</span><span><i style="background:${COLORS.cyan}"></i>${t("Desde caché")}</span><span><i style="background:${COLORS.red}"></i>${t("Bloqueadas")}</span></div>
        <div class="chart"><canvas data-chart="timeline"></canvas></div></div>`;
      case "donut": return `<div class="card"><header><h2>${ico("shield")}${t("Tipo de respuesta")}</h2></header><div class="chart chart-sm"><canvas data-chart="donut"></canvas></div><div class="col mt small" data-legend></div></div>`;
      case "top_domains": return listCard("Dominios más consultados", "globe", s.top_domains, "domain", (r) => r.domain, "", (r) => `<a href="#" class="muted small" data-block="${esc(r.domain)}" title="${t("Bloquear")}">${ico("x")}</a>`);
      case "top_blocked": return listCard("Más bloqueados", "shield", s.top_blocked, "domain", (r) => r.domain, "red", (r) => `<a href="#" class="muted small" data-allow="${esc(r.domain)}" title="${t("Permitir")}">${ico("check")}</a>`);
      case "top_clients": return listCard("Dispositivos más activos", "devices", s.top_clients, "client", (r) => r.name === r.client ? r.client : `${r.name} (${r.client})`, "");
      case "live_devices": {
        const on = devs.filter((d) => d.online).slice(0, 10);
        return `<div class="card"><header><h3>${ico("devices")}${t("Dispositivos conectados")} <span class="live-dot"></span></h3><a class="btn sm ghost" href="#/devices">${t("Ver todos")}</a></header>
          <div class="col">${on.map((d) => `<div class="row" style="flex-wrap:nowrap">${brandIcon(d.brand, d.kind)}<div class="grow truncate"><b>${esc(d.name || d.friendly || d.hostname || d.vendor || d.ip)}</b><div class="small muted">${esc(d.ip || "")} · ${esc(d.brand_name || d.vendor || "")}</div></div>
          ${d.internet_blocked ? `<span class="badge err">${t("Pausado")}</span>` : `<span class="small muted">${ms(d.latency)}</span>`}</div>`).join("") || `<p class="muted small">${t("Sin dispositivos en línea")}</p>`}</div></div>`;
      }
      case "quick": return `<div class="card"><header><h3>${ico("zap")}${t("Acciones rápidas")}</h3></header><div class="grid g2">
        <button class="btn" data-q="family">${ico("pause")}${t("Pausa familiar 30 min")}</button><button class="btn" data-q="resume">${ico("play")}${t("Reanudar a todos")}</button>
        <button class="btn" data-q="scan">${ico("search")}${t("Escanear red")}</button><a class="btn" href="#/speed">${ico("gauge")}${t("Test de velocidad")}</a>
        <a class="btn" href="#/assistant">${ico("zap")}${t("Asistente")}</a><button class="btn" data-q="flush">${ico("trash")}${t("Vaciar caché DNS")}</button></div></div>`;
      case "connection": {
        const conn = sys.monitor;
        return `<div class="card"><header><h3>${ico("wifi")}${t("Conexión")}</h3></header>${conn?.results ? `<div class="col">${Object.entries(conn.results).map(([k, r]) => `<div class="row between"><span>${k === "gateway" ? t("Router") : esc(k)}</span><span><span class="dot dot-${r.loss >= 100 ? "err" : r.loss > 0 ? "warn" : "ok"}"></span> ${ms(r.avg)}</span></div>`).join("")}<div class="row between"><span>DNS</span><span>${ms(conn.dns_ms)}</span></div></div>` : `<p class="muted">${t("Midiendo…")}</p>`}</div>`;
      }
      case "system": {
        const mem = sys.memory;
        return `<div class="card"><header><h3>${ico("server")}${t("Sistema")}</h3><a class="btn sm ghost" href="#/settings">${t("Ajustes")}</a></header><dl class="kv">
          <dt>${t("Equipo")}</dt><dd>${esc(sys.hostname)} · ${esc(sys.ip || "")}</dd><dt>${t("Carga CPU")}</dt><dd>${sys.load.map((x) => x.toFixed(2)).join(" / ")} (${sys.cpus} CPU)</dd>
          <dt>${t("Memoria")}</dt><dd>${bytes(mem.total - mem.available)} / ${bytes(mem.total)}</dd><dt>${t("Disco libre")}</dt><dd>${bytes(sys.disk.free)}</dd>
          ${sys.temperature ? `<dt>${t("Temperatura")}</dt><dd>${sys.temperature} °C</dd>` : ""}<dt>Unbound</dt><dd>${sys.unbound.running ? `<span class="badge ok">${t("activo")}</span>` : `<span class="badge">${t("detenido")}</span>`}</dd></dl></div>`;
      }
      case "events": {
        const lvl = { info: "ok", warning: "warn", error: "err" };
        return `<div class="card"><header><h3>${ico("info")}${t("Eventos recientes")}</h3></header><div class="col small">${events.map((e) => `<div class="row" style="flex-wrap:nowrap;align-items:flex-start"><span class="dot dot-${lvl[e.level]}" style="margin-top:6px"></span><div><div>${esc(e.message)}</div><span class="muted">${ago(e.ts)}</span></div></div>`).join("") || `<p class="muted">${t("Sin eventos")}</p>`}</div></div>`;
      }
    }
    return "";
  }

  function listCard(name, icon, rows, key, label, cls, action) {
    const max = rows[0]?.n || 1;
    return `<div class="card"><header><h3>${ico(icon)}${t(name)}</h3></header><div class="bar-list">${rows.length ? rows.map((r) => `<div class="bar-item ${cls}" title="${esc(r[key])}"><span class="fill" style="width:${(r.n / max) * 100}%"></span>
      <span class="name">${esc(label(r))}</span><span class="row nowrap">${action ? action(r) : ""}<b>${num(r.n)}</b></span></div>`).join("") : `<p class="muted small">${t("Sin datos todavía")}</p>`}</div></div>`;
  }

  function draw() {
    if (!data) return;
    charts.forEach((c) => c.destroy()); charts.length = 0;
    const ids = layout.order.filter((id) => !layout.hidden.includes(id));
    dash.innerHTML = ids.map((id, i) => `<div class="w s${W[id].size}" data-id="${id}" draggable="${editing}">
      <div class="w-tools"><button data-mv="${id}|-1" title="${t("Mover antes")}">‹</button><button data-mv="${id}|1" title="${t("Mover después")}">›</button><button class="rm" data-rm="${id}" title="${t("Quitar")}">✕</button></div>
      ${widget(id)}</div>`).join("") || `<div class="w s4"><div class="card">${t("El panel está vacío. Pulsa «Editar panel» → «Añadir tarjeta».")}</div></div>`;
    const tl = data.s.timeline;
    const cv = dash.querySelector('[data-chart="timeline"]');
    if (cv) charts.push(lineChart(cv, { labels: tl.map((r) => r.t), stacked: true,
      xFmt: hours > 24 ? (x) => new Date(x * 1000).toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : undefined,
      series: [{ name: t("Bloqueadas"), color: COLORS.red, values: tl.map((r) => r.blocked || 0) }, { name: t("Caché"), color: COLORS.cyan, values: tl.map((r) => r.cached || 0) },
        { name: t("Permitidas"), color: COLORS.blue, values: tl.map((r) => r.total - (r.blocked || 0) - (r.cached || 0)) }] }));
    const dv = dash.querySelector('[data-chart="donut"]');
    if (dv) {
      const st = data.s.statuses.slice(0, 6);
      const colors = { 0: COLORS.blue, 1: COLORS.cyan, 10: COLORS.cyan, 2: COLORS.red, 3: COLORS.pink, 4: COLORS.amber, 5: COLORS.violet, 6: COLORS.green, 7: COLORS.green, 8: COLORS.slate };
      charts.push(donut(dv, st.map((x) => ({ value: x.n, color: colors[x.status] || COLORS.slate })), { center: [pct(data.s.blocked_pct), t("bloqueado")] }));
      dv.closest(".card").querySelector("[data-legend]").innerHTML = st.map((x) => `<div class="row between"><span><span class="dot" style="background:${colors[x.status] || COLORS.slate}"></span> ${esc(x.name)}</span><b>${num(x.n)}</b></div>`).join("");
    }
    const sp = dash.querySelector("#w-speed");
    if (sp) api.get("/speedtest/history?limit=1").then((r) => { if (r[0]) sp.querySelector(".v").textContent = `${mbps(r[0].download)}`; sp.querySelector(".l").textContent = r[0] ? `${t("Última velocidad")} · ↑${mbps(r[0].upload)} · ${ms(r[0].ping)}` : t("Sin tests"); }).catch(() => {});
  }

  on(dash, "click", "[data-block]", async (e, b) => { e.preventDefault(); try { await api.post("/rules", { kind: "deny", value: b.dataset.block }); toast(`${b.dataset.block} ${t("bloqueado")}`); load(); } catch (err) { toastErr(err); } });
  on(dash, "click", "[data-allow]", async (e, b) => { e.preventDefault(); try { await api.post("/rules", { kind: "allow", value: b.dataset.allow }); toast(`${b.dataset.allow} ${t("permitido")}`); load(); } catch (err) { toastErr(err); } });
  on(dash, "click", "[data-q]", async (e, b) => {
    try {
      const q = b.dataset.q;
      if (q === "family") { const r = await api.post("/family/pause", { minutes: 30 }); toast(`${t("Internet pausado en")} ${r.devices} ${t("dispositivos")}`, "info"); }
      if (q === "resume") { await api.post("/family/resume", {}); toast(t("Internet reanudado")); }
      if (q === "scan") { b.disabled = true; const r = await api.post("/devices/scan"); toast(`${r.found ?? ""} ${t("dispositivos en línea")}`); b.disabled = false; }
      if (q === "flush") { const r = await api.post("/dns/cache/flush"); toast(`${t("Caché vaciada")} (${num(r.cleared)})`); }
      load();
    } catch (err) { toastErr(err); }
  });

  function renderAlerts(sys, s) {
    const box = el.querySelector("#dash-alerts");
    const alerts = [];
    if (sys.dns.error) alerts.push(["err", t("El servidor DNS no está escuchando"), sys.dns.error]);
    if (sys.dns.fallback) alerts.push(["warn", t("Unbound no disponible"), t("Se está usando Cloudflare como DNS de respaldo.")]);
    if (!s.total) alerts.push(["info", t("Aún no hay tráfico DNS"), `${t("Configura tu router para que entregue")} ${sys.ip || ""} ${t("como servidor DNS, o activa el modo automático en «Red».")}`]);
    box.innerHTML = alerts.map(([k, ti, d]) => `<div class="alert ${k}">${ico(k === "info" ? "info" : "alert")}<div><b>${esc(ti)}</b><p class="small">${esc(d)}</p></div></div>`).join("");
  }

  await load();
  const timer = setInterval(() => { if (!editing) load().catch(() => {}); }, 15000);
  return () => { clearInterval(timer); charts.forEach((c) => c.destroy()); };
}
