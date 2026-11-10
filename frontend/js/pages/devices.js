import { api, esc, ico, ms, num, ago, toast, toastErr, busy, drawer, on, $, $$, KIND_LABEL, empty, confirmDialog, lineChart, COLORS, time as fmtTime } from "../lib.js";
import { t } from "../i18n.js";
import { brandIcon, appIcon } from "../brands.js";

export const title = "Dispositivos";
export const subtitle = "Todos los equipos de tu red en tiempo real, su actividad y sus controles";

const fmtRate = (bps) => bps == null ? "" : bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} Mb/s` : bps >= 1e3 ? `${(bps / 1e3).toFixed(0)} kb/s` : `${Math.round(bps)} b/s`;

export async function render(el) {
  let view = localStorage.getItem("np-dev-view") || "map";
  let data = { devices: [] };
  let profiles = [];
  let filter = "";
  let es = null;
  el.innerHTML = `
    <div class="row mb">
      <div class="btn-group" id="view"><button data-v="map">${ico("network")} ${t("Mapa")}</button><button data-v="grid">${ico("devices")} ${t("Tarjetas")}</button><button data-v="table">${ico("list")} ${t("Tabla")}</button></div>
      <input id="dev-search" placeholder="${t("Buscar por nombre, IP, MAC, marca o modelo…")}" style="max-width:340px">
      <span class="grow"></span>
      <span class="small muted row" style="gap:6px"><span class="live-dot"></span><span id="live-txt">${t("En tiempo real")}</span></span>
      <button class="btn" id="scan">${ico("search")}${t("Analizar a fondo")}</button>
    </div>
    <div id="dev-summary" class="grid g4 mb"></div>
    <div id="dev-view"></div>`;

  const setView = (v) => { view = v; try { localStorage.setItem("np-dev-view", v); } catch {} draw(); };
  on(el, "click", "#view button", (e, b) => setView(b.dataset.v));
  el.querySelector("#dev-search").oninput = (e) => { filter = e.target.value.toLowerCase(); draw(); };
  el.querySelector("#scan").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api.post("/devices/scan");
    toast(r.found != null ? `${t("Análisis completado")}: ${r.found} ${t("en línea")}` : t("Análisis en curso…"));
    await load();
  }).catch(() => {});
  on(el, "click", "[data-mac]", (e, n) => { if (!e.target.closest("button")) openDevice(n.dataset.mac); });

  async function load() {
    [data, profiles] = await Promise.all([api.get("/devices"), api.get("/profiles")]);
    summary();
    draw();
  }

  function summary() {
    const d = data.devices;
    const online = d.filter((x) => x.online).length, blocked = d.filter((x) => x.internet_blocked).length;
    const vms = d.filter((x) => x.hypervisor).length;
    el.querySelector("#dev-summary").innerHTML = [
      ["Dispositivos", num(d.length), "devices", "blue"], ["En línea", num(online), "wifi", "green"],
      ["Con Internet pausado", num(blocked), "pause", "red"], ["Máquinas virtuales", num(vms), "server", "violet"],
    ].map(([l, v, i, tn]) => `<div class="card stat"><div class="ic tint-${tn}">${ico(i)}</div><div><div class="v">${v}</div><div class="l">${t(l)}</div></div></div>`).join("");
  }

  // ---------------------------------------------------------------- tiempo real (SSE)
  async function live() {
    try {
      const res = await fetch("/api/devices/stream", { credentials: "same-origin" });
      if (!res.ok) throw new Error();
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      es = reader;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (line) onEvent(JSON.parse(line.slice(6)));
        }
      }
    } catch {}
    if (es !== "closed") setTimeout(live, 5000);
  }
  let reloadT = null;
  function onEvent(ev) {
    const msg = { new: t("Nuevo dispositivo"), online: t("Conectado"), offline: t("Desconectado") }[ev.type];
    if (msg) {
      toast(`${msg}: ${ev.name}`, ev.type === "offline" ? "warn" : ev.type === "new" ? "info" : "ok", 4000);
      el.querySelector("#live-txt").textContent = `${msg}: ${ev.name} · ${fmtTime(ev.ts)}`;
    }
    clearTimeout(reloadT);
    reloadT = setTimeout(async () => {
      await load();
      const n = el.querySelector(`[data-mac="${CSS.escape(ev.mac)}"]`);
      n?.classList.add("flash");
    }, 300);
  }

  const list = () => data.devices.filter((d) => !filter || [d.name, d.friendly, d.hostname, d.ip, d.mac, d.vendor, d.brand_name, d.model, d.os]
    .some((x) => (x || "").toLowerCase().includes(filter)));
  const dname = (d) => d.name || d.friendly || d.hostname || d.model || d.brand_name || d.vendor || d.ip || d.mac;
  const profileOf = (d) => profiles.find((p) => p.id === d.profile) || profiles[0] || { name: "—", color: "#64748b" };
  const childrenOf = (mac) => data.devices.filter((x) => x.parent_effective === mac);
  const rate = (d) => d.rate ? `<span class="rate"><span class="d">↓${fmtRate(d.rate.rx_bps)}</span> <span class="u">↑${fmtRate(d.rate.tx_bps)}</span></span>` : "";

  function draw() {
    $$("#view button", el).forEach((b) => b.classList.toggle("active", b.dataset.v === view));
    const box = el.querySelector("#dev-view");
    const ds = list();
    if (!ds.length) { box.innerHTML = `<div class="card">${empty(t("No se encontraron dispositivos."), "devices")}</div>`; return; }
    if (view === "grid") {
      const card = (d) => {
        const p = profileOf(d);
        return `<div class="card dev-card ${d.online ? "" : "offline"}" data-mac="${esc(d.mac)}">
          <div class="dev-ic">${brandIcon(d.brand, d.kind, "lg")}<span class="dot dot-${d.internet_blocked ? "err" : d.online ? "ok" : "off"}"></span></div>
          <div class="meta"><b>${esc(dname(d))}</b><span>${esc(d.ip || t("sin IP"))} · ${esc(d.brand_name || d.vendor || KIND_LABEL[d.kind] || "")}${d.model ? " · " + esc(d.model) : ""}</span>
            <div class="row small" style="gap:6px;margin-top:4px"><span class="badge" style="border-color:${esc(p.color)};color:${esc(p.color)}">${esc(p.name)}</span>
            ${d.internet_blocked ? `<span class="badge err">${t("Sin Internet")}</span>` : ""}${d.hypervisor ? `<span class="badge violet">${esc(d.hypervisor === "proxmox" ? "VM/LXC" : "VM")}</span>` : ""}
            ${d.limit_down || d.limit_up ? `<span class="badge warn">${ico("gauge")}${d.limit_down || "∞"}/${d.limit_up || "∞"} Mb</span>` : ""}${rate(d)}</div></div>
          <div class="right small"><b>${num(d.queries)}</b><div class="muted">${t("consultas")}</div></div></div>`;
      };
      const roots = ds.filter((d) => !d.parent_effective || !ds.some((x) => x.mac === d.parent_effective));
      box.innerHTML = `<div class="grid g-auto">${roots.map((d) => {
        const kids = childrenOf(d.mac).filter((k) => ds.includes(k));
        return kids.length ? `<div style="grid-column:1/-1">${card(d)}<div class="dev-children mt">${kids.map(card).join("")}</div></div>` : card(d);
      }).join("")}</div>`;
    } else if (view === "table") {
      box.innerHTML = `<div class="card"><div class="table-wrap"><table><thead><tr><th></th><th>${t("Nombre")}</th><th>IP</th><th>MAC</th><th>${t("Marca / modelo")}</th><th>${t("Perfil")}</th><th class="right">${t("Latencia")}</th><th class="right">${t("Tráfico")}</th><th class="right">${t("Consultas")}</th><th>${t("Visto")}</th></tr></thead>
        <tbody>${ds.map((d) => `<tr data-mac="${esc(d.mac)}" style="cursor:pointer"><td>${brandIcon(d.brand, d.kind)}</td>
          <td><b>${esc(dname(d))}</b>${d.parent_effective ? `<div class="small muted">↳ ${esc(dname(data.devices.find((x) => x.mac === d.parent_effective) || {}))}</div>` : ""}</td>
          <td class="mono">${esc(d.ip || "")}</td><td class="mono muted">${esc(d.mac)}</td><td class="truncate" style="max-width:220px">${esc(d.brand_name || d.vendor || "")}${d.model ? `<div class="small muted">${esc(d.model)}</div>` : ""}</td>
          <td>${esc(profileOf(d).name)}</td><td class="right">${ms(d.latency)}</td><td class="right">${rate(d)}</td><td class="right">${num(d.queries)}</td>
          <td><span class="dot dot-${d.internet_blocked ? "err" : d.online ? "ok" : "off"}"></span> ${d.online ? t("ahora") : ago(d.last_seen)}</td></tr>`).join("")}</tbody></table></div></div>`;
    } else {
      drawMap(box, ds);
    }
  }

  // ---------------------------------------------------------------- mapa jerárquico
  function drawMap(box, ds) {
    box.innerHTML = `<div class="netmap"><svg id="map"></svg></div><p class="muted small mt">${t("Haz clic en un dispositivo para ver su actividad, controlar su Internet, limitar su velocidad o ver sus puertos. Las máquinas virtuales se agrupan alrededor de su servidor.")}</p>`;
    const svg = box.querySelector("#map");
    const W = svg.clientWidth || 900, H = svg.clientHeight || 560;
    const cx = W / 2, cy = H / 2 + 10;
    const gw = ds.find((d) => d.is_gateway);
    const top = ds.filter((d) => d !== gw && (!d.parent_effective || !ds.some((x) => x.mac === d.parent_effective)));
    let links = `<line class="link on" x1="${cx}" y1="62" x2="${cx}" y2="${cy - 32}"/>`;
    let nodes = `<g class="node" transform="translate(${cx},${40})"><circle class="bg" r="20"/><g transform="translate(-9,-9) scale(.75)"><path class="icon" d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></g><text y="34">Internet</text></g>`;
    const R = Math.min(W, H) * 0.34;
    const sx = W > H ? 1.45 : 1;
    top.forEach((d, i) => {
      const a = (i / top.length) * Math.PI * 2 + Math.PI / 2 + 0.25;
      const x = cx + Math.cos(a) * R * sx, y = cy + Math.sin(a) * R;
      links += `<line class="link ${d.internet_blocked ? "blocked" : d.online ? "on" : ""}" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}"/>`;
      const kids = childrenOf(d.mac).filter((k) => ds.includes(k));
      kids.forEach((k, j) => {
        const spread = Math.min(Math.PI * 0.9, 0.45 * kids.length);
        const ka = a + (kids.length === 1 ? 0 : -spread / 2 + (j / (kids.length - 1)) * spread);
        const rr = 74 + (j % 2) * 14;
        const kx = x + Math.cos(ka) * rr * (W > H ? 1.2 : 1), ky = y + Math.sin(ka) * rr;
        links += `<line class="link ${k.online ? "on" : ""}" x1="${x}" y1="${y}" x2="${kx}" y2="${ky}" style="stroke-dasharray:3 4"/>`;
        nodes += node(k, kx, ky, 15, true);
      });
      nodes += node(d, x, y, kids.length ? 24 : 21);
    });
    nodes += gw ? node(gw, cx, cy, 30) : `<g class="node" transform="translate(${cx},${cy})"><circle class="bg" r="30"/><text y="48">${t("Router")}</text></g>`;
    svg.innerHTML = links + nodes;
  }

  function node(d, x, y, r, small = false) {
    const color = d.internet_blocked ? "var(--err)" : d.online ? "var(--ok)" : "var(--muted)";
    const label = dname(d);
    const icon = brandIcon(d.brand, d.kind);
    return `<g class="node" data-mac="${esc(d.mac)}" transform="translate(${x},${y})">
      <circle class="bg" r="${r}"/><circle r="${r + 3}" fill="none" stroke="${color}" stroke-width="2" opacity="${d.online ? 0.9 : 0.4}"/>
      <foreignObject x="-15" y="-15" width="30" height="30" style="pointer-events:none"><div xmlns="http://www.w3.org/1999/xhtml" style="transform:scale(${small ? 0.8 : 1});transform-origin:center">${icon}</div></foreignObject>
      <text y="${r + 14}" style="font-size:${small ? 10 : 11}px">${esc(label.length > 20 ? label.slice(0, 19) + "…" : label)}</text>
      ${small ? "" : `<text class="sub" y="${r + 27}">${esc(d.ip || "")}${d.latency != null ? " · " + ms(d.latency) : ""}</text>`}
      <title>${esc(label)} — ${esc(d.ip || "")} ${esc(d.brand_name || d.vendor || "")} ${esc(d.model || "")}</title></g>`;
  }

  // ---------------------------------------------------------------- ficha del dispositivo
  async function openDevice(mac) {
    const d = await api.get(`/devices/${encodeURIComponent(mac)}`);
    const full = data.devices.find((x) => x.mac === mac) || d;
    const pol = d.policy || {};
    const paused = d.paused || (d.paused_until && d.paused_until * 1000 > Date.now());
    const kids = childrenOf(mac);
    const parent = data.devices.find((x) => x.mac === full.parent_effective);
    const dr = drawer({ title: dname(full), body: `
      <div class="row mb" style="flex-wrap:nowrap">${brandIcon(full.brand, full.kind, "lg")}
        <div class="grow"><div class="row"><span class="badge ${pol.block_all ? "err" : full.online ? "ok" : ""}">${pol.block_all ? t("Internet bloqueado") : full.online ? t("En línea") : t("Desconectado")}</span>
        ${full.os ? `<span class="badge">${esc(full.os)}</span>` : ""}${full.hypervisor ? `<span class="badge violet">${esc(full.hypervisor)}</span>` : ""}</div>
        <div class="muted small mono">${esc(full.ip || "")} · ${esc(mac)}</div>
        ${pol.block_reason ? `<div class="small warn">${esc(pol.block_reason)}</div>` : ""}</div></div>
      <div class="tabs mb"><button class="active" data-dt="sum">${t("Resumen")}</button><button data-dt="act">${t("Actividad")}</button><button data-dt="ctl">${t("Control")}</button>
        <button data-dt="spd">${t("Velocidad")}</button><button data-dt="ports">${t("Puertos")}</button><button data-dt="cfg">${t("Ajustes")}</button></div>
      <div id="dt"></div>` });
    const pane = dr.body.querySelector("#dt");
    let chart = null;
    const tabs = {
      sum() {
        pane.innerHTML = `<dl class="kv mb">
          <dt>${t("Marca")}</dt><dd>${esc(full.brand_name || full.vendor || "—")}</dd><dt>${t("Modelo")}</dt><dd>${esc(full.model || "—")}</dd>
          <dt>${t("Nombre en red")}</dt><dd>${esc(full.hostname || full.friendly || "—")}</dd><dt>${t("Fabricante (MAC)")}</dt><dd>${esc(full.vendor || "—")}</dd>
          <dt>${t("Servicios")}</dt><dd>${esc((full.services || []).join(", ") || "—")}</dd><dt>${t("Latencia")}</dt><dd>${ms(full.latency)}</dd>
          ${parent ? `<dt>${t("Alojado en")}</dt><dd><a href="#" data-goto="${esc(parent.mac)}">${esc(dname(parent))}</a></dd>` : ""}
          ${kids.length ? `<dt>${t("Aloja")}</dt><dd>${kids.map((k) => `<a href="#" data-goto="${esc(k.mac)}">${esc(dname(k))}</a>`).join(", ")}</dd>` : ""}
          <dt>${t("Primera vez")}</dt><dd>${full.first_seen ? new Date(full.first_seen * 1000).toLocaleString() : "—"}</dd><dt>${t("Última vez")}</dt><dd>${full.online ? t("ahora") : ago(full.last_seen)}</dd></dl>
          <h4 class="mb">${t("Consultas (24 h)")}</h4><div class="chart chart-sm mb"><canvas id="dev-chart"></canvas></div>`;
        chart?.destroy();
        chart = lineChart(pane.querySelector("#dev-chart"), { labels: d.hourly.map((x) => x.t),
          series: [{ name: t("Consultas"), color: COLORS.blue, values: d.hourly.map((x) => x.total) }, { name: t("Bloqueadas"), color: COLORS.red, values: d.hourly.map((x) => x.blocked) }] });
      },
      async act() {
        pane.innerHTML = `<div class="row mb"><div class="btn-group" id="ah">${[[1, "1 h"], [24, "24 h"], [168, "7 días"]].map(([v, l]) => `<button data-ah="${v}" class="${v === 24 ? "active" : ""}">${t(l)}</button>`).join("")}</div></div><div id="act-out">${empty(t("Cargando…"))}</div>
          <p class="small muted mt">${t("Basado en las consultas DNS: muestra qué apps y webs abre y cuánto tiempo, no el contenido (las comunicaciones cifradas no pueden ni deben verse).")}</p>`;
        const loadAct = async (hrs) => {
          const r = await api.get(`/devices/${encodeURIComponent(mac)}/activity?hours=${hrs}`);
          const out = pane.querySelector("#act-out");
          if (!r.items.length) { out.innerHTML = empty(t("Sin actividad en este periodo. ¿Usa Bipon como DNS?")); return; }
          const max = r.items[0].active_minutes || 1;
          out.innerHTML = `<div class="col">${r.items.map((g) => `<div class="rec" style="padding:10px 12px">
            <div class="row" style="flex-wrap:nowrap">${g.app ? appIcon(g.key, g.name) : `<span class="brand-ic" style="background:var(--card-2);color:var(--muted)">${ico("globe")}</span>`}
            <div class="grow truncate"><b>${esc(g.name)}</b> <span class="small muted">${esc(t(g.category))}</span>
              <div class="progress mt" style="height:5px"><i style="width:${Math.max(3, g.active_minutes / max * 100)}%"></i></div></div>
            <div class="right small nowrap"><b>${g.active_minutes} min</b><div class="muted">${fmtTime(g.first).slice(0, 5)}–${fmtTime(g.last).slice(0, 5)}</div></div>
            ${g.blocked ? `<span class="badge err">${g.blocked} ${t("bloq.")}</span>` : ""}</div>
            <details class="small muted mt"><summary>${g.sessions.length} ${t("sesiones")} · ${g.queries} ${t("consultas")}</summary>
              <div>${g.sessions.map(([a, b]) => `${fmtTime(a).slice(0, 5)}–${fmtTime(b).slice(0, 5)}`).join(" · ")}</div><div class="mono">${g.domains.map(esc).join(", ")}</div></details></div>`).join("")}</div>`;
        };
        on(pane, "click", "[data-ah]", (e, b) => { $$("[data-ah]", pane).forEach((x) => x.classList.toggle("active", x === b)); loadAct(Number(b.dataset.ah)); });
        loadAct(24);
      },
      ctl() {
        pane.innerHTML = `<div class="card flat mb"><h4 class="mb">${t("Control de Internet")} <span class="badge ok">${t("se aplica al instante")}</span></h4>
          <div class="row">${paused ? `<button class="btn success" data-act="resume">${ico("play")}${t("Reanudar Internet")}</button>`
            : `<button class="btn danger" data-act="pause" data-min="0">${ico("pause")}${t("Pausar Internet")}</button>
               <button class="btn sm" data-act="pause" data-min="15">15 min</button><button class="btn sm" data-act="pause" data-min="30">30 min</button><button class="btn sm" data-act="pause" data-min="60">1 h</button><button class="btn sm" data-act="pause" data-min="120">2 h</button>`}</div>
          ${d.paused_until && d.paused_until * 1000 > Date.now() ? `<p class="muted small">${t("Pausado hasta")} ${new Date(d.paused_until * 1000).toLocaleTimeString()}</p>` : ""}
          <p class="small muted mt">${t("El dispositivo deja de resolver nombres en segundos. En modo router además se cortan sus conexiones abiertas al momento.")}</p></div>
          <label class="field mb">${t("Perfil (control parental)")}<select id="prof">${profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === full.profile ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
          <dl class="kv"><dt>${t("Perfil efectivo")}</dt><dd>${esc(pol.profile_name || "")}${pol.services?.length ? ` · ${pol.services.length} ${t("apps bloqueadas")}` : ""}${pol.safesearch ? " · SafeSearch" : ""}</dd></dl>`;
        pane.querySelector("#prof").onchange = async (e) => { await api.patch(`/devices/${encodeURIComponent(mac)}`, { profile: e.target.value }).catch(toastErr); toast(t("Perfil aplicado al instante")); load(); };
      },
      spd() {
        pane.innerHTML = `<div class="card flat mb"><h4 class="mb">${t("Velocidad actual")}</h4><div class="grid g2">
          <div class="big-metric"><div class="v" id="rx">${fmtRate(full.rate?.rx_bps) || "—"}</div><div class="l">↓ ${t("Bajada")}</div></div>
          <div class="big-metric"><div class="v" id="tx">${fmtRate(full.rate?.tx_bps) || "—"}</div><div class="l">↑ ${t("Subida")}</div></div></div>
          ${full.rate ? "" : `<p class="small muted mt">${t("La velocidad por dispositivo se mide cuando el tráfico pasa por Bipon (Router y firewall → Modo router).")}</p>`}</div>
          <form id="lim" class="card flat"><h4 class="mb">${t("Limitar velocidad")}</h4>
          <div class="form-grid"><label class="field">${t("Bajada máx. (Mbps)")}<input type="number" step="0.1" min="0" name="down" value="${full.limit_down || ""}" placeholder="${t("sin límite")}"></label>
          <label class="field">${t("Subida máx. (Mbps)")}<input type="number" step="0.1" min="0" name="up" value="${full.limit_up || ""}" placeholder="${t("sin límite")}"></label></div>
          <div class="row mt"><button class="btn primary">${ico("check")}${t("Aplicar")}</button><button type="button" class="btn ghost" id="nolim">${t("Quitar límite")}</button></div>
          <p class="small muted mt">${t("Requiere que Bipon haga de router (los límites se aplican con control de tráfico del sistema).")}</p></form>`;
        const f = pane.querySelector("#lim");
        const send = async (down, up) => {
          const r = await api.patch(`/devices/${encodeURIComponent(mac)}/limit`, { limit_down: down, limit_up: up });
          toast(r.message || t("Límite aplicado"), r.applied === null ? "info" : "ok", 6000); load();
        };
        f.onsubmit = (e) => { e.preventDefault(); busy(f.querySelector(".primary"), () => send(f.down.value || null, f.up.value || null)).catch(() => {}); };
        pane.querySelector("#nolim").onclick = () => send(null, null).catch(toastErr);
        const timer = setInterval(async () => {
          if (!document.body.contains(pane)) return clearInterval(timer);
          const r = await api.get("/devices").catch(() => null);
          const x = r?.devices.find((y) => y.mac === mac);
          if (x?.rate && pane.querySelector("#rx")) { pane.querySelector("#rx").textContent = fmtRate(x.rate.rx_bps); pane.querySelector("#tx").textContent = fmtRate(x.rate.tx_bps); }
        }, 10000);
      },
      ports() {
        pane.innerHTML = `<div class="row mb"><button class="btn sm" id="ps">${ico("search")}${t("Escanear puertos abiertos")}</button><button class="btn sm" id="pg">${ico("activity")}Ping</button><button class="btn sm" id="wol">${ico("power")}${t("Encender (WoL)")}</button></div><div id="pout"></div>`;
        const out = pane.querySelector("#pout");
        pane.querySelector("#ps").onclick = (e) => busy(e.currentTarget, async () => {
          out.innerHTML = `<p class="muted small">${t("Escaneando puertos comunes…")}</p>`;
          const r = await api.post(`/devices/${encodeURIComponent(mac)}/ports`);
          out.innerHTML = r.ports.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Puerto")}</th><th>${t("Servicio")}</th><th></th></tr></thead><tbody>${r.ports.map((p) => `<tr><td class="mono">${p.port}/TCP</td><td>${esc(p.service || "—")}</td>
            <td class="actions">${p.web ? `<a class="btn sm" target="_blank" rel="noopener" href="${p.web}://${esc(r.ip)}:${p.port}/">${ico("external")}${t("Abrir")}</a>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted small">${t("No hay puertos comunes abiertos.")}</p>`;
        }).catch(() => {});
        pane.querySelector("#pg").onclick = (e) => busy(e.currentTarget, async () => {
          const r = await api.post(`/devices/${encodeURIComponent(mac)}/ping`);
          out.innerHTML = r.received ? `<div class="alert ok">${ico("check")}<div>${ms(r.avg)} (${t("mín")} ${ms(r.min)}, ${t("máx")} ${ms(r.max)}, jitter ${ms(r.jitter)}) · ${t("pérdida")} ${r.loss}%</div></div>` : `<div class="alert warn">${ico("alert")}<div>${t("No responde a ping.")}</div></div>`;
        }).catch(() => {});
        pane.querySelector("#wol").onclick = (e) => busy(e.currentTarget, () => api.post(`/devices/${encodeURIComponent(mac)}/wake`)).then(() => toast(t("Paquete mágico enviado"))).catch(() => {});
      },
      cfg() {
        const others = data.devices.filter((x) => x.mac !== mac);
        pane.innerHTML = `<form class="col" id="dev-form"><div class="form-grid">
          <label class="field">${t("Nombre")}<input name="name" value="${esc(d.name)}" placeholder="${esc(full.friendly || full.hostname || t("Ej. Móvil de Ana"))}"></label>
          <label class="field">${t("Tipo")}<select name="kind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${k === d.kind ? "selected" : ""}>${t(v)}</option>`).join("")}</select></label>
          <label class="field">${t("Conectado a / alojado en")}<select name="parent"><option value="">${t("Automático")}${full.auto_parent ? ` (${esc(dname(data.devices.find((x) => x.mac === full.auto_parent) || {}))})` : ""}</option>
            ${others.map((o) => `<option value="${esc(o.mac)}" ${o.mac === d.parent ? "selected" : ""}>${esc(dname(o))}</option>`).join("")}</select></label></div>
          <label class="field">${t("Notas")}<input name="notes" value="${esc(d.notes)}"></label>
          <div class="row"><button class="btn primary" type="submit">${ico("check")}${t("Guardar")}</button><span class="grow"></span><button type="button" class="btn sm danger" id="forget">${ico("trash")}${t("Olvidar dispositivo")}</button></div></form>`;
        const f = pane.querySelector("#dev-form");
        f.onsubmit = async (e) => {
          e.preventDefault();
          await busy(f.querySelector(".primary"), () => api.patch(`/devices/${encodeURIComponent(mac)}`, { name: f.name.value, kind: f.kind.value, parent: f.parent.value || null, notes: f.notes.value })).catch(() => {});
          toast(t("Dispositivo guardado")); load();
        };
        pane.querySelector("#forget").onclick = async () => {
          if (await confirmDialog(t("Olvidar dispositivo"), t("Se eliminará su nombre y perfil. Volverá a aparecer si se conecta de nuevo."))) { await api.del(`/devices/${encodeURIComponent(mac)}`); dr.close(); load(); }
        };
      },
    };
    on(dr.body, "click", "[data-dt]", (e, b) => { $$("[data-dt]", dr.body).forEach((x) => x.classList.toggle("active", x === b)); tabs[b.dataset.dt](); });
    on(dr.body, "click", "[data-goto]", (e, a) => { e.preventDefault(); dr.close(); setTimeout(() => openDevice(a.dataset.goto), 220); });
    on(dr.body, "click", "[data-act]", async (e, b) => {
      const body = b.dataset.act === "resume" ? { paused: 0, pause_minutes: 0 } : Number(b.dataset.min) ? { pause_minutes: Number(b.dataset.min) } : { paused: 1 };
      try {
        await busy(b, () => api.patch(`/devices/${encodeURIComponent(mac)}`, body));
        toast(b.dataset.act === "resume" ? t("Internet reanudado") : t("Internet pausado"), b.dataset.act === "resume" ? "ok" : "info");
        dr.close(); await load(); openDevice(mac);
      } catch {}
    });
    tabs.sum();
  }

  await load();
  live();
  const timer = setInterval(() => load().catch(() => {}), 30000);
  const onResize = () => view === "map" && draw();
  window.addEventListener("resize", onResize);
  return () => { clearInterval(timer); window.removeEventListener("resize", onResize); try { es?.cancel?.(); } catch {} es = "closed"; };
}
