import { api, esc, ico, toast, toastErr, busy, modal, on, toggle, confirmDialog, $, $$ } from "../lib.js";

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
    <button class="btn primary" id="new">${ico("plus")}Nuevo perfil</button></div><div class="grid g-auto" id="profiles"></div>`;

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
        <p class="small muted">${p.blocked_services.length ? `${ico("x")} ${p.blocked_services.length} apps bloqueadas: ${esc(p.blocked_services.slice(0, 5).map((id) => services.find((s) => s.id === id)?.name || id).join(", "))}${p.blocked_services.length > 5 ? "…" : ""}` : "Sin apps bloqueadas"}</p>
        <p class="small muted">${sch.length ? `${ico("clock")} ${sch.map((s) => `${esc(s.label || "Horario")} ${s.start}–${s.end}`).join(" · ")}` : "Sin horarios"}</p>
        <div class="row mt"><button class="btn sm" data-edit="${esc(p.id)}">${ico("edit")}Editar</button>
          ${p.id !== "default" ? `<button class="btn sm ghost" data-del="${esc(p.id)}">${ico("trash")}</button>` : ""}</div></div>`;
    }).join("");
    el._profiles = profiles;
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
    const letter = (s) => `<span class="letter" style="background:hsl(${[...s.id].reduce((a, c) => a + c.charCodeAt(0), 0) % 360} 55% 45%)">${esc(s.name[0])}</span>`;
    const m = modal({ title: `Perfil: ${p.name}`, wide: true, body: `
      <div class="tabs"><button class="active" data-tab="general">General</button><button data-tab="apps">Apps bloqueadas</button><button data-tab="sched">Horarios</button></div>
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
      <div data-pane="sched" class="hidden">
        <p class="muted small mb">Define franjas horarias. Pueden cruzar la medianoche (p. ej. 22:00–07:00).</p>
        <div id="sched-list" class="col"></div>
        <button class="btn sm mt" id="add-sched">${ico("plus")}Añadir horario</button>
      </div>`,
      actions: [{ label: "Cancelar" }, { label: "Guardar", kind: "primary", onClick: async (mm) => {
        const body = { name: $("[name=name]", mm).value, color: $("[name=color]", mm).value, blocked_services: p.blocked_services, schedules: readSched(mm) };
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
