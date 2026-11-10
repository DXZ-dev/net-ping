import { $, $$, api, esc, h, ico, toast, toastErr, busy, modal, duration, fresh } from "./lib.js";
import { countryName, getLang, setLang, startTranslator, t } from "./i18n.js";
import { applyPrefs, loadPrefs, savePrefs } from "./theme.js";

const LOGO = `<div class="brand-logo"><svg class="ico" viewBox="0 0 24 24"><path d="M3 13h4l2.5-6 4 11 2.5-5H21"/></svg></div>`;
export const APP_NAME = "Bipon Net";

// roles: a = admin, p = padres, v = sólo lectura
const NAV = [
  { section: "General" },
  { path: "dashboard", label: "Panel", icon: "home", roles: "apv" },
  { path: "assistant", label: "Asistente", icon: "zap", badge: "IA", roles: "a" },
  { path: "queries", label: "Consultas", icon: "list", roles: "apv" },
  { section: "Red y familia" },
  { path: "devices", label: "Dispositivos", icon: "devices", roles: "apv", live: true },
  { path: "parental", label: "Control parental", icon: "users", roles: "apv" },
  { path: "blocking", label: "Bloqueo", icon: "shield", roles: "av" },
  { path: "security", label: "Seguridad", icon: "lock", roles: "av" },
  { section: "DNS" },
  { path: "dns", label: "DNS y latencia", icon: "globe", roles: "av" },
  { path: "unbound", label: "Unbound", icon: "server", roles: "av" },
  { section: "Conexión" },
  { path: "gaming", label: "Gaming y streaming", icon: "console", roles: "av" },
  { path: "speed", label: "Velocidad", icon: "gauge", roles: "av" },
  { path: "network", label: "Red: DHCP y Wi-Fi", icon: "wifi", roles: "av" },
  { path: "router", label: "Router y firewall", icon: "router", roles: "av" },
  { path: "connectivity", label: "NAT y CGNAT", icon: "network", roles: "av" },
  { path: "vpn", label: "VPN", icon: "key", roles: "av" },
  { path: "monitor", label: "Monitor", icon: "activity", roles: "av" },
  { path: "tools", label: "Herramientas", icon: "tool", roles: "av" },
  { path: "infra", label: "Infraestructura", icon: "server", roles: "av" },
  { section: "Sistema" },
  { path: "notifications", label: "Notificaciones", icon: "info", roles: "av" },
  { path: "reports", label: "Informes", icon: "list", roles: "apv" },
  { path: "integrations", label: "Integraciones", icon: "link", roles: "av" },
  { path: "access", label: "Usuarios y acceso", icon: "users", roles: "a" },
  { path: "settings", label: "Ajustes", icon: "settings", roles: "apv" },
];

export const session = { user: null, edition: "home", version: "" };
let cleanup = null;
let statusTimer = null;
let searchReady = false;

applyPrefs(loadPrefs());
setLang(getLang());

// ---------------------------------------------------------------- arranque
async function boot() {
  let st;
  try { st = await api.get("/auth/status"); }
  catch { $("#root").innerHTML = `<div class="login"><div class="card"><p>${t("No se puede contactar con el servidor.")}</p></div></div>`; return; }
  session.version = st.version;
  session.edition = st.edition || "home";
  if (st.setup_required) return wizard({ fresh: true, version: st.version });
  if (!st.authenticated) return renderLogin(st.version);
  session.user = st.user;
  try {
    const prefs = await api.get("/system/prefs");
    if (prefs.ui && Object.keys(prefs.ui).length) {
      const merged = { ...loadPrefs(), ...prefs.ui };
      savePrefs(merged);
      let chosen = false;
      try { chosen = sessionStorage.getItem("bipon-lang-chosen") === "1"; sessionStorage.removeItem("bipon-lang-chosen"); } catch {}
      // Si el idioma se eligió en la pantalla de acceso, gana esa elección y se guarda en la cuenta.
      if (chosen && prefs.ui.lang !== getLang()) api.put("/system/prefs", { ui: { lang: getLang() } }).catch(() => {});
      else if (prefs.ui.lang && prefs.ui.lang !== getLang()) setLang(prefs.ui.lang);
    }
  } catch {}
  if (!st.setup_completed && st.user?.role === "admin") return wizard({ fresh: false, version: st.version });
  renderShell(st.version);
}

function renderLogin(version, needCode = false, creds = {}) {
  $("#root").innerHTML = `
  <div class="login"><form class="card col" autocomplete="on">
    <div class="brand">${LOGO}<div><b>${APP_NAME}</b><small>${t("Tu red, bajo control")}</small></div></div>
    <h2 class="center">${needCode ? t("Verificación en dos pasos") : t("Iniciar sesión")}</h2>
    ${needCode ? `<p class="muted center small">${t("Introduce el código de 6 dígitos de tu app de autenticación.")}</p>
      <label class="field">${t("Código")}<input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required></label>`
    : `<label class="field">${t("Usuario")}<input name="username" value="admin" autocomplete="username" required></label>
      <label class="field">${t("Contraseña")}<input type="password" name="password" required autocomplete="current-password"></label>`}
    <button class="btn primary lg" type="submit">${t("Entrar")}</button>
    <div class="row between small muted"><span>v${esc(version)}</span>${langSwitch()}</div>
  </form></div>`;
  bindLangSwitch(() => renderLogin(version, needCode, creds));
  const form = $("form");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = needCode ? { ...creds, code: form.code.value } : { username: form.username.value.trim(), password: form.password.value };
    try {
      const r = await busy(form.querySelector("button"), () => api.post("/auth/login", body));
      if (r.totp_required) return renderLogin(version, true, body);
      boot();
    } catch {}
  };
  startTranslator();
}

const langSwitch = () => `<span class="btn-group" id="lang-sw"><button type="button" data-l="es" class="${getLang() === "es" ? "active" : ""}">ES</button><button type="button" data-l="en" class="${getLang() === "en" ? "active" : ""}">EN</button></span>`;
function bindLangSwitch(rerender) {
  $$("#lang-sw [data-l]").forEach((b) => b.onclick = () => {
    setLang(b.dataset.l);
    try { sessionStorage.setItem("bipon-lang-chosen", "1"); } catch {}
    rerender();
  });
}

// ---------------------------------------------------------------- asistente de registro
async function wizard({ fresh: isNew, version }) {
  const info = await api.get("/setup/info").catch(() => ({ countries: [], timezones: [], network: {}, lists: [], regional_lists: {} }));
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const guessCountry = info.detected_country && info.countries.some((c) => c.code === info.detected_country) ? info.detected_country
    : (info.countries.find((c) => c.timezones.includes(browserTz))?.code || "OTHER");
  const st = { edition: "home", country: guessCountry, timezone: browserTz, connection: "router", mode: "recommended", family: true,
    lists: [], name: "", email: "", username: "admin" };
  const steps = isNew ? ["welcome", "account", "region", "network", "lists", "mode"] : ["welcome", "region", "network", "lists", "mode"];
  let i = 0;

  const draw = () => {
    const step = steps[i];
    $("#root").innerHTML = `<div class="wizard"><div class="card">
      <div class="row between mb"><div class="brand" style="padding:0">${LOGO}<div><b>${APP_NAME}</b><small>${t("Configuración inicial")} · v${esc(version)}</small></div></div>${langSwitch()}</div>
      <div class="wizard-steps">${steps.map((_, k) => `<i class="${k <= i ? "on" : ""}"></i>`).join("")}</div>
      <div id="wz-body"></div>
      <div class="row between mt"><button class="btn ghost" id="wz-back" ${i === 0 ? "disabled" : ""}>‹ ${t("Atrás")}</button>
        <button class="btn primary" id="wz-next">${i === steps.length - 1 ? t("Finalizar") : t("Continuar")}</button></div></div></div>`;
    bindLangSwitch(draw);
    const body = $("#wz-body");
    VIEWS[step](body);
    $("#wz-back").onclick = () => { if (i > 0) { i--; draw(); } };
    $("#wz-next").onclick = async (e) => {
      try { await busy(e.currentTarget, () => NEXT[step]?.(body)); } catch { return; }
      if (i < steps.length - 1) { i++; draw(); }
    };
    startTranslator();
  };

  const choice = (group, value, icon, title, desc, cur) =>
    `<div class="choice ${cur === value ? "active" : ""}" data-group="${group}" data-value="${value}"><div class="ci">${ico(icon)}</div><div><b>${title}</b><p>${desc}</p></div></div>`;
  const bindChoices = (root) => root.querySelectorAll(".choice").forEach((c) => c.onclick = () => {
    st[c.dataset.group] = c.dataset.value;
    root.querySelectorAll(`.choice[data-group="${c.dataset.group}"]`).forEach((x) => x.classList.toggle("active", x === c));
  });

  const VIEWS = {
    welcome(b) {
      b.innerHTML = `<h2>${isNew ? t("Bienvenido a Bipon Net 👋") : t("Completemos tu configuración")}</h2>
        <p class="muted">${t("En unos pasos dejaremos tu red protegida, rápida y bajo control. ¿Para qué la vas a usar?")}</p>
        <div class="grid g2 mt">
          ${choice("edition", "home", "home", t("Hogar"), t("Familia, gaming y streaming: control parental, tiempo de pantalla, bloqueo de anuncios y modo gaming."), st.edition)}
          ${choice("edition", "business", "server", t("Empresa"), t("Oficinas y negocios: departamentos, horario laboral, red de invitados, auditoría de cambios, exportación CSV y retención ampliada."), st.edition)}
        </div>`;
      bindChoices(b);
    },
    account(b) {
      b.innerHTML = `<h2>${t("Crea tu cuenta de administrador")}</h2><p class="muted small">${t("Protege el panel de tu red. Podrás añadir más usuarios (por ejemplo, para los padres) más adelante.")}</p>
        <div class="form-grid mt">
          <label class="field">${t("Tu nombre")}<input name="name" value="${esc(st.name)}" autocomplete="name"></label>
          <label class="field">${t("Usuario")}<input name="username" value="${esc(st.username)}" autocomplete="username" required></label>
          <label class="field">${t("Contraseña")}<input type="password" name="password" minlength="8" autocomplete="new-password" required><small>${t("Mínimo 8 caracteres")}</small></label>
          <label class="field">${t("Repite la contraseña")}<input type="password" name="password2" minlength="8" autocomplete="new-password" required></label>
          <label class="field">${t("Email (opcional)")}<input type="email" name="email" value="${esc(st.email)}" autocomplete="email"><small>${t("Para recibir avisos e informes")}</small></label>
        </div>`;
    },
    region(b) {
      const c = info.countries.find((x) => x.code === st.country);
      const tzs = c?.timezones?.length ? c.timezones : info.timezones;
      if (!tzs.includes(st.timezone)) st.timezone = tzs.includes(browserTz) ? browserTz : tzs[0];
      b.innerHTML = `<h2>${t("¿Dónde estás?")}</h2><p class="muted small">${t("Ajusta los horarios del control parental, las listas de anuncios de tu idioma y la región de servidores de juego más cercana.")}</p>
        <div class="form-grid mt">
          <label class="field">${t("País")}<select name="country">${info.countries.map((x) => `<option value="${x.code}" ${x.code === st.country ? "selected" : ""}>${esc(countryName(x.code, x.name))}</option>`).join("")}</select>
            ${info.detected_country ? `<small>${t("Detectado automáticamente")}: ${esc(info.detected_country)}</small>` : ""}</label>
          <label class="field">${t("Zona horaria")}<select name="timezone">${tzs.map((z) => `<option ${z === st.timezone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select></label>
          <label class="field">${t("Idioma")}<select name="lang"><option value="es" ${getLang() === "es" ? "selected" : ""}>Español</option><option value="en" ${getLang() === "en" ? "selected" : ""}>English</option></select></label>
        </div>`;
      b.querySelector("[name=country]").onchange = (e) => { st.country = e.target.value; VIEWS.region(b); startTranslator(); };
      b.querySelector("[name=timezone]").onchange = (e) => { st.timezone = e.target.value; };
      b.querySelector("[name=lang]").onchange = (e) => { setLang(e.target.value); draw(); };
    },
    network(b) {
      const n = info.network || {};
      b.innerHTML = `<h2>${t("¿Cómo se conectarán tus dispositivos?")}</h2>
        <p class="muted small">${t("Hemos detectado")}: <b>${esc(n.ip || "?")}</b> (${t("este equipo")}) · ${t("router")} <b>${esc(n.gateway || "?")}</b> · ${esc(n.hostname || "")}</p>
        <div class="col mt">
          ${choice("connection", "router", "router", t("Cambiaré el DNS en mi router (recomendado)"), t("100 % del tráfico filtrado. En el router pon como DNS del DHCP la IP") + ` ${esc(n.ip || "")}.`, st.connection)}
          ${choice("connection", "auto", "zap", t("Automático, sin tocar el router"), t("Bipon se anuncia como DNS por IPv6. Si Bipon se apaga, la red sigue usando el DNS del router automáticamente."), st.connection)}
          ${choice("connection", "dhcp", "wifi", t("Bipon será el servidor DHCP"), t("Para routers del proveedor que no dejan cambiar el DNS. Desactiva el DHCP del router."), st.connection)}
        </div>`;
      bindChoices(b);
    },
    lists(b) {
      const country = info.countries.find((x) => x.code === st.country);
      const regional = info.regional_lists?.[country?.lang];
      const all = [...info.lists, ...(regional ? [{ ...regional, category: "ads", default: true }] : [])];
      const cat = { ads: t("Anuncios y rastreo"), security: t("Seguridad"), adult: t("Adultos y apuestas") };
      b.innerHTML = `<h2>${t("Listas de bloqueo")}</h2><p class="muted small">${t("Todas empiezan desactivadas. Marca las que quieras activar (puedes cambiarlo después en Bloqueo).")}</p>
        <div class="row mt mb"><button class="btn sm" id="rec-on">${ico("check")}${t("Marcar las recomendadas")}</button><button class="btn sm ghost" id="all-off">${t("Desmarcar todas")}</button></div>
        <div class="col">${all.map((l) => `<label class="opt" style="padding:8px 0"><div class="t"><b>${esc(l.name)}</b><span>${cat[l.category] || ""}${l.default ? " · " + t("recomendada") : ""}</span></div>
          <label class="switch"><input type="checkbox" data-url="${esc(l.url)}" ${st.lists.includes(l.url) ? "checked" : ""}><span></span></label></label>`).join("")}</div>
        ${st.edition === "home" ? `<div class="opt mt"><div class="t"><b>${t("Crear perfil «Niños»")}</b><span>${t("Búsqueda segura, sin contenido adulto y horario de dormir; luego asígnalo a sus dispositivos.")}</span></div><label class="switch"><input type="checkbox" id="fam" ${st.family ? "checked" : ""}><span></span></label></div>` : ""}`;
      const sync = () => { st.lists = $$("[data-url]", b).filter((x) => x.checked).map((x) => x.dataset.url); };
      $$("[data-url]", b).forEach((x) => x.onchange = sync);
      b.querySelector("#rec-on").onclick = () => { $$("[data-url]", b).forEach((x) => { x.checked = !!all.find((l) => l.url === x.dataset.url)?.default; }); sync(); };
      b.querySelector("#all-off").onclick = () => { $$("[data-url]", b).forEach((x) => { x.checked = false; }); sync(); };
      const fam = b.querySelector("#fam");
      if (fam) fam.onchange = () => { st.family = fam.checked; };
    },
    mode(b) {
      b.innerHTML = `<h2>${t("¿Cómo quieres configurarlo?")}</h2>
        <div class="col mt">
          ${choice("mode", "auto", "zap", t("Autoconfigurar"), t("El asistente analiza tu red (DNS más rápido desde tu zona, NAT, hardware, dispositivos) y aplica la configuración óptima para ti."), st.mode)}
          ${choice("mode", "recommended", "check", t("Configuración recomendada"), t("Valores equilibrados probados: caché optimista, modo gaming, seguridad, test de velocidad nocturno e informes semanales."), st.mode)}
          ${choice("mode", "manual", "settings", t("Manual"), t("Todo por defecto; lo ajustas tú desde cada sección."), st.mode)}
        </div>`;
      bindChoices(b);
    },
  };

  const NEXT = {
    async account(b) {
      const f = (n) => b.querySelector(`[name=${n}]`).value;
      if (f("password") !== f("password2")) throw toastErr(new Error(t("Las contraseñas no coinciden")));
      st.name = f("name"); st.email = f("email"); st.username = f("username").trim() || "admin";
      await api.post("/auth/setup", { username: st.username, password: f("password") });
    },
    async mode() {
      const r = await api.post("/setup/complete", { ...st });
      try { await api.put("/system/prefs", { ui: { ...loadPrefs(), lang: getLang() } }); } catch {}
      session.edition = st.edition;
      modal({ title: t("¡Listo!"), body: `<div class="col">${r.notes.map((n) => `<div class="alert ok">${ico("check")}<div class="small">${esc(n)}</div></div>`).join("")}</div>`,
        actions: [{ label: t("Entrar al panel"), kind: "primary", onClick: () => { boot(); } }] });
      startTranslator();
      return new Promise(() => {});
    },
  };
  draw();
}

window.addEventListener("bipon:auth", () => { if (!$(".login") && !$(".wizard")) boot(); });

// ---------------------------------------------------------------- shell
function renderShell(version) {
  const role = (session.user?.role || "admin")[0];
  const items = NAV.filter((n) => n.section || n.roles.includes(role));
  const nav = items.filter((n, k) => !(n.section && (!items[k + 1] || items[k + 1].section)))
    .map((n) => n.section ? `<div class="nav-section">${esc(n.section)}</div>`
      : `<a href="#/${n.path}" data-path="${n.path}">${ico(n.icon)}<span>${esc(n.label)}</span>${n.badge ? `<span class="badge info">${n.badge}</span>` : ""}${n.live ? `<span class="live-dot" style="margin-left:auto" title="${t("En tiempo real")}"></span>` : ""}</a>`).join("");
  $("#root").innerHTML = `
  <div class="app">
    <aside class="sidebar">
      <div class="brand">${LOGO}<div><b>${APP_NAME}</b><small>v${esc(version)} · ${session.edition === "business" ? t("Empresa") : t("Hogar")}</small></div></div>
      <nav class="nav">${nav}</nav>
      <div class="sidebar-foot">
        <a class="icon-btn" href="#/settings" title="${t("Apariencia")}">${ico("sun")}</a>
        <button class="icon-btn" id="lang-btn" title="${t("Idioma")}"><b style="font-size:11px">${getLang().toUpperCase()}</b></button>
        <button class="icon-btn" id="tour-btn" title="${t("Guía del sistema")}">${ico("help")}</button>
        <span class="grow"></span>
        <span class="small muted truncate" style="max-width:80px" title="${esc(session.user?.username || "")}">${esc(session.user?.username || "")}</span>
        <button class="icon-btn" id="logout-btn" title="${t("Cerrar sesión")}">${ico("logout")}</button>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" id="menu-btn" aria-label="${t("Menú")}">${ico("menu")}</button>
        <div class="title"><h1 id="page-title"></h1><p id="page-sub"></p></div>
        <button class="gsearch-btn" id="search-btn" title="${t("Buscar")} (Ctrl+K)">${ico("search")}<span>${t("Buscar…")}</span><kbd>Ctrl K</kbd></button>
        <div class="row" id="topbar-status"></div>
      </header>
      <main class="content" id="page"></main>
    </div>
  </div>`;
  $("#lang-btn").onclick = async () => {
    setLang(getLang() === "es" ? "en" : "es");
    try { await api.put("/system/prefs", { ui: { lang: getLang() } }); } catch {}
    location.reload();
  };
  $("#logout-btn").onclick = async () => { await api.post("/auth/logout"); boot(); };
  $("#tour-btn").onclick = () => import("./tour.js").then((m) => m.startTour(true));
  $("#menu-btn").onclick = () => $(".app").classList.toggle("nav-open");
  $("#search-btn").onclick = () => import("./search.js").then((m) => m.openSearch());
  if (!searchReady) { searchReady = true; import("./search.js").then((m) => m.initSearch(NAV, role)); }
  window.onhashchange = route;
  startTranslator();
  route();
  refreshStatus();
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, 10000);
  registerPWA();
  // la guía se muestra una vez tras el registro (o hasta que se omita)
  if (!loadPrefs().tour_done) setTimeout(() => import("./tour.js").then((m) => m.startTour()), 1200);
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
        ${state === "on" ? t("Protección activa") : state === "paused" ? `${t("Pausado")} · ${left}` : t("Protección desactivada")}</span>
      <button class="btn sm" id="pause-btn">${ico(state === "on" ? "pause" : "play")}${state === "on" ? t("Pausar") : t("Reanudar")}</button>`;
    $("#pause-btn").onclick = () => state === "on" ? pauseDialog() : api.post("/blocking", { enabled: true }).then(refreshStatus);
  } catch {}
}

function pauseDialog() {
  const opts = [[300, "5 minutos"], [1800, "30 minutos"], [3600, "1 hora"], [0, "Indefinidamente"]];
  const m = modal({ title: t("Pausar bloqueo de anuncios"), body: `
    <p class="muted">${t("Los controles parentales por dispositivo (horarios, apps bloqueadas, Internet pausado) siguen activos.")}</p>
    <div class="col mt">${opts.map(([s, l]) => `<button class="btn" data-s="${s}">${ico("clock")}${t(l)}</button>`).join("")}</div>` });
  $$("[data-s]", m.body).forEach((b) => b.onclick = async () => {
    const s = Number(b.dataset.s);
    await api.post("/blocking", s ? { pause_seconds: s } : { enabled: false }).catch(toastErr);
    m.close(); refreshStatus(); toast(t("Bloqueo pausado"), "info");
  });
}

// ---------------------------------------------------------------- router
async function route() {
  const path = (location.hash.replace(/^#\//, "") || "dashboard").split(/[?/]/)[0];
  const role = (session.user?.role || "admin")[0];
  const item = NAV.find((n) => n.path === path && n.roles.includes(role)) || NAV.find((n) => n.path && n.roles.includes(role));
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.path === item.path));
  $(".app")?.classList.remove("nav-open");
  if (typeof cleanup === "function") { try { cleanup(); } catch {} }
  cleanup = null;
  const page = fresh($("#page"));
  page.innerHTML = `<div class="grid g4">${'<div class="skeleton" style="height:84px"></div>'.repeat(4)}</div><div class="skeleton mt" style="height:280px"></div>`;
  try {
    const mod = await import(`./pages/${item.path}.js`);
    $("#page-title").textContent = mod.title || item.label;
    $("#page-sub").textContent = mod.subtitle || "";
    document.title = `${t(mod.title || item.label)} · ${APP_NAME}`;
    page.innerHTML = "";
    cleanup = await mod.render(page, { refreshStatus, reload: route, session });
  } catch (e) {
    console.error(e);
    if (e?.status === 401 || e?.status === 428) return;
    page.innerHTML = `<div class="alert err">${ico("alert")}<div><b>${t("Error al cargar la página")}</b><p>${esc(e.message || e)}</p></div></div>`;
  }
}

// ---------------------------------------------------------------- PWA
async function registerPWA() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  try { await navigator.serviceWorker.register("/sw.js", { scope: "/" }); } catch {}
}

boot();
