import { api, esc, num, pct, ms, ico, lineChart, donut, COLORS, PALETTE, duration, bytes, ago, $, on, toast, toastErr, h } from "../lib.js";

export const title = "Panel";
export const subtitle = "Resumen en tiempo real de tu red";

const RANGES = [[1, "1 h"], [24, "24 h"], [168, "7 días"]];

export async function render(el) {
  let hours = 24;
  const charts = [];
  el.innerHTML = `
    <div id="dash-alerts" class="col mb"></div>
    <div class="grid g4" id="stats"></div>
    <div class="grid g3 mt">
      <div class="card span2"><header><h2>${ico("activity")}Consultas DNS</h2>
        <div class="btn-group" id="range">${RANGES.map(([v, l]) => `<button data-h="${v}" class="${v === hours ? "active" : ""}">${l}</button>`).join("")}</div></header>
        <div class="legend mb"><span><i style="background:${COLORS.blue}"></i>Permitidas</span><span><i style="background:${COLORS.cyan}"></i>Desde caché</span><span><i style="background:${COLORS.red}"></i>Bloqueadas</span></div>
        <div class="chart"><canvas id="c-timeline"></canvas></div></div>
      <div class="card"><header><h2>${ico("shield")}Tipo de respuesta</h2></header>
        <div class="chart chart-sm"><canvas id="c-donut"></canvas></div><div id="donut-legend" class="col mt small"></div></div>
    </div>
    <div class="grid g3 mt">
      <div class="card"><header><h3>${ico("globe")}Dominios más consultados</h3></header><div id="top-domains" class="bar-list"></div></div>
      <div class="card"><header><h3>${ico("shield")}Más bloqueados</h3></header><div id="top-blocked" class="bar-list"></div></div>
      <div class="card"><header><h3>${ico("devices")}Dispositivos más activos</h3></header><div id="top-clients" class="bar-list"></div></div>
    </div>
    <div class="grid g3 mt">
      <div class="card"><header><h3>${ico("wifi")}Conexión</h3><a href="#/monitor" class="btn sm ghost">Ver monitor</a></header><div id="conn"></div></div>
      <div class="card"><header><h3>${ico("server")}Sistema</h3></header><div id="sys"></div></div>
      <div class="card"><header><h3>${ico("info")}Eventos recientes</h3></header><div id="events" class="col small"></div></div>
    </div>`;

  on(el, "click", "[data-h]", (e, b) => { hours = Number(b.dataset.h); el.querySelectorAll("#range button").forEach((x) => x.classList.toggle("active", x === b)); load(); });
  on(el, "click", "[data-block]", async (e, b) => {
    e.preventDefault();
    try { await api.post("/rules", { kind: "deny", value: b.dataset.block }); toast(`${b.dataset.block} bloqueado`); load(); } catch (err) { toastErr(err); }
  });
  on(el, "click", "[data-allow]", async (e, b) => {
    e.preventDefault();
    try { await api.post("/rules", { kind: "allow", value: b.dataset.allow }); toast(`${b.dataset.allow} permitido`); load(); } catch (err) { toastErr(err); }
  });

  async function load() {
    const [s, sys, events] = await Promise.all([api.get(`/stats/summary?hours=${hours}`), api.get("/system"), api.get("/events?limit=8")]);
    renderAlerts(sys);
    const mon = sys.monitor?.results || {};
    const inet = mon["1.1.1.1"]?.avg ?? mon["8.8.8.8"]?.avg;
    const cards = [
      ["Consultas", num(s.total), `${s.clients} clientes`, "activity", "blue"],
      ["Bloqueadas", num(s.blocked), pct(s.blocked_pct) + " del total", "shield", "red"],
      ["Respuesta media", ms(sys.dns.avg_response_ms), `${pct(sys.dns.cache.hit_ratio)} desde caché`, "zap", "green"],
      ["Latencia Internet", ms(inet), sys.monitor?.online === false ? "Sin conexión" : `router ${ms(mon.gateway?.avg)}`, "globe", "cyan"],
      ["Dominios en listas", num(s.domains_blocked), `${num(sys.lists?.allow || 0)} permitidos manualmente`, "list", "violet"],
      ["Consultas/seg", (sys.dns.qps ?? 0).toLocaleString("es"), `${num(sys.dns.total)} desde el arranque`, "gauge", "amber"],
      ["DNS upstream", ms(s.avg_upstream_ms), sys.dns.upstreams?.[0]?.server || "—", "server", "blue"],
      ["Activo desde", duration(sys.app_uptime), `Sistema: ${duration(sys.uptime)}`, "clock", "green"],
    ];
    el.querySelector("#stats").innerHTML = cards.map(([l, v, sub, icon, tint]) => `
      <div class="card stat"><div class="ic tint-${tint}">${ico(icon)}</div><div class="truncate"><div class="v">${esc(v)}</div><div class="l">${esc(l)} · <span class="small">${esc(sub)}</span></div></div></div>`).join("");

    charts.forEach((c) => c.destroy()); charts.length = 0;
    const tl = s.timeline;
    charts.push(lineChart(el.querySelector("#c-timeline"), {
      labels: tl.map((r) => r.t), stacked: true,
      xFmt: hours > 24 ? (t) => new Date(t * 1000).toLocaleDateString("es", { weekday: "short", day: "numeric" }) : undefined,
      series: [
        { name: "Bloqueadas", color: COLORS.red, values: tl.map((r) => r.blocked || 0) },
        { name: "Caché", color: COLORS.cyan, values: tl.map((r) => r.cached || 0) },
        { name: "Permitidas", color: COLORS.blue, values: tl.map((r) => r.total - (r.blocked || 0) - (r.cached || 0)) },
      ],
    }));
    const st = s.statuses.slice(0, 6);
    const colors = { 0: COLORS.blue, 1: COLORS.cyan, 10: COLORS.cyan, 2: COLORS.red, 3: COLORS.pink, 4: COLORS.amber, 5: COLORS.violet, 6: COLORS.green, 7: COLORS.green, 8: COLORS.slate, 9: COLORS.pink };
    charts.push(donut(el.querySelector("#c-donut"), st.map((x) => ({ value: x.n, color: colors[x.status] || COLORS.slate })), { center: [pct(s.blocked_pct), "bloqueado"] }));
    el.querySelector("#donut-legend").innerHTML = st.map((x) => `<div class="row between"><span><span class="dot" style="background:${colors[x.status] || COLORS.slate}"></span> ${esc(x.name)}</span><b>${num(x.n)}</b></div>`).join("");

    const bars = (rows, key, label, cls, action) => {
      if (!rows.length) return `<p class="muted small">Sin datos todavía</p>`;
      const max = rows[0].n;
      return rows.map((r) => `<div class="bar-item ${cls}" title="${esc(r[key])}"><span class="fill" style="width:${(r.n / max) * 100}%"></span>
        <span class="name">${esc(label(r))}</span><span class="row nowrap">${action ? action(r) : ""}<b>${num(r.n)}</b></span></div>`).join("");
    };
    el.querySelector("#top-domains").innerHTML = bars(s.top_domains, "domain", (r) => r.domain, "", (r) => `<a href="#" class="muted small" data-block="${esc(r.domain)}" title="Bloquear">${ico("x")}</a>`);
    el.querySelector("#top-blocked").innerHTML = bars(s.top_blocked, "domain", (r) => r.domain, "red", (r) => `<a href="#" class="muted small" data-allow="${esc(r.domain)}" title="Permitir">${ico("check")}</a>`);
    el.querySelector("#top-clients").innerHTML = bars(s.top_clients, "client", (r) => r.name === r.client ? r.client : `${r.name} (${r.client})`, "");

    const conn = sys.monitor;
    el.querySelector("#conn").innerHTML = conn?.results ? `
      <div class="col">${Object.entries(conn.results).map(([k, r]) => `<div class="row between"><span>${k === "gateway" ? "Router" : esc(k)}</span>
        <span><span class="dot dot-${r.loss >= 100 ? "err" : r.loss > 0 ? "warn" : "ok"}"></span> ${ms(r.avg)} · ${r.loss}% pérdida</span></div>`).join("")}
      <div class="row between"><span>Resolución DNS</span><span>${ms(conn.dns_ms)}</span></div>
      <p class="muted small">Última medición ${ago(conn.ts)}</p></div>` : `<p class="muted">Midiendo…</p>`;
    const mem = sys.memory;
    el.querySelector("#sys").innerHTML = `<dl class="kv">
      <dt>Equipo</dt><dd>${esc(sys.hostname)} · ${esc(sys.ip || "")}</dd>
      <dt>Carga CPU</dt><dd>${sys.load.map((x) => x.toFixed(2)).join(" / ")} (${sys.cpus} CPU)</dd>
      <dt>Memoria</dt><dd>${bytes(mem.total - mem.available)} de ${bytes(mem.total)}</dd>
      <dt>Disco libre</dt><dd>${bytes(sys.disk.free)}</dd>
      ${sys.temperature ? `<dt>Temperatura</dt><dd>${sys.temperature} °C</dd>` : ""}
      <dt>Unbound</dt><dd>${sys.unbound.running ? `<span class="badge ok">activo</span>` : sys.unbound.available ? `<span class="badge err">detenido</span>` : `<span class="badge">no instalado</span>`}</dd>
      <dt>Caché DNS</dt><dd>${num(sys.dns.cache.entries)} entradas · ${pct(sys.dns.cache.hit_ratio)} aciertos</dd></dl>`;
    const lvl = { info: "info", warning: "warn", error: "err" };
    el.querySelector("#events").innerHTML = events.length ? events.map((e) => `<div class="row" style="flex-wrap:nowrap;align-items:flex-start"><span class="dot dot-${lvl[e.level] === "info" ? "ok" : lvl[e.level]}" style="margin-top:6px"></span>
      <div><div>${esc(e.message)}</div><span class="muted">${ago(e.ts)} · ${esc(e.category)}</span></div></div>`).join("") : `<p class="muted">Sin eventos</p>`;
  }

  function renderAlerts(sys) {
    const box = el.querySelector("#dash-alerts");
    const alerts = [];
    if (sys.dns.error) alerts.push(["err", "El servidor DNS no está escuchando", sys.dns.error + " Consulta la guía de instalación (sección «Puerto 53 ocupado»)."]);
    if (sys.dns.fallback) alerts.push(["warn", "Unbound no disponible", "Se está usando Cloudflare como DNS de respaldo. Instala Unbound para resolver de forma privada."]);
    if (!sys.dns.total) alerts.push(["info", "Aún no hay tráfico DNS", `Configura tu router para que entregue ${sys.ip || "la IP de este equipo"} como servidor DNS por DHCP, o cámbialo en cada dispositivo. Después ejecuta el Asistente para optimizar la red.`]);
    box.innerHTML = alerts.map(([k, t, d]) => `<div class="alert ${k}">${ico(k === "info" ? "info" : "alert")}<div><b>${esc(t)}</b><p class="small">${esc(d)}</p></div>
      ${k === "info" ? `<a class="btn sm primary" href="#/assistant">${ico("zap")}Abrir asistente</a>` : ""}</div>`).join("");
  }

  await load();
  const timer = setInterval(() => load().catch(() => {}), 15000);
  return () => { clearInterval(timer); charts.forEach((c) => c.destroy()); };
}
