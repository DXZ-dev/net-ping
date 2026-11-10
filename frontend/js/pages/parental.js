import { api, esc, ico, toast, toastErr, busy, modal, on, toggle, confirmDialog, $, $$ } from "../lib.js";
import { t } from "../i18n.js";
import { appIcon } from "../brands.js";

export const title = "Control parental";
export const subtitle = "Perfiles por dispositivo: horarios, apps, búsqueda segura y contenido";

const DAYS = ["L", "M", "X", "J", "V", "S", "D"];
const TEMPLATES = [
  ["kids", "Niños", "Búsqueda segura, YouTube restringido, sin adultos/apuestas/TikTok, horario de dormir", "#f59e0b"],
  ["teen", "Adolescentes", "Sin adultos ni apuestas; redes y juegos bloqueados en horario de deberes", "#8b5cf6"],
  ["gamer", "Gamer", "Sin filtros que interfieran con juegos; sólo protección contra amenazas", "#22c55e"],
  ["guest", "Invitados", "Navegación limpia, sin VPN para saltarse filtros", "#06b6d4"],
  ["iot", "IoT / Domótica", "Protección contra malware para cámaras y dispositivos inteligentes", "#64748b"],
  ["", "Vacío", "Empieza desde cero", "#3b82f6"],
];

export async function render(el) {
  let services = [];
  el.innerHTML = `<div class="row mb"><p class="muted grow">Asigna un perfil a cada dispositivo desde <a href="#/devices">Dispositivos</a>. Las reglas del perfil se aplican aunque pauses el bloqueo de anuncios.</p>
    <button class="btn danger" id="fam-pause">${ico("pause")}Pausa familiar</button><button class="btn success" id="fam-resume">${ico("play")}Reanudar a todos</button>
    <button class="btn primary" id="new">${ico("plus")}Nuevo perfil</button></div><div class="grid g-auto" id="profiles"></div>
    <div class="card mt"><header><h2>${ico("clock")}Tiempo de pantalla de hoy</h2><span class="small muted">Estimado por la actividad de red de cada dispositivo</span></header><div id="st"></div></div>`;

  el.querySelector("#fam-pause").onclick = () => {
    const m = modal({ title: "Pausa familiar", body: `<p class="muted small">Corta Internet al instante en todos los dispositivos (o sólo en algunos perfiles). Servidores y router quedan fuera.</p>
      <div class="chips mt mb" id="fp">${(el._profiles || []).map((p) => `<span class="chip sel" data-p="${esc(p.id)}">${esc(p.name)}</span>`).join("")}</div>
      <div class="grid g4">${[[15, "15 min"], [30, "30 min"], [60, "1 h"], [0, "Hasta reanudar"]].map(([v, l]) => `<button class="btn" data-min="${v}">${l}</button>`).join("")}</div>` });
    on(m.body, "click", "[data-p]", (e, c) => c.classList.toggle("sel"));
    on(m.body, "click", "[data-min]", async (e, b) => {
      const profs = $$("[data-p].sel", m.body).map((x) => x.dataset.p);
      try { const r = await api.post("/family/pause", { minutes: Number(b.dataset.min), profiles: profs.length === (el._profiles || []).length ? null : profs }); toast(`Internet pausado en ${r.devices} dispositivos`, "info"); m.close(); }
      catch (err) { toastErr(err); }
    });
  };
  el.querySelector("#fam-resume").onclick = async () => { try { const r = await api.post("/family/resume", {}); toast(`Internet reanudado en ${r.devices} dispositivos`); } catch (e) { toastErr(e); } };

  async function loadScreen() {
    const r = await api.get("/screentime").catch(() => ({ devices: [] }));
    const box = el.querySelector("#st");
    if (!r.devices.length) { box.innerHTML = `<p class="muted small">Sin actividad todavía hoy. Configura límites diarios en cada perfil (pestaña «Tiempo de pantalla»).</p>`; return; }
    box.innerHTML = `<div class="grid g2">${r.devices.map((d) => `<div class="rec"><div class="row between"><b>${esc(d.name)}</b><span class="badge">${esc(d.profile || "")}</span></div>
      <div class="col mt">${d.apps.slice(0, 8).map((a) => {
        const lim = a.limit ? a.limit + (a.bonus || 0) : null;
        const pctv = lim ? Math.min(100, (a.minutes / lim) * 100) : null;
        return `<div><div class="row small" style="flex-wrap:nowrap">${a.service === r.all_key ? `<span class="brand-ic" style="background:var(--card-2);color:var(--text)">${ico("globe")}</span>` : appIcon(a.service, a.name)}
          <span class="grow">${esc(a.name)}</span><b>${a.minutes} min</b>${lim ? `<span class="muted"> / ${lim}</span>` : ""}
          ${a.limit ? `<button class="btn sm" data-bonus="${esc(d.mac)}|${esc(a.service)}" title="Dar 15 minutos más">+15</button>` : ""}</div>
          ${lim ? `<div class="progress mt" style="height:5px"><i style="width:${pctv}%;${pctv >= 100 ? "background:var(--err)" : pctv > 80 ? "background:var(--warn)" : ""}"></i></div>` : ""}</div>`;
      }).join("")}</div></div>`).join("")}</div>`;
    on(box, "click", "[data-bonus]", async (e, b) => {
      const [mac, svc] = b.dataset.bonus.split("|");
      try { await api.post(`/screentime/${encodeURIComponent(mac)}/bonus`, { service: svc, minutes: 15 }); toast("15 minutos extra concedidos"); loadScreen(); } catch (err) { toastErr(err); }
    });
  }

  async function load() {
    const [profiles, svc] = await Promise.all([api.get("/profiles"), services.length ? { services } : api.get("/services")]);
    services = svc.services;
    el.querySelector("#profiles").innerHTML = profiles.map((p) => {
      const sch = (p.schedules || []).filter((s) => s.enabled !== false);
      return `<div class="card profile-card" style="--pc:${esc(p.color)}">
        <header><h2>${esc(p.name)}</h2><span class="badge">${p.devices} disp.</span></header>
        <div class="chips mb">
          ${p.block_all ? `<span class="badge err">Sin Internet</span>` : ""}
          ${p.filtering ? `<span class="badge info">Anuncios</span>` : ""}${p.threat_protection ? `<span class="badge ok">Anti-malware</span>` : ""}
          ${p.adult_filter ? `<span class="badge warn">Adultos</span>` : ""}${p.safesearch ? `<span class="badge violet">SafeSearch</span>` : ""}
          ${p.youtube_restricted ? `<span class="badge violet">YouTube restringido</span>` : ""}
        </div>
        <p class="small muted">${p.blocked_services.length ? `${ico("x")} ${p.blocked_services.length} apps bloqueadas: ${esc(p.blocked_services.slice(0, 5).map((id) => t(services.find((s) => s.id === id)?.name || id)).join(", "))}${p.blocked_services.length > 5 ? "…" : ""}` : "Sin apps bloqueadas"}</p>
        <p class="small muted">${sch.length ? `${ico("clock")} ${sch.map((s) => `${esc(s.label || "Horario")} ${s.start}–${s.end}`).join(" · ")}` : "Sin horarios"}</p>
        <div class="row mt"><button class="btn sm" data-edit="${esc(p.id)}">${ico("edit")}Editar</button>
          ${p.id !== "default" ? `<button class="btn sm ghost" data-del="${esc(p.id)}">${ico("trash")}</button>` : ""}</div></div>`;
    }).join("");
    el._profiles = profiles;
    loadScreen();
  }

  el.querySelector("#new").onclick = () => {
    const m = modal({ title: "Nuevo perfil", wide: true, body: `
      <label class="field mb">Nombre<input id="pname" placeholder="Ej. Hijos"></label>
      <h4 class="mb">Empezar con una plantilla</h4>
      <div class="grid g3">${TEMPLATES.map(([id, n, d, c], i) => `<div class="preset ${i === 0 ? "active" : ""}" data-tpl="${id}" style="border-left:3px solid ${c}"><b>${n}</b><span>${d}</span></div>`).join("")}</div>`,
      actions: [{ label: "Cancelar" }, { label: "Crear perfil", kind: "primary", onClick: async (mm) => {
        const tpl = $(".preset.active", mm).dataset.tpl;
        const name = $("#pname", mm).value.trim() || TEMPLATES.find((t) => t[0] === tpl)[1];
        const p = await api.post("/profiles", { name, template: tpl, color: TEMPLATES.find((t) => t[0] === tpl)[3] });
        toast("Perfil creado"); await load(); edit(p.id);
      } }] });
    on(m.body, "click", "[data-tpl]", (e, b) => { $$(".preset", m.body).forEach((x) => x.classList.toggle("active", x === b)); });
  };
  on(el, "click", "[data-edit]", (e, b) => edit(b.dataset.edit));
  on(el, "click", "[data-del]", async (e, b) => {
    if (!(await confirmDialog("Eliminar perfil", "Los dispositivos con este perfil pasarán al perfil predeterminado."))) return;
    await api.del(`/profiles/${b.dataset.del}`).catch(toastErr); load();
  });

  function edit(id) {
    const p = structuredClone(el._profiles.find((x) => x.id === id));
    const cats = [...new Set(services.map((s) => s.category))];
    const letter = (s) => appIcon(s.id, s.name);
    const m = modal({ title: `Perfil: ${p.name}`, wide: true, body: `
      <div class="tabs"><button class="active" data-tab="general">General</button><button data-tab="apps">Apps bloqueadas</button><button data-tab="sched">Horarios</button><button data-tab="limits">Tiempo de pantalla</button></div>
      <div data-pane="general">
        <div class="form-grid mb"><label class="field">Nombre<input name="name" value="${esc(p.name)}"></label><label class="field">Color<input type="color" name="color" value="${esc(p.color)}" style="height:40px;padding:4px"></label></div>
        ${opt("block_all", "Bloquear todo Internet", "Corta Internet a todos los dispositivos de este perfil.", p.block_all)}
        ${opt("filtering", "Bloquear anuncios y rastreadores", "Aplica las listas de anuncios.", p.filtering)}
        ${opt("threat_protection", "Protección contra malware y phishing", "Bloquea dominios maliciosos conocidos.", p.threat_protection)}
        ${opt("adult_filter", "Bloquear contenido adulto y apuestas", "Usa las listas de categoría «adultos».", p.adult_filter)}
        ${opt("safesearch", "Forzar búsqueda segura", "Google, Bing, DuckDuckGo y Brave muestran sólo resultados seguros.", p.safesearch)}
        ${opt("youtube_restricted", "YouTube en modo restringido", "Oculta vídeos potencialmente inadecuados.", p.youtube_restricted)}
      </div>
      <div data-pane="apps" class="hidden">
        <p class="muted small mb">Pulsa para bloquear/desbloquear. Se bloquean todos los dominios de cada app.</p>
        ${cats.map((c) => `<h4 class="mb mt">${esc(c)}</h4><div class="svc-grid">${services.filter((s) => s.category === c).map((s) =>
          `<div class="svc ${p.blocked_services.includes(s.id) ? "on" : ""}" data-svc="${s.id}">${letter(s)}<span class="grow">${esc(s.name)}</span></div>`).join("")}</div>`).join("")}
      </div>
      <div data-pane="limits" class="hidden">
        <p class="muted small mb">Minutos diarios permitidos (vacío = sin límite). Al agotarse, la app o Internet se bloquean hasta medianoche; puedes dar tiempo extra desde aquí o desde el móvil.</p>
        <div class="opt"><div class="t"><b>Internet en total</b><span>Todas las apps y webs</span></div><input type="number" min="0" data-limit="__internet__" value="${esc((p.limits || {}).__internet__ || "")}" placeholder="∞" style="width:100px"> min</div>
        <div class="svc-grid mt">${services.map((x) => `<label class="svc" style="cursor:default">${appIcon(x.id, x.name)}<span class="grow">${esc(x.name)}</span><input type="number" min="0" data-limit="${x.id}" value="${esc((p.limits || {})[x.id] || "")}" placeholder="∞" style="width:74px;height:30px"></label>`).join("")}</div>
      </div>
      <div data-pane="sched" class="hidden">
        <p class="muted small mb">Define franjas horarias. Pueden cruzar la medianoche (p. ej. 22:00–07:00).</p>
        <div id="sched-list" class="col"></div>
        <button class="btn sm mt" id="add-sched">${ico("plus")}Añadir horario</button>
      </div>`,
      actions: [{ label: "Cancelar" }, { label: "Guardar", kind: "primary", onClick: async (mm) => {
        const limits = {};
        $$("[data-limit]", mm).forEach((i) => { if (Number(i.value) > 0) limits[i.dataset.limit] = Number(i.value); });
        const body = { name: $("[name=name]", mm).value, color: $("[name=color]", mm).value, blocked_services: p.blocked_services, schedules: readSched(mm), limits };
        for (const k of ["block_all", "filtering", "threat_protection", "adult_filter", "safesearch", "youtube_restricted"]) body[k] = $(`[name=${k}]`, mm).checked;
        await api.put(`/profiles/${id}`, body);
        toast("Perfil guardado"); load();
      } }] });

    on(m.body, "click", "[data-tab]", (e, b) => {
      $$("[data-tab]", m.body).forEach((x) => x.classList.toggle("active", x === b));
      $$("[data-pane]", m.body).forEach((x) => x.classList.toggle("hidden", x.dataset.pane !== b.dataset.tab));
    });
    on(m.body, "click", "[data-svc]", (e, b) => {
      const sid = b.dataset.svc;
      p.blocked_services = p.blocked_services.includes(sid) ? p.blocked_services.filter((x) => x !== sid) : [...p.blocked_services, sid];
      b.classList.toggle("on");
    });
    const list = $("#sched-list", m.body);
    const schedRow = (s) => `<div class="card flat sched">
      <div class="row"><input data-k="label" value="${esc(s.label || "")}" placeholder="Nombre (ej. Hora de dormir)" style="max-width:220px">
        <input type="time" data-k="start" value="${esc(s.start)}" style="max-width:130px"> a <input type="time" data-k="end" value="${esc(s.end)}" style="max-width:130px">
        <select data-k="action" style="max-width:220px"><option value="block_all" ${s.action === "block_all" ? "selected" : ""}>Bloquear todo Internet</option><option value="block_services" ${s.action === "block_services" ? "selected" : ""}>Bloquear apps elegidas</option></select>
        <span class="grow"></span>${toggle("enabled", s.enabled !== false, 'data-k="enabled"')}<button class="icon-btn" data-rm>${ico("trash")}</button></div>
      <div class="days mt">${DAYS.map((d, i) => `<span class="chip ${(s.days || []).includes(i) ? "sel" : ""}" data-day="${i}">${d}</span>`).join("")}</div>
      <div class="chips mt svcs ${s.action === "block_services" ? "" : "hidden"}">${services.map((x) => `<span class="chip ${(s.services || []).includes(x.id) ? "on" : ""}" data-ssvc="${x.id}">${esc(x.name)}</span>`).join("")}</div></div>`;
    list.innerHTML = (p.schedules || []).map(schedRow).join("");
    $("#add-sched", m.body).onclick = () => list.insertAdjacentHTML("beforeend", schedRow({ start: "22:00", end: "07:00", days: [0, 1, 2, 3, 4, 5, 6], action: "block_all", services: [] }));
    on(list, "click", "[data-day],[data-ssvc]", (e, b) => b.classList.toggle(b.dataset.day ? "sel" : "on"));
    on(list, "click", "[data-rm]", (e, b) => b.closest(".sched").remove());
    on(list, "change", "[data-k=action]", (e, s) => s.closest(".sched").querySelector(".svcs").classList.toggle("hidden", s.value !== "block_services"));
  }

  function readSched(root) {
    return $$(".sched", root).map((r) => ({
      label: $("[data-k=label]", r).value, start: $("[data-k=start]", r).value, end: $("[data-k=end]", r).value,
      action: $("[data-k=action]", r).value, enabled: $("[data-k=enabled]", r).checked,
      days: $$("[data-day].sel", r).map((x) => Number(x.dataset.day)), services: $$("[data-ssvc].on", r).map((x) => x.dataset.ssvc),
    }));
  }

  const opt = (name, t, d, v) => `<div class="opt"><div class="t"><b>${t}</b><span>${d}</span></div>${toggle(name, v)}</div>`;

  await load();
}
