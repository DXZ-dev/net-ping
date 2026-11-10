import { api, stream, esc, ico, ms, toast, toastErr, busy, on, empty, $, $$ } from "../lib.js";
import { benchTable } from "./dns.js";

export const title = "Herramientas de red";
export const subtitle = "Diagnóstico, ping, traceroute, DNS, puertos, whois, HTTP/TLS y Wake-on-LAN";

const TOOLS = [
  ["diag", "Diagnóstico", "activity"], ["ping", "Ping", "target"], ["trace", "Traceroute", "network"], ["lookup", "Consulta DNS", "globe"],
  ["bench", "Benchmark DNS", "gauge"], ["latency", "Latencia global", "zap"], ["ports", "Puertos", "search"], ["http", "Web / TLS", "lock"],
  ["whois", "Whois", "info"], ["wol", "Wake-on-LAN", "power"],
];

export async function render(el) {
  let ctrl = null;
  el.innerHTML = `<div class="tabs">${TOOLS.map(([id, l, i], n) => `<button data-t="${id}" class="${n === 0 ? "active" : ""}">${ico(i)}${l}</button>`).join("")}</div><div id="tool"></div>`;
  const box = () => el.querySelector("#tool");
  on(el, "click", "[data-t]", (e, b) => { $$("[data-t]", el).forEach((x) => x.classList.toggle("active", x === b)); ctrl?.abort(); show(b.dataset.t); });

  function show(t) {
    const fresh = box().cloneNode(false); box().replaceWith(fresh);
    ({ diag, ping, trace, lookup, bench, latency, ports, http, whois, wol })[t](fresh);
  }

  function diag(b) {
    b.innerHTML = `<div class="card"><header><h2>${ico("activity")}Diagnóstico de conexión</h2><button class="btn primary" id="go">${ico("play")}Ejecutar</button></header>
      <p class="muted small">Comprueba paso a paso: interfaz → router → Internet → DNS → navegación → IPv6 → IP pública. Identifica en qué punto está el problema.</p><div id="out" class="mt"></div></div>`;
    b.querySelector("#go").onclick = (e) => busy(e.currentTarget, async () => {
      const r = await api.post("/tools/diagnose");
      const icon = { ok: ["check", "green"], warn: ["alert", "amber"], fail: ["x", "red"], info: ["info", "blue"] };
      b.querySelector("#out").innerHTML = `<div class="alert ${r.status === "ok" ? "ok" : r.status === "warn" ? "warn" : "err"} mb">${ico(r.status === "ok" ? "check" : "alert")}<div><b>${r.status === "ok" ? "Todo funciona correctamente" : r.status === "warn" ? "Funciona, con avisos" : "Se detectaron problemas"}</b></div></div>
        ${r.checks.map((c) => `<div class="check"><div class="ci tint-${icon[c.status][1]}">${ico(icon[c.status][0])}</div><div><b>${esc(c.name)}</b><div class="muted small">${esc(c.detail)}</div></div></div>`).join("")}`;
    }).catch(() => {});
  }

  function term(b, label, placeholder, endpoint, extra = "") {
    b.innerHTML = `<div class="card"><form class="inline-form mb" id="f"><input name="host" placeholder="${placeholder}" required>${extra}<button class="btn primary">${ico("play")}${label}</button><button type="button" class="btn" id="stop">${ico("x")}Parar</button></form>
      <div class="terminal" id="term"></div></div>`;
    const termEl = b.querySelector("#term");
    b.querySelector("#stop").onclick = () => ctrl?.abort();
    b.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      ctrl?.abort(); ctrl = new AbortController();
      termEl.textContent = "";
      const f = e.target;
      const btn = f.querySelector(".primary"); btn.disabled = true;
      try {
        await stream(endpoint(f), (ev) => {
          if (ev.error) termEl.textContent += `\nError: ${ev.error}`;
          else termEl.textContent += ev.line + "\n";
          termEl.scrollTop = termEl.scrollHeight;
        }, ctrl.signal);
      } catch (err) { if (err.name !== "AbortError") toastErr(err); }
      btn.disabled = false;
    };
  }
  const ping = (b) => term(b, "Ping", "1.1.1.1 o google.com", (f) => `/tools/ping?host=${encodeURIComponent(f.host.value.trim())}&count=${f.count.value}`,
    `<select name="count" style="max-width:110px"><option value="10">10</option><option value="4">4</option><option value="30">30</option><option value="100">100</option></select>`);
  const trace = (b) => term(b, "Trazar", "google.com", (f) => `/tools/traceroute?host=${encodeURIComponent(f.host.value.trim())}`);

  function lookup(b) {
    b.innerHTML = `<div class="card"><form class="inline-form mb" id="f"><input name="name" placeholder="dominio o IP" required>
      <select name="type" style="max-width:110px">${["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "PTR", "SRV", "CAA", "HTTPS", "DS", "DNSKEY"].map((t) => `<option>${t}</option>`).join("")}</select>
      <select name="server" style="max-width:240px"><option value="">NetPing (con filtros)</option><option value="127.0.0.1:5335">Unbound</option><option>1.1.1.1</option><option>8.8.8.8</option><option>9.9.9.9</option><option value="https://1.1.1.1/dns-query">Cloudflare DoH</option><option value="tls://9.9.9.9#dns.quad9.net">Quad9 DoT</option></select>
      <button class="btn primary">${ico("search")}Consultar</button></form><div id="out"></div></div>`;
    b.querySelector("#f").onsubmit = (e) => {
      e.preventDefault();
      const f = e.target;
      busy(f.querySelector("button"), async () => {
        const r = await api.post("/tools/lookup", { name: f.name.value.trim(), type: f.type.value, server: f.server.value });
        const rows = (list) => list.map((a) => `<tr><td class="mono">${esc(a.name)}</td><td><span class="badge">${esc(a.type)}</span></td><td>${a.ttl}</td><td class="mono" style="word-break:break-all">${esc(a.data)}</td></tr>`).join("");
        b.querySelector("#out").innerHTML = `<div class="row mb"><span class="badge ${r.rcode === "NOERROR" ? "ok" : "err"}">${esc(r.rcode)}</span><span class="badge info">${ms(r.rtt_ms)}</span>
          ${r.flags.ad ? `<span class="badge ok">${ico("lock")}DNSSEC validado</span>` : ""}${r.flags.aa ? `<span class="badge">autoritativa</span>` : ""}<span class="muted small mono">${esc(r.server)}</span></div>
          <div class="table-wrap"><table><thead><tr><th>Nombre</th><th>Tipo</th><th>TTL</th><th>Dato</th></tr></thead><tbody>${rows(r.answers) || `<tr><td colspan="4" class="muted">Sin respuestas</td></tr>`}${r.authority.length ? `<tr><td colspan="4"><h4>Autoridad</h4></td></tr>${rows(r.authority)}` : ""}</tbody></table></div>`;
      }).catch(() => {});
    };
  }

  function bench(b) {
    b.innerHTML = `<div class="card"><header><h2>${ico("gauge")}Benchmark de servidores DNS</h2><button class="btn primary" id="go">${ico("play")}Ejecutar</button></header>
      <p class="muted small">Compara NetPing (con caché), Unbound y los principales DNS públicos, incluidos cifrados (DoH/DoT) y familiares.</p>
      <label class="field mt">Servidores extra (opcional, separados por espacio)<input id="extra" placeholder="192.168.1.1 tls://1.1.1.1#cloudflare-dns.com"></label><div id="out" class="mt"></div></div>`;
    b.querySelector("#go").onclick = (e) => busy(e.currentTarget, async () => {
      b.querySelector("#out").innerHTML = `<p class="muted">Midiendo… (15-30 s)</p>`;
      b.querySelector("#out").innerHTML = benchTable(await api.post("/tools/benchmark", { extended: true, custom: b.querySelector("#extra").value.split(/\s+/).filter(Boolean) }));
    }).catch(() => {});
  }

  function latency(b) {
    b.innerHTML = `<div class="card"><header><h2>${ico("zap")}Latencia a servicios globales</h2><button class="btn primary" id="go">${ico("play")}Medir</button></header><div id="out"></div></div>`;
    b.querySelector("#go").onclick = (e) => busy(e.currentTarget, async () => {
      const rows = await api.post("/tools/latency");
      b.querySelector("#out").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Destino</th><th class="right">Media</th><th class="right">Mín</th><th class="right">Jitter</th><th class="right">Pérdida</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><b>${esc(r.name)}</b> <span class="muted mono small">${esc(r.host)}</span></td><td class="right">${ms(r.avg)}</td><td class="right">${ms(r.min)}</td><td class="right">${ms(r.jitter)}</td><td class="right ${r.loss ? "err" : ""}">${r.loss}%</td></tr>`).join("")}</tbody></table></div>
        <p class="muted small mt">Para gaming mira también <a href="#/gaming">Gaming → regiones de servidores</a>.</p>`;
    }).catch(() => {});
  }

  function ports(b) {
    b.innerHTML = `<div class="card"><form class="inline-form mb" id="f"><input name="host" placeholder="192.168.1.1" required><input name="ports" value="common" placeholder="common, 80,443 o 1-1024" style="max-width:220px">
      <button class="btn primary">${ico("search")}Escanear</button></form><p class="muted small">Escanea sólo equipos de tu propiedad.</p><div id="out" class="mt"></div></div>`;
    b.querySelector("#f").onsubmit = (e) => {
      e.preventDefault(); const f = e.target;
      busy(f.querySelector("button"), async () => {
        const r = await api.post("/tools/ports", { host: f.host.value.trim(), ports: f.ports.value.trim() });
        b.querySelector("#out").innerHTML = `<p class="small mb">${r.open.length} abiertos de ${r.scanned} comprobados</p>` + (r.open.length ? `<div class="table-wrap"><table><thead><tr><th>Puerto</th><th>Servicio</th><th class="right">Tiempo</th><th></th></tr></thead><tbody>
          ${r.open.map((p) => `<tr><td class="mono">${p.port}</td><td>${esc(p.service || "—")}</td><td class="right">${ms(p.ms)}</td><td class="actions">${p.web ? `<a class="btn sm" target="_blank" rel="noopener" href="${p.web}://${esc(r.host)}:${p.port}/">${ico("external")}Abrir</a>` : ""}</td></tr>`).join("")}</tbody></table></div>` : empty("Ningún puerto abierto"));
      }).catch(() => {});
    };
  }

  function http(b) {
    b.innerHTML = `<div class="card"><form class="inline-form mb" id="f"><input name="url" placeholder="https://ejemplo.com" required><button class="btn primary">${ico("search")}Comprobar</button></form><div id="out"></div></div>`;
    b.querySelector("#f").onsubmit = (e) => {
      e.preventDefault(); const f = e.target;
      busy(f.querySelector("button"), async () => {
        const r = await api.post("/tools/http", { url: f.url.value.trim() });
        const tls = r.tls;
        b.querySelector("#out").innerHTML = r.error ? `<div class="alert err">${ico("x")}<div>${esc(r.error)}</div></div>` : `
          <div class="grid g4 mb">${[["Estado", r.status], ["Tiempo total", ms(r.total_ms)], ["DNS", ms(r.dns_ms)], ["Protocolo", r.http_version]].map(([l, v]) => `<div class="big-metric"><div class="v" style="font-size:20px">${esc(v)}</div><div class="l">${l}</div></div>`).join("")}</div>
          ${tls ? `<div class="alert ${tls.valid ? (tls.days_left < 15 ? "warn" : "ok") : "err"} mb">${ico("lock")}<div>${tls.valid ? `<b>Certificado válido</b> · ${esc(tls.subject)} · emitido por ${esc(tls.issuer)} · caduca en <b>${tls.days_left} días</b> · ${esc(tls.protocol)}` : `<b>Certificado inválido:</b> ${esc(tls.error)}`}</div></div>` : ""}
          ${r.redirects.length ? `<p class="small">Redirecciones: ${r.redirects.map(esc).join(" → ")} → ${esc(r.final_url)}</p>` : ""}
          <dl class="kv mt">${Object.entries(r.headers).map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono small">${esc(v)}</dd>`).join("")}</dl>`;
      }).catch(() => {});
    };
  }

  function whois(b) {
    b.innerHTML = `<div class="card"><form class="inline-form mb" id="f"><input name="q" placeholder="dominio.com o IP" required><button class="btn primary">${ico("search")}Consultar</button></form><pre id="out" class="hidden"></pre></div>`;
    b.querySelector("#f").onsubmit = (e) => {
      e.preventDefault(); const f = e.target;
      busy(f.querySelector("button"), async () => { const r = await api.post("/tools/whois", { query: f.q.value.trim() }); const o = b.querySelector("#out"); o.classList.remove("hidden"); o.textContent = r.output; }).catch(() => {});
    };
  }

  async function wol(b) {
    const devs = (await api.get("/devices").catch(() => ({ devices: [] }))).devices;
    b.innerHTML = `<div class="card"><p class="muted small mb">Enciende equipos apagados que tengan Wake-on-LAN activado en la BIOS/tarjeta de red.</p>
      <form class="inline-form" id="f"><select name="dev" style="max-width:300px"><option value="">— elige un dispositivo —</option>${devs.map((d) => `<option value="${esc(d.mac)}">${esc(d.name || d.hostname || d.ip || d.mac)}</option>`).join("")}</select>
      <input name="mac" placeholder="AA:BB:CC:DD:EE:FF" style="max-width:220px"><button class="btn primary">${ico("power")}Despertar</button></form></div>`;
    const f = b.querySelector("#f");
    f.dev.onchange = () => { f.mac.value = f.dev.value; };
    f.onsubmit = (e) => { e.preventDefault(); busy(f.querySelector("button"), () => api.post("/tools/wol", { mac: f.mac.value.trim() })).then(() => toast("Paquete mágico enviado")).catch(() => {}); };
  }

  show("diag");
  return () => ctrl?.abort();
}
