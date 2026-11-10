import { api, esc, ico, ms, num, pct, ago, toast, toastErr, busy, on, toggle, help, $, $$, empty } from "../lib.js";

export const title = "DNS y latencia";
export const subtitle = "Elige tu DNS, optimiza la latencia según tu zona y configura la caché";

const STRATEGY = { fastest: ["Más rápido (recomendado)", "Envía cada consulta al servidor con menor latencia medida; si falla, prueba el siguiente."],
  parallel: ["Paralelo", "Consulta a todos a la vez y usa la primera respuesta: mínima latencia, más tráfico."],
  failover: ["En orden", "Usa siempre el primero; los demás sólo si falla."] };

export async function render(el, app) {
  const d = await api.get("/dns");
  const s = d.settings;
  const groups = {};
  for (const [id, p] of Object.entries(d.presets)) (groups[p.group] ||= []).push([id, p]);
  const curPreset = s.preset?.startsWith("auto:") ? s.preset.slice(5) : s.preset;

  el.innerHTML = `
    ${d.error ? `<div class="alert err mb">${ico("alert")}<div><b>DNS sin escuchar</b><p class="small">${esc(d.error)}</p></div></div>` : ""}
    ${d.fallback ? `<div class="alert warn mb">${ico("alert")}<div>Unbound no está disponible: se usa Cloudflare como respaldo temporal.</div></div>` : ""}
    <div class="grid g4 mb" id="dns-stats"></div>

    <div class="card mb">
      <header><h2>${ico("zap")}Optimizador de latencia</h2>
        <button class="btn" id="bench">${ico("activity")}Sólo medir</button><button class="btn primary" id="opt">${ico("zap")}Medir y aplicar el más rápido</button></header>
      <p class="muted small">Mide desde <b>tu ubicación</b> la latencia real de cada resolver (consultas cacheadas y en frío) y su fiabilidad. Los DNS públicos son <i>anycast</i>: te responde el nodo más cercano, por eso el resultado varía según tu país y proveedor.</p>
      <div class="opt"><div class="t"><b>Re-optimizar automáticamente</b><span>Repite la medición periódicamente y cambia al más rápido si tu ruta cambia.</span></div>
        <select id="auto-int" style="width:auto"><option value="6">Cada 6 h</option><option value="24">Cada día</option><option value="168">Cada semana</option></select>
        ${toggle("auto", s.auto_optimize?.enabled, 'id="auto-on"')}</div>
      ${s.auto_optimize?.last_run ? `<p class="muted small">Última optimización ${ago(s.auto_optimize.last_run)}${s.auto_optimize.last_result ? `: ${esc(s.auto_optimize.last_result.name)} (${ms(s.auto_optimize.last_result.median)})` : ""}</p>` : ""}
      <div id="bench-res" class="mt"></div>
    </div>

    <div class="grid g3">
      <div class="card span2">
        <header><h2>${ico("globe")}Servidor DNS upstream</h2></header>
        ${Object.entries(groups).map(([g, items]) => `<h4 class="mb mt">${esc(g)}</h4><div class="grid g3">${items.filter(([id]) => id !== "custom").map(([id, p]) =>
          `<div class="preset ${id === curPreset ? "active" : ""}" data-preset="${id}"><b>${esc(p.name)}</b><span class="mono">${esc(p.servers.join(", "))}</span></div>`).join("")}</div>`).join("")}
        <h4 class="mb mt">Servidores en uso ${help("Formatos: 1.1.1.1 · 1.1.1.1:53 · tcp://1.1.1.1 · tls://1.1.1.1#cloudflare-dns.com · https://1.1.1.1/dns-query")}</h4>
        <textarea id="ups" rows="4">${esc(s.upstreams.join("\n"))}</textarea>
        <div class="form-grid mt">
          <label class="field">Estrategia<select id="strategy">${Object.entries(STRATEGY).map(([k, [l]]) => `<option value="${k}" ${k === s.strategy ? "selected" : ""}>${l}</option>`).join("")}</select><small id="strat-help">${STRATEGY[s.strategy]?.[1] || ""}</small></label>
          <label class="field">Tiempo máximo (ms)<input type="number" id="timeout" value="${s.timeout_ms}" min="300" max="10000"></label>
        </div>
        <div class="row mt"><button class="btn primary" id="save-ups">${ico("check")}Guardar servidores</button><button class="btn" id="test-ups">${ico("activity")}Probar estos servidores</button></div>
        <div id="ups-stats" class="mt"></div>
      </div>

      <div class="card">
        <header><h2>${ico("zap")}Caché y rendimiento</h2></header>
        <form id="cache-form">
          <label class="field mb">Tamaño de caché (entradas)<input type="number" name="cache_size" value="${s.cache_size}" min="0" max="1000000"></label>
          <div class="form-grid mb"><label class="field">TTL mínimo (s)<input type="number" name="cache_min_ttl" value="${s.cache_min_ttl}" min="0" max="3600"></label>
            <label class="field">TTL máximo (s)<input type="number" name="cache_max_ttl" value="${s.cache_max_ttl}" min="60" max="604800"></label></div>
          ${opt("optimistic_cache", "Caché optimista", "Responde al instante con datos caducados y los refresca en segundo plano. Elimina esperas.", s.optimistic_cache)}
          ${opt("prefetch", "Prefetch", "Renueva los dominios populares antes de que caduquen.", s.prefetch)}
          <h4 class="mt mb">Bloqueo</h4>
          <label class="field mb">Respuesta a dominios bloqueados<select name="blocking_mode"><option value="null" ${s.blocking_mode === "null" ? "selected" : ""}>0.0.0.0 / :: (recomendado, más rápido)</option><option value="nxdomain" ${s.blocking_mode === "nxdomain" ? "selected" : ""}>NXDOMAIN (no existe)</option></select></label>
          ${opt("block_doh_canary", "Evitar DNS cifrado del navegador", "Indica a Firefox/Chrome que usen el DNS de la red (necesario para el control parental).", s.block_doh_canary)}
          ${opt("block_icloud_relay", "Bloquear iCloud Private Relay", "Impide que dispositivos Apple salten el filtro con Private Relay.", s.block_icloud_relay)}
          ${opt("log_queries", "Registrar consultas", "Desactívalo para máxima privacidad (las estadísticas dejarán de actualizarse).", s.log_queries)}
          <div class="row mt"><button class="btn primary">${ico("check")}Guardar</button><button class="btn" type="button" id="flush">${ico("trash")}Vaciar caché</button></div>
        </form>
      </div>
    </div>

    <div class="grid g2 mt">
      <div class="card"><header><h2>${ico("list")}Registros DNS locales</h2></header>
        <p class="muted small mb">Nombres para tus equipos (ej. <code>nas.lan → 192.168.1.10</code>). Bipon Net responde además en <code>bipon.lan</code>.</p>
        <div id="local-list" class="col"></div>
        <form class="inline-form mt" id="local-form"><input name="name" placeholder="nas.lan" required><select name="type" style="max-width:100px"><option>A</option><option>AAAA</option><option>CNAME</option></select><input name="value" placeholder="192.168.1.10" required><button class="btn">${ico("plus")}</button></form>
      </div>
      <div class="card"><header><h2>${ico("router")}Reenvío condicional</h2></header>
        <p class="muted small mb">Envía los nombres de tu red local (y las búsquedas inversas) al router, para ver los nombres DHCP de cada dispositivo.</p>
        <form id="cf-form" class="col">
          ${opt("enabled", "Activar", "", s.conditional_forwarding?.enabled)}
          <div class="form-grid"><label class="field">Dominio local<input name="domain" value="${esc(s.conditional_forwarding?.domain || "lan")}"></label>
          <label class="field">IP del router<input name="server" value="${esc(s.conditional_forwarding?.server || "")}" placeholder="192.168.1.1"></label></div>
          <div><button class="btn primary">${ico("check")}Guardar</button></div></form>
      </div>
    </div>`;

  const save = (patch, msg = "Guardado") => api.put("/dns", patch).then(() => { toast(msg); }).catch(toastErr);
  el.querySelector("#auto-int").value = String(s.auto_optimize?.interval_hours || 24);
  const saveAuto = () => save({ auto_optimize: { ...(s.auto_optimize || {}), enabled: el.querySelector("#auto-on").checked, interval_hours: Number(el.querySelector("#auto-int").value) } }, "Optimización automática actualizada");
  el.querySelector("#auto-on").onchange = saveAuto;
  el.querySelector("#auto-int").onchange = saveAuto;

  on(el, "click", "[data-preset]", (e, b) => {
    const p = d.presets[b.dataset.preset];
    $$("[data-preset]", el).forEach((x) => x.classList.toggle("active", x === b));
    el.querySelector("#ups").value = p.servers.join("\n");
    el.querySelector("#ups").dataset.preset = b.dataset.preset;
    toast(`${p.name} seleccionado — pulsa «Guardar servidores»`, "info");
  });
  el.querySelector("#strategy").onchange = (e) => { el.querySelector("#strat-help").textContent = STRATEGY[e.target.value][1]; };
  el.querySelector("#save-ups").onclick = (e) => busy(e.currentTarget, () => api.put("/dns", {
    upstreams: el.querySelector("#ups").value.split(/\s+/).filter(Boolean), strategy: el.querySelector("#strategy").value,
    timeout_ms: Number(el.querySelector("#timeout").value), preset: el.querySelector("#ups").dataset.preset || "custom",
  })).then(() => toast("Servidores DNS actualizados")).catch(() => {});
  el.querySelector("#test-ups").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api.post("/dns/test", { servers: el.querySelector("#ups").value.split(/\s+/).filter(Boolean) });
    el.querySelector("#bench-res").innerHTML = benchTable(r);
    el.querySelector("#bench-res").scrollIntoView({ behavior: "smooth", block: "center" });
  }).catch(() => {});

  const runBench = (apply) => async (e) => {
    const box = el.querySelector("#bench-res");
    box.innerHTML = `<p class="muted">Midiendo ~30 consultas por servidor… (10-20 s)</p>`;
    await busy(e.currentTarget, async () => {
      if (apply) {
        const r = await api.post("/dns/optimize", { apply: true });
        box.innerHTML = (r.chosen ? `<div class="alert ok mb">${ico("check")}<div><b>Aplicado: ${esc(r.chosen.name)}</b> (${ms(r.chosen.median)}) · servidores ${esc(r.chosen.servers.join(", "))}</div></div>` : `<div class="alert warn mb">${ico("alert")}<div>Ningún resolver fue lo bastante fiable.</div></div>`) + benchTable(r.results);
        setTimeout(() => app.reload(), 2500);
      } else {
        box.innerHTML = benchTable(await api.post("/tools/benchmark", { extended: true }));
      }
    }).catch(() => { box.innerHTML = ""; });
  };
  el.querySelector("#bench").onclick = runBench(false);
  el.querySelector("#opt").onclick = runBench(true);

  el.querySelector("#cache-form").onsubmit = (e) => {
    e.preventDefault();
    const f = e.target;
    const patch = {};
    for (const k of ["cache_size", "cache_min_ttl", "cache_max_ttl"]) patch[k] = Number(f[k].value);
    for (const k of ["optimistic_cache", "prefetch", "block_doh_canary", "block_icloud_relay", "log_queries"]) patch[k] = f[k].checked;
    patch.blocking_mode = f.blocking_mode.value;
    busy(f.querySelector("button"), () => api.put("/dns", patch)).then(() => toast("Ajustes de caché guardados")).catch(() => {});
  };
  el.querySelector("#flush").onclick = (e) => busy(e.currentTarget, () => api.post("/dns/cache/flush")).then((r) => toast(`Caché vaciada (${num(r.cleared)} entradas)`)).catch(() => {});

  let local = [...(s.local_records || [])];
  const drawLocal = () => {
    el.querySelector("#local-list").innerHTML = local.length ? local.map((r, i) => `<div class="row between rec" style="padding:8px 12px"><span class="mono">${esc(r.name)} <span class="muted">${esc(r.type)}</span> → ${esc(r.value)}</span><button class="icon-btn" data-lrm="${i}">${ico("trash")}</button></div>`).join("") : `<p class="muted small">Sin registros</p>`;
  };
  drawLocal();
  el.querySelector("#local-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    local.push({ name: f.name.value.trim(), type: f.type.value, value: f.value.value.trim() });
    await save({ local_records: local }, "Registro añadido"); f.reset(); drawLocal();
  };
  on(el, "click", "[data-lrm]", async (e, b) => { local.splice(Number(b.dataset.lrm), 1); await save({ local_records: local }, "Registro eliminado"); drawLocal(); });
  el.querySelector("#cf-form").onsubmit = (e) => {
    e.preventDefault();
    const f = e.target;
    save({ conditional_forwarding: { enabled: f.enabled.checked, domain: f.domain.value.trim(), server: f.server.value.trim() } });
  };

  async function stats() {
    const x = (await api.get("/dns").catch(() => null))?.stats;
    if (!x) return;
    el.querySelector("#dns-stats").innerHTML = [
      ["Respuesta media", ms(x.avg_response_ms), "zap", "green"], ["Aciertos de caché", pct(x.cache.hit_ratio), "server", "cyan"],
      ["Entradas en caché", num(x.cache.entries), "list", "violet"], ["Consultas/seg", String(x.qps), "activity", "blue"],
    ].map(([l, v, i, t]) => `<div class="card stat"><div class="ic tint-${t}">${ico(i)}</div><div><div class="v">${v}</div><div class="l">${l}</div></div></div>`).join("");
    el.querySelector("#ups-stats").innerHTML = x.upstreams.length ? `<div class="table-wrap"><table><thead><tr><th>Servidor</th><th>Protocolo</th><th class="right">Latencia media</th><th class="right">Consultas</th><th class="right">Fallos</th></tr></thead>
      <tbody>${x.upstreams.map((u, i) => `<tr><td class="mono">${i === 0 && s.strategy === "fastest" ? "" : ""}${esc(u.server)}</td><td><span class="badge">${esc(u.proto.toUpperCase())}</span></td><td class="right">${ms(u.avg_ms)}</td><td class="right">${num(u.queries)}</td><td class="right ${u.failures ? "err" : ""}">${num(u.failures)}</td></tr>`).join("")}</tbody></table></div>` : "";
  }
  stats();
  const timer = setInterval(stats, 5000);
  return () => clearInterval(timer);
}

const opt = (name, t, d, v) => `<div class="opt"><div class="t"><b>${t}</b>${d ? `<span>${d}</span>` : ""}</div>${toggle(name, v)}</div>`;

export function benchTable(rows) {
  if (!rows?.length) return empty("Sin resultados");
  const best = rows.find((r) => r.median != null)?.median || 1;
  return `<div class="table-wrap"><table><thead><tr><th>#</th><th>Resolver</th><th class="right">Mediana</th><th class="right">Mín</th><th class="right">En frío</th><th class="right">Fiabilidad</th><th style="width:30%"></th></tr></thead><tbody>
    ${rows.map((r, i) => `<tr><td>${r.median != null ? i + 1 : "—"}</td><td><b>${esc(r.name)}</b><div class="muted small mono">${esc(r.server)}</div></td>
      <td class="right ${i === 0 ? "ok" : ""}"><b>${ms(r.median)}</b></td><td class="right">${ms(r.min)}</td><td class="right">${ms(r.uncached)}</td>
      <td class="right ${r.reliability < 95 ? "warn" : ""}">${r.reliability}%</td>
      <td>${r.median != null ? `<div class="progress"><i style="width:${Math.max(4, Math.min(100, (best / r.median) * 100))}%"></i></div>` : `<span class="badge err">sin respuesta</span>`}</td></tr>`).join("")}</tbody></table></div>`;
}
