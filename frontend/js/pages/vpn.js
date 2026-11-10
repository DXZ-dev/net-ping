import { api, esc, ico, bytes, ago, toast, toastErr, busy, modal, on, toggle, download, copy, confirmDialog, empty, fresh, $, $$ } from "../lib.js";

export const title = "VPN";
export const subtitle = "WireGuard, OpenVPN y Tailscale: conéctate a tu red desde cualquier lugar (también con CGNAT)";

export async function render(el) {
  let tab = (location.hash.split("/")[2] || sessionStorage.getItem("np-vpn-tab") || "wireguard");
  el.innerHTML = `<div class="tabs">
      <button data-tab="wireguard">${ico("zap")}WireGuard</button><button data-tab="openvpn">${ico("lock")}OpenVPN</button>
      <button data-tab="tailscale">${ico("network")}Tailscale</button><button data-tab="relay">${ico("globe")}VPS de relevo (CGNAT)</button>
      <button data-tab="guide">${ico("help")}¿Cuál elijo?</button></div>
    <div id="vpn-warn"></div><div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  let timer;
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; try { sessionStorage.setItem("np-vpn-tab", tab); } catch {} show(); });

  async function show() {
    clearInterval(timer);
    $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const ov = await api.get("/vpn");
    pane = fresh(pane);
    el.querySelector("#vpn-warn").innerHTML = !ov.tun || !ov.net_admin ? `<div class="alert warn mb">${ico("alert")}<div><b>Permisos de red limitados</b>
      <p class="small">${!ov.tun ? "No existe /dev/net/tun. " : ""}${!ov.net_admin ? "Falta la capacidad NET_ADMIN. " : ""}En Docker añade <code>cap_add: [NET_ADMIN]</code> y <code>devices: [/dev/net/tun]</code> (ya incluidos en el docker-compose.yml). En Proxmox LXC activa «TUN» en el contenedor.</p></div></div>` : "";
    if (tab === "wireguard") return wireguard(ov.wireguard);
    if (tab === "openvpn") return openvpn(ov.openvpn);
    if (tab === "tailscale") return tailscale(ov.tailscale);
    if (tab === "relay") return relay();
    return guide();
  }
  const refresh = () => show();

  // =============================================================== WireGuard
  function wireguard(w) {
    pane = fresh(pane);
    if (!w.available) { pane.innerHTML = notInstalled("WireGuard", "wireguard-tools"); return; }
    pane.innerHTML = `
      <div class="row mb"><p class="muted grow small">El protocolo VPN más rápido y con menor latencia. Crea un servidor para entrar a tu red desde fuera, o importa un túnel de tu proveedor VPN.</p>
        <button class="btn" id="wg-import">${ico("upload")}Importar túnel</button><button class="btn primary" id="wg-new">${ico("plus")}Nuevo servidor</button></div>
      ${w.servers.length ? w.servers.map(server).join("") : `<div class="card mb">${empty("No hay servidores WireGuard. Crea uno para acceder a tu red (y a su bloqueo de anuncios) desde el móvil o el portátil.", "zap")}</div>`}
      ${w.clients.length ? `<div class="card mt"><header><h2>${ico("upload")}Túneles salientes</h2></header><div class="table-wrap"><table><thead><tr><th>Estado</th><th>Nombre</th><th>Endpoint</th><th>Handshake</th><th>Tráfico</th><th>Auto</th><th></th></tr></thead><tbody>
        ${w.clients.map((c) => `<tr><td>${c.up ? `<span class="badge ${c.online ? "ok" : "warn"}">${c.online ? "conectado" : "sin handshake"}</span>` : `<span class="badge">apagado</span>`}</td>
          <td><b>${esc(c.name)}</b></td><td class="mono small">${esc(c.endpoint || "")}</td><td>${c.handshake ? ago(c.handshake) : "—"}</td>
          <td class="small">↓${bytes(c.rx)} ↑${bytes(c.tx)}</td><td>${c.autostart ? "sí" : "no"}</td>
          <td class="actions">${c.up ? `<button class="btn sm" data-wg="${esc(c.name)}" data-act="down">${ico("power")}Apagar</button>` : `<button class="btn sm success" data-wg="${esc(c.name)}" data-act="up">${ico("play")}Conectar</button>`}
            <button class="icon-btn" data-wgdel="${esc(c.name)}">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div></div>` : ""}`;

    pane.querySelector("#wg-new").onclick = () => modal({ title: "Nuevo servidor WireGuard", body: `<div class="form-grid">
        <label class="field">Nombre de interfaz<input name="name" value="wg0"></label>
        <label class="field">Puerto UDP<input type="number" name="port" value="51820"></label>
        <label class="field">Red del túnel<input name="network" value="10.8.0.0/24"></label>
        <label class="field">Endpoint público<input name="endpoint" placeholder="tu-ip-publica o midominio.duckdns.org"><small>Sin esto no se pueden exportar perfiles.</small></label>
        </div><div class="opt">${""}<div class="t"><b>Túnel completo por defecto</b><span>Todo el tráfico del cliente pasa por casa (bloqueo de anuncios en 4G). Si no, sólo el tráfico a tu LAN.</span></div>${toggle("full", true)}</div>
        <div class="opt"><div class="t"><b>Arrancar automáticamente</b></div>${toggle("auto", true)}</div>`,
      actions: [{ label: "Cancelar" }, { label: "Crear y arrancar", kind: "primary", onClick: async (m) => {
        const body = { name: $("[name=name]", m).value, port: Number($("[name=port]", m).value), network: $("[name=network]", m).value,
          endpoint: $("[name=endpoint]", m).value, full_tunnel: $("[name=full]", m).checked, autostart: $("[name=auto]", m).checked };
        await api.post("/vpn/wireguard/servers", body);
        try { await api.post(`/vpn/wireguard/${body.name}/up`); toast("Servidor creado y activo"); } catch (e) { toast(`Creado, pero no arrancó: ${e.message}`, "warn", 8000); }
        refresh();
      } }] });
    pane.querySelector("#wg-import").onclick = () => modal({ title: "Importar túnel WireGuard", wide: true, body: `
      <p class="muted small mb">Pega el .conf de tu proveedor (Mullvad, Proton, IVPN, tu VPS…). Por seguridad no se permiten PostUp/PreUp.</p>
      <div class="form-grid mb"><label class="field">Nombre<input name="name" value="wg-vpn"></label></div>
      <label class="field">Configuración<textarea name="conf" rows="10" placeholder="[Interface]\nPrivateKey = …\nAddress = …\n\n[Peer]\nPublicKey = …\nEndpoint = …\nAllowedIPs = 0.0.0.0/0"></textarea></label>
      <input type="file" accept=".conf" class="mt" id="wgfile">
      <div class="alert warn mt">${ico("alert")}<div class="small">Si el túnel usa <code>AllowedIPs = 0.0.0.0/0</code>, todo el tráfico del servidor NetPing (incluido el DNS hacia Internet) saldrá por la VPN.</div></div>`,
      actions: [{ label: "Cancelar" }, { label: "Importar", kind: "primary", onClick: async (m) => {
        await api.post("/vpn/wireguard/import", { name: $("[name=name]", m).value, config: $("[name=conf]", m).value });
        toast("Túnel importado"); refresh();
      } }] });
    document.addEventListener("change", function f(e) {
      if (e.target.id !== "wgfile") return;
      const file = e.target.files[0]; if (!file) return;
      file.text().then((t) => { const ta = e.target.closest(".modal").querySelector("[name=conf]"); ta.value = t; const n = e.target.closest(".modal").querySelector("[name=name]"); n.value = file.name.replace(/\.conf$/, "").replace(/[^\w.-]/g, "").slice(0, 15); });
    }, { once: true });

    on(pane, "click", "[data-wg]", (e, b) => busy(b, () => api.post(`/vpn/wireguard/${b.dataset.wg}/${b.dataset.act}`)).then((r) => { toast(b.dataset.act === "up" ? "Interfaz activa" : "Interfaz apagada"); if (r.message) console.info(r.message); refresh(); }).catch(() => {}));
    on(pane, "click", "[data-wgdel]", async (e, b) => { if (await confirmDialog("Eliminar", `¿Eliminar ${b.dataset.wgdel}? Los clientes dejarán de funcionar.`)) { await api.del(`/vpn/wireguard/${b.dataset.wgdel}`).catch(toastErr); refresh(); } });
    on(pane, "click", "[data-addpeer]", (e, b) => addPeer(b.dataset.addpeer));
    on(pane, "click", "[data-peerconf]", async (e, b) => { const [srv, pid] = b.dataset.peerconf.split("|"); try { showConf(await api.get(`/vpn/wireguard/${srv}/peers/${pid}/config`)); } catch (err) { toastErr(err); } });
    on(pane, "change", "[data-peertog]", (e, t) => { const [srv, pid] = t.dataset.peertog.split("|"); api.patch(`/vpn/wireguard/${srv}/peers/${pid}`, { enabled: t.checked }).then(() => toast(t.checked ? "Peer activado" : "Peer desactivado")).catch(toastErr); });
    on(pane, "click", "[data-peerdel]", async (e, b) => { const [srv, pid] = b.dataset.peerdel.split("|"); if (await confirmDialog("Eliminar dispositivo", "Su configuración dejará de funcionar.")) { await api.del(`/vpn/wireguard/${srv}/peers/${pid}`).catch(toastErr); refresh(); } });
    on(pane, "click", "[data-srvedit]", (e, b) => editServer(w.servers.find((s) => s.name === b.dataset.srvedit)));
    timer = setInterval(async () => { if (!document.querySelector(".overlay")) { const ov = await api.get("/vpn").catch(() => null); if (ov && tab === "wireguard") wireguard(ov.wireguard); } }, 8000);
  }

  function server(s) {
    return `<div class="card mb"><header><h2>${ico("server")}${esc(s.name)} ${s.up ? `<span class="badge ok">activo</span>` : `<span class="badge">apagado</span>`}</h2>
      ${s.up ? `<button class="btn sm" data-wg="${esc(s.name)}" data-act="down">${ico("power")}Apagar</button>` : `<button class="btn sm success" data-wg="${esc(s.name)}" data-act="up">${ico("play")}Arrancar</button>`}
      <button class="btn sm" data-srvedit="${esc(s.name)}">${ico("settings")}</button><button class="btn sm primary" data-addpeer="${esc(s.name)}">${ico("plus")}Añadir dispositivo</button>
      <button class="icon-btn" data-wgdel="${esc(s.name)}">${ico("trash")}</button></header>
      <div class="row small muted mb">Puerto UDP ${s.port} · Red ${esc(s.network)} · Endpoint ${esc(s.endpoint || "⚠ sin definir")} · DNS ${esc(s.dns || "—")} · ${s.full_tunnel ? "túnel completo" : "sólo LAN"}</div>
      ${s.peers.length ? `<div class="table-wrap"><table><thead><tr><th>Activo</th><th>Dispositivo</th><th>IP túnel</th><th>Conexión</th><th>Último handshake</th><th>Tráfico</th><th></th></tr></thead><tbody>
      ${s.peers.map((p) => `<tr><td>${toggle("e", p.enabled, `data-peertog="${esc(s.name)}|${p.id}"`)}</td><td><b>${esc(p.name)}</b></td><td class="mono">${esc(p.address)}</td>
        <td>${p.online ? `<span class="badge ok">${ico("check")}conectado</span><div class="small muted mono">${esc(p.endpoint || "")}</div>` : `<span class="badge">desconectado</span>`}</td>
        <td>${p.handshake ? ago(p.handshake) : "nunca"}</td><td class="small">↓${bytes(p.tx)} ↑${bytes(p.rx)}</td>
        <td class="actions"><button class="btn sm" data-peerconf="${esc(s.name)}|${p.id}">${ico("qr")}QR / .conf</button><button class="icon-btn" data-peerdel="${esc(s.name)}|${p.id}">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted small">Sin dispositivos. Pulsa «Añadir dispositivo» y escanea el QR con la app WireGuard.</p>`}</div>`;
  }

  function addPeer(srv) {
    modal({ title: "Añadir dispositivo", body: `<label class="field">Nombre<input name="pname" placeholder="movil-ana"></label>
      <label class="field mt">Tipo de túnel<select name="ft"><option value="">Según servidor</option><option value="1">Completo (todo por casa)</option><option value="0">Dividido (sólo red de casa)</option></select></label>`,
      actions: [{ label: "Cancelar" }, { label: "Crear", kind: "primary", onClick: async (m) => {
        const ft = $("[name=ft]", m).value;
        const r = await api.post(`/vpn/wireguard/${srv}/peers`, { name: $("[name=pname]", m).value, full_tunnel: ft === "" ? null : ft === "1" });
        refresh();
        if (r.config) setTimeout(() => showConf(r.config), 250); else toast("Dispositivo creado. Define el endpoint para exportarlo.", "warn");
      } }] });
  }

  function editServer(s) {
    modal({ title: `Servidor ${s.name}`, body: `<div class="form-grid">
      <label class="field">Endpoint público<input name="endpoint" value="${esc(s.endpoint)}"></label><label class="field">Puerto<input type="number" name="port" value="${s.port}"></label>
      <label class="field">DNS para clientes<input name="dns" value="${esc(s.dns)}"><small>La IP de NetPing = bloqueo de anuncios fuera de casa.</small></label>
      <label class="field">Red LAN<input name="lan_net" value="${esc(s.lan_net)}"></label><label class="field">MTU<input type="number" name="mtu" value="${s.mtu}"></label>
      <label class="field">Keepalive (s)<input type="number" name="keepalive" value="${s.keepalive}"><small>25 s mantiene el túnel vivo tras NAT/CGNAT.</small></label></div>
      <div class="opt"><div class="t"><b>Túnel completo</b></div>${toggle("full_tunnel", s.full_tunnel)}</div><div class="opt"><div class="t"><b>Arranque automático</b></div>${toggle("autostart", s.autostart)}</div>`,
      actions: [{ label: "Cancelar" }, { label: "Guardar", kind: "primary", onClick: async (m) => {
        const b = {}; for (const k of ["endpoint", "dns", "lan_net"]) b[k] = $(`[name=${k}]`, m).value;
        for (const k of ["port", "mtu", "keepalive"]) b[k] = Number($(`[name=${k}]`, m).value);
        for (const k of ["full_tunnel", "autostart"]) b[k] = $(`[name=${k}]`, m).checked;
        await api.patch(`/vpn/wireguard/${s.name}`, b); toast("Guardado"); refresh();
      } }] });
  }

  function showConf(c) {
    modal({ title: c.name, wide: true, body: `<div class="row" style="align-items:flex-start;gap:20px">
      ${c.qr ? `<div class="center"><div class="qr">${c.qr}</div><p class="small muted">Escanéalo con la app WireGuard</p></div>` : ""}
      <div class="grow" style="min-width:260px"><pre>${esc(c.content)}</pre>
      <div class="row mt"><button class="btn" id="dl">${ico("download")}Descargar</button><button class="btn" id="cp">${ico("copy")}Copiar</button></div>
      <p class="small warn mt">${ico("alert")} Contiene la clave privada del dispositivo: no la compartas.</p></div></div>` }).body.querySelector("#dl").onclick = () => download(c.name, c.content);
    $("#cp").onclick = () => copy(c.content);
  }

  // =============================================================== OpenVPN
  function openvpn(o) {
    pane = fresh(pane);
    if (!o.available) { pane.innerHTML = notInstalled("OpenVPN", "openvpn"); return; }
    const s = o.server;
    pane.innerHTML = `
      <div class="grid g3">
        <div class="card span2"><header><h2>${ico("server")}Servidor OpenVPN ${s ? (o.running ? `<span class="badge ok">activo</span>` : `<span class="badge">detenido</span>`) : ""}</h2>
          ${s ? (o.running ? `<button class="btn sm" data-ov="stop">${ico("power")}Detener</button>` : `<button class="btn sm success" data-ov="start">${ico("play")}Iniciar</button>`) : ""}
          <button class="btn sm ${s ? "" : "primary"}" id="ov-init">${ico("settings")}${s ? "Configurar" : "Crear servidor"}</button></header>
          ${s ? `<p class="small muted mb">${esc(s.proto.toUpperCase())} ${s.port} · Red ${esc(s.network)} · Endpoint ${esc(s.endpoint || "⚠ sin definir")} · DNS ${esc(s.dns || "—")} · ${s.full_tunnel ? "túnel completo" : "sólo LAN"} · AES-256-GCM + tls-crypt · certificados EC P-256</p>
            <div class="row mb"><input id="ov-name" placeholder="nombre-del-cliente (sin espacios)" style="max-width:280px"><button class="btn primary" id="ov-add">${ico("plus")}Crear cliente .ovpn</button></div>
            <div class="table-wrap"><table><thead><tr><th>Cliente</th><th>Creado</th><th>Estado</th><th></th></tr></thead><tbody>
            ${o.clients.map((c) => { const con = o.connected.find((x) => x.name === c.name);
              return `<tr><td><b>${esc(c.name)}</b></td><td>${ago(c.created)}</td><td>${c.revoked ? `<span class="badge err">revocado</span>` : con ? `<span class="badge ok">conectado · ${esc(con.virtual)}</span><div class="small muted">${esc(con.real)} · ↓${bytes(con.tx)} ↑${bytes(con.rx)}</div>` : `<span class="badge">desconectado</span>`}</td>
              <td class="actions">${c.revoked ? "" : `<button class="btn sm" data-ovdl="${esc(c.name)}">${ico("download")}.ovpn</button><button class="btn sm danger" data-ovrev="${esc(c.name)}">Revocar</button>`}</td></tr>`; }).join("") || `<tr><td colspan="4" class="muted">Sin clientes</td></tr>`}
            </tbody></table></div>` : `<p class="muted">Crea un servidor OpenVPN con su propia autoridad de certificación. Úsalo con dispositivos o redes donde WireGuard no esté disponible (OpenVPN sobre TCP 443 atraviesa casi cualquier firewall).</p>`}
        </div>
        <div class="card"><header><h3>${ico("list")}Registro del servidor</h3></header><pre style="max-height:320px">${esc((o.log || []).join("\n") || "—")}</pre></div>
      </div>
      <div class="card mt"><header><h2>${ico("upload")}Perfiles cliente (conectarse a otra VPN)</h2><button class="btn" id="ov-imp">${ico("upload")}Importar .ovpn</button></header>
        ${o.profiles.length ? `<div class="table-wrap"><table><thead><tr><th>Perfil</th><th>Estado</th><th>Últimas líneas</th><th></th></tr></thead><tbody>
          ${o.profiles.map((p) => `<tr><td><b>${esc(p.name)}</b>${p.auth ? `<div class="small muted">usuario/contraseña</div>` : ""}</td>
          <td>${p.connected ? `<span class="badge ok">conectado</span>` : p.running ? `<span class="badge warn">conectando…</span>` : `<span class="badge">desconectado</span>`}</td>
          <td class="small mono muted truncate" style="max-width:380px">${esc((p.log || []).slice(-1)[0] || "")}</td>
          <td class="actions">${p.running ? `<button class="btn sm" data-ovp="${esc(p.name)}" data-act="disconnect">Desconectar</button>` : `<button class="btn sm success" data-ovp="${esc(p.name)}" data-act="connect">Conectar</button>`}
          <button class="icon-btn" data-ovp="${esc(p.name)}" data-act="delete">${ico("trash")}</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="muted small">Importa el .ovpn de tu proveedor o de tu trabajo para conectar NetPing a esa VPN.</p>`}</div>`;

    pane.querySelector("#ov-init").onclick = () => modal({ title: s ? "Configurar servidor OpenVPN" : "Crear servidor OpenVPN", body: `<div class="form-grid">
        <label class="field">Protocolo<select name="proto"><option value="udp" ${s?.proto !== "tcp" ? "selected" : ""}>UDP (más rápido)</option><option value="tcp" ${s?.proto === "tcp" ? "selected" : ""}>TCP (atraviesa firewalls)</option></select></label>
        <label class="field">Puerto<input type="number" name="port" value="${s?.port || 1194}"></label>
        <label class="field">Red del túnel<input name="network" value="${esc(s?.network || "10.9.0.0/24")}"></label>
        <label class="field">Endpoint público<input name="endpoint" value="${esc(s?.endpoint || "")}" placeholder="IP o dominio"></label>
        <label class="field">DNS para clientes<input name="dns" value="${esc(s?.dns || "")}" placeholder="IP de NetPing"></label></div>
        <div class="opt"><div class="t"><b>Túnel completo</b><span>Todo el tráfico por casa.</span></div>${toggle("full", s ? s.full_tunnel : true)}</div>
        <div class="opt"><div class="t"><b>Permitir comunicación entre clientes</b></div>${toggle("c2c", s?.client_to_client)}</div>`,
      actions: [{ label: "Cancelar" }, { label: "Guardar", kind: "primary", onClick: async (m) => {
        await api.post("/vpn/openvpn/server", { proto: $("[name=proto]", m).value, port: Number($("[name=port]", m).value), network: $("[name=network]", m).value,
          endpoint: $("[name=endpoint]", m).value, dns: $("[name=dns]", m).value || undefined, full_tunnel: $("[name=full]", m).checked, client_to_client: $("[name=c2c]", m).checked });
        toast("Servidor configurado (PKI generada)"); refresh();
      } }] });
    on(pane, "click", "[data-ov]", (e, b) => busy(b, () => api.post(`/vpn/openvpn/server/${b.dataset.ov}`)).then(() => setTimeout(refresh, 1500)).catch(() => {}));
    const add = pane.querySelector("#ov-add");
    if (add) add.onclick = () => busy(add, async () => {
      const r = await api.post("/vpn/openvpn/clients", { name: pane.querySelector("#ov-name").value.trim() });
      if (r.content) download(r.name, r.content); else toast(r.warning, "warn");
      refresh();
    }).catch(() => {});
    on(pane, "click", "[data-ovdl]", async (e, b) => { try { const r = await api.get(`/vpn/openvpn/clients/${b.dataset.ovdl}`); download(r.name, r.content); } catch (err) { toastErr(err); } });
    on(pane, "click", "[data-ovrev]", async (e, b) => { if (await confirmDialog("Revocar certificado", `${b.dataset.ovrev} no podrá volver a conectarse.`)) { await api.del(`/vpn/openvpn/clients/${b.dataset.ovrev}`); refresh(); } });
    pane.querySelector("#ov-imp").onclick = () => modal({ title: "Importar perfil .ovpn", wide: true, body: `<div class="form-grid mb"><label class="field">Nombre<input name="name" value="vpn1"></label>
        <label class="field">Usuario (opcional)<input name="user" autocomplete="off"></label><label class="field">Contraseña (opcional)<input name="pass" type="password" autocomplete="new-password"></label></div>
        <label class="field">Contenido .ovpn<textarea name="conf" rows="10"></textarea></label>`,
      actions: [{ label: "Cancelar" }, { label: "Importar", kind: "primary", onClick: async (m) => {
        await api.post("/vpn/openvpn/profiles", { name: $("[name=name]", m).value, config: $("[name=conf]", m).value, username: $("[name=user]", m).value, password: $("[name=pass]", m).value });
        toast("Perfil importado"); refresh();
      } }] });
    on(pane, "click", "[data-ovp]", (e, b) => busy(b, () => api.post(`/vpn/openvpn/profiles/${b.dataset.ovp}/${b.dataset.act}`)).then(() => setTimeout(refresh, 1200)).catch(() => {}));
    timer = setInterval(async () => { if (!document.querySelector(".overlay")) { const ov = await api.get("/vpn").catch(() => null); if (ov && tab === "openvpn") openvpn(ov.openvpn); } }, 10000);
  }

  // =============================================================== Tailscale
  function tailscale(t) {
    pane = fresh(pane);
    if (!t.available) { pane.innerHTML = notInstalled("Tailscale", "tailscale (https://tailscale.com/download/linux)"); return; }
    const p = t.prefs || {};
    const running = t.state === "Running";
    pane.innerHTML = `
      <div class="grid g3">
        <div class="card span2"><header><h2>${ico("network")}Tailscale ${running ? `<span class="badge ok">conectado</span>` : `<span class="badge">${esc(t.state || (t.daemon ? "desconectado" : "detenido"))}</span>`}</h2>
          ${running ? `<button class="btn sm" data-ts="down">${ico("power")}Desconectar</button><button class="btn sm danger" data-ts="logout">Cerrar sesión</button>` : ""}</header>
          <p class="muted small">Red privada mallada sobre WireGuard: conecta tus dispositivos <b>aunque estés detrás de CGNAT</b> y sin abrir puertos. Activa «Anunciar mi LAN» para llegar a todos los equipos de casa y usa NetPing como DNS de tu tailnet para tener bloqueo de anuncios en todas partes.</p>
          ${t.login_url ? `<div class="alert info mt">${ico("key")}<div><b>Inicia sesión para autorizar este equipo</b><p class="small"><a href="${esc(t.login_url)}" target="_blank" rel="noopener">${esc(t.login_url)}</a></p></div></div>` : ""}
          ${(t.health || []).map((h) => `<div class="alert warn mt small">${ico("alert")}<div>${esc(h)}</div></div>`).join("")}
          ${running ? `<dl class="kv mt"><dt>Este equipo</dt><dd>${esc(t.self?.name)} · ${esc((t.self?.ips || []).join(", "))}</dd><dt>Tailnet</dt><dd>${esc(t.tailnet || "")} · MagicDNS ${esc(t.magic_dns || "")}</dd><dt>Versión</dt><dd>${esc(t.version || "")}${t.userspace ? " · modo userspace" : ""}</dd></dl>` : ""}
          <form id="tsf" class="mt">
            <div class="form-grid"><label class="field">Hostname<input name="hostname" value="${esc(p.hostname || "netping")}"></label>
              <label class="field">Rutas a anunciar (LAN)<input name="routes" value="${esc((p.advertise_routes || []).join(", "))}"></label>
              <label class="field">Usar exit node<input name="exit_node" value="${esc(p.exit_node || "")}" placeholder="IP o nombre (vacío = ninguno)"></label>
              <label class="field">Servidor de control (Headscale)<input name="login_server" value="${esc(p.login_server || "")}" placeholder="vacío = Tailscale oficial"></label></div>
            ${o2("advertise_exit_node", "Ofrecer este equipo como exit node", "Tus dispositivos pueden salir a Internet por casa (bloqueo de anuncios y tu IP de casa en cualquier Wi-Fi).", p.advertise_exit_node)}
            ${o2("accept_routes", "Aceptar rutas de otros nodos", "", p.accept_routes)}
            ${o2("accept_dns", "Usar el DNS de Tailscale en este equipo", "Normalmente desactivado: NetPing ya es el DNS.", p.accept_dns)}
            ${o2("ssh", "Tailscale SSH", "Acceso SSH a este equipo autenticado por Tailscale.", p.ssh)}
            ${o2("shields_up", "Shields Up", "Bloquea conexiones entrantes desde la tailnet.", p.shields_up)}
            ${o2("exit_node_allow_lan", "Permitir LAN local con exit node", "", p.exit_node_allow_lan)}
            ${o2("autostart", "Arrancar con NetPing", "", p.autostart)}
            ${!running ? `<label class="field mt">Auth key (opcional)<input name="authkey" placeholder="tskey-auth-…" autocomplete="off"><small>Sin clave se mostrará un enlace para iniciar sesión.</small></label>` : ""}
            <div class="row mt">${running ? `<button class="btn primary">${ico("check")}Aplicar cambios</button>` : `<button class="btn primary">${ico("play")}Conectar</button>`}</div>
          </form>
        </div>
        <div class="col">
          <div class="card"><header><h3>${ico("activity")}Latencia a relés DERP</h3><button class="btn sm" id="nc">${ico("refresh")}Medir</button></header><div id="ncres"><p class="muted small">Mide la latencia a los relés de Tailscale y si tu NAT permite conexión directa.</p></div></div>
          <div class="card"><header><h3>${ico("target")}Ping por Tailscale</h3></header><form class="inline-form" id="tsping"><input name="t" placeholder="nodo o IP 100.x"><button class="btn sm">Ping</button></form><pre id="tspres" class="mt hidden"></pre></div>
        </div>
      </div>
      ${running ? `<div class="card mt"><header><h2>${ico("devices")}Dispositivos de la tailnet (${t.peers.length})</h2></header><div class="table-wrap"><table><thead><tr><th></th><th>Nombre</th><th>IP</th><th>SO</th><th>Conexión</th><th>Tráfico</th><th>Visto</th></tr></thead><tbody>
        ${t.peers.map((x) => `<tr><td><span class="dot dot-${x.online ? "ok" : "off"}"></span></td><td><b>${esc(x.name)}</b>${x.exit_option ? ` <span class="badge violet">exit node</span>` : ""}${x.exit_node ? ` <span class="badge ok">en uso</span>` : ""}${x.routes?.length ? `<div class="small muted">rutas ${esc(x.routes.join(", "))}</div>` : ""}</td>
          <td class="mono">${esc((x.ips || [])[0] || "")}</td><td>${esc(x.os || "")}</td><td>${x.direct ? `<span class="badge ok">directa</span>` : x.online ? `<span class="badge warn">relé ${esc(x.relay)}</span>` : "—"}</td>
          <td class="small">↓${bytes(x.rx)} ↑${bytes(x.tx)}</td><td class="small muted">${x.online ? "ahora" : esc((x.last_seen || "").slice(0, 10))}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">Sin otros dispositivos</td></tr>`}</tbody></table></div></div>` : ""}`;

    const f = pane.querySelector("#tsf");
    f.onsubmit = async (e) => {
      e.preventDefault();
      const body = { hostname: f.hostname.value, advertise_routes: f.routes.value.split(/[\s,]+/).filter(Boolean), exit_node: f.exit_node.value.trim(), login_server: f.login_server.value.trim() };
      for (const k of ["advertise_exit_node", "accept_routes", "accept_dns", "ssh", "shields_up", "exit_node_allow_lan", "autostart"]) body[k] = f[k].checked;
      await busy(f.querySelector("button.primary"), async () => {
        if (running) { await api.post("/vpn/tailscale/set", body); toast("Preferencias aplicadas"); }
        else {
          const r = await api.post("/vpn/tailscale/up", { ...body, authkey: f.authkey?.value.trim() || "" });
          if (r.login_url) toast("Abre el enlace para iniciar sesión", "info", 8000); else if (r.ok) toast("Tailscale conectado");
          else toast(r.output || "No se pudo conectar", "error", 8000);
        }
        refresh();
      }).catch(() => {});
    };
    on(pane, "click", "[data-ts]", async (e, b) => { if (b.dataset.ts === "logout" && !(await confirmDialog("Cerrar sesión", "Este equipo saldrá de la tailnet."))) return; busy(b, () => api.post(`/vpn/tailscale/${b.dataset.ts}`)).then(refresh).catch(() => {}); });
    pane.querySelector("#nc").onclick = (e) => busy(e.currentTarget, async () => {
      const r = await api.post("/vpn/tailscale/netcheck");
      pane.querySelector("#ncres").innerHTML = `<dl class="kv small">${Object.entries(r.info).map(([k, v]) => `<dt>${esc({ udp: "UDP", ipv4: "IPv4", ipv6: "IPv6", mapping_varies: "NAT cambia puerto", portmap: "Port mapping", nearest: "DERP más cercano" }[k] || k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        <div class="col mt small">${r.derp.slice(0, 8).map((d, i) => `<div class="row between"><span>${i === 0 ? "⭐ " : ""}${esc(d.name)}</span><b>${d.ms} ms</b></div>`).join("")}</div>`;
    }).catch(() => {});
    pane.querySelector("#tsping").onsubmit = (e) => { e.preventDefault(); const pre = pane.querySelector("#tspres"); busy(e.target.querySelector("button"), () => api.post("/vpn/tailscale/ping", { target: e.target.t.value })).then((r) => { pre.classList.remove("hidden"); pre.textContent = r.output; }).catch(() => {}); };
    if (t.login_url) timer = setInterval(async () => { const ov = await api.get("/vpn").catch(() => null); if (ov?.tailscale?.state === "Running") { toast("¡Tailscale conectado!"); refresh(); } }, 4000);
  }

  // =============================================================== VPS relay
  function relay() {
    pane.innerHTML = `<div class="grid g3"><form class="card span2" id="rf">
      <header><h2>${ico("globe")}Generador: VPS de relevo con WireGuard</h2></header>
      <p class="muted small mb">Para redes con <b>CGNAT</b> o sin puertos abiertos: tu casa se conecta <i>saliendo</i> hacia un VPS con IP pública; los clientes y los puertos que publiques entran por el VPS. Elige un VPS en tu misma ciudad o país para minimizar la latencia.</p>
      <div class="form-grid">
        <label class="field">Modo<select name="mode"><option value="relay">VPS de relevo (CGNAT)</option><option value="home">Servidor en casa (IP pública)</option></select></label>
        <label class="field">IP/dominio del VPS (o de casa)<input name="endpoint" required placeholder="203.0.113.10"></label>
        <label class="field">Puerto UDP<input type="number" name="port" value="51820"></label>
        <label class="field">Red del túnel<input name="tunnel_net" value="10.8.0.0/24"></label>
        <label class="field">Red de tu casa<input name="lan_net" value="192.168.1.0/24"></label>
        <label class="field">DNS clientes (NetPing)<input name="dns" placeholder="192.168.1.2"></label>
        <label class="field">Interfaz LAN en casa<input name="lan_iface" value="eth0"></label>
        <label class="field">Interfaz pública del VPS<input name="wan_iface" value="eth0"></label>
        <label class="field">MTU<input type="number" name="mtu" value="1420"></label>
      </div>
      <label class="field mt">Clientes (separados por coma)<input name="clients" value="movil, portatil"></label>
      <label class="field mt">Puertos a publicar en el VPS (uno por línea: proto puerto ip-destino [puerto-destino])
        <textarea name="forwards" rows="3" placeholder="udp 3074 192.168.1.50\ntcp 25565 192.168.1.20\ntcp 8443 192.168.1.10 443"></textarea>
        <small>Ej.: consola (NAT abierto), servidor de Minecraft, Plex… Funciona aunque tu ISP use CGNAT.</small></label>
      <div class="opt"><div class="t"><b>Túnel completo para los clientes</b></div>${toggle("full_tunnel", true)}</div>
      <button class="btn primary mt">${ico("zap")}Generar configuraciones</button></form>
      <div class="card"><header><h3>${ico("info")}Cómo funciona</h3></header><ol class="steps small">
        <li>Casa → VPS: túnel saliente (no necesita puertos abiertos en casa).</li><li>Clientes → VPS → casa: acceso a tu red y a NetPing.</li>
        <li>Internet → VPS:puerto → dispositivo de casa: puertos publicados con DNAT.</li><li>Latencia extra ≈ ping casa↔VPS: elige un VPS cercano.</li></ol></div></div>
      <div id="rres" class="mt"></div>`;
    api.get("/system").then((s) => {
      const f = pane.querySelector("#rf"); const itf = s.interfaces?.[0];
      if (itf) { f.lan_net.value = itf.network; f.lan_iface.value = itf.name; f.dns.value = itf.ip; }
    }).catch(() => {});
    pane.querySelector("#rf").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      const forwards = f.forwards.value.split("\n").map((l) => l.trim().split(/\s+/)).filter((x) => x.length >= 3).map(([proto, port, target, tp]) => ({ proto, port: Number(port), target, target_port: tp ? Number(tp) : undefined }));
      await busy(f.querySelector("button.primary"), async () => {
        const r = await api.post("/wireguard/generate", { mode: f.mode.value, endpoint: f.endpoint.value, port: Number(f.port.value), tunnel_net: f.tunnel_net.value, lan_net: f.lan_net.value,
          dns: f.dns.value, lan_iface: f.lan_iface.value, wan_iface: f.wan_iface.value, mtu: Number(f.mtu.value), full_tunnel: f.full_tunnel.checked,
          clients: f.clients.value.split(","), forwards });
        const box = pane.querySelector("#rres");
        box.innerHTML = `<div class="card"><header><h2>Configuraciones generadas</h2></header><ol class="steps mb">${r.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
          <div class="grid g2">${r.files.map((x, i) => `<div class="card flat"><header><h3>${esc(x.name)}</h3><span class="badge">${{ server: "servidor", home: "casa", client: "cliente" }[x.role]}</span>
            <button class="btn sm" data-dlf="${i}">${ico("download")}</button><button class="btn sm" data-cpf="${i}">${ico("copy")}</button></header>
            <div class="row" style="align-items:flex-start">${x.qr ? `<div class="qr" style="width:150px">${x.qr}</div>` : ""}<pre class="grow" style="max-height:220px">${esc(x.content)}</pre></div></div>`).join("")}</div></div>`;
        on(box, "click", "[data-dlf]", (ev, b) => download(r.files[b.dataset.dlf].name, r.files[b.dataset.dlf].content));
        on(box, "click", "[data-cpf]", (ev, b) => copy(r.files[b.dataset.cpf].content));
      }).catch(() => {});
    };
  }

  function guide() {
    const rows = [
      ["WireGuard", "La más rápida y con menor latencia (ideal para gaming/streaming remoto)", "Necesita IP pública o IPv6 + 1 puerto UDP", "Excelente"],
      ["Tailscale", "Funciona detrás de CGNAT sin abrir puertos; conexión directa P2P cuando es posible", "Cuenta Tailscale (o Headscale propio)", "Excelente"],
      ["OpenVPN", "Máxima compatibilidad; por TCP 443 atraviesa firewalls corporativos", "IP pública + puerto", "Buena"],
      ["VPS de relevo", "Publica puertos (consolas, servidores de juego) aunque tengas CGNAT", "Un VPS barato cercano", "Muy buena"],
    ];
    pane.innerHTML = `<div class="card"><div class="table-wrap"><table><thead><tr><th>Opción</th><th>Ventaja</th><th>Requisito</th><th>Rendimiento</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td><b>${r[0]}</b></td><td>${r[1]}</td><td class="muted">${r[2]}</td><td><span class="badge ok">${r[3]}</span></td></tr>`).join("")}</tbody></table></div>
      <div class="alert info mt">${ico("info")}<div class="small">¿No sabes si tienes CGNAT? Ejecuta el <a href="#/connectivity">análisis de NAT y CGNAT</a>: te dirá cuál usar.</div></div></div>`;
  }

  show();
  return () => clearInterval(timer);
}

const notInstalled = (n, pkg) => `<div class="alert warn">${ico("alert")}<div><b>${n} no está instalado en este sistema</b><p class="small">Viene incluido en la imagen Docker. En Linux/Proxmox: instala <code>${esc(pkg)}</code> y reinicia NetPing.</p></div></div>`;
const o2 = (name, t, d, v) => `<div class="opt"><div class="t"><b>${t}</b>${d ? `<span>${d}</span>` : ""}</div>${toggle(name, v)}</div>`;
