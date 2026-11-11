// Autoridad certificadora local: certificados de confianza para IPs y nombres de la red local.
import { api, esc, ico, toast, toastErr, busy, toggle, copy, modal, confirmDialog, on, $ } from "../lib.js";
import { t } from "../i18n.js";

const GUIDES = [
  ["windows", "Windows", ["Descarga el certificado (.crt) y ábrelo con doble clic.", "Pulsa «Instalar certificado» → «Equipo local».", "Elige «Colocar todos los certificados en el siguiente almacén» → «Entidades de certificación raíz de confianza».", "Termina y reinicia el navegador. Chrome y Edge lo usan al momento."]],
  ["mac", "macOS", ["Descarga el perfil para iPhone/Mac o el .crt y ábrelo: se añade a «Acceso a Llaveros».", "En Llaveros, haz doble clic en «Bipon Net Local CA» → Confiar → «Confiar siempre».", "Cierra la ventana e introduce tu contraseña."]],
  ["ios", "iPhone / iPad", ["Abre en Safari la dirección del código QR y pulsa «Permitir» para descargar el perfil.", "Ajustes → «Perfil descargado» → Instalar.", "Ajustes → General → Información → Ajustes de confianza de certificados → activa «Bipon Net Local CA»."]],
  ["android", "Android", ["Descarga el certificado (.crt) desde el código QR.", "Ajustes → Seguridad → Más ajustes → Cifrado y credenciales → Instalar un certificado → «Certificado de CA».", "Confirma «Instalar de todos modos». Chrome lo usa al instante."]],
  ["linux", "Linux", ["sudo cp bipon-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates", "Fedora/Arch: sudo trust anchor bipon-ca.pem", "Chrome/Chromium en Linux usa su propio almacén: Ajustes → Privacidad → Seguridad → Gestionar certificados → Autoridades → Importar."]],
  ["firefox", "Firefox", ["Firefox usa su propio almacén en todos los sistemas.", "Ajustes → Privacidad y seguridad → Certificados → Ver certificados → Autoridades → Importar.", "Marca «Confiar en esta CA para identificar sitios web»."]],
];

export async function renderCA(box, app) {
  const s = await api.get("/tls/ca");
  const ca = s.ca;
  if (!ca.present) {
    box.innerHTML = `<div class="card"><header><h2>${ico("shield")}${t("Autoridad certificadora local")}</h2><span class="badge">${t("sin crear")}</span></header>
      <p class="muted small mb">${t("Bipon se convierte en la autoridad de certificados de tu red: instalas su certificado raíz UNA vez en cada equipo y todo lo que firme (este panel, Proxmox, el NAS, la impresora, cámaras…) se abre por HTTPS con candado y sin avisos, también por IP.")}</p>
      <div class="opt"><div class="t"><b>${t("Restringir a la red local (recomendado)")}</b><span>${t("La CA sólo podrá firmar IPs privadas y dominios locales (.lan, .local, .home.arpa…). Aunque alguien robara su clave, no podría suplantar webs de Internet.")}</span></div>${toggle("ca-con", true)}</div>
      <label class="field mt">${t("Dominios propios extra (opcional, separados por coma)")}<input id="ca-dom" placeholder="casa.miempresa.com"></label>
      <button class="btn primary mt" id="ca-create">${ico("shield")}${t("Crear CA y firmar el panel")}</button></div>`;
    $("#ca-create", box).onclick = (e) => busy(e.currentTarget, () => api.post("/tls/ca", { constrained: $("[name=ca-con]", box).checked,
      extra_domains: $("#ca-dom", box).value.split(",").map((x) => x.trim()).filter(Boolean) })).then(() => { toast(t("CA creada: el panel ya usa un certificado firmado por ella")); app.reload(); }).catch(() => {});
    return;
  }
  const cert = s.certificate;
  box.innerHTML = `
  <div class="card"><header><h2>${ico("shield")}${t("Autoridad certificadora local")}</h2><span class="badge ok">${t("activa")}</span></header>
    <div class="grid g2">
      <div>
        <dl class="kv small"><dt>${t("Nombre")}</dt><dd>${esc(ca.subject)}</dd><dt>${t("Caduca")}</dt><dd>${ca.days_left} ${t("días")}</dd>
          <dt>${t("Restricción")}</dt><dd>${ca.constrained ? `<span class="badge ok">${t("sólo red local")}</span>` : `<span class="badge warn">${t("sin restricción")}</span>`}</dd>
          <dt>SHA-256</dt><dd class="mono" style="word-break:break-all;font-size:11px">${esc(ca.sha256)}</dd><dt>SHA-1</dt><dd class="mono" style="word-break:break-all;font-size:11px">${esc(ca.sha1)}</dd></dl>
        <h4 class="mt mb">${t("1. Instala el certificado raíz en tus equipos")}</h4>
        <div class="row" style="flex-wrap:wrap">
          <a class="btn sm primary" href="/ca.crt" download>${ico("download")}Windows / Android (.crt)</a>
          <a class="btn sm" href="/ca.mobileconfig" download>${ico("download")}iPhone / Mac</a>
          <a class="btn sm" href="/ca.pem" download>${ico("download")}Linux / Firefox (.pem)</a></div>
        <div class="tabs mt" id="ca-os">${GUIDES.map(([k, l], i) => `<button type="button" data-os="${k}" class="${i ? "" : "active"}">${l}</button>`).join("")}</div>
        <ol class="small mt" id="ca-steps"></ol>
        <p class="small muted">${t("Comprueba que la huella SHA-1 o SHA-256 que muestra tu equipo coincide con la de arriba.")}</p>
      </div>
      <div class="center">
        ${s.qr ? `<div class="qr" style="max-width:220px;margin:0 auto;background:#fff;border-radius:12px;padding:8px">${s.qr}</div>` : ""}
        <p class="small muted mt">${t("Escanéalo con el móvil para descargar el certificado")}</p>
        <div class="row" style="justify-content:center"><code class="small">${esc(s.ca_url)}</code><button class="icon-btn" id="ca-url-cp" title="${t("Copiar")}">${ico("copy")}</button></div>
      </div>
    </div></div>

  <div class="grid g2 mt">
    <div class="card"><header><h3>${ico("lock")}${t("2. Certificado de este panel")}</h3>${cert.signed_by_ca ? `<span class="badge ok">${t("firmado por tu CA")}</span>` : `<span class="badge warn">${t("no firmado por la CA")}</span>`}</header>
      <p class="small muted mb">${t("Se renueva solo si cambia la IP del equipo o le quedan menos de 30 días. El cambio se aplica al momento, sin reiniciar.")}</p>
      <dl class="kv small"><dt>${t("Nombres")}</dt><dd class="mono" style="word-break:break-all">${esc((cert.names || []).join(", "))}</dd><dt>${t("Caduca")}</dt><dd>${cert.days_left ?? "—"} ${t("días")}</dd></dl>
      <label class="field mt">${t("Nombres extra (p. ej. bipon.casa.lan)")}<input id="ca-extra" value="${esc((s.extra_names || []).join(", "))}"></label>
      <button class="btn mt" id="ca-panel">${ico("refresh")}${t("Firmar de nuevo")}</button></div>
    <form class="card" id="ca-new"><header><h3>${ico("plus")}${t("3. Firmar otro equipo")}</h3></header>
      <p class="small muted mb">${t("Para Proxmox, NAS, router, impresoras, cámaras, Home Assistant, Plex… Descarga el certificado y su clave e instálalos en el equipo.")}</p>
      <div class="form-grid"><label class="field">${t("Nombre")}<input name="label" placeholder="NAS" required></label>
        <label class="field">${t("Tipo de clave")}<select name="key_type"><option value="ec">ECDSA P-256 (${t("moderno")})</option><option value="rsa">RSA 2048 (${t("máxima compatibilidad")})</option></select></label></div>
      <label class="field mt">${t("Nombres e IPs (separados por coma o espacio)")}<input name="names" placeholder="nas.lan, 192.168.18.20" required></label>
      <div class="form-grid mt"><label class="field">${t("Validez (días, máx. 825)")}<input type="number" name="days" value="825" min="1" max="825"></label>
        <label class="opt" style="border:0;align-self:end"><div class="t"><b>${t("Crear registro DNS local")}</b><span>${t("nas.lan → su IP")}</span></div>${toggle("dns_record", true)}</label></div>
      <div class="row mt"><button class="btn primary">${ico("shield")}${t("Firmar")}</button>
        <button type="button" class="btn" id="ca-pve">${ico("server")}${t("Firmar Proxmox automáticamente")}</button></div></form>
  </div>

  <div class="card mt"><header><h3>${ico("list")}${t("Certificados emitidos")} (${s.issued.length})</h3></header>
    ${s.issued.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Equipo")}</th><th>${t("Nombres")}</th><th>${t("Caduca")}</th><th></th></tr></thead><tbody>
      ${s.issued.map((c) => `<tr><td><b>${esc(c.label)}</b><div class="small muted">${c.key_type === "rsa" ? "RSA" : "ECDSA"}</div></td><td class="mono small">${esc(c.names.join(", "))}</td>
        <td>${c.days_left < 30 ? `<span class="badge warn">${c.days_left} ${t("días")}</span>` : `${c.days_left} ${t("días")}`}</td>
        <td class="actions nowrap"><button class="btn sm" data-dl="${c.id}">${ico("download")}${t("Descargar")}</button>
          <button class="icon-btn" data-renew="${c.id}" title="${t("Renovar")}">${ico("refresh")}</button><button class="icon-btn" data-cdel="${c.id}" title="${t("Eliminar")}">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted small">${t("Todavía no has firmado ningún equipo.")}</p>`}
    <details class="small mt"><summary>${t("Recrear la CA")}</summary><p class="muted">${t("Genera una CA nueva: tendrás que instalar de nuevo el certificado raíz en todos los equipos y volver a firmar los certificados emitidos. Se guarda una copia de la anterior.")}</p>
      <button class="btn sm danger" id="ca-recreate">${ico("alert")}${t("Recrear CA")}</button></details></div>`;

  const steps = (k) => { const g = GUIDES.find((x) => x[0] === k); $("#ca-steps", box).innerHTML = g[2].map((x) => `<li>${esc(t(x))}</li>`).join(""); };
  steps("windows");
  on(box, "click", "[data-os]", (e, b) => { box.querySelectorAll("[data-os]").forEach((x) => x.classList.toggle("active", x === b)); steps(b.dataset.os); });
  $("#ca-url-cp", box).onclick = () => copy(s.ca_url);
  $("#ca-panel", box).onclick = (e) => busy(e.currentTarget, () => api.post("/tls/ca/panel", { extra_names: $("#ca-extra", box).value.split(/[\s,]+/).filter(Boolean) }))
    .then(() => { toast(t("Certificado del panel firmado y aplicado")); app.reload(); }).catch(() => {});
  const f = $("#ca-new", box);
  f.onsubmit = (e) => {
    e.preventDefault();
    busy(f.querySelector(".primary"), () => api.post("/tls/issued", { label: f.label.value.trim(), names: f.names.value, days: Number(f.days.value) || 825, key_type: f.key_type.value, dns_record: f.dns_record.checked }))
      .then((c) => { toast(`${t("Certificado firmado")}: ${c.label}`); renderCA(box, app).then(() => download(c.id)); }).catch(() => {});
  };
  $("#ca-pve", box).onclick = (e) => busy(e.currentTarget, () => api.post("/tls/proxmox")).then((r) => {
    modal({ title: t("Certificados de Proxmox"), body: r.nodes.map((n) => `<div class="row between small rec mb"><b>${esc(n.node)}</b>${n.ok ? `<span class="badge ok">${t("instalado")}</span>` : `<span class="small" style="color:var(--err)">${esc(n.error)}</span>`}</div>`).join("")
      + `<p class="small muted">${t("Proxmox reinicia su panel web en unos segundos. Ábrelo por su nombre (p. ej. https://homelab.lan:8006) o por su IP.")}</p>`, actions: [{ label: t("Cerrar"), kind: "primary" }] });
    renderCA(box, app);
  }).catch(() => {});
  on(box, "click", "[data-renew]", (e, b) => busy(b, () => api.post(`/tls/issued/${b.dataset.renew}/renew`)).then(() => { toast(t("Certificado renovado: vuelve a instalarlo en el equipo")); renderCA(box, app); }).catch(() => {}));
  on(box, "click", "[data-cdel]", async (e, b) => { if (await confirmDialog(t("Eliminar certificado"), t("¿Seguro?"))) { await api.del(`/tls/issued/${b.dataset.cdel}`).catch(toastErr); renderCA(box, app); } });
  on(box, "click", "[data-dl]", (e, b) => download(b.dataset.dl));
  $("#ca-recreate", box).onclick = async () => {
    if (!(await confirmDialog(t("Recrear CA"), t("Tendrás que instalar de nuevo el certificado raíz en todos tus equipos. ¿Continuar?")))) return;
    await api.post("/tls/ca", { force: true, constrained: ca.constrained }).catch(toastErr);
    renderCA(box, app);
  };

  function download(id) {
    modal({ title: t("Descargar certificado"), body: `
      <div class="col">
        <button class="btn" data-f="pem">${ico("download")}${t("Certificado + cadena (fullchain.pem)")}</button>
        <button class="btn" data-f="key">${ico("key")}${t("Clave privada (key.pem)")}</button>
        <button class="btn" data-f="bundle">${ico("download")}${t("Todo en uno (clave + certificado + CA)")}</button>
        <label class="field mt">${t("Contraseña del .pfx (Windows, IIS, Synology)")}<input type="password" id="pfx-pw" autocomplete="new-password"></label>
        <button class="btn" data-f="pfx">${ico("download")}PFX / PKCS#12</button>
        <p class="small muted">${t("La clave privada permite suplantar a ese equipo: guárdala sólo en él.")}</p></div>`,
      actions: [{ label: t("Cerrar") }] });
    const m = document.querySelector(".overlay:last-of-type");
    m.querySelectorAll("[data-f]").forEach((b) => b.onclick = async () => {
      try {
        const r = await fetch(`/api/tls/issued/${id}/download`, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
          body: JSON.stringify({ format: b.dataset.f, password: m.querySelector("#pfx-pw").value }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        const name = (r.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || "certificado";
        const url = URL.createObjectURL(await r.blob());
        const a = document.createElement("a"); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (err) { toastErr(err); }
    });
  }
}
