import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, on, modal, confirmDialog, $, $$ } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Notificaciones";
export const subtitle = "Avisos al móvil o al correo cuando pasa algo importante en tu red";

const HELP = {
  telegram: "Crea un bot con @BotFather (te da el token) y escribe a tu bot; el chat ID lo obtienes con @userinfobot.",
  discord: "Ajustes del canal → Integraciones → Webhooks → Nuevo webhook → Copiar URL.",
  slack: "Crea una app → Incoming Webhooks → copia la URL.",
  ntfy: "Instala la app ntfy y suscríbete a un tema difícil de adivinar (p. ej. bipon-casa-8f2k). Servidor por defecto: https://ntfy.sh",
  gotify: "URL de tu servidor Gotify y el token de una aplicación.",
  email: "Servidor SMTP de tu correo (Gmail: smtp.gmail.com, puerto 587, STARTTLS, con contraseña de aplicación).",
  webhook: "Bipon enviará un POST JSON; si pones un secreto, firma el cuerpo con HMAC-SHA256 (cabecera X-Bipon-Signature).",
};
const FIELD = { bot_token: "Token del bot", chat_id: "Chat ID", webhook_url: "URL del webhook", server: "Servidor", topic: "Tema", token: "Token",
  url: "URL", host: "Servidor SMTP", port: "Puerto", security: "Seguridad (starttls/ssl/none)", username: "Usuario", password: "Contraseña",
  sender: "Remitente", to: "Destinatario", secret: "Secreto (opcional)" };

export async function render(el, app) {
  const s = await api.get("/notify");
  const ev = s.events || {};
  el.innerHTML = `
    <div class="grid g3">
      <div class="card span2"><header><h2>${ico("info")}${t("Canales")}</h2><button class="btn primary sm" id="add">${ico("plus")}${t("Añadir canal")}</button></header>
        ${s.channels.length ? `<div class="col">${s.channels.map((c) => `<div class="rec row" style="flex-wrap:nowrap"><div class="grow"><b>${esc(c.name)}</b> <span class="badge">${esc(s.channel_types[c.type]?.label || c.type)}</span>
          ${c.enabled ? "" : `<span class="badge">${t("desactivado")}</span>`}</div><button class="btn sm" data-test="${c.id}">${ico("zap")}${t("Probar")}</button>
          <button class="icon-btn" data-edit="${c.id}">${ico("edit")}</button><button class="icon-btn" data-del="${c.id}">${ico("trash")}</button></div>`).join("")}</div>`
        : empty(t("Añade Telegram, Discord, ntfy, email… para recibir avisos."), "info")}
        <div class="opt mt"><div class="t"><b>${t("Notificaciones push en este navegador/móvil")}</b><span>${t("Requiere HTTPS con certificado válido (p. ej. a través de Tailscale o Cloudflare Tunnel). Suscripciones activas")}: ${s.push_subscriptions}</span></div>
          <button class="btn sm" id="push">${ico("plus")}${t("Activar en este dispositivo")}</button></div></div>
      <div class="card"><header><h3>${ico("moon")}${t("Horas de silencio")}</h3></header>
        <div class="opt"><div class="t"><b>${t("Activar")}</b><span>${t("No enviar avisos en esta franja.")}</span></div>${toggle("q", s.quiet.enabled, 'id="q-en"')}</div>
        <div class="form-grid mt"><label class="field">${t("Desde")}<input type="time" id="q-s" value="${esc(s.quiet.start)}"></label><label class="field">${t("Hasta")}<input type="time" id="q-e" value="${esc(s.quiet.end)}"></label></div>
        <div class="opt"><div class="t"><b>${t("Dejar pasar los urgentes")}</b><span>${t("Caídas de Internet, amenazas…")}</span></div>${toggle("qh", s.quiet.allow_high, 'id="q-h"')}</div>
        <button class="btn sm mt" id="q-save">${ico("check")}${t("Guardar")}</button></div>
    </div>
    <div class="card mt"><header><h2>${ico("list")}${t("¿De qué quieres enterarte?")}</h2></header>
      ${Object.entries(s.event_catalog).map(([id, e]) => {
        const cur = ev[id]; const on_ = cur === undefined ? e.default : typeof cur === "boolean" ? cur : cur.enabled !== false;
        return `<div class="opt"><div class="t"><b>${e.icon} ${esc(t(e.label))}</b><span>${e.priority === "high" ? t("Urgente") : e.priority === "low" ? t("Informativo") : t("Normal")}</span></div>${toggle(id, on_, `data-ev="${id}"`)}</div>`;
      }).join("")}</div>
    <div class="card mt"><header><h3>${ico("clock")}${t("Últimos envíos")}</h3></header>${s.log.length ? `<div class="table-wrap"><table><tbody>${s.log.map((l) => `<tr><td class="small muted nowrap">${ago(l.ts)}</td><td>${esc(l.title)}</td><td>${esc(l.channel)}</td>
      <td>${l.ok ? `<span class="badge ok">${t("enviado")}</span>` : `<span class="badge err" title="${esc(l.error)}">${t("error")}</span>`}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted small">${t("Nada enviado todavía")}</p>`}</div>`;

  const editChannel = (ch) => {
    const type = ch?.type || "telegram";
    const m = modal({ title: ch ? t("Editar canal") : t("Nuevo canal"), body: `<label class="field">${t("Tipo")}<select id="ct" ${ch ? "disabled" : ""}>${Object.entries(s.channel_types).map(([k, v]) => `<option value="${k}" ${k === type ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select></label>
        <label class="field mt">${t("Nombre")}<input id="cn" value="${esc(ch?.name || "")}"></label><div id="cf" class="form-grid mt"></div><p class="small muted mt" id="ch-help"></p>`,
      actions: [{ label: t("Cancelar") }, { label: t("Guardar"), kind: "primary", onClick: async (mm) => {
        const config = {}; $$("[data-cf]", mm).forEach((i) => { config[i.dataset.cf] = i.value; });
        await api.post("/notify/channels", { id: ch?.id, type: $("#ct", mm).value, name: $("#cn", mm).value, enabled: true, config });
        toast(t("Canal guardado")); app.reload();
      } }] });
    const fields = () => {
      const tp = $("#ct", m.body).value;
      $("#cf", m.body).innerHTML = s.channel_types[tp].fields.map((f) => `<label class="field">${t(FIELD[f] || f)}<input data-cf="${f}" value="${esc(ch?.config?.[f] || (f === "server" ? "https://ntfy.sh" : f === "port" ? "587" : f === "security" ? "starttls" : ""))}" ${["password", "bot_token", "token", "secret"].includes(f) ? 'type="password" autocomplete="new-password"' : ""}></label>`).join("");
      $("#ch-help", m.body).textContent = t(HELP[tp] || "");
    };
    $("#ct", m.body).onchange = fields; fields();
  };
  el.querySelector("#add").onclick = () => editChannel(null);
  on(el, "click", "[data-edit]", (e, b) => editChannel(s.channels.find((c) => c.id === b.dataset.edit)));
  on(el, "click", "[data-del]", async (e, b) => { if (await confirmDialog(t("Eliminar canal"), t("¿Seguro?"))) { await api.del(`/notify/channels/${b.dataset.del}`); app.reload(); } });
  on(el, "click", "[data-test]", (e, b) => busy(b, () => api.post(`/notify/channels/${b.dataset.test}/test`)).then((r) => toast(r.ok ? t("¡Mensaje de prueba enviado!") : `${t("Error")}: ${r.error}`, r.ok ? "ok" : "error", 8000)).catch(() => {}));
  on(el, "change", "[data-ev]", async (e, i) => { const events = { ...ev, [i.dataset.ev]: i.checked }; await api.put("/notify/settings", { events }).catch(toastErr); ev[i.dataset.ev] = i.checked; toast(t("Guardado")); });
  el.querySelector("#q-save").onclick = () => api.put("/notify/settings", { quiet: { enabled: $("#q-en", el).checked, start: $("#q-s", el).value, end: $("#q-e", el).value, allow_high: $("#q-h", el).checked } }).then(() => toast(t("Guardado"))).catch(toastErr);
  el.querySelector("#push").onclick = (e) => busy(e.currentTarget, async () => {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) throw new Error(t("Las notificaciones push requieren abrir Bipon por HTTPS con un certificado válido."));
    const reg = await navigator.serviceWorker.register("/sw.js");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error(t("Permiso de notificaciones denegado"));
    const key = s.vapid_public_key.replace(/-/g, "+").replace(/_/g, "/");
    const raw = Uint8Array.from(atob(key + "=".repeat((4 - key.length % 4) % 4)), (ch) => ch.charCodeAt(0));
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
    await api.post("/push/subscribe", sub.toJSON());
    await api.post("/push/test");
    toast(t("Notificaciones push activadas"));
  }).catch(() => {});
}
