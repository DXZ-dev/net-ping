import { api, esc, ico, num, pct, duration, bytes, toast, toastErr, busy, toggle, help, modal, $ } from "../lib.js";

export const title = "Unbound";
export const subtitle = "Resolutor DNS recursivo propio: privacidad total y validación DNSSEC";

export async function render(el, app) {
  const d = await api.get("/unbound");
  const st = d.status, s = d.settings, x = d.stats || {};
  if (!st.available) {
    el.innerHTML = `<div class="alert warn">${ico("alert")}<div><b>Unbound no está instalado</b><p class="small">La imagen Docker y el instalador lo incluyen. En una instalación manual: <code>apt install unbound</code> y reinicia NetPing.</p></div></div>`;
    return;
  }
  el.innerHTML = `
    <div class="grid g4 mb">
      ${stat("Estado", st.running ? "Activo" : "Detenido", st.running ? `PID ${st.pid} · ${duration(st.uptime)}` : st.last_error || "", st.running ? "green" : "red", "server")}
      ${stat("Consultas", num(x.queries), `${pct(x.hit_ratio)} desde caché`, "blue", "activity")}
      ${stat("Recursión media", x.recursion_avg_ms != null ? `${x.recursion_avg_ms} ms` : "—", `mediana ${x.recursion_median_ms ?? "—"} ms`, "amber", "clock")}
      ${stat("DNSSEC", num(x.secure), `${num(x.bogus)} respuestas falsificadas bloqueadas`, "violet", "lock")}
    </div>
    <div class="grid g3">
      <form class="card span2" id="uf">
        <header><h2>${ico("settings")}Configuración</h2></header>
        <h4 class="mb">Modo de resolución</h4>
        <div class="grid g3 mb">
          ${mode("recursive", "Recursivo", "Pregunta directamente a los servidores raíz. Nadie ve todas tus consultas. Máxima privacidad.")}
          ${mode("forward-tls", "Reenvío cifrado (DoT)", "Envía las consultas cifradas a un proveedor (Cloudflare, Quad9…). Tu ISP no puede verlas.")}
          ${mode("forward", "Reenvío simple", "Reenvía a un DNS sin cifrar. Útil si tu red bloquea la recursión.")}
        </div>
        <div id="fwd" class="${s.mode === "recursive" ? "hidden" : ""} mb">
          <label class="field">Servidores de reenvío (uno por línea) ${help("Formato: IP@puerto#nombre-tls. Ej: 1.1.1.1@853#cloudflare-dns.com")}
          <textarea name="forward_servers" rows="3">${esc((s.forward_servers || []).join("\n"))}</textarea></label>
          <div class="row mt small">Rellenar con:
            <button type="button" class="btn sm" data-fwd="1.1.1.1@853#cloudflare-dns.com 1.0.0.1@853#cloudflare-dns.com">Cloudflare</button>
            <button type="button" class="btn sm" data-fwd="9.9.9.9@853#dns.quad9.net 149.112.112.112@853#dns.quad9.net">Quad9</button>
            <button type="button" class="btn sm" data-fwd="8.8.8.8@853#dns.google 8.8.4.4@853#dns.google">Google</button></div>
        </div>
        <h4 class="mb">Seguridad y privacidad</h4>
        ${o("dnssec", "Validación DNSSEC", "Rechaza respuestas falsificadas criptográficamente.")}
        ${o("qname_minimisation", "Minimización de QNAME", "Cada servidor sólo ve la parte del nombre que necesita.")}
        ${o("rebind_protection", "Protección DNS rebinding", "Impide que dominios públicos apunten a IPs de tu red local.")}
        ${o("hide_identity", "Ocultar identidad y versión", "")}
        ${o("harden_dnssec_stripped", "Endurecer DNSSEC", "Rechaza respuestas sin firma de zonas firmadas.")}
        ${o("use_caps_for_id", "0x20 (mayúsculas aleatorias)", "Protección extra contra envenenamiento. Algunos servidores antiguos fallan.")}
        <h4 class="mb mt">Velocidad</h4>
        ${o("prefetch", "Prefetch", "Renueva registros populares antes de que caduquen.")}
        ${o("prefetch_key", "Prefetch de claves DNSSEC", "")}
        ${o("serve_expired", "Servir caducados", "Responde al instante con datos caducados mientras actualiza (cero espera).")}
        ${o("aggressive_nsec", "NSEC agresivo", "Responde dominios inexistentes desde caché usando pruebas DNSSEC.")}
        ${o("do_ip6", "Usar IPv6 hacia Internet", "Sólo si tu conexión tiene IPv6.")}
        <div class="form-grid mt">
          ${num_("num_threads", "Hilos", 1, 16)} ${txt("msg_cache_size", "Caché de mensajes")} ${txt("rrset_cache_size", "Caché de registros")}
          ${num_("cache_min_ttl", "TTL mínimo (s)", 0, 3600)} ${num_("cache_max_ttl", "TTL máximo (s)", 60, 604800)} ${num_("edns_buffer_size", "Búfer EDNS", 512, 4096)}
          ${num_("verbosity", "Nivel de log", 0, 5)} ${num_("port", "Puerto local", 1024, 65535)}
        </div>
        <label class="field mt">Configuración avanzada (se añade al bloque server:)<textarea name="custom" rows="4" placeholder="# p. ej.\n# local-zone: \"example.\" refuse">${esc(s.custom || "")}</textarea></label>
        <div class="row mt">${toggle("enabled", s.enabled)}<span>Unbound activado</span><span class="grow"></span>
          <button class="btn primary">${ico("check")}Validar y aplicar</button></div>
      </form>
      <div class="col">
        <div class="card"><header><h3>${ico("power")}Acciones</h3></header>
          <div class="col">
            <button class="btn" data-a="restart">${ico("refresh")}Reiniciar Unbound</button>
            <button class="btn" data-a="flush">${ico("trash")}Vaciar caché</button>
            <button class="btn" data-a="flush_infra">${ico("trash")}Vaciar caché de infraestructura</button>
            <button class="btn" id="showconf">${ico("list")}Ver unbound.conf</button>
          </div></div>
        <div class="card"><header><h3>${ico("info")}Caché</h3></header>
          <dl class="kv"><dt>Registros</dt><dd>${bytes(x.mem_cache_rrset)}</dd><dt>Mensajes</dt><dd>${bytes(x.mem_cache_msg)}</dd>
          <dt>Prefetch</dt><dd>${num(x.prefetch)}</dd><dt>Caducados servidos</dt><dd>${num(x.expired_served)}</dd><dt>NXDOMAIN</dt><dd>${num(x.nxdomain)}</dd><dt>SERVFAIL</dt><dd>${num(x.servfail)}</dd></dl></div>
        <div class="card"><header><h3>${ico("list")}Registro</h3></header><pre style="max-height:260px">${esc((d.logs || []).slice(-40).join("\n") || "Sin mensajes")}</pre></div>
      </div>
    </div>`;

  const f = el.querySelector("#uf");
  f.querySelectorAll("[name=mode]").forEach((r) => r.onchange = () => el.querySelector("#fwd").classList.toggle("hidden", r.value === "recursive" && r.checked));
  f.querySelectorAll("[data-fwd]").forEach((b) => b.onclick = () => { f.forward_servers.value = b.dataset.fwd.split(" ").join("\n"); });
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = { mode: f.querySelector("[name=mode]:checked").value, forward_servers: f.forward_servers.value.split(/\s+/).filter(Boolean), custom: f.custom.value };
    for (const k of ["dnssec", "qname_minimisation", "rebind_protection", "hide_identity", "harden_dnssec_stripped", "use_caps_for_id", "prefetch", "prefetch_key", "serve_expired", "aggressive_nsec", "do_ip6", "enabled"]) body[k] = f[k].checked;
    for (const k of ["num_threads", "cache_min_ttl", "cache_max_ttl", "edns_buffer_size", "verbosity", "port"]) body[k] = Number(f[k].value);
    for (const k of ["msg_cache_size", "rrset_cache_size"]) body[k] = f[k].value;
    await busy(f.querySelector("button.primary"), () => api.put("/unbound", body)).then(() => { toast("Unbound reconfigurado"); setTimeout(() => app.reload(), 1200); }).catch(() => {});
  };
  el.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => busy(b, () => api.post(`/unbound/${b.dataset.a}`)).then(() => { toast("Hecho"); setTimeout(() => app.reload(), 800); }).catch(() => {}));
  el.querySelector("#showconf").onclick = async () => { const r = await api.get("/unbound/config"); modal({ title: "unbound.conf", wide: true, body: `<pre>${esc(r.config)}</pre>` }); };

  function mode(v, t, desc) { return `<label class="preset ${s.mode === v ? "active" : ""}"><input type="radio" name="mode" value="${v}" ${s.mode === v ? "checked" : ""} class="hidden" onchange="this.closest('.grid').querySelectorAll('.preset').forEach(p=>p.classList.toggle('active',p.contains(this)))"><b>${t}</b><span>${desc}</span></label>`; }
  function o(k, t, desc) { return `<div class="opt"><div class="t"><b>${t}</b>${desc ? `<span>${desc}</span>` : ""}</div>${toggle(k, s[k])}</div>`; }
  function num_(k, l, min, max) { return `<label class="field">${l}<input type="number" name="${k}" value="${esc(s[k])}" min="${min}" max="${max}"></label>`; }
  function txt(k, l) { return `<label class="field">${l}<input name="${k}" value="${esc(s[k])}" pattern="[0-9]+[kmgKMG]?"></label>`; }
}

const stat = (l, v, sub, tint, icon) => `<div class="card stat"><div class="ic tint-${tint}">${ico(icon)}</div><div class="truncate"><div class="v">${esc(v)}</div><div class="l">${esc(l)} · <span class="small">${esc(sub)}</span></div></div></div>`;
