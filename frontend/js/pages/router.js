import { api, esc, ico, ago, toast, toastErr, busy, toggle, empty, on, confirmDialog, $, $$, bytes, duration } from "../lib.js";
import { t } from "../i18n.js";
import { brandIcon } from "../brands.js";

export const title = "Router y firewall";
export const subtitle = "Controla tu router, el firewall, las zonas y la velocidad de cada dispositivo";

const rate = (bps) => bps == null ? "—" : bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} Mb/s` : `${Math.round(bps / 1e3)} kb/s`;

export async function render(el) {
  let tab = sessionStorage.getItem("np-rt-tab") || "control";
  el.innerHTML = `<div class="tabs mb"><button data-tab="gw">${ico("zap")}${t("Bipon como puerta de enlace")}</button><button data-tab="control">${ico("router")}${t("Mi router")}</button><button data-tab="mode">${ico("shield")}${t("Modo router y firewall")}</button>
    <button data-tab="speed">${ico("gauge")}${t("Velocidad por dispositivo")}</button><button data-tab="traffic">${ico("activity")}${t("Consumo de datos")}</button></div><div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  let timer;
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; try { sessionStorage.setItem("np-rt-tab", tab); } catch {} show(); });
  async function show() {
    clearInterval(timer);
    $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const n = pane.cloneNode(false); pane.replaceWith(n); pane = n;
    pane.innerHTML = `<div class="skeleton" style="height:240px"></div>`;
    try { await V[tab](); } catch (e) { pane.innerHTML = `<div class="alert err">${ico("alert")}<div>${esc(e.message)}</div></div>`; }
  }

  const V = {
    // ------------------------------------------------------------ asistente de puerta de enlace (un solo cable)
    async gw() {
      const st = await api.get("/gateway");
      if (st.active) {
        const tk = st.takeover || {};
        pane.innerHTML = `<div class="card"><header><h2>${ico("check")}${t("Bipon es la puerta de enlace de tu red")}</h2><span class="badge ok">${t("activo")}</span></header>
          <div class="grid g3 mt">
            <div class="card stat"><div class="ic tint-green">${ico("devices")}</div><div><div class="v">${tk.bipon ?? "—"} / ${tk.total ?? "—"}</div><div class="l">${t("equipos con IP de Bipon")}</div></div></div>
            <div class="card stat"><div class="ic tint-blue">${ico("activity")}</div><div><div class="v">${st.through}</div><div class="l">${t("equipos con tráfico pasando ahora por Bipon")}</div></div></div>
            <div class="card stat"><div class="ic tint-violet">${ico("shield")}</div><div><div class="v">${t("Total")}</div><div class="l">${t("límites, pausas y firewall por equipo")}</div></div></div></div>
          ${tk.pending?.length ? `<p class="small muted mt">${t("Aún con la puerta de enlace del router (pasarán al reconectar el Wi-Fi o al renovar su IP)")}: ${tk.pending.slice(0, 10).map((x) => esc(x.name)).join(", ")}</p>` : ""}
          <h4 class="mt mb">${t("Para juegos (NAT abierto)")}</h4>
          <p class="small muted">${t("En tu router Huawei pon la IP de Bipon en «DMZ» y crea aquí, en «Modo router y firewall», la redirección de puertos de tu consola. Así el NAT de tus juegos queda abierto.")}</p>
          <div class="row mt"><button class="btn danger" id="gw-off">${ico("x")}${t("Dejar de ser la puerta de enlace")}</button></div></div>`;
        pane.querySelector("#gw-off").onclick = async (e) => {
          if (!(await confirmDialog(t("Desactivar"), t("Los equipos volverán a usar el router del proveedor al renovar su IP (máx. 30 min). ¿Continuar?")))) return;
          busy(e.currentTarget, () => api.post("/gateway/disable")).then(() => { toast(t("Desactivado")); show(); }).catch(() => {});
        };
        return;
      }
      const pre = await api.get("/gateway/precheck");
      const ICON = { ok: ["ok", "check"], warn: ["warn", "alert"], fail: ["err", "x"] };
      pane.innerHTML = `<div class="card mb"><header><h2>${ico("zap")}${t("Bipon como puerta de enlace")}</h2></header>
          <p class="muted small">${t("Así Bipon controla TODO el tráfico de la casa (cable y Wi-Fi) sin tocar la configuración del proveedor: el Huawei sigue siendo módem, Wi-Fi y switch, y Bipon pasa a ser la puerta de enlace que reparte su DHCP. Con eso funcionan de verdad los límites de velocidad, las pausas, el corte de intrusos y «Proteger mi partida».")}</p>
          <pre class="small mt" style="line-height:1.4">Internet ── Huawei (módem + Wi-Fi + switch)
                  │
     tus equipos ─┴─ Bipon ${esc(pre.self_ip || "")} (puerta de enlace)</pre></div>
        <div class="card mb"><header><h3>${ico("list")}${t("1. Comprobaciones")}</h3><button class="btn sm" id="gw-re">${ico("refresh")}${t("Repetir")}</button></header>
          <div class="col">${pre.checks.map((c) => `<div class="row rec" style="flex-wrap:nowrap;align-items:flex-start"><span class="badge ${ICON[c.status][0]}">${ico(ICON[c.status][1])}</span><div class="grow"><b>${esc(t(c.title))}</b><div class="small muted">${esc(t(c.detail))}</div></div></div>`).join("")}</div></div>
        <div class="card mb"><header><h3>${ico("shield")}${t("2. Activar el reenvío (con seguridad)")}</h3>${st.routing ? `<span class="badge ok">${t("hecho")}</span>` : ""}</header>
          <p class="small muted mb">${t("Se activa el firewall de Bipon. Si en 60 s no confirmas (por ejemplo porque perdiste la conexión), se deshace solo.")}</p>
          <div id="gw-step1">${st.routing && !st.pending ? `<p class="small">${ico("check")} ${t("Reenvío activo y confirmado.")}</p>` : `<button class="btn primary" id="gw-route" ${pre.ready ? "" : "disabled"}>${ico("zap")}${t("Activar")}</button>`}</div></div>
        <div class="card"><header><h3>${ico("server")}${t("3. Repartir Bipon como puerta de enlace")}</h3></header>
          <p class="small muted mb">${t("El DHCP de Bipon (principal, con el del router de respaldo) entrega «puerta de enlace = Bipon». Los equipos se pasan solos al reconectar el Wi-Fi o al renovar su IP (concesión de 30 min). Si Bipon se apaga, vuelven al router del proveedor.")}</p>
          <button class="btn primary" id="gw-dhcp" ${st.routing && !st.pending ? "" : "disabled"}>${ico("check")}${t("Activar y terminar")}</button></div>`;
      pane.querySelector("#gw-re").onclick = () => show();
      const r1 = pane.querySelector("#gw-route");
      if (r1) r1.onclick = (e) => busy(e.currentTarget, () => api.post("/gateway/routing")).then((r) => {
        if (!r.ok) { toast(r.message, "error", 8000); return; }
        const box = pane.querySelector("#gw-step1");
        let left = 60;
        box.innerHTML = `<div class="alert warn">${ico("alert")}<div class="small"><b>${t("¿Sigues viendo esta página?")}</b> ${t("Confirma para mantener los cambios.")} <span id="gw-left">${left}</span> s</div></div>
          <button class="btn primary mt" id="gw-ok">${ico("check")}${t("Confirmar")}</button>`;
        const tmr = setInterval(() => { left -= 1; const l = pane.querySelector("#gw-left"); if (l) l.textContent = left; if (left <= 0) { clearInterval(tmr); toast(t("No se confirmó: cambios revertidos"), "info"); show(); } }, 1000);
        pane.querySelector("#gw-ok").onclick = (ev) => busy(ev.currentTarget, () => api.post("/router/confirm")).then(() => { clearInterval(tmr); toast(t("Confirmado")); show(); }).catch(() => {});
      }).catch(() => {});
      pane.querySelector("#gw-dhcp").onclick = (e) => busy(e.currentTarget, () => api.post("/gateway/dhcp")).then(() => { toast(t("¡Listo! Bipon es la puerta de enlace")); show(); }).catch(() => {});
    },
    // ------------------------------------------------------------ control del router
    async control() {
      const s = await api.get("/routerctl");
      const c = s.config;
      const st = s.status;
      pane.innerHTML = `<div class="grid g3"><form class="card" id="rf"><header><h3>${ico("key")}${t("Acceso al router")}</h3></header>
          <p class="muted small mb">${t("Bipon sólo cambia ajustes seguros de tu red local (DNS del DHCP, canal Wi-Fi, reinicio, copia de seguridad). Nunca toca la conexión del proveedor (WAN, PPPoE, VoIP, TV).")}</p>
          <label class="field">${t("Tipo de router")}<select name="type"><option value="">—</option>${Object.entries(s.drivers).map(([k, v]) => `<option value="${k}" ${c.type === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          <label class="field mt">${t("Dirección")}<input name="url" value="${esc(c.url || (s.gateway ? `http://${s.gateway}` : ""))}"></label>
          <label class="field mt">${t("Usuario")}<input name="username" value="${esc(c.username || "")}" autocomplete="off"></label>
          <label class="field mt">${t("Contraseña")}<input type="password" name="password" value="${esc(c.password || "")}" autocomplete="new-password"></label>
          <button class="btn primary mt">${ico("link")}${t("Conectar")}</button>
          <p class="small muted mt">${t("¿Router del proveedor no compatible? Usa")} <a href="${s.gateway ? `http://${esc(s.gateway)}` : "#"}" target="_blank" rel="noopener">${t("su panel")}</a> ${t("y NAT y CGNAT → UPnP para los puertos.")}</p></form>
        <div class="card span2"><header><h3>${ico("router")}${t("Estado")}</h3>${st ? `<button class="btn sm" id="bk">${ico("download")}${t("Copia de seguridad")}</button><button class="btn sm danger" id="rb">${ico("power")}${t("Reiniciar router")}</button>` : ""}</header>
          ${s.error ? `<div class="alert err">${ico("alert")}<div class="small">${esc(s.error)}</div></div>` : !st ? empty(t("Conecta tu router para controlarlo desde aquí."), "router") : `
          <dl class="kv mb"><dt>${t("Modelo")}</dt><dd>${esc(st.model || "—")}</dd><dt>Firmware</dt><dd>${esc(st.firmware || "—")}</dd><dt>${t("Encendido")}</dt><dd>${st.uptime ? duration(Number(st.uptime) || 0) : "—"}</dd>
            <dt>${t("IP pública (WAN)")}</dt><dd>${esc(st.wan_ip || "—")}</dd><dt>${t("DNS que reparte")}</dt><dd class="mono">${esc((st.dhcp_dns || []).join(", ") || t("el del router"))}</dd></dl>
          <div class="row mb"><button class="btn primary" id="set-dns">${ico("globe")}${t("Usar Bipon como DNS de la red")}</button></div>
          ${(st.radios || []).length ? `<h4 class="mb">Wi-Fi</h4><div class="table-wrap"><table><tbody>${st.radios.map((r) => `<tr><td>${esc(r.name || r.id)}</td><td>${esc(r.band || "")}</td><td>${t("canal")} <b>${esc(r.channel ?? "auto")}</b></td>
            <td class="actions"><input class="ch" data-radio="${esc(r.id)}" placeholder="${t("canal")}" style="width:80px;display:inline-block"> <button class="btn sm" data-setch="${esc(r.id)}">${t("Cambiar")}</button></td></tr>`).join("")}</tbody></table></div>` : ""}`}</div></div>
        <div class="grid g2 mt"><div class="card"><header><h3>${ico("refresh")}${t("Cambios realizados (deshacer)")}</h3></header>
          ${s.history.length ? s.history.map((h, i) => `<div class="row between rec mb"><span class="small"><b>${h.op === "dhcp_dns" ? t("DNS del DHCP") : t("Canal Wi-Fi")}</b> · ${ago(h.ts)}<br><span class="muted mono">${esc(JSON.stringify(h.prev))} → ${esc(JSON.stringify(h.new))}</span></span>
            <button class="btn sm" data-undo="${i}">${ico("refresh")}${t("Deshacer")}</button></div>`).join("") : `<p class="muted small">${t("Sin cambios")}</p>`}</div>
          <div class="card"><header><h3>${ico("download")}${t("Copias del router")}</h3></header>${s.backups.length ? s.backups.map((b) => `<div class="row between small"><span class="mono">${esc(b.file)}</span><span class="muted">${bytes(b.size)} · ${ago(b.ts)}</span></div>`).join("") : `<p class="muted small">${t("Ninguna todavía")}</p>`}</div></div>`;
      const form = pane.querySelector("#rf");
      form.onsubmit = (e) => { e.preventDefault(); busy(form.querySelector(".primary"), () => api.put("/routerctl", { type: form.type.value, url: form.url.value.trim(), username: form.username.value, password: form.password.value })).then(() => { toast(t("Router conectado")); show(); }).catch(() => {}); };
      const act = (a, body = {}) => api.post(`/routerctl/${a}`, body);
      pane.querySelector("#set-dns")?.addEventListener("click", (e) => busy(e.currentTarget, () => act("dns")).then(() => { toast(t("El router ya reparte Bipon como DNS")); show(); }).catch(() => {}));
      pane.querySelector("#bk")?.addEventListener("click", (e) => busy(e.currentTarget, () => act("backup")).then((r) => { toast(`${t("Copia guardada")}: ${r.file}`); show(); }).catch(() => {}));
      pane.querySelector("#rb")?.addEventListener("click", async () => { if (await confirmDialog(t("Reiniciar router"), t("Toda la red se quedará sin Internet 1-3 minutos."))) act("reboot").then(() => toast(t("Reiniciando el router…"), "info")).catch(toastErr); });
      on(pane, "click", "[data-setch]", (e, b) => { const v = pane.querySelector(`[data-radio="${CSS.escape(b.dataset.setch)}"]`).value.trim(); if (v) busy(b, () => act("channel", { radio: b.dataset.setch, channel: v })).then(() => { toast(t("Canal cambiado")); show(); }).catch(() => {}); });
      on(pane, "click", "[data-undo]", (e, b) => busy(b, () => act("undo", { index: Number(b.dataset.undo) })).then(() => { toast(t("Cambio deshecho")); show(); }).catch(() => {}));
    },
    // ------------------------------------------------------------ modo router / firewall
    async mode() {
      const s = await api.get("/router");
      const c = s.config;
      const ifs = s.interfaces.map((i) => `<option ${""}>${esc(i.name)}</option>`).join("");
      pane.innerHTML = `${s.pending ? `<div class="alert warn mb">${ico("clock")}<div class="grow"><b>${t("Cambios pendientes de confirmar")}</b><p class="small">${t("Si no confirmas, se revertirán automáticamente para que no pierdas el acceso.")}</p></div><button class="btn primary" id="confirm">${ico("check")}${t("Confirmar cambios")}</button></div>` : ""}
        <div class="alert info mb">${ico("info")}<div class="small">${t("Sólo tiene efecto si el tráfico de la red pasa por este equipo (Bipon hace de router: dos interfaces o VLAN, p. ej. en Proxmox). Bipon sólo gestiona su propia tabla de firewall «bipon».")}</div></div>
        <form class="grid g2" id="mf"><div class="card"><header><h3>${ico("router")}${t("Modo router")}</h3>${s.active ? `<span class="badge ok">${t("reglas activas")}</span>` : ""}</header>
          <div class="opt"><div class="t"><b>${t("Activar modo router")}</b></div>${toggle("enabled", c.enabled)}</div>
          <div class="form-grid mt"><label class="field">${t("Interfaz WAN (Internet)")}<input name="wan" value="${esc(c.wan)}" list="ifs"></label><label class="field">${t("Interfaz LAN")}<input name="lan" value="${esc(c.lan)}" list="ifs"></label></div>
          <datalist id="ifs">${ifs}</datalist>
          ${o("nat", "NAT (compartir Internet)", "", c.nat)}${o("force_dns", "Forzar DNS de Bipon", "Redirige a Bipon el DNS de los equipos con DNS fijo (8.8.8.8…): nadie se salta el filtro.", c.force_dns)}
          ${o("block_devices", "Bloqueo real de Internet", "Los dispositivos pausados o en horario sin Internet se bloquean también en el firewall (no sólo por DNS).", c.block_devices)}
          ${o("accounting", "Medir consumo por dispositivo", "Velocidad en vivo y datos consumidos de cada equipo.", c.accounting)}</div>
          <div class="card"><header><h3>${ico("network")}${t("Zonas y VLAN")}</h3><button type="button" class="btn sm" id="add-z">${ico("plus")}${t("Zona")}</button></header>
          <p class="muted small mb">${t("Separa IoT, invitados o empleados en su propia red. «Aislada» impide que acceda a tus otros equipos.")}</p><div id="zones" class="col"></div>
          <h4 class="mt mb">${t("Redirección de puertos")}</h4><div id="fwds" class="col"></div><button type="button" class="btn sm mt" id="add-f">${ico("plus")}${t("Redirección")}</button></div></form>
        <div class="row mt"><button class="btn primary" id="apply">${ico("check")}${t("Aplicar (con reversión automática a los 60 s)")}</button><a class="btn" href="/api/router/preview" target="_blank">${ico("list")}${t("Ver reglas")}</a></div>
        ${s.last_error ? `<div class="alert err mt">${ico("alert")}<div class="small mono">${esc(s.last_error)}</div></div>` : ""}`;
      const zones = [...c.zones], fwds = [...c.port_forwards];
      const drawZ = () => { pane.querySelector("#zones").innerHTML = zones.map((z, i) => `<div class="rec"><div class="form-grid">
        <label class="field">${t("Nombre")}<input data-z="${i}" data-k="name" value="${esc(z.name)}"></label><label class="field">${t("Dirección (IP/máscara)")}<input data-z="${i}" data-k="address" value="${esc(z.address)}" placeholder="192.168.20.1/24"></label>
        <label class="field">VLAN<input data-z="${i}" data-k="vlan" value="${esc(z.vlan ?? "")}" placeholder="20"></label><label class="field">${t("Interfaz física")}<input data-z="${i}" data-k="parent" value="${esc(z.parent || "")}" placeholder="${esc(c.lan)}"></label></div>
        <div class="row mt">${toggle("iso", z.isolate !== false, `data-z="${i}" data-k="isolate"`)} ${t("Aislada")} ${toggle("inet", z.internet !== false, `data-z="${i}" data-k="internet"`)} ${t("Con Internet")}<span class="grow"></span><button type="button" class="icon-btn" data-zdel="${i}">${ico("trash")}</button></div></div>`).join("") || `<p class="small muted">${t("Sin zonas")}</p>`; };
      const drawF = () => { pane.querySelector("#fwds").innerHTML = fwds.map((x, i) => `<div class="inline-form"><select data-f="${i}" data-k="proto" style="max-width:80px"><option ${x.proto === "tcp" ? "selected" : ""}>tcp</option><option ${x.proto === "udp" ? "selected" : ""}>udp</option></select>
        <input data-f="${i}" data-k="port" value="${esc(x.port)}" placeholder="${t("puerto")}" style="max-width:90px"><input data-f="${i}" data-k="to_ip" value="${esc(x.to_ip)}" placeholder="${t("IP destino")}"><input data-f="${i}" data-k="to_port" value="${esc(x.to_port || "")}" placeholder="${t("puerto destino")}" style="max-width:110px">
        <button type="button" class="icon-btn" data-fdel="${i}">${ico("trash")}</button></div>`).join("") || `<p class="small muted">${t("Sin redirecciones")}</p>`; };
      drawZ(); drawF();
      on(pane, "input", "[data-z]", (e, i) => { zones[i.dataset.z][i.dataset.k] = i.type === "checkbox" ? i.checked : i.value.trim(); });
      on(pane, "change", "[data-z][type=checkbox]", (e, i) => { zones[i.dataset.z][i.dataset.k] = i.checked; });
      on(pane, "input", "[data-f]", (e, i) => { fwds[i.dataset.f][i.dataset.k] = i.value.trim(); });
      on(pane, "click", "[data-zdel]", (e, b) => { zones.splice(Number(b.dataset.zdel), 1); drawZ(); });
      on(pane, "click", "[data-fdel]", (e, b) => { fwds.splice(Number(b.dataset.fdel), 1); drawF(); });
      pane.querySelector("#add-z").onclick = () => { zones.push({ name: "iot", address: "192.168.20.1/24", vlan: 20, parent: c.lan, isolate: true, internet: true }); drawZ(); };
      pane.querySelector("#add-f").onclick = () => { fwds.push({ proto: "tcp", port: "", to_ip: "", to_port: "" }); drawF(); };
      pane.querySelector("#confirm")?.addEventListener("click", () => api.post("/router/confirm").then(() => { toast(t("Cambios confirmados")); show(); }).catch(toastErr));
      pane.querySelector("#apply").onclick = (e) => {
        const f = pane.querySelector("#mf");
        const body = { enabled: f.enabled.checked, wan: f.wan.value.trim(), lan: f.lan.value.trim(), nat: f.nat.checked, force_dns: f.force_dns.checked,
          block_devices: f.block_devices.checked, accounting: f.accounting.checked, zones: zones.map((z) => ({ ...z, vlan: z.vlan === "" ? null : z.vlan })),
          port_forwards: fwds.filter((x) => x.port && x.to_ip).map((x) => ({ ...x, port: Number(x.port), to_port: x.to_port ? Number(x.to_port) : undefined })) };
        busy(e.currentTarget, () => api.put("/router", body)).then((r) => { toast(r.message, r.ok ? "ok" : "error", 8000); show(); }).catch(() => {});
      };
    },
    // ------------------------------------------------------------ velocidad
    async speed() {
      const [r, d, lv, sc] = await Promise.all([api.get("/router"), api.get("/devices"), api.get("/devices/rates").catch(() => ({ rates: {} })),
        api.get("/speedctl").catch(() => ({ methods: {}, labels: {} }))]);
      const MSHORT = { proxmox: "Proxmox", router: "Bipon", mikrotik: "MikroTik", openwrt: "OpenWrt" };
      const meth = (mac) => { const m = sc.methods[mac]; return m ? `<span class="badge ok" title="${esc(t(sc.labels[m] || ""))}">${MSHORT[m]}</span>` : `<span class="badge" title="${t("Sin método disponible")}">—</span>`; };
      const anyMethod = Object.values(sc.methods || {}).some(Boolean);
      const SRC = { router: "router", proxmox: "Proxmox", local: "Bipon" };
      const cell = (x, k, arrow) => {
        const v = lv.rates[x.mac];
        if (v) return `${arrow}${rate(v[k])}`;
        return `<span class="muted" title="${t("Este equipo no pasa por Bipon: activa el modo router para medirlo")}">${arrow}—</span>`;
      };
      const sh = r.config.shaping || {};
      const draw = (devs) => devs.map((x) => `<tr data-mac="${esc(x.mac)}"><td>${brandIcon(x.brand, x.kind)}</td><td><b>${esc(x.name || x.friendly || x.hostname || x.ip || x.mac)}</b><div class="small muted mono">${esc(x.ip || "")}</div></td>
        <td class="right rate"><span class="d">${cell(x, "rx_bps", "↓")}</span></td><td class="right rate"><span class="u">${cell(x, "tx_bps", "↑")}</span></td>
        <td class="small muted src">${lv.rates[x.mac] ? esc(SRC[lv.rates[x.mac].source] || "") : ""}</td>
        <td>${meth(x.mac)}</td>
        <td><input type="number" step="0.5" min="0" data-ld="${esc(x.mac)}" value="${x.limit_down || ""}" placeholder="∞" style="width:90px"></td>
        <td><input type="number" step="0.5" min="0" data-lu="${esc(x.mac)}" value="${x.limit_up || ""}" placeholder="∞" style="width:90px"></td>
        <td class="actions nowrap"><select data-preset="${esc(x.mac)}" title="${t("Límite rápido")}" style="width:auto"><option value="">${t("Rápido…")}</option>${[1, 3, 5, 10, 25, 50].map((v) => `<option value="${v}">${v} Mbps</option>`).join("")}<option value="0">${t("Sin límite")}</option></select>
          <button class="btn sm" data-lim="${esc(x.mac)}">${t("Aplicar")}</button></td></tr>`).join("");
      pane.innerHTML = `<div class="card mb"><header><h2>${ico("gauge")}${t("Controlador de velocidad")}</h2></header>
        <p class="muted small">${t("Pon a cada dispositivo una velocidad máxima de bajada y subida. Bipon la aplica donde pasa su tráfico, sin necesidad de ser el router:")}</p>
        <div class="grid g4 mt small">
          <div class="rec"><b>${ico("server")} Proxmox</b><div class="muted">${sc.proxmox ? t("VMs y LXC: activo") : t("Conecta Proxmox en Integraciones")}</div></div>
          <div class="rec"><b>${ico("router")} MikroTik</b><div class="muted">${sc.router_type === "mikrotik" ? t("Conectado: colas por dispositivo") : t("Conéctalo en «Mi router»")}</div></div>
          <div class="rec"><b>${ico("router")} OpenWrt</b><div class="muted">${sc.router_type === "openwrt" ? t("Conectado (requiere nft-qos)") : t("Conéctalo en «Mi router»")}</div></div>
          <div class="rec"><b>${ico("shield")} ${t("Modo router")}</b><div class="muted">${sc.router_mode ? t("Activo: todos los dispositivos") : t("Opcional (abajo)")}</div></div></div>
        ${anyMethod ? "" : `<div class="alert warn mt">${ico("alert")}<div class="small">${t("Tu router actual no permite controlar la velocidad desde fuera (los routers del proveedor no tienen API). Los límites se guardan y se aplicarán en cuanto conectes un MikroTik/OpenWrt o actives el modo router. Las VMs de Proxmox sí se limitan ya.")}</div></div>`}
        <details class="mt"><summary class="small">${t("Modo router (opcional): Bipon limita a todos los dispositivos con colas HTB + fq_codel")}</summary>
        <form class="form-grid mt" id="shf"><div class="opt" style="border:0"><div class="t"><b>${t("Activar límites")}</b></div>${toggle("enabled", sh.enabled)}</div>
          <label class="field">${t("Interfaz LAN")}<input name="iface" value="${esc(sh.iface || r.config.lan || "")}"></label>
          <label class="field">${t("Bajada total de tu línea (Mbps)")}<input type="number" name="down_mbit" value="${sh.down_mbit || ""}"></label>
          <label class="field">${t("Subida total de tu línea (Mbps)")}<input type="number" name="up_mbit" value="${sh.up_mbit || ""}"></label>
          <div class="row" style="align-items:flex-end"><button class="btn primary">${ico("check")}${t("Guardar")}</button></div></form></details></div>
        <div class="grid g2 mb">
          <div class="card stat"><div class="ic tint-blue">${ico("globe")}</div><div class="truncate"><div class="v" id="line-v">${lv.line ? `↓${rate(lv.line.rx_bps)} · ↑${rate(lv.line.tx_bps)}` : "—"}</div>
            <div class="l">${t("Internet en total (router por UPnP)")}${lv.line ? "" : ` · <span class="small">${t("tu router no publica los contadores por UPnP")}</span>`}</div></div></div>
          ${lv.router_mode ? `<div class="alert ok">${ico("check")}<div class="small">${t("Modo router activo: se mide la velocidad de todos los dispositivos.")}</div></div>`
            : `<div class="alert info">${ico("info")}<div class="small"><b>${t("¿Por qué algunos equipos muestran «—»?")}</b> ${t("Bipon sólo puede medir el tráfico que pasa por él. Ahora mide en vivo: el propio Bipon")}${lv.proxmox ? t(", las VMs y contenedores de Proxmox") : ""}${t(" y el total de la línea. Para medir cada móvil, TV o PC, activa el modo router (Bipon como puerta de enlace) en «Modo router y firewall».")}</div></div>`}
        </div>
        <div class="card"><header><h3>${ico("devices")}${t("Dispositivos")} <span class="live-dot"></span></h3></header><div class="table-wrap"><table><thead><tr><th></th><th>${t("Dispositivo")}</th><th class="right">${t("Bajada ahora")}</th><th class="right">${t("Subida ahora")}</th><th>${t("Fuente")}</th><th>${t("Control")}</th><th>${t("Límite ↓")}</th><th>${t("Límite ↑")}</th><th></th></tr></thead>
        <tbody id="spd">${draw(d.devices.filter((x) => x.ip))}</tbody></table></div></div>`;
      const shf = pane.querySelector("#shf");
      shf.onsubmit = (e) => { e.preventDefault(); busy(shf.querySelector(".primary"), () => api.post("/router/shaping", { enabled: shf.enabled.checked, iface: shf.iface.value.trim(), down_mbit: Number(shf.down_mbit.value) || null, up_mbit: Number(shf.up_mbit.value) || null })).then((x) => toast(x.message, x.ok ? "ok" : "error", 7000)).catch(() => {}); };
      on(pane, "click", "[data-lim]", (e, b) => {
        const mac = b.dataset.lim;
        busy(b, () => api.patch(`/devices/${encodeURIComponent(mac)}/limit`, { limit_down: pane.querySelector(`[data-ld="${CSS.escape(mac)}"]`).value || null, limit_up: pane.querySelector(`[data-lu="${CSS.escape(mac)}"]`).value || null }))
          .then((x) => toast(x.message || t("Límite aplicado"), x.ok === false ? "error" : x.applied ? "ok" : "info", 7000)).catch(() => {});
      });
      on(pane, "change", "[data-preset]", (e, sel) => {
        if (sel.value === "") return;
        const mac = sel.dataset.preset;
        const v = sel.value === "0" ? "" : sel.value;
        pane.querySelector(`[data-ld="${CSS.escape(mac)}"]`).value = v;
        pane.querySelector(`[data-lu="${CSS.escape(mac)}"]`).value = v ? Math.max(1, Math.round(v / 2)) : "";
        sel.value = "";
        pane.querySelector(`[data-lim="${CSS.escape(mac)}"]`).click();
      });
      timer = setInterval(async () => {
        const n = await api.get("/devices/rates").catch(() => null);
        if (!n) return;
        lv.rates = n.rates;
        for (const [mac, v] of Object.entries(n.rates)) {
          const row = pane.querySelector(`tr[data-mac="${CSS.escape(mac)}"]`);
          if (!row) continue;
          row.querySelector(".d").textContent = `↓${rate(v.rx_bps)}`;
          row.querySelector(".u").textContent = `↑${rate(v.tx_bps)}`;
          row.querySelector(".src").textContent = SRC[v.source] || "";
        }
        if (n.line) pane.querySelector("#line-v").textContent = `↓${rate(n.line.rx_bps)} · ↑${rate(n.line.tx_bps)}`;
      }, 3000);
    },
    // ------------------------------------------------------------ tráfico
    async traffic() {
      const rows = await api.get("/router/traffic?hours=24");
      const max = rows[0] ? rows[0].rx + rows[0].tx : 1;
      pane.innerHTML = `<div class="card"><header><h2>${ico("activity")}${t("Consumo de datos (24 h)")}</h2></header>
        ${rows.length ? `<div class="col">${rows.map((r) => `<div><div class="row between small"><b>${esc(r.name)}</b><span>↓ ${bytes(r.rx)} · ↑ ${bytes(r.tx)}</span></div><div class="progress mt"><i style="width:${Math.max(2, (r.rx + r.tx) / max * 100)}%"></i></div></div>`).join("")}</div>`
        : empty(t("Sin datos: activa el modo router con «Medir consumo por dispositivo»."), "activity")}</div>`;
    },
  };
  show();
  return () => clearInterval(timer);
}

const o = (k, l, d, v) => `<div class="opt"><div class="t"><b>${t(l)}</b>${d ? `<span>${t(d)}</span>` : ""}</div>${toggle(k, v)}</div>`;
