// Detalle de cada tarjeta del panel (se abre al hacer clic; cada una muestra su propia información).
import { api, esc, ico, num, pct, ms, ago, duration, bytes, modal } from "../lib.js";
import { t } from "../i18n.js";

const bars = (rows, label, value, fmt = num, color = "var(--accent)") => {
  if (!rows?.length) return `<p class="muted small">${t("Sin datos todavía")}</p>`;
  const max = Math.max(...rows.map(value), 1);
  return `<div class="col">${rows.map((r) => `<div><div class="row between small"><span class="truncate">${esc(label(r))}</span><b>${fmt(value(r))}</b></div>
    <div class="progress" style="height:6px"><i style="width:${Math.max(2, (value(r) / max) * 100)}%;background:${color}"></i></div></div>`).join("")}</div>`;
};
const kv = (pairs) => `<dl class="kv small">${pairs.filter(Boolean).map(([k, v]) => `<dt>${esc(t(k))}</dt><dd>${v}</dd>`).join("")}</dl>`;
const go = (href, label) => `<a class="btn sm mt" href="${href}" data-close>${ico("external")}${t(label)}</a>`;
const tip = (text) => `<div class="alert info mt">${ico("info")}<div class="small">${t(text)}</div></div>`;
const range = (h) => (h === 1 ? t("última hora") : h === 24 ? t("últimas 24 h") : t("últimos 7 días"));

export async function openDetail(id, data, hours) {
  const { s, sys, devs, sec } = data;
  let title = "", body = "";
  switch (id) {
    case "st_queries":
      title = t("Consultas DNS");
      body = kv([["Periodo", range(hours)], ["Total", num(s.total)], ["Clientes distintos", num(s.clients)], ["Desde caché", `${num(s.cached)} (${pct(s.total ? (s.cached / s.total) * 100 : 0)})`]])
        + `<h4 class="mt mb">${t("Quién consulta más")}</h4>` + bars(s.top_clients, (r) => (r.name === r.client ? r.client : `${r.name} (${r.client})`), (r) => r.n)
        + `<h4 class="mt mb">${t("Tipos de consulta")}</h4>` + bars(s.qtypes, (r) => r.qtype, (r) => r.n, num, "var(--accent-2)")
        + go("#/queries", "Ver el registro de consultas");
      break;
    case "st_blocked":
      title = t("Bloqueos");
      body = kv([["Periodo", range(hours)], ["Bloqueadas", `${num(s.blocked)} (${pct(s.blocked_pct)})`], ["Dominios en listas", num(s.domains_blocked)]])
        + `<h4 class="mt mb">${t("Lo más bloqueado")}</h4>` + bars(s.top_blocked, (r) => r.domain, (r) => r.n, num, "var(--err)")
        + `<h4 class="mt mb">${t("Por motivo")}</h4>` + bars((s.statuses || []).filter((x) => /Bloq|Amenaza|parental|Seguridad|TLD|país|Rebinding/i.test(x.name)), (r) => t(r.name), (r) => r.n, num, "var(--err)")
        + go("#/blocking", "Gestionar listas y reglas");
      break;
    case "st_response": {
      title = t("Tiempo de respuesta DNS");
      const ups = sys.dns.upstreams || [];
      body = kv([["Respuesta media (Bipon)", ms(sys.dns.avg_response_ms)], ["Media de los servidores DNS", ms(s.avg_upstream_ms)], ["Aciertos de caché", pct(sys.dns.cache.hit_ratio)]])
        + `<h4 class="mt mb">${t("Servidores DNS en uso")}</h4>` + bars(ups.filter((u) => u.avg_ms != null), (u) => `${u.server} (${u.proto})${u.failures ? ` · ${u.failures} ${t("fallos")}` : ""}`, (u) => u.avg_ms, ms, "var(--ok)")
        + `<h4 class="mt mb">${t("Cómo se respondió")}</h4>` + bars(s.statuses, (r) => t(r.name), (r) => r.n)
        + tip("Cuanto más alto el % de caché, más consultas se responden en menos de 1 ms. El optimizador elige el DNS más rápido desde tu ubicación.")
        + go("#/dns", "Optimizar DNS");
      break;
    }
    case "st_latency": {
      title = t("Latencia a Internet");
      const lag = await api.get("/lag").catch(() => null);
      const res = sys.monitor?.results || {};
      body = (lag ? kv([["Ping ahora", ms(lag.ping)], ["Normal de tu línea", ms(lag.baseline)], ["Jitter (variación)", lag.jitter != null ? `${lag.jitter} ms` : "—"], ["Pérdida (5 min)", `${lag.loss} %`]]) : "")
        + `<h4 class="mt mb">${t("Por destino")}</h4>` + bars(Object.entries(res).map(([k, v]) => ({ k, ...v })), (r) => (r.k === "gateway" ? t("Router") : r.k) + (r.loss ? ` · ${r.loss}% ${t("pérdida")}` : ""), (r) => r.avg ?? 0, ms, "var(--accent-2)")
        + (lag?.culprits?.length ? `<h4 class="mt mb">${t("Qué está cargando la red ahora")}</h4>` + lag.culprits.slice(0, 4).map((c) => `<div class="row between small rec mb"><b>${esc(c.name)}</b><span class="muted">${esc(c.reasons.map((x) => t(x)).join(", "))}</span></div>`).join("") : "")
        + tip("Router → tu red local (debería ser < 2 ms). 1.1.1.1 / 8.8.8.8 → tu salida a Internet. Si sube mucho cuando alguien descarga, mira el Vigilante de lag.")
        + go("#/gaming", "Abrir Vigilante de lag");
      break;
    }
    case "st_devices": {
      title = t("Dispositivos");
      const on = devs.filter((d) => d.online), off = devs.filter((d) => !d.online), paused = devs.filter((d) => d.internet_blocked);
      const row = (d) => `<div class="row between small" style="padding:4px 0"><span class="truncate"><span class="dot ${d.internet_blocked ? "dot-err" : d.online ? "dot-ok" : "dot-off"}"></span> <b>${esc(d.name || d.friendly || d.hostname || d.vendor || d.ip)}</b> <span class="muted">${esc(d.ip || "")}</span></span><span class="muted nowrap">${d.online ? ms(d.latency) : ago(d.last_seen)}</span></div>`;
      body = kv([["En línea", `${on.length}/${devs.length}`], ["Con Internet pausado", String(paused.length)], ["Desconectados", String(off.length)]])
        + `<h4 class="mt mb">${t("En línea")}</h4>${on.map(row).join("") || `<p class="muted small">${t("Ninguno")}</p>`}`
        + (off.length ? `<details class="mt"><summary class="small">${t("Desconectados")} (${off.length})</summary>${off.map(row).join("")}</details>` : "")
        + go("#/devices", "Ver el mapa de dispositivos");
      break;
    }
    case "st_domains": {
      title = t("Listas de bloqueo");
      const lists = await api.get("/lists").catch(() => []);
      const arr = Array.isArray(lists) ? lists : lists.lists || [];
      body = kv([["Dominios en listas", num(s.domains_blocked)], ["Listas activas", `${arr.filter((l) => l.enabled).length}/${arr.length}`]])
        + `<h4 class="mt mb">${t("Listas activas")}</h4>` + bars(arr.filter((l) => l.enabled), (l) => `${l.name}${l.last_status && !/^ok/i.test(l.last_status) ? " ⚠" : ""}`, (l) => l.domains || 0)
        + go("#/blocking", "Gestionar listas");
      break;
    }
    case "st_qps":
      title = t("Consultas por segundo");
      body = kv([["Ahora", `${sys.dns.qps ?? 0} /s`], ["Desde el arranque", num(sys.dns.total)], ["Bloqueadas desde el arranque", num(sys.dns.blocked)], ["Errores", num(sys.dns.errors)], ["Bipon activo", duration(sys.dns.uptime)]])
        + tip("Un pico repentino de consultas en un solo equipo puede ser malware o una app defectuosa: Seguridad lo analiza cada 5 minutos.")
        + go("#/security", "Ver seguridad");
      break;
    case "st_uptime":
    case "system": {
      title = t("Sistema");
      const mem = sys.memory || {}, disk = sys.disk || {};
      body = kv([["Equipo", esc(sys.hostname || "")], ["Versión", esc(sys.version || "")], ["IP", esc(sys.ip || "")], ["Bipon activo", duration(sys.app_uptime)], ["Sistema encendido", duration(sys.uptime)],
        ["Carga CPU", `${(sys.load || []).join(" / ")} (${sys.cpus} CPU)`], mem.total ? ["Memoria", `${bytes(mem.total - mem.available)} / ${bytes(mem.total)}`] : null,
        disk.total ? ["Disco", `${bytes(disk.used ?? disk.total - disk.free)} / ${bytes(disk.total)}`] : null, sys.temperature != null ? ["Temperatura", `${sys.temperature} °C`] : null,
        ["Unbound", sys.unbound?.running ? t("Activo") : t("Detenido")]])
        + go("#/settings", "Ajustes del sistema");
      break;
    }
    case "st_cache": {
      title = t("Caché DNS");
      const c = sys.dns.cache || {};
      body = kv([["Aciertos", pct(c.hit_ratio)], ["Entradas", `${num(c.entries)} / ${num(c.size)}`], ["Respondidas desde caché", num(c.hits)], ["Respondidas caducadas al instante (optimista)", num(c.stale_hits)], ["Fallos de caché", num(c.misses)]])
        + tip("La caché optimista responde al instante aunque el dato haya caducado y lo renueva por detrás: cero espera en juegos y vídeo.")
        + go("#/dns", "Ajustes de caché");
      break;
    }
    case "st_threats": {
      title = t("Amenazas y anomalías");
      const list = (sec.anomalies || []).slice(0, 20);
      body = list.length ? list.map((a) => `<div class="rec mb small"><div class="row between"><b>${esc(a.title || a.kind || "")}</b><span class="muted">${ago(a.ts)}</span></div><div class="muted">${esc(a.detail || a.message || "")}</div></div>`).join("")
        : `<p class="muted small">${t("Sin anomalías. Tu red está tranquila.")}</p>`;
      body += go("#/security", "Ver seguridad");
      break;
    }
    case "st_speed": {
      title = t("Velocidad");
      const hist = await api.get("/speedtest/history").catch(() => []);
      const rows = (Array.isArray(hist) ? hist : hist.history || []).slice(0, 8);
      body = rows.length ? bars(rows, (r) => `${new Date(r.ts * 1000).toLocaleString()} · ↑${(r.upload ?? 0).toFixed?.(0) ?? r.upload} · ${r.grade || ""}`, (r) => r.download || 0, (v) => `${Number(v).toFixed(0)} Mbps`, "var(--ok)")
        : `<p class="muted small">${t("Aún no hay tests.")}</p>`;
      body += go("#/speed", "Hacer un test");
      break;
    }
    default:
      return;
  }
  modal({ title, wide: true, body, actions: [{ label: t("Cerrar") }] });
  // los enlaces «ir a…» cierran la ventana
  document.querySelectorAll(".overlay [data-close]").forEach((a) => a.addEventListener("click", () => a.closest(".overlay")?.remove()));
}
