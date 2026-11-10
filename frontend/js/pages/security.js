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
    </div>`;

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
