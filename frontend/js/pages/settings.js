import { api, esc, ico, toast, toastErr, busy, toggle, ago, datetime, on, modal, confirmDialog, download, empty, $, $$ } from "../lib.js";
import { countryName, getLang, setLang, t } from "../i18n.js";
import { SKINS, loadPrefs, savePrefs, DEFAULT_PREFS } from "../theme.js";

export const title = "Ajustes";
export const subtitle = "Apariencia, idioma, actualizaciones, perfiles de configuración y sistema";

export async function render(el, app) {
  const admin = app.session?.user?.role === "admin";
  const TABS = [["look", "Apariencia", "sun"], ["lang", "Idioma y región", "globe"], ...(admin ? [["recovery", "Reparar y restablecer", "zap"], ["updates", "Actualizaciones", "download"], ["configs", "Perfiles de configuración", "list"],
    ["system", "Sistema", "settings"], ["logs", "Registros", "list"], ["audit", "Auditoría", "shield"]] : []), ["account", "Mi cuenta", "key"]];
  let tab = sessionStorage.getItem("np-set-tab") || "look";
  if (!TABS.some((x) => x[0] === tab)) tab = "look";
  el.innerHTML = `<div class="tabs mb">${TABS.map(([k, l, i]) => `<button data-tab="${k}">${ico(i)}${t(l)}</button>`).join("")}</div><div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; try { sessionStorage.setItem("np-set-tab", tab); } catch {} show(); });
  async function show() {
    $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const n = pane.cloneNode(false); pane.replaceWith(n); pane = n;
    try { await V[tab](); } catch (e) { pane.innerHTML = `<div class="alert err">${ico("alert")}<div>${esc(e.message)}</div></div>`; }
  }
  const syncPrefs = (p) => api.put("/system/prefs", { ui: p }).catch(() => {});

  const V = {
    // ------------------------------------------------------------ apariencia
    look() {
      const p = loadPrefs();
      const seg = (k, opts) => `<div class="btn-group">${opts.map(([v, l]) => `<button data-pref="${k}" data-val="${v}" class="${String(p[k]) === v ? "active" : ""}">${t(l)}</button>`).join("")}</div>`;
      pane.innerHTML = `<div class="card mb"><header><h2>${ico("sun")}${t("Tema")}</h2></header><div class="theme-grid">${SKINS.map((s) => `<div class="theme-opt ${p.skin === s.id ? "active" : ""}" data-skin="${s.id}">
          <div class="sw" style="background:${s.sw[0]}"><i style="top:12px;background:${s.sw[1]};width:45%"></i><i style="top:28px;background:${s.sw[2]};opacity:.8"></i><i style="top:44px;background:${s.sw[1]};opacity:.4;width:60%"></i></div>
          <b>${t(s.name)}</b><div class="small muted">${t(s.desc)}</div></div>`).join("")}</div></div>
        <div class="card"><header><h2>${ico("zap")}${t("Efectos visuales")}</h2></header>
          <div class="opt"><div class="t"><b>${t("Efecto cristal (desenfoque)")}</b><span>${t("Más alto se ve mejor pero consume más en equipos lentos.")}</span></div>${seg("blur", [["off", "Sin"], ["normal", "Normal"], ["high", "Intenso"]])}</div>
          <div class="opt"><div class="t"><b>${t("Fondo")}</b><span>${t("Luces de color detrás de la interfaz.")}</span></div>${seg("bg", [["off", "Liso"], ["static", "Estático"], ["animated", "Animado"]])}</div>
          <div class="opt"><div class="t"><b>${t("Animaciones")}</b></div>${seg("anim", [["on", "Sí"], ["off", "No"]])}</div>
          <div class="opt"><div class="t"><b>${t("Esquinas")}</b></div>${seg("radius", [["sm", "Rectas"], ["md", "Normales"], ["lg", "Redondeadas"]])}</div>
          <div class="opt"><div class="t"><b>${t("Densidad")}</b></div>${seg("density", [["normal", "Cómoda"], ["compact", "Compacta"]])}</div>
          <div class="opt"><div class="t"><b>${t("Color de acento")}</b><span>${t("Personaliza botones y resaltados.")}</span></div><input type="color" id="accent" value="${p.accent || "#3b82f6"}" style="width:60px;height:36px;padding:3px">
            <button class="btn sm ghost" id="accent-reset">${t("Del tema")}</button></div>
          <div class="row mt"><button class="btn ghost" id="reset">${t("Restablecer apariencia")}</button><button class="btn" id="tour-start">${ico("help")}${t("Ver la guía del sistema")}</button></div></div>`;
      const apply = (patch) => { const np = { ...loadPrefs(), ...patch }; savePrefs(np); syncPrefs(patch); show(); };
      on(pane, "click", "[data-skin]", (e, d) => apply({ skin: d.dataset.skin }));
      on(pane, "click", "[data-pref]", (e, b) => apply({ [b.dataset.pref]: b.dataset.val }));
      pane.querySelector("#accent").onchange = (e) => apply({ accent: e.target.value });
      pane.querySelector("#accent-reset").onclick = () => apply({ accent: "" });
      pane.querySelector("#reset").onclick = () => apply({ ...DEFAULT_PREFS });
      pane.querySelector("#tour-start").onclick = () => import("../tour.js").then((m) => m.startTour(true));
    },
    // ------------------------------------------------------------ idioma y región
    async lang() {
      const [prefs, info] = await Promise.all([api.get("/system/prefs"), api.get("/setup/info").catch(() => ({ countries: [], timezones: [] }))]);
      const r = prefs.region || {};
      pane.innerHTML = `<div class="card"><header><h2>${ico("globe")}${t("Idioma y región")}</h2></header><div class="form-grid">
        <label class="field">${t("Idioma de la interfaz")}<select id="lg"><option value="es" ${getLang() === "es" ? "selected" : ""}>Español</option><option value="en" ${getLang() === "en" ? "selected" : ""}>English</option></select></label>
        ${admin ? `<label class="field">${t("País")}<select id="ct">${info.countries.map((c) => `<option value="${c.code}" ${c.code === r.country ? "selected" : ""}>${esc(countryName(c.code, c.name))}</option>`).join("")}</select></label>
        <label class="field">${t("Zona horaria")}<select id="tz">${info.timezones.map((z) => `<option ${z === r.timezone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select></label>` : ""}</div>
        <button class="btn primary mt" id="save">${ico("check")}${t("Guardar")}</button></div>`;
      pane.querySelector("#save").onclick = (e) => busy(e.currentTarget, async () => {
        const lg = pane.querySelector("#lg").value;
        await api.put("/system/prefs", { ui: { lang: lg }, ...(admin ? { region: { country: pane.querySelector("#ct").value, timezone: pane.querySelector("#tz").value } } : {}) });
        if (lg !== getLang()) { setLang(lg); location.reload(); } else toast(t("Guardado"));
      }).catch(() => {});
    },
    // ------------------------------------------------------------ reparar y restablecer
    async recovery() {
      const u = await api.get("/updates");
      const hist = (u.history || []).filter((h) => h.commit !== u.commit);
      const docker = u.mode === "docker";
      pane.innerHTML = `<div class="grid g2">
        <div class="card"><header><h2>${ico("zap")}FIX — ${t("Reparar el sistema")}</h2></header>
          <p class="muted small">${t("Revisa y arregla solo lo que suele romperse: base de datos, servidor DNS, Unbound, servidores de salida, listas de bloqueo, caché, firewall, colas de velocidad olvidadas, disco y procesos internos. También mide la latencia. No borra tu configuración.")}</p>
          <button class="btn primary mt" id="fix" style="font-size:1.05rem;padding:.8rem 1.6rem">${ico("zap")}FIX</button>
          <div id="fix-out" class="mt"></div></div>
        <div class="card"><header><h2>${ico("refresh")}${t("Volver a la versión anterior")}</h2></header>
          <p class="muted small">${t("Si una actualización te dio problemas, vuelve a la versión que tenías antes. Tu configuración se conserva y antes se hace una copia de seguridad.")}</p>
          <dl class="kv mt"><dt>${t("Versión instalada")}</dt><dd>${esc(u.version)} <span class="mono small muted">${esc((u.commit || "").slice(0, 7))}</span></dd></dl>
          ${hist.length ? `<label class="field mt">${t("Versión a la que volver")}<select id="rb-to">${hist.map((h) => `<option value="${esc(h.commit)}">${esc(h.version || "")} · ${esc(h.commit.slice(0, 7))} · ${h.ts ? datetime(h.ts) : ""}</option>`).join("")}</select></label>`
            : `<p class="small muted mt">${t("Aún no hay versiones guardadas en este equipo: se volverá al cambio anterior del repositorio.")}</p>`}
          ${docker ? `<div class="alert info mt small">${ico("info")}<div>${t("En Docker se vuelve atrás desde el equipo anfitrión; el botón te dará el comando.")}</div></div>` : ""}
          <button class="btn mt" id="rb">${ico("refresh")}${t("Volver a la versión anterior")}</button></div>
        <div class="card"><header><h2>${ico("power")}${t("Reiniciar Bipon")}</h2></header>
          <p class="muted small">${t("Reinicia el servicio completo (unos segundos sin panel; el DNS vuelve solo).")}</p>
          <button class="btn mt" id="restart">${ico("power")}${t("Reiniciar ahora")}</button></div>
        <div class="card" style="border-color:var(--danger)"><header><h2>${ico("trash")}${t("Restablecer de fábrica")}</h2></header>
          <p class="muted small">${t("Deja Bipon como recién instalado: se borran usuarios, configuración, dispositivos, estadísticas, certificados y reglas, y aparece el asistente inicial. Antes se guarda una copia y los datos se apartan en la carpeta de copias por si te arrepientes.")}</p>
          <button class="btn danger mt" id="factory">${ico("trash")}${t("Restablecer de fábrica")}</button></div></div>`;
      const waitBack = (ms = 6000) => setTimeout(function w() { fetch("/api/auth/status").then((x) => x.ok ? location.replace("/") : setTimeout(w, 2500)).catch(() => setTimeout(w, 2500)); }, ms);
      const ST = { ok: ["check", "ok", "Bien"], fixed: ["zap", "info", "Reparado"], warn: ["alert", "warn", "Aviso"], skip: ["info", "", "No aplica"] };
      pane.querySelector("#fix").onclick = (e) => busy(e.currentTarget, () => api.post("/system/repair")).then((r) => {
        pane.querySelector("#fix-out").innerHTML = `<div class="alert ${r.warnings ? "warn" : "ok"}">${ico(r.warnings ? "alert" : "check")}<div><b>${esc(t(r.summary))}</b> <span class="small muted">(${r.seconds} s)</span></div></div>
          <div class="col mt">${r.steps.map((s) => { const [ic, cls, lb] = ST[s.status] || ST.skip; return `<div class="rec row" style="flex-wrap:nowrap"><span class="badge ${cls}">${ico(ic)}${t(lb)}</span><div class="grow"><b>${esc(t(s.name))}</b><div class="small muted">${esc(s.detail)}</div></div></div>`; }).join("")}</div>`;
        toast(t(r.summary), r.warnings ? "warn" : "ok");
      }).catch(() => {});
      const askPassword = (title, text, extra = "") => new Promise((resolve) => {
        let val = null;
        modal({ title, body: `<p>${esc(text)}</p>${extra}<label class="field mt">${t("Contraseña de administrador")}<input type="password" id="pw" autocomplete="current-password"></label>`,
          onClose: () => resolve(val),
          actions: [{ label: t("Cancelar") }, { label: t("Confirmar"), kind: "danger", onClick: (m) => {
            const word = $("#word", m);
            if (word && !/^(RESTABLECER|RESET)$/i.test(word.value.trim())) { toast(t("Escribe RESTABLECER para confirmar"), "warn"); return false; }
            val = { password: $("#pw", m).value, confirm: word ? word.value.trim() : "" };
          } }] });
      });
      pane.querySelector("#rb").onclick = async (e) => {
        const cred = await askPassword(t("Volver a la versión anterior"), t("Bipon se reiniciará con la versión elegida (1-2 minutos sin panel). Tu configuración se conserva."));
        if (!cred) return;
        const commit = pane.querySelector("#rb-to")?.value || "";
        busy(e.currentTarget, () => api.post("/system/rollback", { ...cred, commit })).then((r) => {
          toast(t(r.message), r.ok ? "info" : "warn", 15000);
          if (r.ok) waitBack(25000);
        }).catch(() => {});
      };
      pane.querySelector("#restart").onclick = async (e) => {
        if (!(await confirmDialog(t("Reiniciar Bipon"), t("El panel y el DNS se reinician (unos segundos)."), { danger: false, label: t("Reiniciar") }))) return;
        busy(e.currentTarget, () => api.post("/system/restart")).then(() => { toast(t("Reiniciando…"), "info", 8000); waitBack(); }).catch(() => {});
      };
      pane.querySelector("#factory").onclick = async (e) => {
        const cred = await askPassword(t("Restablecer de fábrica"), t("Esto borra toda la configuración, los usuarios y los datos. Para confirmar escribe RESTABLECER y tu contraseña."),
          `<label class="field mt">${t("Escribe RESTABLECER")}<input id="word" autocomplete="off" data-notr></label>`);
        if (!cred) return;
        busy(e.currentTarget, () => api.post("/system/factory-reset", cred)).then((r) => { toast(t(r.message), "info", 15000); waitBack(8000); }).catch(() => {});
      };
    },
    // ------------------------------------------------------------ actualizaciones
    async updates() {
      const s = await api.get("/updates");
      const lc = s.last_check || {};
      const c = s.config;
      pane.innerHTML = `<div class="grid g2"><div class="card"><header><h2>${ico("download")}${t("Actualizaciones")}</h2></header>
          <dl class="kv"><dt>${t("Versión instalada")}</dt><dd>${esc(s.version)} <span class="mono small muted">${esc((s.commit || "").slice(0, 7))}</span></dd>
            <dt>${t("Tipo de instalación")}</dt><dd>${esc({ systemd: "Linux / Proxmox (servicio)", git: "Linux (copia git)", docker: "Docker", unknown: "Desconocido" }[s.mode] || s.mode)}</dd>
            <dt>${t("Última comprobación")}</dt><dd>${lc.ts ? ago(lc.ts) : t("nunca")}</dd></dl>
          <div id="upd-state" class="mt">${lc.ts ? (lc.available ? `<div class="alert info">${ico("download")}<div><b>${t("Nueva versión disponible")}</b><p class="small">${esc(lc.remote_message || "")} · ${esc((lc.remote_commit || "").slice(0, 7))} · ${lc.remote_date ? new Date(lc.remote_date).toLocaleString() : ""}</p></div></div>`
            : `<div class="alert ok">${ico("check")}<div>${t("Tienes la última versión")}</div></div>`) : ""}</div>
          <div class="row mt"><button class="btn" id="check">${ico("refresh")}${t("Buscar actualizaciones")}</button><button class="btn primary" id="apply" ${lc.available ? "" : "disabled"}>${ico("download")}${t("Actualizar ahora")}</button></div>
          <div class="opt mt"><div class="t"><b>${t("¿Deseas recibir actualizaciones automáticas?")}</b><span>${t("Se comprueba cada hora y se instala de madrugada (Linux/Proxmox). En Docker sólo se avisa.")}</span></div>${toggle("auto", c.auto, 'id="auto"')}</div>
          <label class="field mt">${t("Hora de instalación automática")}<input type="time" id="at" value="${esc(c.time)}" style="max-width:140px"></label></div>
        <div class="card"><header><h3>${ico("settings")}${t("Origen")}</h3></header>
          <div class="form-grid"><label class="field">${t("Repositorio de GitHub")}<input id="repo" value="${esc(c.repo)}"></label><label class="field">${t("Rama")}<input id="br" value="${esc(c.branch)}" placeholder="${t("la principal")}"></label></div>
          <label class="field mt">${t("Token de GitHub (sólo si el repositorio es privado)")}<input type="password" id="tok" value="${esc(c.token)}" autocomplete="new-password"></label>
          <button class="btn mt" id="save">${ico("check")}${t("Guardar")}</button>
          ${s.mode === "docker" ? `<div class="alert info mt small">${ico("info")}<div>${t("En Docker actualiza desde el equipo anfitrión:")}<pre data-notr>git pull && docker compose up -d --build</pre></div></div>` : ""}</div></div>`;
      const save = () => api.put("/updates", { auto: pane.querySelector("#auto").checked, time: pane.querySelector("#at").value, repo: pane.querySelector("#repo").value.trim(), branch: pane.querySelector("#br").value.trim(), token: pane.querySelector("#tok").value });
      pane.querySelector("#auto").onchange = () => save().then(() => toast(t("Guardado"))).catch(toastErr);
      pane.querySelector("#save").onclick = (e) => busy(e.currentTarget, save).then(() => toast(t("Guardado"))).catch(() => {});
      pane.querySelector("#check").onclick = (e) => busy(e.currentTarget, () => api.post("/updates/check")).then(show).catch(() => {});
      pane.querySelector("#apply").onclick = async (e) => {
        if (!(await confirmDialog(t("Actualizar Bipon Net"), t("Se descargará la nueva versión y el servicio se reiniciará (1-2 minutos sin panel; el DNS se recupera solo)."), { danger: false, label: t("Actualizar") }))) return;
        busy(e.currentTarget, () => api.post("/updates/apply")).then((r) => {
          toast(r.message, r.ok ? "info" : "warn", 12000);
          if (r.ok) setTimeout(function wait() { fetch("/api/auth/status").then((x) => x.ok ? location.reload() : setTimeout(wait, 3000)).catch(() => setTimeout(wait, 3000)); }, 20000);
        }).catch(() => {});
      };
    },
    // ------------------------------------------------------------ perfiles de configuración
    async configs() {
      const s = await api.get("/configs");
      pane.innerHTML = `<div class="card"><header><h2>${ico("list")}${t("Perfiles de configuración")}</h2><button class="btn primary sm" id="new">${ico("plus")}${t("Guardar configuración actual")}</button><label class="btn sm">${ico("upload")}${t("Importar")}<input type="file" accept=".json" id="imp" class="hidden"></label></header>
        <p class="muted small mb">${t("Guarda la configuración completa (DNS, listas, reglas, perfiles, dispositivos, router…) con un nombre para cambiar de escenario o probar sin miedo. Antes de aplicar otro perfil se guarda una copia automática.")}</p>
        ${s.active ? `<div class="alert info small mb">${ico("check")}<div>${t("Perfil activo")}: <b>${esc(s.active.name)}</b> · ${ago(s.active.ts)}</div></div>` : ""}
        ${s.profiles.length ? `<div class="col">${s.profiles.map((p) => `<div class="rec row" style="flex-wrap:nowrap"><div class="grow"><b>${esc(p.name)}</b> ${p.auto ? `<span class="badge">${t("copia automática")}</span>` : ""}
          <div class="small muted">${esc(p.description || "")} · ${datetime(p.created)}</div></div>
          <button class="btn sm" data-cmp="${p.id}">${ico("search")}${t("Comparar")}</button><button class="btn sm primary" data-apply="${p.id}" data-name="${esc(p.name)}">${ico("play")}${t("Aplicar")}</button>
          <a class="icon-btn" href="/api/configs/${p.id}/export" title="${t("Exportar")}">${ico("download")}</a><button class="icon-btn" data-del="${p.id}">${ico("trash")}</button></div>`).join("")}</div>`
        : empty(t("Aún no has guardado ningún perfil."), "list")}</div>`;
      pane.querySelector("#new").onclick = () => modal({ title: t("Guardar configuración"), body: `<label class="field">${t("Nombre")}<input id="nm" placeholder="${t("Ej. Vacaciones, Exámenes, Pruebas")}"></label><label class="field mt">${t("Descripción")}<input id="ds"></label>`,
        actions: [{ label: t("Cancelar") }, { label: t("Guardar"), kind: "primary", onClick: async (m) => { await api.post("/configs", { name: $("#nm", m).value, description: $("#ds", m).value }); toast(t("Perfil guardado")); show(); } }] });
      on(pane, "click", "[data-cmp]", async (e, b) => {
        const d = await api.get(`/configs/${b.dataset.cmp}/compare`);
        modal({ title: t("Diferencias con la configuración actual"), body: d.length ? `<div class="col">${d.map((x) => `<div class="row between"><b>${esc(x.section)}</b><span class="badge">${esc(t(x.change))}</span></div>`).join("")}</div>` : `<p>${t("Es idéntico a la configuración actual.")}</p>` });
      });
      on(pane, "click", "[data-apply]", async (e, b) => {
        if (!(await confirmDialog(t("Aplicar perfil"), `${t("Se aplicará")} «${b.dataset.name}». ${t("Antes se guarda una copia automática de la configuración actual.")}`, { danger: false, label: t("Aplicar") }))) return;
        busy(b, () => api.post(`/configs/${b.dataset.apply}/apply`)).then(() => { toast(t("Perfil aplicado")); show(); }).catch(() => {});
      });
      on(pane, "click", "[data-del]", async (e, b) => { if (await confirmDialog(t("Eliminar perfil"), t("¿Seguro?"))) { await api.del(`/configs/${b.dataset.del}`); show(); } });
      pane.querySelector("#imp").onchange = async (e) => { const f = e.target.files[0]; if (!f) return; try { await api.post("/configs/import", JSON.parse(await f.text())); toast(t("Perfil importado")); show(); } catch (err) { toastErr(err); } };
    },
    // ------------------------------------------------------------ sistema
    async system() {
      const [s, sys, prefs] = await Promise.all([api.get("/settings"), api.get("/system"), api.get("/system/prefs")]);
      pane.innerHTML = `<div class="grid g2">
        <div class="card"><header><h2>${ico("lock")}${t("Privacidad")}</h2></header>
          <div class="opt"><div class="t"><b>${t("Anonimizar clientes en el registro")}</b><span>${t("Guarda un identificador en lugar de la IP de cada equipo (protección de datos en empresas). Desactiva las estadísticas por dispositivo.")}</span></div>${toggle("anon", prefs.privacy?.anonymize_clients, 'id="anon"')}</div>
          <button class="btn mt" id="ed-save">${ico("check")}${t("Guardar")}</button></div>
        <form class="card" id="gen"><header><h2>${ico("settings")}${t("General")}</h2></header><div class="form-grid">
          <label class="field">${t("Análisis completo cada (s)")}<input type="number" name="scan_interval" min="120" value="${s.devices.scan_interval}"></label>
          <label class="field">${t("Retención de datos (días)")}<input type="number" name="retention_days" min="1" max="365" value="${s.retention_days}"></label>
          <label class="field">${t("Monitor cada (s)")}<input type="number" name="interval" min="15" value="${s.monitor.interval}"></label>
          <label class="field">${t("Destinos del monitor")}<input name="targets" value="${esc(s.monitor.targets.join(", "))}"></label>
          <label class="field">${t("Subredes a escanear")}<input name="subnets" value="${esc((s.devices.subnets || []).join(", "))}" placeholder="${t("automático")}"></label></div>
          <div class="opt"><div class="t"><b>${t("Resolver nombres de dispositivos")}</b></div>${toggle("resolve_names", s.devices.resolve_names)}</div>
          <div class="opt"><div class="t"><b>${t("Monitor de conexión")}</b></div>${toggle("monitor_enabled", s.monitor.enabled)}</div>
          <button class="btn primary mt">${ico("check")}${t("Guardar")}</button></form></div>
        <div class="grid g2 mt"><div class="card"><header><h2>${ico("download")}${t("Copia de seguridad")}</h2></header>
          <p class="muted small">${t("Exporta listas, reglas, perfiles, nombres de dispositivos y configuración (sin contraseñas ni claves VPN).")}</p>
          <div class="row mt"><a class="btn primary" href="/api/backup">${ico("download")}${t("Descargar copia")}</a><label class="btn">${ico("upload")}${t("Restaurar…")}<input type="file" accept=".json" id="restore" class="hidden"></label>
            <a class="btn" href="/api/export/queries.csv">${ico("download")}${t("Exportar consultas (CSV)")}</a></div></div>
          <div class="card" id="autobk"></div>
          <div class="card"><header><h2>${ico("trash")}${t("Borrar datos")}</h2></header>
            <p class="muted small mb">${t("Acciones irreversibles. La configuración, las listas y los perfiles no se tocan.")}</p>
            <div class="col">
              <button class="btn" id="del-stats">${ico("trash")}${t("Borrar estadísticas")}</button>
              <button class="btn" id="del-logs">${ico("trash")}${t("Borrar registros")}</button>
              <button class="btn danger" id="del-devs">${ico("trash")}${t("Borrar dispositivos conocidos")}</button></div>
            <p class="small muted mt">${t("«Borrar dispositivos conocidos» es ideal tras cambiar la clave del Wi-Fi/router: se vacía la lista y solo reaparecen los equipos que se reconecten con la clave nueva.")}</p></div>
          <div class="card"><header><h2>${ico("info")}${t("Acerca de")}</h2></header><dl class="kv"><dt>${t("Versión")}</dt><dd>Bipon Net ${esc(sys.version)}</dd><dt>${t("Sistema")}</dt><dd>${esc(sys.platform)}</dd>
            <dt>Python</dt><dd>${esc(sys.python)}</dd><dt>${t("IP del servidor")}</dt><dd>${esc(sys.ip || "")}</dd><dt>${t("Puerto DNS")}</dt><dd>${esc(sys.dns.port)}</dd><dt>API</dt><dd><a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></dd></dl></div></div>`;
      drawAutoBackup(pane.querySelector("#autobk"));
      const reset = (what, title, msg) => async () => {
        if (!(await confirmDialog(title, msg))) return;
        const r = await api.post("/system/reset", { what }).catch(toastErr);
        if (r) toast(`${t("Borrado")}: ${r.deleted.map((x) => t(x)).join(", ")}`);
      };
      pane.querySelector("#del-stats").onclick = reset(["stats"], t("Borrar estadísticas"), t("Se borrarán consultas, velocidad, tráfico y monitor. ¿Continuar?"));
      pane.querySelector("#del-logs").onclick = reset(["logs"], t("Borrar registros"), t("Se borrarán los eventos y el registro de la aplicación. ¿Continuar?"));
      pane.querySelector("#del-devs").onclick = reset(["devices"], t("Borrar dispositivos conocidos"), t("Se borrará la lista de dispositivos y las aceptaciones del portal. Reaparecerán al reconectarse. ¿Continuar?"));
      pane.querySelector("#ed-save").onclick = (e) => busy(e.currentTarget, () => api.put("/system/prefs", { privacy: { anonymize_clients: pane.querySelector("#anon").checked } })).then(() => toast(t("Guardado"))).catch(() => {});
      const f = pane.querySelector("#gen");
      f.onsubmit = (e) => { e.preventDefault(); busy(f.querySelector("button.primary"), () => api.put("/settings", {
        devices: { scan_interval: Number(f.scan_interval.value), resolve_names: f.resolve_names.checked, subnets: f.subnets.value.split(/[\s,]+/).filter(Boolean) },
        monitor: { interval: Number(f.interval.value), enabled: f.monitor_enabled.checked, targets: f.targets.value.split(/[\s,]+/).filter(Boolean) }, retention_days: Number(f.retention_days.value),
      })).then(() => toast(t("Ajustes guardados"))).catch(() => {}); };
      pane.querySelector("#restore").onchange = async (e) => { const file = e.target.files[0]; if (!file) return; try { await api.post("/restore", JSON.parse(await file.text())); toast(t("Copia restaurada")); } catch (err) { toastErr(err); } };
    },
    // ------------------------------------------------------------ auditoría
    async logs() {
      const st = { source: "events", level: "", q: "", category: "" };
      pane.innerHTML = `<div class="card"><header><h2>${ico("list")}${t("Registros del sistema")} <span class="live-dot"></span></h2>
          <button class="btn sm" id="lg-dl">${ico("download")}${t("Descargar")}</button></header>
        <p class="muted small mb">${t("Para encontrar problemas: eventos de Bipon (caídas, picos de lag, bloqueos, cambios), el registro interno de la aplicación y el de Unbound. Se actualiza solo cada 5 s.")}</p>
        <div class="row mb" style="flex-wrap:wrap;gap:8px">
          <div class="btn-group" id="lg-src"><button data-s="events" class="active">${t("Eventos")}</button><button data-s="app">${t("Aplicación")}</button><button data-s="unbound">Unbound</button></div>
          <select id="lg-lvl" style="width:auto"><option value="">${t("Todos los niveles")}</option><option value="info">${t("Info y superior")}</option><option value="warning">${t("Avisos y errores")}</option><option value="error">${t("Sólo errores")}</option></select>
          <select id="lg-cat" style="width:auto"><option value="">${t("Todas las categorías")}</option></select>
          <input id="lg-q" placeholder="${t("Buscar en los registros…")}" style="max-width:260px"></div>
        <div id="lg-sum" class="row small mb" style="gap:10px"></div>
        <div class="table-wrap" style="max-height:60vh;overflow:auto"><table class="logs"><tbody id="lg-rows"></tbody></table></div></div>`;
      const rowsEl = pane.querySelector("#lg-rows"), cat = pane.querySelector("#lg-cat");
      let last = [];
      const badge = (l) => `<span class="badge ${l === "error" || l === "critical" ? "err" : l === "warning" ? "warn" : "info"}">${esc(t(l === "warning" ? "aviso" : l === "error" || l === "critical" ? "error" : "info"))}</span>`;
      const load = async () => {
        if (!document.body.contains(rowsEl)) return clearInterval(timer);
        const r = await api.get(`/system/logs?source=${st.source}&level=${st.level}&q=${encodeURIComponent(st.q)}&category=${encodeURIComponent(st.category)}&limit=400`).catch(() => null);
        if (!r) return;
        last = r.rows;
        cat.parentElement && (cat.style.display = st.source === "events" ? "" : "none");
        if (r.categories && cat.options.length <= 1) r.categories.forEach((c) => cat.insertAdjacentHTML("beforeend", `<option value="${esc(c)}">${esc(t(c))}</option>`));
        pane.querySelector("#lg-sum").innerHTML = r.counts_24h ? `<span class="muted">${t("Últimas 24 h")}:</span> ${["error", "warning", "info"].map((l) => `${badge(l)} ${r.counts_24h[l] || 0}`).join(" ")}` : "";
        rowsEl.innerHTML = r.rows.length ? r.rows.map((x) => `<tr><td class="nowrap small muted">${x.ts ? datetime(x.ts) : ""}</td><td>${badge(x.level)}</td><td class="small muted nowrap">${esc(x.source || "")}</td><td class="small mono" style="white-space:pre-wrap;word-break:break-word">${esc(x.message)}</td></tr>`).join("")
          : `<tr><td class="muted">${t("Sin registros")}</td></tr>`;
      };
      pane.querySelector("#lg-src").onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; st.source = b.dataset.s; pane.querySelectorAll("#lg-src button").forEach((x) => x.classList.toggle("active", x === b)); load(); };
      pane.querySelector("#lg-lvl").onchange = (e) => { st.level = e.target.value; load(); };
      cat.onchange = (e) => { st.category = e.target.value; load(); };
      let deb; pane.querySelector("#lg-q").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { st.q = e.target.value.trim(); load(); }, 300); };
      pane.querySelector("#lg-dl").onclick = () => {
        const txt = last.map((x) => `${x.ts ? new Date(x.ts * 1000).toISOString() : ""}\t${x.level}\t${x.source || ""}\t${x.message}`).join("\n");
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" })); a.download = `bipon-${st.source}-${Date.now()}.log`; a.click();
      };
      await load();
      const timer = setInterval(load, 5000);
    },
    async audit() {
      const [rows, events] = await Promise.all([api.get("/audit?limit=300"), api.get("/events?limit=150")]);
      pane.innerHTML = `<div class="grid g2"><div class="card"><header><h2>${ico("shield")}${t("Quién cambió qué")}</h2></header>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Fecha")}</th><th>${t("Usuario")}</th><th>${t("Acción")}</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td class="small nowrap muted">${datetime(r.ts)}</td><td>${esc(r.username)}<div class="small muted mono">${esc(r.ip)}</div></td><td class="mono small">${esc(r.method)} ${esc(r.path.replace("/api/", ""))}</td>
          <td>${r.status < 400 ? `<span class="badge ok">${r.status}</span>` : `<span class="badge err">${r.status}</span>`}</td></tr>`).join("")}</tbody></table></div>` : empty(t("Sin cambios registrados"))}</div>
        <div class="card"><header><h2>${ico("list")}${t("Eventos del sistema")}</h2></header><div class="table-wrap"><table><tbody>${events.map((e) => `<tr><td class="small nowrap muted">${datetime(e.ts)}</td><td><span class="badge ${{ info: "info", warning: "warn", error: "err" }[e.level]}">${esc(e.category)}</span></td><td class="small">${esc(e.message)}</td></tr>`).join("")}</tbody></table></div></div></div>`;
    },
    // ------------------------------------------------------------ mi cuenta
    account() {
      pane.innerHTML = `<form class="card" id="pw" style="max-width:520px"><header><h2>${ico("key")}${t("Cambiar mi contraseña")}</h2></header><div class="col">
        <label class="field">${t("Contraseña actual")}<input type="password" name="current" required autocomplete="current-password"></label>
        <label class="field">${t("Nueva contraseña")}<input type="password" name="new" minlength="8" required autocomplete="new-password"></label>
        <label class="field">${t("Repite la nueva")}<input type="password" name="new2" minlength="8" required autocomplete="new-password"></label>
        <div><button class="btn primary">${ico("check")}${t("Cambiar")}</button></div></div><p class="muted small mt">${t("Cambiarla cierra tus demás sesiones. La verificación en dos pasos se configura en Usuarios y acceso.")}</p></form>`;
      const f = pane.querySelector("#pw");
      f.onsubmit = (e) => { e.preventDefault(); if (f.new.value !== f.new2.value) return toast(t("Las contraseñas no coinciden"), "error"); busy(f.querySelector("button"), () => api.post("/auth/password", { current: f.current.value, new: f.new.value })).then(() => { toast(t("Contraseña cambiada")); f.reset(); }).catch(() => {}); };
    },
  };
  show();
}

// Copias de seguridad automáticas
async function drawAutoBackup(box) {
  if (!box) return;
  const r = await api.get("/backups").catch(() => null);
  if (!r) { box.remove(); return; }
  const k = r.config;
  const kb = (n) => (n / 1024).toFixed(1) + " KB";
  box.innerHTML = `<header><h2>${ico("clock")}${t("Copias automáticas")}</h2>${toggle("bk-on", k.enabled)}</header>
    <p class="muted small mb">${t("Bipon guarda solo una copia de tu configuración, reglas, listas y dispositivos. Si algo se rompe, vuelves atrás con un clic.")}</p>
    <div class="form-grid"><label class="field">${t("Frecuencia")}<select id="bk-every"><option value="daily" ${k.every === "daily" ? "selected" : ""}>${t("Cada día")}</option><option value="weekly" ${k.every === "weekly" ? "selected" : ""}>${t("Cada semana")}</option></select></label>
      <label class="field">${t("Conservar las últimas")}<input type="number" id="bk-keep" min="1" max="365" value="${k.keep}"></label></div>
    <label class="field mt">${t("Copiar también a (opcional: disco o NAS montado)")}<input id="bk-copy" value="${esc(k.copy_to || "")}" placeholder="/mnt/nas/bipon"></label>
    <div class="row mt"><button class="btn sm" id="bk-save">${ico("check")}${t("Guardar")}</button><button class="btn sm primary" id="bk-now">${ico("download")}${t("Hacer copia ahora")}</button></div>
    ${r.backups.length ? `<div class="table-wrap mt" style="max-height:260px;overflow:auto"><table><tbody>${r.backups.map((b) => `<tr><td class="small">${new Date(b.ts * 1000).toLocaleString()} ${b.auto ? `<span class="badge">${t("auto")}</span>` : ""}</td><td class="small muted">${kb(b.size)}</td>
      <td class="actions nowrap"><a class="icon-btn" href="/api/backups/${encodeURIComponent(b.name)}" title="${t("Descargar")}">${ico("download")}</a>
        <button class="btn sm" data-bkr="${esc(b.name)}">${t("Restaurar")}</button><button class="icon-btn" data-bkd="${esc(b.name)}" title="${t("Eliminar")}">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="small muted mt">${t("Aún no hay copias.")}</p>`}`;
  const save = () => busy(box.querySelector("#bk-save"), () => api.put("/backups/config", { enabled: box.querySelector("[name=bk-on]").checked, every: box.querySelector("#bk-every").value,
    keep: Number(box.querySelector("#bk-keep").value) || 14, copy_to: box.querySelector("#bk-copy").value.trim() })).then(() => toast(t("Guardado"))).catch(() => {});
  box.querySelector("#bk-save").onclick = save;
  box.querySelector("[name=bk-on]").onchange = save;
  box.querySelector("#bk-now").onclick = (e) => busy(e.currentTarget, () => api.post("/backups")).then((x) => { toast(`${t("Copia creada")}${x.copied_to ? " · " + x.copied_to : ""}`); drawAutoBackup(box); }).catch(() => {});
  box.querySelectorAll("[data-bkr]").forEach((b) => b.onclick = async () => {
    if (!(await confirmDialog(t("Restaurar copia"), t("Se guardará antes una copia del estado actual. ¿Restaurar esta copia?")))) return;
    busy(b, () => api.post(`/backups/${encodeURIComponent(b.dataset.bkr)}/restore`)).then(() => toast(t("Copia restaurada"))).catch(() => {});
  });
  box.querySelectorAll("[data-bkd]").forEach((b) => b.onclick = () => api.del(`/backups/${encodeURIComponent(b.dataset.bkd)}`).then(() => drawAutoBackup(box)).catch(toastErr));
}
