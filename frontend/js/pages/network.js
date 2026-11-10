import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, on, modal, $, $$, bytes } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Red: DHCP, Wi-Fi y portal";

let capTimer = null;
const capRow = (a, reg) => `<tr><td><span class="dot ${a.online ? "dot-ok" : "dot-off"}"></span> ${esc(a.device || a.mac)}<div class="small muted">${esc(a.name || "")} ${esc(a.email || "")}</div></td>
  <td class="small muted nowrap">${new Date(a.ts * 1000).toLocaleString(document.documentElement.lang === "en" ? "en-US" : "es")}</td>
  <td class="actions nowrap">${a.mac.startsWith("ip:") ? "" : `<button class="icon-btn" data-trust-mac="${esc(a.mac)}" data-on="${reg ? 1 : 0}" title="${t(reg ? "Quitar de registrados" : "Registrar (no volver a pedir)")}">${ico("star")}</button>`}
  <button class="icon-btn" data-rst="${esc(a.mac)}" title="${t("Pedir de nuevo")}">${ico("refresh")}</button></td></tr>`;
export const subtitle = "Cómo se conectan tus dispositivos a Bipon, la cobertura Wi-Fi y la página de bienvenida";

export async function render(el, app) {
  let tab = sessionStorage.getItem("np-net-tab") || "auto";
  el.innerHTML = `<div class="tabs mb">
    <button data-tab="auto">${ico("zap")}${t("Modo automático")}</button><button data-tab="dhcp">${ico("server")}DHCP</button>
    <button data-tab="wifi">${ico("wifi")}Wi-Fi</button><button data-tab="captive">${ico("shield")}${t("Portal de bienvenida")}</button>
    <button data-tab="access">${ico("lock")}${t("Acceso local / externo")}</button></div><div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; try { sessionStorage.setItem("np-net-tab", tab); } catch {} show(); });
  async function show() {
    $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const n = pane.cloneNode(false); pane.replaceWith(n); pane = n;
    pane.innerHTML = `<div class="skeleton" style="height:240px"></div>`;
    try { await VIEWS[tab](); } catch (e) { pane.innerHTML = `<div class="alert err">${ico("alert")}<div>${esc(e.message)}</div></div>`; }
  }

  const VIEWS = {
    // ------------------------------------------------------------ modo automático (IPv6 RDNSS)
    async auto() {
      const s = await api.get("/autodns");
      pane.innerHTML = `<div class="grid g3"><div class="card span2">
        <header><h2>${ico("zap")}${t("Usar Bipon sin tocar el router")}</h2>${s.announcing ? `<span class="badge ok"><span class="live-dot"></span> ${t("anunciando")}</span>` : `<span class="badge">${t("inactivo")}</span>`}</header>
        <p class="muted small">${t("Bipon se anuncia por IPv6 como servidor DNS de la red (estándar RFC 8106). Windows, Android, iOS, macOS y Linux lo empiezan a usar solos. No cambia la puerta de enlace: el tráfico sigue yendo por tu router.")}</p>
        <div class="opt"><div class="t"><b>${t("Activar modo automático")}</b><span>${t("Si Bipon se apaga o falla, deja de anunciarse y en menos de 90 s todos vuelven al DNS del router.")}</span></div>${toggle("en", s.config.enabled, 'id="ad-en"')}</div>
        <div class="form-grid mt"><label class="field">${t("Interfaz")}<input id="ad-if" value="${esc(s.config.interface || "")}" placeholder="${t("automática")}"></label>
          <label class="field">${t("Dominio de búsqueda")}<input id="ad-dom" value="${esc(s.config.search_domain || "lan")}"></label></div>
        <button class="btn primary mt" id="ad-save">${ico("check")}${t("Guardar")}</button>
        ${s.error ? `<div class="alert err mt">${ico("alert")}<div class="small">${esc(s.error)}</div></div>` : ""}</div>
        <div class="card"><header><h3>${ico("info")}${t("Estado")}</h3></header><dl class="kv">
          <dt>${t("DNS anunciado")}</dt><dd class="mono">${esc(s.address || "—")}</dd><dt>${t("Prefijo privado")}</dt><dd class="mono">${esc(s.prefix || "—")}</dd>
          <dt>${t("Interfaz")}</dt><dd>${esc(s.interface || "—")}</dd><dt>${t("Salud del DNS")}</dt><dd>${s.healthy ? `<span class="badge ok">OK</span>` : `<span class="badge">—</span>`}</dd>
          <dt>${t("Anuncios enviados")}</dt><dd>${s.sent} · ${ago(s.last_sent)}</dd><dt>${t("Clientes por IPv6")}</dt><dd>${s.ipv6_clients}</dd></dl>
          <p class="small muted mt">${t("Para filtrar el 100 % del tráfico, lo ideal sigue siendo poner Bipon como DNS en el router: con el modo automático algunos equipos pueden preguntar a veces al DNS del router.")}</p></div></div>`;
      pane.querySelector("#ad-save").onclick = (e) => busy(e.currentTarget, () => api.put("/autodns", { enabled: pane.querySelector("#ad-en").checked,
        interface: pane.querySelector("#ad-if").value.trim(), search_domain: pane.querySelector("#ad-dom").value.trim() })).then(() => { toast(t("Guardado")); setTimeout(show, 1500); }).catch(() => {});
    },
    // ------------------------------------------------------------ DHCP
    async dhcp() {
      const s = await api.get("/dhcp");
      const c = { ...s.suggested, ...s.config };
      pane.innerHTML = `<div class="grid g3"><form class="card span2" id="df">
        <header><h2>${ico("server")}${t("Servidor DHCP de Bipon")}</h2>${s.running ? `<span class="badge ok">${t("activo")}</span>` : `<span class="badge">${t("inactivo")}</span>`}</header>
        <p class="muted small">${t("Úsalo si tu router no permite cambiar el DNS: desactiva el DHCP del router y deja que Bipon reparta las IP (con su DNS y nombres automáticos equipo.lan).")}</p>
        ${s.error ? `<div class="alert err mb">${ico("alert")}<div class="small">${esc(s.error)}</div></div>` : ""}
        <div class="opt"><div class="t"><b>${t("Activar DHCP")}</b><span>${t("Antes de activarlo se comprueba que no haya otro servidor DHCP en la red.")}</span></div>${toggle("enabled", c.enabled)}</div>
        <div class="form-grid mt">
          ${f("server_ip", "IP de este servidor", c.server_ip)}${f("netmask", "Máscara", c.netmask)}${f("router", "Puerta de enlace (router)", c.router)}
          ${f("range_start", "Rango: inicio", c.range_start)}${f("range_end", "Rango: fin", c.range_end)}${f("lease_time", "Concesión (s)", c.lease_time, "number")}
          ${f("domain", "Dominio local", c.domain)}${f("dns", "DNS (separados por coma)", (c.dns || []).join(", "))}${f("interface", "Interfaz", c.interface)}</div>
        <h4 class="mt mb">${t("Reservas de IP")}</h4><div id="statics" class="col"></div>
        <button type="button" class="btn sm mt" id="add-st">${ico("plus")}${t("Añadir reserva")}</button>
        <div class="row mt"><button class="btn primary">${ico("check")}${t("Guardar y aplicar")}</button><button type="button" class="btn" id="probe">${ico("search")}${t("Buscar otros DHCP")}</button></div>
        <div id="probe-out" class="mt"></div></form>
        <div class="card"><header><h3>${ico("activity")}${t("Estadísticas")}</h3></header><dl class="kv">${Object.entries(s.stats).map(([k, v]) => `<dt>${k.toUpperCase()}</dt><dd>${v}</dd>`).join("")}</dl></div></div>
        <div class="card mt"><header><h3>${ico("list")}${t("Concesiones")} (${s.leases.length})</h3></header>
        ${s.leases.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Estado")}</th><th>IP</th><th>MAC</th><th>${t("Nombre")}</th><th>${t("Caduca")}</th><th></th></tr></thead><tbody>
        ${s.leases.map((l) => `<tr><td>${l.active ? `<span class="badge ok">${t("activa")}</span>` : `<span class="badge">${t("caducada")}</span>`}</td><td class="mono">${esc(l.ip)}</td><td class="mono muted">${esc(l.mac)}</td>
          <td>${esc(l.name || l.hostname || "")}</td><td class="small">${new Date(l.expires * 1000).toLocaleString()}</td><td class="actions"><button class="icon-btn" data-rel="${esc(l.mac)}">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div>` : empty(t("Sin concesiones"))}</div>`;
      const statics = [...(c.static || [])];
      const drawSt = () => { pane.querySelector("#statics").innerHTML = statics.map((x, i) => `<div class="inline-form"><input data-si="${i}" data-k="mac" value="${esc(x.mac)}" placeholder="MAC"><input data-si="${i}" data-k="ip" value="${esc(x.ip)}" placeholder="IP"><input data-si="${i}" data-k="hostname" value="${esc(x.hostname || "")}" placeholder="${t("nombre")}"><button type="button" class="icon-btn" data-sdel="${i}">${ico("trash")}</button></div>`).join("") || `<p class="small muted">${t("Sin reservas")}</p>`; };
      drawSt();
      on(pane, "input", "[data-si]", (e, i) => { statics[i.dataset.si][i.dataset.k] = i.value.trim(); });
      on(pane, "click", "[data-sdel]", (e, b) => { statics.splice(Number(b.dataset.sdel), 1); drawSt(); });
      pane.querySelector("#add-st").onclick = () => { statics.push({ mac: "", ip: "", hostname: "" }); drawSt(); };
      on(pane, "click", "[data-rel]", async (e, b) => { await api.del(`/dhcp/leases/${encodeURIComponent(b.dataset.rel)}`).catch(toastErr); show(); });
      pane.querySelector("#probe").onclick = (e) => busy(e.currentTarget, async () => {
        const r = await api.post("/dhcp/probe");
        pane.querySelector("#probe-out").innerHTML = r.length ? r.map((x) => x.error ? `<div class="alert warn small">${esc(x.error)}</div>` : `<div class="alert ${x.own ? "ok" : "warn"} small">${ico(x.own ? "check" : "alert")}<div>${x.own ? t("Bipon") : t("Otro servidor DHCP")}: <b>${esc(x.server)}</b> · ${t("ofrece")} ${esc(x.offered_ip)} · DNS ${esc((x.dns || []).join(", "))}</div></div>`).join("") : `<div class="alert ok small">${ico("check")}<div>${t("No hay otros servidores DHCP en la red.")}</div></div>`;
      }).catch(() => {});
      const form = pane.querySelector("#df");
      form.onsubmit = async (e) => {
        e.preventDefault();
        const body = { enabled: form.enabled.checked, static: statics.filter((x) => x.mac && x.ip) };
        for (const k of ["server_ip", "netmask", "router", "range_start", "range_end", "domain", "interface"]) body[k] = form[k].value.trim();
        body.lease_time = Number(form.lease_time.value) || 86400;
        body.dns = form.dns.value.split(/[\s,]+/).filter(Boolean);
        try { await busy(form.querySelector(".primary"), () => api.put("/dhcp", body)); toast(t("DHCP actualizado")); show(); }
        catch (err) {
          if (err.status === 409 && confirm(err.message + "\n\n" + t("¿Activar igualmente?"))) { await api.put("/dhcp", { ...body, force: true }).catch(toastErr); show(); }
        }
      };
    },
    // ------------------------------------------------------------ Wi-Fi
    async wifi() {
      const s = await api.get("/integrations");
      const st = s.wifi_state || {};
      pane.innerHTML = `<div class="card mb"><header><h2>${ico("wifi")}${t("Controladores Wi-Fi")}</h2><button class="btn sm" id="wf-ref">${ico("refresh")}${t("Actualizar")}</button><button class="btn sm primary" id="wf-add">${ico("plus")}${t("Añadir")}</button></header>
        <p class="muted small mb">${t("Conecta tu UniFi, OpenWrt o MikroTik para ver la señal de cada dispositivo, el canal y la saturación de cada radio y recibir recomendaciones.")}</p>
        ${s.wifi.length ? s.wifi.map((w) => `<div class="row between rec mb"><span>${ico("router")} <b>${esc(w.name || w.type)}</b> <span class="muted small">${esc(s.wifi_types[w.type])} · ${esc(w.url)}</span></span><button class="icon-btn" data-wdel="${esc(w.id)}">${ico("trash")}</button></div>`).join("") : empty(t("Ningún controlador conectado"), "wifi")}
        ${Object.entries(st.errors || {}).map(([k, v]) => `<div class="alert err small mt">${ico("alert")}<div><b>${esc(k)}</b>: ${esc(v)}</div></div>`).join("")}</div>
        ${st.recommendations?.length ? `<div class="card mb"><header><h3>${ico("zap")}${t("Recomendaciones")}</h3></header>${st.recommendations.map((r) => `<div class="alert info small mb">${ico("info")}<div>${esc(r)}</div></div>`).join("")}</div>` : ""}
        ${st.radios?.length ? `<div class="card mb"><header><h3>${ico("router")}${t("Radios")}</h3></header><div class="table-wrap"><table><thead><tr><th>AP</th><th>${t("Banda")}</th><th>${t("Canal")}</th><th class="right">${t("Uso")}</th><th class="right">${t("Clientes")}</th></tr></thead><tbody>
          ${st.radios.map((r) => `<tr><td>${esc(r.ap)}</td><td>${esc(r.band)}</td><td>${esc(r.channel ?? "")}</td><td class="right">${r.utilization != null ? r.utilization + " %" : "—"}</td><td class="right">${r.clients ?? ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
        ${st.clients?.length ? `<div class="card"><header><h3>${ico("devices")}${t("Clientes Wi-Fi")}</h3></header><div class="table-wrap"><table><thead><tr><th>${t("Dispositivo")}</th><th>AP</th><th>${t("Banda")}</th><th class="right">${t("Señal")}</th><th>${t("Calidad")}</th><th class="right">${t("Velocidad")}</th></tr></thead><tbody>
          ${st.clients.map((c) => `<tr><td><b>${esc(c.name || c.mac)}</b><div class="small muted mono">${esc(c.ip || c.mac)}</div></td><td>${esc(c.ap || "")}</td><td>${esc(c.band)} · ${esc(c.channel ?? "")}</td>
            <td class="right">${c.signal ?? "—"} dBm</td><td><span class="badge ${{ excelente: "ok", buena: "ok", regular: "warn", mala: "err" }[c.quality] || ""}">${esc(t(c.quality))}</span></td><td class="right">${Math.round(c.rx_rate || 0)} Mb/s</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
      pane.querySelector("#wf-ref").onclick = (e) => busy(e.currentTarget, () => api.post("/integrations/wifi/refresh")).then(show).catch(() => {});
      on(pane, "click", "[data-wdel]", async (e, b) => { await api.del(`/integrations/wifi/${b.dataset.wdel}`).catch(toastErr); show(); });
      pane.querySelector("#wf-add").onclick = () => modal({ title: t("Añadir controlador Wi-Fi"), body: `<div class="form-grid">
          <label class="field">${t("Tipo")}<select name="type">${Object.entries(s.wifi_types).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
          <label class="field">${t("Nombre")}<input name="name" placeholder="${t("Wi-Fi casa")}"></label>
          <label class="field">URL<input name="url" placeholder="https://192.168.1.2:8443"></label><label class="field">${t("Usuario")}<input name="username" autocomplete="off"></label>
          <label class="field">${t("Contraseña")}<input type="password" name="password" autocomplete="new-password"></label><label class="field">${t("Sitio (UniFi)")}<input name="site" value="default"></label></div>
          <p class="small muted mt">${t("Usa una cuenta de sólo lectura. OpenWrt necesita rpcd con permisos de iwinfo.")}</p>`,
        actions: [{ label: t("Cancelar") }, { label: t("Conectar"), kind: "primary", onClick: async (m) => {
          const g = (n) => $(`[name=${n}]`, m).value.trim();
          await api.post("/integrations/wifi", { type: g("type"), name: g("name"), url: g("url"), username: g("username"), password: g("password"), site: g("site"), enabled: true });
          toast(t("Controlador añadido")); show();
        } }] });
    },
    // ------------------------------------------------------------ portal cautivo
    async captive() {
      const s = await api.get("/captive");
      const c = s.config;
      pane.innerHTML = `<div class="grid g2"><form class="card" id="cf">
        <header><h2>${ico("shield")}${t("Términos y condiciones al conectarse")}</h2>${s.running ? `<span class="badge ok">${t("activo")}</span>` : `<span class="badge">${t("inactivo")}</span>`}</header>
        <p class="muted small">${t("La primera vez que un dispositivo se conecta, su sistema muestra «Iniciar sesión en la red» con esta página. Hasta aceptar, sólo ve el portal. Requiere que los dispositivos usen Bipon como DNS (router o DHCP de Bipon).")}</p>
        ${s.error ? `<div class="alert err mb">${ico("alert")}<div class="small">${esc(s.error)}</div></div>` : ""}
        <div class="opt"><div class="t"><b>${t("Activar portal de bienvenida")}</b><span>${t("Los dispositivos ya conocidos se dan por aceptados al activarlo.")}</span></div>${toggle("enabled", c.enabled)}</div>
        <label class="field mt">${t("Título")}<input name="title" value="${esc(c.title)}"></label>
        <label class="field mt">${t("Texto de los términos")}<textarea name="body" rows="9">${esc(c.body)}</textarea><small>${t("Separa párrafos con una línea en blanco.")}</small></label>
        <div class="form-grid mt">
          <label class="field">${t("Texto de la casilla")}<input name="checkbox_label" value="${esc(c.checkbox_label)}"></label>
          <label class="field">${t("Texto del botón")}<input name="accept_label" value="${esc(c.accept_label)}"></label>
          <label class="field">${t("Mensaje tras aceptar")}<input name="success_text" value="${esc(c.success_text)}"></label>
          <label class="field">${t("Color")}<input type="color" name="color" value="${esc(c.color)}" style="height:38px;padding:3px"></label>
          <label class="field">${t("Logo (URL https)")}<input name="logo_url" value="${esc(c.logo_url)}"></label>
          <label class="field">${t("Volver a pedir cada (días, 0 = nunca)")}<input type="number" name="expiry_days" min="0" value="${c.expiry_days}"></label>
          <label class="field">${t("Puerto del portal")}<input type="number" name="port" value="${c.port}"></label></div>
        ${opt2("ask_name", "Pedir nombre", c.ask_name)}${opt2("ask_email", "Pedir email", c.ask_email)}
        <div class="row mt"><button class="btn primary">${ico("check")}${t("Guardar")}</button><a class="btn" href="/api/captive/preview" target="_blank">${ico("external")}${t("Vista previa")}</a></div></form>
        <div class="col">
          ${s.weak_dns ? `<div class="alert warn">${ico("alert")}<div class="small">${t("Con el modo automático los equipos también pueden usar el DNS del router y saltarse el portal. Para que sea obligatorio, pon Bipon como único DNS en el router o usa el DHCP de Bipon.")}</div></div>` : ""}
          ${c.enabled && !s.firewall ? `<div class="alert info">${ico("info")}<div class="small">${t("El portal funciona por DNS: al volver a pedir los términos, las conexiones ya abiertas del dispositivo pueden seguir unos minutos. Con el modo router de Bipon el corte es inmediato.")}</div></div>` : ""}
          <div class="card"><header><h3>${ico("devices")}${t("Pendientes de aceptar")} (${s.pending.length})</h3>
            <button class="btn sm" id="cap-scan">${ico("refresh")}${t("Buscar dispositivos nuevos")}</button></header>
            <p class="small muted mb">${t("Se actualiza solo cada pocos segundos.")}</p>
            ${s.pending.length ? s.pending.map((p) => `<div class="row between small" style="padding:6px 0"><span class="grow truncate"><b>${esc(p.name || p.ip)}</b><span class="muted"> · ${esc(p.ip)}${p.brand ? " · " + esc(p.brand) : ""}</span></span>
              <button class="btn sm" data-allow="${esc(p.mac)}">${ico("check")}${t("Permitir")}</button><button class="btn sm ghost" data-allow="${esc(p.mac)}" data-trust="1" title="${t("Permitir y registrar")}">${ico("star")}</button></div>`).join("")
              : `<p class="muted small">${t("Ninguno")}</p>`}</div>
          <div class="card"><header><h3>${ico("star")}${t("Dispositivos registrados")} (${s.registered.length})</h3></header>
            <p class="small muted mb">${t("Nunca se les vuelve a pedir aceptar: ni al caducar ni al pedir a todos de nuevo.")}</p>
            ${s.registered.length ? `<div class="table-wrap"><table><tbody>${s.registered.map((a) => capRow(a, true)).join("")}</tbody></table></div>` : `<p class="muted small">${t("Ninguno todavía. Pulsa ☆ en una aceptación para registrarlo.")}</p>`}</div>
          <div class="card"><header><h3>${ico("check")}${t("Aceptaciones")} (${s.accepts.length})</h3><button class="btn sm ghost" id="reset-all">${t("Pedir a todos de nuevo")}</button></header>
          <div class="table-wrap"><table><tbody>${s.accepts.slice(0, 150).map((a) => capRow(a, false)).join("") || `<tr><td class="muted">${t("Nadie todavía")}</td></tr>`}</tbody></table></div></div></div></div>`;
      const form = pane.querySelector("#cf");
      form.onsubmit = (e) => {
        e.preventDefault();
        const body = {};
        for (const k of ["title", "body", "checkbox_label", "accept_label", "success_text", "color", "logo_url"]) body[k] = form[k].value;
        for (const k of ["enabled", "ask_name", "ask_email"]) body[k] = form[k].checked;
        body.expiry_days = Number(form.expiry_days.value) || 0;
        body.port = Number(form.port.value) || 80;
        busy(form.querySelector(".primary"), () => api.put("/captive", body)).then(() => { toast(t("Portal guardado")); setTimeout(show, 2000); }).catch(() => {});
      };
      pane.querySelector("#reset-all").onclick = async () => { if (confirm(t("¿Pedir a todos los dispositivos que acepten de nuevo?"))) { await api.del("/captive/accepts").catch(toastErr); show(); } };
      on(pane, "click", "[data-rst]", async (e, b) => { await api.del(`/captive/accepts?mac=${encodeURIComponent(b.dataset.rst)}`).then(() => toast(t("Se le pedirá aceptar de nuevo"))).catch(toastErr); show(); });
      on(pane, "click", "[data-allow]", async (e, b) => { await busy(b, () => api.post("/captive/allow", { mac: b.dataset.allow, trusted: !!b.dataset.trust })).then(() => toast(t("Dispositivo permitido"))).catch(toastErr); show(); });
      on(pane, "click", "[data-trust-mac]", async (e, b) => { await api.post("/captive/trust", { mac: b.dataset.trustMac, trusted: b.dataset.on !== "1" }).catch(toastErr); show(); });
      pane.querySelector("#cap-scan").onclick = (e) => busy(e.currentTarget, () => api.post("/devices/scan")).then(() => setTimeout(show, 2500)).catch(() => {});
      // refresco automático mientras esta pestaña esté abierta y no se esté editando el formulario
      clearInterval(capTimer);
      const sig = (x) => JSON.stringify([x.pending, x.accepts.map((a) => [a.mac, a.ts, a.online]), x.registered.map((a) => [a.mac, a.online])]);
      const mine = sig(s);
      capTimer = setInterval(async () => {
        if (!document.body.contains(form)) return clearInterval(capTimer);
        if (form.contains(document.activeElement)) return;
        const n = await api.get("/captive").catch(() => null);
        if (n && sig(n) !== mine) show();  // sólo se redibuja si algo cambió
      }, 5000);
    },
    // ------------------------------------------------------------ acceso local/externo
    async access() {
      const s = await api.get("/access");
      const c = s.config;
      const modeSel = (k) => `<select data-mode="${k}">${[["local", "Sólo red local"], ["local_vpn", "Red local + VPN (recomendado)"], ["custom", "Red local + lista propia"], ["any", "Cualquier origen (no recomendado)"]]
        .map(([v, l]) => `<option value="${v}" ${c[k] === v ? "selected" : ""}>${t(l)}</option>`).join("")}</select>`;
      pane.innerHTML = `<div class="grid g2">
        <div class="card"><header><h2>${ico("lock")}${t("Panel web")}</h2></header>
          <p class="muted small mb">${t("Desde dónde se puede abrir este panel. Tu IP actual:")} <b class="mono">${esc(s.your_ip || "?")}</b></p>${modeSel("web")}
          <label class="field mt">${t("IPs o redes permitidas además (una por línea)")}<textarea id="web_allow" rows="3" placeholder="203.0.113.10/32">${esc(c.web_allow.join("\n"))}</textarea></label></div>
        <div class="card"><header><h2>${ico("globe")}${t("Servidor DNS")}</h2></header>
          <p class="muted small mb">${t("Responder sólo a tu red evita que Bipon se use desde Internet como «resolver abierto» para ataques.")}</p>${modeSel("dns")}
          <label class="field mt">${t("IPs o redes permitidas además (una por línea)")}<textarea id="dns_allow" rows="3">${esc(c.dns_allow.join("\n"))}</textarea></label></div></div>
        <div class="row mt"><button class="btn primary" id="ac-save">${ico("check")}${t("Guardar")}</button></div>
        <div class="card mt"><header><h3>${ico("alert")}${t("Intentos externos rechazados")}</h3></header>${s.denied.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Origen")}</th><th>${t("Servicio")}</th><th>${t("Último")}</th></tr></thead><tbody>
          ${s.denied.map((d) => `<tr><td class="mono">${esc(d.ip)}</td><td>${d.target === "web" ? t("Panel") : "DNS"}</td><td>${ago(d.last)}</td></tr>`).join("")}</tbody></table></div>` : empty(t("Ningún intento externo"), "shield")}</div>`;
      pane.querySelector("#ac-save").onclick = (e) => busy(e.currentTarget, async () => {
        const r = await api.put("/access", { web: pane.querySelector('[data-mode="web"]').value, dns: pane.querySelector('[data-mode="dns"]').value,
          web_allow: pane.querySelector("#web_allow").value.split(/\s+/).filter(Boolean), dns_allow: pane.querySelector("#dns_allow").value.split(/\s+/).filter(Boolean) });
        toast(r.warning || t("Acceso actualizado"), r.warning ? "warn" : "ok", 7000); show();
      }).catch(() => {});
    },
  };
  show();
}

const f = (k, l, v, type = "text") => `<label class="field">${t(l)}<input name="${k}" type="${type}" value="${esc(v ?? "")}"></label>`;
const opt2 = (k, l, v) => `<div class="opt"><div class="t"><b>${t(l)}</b></div>${toggle(k, v)}</div>`;
