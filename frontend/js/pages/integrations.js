import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, copy, download, on } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Integraciones";
export const subtitle = "Proxmox, Home Assistant y un segundo Bipon para alta disponibilidad";

export async function render(el, app) {
  const [px, sy] = await Promise.all([api.get("/integrations/proxmox"), api.get("/sync")]);
  const inv = Object.entries(px.inventory || {});
  el.innerHTML = `<div class="grid g2">
    <form class="card" id="pxf"><header><h2>${ico("server")}Proxmox VE</h2>${inv.length ? `<span class="badge ok">${inv.length} VM/LXC</span>` : ""}</header>
      <p class="muted small mb">${t("Con un token de API, Bipon sabe exactamente qué VM y contenedor hay en cada servidor (jerarquía y nombres reales en Dispositivos), muestra su consumo en Infraestructura y puede encenderlos o apagarlos.")}</p>
      <label class="field">URL<input name="url" value="${esc(px.config.url || "")}" placeholder="https://192.168.1.10:8006"></label>
      <div class="form-grid mt"><label class="field">Token ID<input name="token_id" value="${esc(px.config.token_id || "")}" placeholder="root@pam!bipon"></label>
        <label class="field">${t("Secreto")}<input type="password" name="secret" value="${esc(px.config.secret || "")}" autocomplete="new-password"></label></div>
      <div class="opt"><div class="t"><b>${t("Verificar certificado")}</b><span>${t("Desactívalo si Proxmox usa su certificado autofirmado.")}</span></div>${toggle("verify_tls", px.config.verify_tls)}</div>
      <details class="small mt"><summary>${t("Cómo crear el token")}</summary><ol><li>Proxmox → Datacenter → Permissions → API Tokens → Add.</li>
        <li>${t("Usuario")} <code>root@pam</code>, ID <code>bipon</code>. ${t("Para sólo ver: rol PVEAuditor. Para encender/apagar: PVEVMUser. Para activar TUN: desmarca «Privilege Separation».")}</li><li>${t("Copia el secreto (sólo se muestra una vez).")}</li></ol></details>
      ${px.error ? `<div class="alert err mt small">${ico("alert")}<div>${esc(px.error)}</div></div>` : ""}
      <button class="btn primary mt">${ico("link")}${t("Conectar y sincronizar")}</button></form>
    <div class="card"><header><h2>${ico("home")}Home Assistant</h2></header>
      <p class="muted small mb">${t("Sensores (consultas, bloqueos, latencia, velocidad, dispositivos) e interruptores (pausar bloqueo, pausa familiar, pausar un dispositivo) para tus automatizaciones.")}</p>
      <ol class="steps small"><li>${t("Crea un token de sólo lectura en Usuarios y acceso → Tokens de API (o «Administrador» si quieres usar los interruptores).")}</li>
        <li>${t("Descarga la configuración y pégala en configuration.yaml, sustituyendo TU_TOKEN_DE_BIPON.")}</li><li>${t("Reinicia Home Assistant.")}</li></ol>
      <div class="row mt"><button class="btn" id="ha-dl">${ico("download")}${t("Descargar configuración")}</button><button class="btn" id="ha-cp">${ico("copy")}${t("Copiar")}</button></div>
      <p class="small muted mt">${t("También puedes usar los webhooks de Notificaciones para disparar automatizaciones.")}</p></div></div>
    ${inv.length ? `<div class="card mt"><header><h3>${ico("server")}${t("Inventario de Proxmox")}</h3></header><div class="table-wrap"><table><thead><tr><th>${t("Nombre")}</th><th>${t("Tipo")}</th><th>ID</th><th>${t("Nodo")}</th><th>MAC</th><th>${t("Estado")}</th></tr></thead><tbody>
      ${inv.map(([mac, v]) => `<tr><td><b>${esc(v.name || "")}</b></td><td>${v.type === "lxc" ? "LXC" : "VM"}</td><td>${v.vmid}</td><td>${esc(v.node)} <span class="muted small">${esc(v.node_ip || "")}</span></td><td class="mono small">${esc(mac)}</td>
      <td><span class="badge ${v.status === "running" ? "ok" : ""}">${esc(v.status || "")}</span></td></tr>`).join("")}</tbody></table></div></div>` : ""}
    <form class="card mt" id="syf"><header><h2>${ico("refresh")}${t("Alta disponibilidad (dos Bipon)")}</h2></header>
      <p class="muted small mb">${t("Instala un segundo Bipon (otra Raspberry, otro LXC) en modo «secundario»: copia de este la configuración, listas, perfiles y dispositivos. Si uno cae, la red sigue con el otro (pon ambos como DNS en el router, o activa el modo automático en los dos).")}</p>
      <div class="form-grid"><label class="field">${t("Este equipo es")}<select name="role">${[["standalone", "Independiente"], ["primary", "Principal"], ["secondary", "Secundario (copia del principal)"]].map(([v, l]) => `<option value="${v}" ${sy.config.role === v ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label>
        <label class="field">${t("URL del principal")}<input name="primary_url" value="${esc(sy.config.primary_url)}" placeholder="https://192.168.1.2:8443"></label>
        <label class="field">${t("Token del principal (rol administrador)")}<input type="password" name="token" value="${esc(sy.config.token)}" autocomplete="new-password"></label>
        <label class="field">${t("Sincronizar cada (s)")}<input type="number" name="interval" value="${sy.config.interval}" min="60"></label></div>
      <div class="row mt"><button class="btn primary">${ico("check")}${t("Guardar")}</button><button type="button" class="btn" id="sync-now">${ico("refresh")}${t("Sincronizar ahora")}</button>
        <span class="small muted">${sy.last_ok ? `${t("Última sincronización")} ${ago(sy.last_ok)}` : ""}</span></div>
      ${sy.last_error ? `<div class="alert err mt small">${ico("alert")}<div>${esc(sy.last_error)}</div></div>` : ""}</form>`;

  const pf = el.querySelector("#pxf");
  pf.onsubmit = (e) => { e.preventDefault(); busy(pf.querySelector(".primary"), () => api.put("/integrations/proxmox", { url: pf.url.value.trim(), token_id: pf.token_id.value.trim(), secret: pf.secret.value, verify_tls: pf.verify_tls.checked }))
    .then((r) => { toast(`${Object.keys(r.inventory || {}).length} ${t("VM/LXC sincronizadas")}`); app.reload(); }).catch(() => {}); };
  const yaml = () => api("/integrations/config/ha.yaml");
  el.querySelector("#ha-dl").onclick = async () => download("bipon-home-assistant.yaml", await yaml());
  el.querySelector("#ha-cp").onclick = async () => copy(await yaml());
  const sf = el.querySelector("#syf");
  sf.onsubmit = (e) => { e.preventDefault(); busy(sf.querySelector(".primary"), () => api.put("/sync", { role: sf.role.value, primary_url: sf.primary_url.value.trim(), token: sf.token.value, interval: Number(sf.interval.value) })).then(() => toast(t("Guardado"))).catch(() => {}); };
  el.querySelector("#sync-now").onclick = (e) => busy(e.currentTarget, () => api.post("/sync/now")).then((r) => toast(`${t("Sincronizado")}: ${r.rules} ${t("reglas")}, ${r.lists} ${t("listas")}, ${r.devices} ${t("dispositivos")}`)).catch(() => {});
}
