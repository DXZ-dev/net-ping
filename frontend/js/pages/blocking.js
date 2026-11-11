import { api, esc, ico, num, ago, toast, toastErr, busy, modal, on, toggle, confirmDialog, empty, fresh, $, $$ } from "../lib.js";
import { t } from "../i18n.js";
import { appIcon } from "../brands.js";

export const title = "Bloqueo";
export const subtitle = "Listas de bloqueo, reglas propias y aplicaciones";

const CAT = { ads: ["Anuncios/rastreo", "info"], security: ["Seguridad", "ok"], adult: ["Adultos/apuestas", "warn"], custom: ["Personalizada", ""] };
const KIND = { allow: ["Permitir", "ok"], deny: ["Bloquear", "err"], regex_allow: ["Regex permitir", "ok"], regex_deny: ["Regex bloquear", "err"] };

export async function render(el) {
  let tab = "lists";
  el.innerHTML = `<div id="sugg"></div><div class="tabs">
      <button data-tab="lists" class="active">${ico("list")}Listas</button><button data-tab="rules">${ico("edit")}Reglas propias</button>
      <button data-tab="apps">${ico("devices")}Aplicaciones</button><button data-tab="check">${ico("search")}Comprobar dominio</button></div>
    <div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  drawSuggestions(el.querySelector("#sugg"));
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x === b)); show(); });

  async function show() {
    pane = fresh(pane);
    if (tab === "lists") return lists();
    if (tab === "rules") return rules();
    if (tab === "apps") return apps();
    return check();
  }

  // ---------------------------------------------------------------- listas
  async function lists() {
    const d = await api.get("/lists");
    pane = fresh(pane);
    pane.innerHTML = `
      <div class="grid g3 mb">
        <div class="card stat"><div class="ic tint-red">${ico("shield")}</div><div><div class="v">${num(d.build.total)}</div><div class="l">dominios bloqueados</div></div></div>
        <div class="card stat"><div class="ic tint-blue">${ico("list")}</div><div><div class="v">${d.lists.filter((l) => l.enabled).length}/${d.lists.length}</div><div class="l">listas activas</div></div></div>
        <div class="card stat"><div class="ic tint-green">${ico("clock")}</div><div><div class="v">${ago(d.build.ts)}</div><div class="l">última compilación (${d.build.seconds ?? 0} s)</div></div></div>
      </div>
      <div class="card"><header><h2>Listas de bloqueo</h2>
        <button class="btn" id="upd">${ico("refresh")}Actualizar todas</button><button class="btn primary" id="add">${ico("plus")}Añadir lista</button></header>
        <div class="table-wrap"><table><thead><tr><th>Activa</th><th>Lista</th><th>Categoría</th><th class="right">Dominios</th><th>Actualizada</th><th></th></tr></thead><tbody>
        ${d.lists.map((l) => `<tr><td>${toggle("e", l.enabled, `data-tog="${l.id}"`)}</td>
          <td><b>${esc(l.name)}</b><div class="muted small truncate" style="max-width:420px">${esc(l.url)}</div></td>
          <td><span class="badge ${CAT[l.category]?.[1] || ""}">${CAT[l.category]?.[0] || esc(l.category)}</span></td>
          <td class="right">${num(l.domains)}</td>
          <td>${l.last_status && l.last_status !== "OK" ? `<span class="badge err" title="${esc(l.last_status)}">error</span>` : `<span class="muted">${ago(l.last_updated)}</span>`}</td>
          <td class="actions"><button class="icon-btn" data-upd="${l.id}" title="Actualizar">${ico("refresh")}</button><button class="icon-btn" data-del="${l.id}" title="Eliminar">${ico("trash")}</button></td></tr>`).join("")}
        </tbody></table></div></div>
      ${d.recommended.length ? `<div class="card mt"><header><h3>Listas recomendadas</h3></header><div class="grid g2">
        ${d.recommended.map((r) => `<div class="rec row between"><div><b>${esc(r.name)}</b> <span class="badge ${CAT[r.category]?.[1]}">${CAT[r.category]?.[0]}</span></div>
          <button class="btn sm" data-rec="${esc(r.url)}" data-name="${esc(r.name)}" data-cat="${r.category}">${ico("plus")}Añadir</button></div>`).join("")}</div></div>` : ""}`;
    pane.querySelector("#upd").onclick = (e) => busy(e.currentTarget, async () => {
      const r = await api.post("/lists/update");
      toast(`Listas actualizadas: ${num(r.build.total)} dominios`); lists();
    }).catch(() => {});
    pane.querySelector("#add").onclick = () => modal({ title: "Añadir lista de bloqueo", body: `
      <div class="col"><label class="field">URL<input name="url" placeholder="https://…/hosts.txt"><small>Formatos: hosts, dominios, Adblock (||dominio^) y comodines.</small></label>
      <label class="field">Nombre<input name="name"></label>
      <label class="field">Categoría<select name="category">${Object.entries(CAT).map(([k, [v]]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <small>La categoría decide qué perfiles la aplican (p. ej. «Adultos» sólo en perfiles con ese filtro).</small></label></div>`,
      actions: [{ label: "Cancelar" }, { label: "Añadir", kind: "primary", onClick: async (m) => {
        const r = await api.post("/lists", { url: $("[name=url]", m).value, name: $("[name=name]", m).value, category: $("[name=category]", m).value });
        r.result?.ok ? toast(`Lista añadida (${num(r.result.domains)} dominios)`) : toast(`Añadida, pero no se pudo descargar: ${r.result?.message}`, "warn");
        lists();
      } }] });
    on(pane, "change", "[data-tog]", async (e, t) => { await api.patch(`/lists/${t.dataset.tog}`, { enabled: t.checked }).catch(toastErr); toast(t.checked ? "Lista activada" : "Lista desactivada"); });
    on(pane, "click", "[data-upd]", (e, b) => busy(b, async () => { const r = await api.post("/lists/update", { id: Number(b.dataset.upd) }); toast(r.results[0]?.ok ? "Actualizada" : r.results[0]?.message, r.results[0]?.ok ? "ok" : "error"); lists(); }).catch(() => {}));
    on(pane, "click", "[data-del]", async (e, b) => { if (await confirmDialog("Eliminar lista", "¿Seguro?")) { await api.del(`/lists/${b.dataset.del}`); lists(); } });
    on(pane, "click", "[data-rec]", (e, b) => busy(b, async () => { await api.post("/lists", { url: b.dataset.rec, name: b.dataset.name, category: b.dataset.cat }); toast("Lista añadida"); lists(); }).catch(() => {}));
  }

  // ---------------------------------------------------------------- reglas
  async function rules() {
    const rows = await api.get("/rules");
    pane = fresh(pane);
    pane.innerHTML = `<div class="grid g3">
      <div class="card"><header><h3>Añadir regla</h3></header>
        <form class="col" id="rf">
          <label class="field">Acción<select name="kind">${Object.entries(KIND).map(([k, [v]]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
          <label class="field">Dominios (uno por línea)<textarea name="values" placeholder="ejemplo.com\nads.ejemplo.net"></textarea>
            <small>Un dominio incluye sus subdominios. Las regex se aplican al nombre completo, p. ej. <code>^ads?[0-9]*\\.</code></small></label>
          <label class="field">Comentario<input name="comment"></label>
          <button class="btn primary">${ico("plus")}Añadir</button></form></div>
      <div class="card span2"><header><h3>Reglas (${rows.length})</h3><input id="rsearch" placeholder="Filtrar…" style="max-width:220px"></header>
        <div class="table-wrap"><table><thead><tr><th>Activa</th><th>Dominio / patrón</th><th>Tipo</th><th>Comentario</th><th></th></tr></thead><tbody id="rbody"></tbody></table></div></div></div>`;
    const body = pane.querySelector("#rbody");
    const draw = (q = "") => {
      const f = rows.filter((r) => r.value.includes(q));
      body.innerHTML = f.length ? f.map((r) => `<tr><td>${toggle("e", r.enabled, `data-rtog="${r.id}"`)}</td><td class="mono">${esc(r.value)}</td>
        <td><span class="badge ${KIND[r.kind][1]}">${KIND[r.kind][0]}</span></td><td class="muted">${esc(r.comment)}</td>
        <td class="actions"><button class="icon-btn" data-rdel="${r.id}">${ico("trash")}</button></td></tr>`).join("") : `<tr><td colspan="5">${empty("Sin reglas")}</td></tr>`;
    };
    draw();
    pane.querySelector("#rsearch").oninput = (e) => draw(e.target.value.toLowerCase());
    pane.querySelector("#rf").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      await busy(f.querySelector("button"), async () => {
        const r = await api.post("/rules", { kind: f.kind.value, values: f.values.value.split(/\s+/).filter(Boolean), comment: f.comment.value });
        toast(`${r.added} regla(s) añadida(s)`); rules();
      }).catch(() => {});
    };
    on(pane, "change", "[data-rtog]", (e, t) => api.patch(`/rules/${t.dataset.rtog}`, { enabled: t.checked }).catch(toastErr));
    on(pane, "click", "[data-rdel]", async (e, b) => { await api.del(`/rules/${b.dataset.rdel}`).catch(toastErr); rules(); });
  }

  // ---------------------------------------------------------------- apps
  async function apps() {
    const { services } = await api.get("/services");
    pane = fresh(pane);
    const blocked = new Set(services.filter((s) => s.blocked).map((s) => s.id));
    const cats = [...new Set(services.map((s) => s.category))];
    const letter = (s) => appIcon(s.id, s.name);
    pane.innerHTML = `<div class="card"><header><h2>Bloquear aplicaciones en toda la red</h2><span class="badge err" id="cnt">${blocked.size} bloqueadas</span></header>
      <p class="muted small mb">Para bloquear apps sólo en algunos dispositivos (p. ej. los de los niños) usa <a href="#/parental">Control parental</a>.</p>
      ${cats.map((c) => `<h4 class="mb mt">${esc(c)}</h4><div class="svc-grid">${services.filter((s) => s.category === c).map((s) =>
        `<div class="svc ${blocked.has(s.id) ? "on" : ""}" data-svc="${s.id}" title="${esc(s.domains.join(", "))}">${letter(s)}<span class="grow">${esc(s.name)}</span>${blocked.has(s.id) ? ico("x", "err") : ""}</div>`).join("")}</div>`).join("")}</div>`;
    on(pane, "click", "[data-svc]", async (e, b) => {
      const id = b.dataset.svc;
      blocked.has(id) ? blocked.delete(id) : blocked.add(id);
      try {
        await api.put("/services", { blocked: [...blocked] });
        const s = services.find((x) => x.id === id);
        toast(`${s.name} ${blocked.has(id) ? "bloqueado" : "desbloqueado"}`, blocked.has(id) ? "info" : "ok");
        apps();
      } catch (err) { toastErr(err); }
    });
  }

  // ---------------------------------------------------------------- comprobar
  async function check() {
    const devs = (await api.get("/devices").catch(() => ({ devices: [] }))).devices.filter((d) => d.ip);
    pane.innerHTML = `<div class="card"><header><h2>¿Por qué se bloquea (o no) un dominio?</h2></header>
      <form class="inline-form" id="cf"><input name="d" placeholder="ej. ads.example.com" required>
        <select name="c" style="max-width:260px"><option value="">Perfil predeterminado</option>${devs.map((d) => `<option value="${esc(d.ip)}">${esc(d.name || d.hostname || d.ip)}</option>`).join("")}</select>
        <button class="btn primary">${ico("search")}Comprobar</button></form><div id="cres" class="mt"></div></div>`;
    pane.querySelector("#cf").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        const r = await api.get(`/check?domain=${encodeURIComponent(f.d.value)}&client=${encodeURIComponent(f.c.value)}`);
        const d = r.decision;
        pane.querySelector("#cres").innerHTML = `
          <div class="alert ${d?.blocked ? "err" : d ? "info" : "ok"}">${ico(d?.blocked ? "x" : "check")}<div>
            <b>${esc(r.domain)}: ${d ? esc(d.status) : "Permitido"}</b><p class="small">${d ? esc(d.reason) : "No coincide con ninguna regla ni lista activa"} · Perfil: ${esc(r.profile)}</p></div></div>
          ${r.matches.length ? `<h4 class="mt mb">Coincidencias</h4><div class="col small">${r.matches.map((m) => `<div><span class="badge">${esc(m.type)}</span> <code>${esc(m.match)}</code> ${m.categories ? esc(m.categories.join(", ")) : ""}</div>`).join("")}</div>` : ""}
          ${r.services.length ? `<p class="small mt">Pertenece a: <b>${esc(r.services.join(", "))}</b></p>` : ""}
          <div class="row mt"><button class="btn sm success" data-q="allow">${ico("check")}Permitir siempre</button><button class="btn sm danger" data-q="deny">${ico("x")}Bloquear siempre</button></div>`;
        pane.querySelector("#cres").onclick = async (ev) => {
          const b = ev.target.closest("[data-q]");
          if (!b) return;
          await api.post("/rules", { kind: b.dataset.q, value: r.domain }).catch(toastErr);
          toast("Regla añadida"); f.requestSubmit();
        };
      } catch (err) { toastErr(err); }
    };
  }

  show();
}

// «¿Algo dejó de funcionar?»: dominios bloqueados que una app reintenta sin parar
async function drawSuggestions(box) {
  const rows = await api.get("/blocking/suggestions").catch(() => []);
  if (!rows.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="card mb"><header><h3>${ico("alert")}${t("¿Algo dejó de funcionar?")}</h3></header>
    <p class="muted small mb">${t("Estos dominios bloqueados se están reintentando muchas veces: suele ser una app o web que no funciona sin ellos. Si algo falla, permítelo; si no, descártalo.")}</p>
    <div class="col">${rows.map((r) => `<div class="row between rec" data-row="${esc(r.domain)}"><span class="grow truncate"><b class="mono">${esc(r.domain)}</b>
        <div class="small muted">${r.known ? esc(t(r.known)) + " · " : ""}${r.retries} ${t("reintentos")} · ${esc(r.devices.join(", "))}</div></span>
      <button class="btn sm success" data-sallow="${esc(r.domain)}">${ico("check")}${t("Permitir")}</button><button class="btn sm ghost" data-sdis="${esc(r.domain)}">${t("Descartar")}</button></div>`).join("")}</div></div>`;
  const gone = (d) => { box.querySelector(`[data-row="${CSS.escape(d)}"]`)?.remove(); if (!box.querySelector("[data-row]")) box.innerHTML = ""; };
  box.querySelectorAll("[data-sallow]").forEach((b) => b.onclick = () => busy(b, () => api.post("/rules", { kind: "allow", value: b.dataset.sallow })).then(() => { toast(`${b.dataset.sallow} ${t("permitido")}`); gone(b.dataset.sallow); }).catch(() => {}));
  box.querySelectorAll("[data-sdis]").forEach((b) => b.onclick = () => api.post("/blocking/suggestions/dismiss", { domain: b.dataset.sdis }).then(() => gone(b.dataset.sdis)).catch(() => {}));
}
