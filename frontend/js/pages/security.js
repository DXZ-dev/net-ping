import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, on, $$ } from "../lib.js";
import { countryName, t } from "../i18n.js";

export const title = "Seguridad";
export const subtitle = "Detección de amenazas, bloqueo por país y protección de la red";

const KIND_ICON = { dga: "alert", tunnel: "network", burst: "activity", threats: "shield", iot_spread: "chip" };

export async function render(el, app) {
  const s = await api.get("/security");
  const c = s.config;
  const tlds = new Set(c.blocked_tlds);
  const countries = new Set(c.blocked_countries);
  const open = s.anomalies.filter((a) => !a.acknowledged);
  el.innerHTML = `
    <div class="grid g4 mb">
      ${stat("Anomalías abiertas", String(open.length), open.length ? "red" : "green", "alert")}
      ${stat("Último análisis", ago(s.last_run), "blue", "clock")}
      ${stat("TLD bloqueados", String(tlds.size), "amber", "globe")}
      ${stat("Países bloqueados", String(countries.size), "violet", "shield")}
    </div>
    <div class="grid g3">
      <div class="card span2"><header><h2>${ico("alert")}${t("Anomalías detectadas")}</h2><button class="btn sm" id="scan">${ico("search")}${t("Analizar ahora")}</button></header>
        <p class="muted small mb">${t("Bipon analiza cada 5 minutos el tráfico DNS de cada dispositivo buscando malware (dominios generados aleatoriamente), túneles DNS de exfiltración, ráfagas anómalas, accesos repetidos a dominios maliciosos e IoT comprometido.")}</p>
        ${s.anomalies.length ? `<div class="col">${s.anomalies.map((a) => `<div class="rec row" style="flex-wrap:nowrap;align-items:flex-start;${a.acknowledged ? "opacity:.55" : ""}">
          <div class="stat"><div class="ic tint-${a.acknowledged ? "green" : "red"}" style="width:36px;height:36px">${ico(KIND_ICON[a.kind] || "alert")}</div></div>
          <div class="grow"><b>${esc(t(a.label))}</b> <span class="badge">${esc(a.device)}</span><div class="small muted">${esc(a.detail)}</div><div class="small muted">${ago(a.ts)}</div></div>
          ${a.acknowledged ? `<span class="badge ok">${t("Revisada")}</span>` : `<button class="btn sm" data-ack="${a.id}">${ico("check")}${t("Marcar revisada")}</button>`}</div>`).join("")}</div>`
        : empty(t("Sin anomalías. Tu red está tranquila."), "shield")}</div>
      <div class="card"><header><h3>${ico("settings")}${t("Protecciones")}</h3></header>
        ${opt("anomaly_detection", "Detección de anomalías", "Análisis automático de comportamiento.", c.anomaly_detection)}
        ${opt("rebind_protection", "Protección DNS rebinding", "Bloquea dominios públicos que apuntan a IPs de tu red (ataques a routers y cámaras).", c.rebind_protection)}
        <label class="field mt">${t("Excepciones de rebinding (una por línea)")}<textarea id="rb" rows="3" placeholder="plex.direct">${esc(c.rebind_allow.join("\n"))}</textarea></label>
        <button class="btn sm mt" id="save-rb">${ico("check")}${t("Guardar")}</button></div>
    </div>
    <div class="grid g2 mt">
      <div class="card"><header><h2>${ico("globe")}${t("Bloquear dominios de primer nivel (TLD)")}</h2></header>
        <p class="muted small mb">${t("Los TLD baratos concentran gran parte del phishing y malware. Pulsa para bloquear o desbloquear.")}</p>
        <div class="chips">${s.risky_tlds.map((x) => `<span class="chip ${tlds.has(x) ? "on" : ""}" data-tld="${x}">.${x}</span>`).join("")}</div>
        <div class="inline-form mt"><input id="tld-new" placeholder="${t("otro TLD, p. ej. ru")}"><button class="btn" id="tld-add">${ico("plus")}</button></div>
        <div class="chips mt">${[...tlds].filter((x) => !s.risky_tlds.includes(x)).map((x) => `<span class="chip on" data-tld="${esc(x)}">.${esc(x)} ✕</span>`).join("")}</div></div>
      <div class="card"><header><h2>${ico("shield")}${t("Bloquear servidores por país")}</h2></header>
        <p class="muted small mb">${t("Bloquea las conexiones a servidores alojados en los países que elijas (base de datos GeoIP DB-IP, se descarga automáticamente).")}
          ${s.geo.loaded ? `<span class="badge ok">${t("GeoIP cargada")} · ${s.geo.ranges.toLocaleString()} ${t("rangos")}</span>` : `<span class="badge">${t("GeoIP se descargará al activar un país")}</span>`}</p>
        <div class="chips">${Object.entries(s.countries).map(([code, name]) => `<span class="chip ${countries.has(code) ? "on" : ""}" data-cc="${code}">${esc(countryName(code, name))}</span>`).join("")}</div></div>
    </div>
    <div class="card mt" id="quarantine"></div>
    <div class="grid g2 mt"><div class="card" id="bypass"></div><div class="card" id="upnp"></div></div>
    <div class="card mt" id="intruders"></div>`;
  drawIntruders(el.querySelector("#intruders"));
  drawQuarantine(el.querySelector("#quarantine"));
  drawBypass(el.querySelector("#bypass"));
  drawUpnp(el.querySelector("#upnp"));

  const save = (patch, msg = t("Guardado")) => api.put("/security", patch).then(() => { toast(msg); app.reload(); }).catch(toastErr);
  el.querySelector("#scan").onclick = (e) => busy(e.currentTarget, async () => { const r = await api.post("/security/scan"); toast(r.length ? `${r.length} ${t("anomalías nuevas")}` : t("Sin anomalías nuevas")); app.reload(); }).catch(() => {});
  on(el, "click", "[data-ack]", async (e, b) => { await api.post(`/security/anomalies/${b.dataset.ack}/ack`).catch(toastErr); app.reload(); });
  on(el, "change", "[data-opt]", (e, i) => save({ [i.dataset.opt]: i.checked }));
  el.querySelector("#save-rb").onclick = () => save({ rebind_allow: el.querySelector("#rb").value.split(/\s+/).filter(Boolean) });
  on(el, "click", "[data-tld]", (e, b) => { const x = b.dataset.tld; tlds.has(x) ? tlds.delete(x) : tlds.add(x); save({ blocked_tlds: [...tlds] }); });
  el.querySelector("#tld-add").onclick = () => { const v = el.querySelector("#tld-new").value.trim().replace(/^\./, "").toLowerCase(); if (v) { tlds.add(v); save({ blocked_tlds: [...tlds] }); } };
  on(el, "click", "[data-cc]", (e, b) => { const x = b.dataset.cc; countries.has(x) ? countries.delete(x) : countries.add(x); save({ blocked_countries: [...countries] }, t("Guardado (la base GeoIP puede tardar en descargarse)")); });
}

const stat = (l, v, tint, icon) => `<div class="card stat"><div class="ic tint-${tint}">${ico(icon)}</div><div><div class="v">${esc(v)}</div><div class="l">${t(l)}</div></div></div>`;
const opt = (k, ti, d, v) => `<div class="opt"><div class="t"><b>${t(ti)}</b><span>${t(d)}</span></div>${toggle(k, v, `data-opt="${k}"`)}</div>`;

// Equipos conectados a tu línea con IP fuera de tu red local (IP fija para escaparse, otra VLAN, intruso)
async function drawIntruders(box) {
  if (!box) return;
  const s = await api.get("/intruders").catch(() => null);
  if (!s) { box.remove(); return; }
  box.innerHTML = `<header><h2>${ico("alert")}${t("Equipos con IP fuera de tu red")}</h2></header>
    <p class="muted small mb">${t("Dispositivos presentes en tu línea cuya IP no pertenece a tu red local")} (${s.local_networks.join(", ") || "—"}): ${t("una IP fija puesta a mano para saltarse el control, otra VLAN o un equipo colado.")}
      ${s.router_mode ? "" : `<br>${t("Para cortarles Internet activa el modo router (Bipon como puerta de enlace).")}`}</p>
    ${s.intruders.length ? `<div class="table-wrap"><table><thead><tr><th></th><th>${t("Dispositivo")}</th><th>IP</th><th>${t("Marca")}</th><th></th></tr></thead><tbody>
      ${s.intruders.map((d) => `<tr><td><span class="dot ${d.blocked ? "dot-err" : d.online ? "dot-ok" : "dot-off"}"></span></td>
        <td><b>${esc(d.name || "—")}</b></td><td class="mono small">${esc(d.ip)}<div class="small muted">${esc(d.mac)}</div></td><td class="small">${esc(d.brand || "")}</td>
        <td class="actions nowrap">${d.blocked ? `<button class="btn sm success" data-unblock="${esc(d.mac)}">${ico("check")}${t("Permitir")}</button>` : `<button class="btn sm danger" data-block="${esc(d.mac)}" ${s.can_block ? "" : "disabled"}>${ico("x")}${t("Cortar Internet")}</button>`}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted small">${t("Ninguno: todos tus equipos usan una IP de tu red local.")}</p>`}`;
  on(box, "click", "[data-block]", (e, b) => busy(b, () => api.post(`/intruders/${encodeURIComponent(b.dataset.block)}/block`, { block: true })).then((r) => { toast(r.enforced ? t("Internet cortado") : t("Guardado (se cortará al activar el modo router)"), r.enforced ? "ok" : "info", 7000); drawIntruders(box); }).catch(() => {}));
  on(box, "click", "[data-unblock]", (e, b) => busy(b, () => api.post(`/intruders/${encodeURIComponent(b.dataset.unblock)}/block`, { block: false })).then(() => { toast(t("Internet restablecido")); drawIntruders(box); }).catch(() => {}));
}


// Cuarentena automática ante malware
async function drawQuarantine(box) {
  const q = await api.get("/guard/quarantine").catch(() => null);
  if (!q) { box.remove(); return; }
  box.innerHTML = `<header><h2>${ico("shield")}${t("Cuarentena automática")}</h2>${toggle("q-on", q.config.quarantine)}</header>
    <p class="muted small mb">${t("Si un equipo muestra malware (dominios aleatorios, túnel DNS o accesos repetidos a dominios maliciosos), se le corta Internet unas horas para que no se propague y te avisamos.")}</p>
    <label class="field" style="max-width:220px">${t("Horas de cuarentena")}<input type="number" id="q-h" min="1" max="72" value="${q.config.quarantine_hours}"></label>
    ${q.devices.length ? `<div class="col mt">${q.devices.map((d) => `<div class="row between rec"><span><b>${esc(d.name)}</b> <span class="muted small">${esc(d.ip || "")}</span><div class="small muted">${esc(d.detail || "")} · ${t("hasta")} ${new Date(d.until * 1000).toLocaleString()}</div></span>
      <button class="btn sm" data-release="${esc(d.mac)}">${ico("check")}${t("Liberar")}</button></div>`).join("")}</div>` : `<p class="muted small mt">${t("Ningún equipo en cuarentena.")}</p>`}`;
  const save = () => api.put("/guard/quarantine", { quarantine: box.querySelector("[name=q-on]").checked, quarantine_hours: Number(box.querySelector("#q-h").value) || 6 }).then(() => toast(t("Guardado"))).catch(toastErr);
  box.querySelector("[name=q-on]").onchange = save;
  box.querySelector("#q-h").onchange = save;
  on(box, "click", "[data-release]", (e, b) => busy(b, () => api.del(`/guard/quarantine/${encodeURIComponent(b.dataset.release)}`)).then(() => { toast(t("Liberado")); drawQuarantine(box); }).catch(() => {}));
}

// Equipos que se saltan Bipon
async function drawBypass(box) {
  const b = await api.get("/guard/bypass").catch(() => null);
  if (!b) { box.remove(); return; }
  const list = (rows, extra) => rows.length ? `<div class="col">${rows.map((d) => `<div class="rec small"><b>${esc(d.name)}</b> <span class="muted">${esc(d.ip)}</span>${extra(d)}</div>`).join("")}</div>` : `<p class="muted small">${t("Ninguno")}</p>`;
  box.innerHTML = `<header><h2>${ico("alert")}${t("Equipos que se saltan Bipon")}</h2></header>
    <p class="muted small mb">${t("Su tráfico no se filtra: no ven el bloqueo de anuncios ni el control parental.")}</p>
    <h4 class="mb">${t("Sin usar el DNS de Bipon (últimas 24 h)")}</h4>
    ${list(b.no_dns, () => `<div class="muted">${t("Tiene un DNS puesto a mano. Quítalo en la configuración de red del equipo o activa «Forzar DNS de Bipon» en modo router.")}</div>`)}
    <h4 class="mt mb">${t("Usando DNS cifrado externo")}</h4>
    ${list(b.encrypted, (d) => `<div class="muted">${d.domains.map((x) => esc(x.domain)).join(", ")}</div>`)}
    <div class="row mt"><button class="btn sm ${b.doh_blocked ? "" : "primary"}" id="doh">${ico("shield")}${b.doh_blocked ? t("Permitir DNS cifrado externo") : t("Bloquear DNS cifrado externo en toda la red")}</button></div>
    ${b.force_dns ? `<p class="small muted mt">${ico("check")} ${t("Forzar DNS de Bipon está activo (modo router).")}</p>` : ""}`;
  box.querySelector("#doh").onclick = (e) => busy(e.currentTarget, () => api.post("/guard/block-doh", { block: !b.doh_blocked })).then(() => { toast(t("Guardado")); drawBypass(box); }).catch(() => {});
}

// Puertos abiertos al exterior por UPnP
async function drawUpnp(box) {
  box.innerHTML = `<header><h2>${ico("router")}${t("Puertos abiertos en tu router")}</h2><button class="btn sm" id="up-r">${ico("refresh")}${t("Revisar")}</button></header><p class="muted small">${t("Buscando…")}</p>`;
  const r = await api.get("/guard/upnp").catch(() => null);
  const RISK = { high: ["err", "Peligroso"], medium: ["warn", "Revisar"], info: ["", "Normal"] };
  box.innerHTML = `<header><h2>${ico("router")}${t("Puertos abiertos en tu router")}</h2><button class="btn sm" id="up-r">${ico("refresh")}${t("Revisar")}</button></header>
    <p class="muted small mb">${t("Lo que tu router ha abierto hacia Internet por UPnP (muchas veces sin que lo sepas). Los peligrosos conviene cerrarlos.")}</p>
    ${!r || !r.available ? `<p class="muted small">${t("Tu router no tiene UPnP activo (eso es bueno para la seguridad).")}</p>`
      : r.mappings.length ? `<div class="table-wrap"><table><tbody>${r.mappings.map((m) => `<tr><td><span class="badge ${RISK[m.risk][0]}">${t(RISK[m.risk][1])}</span></td>
          <td class="mono small">${m.external_port}/${esc(m.protocol)}</td><td class="small"><b>${esc(m.device || "")}</b><div class="muted">${esc(m.why || m.description || "")}</div></td>
          <td class="actions"><button class="btn sm ${m.risk === "high" ? "danger" : ""}" data-upclose="${m.external_port}" data-proto="${esc(m.protocol)}">${t("Cerrar")}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted small">${ico("check")} ${t("Ningún puerto abierto por UPnP.")}</p>`}`;
  box.querySelector("#up-r").onclick = () => drawUpnp(box);
  on(box, "click", "[data-upclose]", (e, b) => busy(b, () => api.post("/guard/upnp/close", { external_port: Number(b.dataset.upclose), protocol: b.dataset.proto })).then(() => { toast(t("Puerto cerrado")); drawUpnp(box); }).catch(() => {}));
}
