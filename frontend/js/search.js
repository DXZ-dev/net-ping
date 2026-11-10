// Buscador global: salta a cualquier página, pestaña u opción (Ctrl+K o «/»).
import { esc, ico } from "./lib.js";
import { t } from "./i18n.js";

// [texto, página, pestaña?, texto a resaltar?, palabras clave extra]
const ENTRIES = [
  // páginas
  ["Panel", "dashboard", null, null, "inicio resumen dashboard tarjetas editar panel"],
  ["Asistente inteligente", "assistant", null, null, "analizar mi red optimizar recomendado ia"],
  ["Consultas", "queries", null, null, "registro dns log historial dominios"],
  ["Dispositivos", "devices", null, null, "equipos mapa red clientes mac ip marca"],
  ["Control parental", "parental", null, null, "niños perfiles horarios hijos familia"],
  ["Tiempo de pantalla", "parental", null, "Tiempo de pantalla de hoy", "limite diario minutos"],
  ["Pausa familiar", "parental", null, null, "cortar internet todos pausar"],
  ["Bloqueo", "blocking", null, null, "anuncios publicidad"],
  ["Listas de bloqueo", "blocking", "lists", null, "listas adlist stevenblack hagezi adguard"],
  ["Reglas propias", "blocking", "rules", null, "permitir bloquear regex whitelist blacklist"],
  ["Bloquear aplicaciones en toda la red", "blocking", "apps", null, "tiktok instagram fortnite apps redes sociales"],
  ["Comprobar dominio", "blocking", "check", null, "por qué se bloquea"],
  ["Seguridad", "security", null, null, "amenazas malware"],
  ["Detección de anomalías", "security", null, "Anomalías detectadas", "dga tunel malware"],
  ["Bloquear servidores por país", "security", null, "Bloquear servidores por país", "geoip pais"],
  ["Bloquear dominios de primer nivel (TLD)", "security", null, "Bloquear dominios de primer nivel (TLD)", "tld"],
  ["Protecciones", "security", null, "Protecciones", "rebinding private relay doh"],
  ["DNS y latencia", "dns", null, null, "servidores upstream cloudflare google quad9"],
  ["Optimizador de latencia", "dns", null, "Optimizador de latencia", "dns mas rapido"],
  ["Servidor DNS upstream", "dns", null, "Servidor DNS upstream", "dot doh estrategia"],
  ["Caché y rendimiento", "dns", null, "Caché y rendimiento", "cache optimista prefetch ttl"],
  ["Registros DNS locales", "dns", null, "Registros DNS locales", "nombres locales lan hosts"],
  ["Reenvío condicional", "dns", null, "Reenvío condicional", "conditional forwarding"],
  ["Unbound", "unbound", null, null, "recursivo dnssec resolver"],
  ["Gaming y streaming", "gaming", null, null, "juegos ping lag netflix"],
  ["Ping a regiones de servidores de juego", "gaming", null, "Ping a regiones de servidores de juego", "region juego"],
  ["Proximidad de CDN de streaming", "gaming", null, "Proximidad de CDN de streaming", "cdn netflix youtube"],
  ["QoS anti-bufferbloat (CAKE)", "gaming", null, "QoS anti-bufferbloat (CAKE)", "qos sqm"],
  ["Velocidad", "speed", null, null, "speedtest test de velocidad internet bufferbloat"],
  ["Modo automático", "network", "auto", null, "ipv6 rdnss sin tocar router"],
  ["DHCP", "network", "dhcp", null, "servidor dhcp ips reservas concesiones"],
  ["Wi-Fi", "network", "wifi", null, "unifi openwrt mikrotik canal señal"],
  ["Portal de bienvenida", "network", "captive", null, "portal cautivo terminos condiciones"],
  ["Acceso local / externo", "network", "access", null, "acceso remoto seguridad panel exterior"],
  ["Mi router", "router", "control", null, "router isp huawei openwrt mikrotik fritzbox reiniciar"],
  ["Modo router y firewall", "router", "mode", null, "firewall nftables zonas vlan redireccion puertos"],
  ["Velocidad por dispositivo", "router", "speed", null, "limitar velocidad limite ancho de banda"],
  ["Consumo de datos", "router", "traffic", null, "trafico datos consumo"],
  ["NAT y CGNAT", "connectivity", null, null, "cgnat puertos abiertos"],
  ["Tipo de NAT", "connectivity", null, "Tipo de NAT", "nat abierto estricto moderado"],
  ["Estados del NAT", "connectivity", null, "Estados del NAT", "nat estados"],
  ["Sesiones NAT (tabla de conexiones)", "connectivity", null, "Sesiones NAT (tabla de conexiones)", "conntrack"],
  ["Puertos del router (UPnP)", "connectivity", null, "Puertos del router (UPnP)", "abrir puerto upnp"],
  ["WireGuard", "vpn", "wireguard", null, "vpn wg qr"],
  ["OpenVPN", "vpn", "openvpn", null, "vpn ovpn"],
  ["Tailscale", "vpn", "tailscale", null, "vpn exit node instalar tailscale"],
  ["VPS de relevo (CGNAT)", "vpn", "relay", null, "vps relay"],
  ["Monitor", "monitor", null, null, "latencia perdida cortes"],
  ["Herramientas", "tools", null, null, "diagnostico"],
  ["Ping", "tools", "ping", null, ""], ["Traceroute", "tools", "trace", null, ""], ["Consulta DNS", "tools", "lookup", null, "dig nslookup"],
  ["Benchmark DNS", "tools", "bench", null, ""], ["Escanear puertos abiertos", "tools", "ports", null, "puertos scanner"],
  ["Whois", "tools", "whois", null, ""], ["Wake-on-LAN", "tools", "wol", null, "encender equipo"],
  ["Proxmox", "infra", "proxmox", null, "vm lxc contenedores servidor"],
  ["Puertos y conexiones", "infra", "ports", null, "tcp udp puertos en uso aplicacion"],
  ["Procesos de Bipon", "infra", "procs", null, "procesos memoria"],
  ["Notificaciones", "notifications", null, null, "telegram discord email avisos push"],
  ["Horas de silencio", "notifications", null, "Horas de silencio", ""],
  ["Informes", "reports", null, null, "informe semanal reporte"],
  ["Integraciones", "integrations", null, null, ""],
  ["Proxmox VE", "integrations", null, "Proxmox VE", "token api proxmox"],
  ["Home Assistant", "integrations", null, "Home Assistant", "domotica"],
  ["Alta disponibilidad (dos Bipon)", "integrations", null, "Alta disponibilidad (dos Bipon)", "ha secundario sincronizar"],
  ["Usuarios y acceso", "access", null, null, "usuarios roles"],
  ["Verificación en dos pasos", "access", null, "Tu verificación en dos pasos", "2fa totp autenticador"],
  ["Tokens de API", "access", null, "Tokens de API", "api token"],
  ["HTTPS y DNS cifrado", "access", null, "HTTPS y DNS cifrado", "certificado tls dot"],
  ["Ajustes", "settings", null, null, "configuracion"],
  ["Apariencia", "settings", "look", null, "tema colores glass oscuro claro efectos"],
  ["Idioma y región", "settings", "lang", null, "idioma ingles español pais zona horaria"],
  ["Actualizaciones", "settings", "updates", null, "actualizar github version"],
  ["Perfiles de configuración", "settings", "configs", null, "guardar configuracion escenario"],
  ["Copia de seguridad", "settings", "system", null, "backup restaurar exportar"],
  ["Auditoría", "settings", "audit", null, "quien cambio que"],
  ["Cambiar mi contraseña", "settings", "account", null, "contraseña cuenta"],
];
const STORE = { network: "np-net-tab", router: "np-rt-tab", infra: "np-infra-tab", settings: "np-set-tab", vpn: "np-vpn-tab" };

const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

let allowedPages = null;
let pageLabels = {};
export function initSearch(nav, role) {
  allowedPages = new Set(nav.filter((n) => n.path && n.roles.includes(role)).map((n) => n.path));
  pageLabels = Object.fromEntries(nav.filter((n) => n.path).map((n) => [n.path, n.label]));
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable;
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); openSearch(); }
  });
}

function results(q) {
  const words = norm(q).split(/\s+/).filter(Boolean);
  const list = ENTRIES.filter((e) => allowedPages?.has(e[1]));
  if (!words.length) return list.filter((e) => !e[2] && !e[3]).slice(0, 12);
  const scored = [];
  for (const e of list) {
    const label = norm(e[0]) + " " + norm(t(e[0]));
    const hay = `${label} ${norm(pageLabels[e[1]])} ${norm(t(pageLabels[e[1]] || ""))} ${norm(e[4])}`;
    if (!words.every((w) => hay.includes(w))) continue;
    scored.push([words.reduce((s, w) => s + (label.startsWith(w) ? 4 : label.includes(w) ? 2 : 1), 0) + (e[2] || e[3] ? 0 : 0.5), e]);
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, 14).map((x) => x[1]);
}

export function openSearch() {
  if (document.getElementById("gsearch")) return;
  const box = document.createElement("div");
  box.id = "gsearch";
  box.className = "gsearch-backdrop";
  box.innerHTML = `<div class="gsearch" role="dialog" aria-modal="true">
    <div class="gsearch-in">${ico("search")}<input id="gs-q" placeholder="${t("Buscar opciones, páginas o ajustes…")}" autocomplete="off"><kbd>Esc</kbd></div>
    <div class="gsearch-list" id="gs-list"></div></div>`;
  document.body.appendChild(box);
  const input = box.querySelector("#gs-q");
  const listEl = box.querySelector("#gs-list");
  let items = [];
  let sel = 0;
  const draw = () => {
    items = results(input.value);
    sel = Math.min(sel, Math.max(0, items.length - 1));
    listEl.innerHTML = items.length ? items.map((e, i) => `<button type="button" class="gs-item ${i === sel ? "sel" : ""}" data-i="${i}">
        <span class="gs-t">${esc(t(e[0]))}</span><span class="gs-p">${esc(t(pageLabels[e[1]] || ""))}${e[2] || e[3] ? " ›" : ""}</span></button>`).join("")
      : `<p class="muted small" style="padding:14px">${t("Sin resultados")}</p>`;
  };
  const close = () => box.remove();
  const pick = (e) => { close(); go(e); };
  input.oninput = () => { sel = 0; draw(); };
  input.onkeydown = (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); sel = Math.min(sel + 1, items.length - 1); draw(); listEl.querySelector(".sel")?.scrollIntoView({ block: "nearest" }); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); sel = Math.max(sel - 1, 0); draw(); listEl.querySelector(".sel")?.scrollIntoView({ block: "nearest" }); }
    else if (ev.key === "Enter" && items[sel]) { ev.preventDefault(); pick(items[sel]); }
    else if (ev.key === "Escape") close();
  };
  listEl.onclick = (ev) => { const b = ev.target.closest("[data-i]"); if (b) pick(items[+b.dataset.i]); };
  box.onclick = (ev) => { if (ev.target === box) close(); };
  draw();
  input.focus();
}

function go([, page, tab, find]) {
  if (tab && STORE[page]) { try { sessionStorage.setItem(STORE[page], tab); } catch {} }
  const target = `#/${page}`;
  if (location.hash !== target) location.hash = target;
  else window.dispatchEvent(new HashChangeEvent("hashchange"));
  const start = Date.now();
  const step = () => {
    if (Date.now() - start > 5000) return;
    const page_ = document.getElementById("page");
    if (tab) {
      const b = page_?.querySelector(`[data-tab="${tab}"], [data-t="${tab}"]`);
      if (!b) return setTimeout(step, 120);
      if (!b.classList.contains("active")) b.click();
    }
    if (find) {
      const want = [norm(find), norm(t(find))];
      const el = [...(page_?.querySelectorAll("h2, h3, h4, b, label") || [])].find((x) => want.some((w) => norm(x.textContent).trim().startsWith(w)));
      if (!el) return setTimeout(step, 150);
      const card = el.closest(".card") || el;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.remove("flash");
      void card.offsetWidth;
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 2200);
    }
  };
  setTimeout(step, 150);
}
