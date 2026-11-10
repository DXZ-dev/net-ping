import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, copy, modal, confirmDialog, on, $ } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Usuarios y acceso";
export const subtitle = "Cuentas, roles, verificación en dos pasos, tokens de API y HTTPS";

const ROLE = { admin: ["Administrador", "Control total"], parent: ["Padres", "Dispositivos, perfiles, tiempo de pantalla y pausas"], viewer: ["Sólo lectura", "Ver estadísticas sin cambiar nada"] };

export async function render(el, app) {
  const [users, tokens, me, tls] = await Promise.all([api.get("/users"), api.get("/tokens"), api.get("/auth/status"), api.get("/tls")]);
  const cert = tls.certificate;
  el.innerHTML = `<div class="grid g3">
    <div class="card span2"><header><h2>${ico("users")}${t("Usuarios")}</h2><button class="btn primary sm" id="add-u">${ico("plus")}${t("Nuevo usuario")}</button></header>
      <div class="table-wrap"><table><thead><tr><th>${t("Usuario")}</th><th>${t("Rol")}</th><th>2FA</th><th>${t("Último acceso")}</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr><td><b>${esc(u.username)}</b>${u.id === me.user?.id ? ` <span class="badge info">${t("tú")}</span>` : ""}</td>
        <td><select data-role="${u.id}" style="width:auto">${Object.entries(ROLE).map(([k, [l]]) => `<option value="${k}" ${u.role === k ? "selected" : ""}>${t(l)}</option>`).join("")}</select></td>
        <td>${u.totp_enabled ? `<span class="badge ok">${t("activa")}</span>` : `<span class="badge">${t("no")}</span>`}</td><td class="small muted">${u.last_login ? ago(u.last_login) : t("nunca")}</td>
        <td class="actions"><button class="btn sm" data-pw="${u.id}">${ico("key")}${t("Contraseña")}</button>${u.totp_enabled ? `<button class="btn sm" data-r2fa="${u.id}">${t("Quitar 2FA")}</button>` : ""}
          ${u.id !== me.user?.id ? `<button class="icon-btn" data-del="${u.id}">${ico("trash")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
      <div class="grid g3 mt small">${Object.entries(ROLE).map(([k, [l, d]]) => `<div class="rec"><b>${t(l)}</b><div class="muted">${t(d)}</div></div>`).join("")}</div></div>
    <div class="card"><header><h3>${ico("lock")}${t("Tu verificación en dos pasos")}</h3></header>
      <p class="muted small">${t("Protege tu cuenta con un código de Google Authenticator, Microsoft Authenticator, Authy o 1Password.")}</p>
      <div id="tfa" class="mt">${me.user?.totp_enabled ? `<div class="alert ok">${ico("check")}<div>${t("Activada")}</div></div><button class="btn sm mt" id="tfa-off">${t("Desactivar")}</button>`
        : `<button class="btn primary" id="tfa-on">${ico("key")}${t("Activar 2FA")}</button>`}</div></div></div>
    <div class="grid g2 mt">
      <div class="card"><header><h2>${ico("key")}${t("Tokens de API")}</h2><button class="btn sm" id="add-t">${ico("plus")}${t("Nuevo token")}</button></header>
        <p class="muted small mb">${t("Para Home Assistant, scripts o un segundo Bipon. Se envían como «Authorization: Bearer np_…».")}</p>
        ${tokens.length ? tokens.map((x) => `<div class="row between rec mb"><span><b>${esc(x.name)}</b> <span class="badge">${t(ROLE[x.role][0])}</span><br><span class="small muted">${t("creado")} ${ago(x.created)} · ${x.last_used ? `${t("usado")} ${ago(x.last_used)}` : t("sin usar")}</span></span>
          <button class="icon-btn" data-tdel="${x.id}">${ico("trash")}</button></div>`).join("") : empty(t("Sin tokens"))}</div>
      <div class="card"><header><h2>${ico("lock")}${t("HTTPS y DNS cifrado")}</h2></header>
        <dl class="kv small"><dt>${t("Certificado")}</dt><dd>${cert.present ? `${esc(cert.subject)} ${cert.self_signed ? `<span class="badge warn">${t("autofirmado")}</span>` : `<span class="badge ok">${t("de confianza")}</span>`}` : "—"}</dd>
          <dt>${t("Caduca")}</dt><dd>${cert.present ? `${cert.days_left} ${t("días")}` : "—"}</dd><dt>${t("Nombres")}</dt><dd class="mono">${esc((cert.names || []).join(", "))}</dd></dl>
        <form id="tlf" class="mt">
          <div class="opt"><div class="t"><b>${t("Panel por HTTPS")}</b><span>${t("Puerto")} ${tls.settings.port || 8443}</span></div>${toggle("enabled", tls.settings.enabled !== false)}</div>
          <div class="opt"><div class="t"><b>${t("Redirigir HTTP a HTTPS")}</b></div>${toggle("redirect_http", tls.settings.redirect_http)}</div>
          <div class="opt"><div class="t"><b>DNS-over-HTTPS</b><span>/dns-query</span></div>${toggle("doh", tls.settings.doh !== false)}</div>
          <div class="opt"><div class="t"><b>DNS-over-TLS</b><span>${t("Puerto")} 853 — ${t("«DNS privado» de Android con un certificado válido")}</span></div>${toggle("dot", tls.settings.dot !== false)}</div>
          <div class="row mt"><button class="btn primary">${ico("check")}${t("Guardar")}</button><button type="button" class="btn" id="cert-up">${ico("upload")}${t("Usar mi certificado")}</button><button type="button" class="btn ghost" id="cert-regen">${t("Regenerar autofirmado")}</button></div>
          <p class="small muted mt">${t("Los cambios de HTTPS se aplican al reiniciar Bipon.")}</p></form></div></div>`;

  on(el, "change", "[data-role]", (e, s) => api.patch(`/users/${s.dataset.role}`, { role: s.value }).then(() => toast(t("Rol actualizado"))).catch((err) => { toastErr(err); app.reload(); }));
  on(el, "click", "[data-del]", async (e, b) => { if (await confirmDialog(t("Eliminar usuario"), t("¿Seguro?"))) { await api.del(`/users/${b.dataset.del}`).catch(toastErr); app.reload(); } });
  on(el, "click", "[data-r2fa]", async (e, b) => { await api.patch(`/users/${b.dataset.r2fa}`, { reset_2fa: true }).catch(toastErr); app.reload(); });
  on(el, "click", "[data-pw]", (e, b) => modal({ title: t("Nueva contraseña"), body: `<label class="field">${t("Contraseña")}<input type="password" id="np" minlength="8" autocomplete="new-password"></label>`,
    actions: [{ label: t("Cancelar") }, { label: t("Guardar"), kind: "primary", onClick: async (m) => { await api.patch(`/users/${b.dataset.pw}`, { password: $("#np", m).value }); toast(t("Contraseña cambiada")); } }] }));
  el.querySelector("#add-u").onclick = () => modal({ title: t("Nuevo usuario"), body: `<div class="col"><label class="field">${t("Usuario")}<input id="nu"></label><label class="field">${t("Contraseña")}<input type="password" id="np" minlength="8" autocomplete="new-password"></label>
      <label class="field">${t("Rol")}<select id="nr">${Object.entries(ROLE).map(([k, [l, d]]) => `<option value="${k}" ${k === "parent" ? "selected" : ""}>${t(l)} — ${t(d)}</option>`).join("")}</select></label></div>`,
    actions: [{ label: t("Cancelar") }, { label: t("Crear"), kind: "primary", onClick: async (m) => { await api.post("/users", { username: $("#nu", m).value, password: $("#np", m).value, role: $("#nr", m).value }); toast(t("Usuario creado")); app.reload(); } }] });
  el.querySelector("#add-t").onclick = () => modal({ title: t("Nuevo token de API"), body: `<div class="col"><label class="field">${t("Nombre")}<input id="tn" placeholder="Home Assistant"></label><label class="field">${t("Rol")}<select id="tr">${Object.entries(ROLE).map(([k, [l]]) => `<option value="${k}" ${k === "viewer" ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label></div>`,
    actions: [{ label: t("Cancelar") }, { label: t("Crear"), kind: "primary", onClick: async (m) => {
      const r = await api.post("/tokens", { name: $("#tn", m).value, role: $("#tr", m).value });
      setTimeout(() => modal({ title: t("Copia tu token"), body: `<p class="small warn">${t("Sólo se muestra esta vez.")}</p><pre data-notr>${esc(r.token)}</pre>`, actions: [{ label: t("Copiar"), kind: "primary", onClick: () => copy(r.token) }], onClose: () => app.reload() }), 200);
    } }] });
  on(el, "click", "[data-tdel]", async (e, b) => { await api.del(`/tokens/${b.dataset.tdel}`).catch(toastErr); app.reload(); });
  const on2 = el.querySelector("#tfa-on");
  if (on2) on2.onclick = async () => {
    const s = await api.post("/auth/2fa/setup");
    modal({ title: t("Activar verificación en dos pasos"), body: `<div class="row" style="align-items:flex-start;gap:18px"><div class="qr">${s.qr || ""}</div><div class="grow col">
      <p class="small">${t("1. Escanea el QR con tu app de autenticación (o introduce la clave).")}</p><code data-notr class="mono small" style="word-break:break-all">${esc(s.secret)}</code>
      <label class="field mt">${t("2. Escribe el código de 6 dígitos")}<input id="code" inputmode="numeric" maxlength="6"></label></div></div>`,
      actions: [{ label: t("Cancelar") }, { label: t("Activar"), kind: "primary", onClick: async (m) => { await api.post("/auth/2fa/enable", { code: $("#code", m).value }); toast(t("2FA activada")); app.reload(); } }] });
  };
  const off = el.querySelector("#tfa-off");
  if (off) off.onclick = () => modal({ title: t("Desactivar 2FA"), body: `<label class="field">${t("Tu contraseña")}<input type="password" id="pw"></label>`,
    actions: [{ label: t("Cancelar") }, { label: t("Desactivar"), kind: "danger", onClick: async (m) => { await api.post("/auth/2fa/disable", { password: $("#pw", m).value }); toast(t("2FA desactivada")); app.reload(); } }] });
  const tf = el.querySelector("#tlf");
  tf.onsubmit = (e) => { e.preventDefault(); busy(tf.querySelector(".primary"), () => api.put("/tls", { enabled: tf.enabled.checked, redirect_http: tf.redirect_http.checked, doh: tf.doh.checked, dot: tf.dot.checked })).then(() => toast(t("Guardado. Reinicia Bipon para aplicar HTTPS."), "info", 7000)).catch(() => {}); };
  el.querySelector("#cert-regen").onclick = (e) => busy(e.currentTarget, () => api.post("/tls/certificate", { regenerate: true })).then(() => { toast(t("Certificado regenerado")); app.reload(); }).catch(() => {});
  el.querySelector("#cert-up").onclick = () => modal({ title: t("Usar mi certificado"), wide: true, body: `<label class="field">${t("Certificado (PEM, cadena completa)")}<textarea id="cc" rows="7"></textarea></label><label class="field mt">${t("Clave privada (PEM)")}<textarea id="ck" rows="5"></textarea></label>`,
    actions: [{ label: t("Cancelar") }, { label: t("Instalar"), kind: "primary", onClick: async (m) => { await api.post("/tls/certificate", { cert: $("#cc", m).value, key: $("#ck", m).value }); toast(t("Certificado instalado. Reinicia Bipon."), "info"); app.reload(); } }] });
}
