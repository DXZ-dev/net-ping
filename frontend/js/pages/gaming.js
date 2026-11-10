import { api, esc, ico, ms, ago, toast, toastErr, busy, on, toggle, empty, $, $$ } from "../lib.js";

export const title = "Gaming y streaming";
export const subtitle = "Mínima latencia y cero interrupciones para juegos competitivos y streaming";

export async function render(el, app) {
  const st = await api.get("/gaming");
  const cfg = st.config || {};
  const active = new Set(cfg.groups?.length ? cfg.groups : Object.keys(st.groups));
  el.innerHTML = `
    <div class="grid g4 mb">
      ${stat("Modo", cfg.enabled !== false ? "Activo" : "Desactivado", "", cfg.enabled !== false ? "green" : "red", "console")}
      ${stat("Dominios prioritarios", `${st.hot}/${st.domains}`, "listos en caché (0 ms)", "cyan", "zap")}
      ${stat("Protegidos", String(st.protected), "nunca bloqueados por listas", "violet", "shield")}
      ${stat("Último precalentado", ago(st.last_warm), `${st.warm_ok} renovados`, "blue", "refresh")}
    </div>

    <div class="grid g3">
      <div class="card span2"><header><h2>${ico("zap")}Modo Gaming y Streaming</h2><button class="btn sm" id="warm">${ico("refresh")}Precalentar ahora</button></header>
        <div class="opt"><div class="t"><b>Activar modo</b><span>Mantiene pre-resueltos (y renovados antes de caducar) los servidores de login, matchmaking, voz y CDN. El DNS responde siempre desde memoria: sin esperas al entrar a una partida ni al empezar un vídeo.</span></div>${toggle("enabled", cfg.enabled !== false, 'id="g-en"')}</div>
        <div class="opt"><div class="t"><b>Proteger infraestructura de juegos y streaming</b><span>Ninguna lista de bloqueo podrá romper Steam, Xbox, PSN, Riot, Epic, Discord, Netflix, YouTube, Twitch… (el control parental por perfil sigue funcionando).</span></div>${toggle("protect", cfg.protect !== false, 'id="g-pr"')}</div>
        <h4 class="mt mb">Plataformas prioritarias</h4>
        <div class="svc-grid">${Object.entries(st.groups).map(([g, list]) => `<div class="svc ${active.has(g) ? "sel" : ""}" data-g="${esc(g)}" title="${esc(list.join(", "))}" style="${active.has(g) ? "border-color:rgba(34,197,94,.5);background:rgba(34,197,94,.08)" : ""}">${ico(active.has(g) ? "check" : "plus")}<span class="grow">${esc(g)}</span><span class="muted small">${list.length}</span></div>`).join("")}</div>
        <label class="field mt">Dominios prioritarios extra (uno por línea)<textarea id="g-custom" rows="3" placeholder="servidor-de-mi-juego.com">${esc((cfg.custom_domains || []).join("\n"))}</textarea></label>
        <button class="btn primary mt" id="g-save">${ico("check")}Guardar</button>
      </div>
      <div class="card"><header><h3>${ico("info")}Qué hace (y qué no)</h3></header>
        <ul class="small" style="padding-left:18px;margin:0">
          <li><b>DNS:</b> elimina por completo la espera DNS de juegos y streaming (0,1-0,5 ms desde caché).</li>
          <li><b>CDN:</b> el test de abajo comprueba qué DNS te envía al nodo de streaming más cercano.</li>
          <li><b>Regiones:</b> mide qué servidores de juego te dan menos ping para elegir región/servidor.</li>
          <li><b>Bufferbloat:</b> el lag cuando alguien descarga se arregla con QoS/SQM en el router (o aquí si NetPing es tu router).</li>
          <li class="muted">El ping al servidor del juego depende de tu ISP y de la distancia: ningún software puede bajarlo de ese mínimo físico, pero sí evitar que suba.</li>
        </ul></div>
    </div>

    <div class="grid g2 mt">
      <div class="card"><header><h2>${ico("globe")}Ping a regiones de servidores de juego</h2><button class="btn primary sm" id="reg">${ico("activity")}Medir</button></header>
        <p class="muted small mb">Regiones de AWS (usadas por GameLift y muchos juegos online). Elige en tu juego la región con menor ping.</p><div id="reg-res">${empty("Pulsa «Medir»", "globe")}</div></div>
      <div class="card"><header><h2>${ico("tv")}Proximidad de CDN de streaming</h2><button class="btn primary sm" id="cdn">${ico("activity")}Comprobar</button></header>
        <p class="muted small mb">Cada DNS puede enviarte a un servidor de Netflix/YouTube distinto. Gana el que te lleva al nodo más cercano.</p><div id="cdn-res">${empty("Pulsa «Comprobar»", "tv")}</div></div>
    </div>

    <div class="card mt" id="qos"></div>`;

  on(el, "click", "[data-g]", async (e, b) => {
    const g = b.dataset.g;
    active.has(g) ? active.delete(g) : active.add(g);
    await saveGroups();
    app.reload();
  });
  async function saveGroups() { await api.put("/gaming", { groups: [...active] }).catch(toastErr); }
  el.querySelector("#g-en").onchange = (e) => api.put("/gaming", { enabled: e.target.checked }).then(() => toast(e.target.checked ? "Modo gaming activado" : "Modo gaming desactivado")).catch(toastErr);
  el.querySelector("#g-pr").onchange = (e) => api.put("/gaming", { protect: e.target.checked }).then(() => toast("Guardado")).catch(toastErr);
  el.querySelector("#g-save").onclick = (e) => busy(e.currentTarget, () => api.put("/gaming", { custom_domains: el.querySelector("#g-custom").value.split(/\s+/).filter(Boolean), groups: [...active] })).then(() => toast("Guardado")).catch(() => {});
  el.querySelector("#warm").onclick = (e) => busy(e.currentTarget, async () => { const r = await api.post("/gaming/warm"); toast(`${r.refreshed} renovados · ${r.fresh} ya estaban frescos`); app.reload(); }).catch(() => {});

  el.querySelector("#reg").onclick = (e) => busy(e.currentTarget, async () => {
    el.querySelector("#reg-res").innerHTML = `<p class="muted">Midiendo ${19} regiones…</p>`;
    const rows = await api.post("/gaming/regions");
    const best = rows.find((r) => r.min != null)?.min || 1;
    el.querySelector("#reg-res").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Región</th><th class="right">Ping</th><th class="right">Jitter</th><th>Calidad</th><th style="width:28%"></th></tr></thead><tbody>
      ${rows.map((r, i) => `<tr><td>${i === 0 ? "⭐ " : ""}<b>${esc(r.name)}</b><div class="muted small mono">${esc(r.region)}</div></td><td class="right"><b>${ms(r.min)}</b></td><td class="right">${ms(r.jitter)}</td>
        <td>${r.quality ? `<span class="badge ${{ excelente: "ok", buena: "info", aceptable: "warn", alta: "err" }[r.quality]}">${r.quality}</span>` : `<span class="badge">sin respuesta</span>`}</td>
        <td>${r.min != null ? `<div class="progress"><i style="width:${Math.max(4, (best / r.min) * 100)}%"></i></div>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }).catch(() => {});

  el.querySelector("#cdn").onclick = (e) => busy(e.currentTarget, async () => {
    el.querySelector("#cdn-res").innerHTML = `<p class="muted">Resolviendo CDN con cada DNS y midiendo… (≈20 s)</p>`;
    const r = await api.post("/gaming/cdn");
    el.querySelector("#cdn-res").innerHTML = `${r.best ? `<div class="alert ok mb">${ico("check")}<div><b>${esc(r.best.name)}</b> te lleva a los nodos CDN más cercanos (${ms(r.best.avg)} de media).
        <button class="btn sm mt" id="use-best">Usar este DNS</button></div></div>` : ""}
      <div class="table-wrap"><table><thead><tr><th>DNS</th>${r.results[0]?.probes.map((p) => `<th class="right">${esc(p.cdn.split(" ")[0])}</th>`).join("") || ""}<th class="right">Media</th></tr></thead><tbody>
      ${r.results.map((x) => `<tr><td><b>${esc(x.name)}</b></td>${x.probes.map((p) => `<td class="right" title="${esc(p.ip || "")}">${ms(p.latency)}</td>`).join("")}<td class="right"><b>${ms(x.avg)}</b></td></tr>`).join("")}</tbody></table></div>`;
    const ub = el.querySelector("#use-best");
    if (ub) ub.onclick = () => busy(ub, async () => {
      const dns = await api.get("/dns");
      const preset = dns.presets[r.best.id];
      await api.put("/dns", { upstreams: preset ? preset.servers : [r.best.server], preset: r.best.id, strategy: "fastest" });
      toast(`DNS cambiado a ${r.best.name}`);
    }).catch(() => {});
  }).catch(() => {});

  // ------------------------------------------------------------- QoS
  const q = await api.get("/qos").catch(() => ({ available: false }));
  el.querySelector("#qos").innerHTML = `<header><h2>${ico("activity")}QoS anti-bufferbloat (CAKE)</h2>${q.active?.length ? `<span class="badge ok">activo</span>` : ""}</header>
    <div class="alert info mb">${ico("info")}<div class="small">Sólo tiene efecto si <b>el tráfico de tu red pasa por este equipo</b> (NetPing hace de router/gateway, p. ej. en Proxmox con OPNsense/OpenWrt al lado). Si tu router es otro, activa SQM/QoS en él con las velocidades del test.</div></div>
    ${q.available ? `<form class="form-grid" id="qf">
      <label class="field">Interfaz de salida (WAN)<select name="iface">${(q.interfaces || []).map((i) => `<option>${esc(i.name)}</option>`).join("")}</select></label>
      <label class="field">Velocidad de subida (Mbps)<input type="number" step="0.1" name="mbit" placeholder="92 % de tu subida real"></label>
      <label class="field">Tipo de conexión<select name="overhead"><option value="ethernet">Fibra / Ethernet</option><option value="pppoe">PPPoE / VDSL</option><option value="docsis">Cable (DOCSIS)</option></select></label>
      <div class="row" style="align-items:flex-end"><button class="btn primary">${ico("check")}Aplicar CAKE</button><button type="button" class="btn" id="qrm">Quitar</button></div></form>
      ${q.active?.length ? `<pre class="mt">${esc(q.active.join("\n"))}</pre>` : ""}` : `<p class="muted">tc (iproute2) no disponible.</p>`}`;
  const qf = el.querySelector("#qf");
  if (qf) {
    qf.onsubmit = (e) => { e.preventDefault(); busy(qf.querySelector(".primary"), () => api.post("/qos", { iface: qf.iface.value, mbit: Number(qf.mbit.value), overhead: qf.overhead.value })).then((r) => toast(r.message, r.ok ? "ok" : "error", 7000)).catch(() => {}); };
    el.querySelector("#qrm").onclick = () => api.post("/qos", { iface: qf.iface.value, remove: true }).then((r) => toast(r.message)).catch(toastErr);
  }
}

const stat = (l, v, sub, tint, icon) => `<div class="card stat"><div class="ic tint-${tint}">${ico(icon)}</div><div class="truncate"><div class="v">${esc(v)}</div><div class="l">${esc(l)}${sub ? ` · <span class="small">${esc(sub)}</span>` : ""}</div></div></div>`;
