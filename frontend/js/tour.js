// Guía interactiva por el sistema: resalta cada sección y explica para qué sirve.
import { $, esc, api } from "./lib.js";
import { t } from "./i18n.js";
import { loadPrefs, savePrefs } from "./theme.js";

const STEPS = [
  { sel: ".brand", title: "Bienvenido a Bipon Net", text: "Te enseñamos el sistema en un minuto. Puedes omitir la guía cuando quieras y volver a verla desde Ajustes → Apariencia." },
  { sel: '.nav a[data-path="dashboard"]', title: "Panel", text: "Tu red de un vistazo: consultas, bloqueos, latencia, dispositivos conectados y acciones rápidas. Pulsa «Editar panel» para mover, añadir o quitar tarjetas." },
  { sel: '.nav a[data-path="assistant"]', title: "Asistente inteligente", text: "Analiza tu red (DNS más rápido desde tu zona, NAT, hardware, dispositivos) y aplica la configuración óptima con un clic." },
  { sel: '.nav a[data-path="devices"]', title: "Dispositivos en tiempo real", text: "Todos los equipos con su marca, modelo y jerarquía (p. ej. Proxmox → sus VMs). Abre uno para ver qué apps usa, pausar su Internet al instante, limitar su velocidad o ver sus puertos." },
  { sel: '.nav a[data-path="parental"]', title: "Control parental", text: "Perfiles con horarios, apps bloqueadas, búsqueda segura y tiempo de pantalla diario. Asigna un perfil a cada dispositivo." },
  { sel: '.nav a[data-path="blocking"]', title: "Bloqueo", text: "Listas de anuncios y malware, tus propias reglas y el bloqueo de apps (TikTok, Fortnite…) en toda la red." },
  { sel: '.nav a[data-path="security"]', title: "Seguridad", text: "Detección de malware y comportamientos raros, bloqueo por país o dominio y protección contra ataques DNS." },
  { sel: '.nav a[data-path="dns"]', title: "DNS y latencia", text: "Elige tu DNS o deja que Bipon mida y use el más rápido desde tu ubicación. Unbound te da un DNS propio y privado." },
  { sel: '.nav a[data-path="gaming"]', title: "Gaming y streaming", text: "Cero espera de DNS para juegos y plataformas de vídeo, ping a regiones de servidores y proximidad de CDN." },
  { sel: '.nav a[data-path="network"]', title: "Red", text: "Modo automático (sin tocar el router), servidor DHCP, cobertura Wi-Fi, portal de bienvenida con términos y acceso local/externo." },
  { sel: '.nav a[data-path="router"]', title: "Router y firewall", text: "Controla tu router, crea zonas para IoT o invitados y limita la velocidad de cada dispositivo." },
  { sel: '.nav a[data-path="vpn"]', title: "VPN", text: "Conéctate a casa desde cualquier sitio con WireGuard, OpenVPN o Tailscale (también si tu proveedor usa CGNAT)." },
  { sel: '.nav a[data-path="infra"]', title: "Infraestructura", text: "Tus servidores Proxmox y sus VMs, los procesos de Bipon y qué puertos TCP/UDP usa cada aplicación." },
  { sel: '.nav a[data-path="notifications"]', title: "Notificaciones", text: "Avisos en Telegram, Discord, ntfy, email… cuando entra un dispositivo nuevo, cae Internet o se bloquea una amenaza." },
  { sel: "#topbar-status", title: "Estado de la protección", text: "Aquí ves si el bloqueo está activo y puedes pausarlo unos minutos. El control parental sigue funcionando aunque lo pauses." },
  { sel: '.nav a[data-path="settings"]', title: "Ajustes", text: "Temas y efectos visuales, idioma, actualizaciones desde GitHub, perfiles de configuración y copias de seguridad. ¡Listo, ya conoces Bipon Net!" },
];

let state = null;

export function startTour(force = false) {
  const prefs = loadPrefs();
  if (!force && prefs.tour_done) return;
  stopTour();
  state = { i: 0, steps: STEPS.filter((s) => document.querySelector(s.sel)) };
  if (!state.steps.length) return;
  const ov = document.createElement("div");
  ov.id = "tour";
  ov.innerHTML = `<div class="tour-hole"></div><div class="tour-pop" role="dialog" aria-live="polite"></div>`;
  document.body.append(ov);
  window.addEventListener("resize", place);
  document.addEventListener("keydown", keys);
  step();
}

function keys(e) {
  if (!state) return;
  if (e.key === "Escape") finish();
  if (e.key === "ArrowRight") next(1);
  if (e.key === "ArrowLeft") next(-1);
}

function step() {
  const s = state.steps[state.i];
  const el = document.querySelector(s.sel);
  if (window.innerWidth <= 900 && s.sel.startsWith(".nav")) $(".app")?.classList.add("nav-open");
  el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const pop = $("#tour .tour-pop");
  const last = state.i === state.steps.length - 1;
  pop.innerHTML = `<div class="small muted">${state.i + 1} / ${state.steps.length}</div><h3 style="margin:4px 0 6px">${esc(t(s.title))}</h3><p class="small" style="margin:0 0 12px">${esc(t(s.text))}</p>
    <div class="row between"><button class="btn sm ghost" data-t="skip">${t("Omitir guía")}</button><span class="row" style="gap:6px">
    ${state.i ? `<button class="btn sm" data-t="prev">${t("Atrás")}</button>` : ""}<button class="btn sm primary" data-t="next">${last ? t("Terminar") : t("Siguiente")}</button></span></div>`;
  pop.querySelector('[data-t="skip"]').onclick = finish;
  pop.querySelector('[data-t="next"]').onclick = () => (last ? finish() : next(1));
  const pv = pop.querySelector('[data-t="prev"]');
  if (pv) pv.onclick = () => next(-1);
  setTimeout(place, 250);
}

function next(d) {
  state.i = Math.max(0, Math.min(state.steps.length - 1, state.i + d));
  step();
}

function place() {
  if (!state) return;
  const el = document.querySelector(state.steps[state.i].sel);
  const hole = $("#tour .tour-hole"), pop = $("#tour .tour-pop");
  if (!el || !hole) return;
  const r = el.getBoundingClientRect();
  const pad = 6;
  Object.assign(hole.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
  const pw = Math.min(340, window.innerWidth - 24);
  let left = r.right + 16, top = r.top;
  if (left + pw > window.innerWidth - 12) { left = Math.max(12, Math.min(r.left, window.innerWidth - pw - 12)); top = r.bottom + 14; }
  if (top + pop.offsetHeight > window.innerHeight - 12) top = Math.max(12, r.top - pop.offsetHeight - 14);
  Object.assign(pop.style, { left: `${left}px`, top: `${Math.max(12, top)}px`, width: `${pw}px` });
}

function finish() {
  const p = { ...loadPrefs(), tour_done: true };
  savePrefs(p);
  api.put("/system/prefs", { ui: { tour_done: true } }).catch(() => {});
  $(".app")?.classList.remove("nav-open");
  stopTour();
}

export function stopTour() {
  $("#tour")?.remove();
  window.removeEventListener("resize", place);
  document.removeEventListener("keydown", keys);
  state = null;
}
