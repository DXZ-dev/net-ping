import { $, $$, api, esc, h, ico, toast, toastErr, busy, modal, duration, fresh } from "./lib.js";

const LOGO = `<div class="brand-logo"><svg class="ico" viewBox="0 0 24 24"><path d="M3 13h4l2.5-6 4 11 2.5-5H21"/></svg></div>`;

const NAV = [
  { section: "General" },
  { path: "dashboard", label: "Panel", icon: "home", module: "dashboard" },
  { path: "assistant", label: "Asistente", icon: "zap", module: "assistant", badge: "IA" },
  { path: "queries", label: "Consultas", icon: "list", module: "queries" },
  { section: "Red y familia" },
  { path: "devices", label: "Dispositivos", icon: "devices", module: "devices" },
  { path: "parental", label: "Control parental", icon: "users", module: "parental" },
  { path: "blocking", label: "Bloqueo", icon: "shield", module: "blocking" },
  { section: "DNS" },
  { path: "dns", label: "DNS y latencia", icon: "globe", module: "dns" },
  { path: "unbound", label: "Unbound", icon: "server", module: "unbound" },
  { section: "Conexión" },
  { path: "gaming", label: "Gaming y streaming", icon: "console", module: "gaming" },
  { path: "speed", label: "Velocidad", icon: "gauge", module: "speed" },
  { path: "connectivity", label: "NAT y CGNAT", icon: "network", module: "connectivity" },
  { path: "vpn", label: "VPN", icon: "lock", module: "vpn" },
  { path: "monitor", label: "Monitor", icon: "activity", module: "monitor" },
  { path: "tools", label: "Herramientas", icon: "tool", module: "tools" },
  { section: "Sistema" },
  { path: "settings", label: "Ajustes", icon: "settings", module: "settings" },
];

let cleanup = null;
let statusTimer = null;

// ---------------------------------------------------------------- autenticación
async function boot() {
  let st;
  try { st = await api.get("/auth/status"); }
  catch { $("#root").innerHTML = `<div class="login"><div class="card"><p>No se puede contactar con NetPing.</p></div></div>`; return; }
  if (st.setup_required) return renderAuth("setup", st.version);
  if (!st.authenticated) return renderAuth("login", st.version);
  renderShell(st.version);
}

function renderAuth(mode, version) {
  const setup = mode === "setup";
  $("#root").innerHTML = `
  <div class="login"><form class="card col" autocomplete="on">
    <div class="brand">${LOGO}<div><b>NetPing</b><small>Red doméstica inteligente</small></div></div>
    <h2 class="center">${setup ? "Bienvenido 👋" : "Iniciar sesión"}</h2>
    <p class="muted center small">${setup ? "Crea la contraseña de administrador para proteger el panel de tu red." : "Introduce la contraseña de administrador."}</p>
    <label class="field">Contraseña<input type="password" name="password" required minlength="${setup ? 8 : 1}" autocomplete="${setup ? "new-password" : "current-password"}"></label>
    ${setup ? `<label class="field">Repite la contraseña<input type="password" name="password2" required minlength="8" autocomplete="new-password"></label>` : ""}
    <button class="btn primary lg" type="submit">${setup ? "Crear y entrar" : "Entrar"}</button>
    <p class="muted small center">v${esc(version)}</p>
  </form></div>`;
  const form = $("form");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const pw = form.password.value;
    if (setup && pw !== form.password2.value) return toast("Las contraseñas no coinciden", "error");
    await busy(form.querySelector("button"), () => api.post(setup ? "/auth/setup" : "/auth/login", { password: pw })).catch(() => {});
    boot();
  };
}

window.addEventListener("netping:auth", () => { if (!$(".login")) boot(); });

// ---------------------------------------------------------------- shell
function renderShell(version) {
  const nav = NAV.map((n) => n.section ? `<div class="nav-section">${esc(n.section)}</div>`
    : `<a href="#/${n.path}" data-path="${n.path}">${ico(n.icon)}<span>${esc(n.label)}</span>${n.badge ? `<span class="badge info">${n.badge}</span>` : ""}</a>`).join("");
  $("#root").innerHTML = `
  <div class="app">
    <aside class="sidebar">
      <div class="brand">${LOGO}<div><b>NetPing</b><small>v${esc(version)} · Red doméstica</small></div></div>
      <nav class="nav">${nav}</nav>
      <div class="sidebar-foot">
        <button class="icon-btn" id="theme-btn" title="Cambiar tema">${ico("moon")}</button>
        <a class="icon-btn" href="/api/docs" target="_blank" rel="noopener" title="Documentación de la API">${ico("help")}</a>
        <span class="grow"></span>
        <button class="icon-btn" id="logout-btn" title="Cerrar sesión">${ico("logout")}</button>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" id="menu-btn" aria-label="Menú">${ico("menu")}</button>
        <div class="title"><h1 id="page-title"></h1><p id="page-sub"></p></div>
        <div class="row" id="topbar-status"></div>
      </header>
      <main class="content" id="page"></main>
    </div>
  </div>`;
  $("#theme-btn").onclick = () => {
    const cur = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = cur;
    try { localStorage.setItem("netping-theme", cur); } catch {}
    window.dispatchEvent(new Event("resize"));
  };
  $("#logout-btn").onclick = async () => { await api.post("/auth/logout"); boot(); };
  $("#menu-btn").onclick = () => $(".app").classList.toggle("nav-open");
  window.onhashchange = route;
  route();
  refreshStatus();
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, 10000);
}

// ---------------------------------------------------------------- estado global (barra superior)
export async function refreshStatus() {
  const box = $("#topbar-status");
  if (!box) return;
  try {
    const s = await api.get("/blocking");
    const paused = s.paused_until && s.paused_until * 1000 > Date.now();
    const state = !s.enabled ? "off" : paused ? "paused" : "on";
    const left = paused ? duration(s.paused_until - Date.now() / 1000) : "";
    box.innerHTML = `
      <span class="badge ${state === "on" ? "ok" : state === "paused" ? "warn" : "err"}">
        <span class="dot dot-${state === "on" ? "ok" : state === "paused" ? "warn" : "err"}"></span>
        ${state === "on" ? "Protección activa" : state === "paused" ? `Pausado · ${left}` : "Protección desactivada"}</span>
      <button class="btn sm" id="pause-btn">${ico(state === "on" ? "pause" : "play")}${state === "on" ? "Pausar" : "Reanudar"}</button>`;
    $("#pause-btn").onclick = () => state === "on" ? pauseDialog() : api.post("/blocking", { enabled: true }).then(refreshStatus);
  } catch {}
}

function pauseDialog() {
  const opts = [[300, "5 minutos"], [1800, "30 minutos"], [3600, "1 hora"], [0, "Indefinidamente"]];
  const m = modal({ title: "Pausar bloqueo de anuncios", body: `
    <p class="muted">Los controles parentales por dispositivo (horarios, apps bloqueadas, Internet pausado) siguen activos.</p>
    <div class="col mt">${opts.map(([s, l]) => `<button class="btn" data-s="${s}">${ico("clock")}${l}</button>`).join("")}</div>` });
  $$("[data-s]", m.body).forEach((b) => b.onclick = async () => {
    const s = Number(b.dataset.s);
    await api.post("/blocking", s ? { pause_seconds: s } : { enabled: false }).catch(toastErr);
    m.close(); refreshStatus(); toast("Bloqueo pausado", "info");
  });
}

// ---------------------------------------------------------------- router
async function route() {
  const path = (location.hash.replace(/^#\//, "") || "dashboard").split("?")[0];
  const item = NAV.find((n) => n.path === path) || NAV[1];
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.path === item.path));
  $(".app")?.classList.remove("nav-open");
  if (typeof cleanup === "function") { try { cleanup(); } catch {} }
  cleanup = null;
  const page = fresh($("#page"));
  page.innerHTML = `<div class="empty"><div class="btn loading ghost" style="width:40px;height:40px"></div></div>`;
  try {
    const mod = await import(`./pages/${item.module}.js`);
    $("#page-title").textContent = mod.title || item.label;
    $("#page-sub").textContent = mod.subtitle || "";
    document.title = `${mod.title || item.label} · NetPing`;
    page.innerHTML = "";
    cleanup = await mod.render(page, { refreshStatus, reload: route });
  } catch (e) {
    console.error(e);
    if (e?.status === 401 || e?.status === 428) return;
    page.innerHTML = `<div class="alert err">${ico("alert")}<div><b>Error al cargar la página</b><p>${esc(e.message || e)}</p></div></div>`;
  }
}

boot();
