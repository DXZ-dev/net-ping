import { api, esc, ico, ms, num, ago, toast, toastErr, busy, drawer, on, $, $$, KIND_ICON, KIND_LABEL, empty, confirmDialog, lineChart, COLORS, h } from "../lib.js";

export const title = "Dispositivos";
export const subtitle = "Todos los equipos de tu red, su actividad y sus controles";

export async function render(el) {
  let view = localStorage.getItem("np-dev-view") || "map";
  let data = { devices: [] };
  let profiles = [];
  let filter = "";
  el.innerHTML = `
    <div class="row mb">
      <div class="btn-group" id="view"><button data-v="map">${ico("network")} Mapa</button><button data-v="grid">${ico("devices")} Tarjetas</button><button data-v="table">${ico("list")} Tabla</button></div>
      <input id="dev-search" placeholder="Buscar por nombre, IP, MAC o fabricante…" style="max-width:340px">
      <span class="grow"></span>
      <span class="muted small" id="scan-info"></span>
      <button class="btn primary" id="scan">${ico("search")}Escanear red</button>
    </div>
    <div id="dev-summary" class="grid g4 mb"></div>
    <div id="dev-view"></div>`;

  const setView = (v) => { view = v; try { localStorage.setItem("np-dev-view", v); } catch {} $$("#view button", el).forEach((b) => b.classList.toggle("active", b.dataset.v === v)); draw(); };
  on(el, "click", "#view button", (e, b) => setView(b.dataset.v));
  el.querySelector("#dev-search").oninput = (e) => { filter = e.target.value.toLowerCase(); draw(); };
  el.querySelector("#scan").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api.post("/devices/scan");
    toast(r.found != null ? `Escaneo completado: ${r.found} dispositivos (${r.new} nuevos)` : "Escaneo en curso…");
    await load();
  }).catch(() => {});
  on(el, "click", "[data-mac]", (e, n) => { if (!e.target.closest("button")) openDevice(n.dataset.mac); });

  async function load() {
    [data, profiles] = await Promise.all([api.get("/devices"), api.get("/profiles")]);
    const d = data.devices;
    el.querySelector("#scan-info").textContent = data.scanning ? "Escaneando…" : `Último escaneo ${ago(data.last_scan)}`;
    const online = d.filter((x) => x.online).length, blocked = d.filter((x) => x.internet_blocked).length;
    const q = d.reduce((a, b) => a + (b.queries || 0), 0);
    el.querySelector("#dev-summary").innerHTML = [
      ["Dispositivos", num(d.length), "devices", "blue"], ["En línea", num(online), "wifi", "green"],
      ["Con Internet pausado", num(blocked), "pause", "red"], ["Consultas 24 h", num(q), "activity", "violet"],
    ].map(([l, v, i, t]) => `<div class="card stat"><div class="ic tint-${t}">${ico(i)}</div><div><div class="v">${v}</div><div class="l">${l}</div></div></div>`).join("");
    draw();
  }

  const list = () => data.devices.filter((d) => !filter || [d.name, d.hostname, d.ip, d.mac, d.vendor].some((x) => (x || "").toLowerCase().includes(filter)));
  const dname = (d) => d.name || d.hostname || d.vendor || d.ip || d.mac;
  const profileOf = (d) => profiles.find((p) => p.id === d.profile) || profiles[0] || { name: "—", color: "#64748b" };

  function draw() {
    $$("#view button", el).forEach((b) => b.classList.toggle("active", b.dataset.v === view));
    const box = el.querySelector("#dev-view");
    const ds = list();
    if (!ds.length) { box.innerHTML = `<div class="card">${empty("No se encontraron dispositivos. Pulsa «Escanear red».", "devices")}</div>`; return; }
    if (view === "grid") {
      box.innerHTML = `<div class="grid g-auto">${ds.map((d) => {
        const p = profileOf(d);
        return `<div class="card dev-card ${d.online ? "" : "offline"}" data-mac="${esc(d.mac)}">
          <div class="dev-ic">${ico(KIND_ICON[d.kind] || "wifi")}<span class="dot dot-${d.internet_blocked ? "err" : d.online ? "ok" : "off"}"></span></div>
          <div class="meta"><b>${esc(dname(d))}</b><span>${esc(d.ip || "sin IP")} · ${esc(d.vendor || KIND_LABEL[d.kind] || "")}</span>
            <div class="row small" style="gap:6px;margin-top:4px"><span class="badge" style="border-color:${esc(p.color)};color:${esc(p.color)}">${esc(p.name)}</span>
            ${d.internet_blocked ? `<span class="badge err">Sin Internet</span>` : ""}${d.latency != null ? `<span class="muted">${ms(d.latency)}</span>` : ""}</div></div>
          <div class="right small"><b>${num(d.queries)}</b><div class="muted">consultas</div></div></div>`;
      }).join("")}</div>`;
    } else if (view === "table") {
      box.innerHTML = `<div class="card"><div class="table-wrap"><table><thead><tr><th></th><th>Nombre</th><th>IP</th><th>MAC</th><th>Fabricante</th><th>Perfil</th><th class="right">Latencia</th><th class="right">Consultas</th><th class="right">Bloqueadas</th><th>Visto</th></tr></thead>
        <tbody>${ds.map((d) => `<tr data-mac="${esc(d.mac)}" style="cursor:pointer"><td><span class="dot dot-${d.internet_blocked ? "err" : d.online ? "ok" : "off"}"></span></td>
          <td><b>${esc(dname(d))}</b></td><td class="mono">${esc(d.ip || "")}</td><td class="mono muted">${esc(d.mac)}</td><td class="truncate" style="max-width:200px">${esc(d.vendor)}</td>
          <td>${esc(profileOf(d).name)}</td><td class="right">${ms(d.latency)}</td><td class="right">${num(d.queries)}</td><td class="right">${num(d.blocked)}</td><td class="muted">${ago(d.last_seen)}</td></tr>`).join("")}</tbody></table></div></div>`;
    } else {
      drawMap(box, ds);
    }
  }

  function drawMap(box, ds) {
    box.innerHTML = `<div class="netmap"><svg id="map"></svg></div><p class="muted small mt">Haz clic en un dispositivo para ver su actividad, hacer ping, escanear puertos, pausar Internet o asignarle un perfil. Las líneas animadas indican dispositivos activos.</p>`;
    const svg = box.querySelector("#map");
    const W = svg.clientWidth || 900, H = svg.clientHeight || 560;
    const cx = W / 2, cy = H / 2;
    const gw = ds.find((d) => d.is_gateway);
    const others = ds.filter((d) => d !== gw);
    const internet = `<g class="node" transform="translate(${cx},${44})"><circle class="bg" r="22"/><g transform="translate(-9,-9) scale(.75)"><path class="icon" d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></g><text y="38">Internet</text></g>`;
    const rings = others.length > 18 ? 2 : 1;
    const R1 = Math.min(W, H) * (rings > 1 ? 0.27 : 0.36), R2 = Math.min(W, H) * 0.44;
    let links = `<line class="link on" x1="${cx}" y1="66" x2="${cx}" y2="${cy - 30}"/>`;
    let nodes = "";
    const inner = rings > 1 ? others.slice(0, Math.ceil(others.length * 0.45)) : others;
    const outer = rings > 1 ? others.slice(inner.length) : [];
    const place = (arr, R, offset) => arr.forEach((d, i) => {
      const a = offset + (i / arr.length) * Math.PI * 2 + Math.PI / 2;
      const x = cx + Math.cos(a) * R * (W > H ? 1.35 : 1), y = cy + Math.sin(a) * R;
      links += `<line class="link ${d.internet_blocked ? "blocked" : d.online ? "on" : ""}" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}"/>`;
      nodes += nodeSvg(d, x, y);
    });
    place(inner, R1, 0.2);
    place(outer, R2, 0.5);
    const gwNode = gw ? nodeSvg(gw, cx, cy, 30) : `<g class="node" transform="translate(${cx},${cy})"><circle class="bg" r="30"/><text y="48">Router</text></g>`;
    svg.innerHTML = links + internet + nodes + gwNode;
  }

  function nodeSvg(d, x, y, r = 22) {
    const color = d.internet_blocked ? "var(--err)" : d.online ? "var(--ok)" : "var(--muted)";
    const icon = { router: "M4 14h16v6H4zM8 17h.01M12 17h.01M7 14l-2-8M17 14l2-8", phone: "M7 2h10v20H7zM11 18h2", tv: "M3 5h18v12H3zM8 21h8",
      laptop: "M4 5h16v11H4zM2 19h20", console: "M6 9h12a4 4 0 0 1 0 8h-1l-2-2H9l-2 2H6a4 4 0 0 1 0-8z", printer: "M6 9V3h12v6M6 18H4v-7h16v7h-2M6 14h12v7H6z",
      camera: "M3 7h4l2-3h6l2 3h4v13H3z", speaker: "M6 2h12v20H6z", chip: "M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4", server: "M4 4h16v6H4zM4 14h16v6H4z",
      tablet: "M5 2h14v20H5zM11 18h2", wifi: "M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 20h.01" }[KIND_ICON[d.kind] || "wifi"];
    const label = dname(d);
    return `<g class="node" data-mac="${esc(d.mac)}" transform="translate(${x},${y})">
      <circle class="bg" r="${r}"/><circle r="${r + 3}" fill="none" stroke="${color}" stroke-width="2" opacity="${d.online ? 0.9 : 0.4}"/>
      <g transform="translate(-9,-9) scale(.75)"><path class="icon" d="${icon}"/></g>
      <text y="${r + 15}">${esc(label.length > 18 ? label.slice(0, 17) + "…" : label)}</text>
      <text class="sub" y="${r + 28}">${esc(d.ip || "")}${d.latency != null ? " · " + ms(d.latency) : ""}</text>
      <title>${esc(label)} — ${esc(d.ip || "")} ${esc(d.vendor || "")}</title></g>`;
  }

  // ------------------------------------------------------------ detalle
  async function openDevice(mac) {
    const d = await api.get(`/devices/${encodeURIComponent(mac)}`);
    const pol = d.policy || {};
    const paused = d.paused || (d.paused_until && d.paused_until * 1000 > Date.now());
    const dr = drawer({ title: dname(d), body: `
      <div class="row mb"><div class="dev-card" style="padding:0"><div class="dev-ic">${ico(KIND_ICON[d.kind] || "wifi")}</div></div>
        <div><div class="row"><span class="badge ${pol.block_all ? "err" : d.online ? "ok" : ""}">${pol.block_all ? "Internet bloqueado" : d.online ? "En línea" : "Desconectado"}</span>
        ${pol.block_reason ? `<span class="muted small">${esc(pol.block_reason)}</span>` : ""}</div>
        <div class="muted small mono">${esc(d.ip || "")} · ${esc(d.mac)}</div></div></div>

      <div class="card flat mb"><h4 class="mb">Control de Internet</h4>
        <div class="row">
          ${paused ? `<button class="btn success" data-act="resume">${ico("play")}Reanudar Internet</button>`
            : `<button class="btn danger" data-act="pause" data-min="0">${ico("pause")}Pausar Internet</button>
               <button class="btn sm" data-act="pause" data-min="30">30 min</button><button class="btn sm" data-act="pause" data-min="60">1 h</button><button class="btn sm" data-act="pause" data-min="120">2 h</button>`}
        </div>
        ${d.paused_until && d.paused_until * 1000 > Date.now() ? `<p class="muted small">Pausado hasta ${new Date(d.paused_until * 1000).toLocaleTimeString("es")}</p>` : ""}
      </div>

      <form class="col mb" id="dev-form">
        <div class="form-grid">
          <label class="field">Nombre<input name="name" value="${esc(d.name)}" placeholder="${esc(d.hostname || "Ej. Móvil de Ana")}"></label>
          <label class="field">Tipo<select name="kind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${k === d.kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label class="field">Perfil (control parental)<select name="profile">${profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === d.profile ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
        </div>
        <label class="field">Notas<input name="notes" value="${esc(d.notes)}"></label>
        <div><button class="btn primary" type="submit">${ico("check")}Guardar</button></div>
      </form>

      <div class="card flat mb"><h4 class="mb">Conexión directa</h4>
        <div class="row">
          <button class="btn sm" data-act="ping">${ico("activity")}Ping</button>
          <button class="btn sm" data-act="ports">${ico("search")}Escanear puertos</button>
          <button class="btn sm" data-act="wake">${ico("power")}Encender (WoL)</button>
          <a class="btn sm" href="#/queries" data-act="log">${ico("list")}Ver consultas</a>
        </div>
        <div id="dev-out" class="mt"></div>
      </div>

      <dl class="kv mb">
        <dt>Fabricante</dt><dd>${esc(d.vendor || "—")}</dd><dt>Hostname</dt><dd>${esc(d.hostname || "—")}</dd>
        <dt>Latencia</dt><dd>${ms(d.latency)}</dd><dt>Primera vez</dt><dd>${d.first_seen ? new Date(d.first_seen * 1000).toLocaleString("es") : "—"}</dd>
        <dt>Última vez</dt><dd>${ago(d.last_seen)}</dd><dt>Perfil efectivo</dt><dd>${esc(pol.profile_name || "")}${pol.services?.length ? ` · ${pol.services.length} apps bloqueadas` : ""}${pol.safesearch ? " · SafeSearch" : ""}</dd>
      </dl>

      <h4 class="mb">Actividad (24 h)</h4>
      <div class="chart chart-sm mb"><canvas id="dev-chart"></canvas></div>
      <h4 class="mb">Dominios más consultados</h4>
      <div class="bar-list">${d.top_domains.length ? d.top_domains.map((t) => `<div class="bar-item ${t.blocked ? "red" : ""}"><span class="fill" style="width:${(t.n / d.top_domains[0].n) * 100}%"></span><span class="name">${esc(t.domain)}</span><b>${num(t.n)}</b></div>`).join("") : `<p class="muted small">Sin consultas DNS de este dispositivo. ¿Usa NetPing como DNS?</p>`}</div>
      <div class="mt"><button class="btn sm danger" data-act="forget">${ico("trash")}Olvidar dispositivo</button></div>` });

    const ch = lineChart(dr.body.querySelector("#dev-chart"), { labels: d.hourly.map((x) => x.t),
      series: [{ name: "Consultas", color: COLORS.blue, values: d.hourly.map((x) => x.total) }, { name: "Bloqueadas", color: COLORS.red, values: d.hourly.map((x) => x.blocked) }] });

    dr.body.querySelector("#dev-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      await busy(f.querySelector("button"), () => api.patch(`/devices/${encodeURIComponent(mac)}`, { name: f.name.value, kind: f.kind.value, profile: f.profile.value, notes: f.notes.value })).catch(() => {});
      toast("Dispositivo guardado"); load();
    };
    const out = dr.body.querySelector("#dev-out");
    on(dr.body, "click", "[data-act]", async (e, b) => {
      const act = b.dataset.act;
      if (act === "log") { try { sessionStorage.setItem("np-q-client", d.ip || ""); } catch {} dr.close(); return; }
      e.preventDefault();
      try {
        if (act === "pause" || act === "resume") {
          const body = act === "resume" ? { paused: 0, pause_minutes: 0 } : Number(b.dataset.min) ? { pause_minutes: Number(b.dataset.min) } : { paused: 1 };
          await busy(b, () => api.patch(`/devices/${encodeURIComponent(mac)}`, body));
          toast(act === "resume" ? "Internet reanudado" : "Internet pausado para este dispositivo", act === "resume" ? "ok" : "info");
          ch.destroy(); dr.close(); await load(); openDevice(mac);
        } else if (act === "ping") {
          const r = await busy(b, () => api.post(`/devices/${encodeURIComponent(mac)}/ping`));
          out.innerHTML = r.received ? `<div class="alert ok">${ico("check")}<div>Responde: <b>${ms(r.avg)}</b> de media (mín ${ms(r.min)}, máx ${ms(r.max)}, jitter ${ms(r.jitter)}) · pérdida ${r.loss}%</div></div>`
            : `<div class="alert warn">${ico("alert")}<div>No responde a ping (puede estar apagado o con firewall).</div></div>`;
        } else if (act === "ports") {
          out.innerHTML = `<p class="muted small">Escaneando puertos comunes…</p>`;
          const r = await busy(b, () => api.post(`/devices/${encodeURIComponent(mac)}/ports`));
          out.innerHTML = r.ports.length ? `<div class="table-wrap"><table><thead><tr><th>Puerto</th><th>Servicio</th><th></th></tr></thead><tbody>${r.ports.map((p) => `<tr><td class="mono">${p.port}</td><td>${esc(p.service || "—")}</td>
            <td class="actions">${p.web ? `<a class="btn sm" target="_blank" rel="noopener" href="${p.web}://${esc(r.ip)}:${p.port}/">${ico("external")}Abrir</a>` : ""}</td></tr>`).join("")}</tbody></table></div>`
            : `<p class="muted small">No hay puertos comunes abiertos.</p>`;
        } else if (act === "wake") {
          await busy(b, () => api.post(`/devices/${encodeURIComponent(mac)}/wake`));
          toast("Paquete mágico enviado");
        } else if (act === "forget") {
          if (await confirmDialog("Olvidar dispositivo", "Se eliminará su nombre y perfil. Volverá a aparecer si se conecta de nuevo.")) {
            await api.del(`/devices/${encodeURIComponent(mac)}`); dr.close(); load();
          }
        }
      } catch (err) { if (act === "forget") toastErr(err); }
    });
  }

  await load();
  const timer = setInterval(() => load().catch(() => {}), 30000);
  const onResize = () => view === "map" && draw();
  window.addEventListener("resize", onResize);
  return () => { clearInterval(timer); window.removeEventListener("resize", onResize); };
}
