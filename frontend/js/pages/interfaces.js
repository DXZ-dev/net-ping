// Interfaces de red: estado y configuración de cada tarjeta (LAN, WAN, módem USB, VLAN) + añadir tarjetas en Proxmox.
import { api, esc, ico, bytes, toast, toastErr, busy, on, modal, confirmDialog, empty, $ } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Interfaces de red";
export const subtitle = "Cada tarjeta de red de Bipon: estado, velocidad, tráfico y su configuración (DHCP, IP fija, MTU, VLAN)";

const KIND = { ethernet: ["network", "Ethernet"], wifi: ["wifi", "Wi-Fi"], usb: ["zap", "USB / módem"], vlan: ["layers", "VLAN"], bridge: ["router", "Puente"],
  bond: ["link", "Agregado"], wireguard: ["key", "WireGuard"], tun: ["key", "Túnel"], macvlan: ["network", "macvlan"], ipvlan: ["network", "ipvlan"], veth: ["network", "veth"] };
const ROLE = (r) => r === "lan" ? ["info", "LAN"] : r === "wan" ? ["warn", "WAN"] : r === "panel" ? ["ok", t("Panel")]
  : r.startsWith("multiwan:") ? ["warn", `${t("Línea")}: ${r.slice(9)}`] : r.startsWith("zona:") ? ["", `${t("Zona")} ${r.slice(5)}`] : ["", r];
const rate = (bps) => bps == null ? "—" : bps >= 1e6 ? `${(bps / 1e6).toFixed(bps >= 1e8 ? 0 : 1)} Mbit/s` : `${Math.round(bps / 1e3)} kbit/s`;
const MODE = { keep: "Como la configura el sistema", dhcp: "DHCP (automática)", static: "IP fija", disabled: "Desactivada" };

export async function render(el) {
  let st = await api.get("/interfaces");
  let timer = null;
  let countdown = null;

  const draw = () => {
    if (!document.body.contains(el) || document.querySelector(".overlay")) return;
    const list = st.interfaces || [];
    el.innerHTML = `
      ${st.pending ? `<div class="alert warn mb">${ico("alert")}<div><b>${t("Cambio en la interfaz del panel pendiente de confirmar")}</b>
        <p class="small">${t("Si sigues viendo esta página, confirma. Si no confirmas, se restaura sola en")} <b id="cd">${Math.max(0, Math.round(st.deadline - Date.now() / 1000))}</b> s.</p>
        <button class="btn primary sm mt" id="confirm">${ico("check")}${t("Confirmar cambio")}</button></div></div>` : ""}
      <div class="card mb"><header><h2>${ico("network")}${t("Interfaces de red")} <span class="live-dot"></span></h2>
        <div class="row"><button class="btn sm" id="vlan">${ico("plus")}${t("Crear VLAN")}</button>${st.proxmox ? `<button class="btn sm primary" id="pve">${ico("server")}${t("Añadir tarjeta en Proxmox")}</button>` : ""}</div></header>
        ${list.length ? `<div class="grid g2">${list.map((i) => {
          const [ic, kl] = KIND[i.kind] || ["network", i.kind];
          const up = i.state === "up" || (i.state === "unknown" && i.carrier);
          const cfg = i.config || { mode: "keep" };
          return `<div class="rec">
            <div class="row between"><b>${ico(ic)}${esc(i.name)}</b><span class="row">${i.roles.map((r) => { const [c, l] = ROLE(r); return `<span class="badge ${c}">${esc(l)}</span>`; }).join("")}
              <span class="badge ${up ? "ok" : "err"}">${up ? t("Conectada") : t("Sin enlace")}</span></span></div>
            <div class="small muted">${esc(t(kl))}${i.speed ? ` · ${i.speed >= 1000 ? i.speed / 1000 + " Gbit/s" : i.speed + " Mbit/s"}${i.duplex === "half" ? " (half-duplex ⚠)" : ""}` : ""} · MTU ${i.mtu}${i.driver ? " · " + esc(i.driver) : ""}${i.vlan_id ? ` · VLAN ${i.vlan_id} ${t("sobre")} ${esc(i.parent || "")}` : ""}</div>
            <dl class="kv small mt"><dt>IPv4</dt><dd>${i.ipv4.length ? i.ipv4.map(esc).join(", ") : "—"} ${i.dhcp ? `<span class="badge">DHCP</span>` : ""}</dd>
              <dt>${t("Puerta de enlace")}</dt><dd>${esc(i.gateway || "—")}</dd><dt>MAC</dt><dd class="mono">${esc(i.mac || "—")}</dd>
              ${i.ipv6.length ? `<dt>IPv6</dt><dd class="small">${i.ipv6.map(esc).join("<br>")}</dd>` : ""}
              <dt>${t("Tráfico ahora")}</dt><dd>↓ ${rate(i.rate?.rx_bps)} · ↑ ${rate(i.rate?.tx_bps)}</dd>
              <dt>${t("Total")}</dt><dd>↓ ${bytes(i.rx_bytes)} · ↑ ${bytes(i.tx_bytes)}</dd>
              ${i.rx_errors || i.tx_errors || i.rx_dropped ? `<dt>${t("Errores")}</dt><dd style="color:var(--warn)">${i.rx_errors + i.tx_errors} ${t("errores")} · ${i.rx_dropped} ${t("descartados")}</dd>` : ""}
              <dt>${t("Configuración")}</dt><dd>${esc(t(MODE[cfg.mode] || cfg.mode))}${cfg.address ? " · " + esc(cfg.address) : ""}${cfg.mtu ? " · MTU " + cfg.mtu : ""}</dd></dl>
            <div class="row mt"><button class="btn sm" data-cfg="${esc(i.name)}">${ico("settings")}${t("Configurar")}</button>
              ${i.kind === "vlan" ? `<button class="icon-btn" data-delvlan="${esc(i.name)}" title="${t("Borrar VLAN")}">${ico("trash")}</button>` : ""}</div></div>`;
        }).join("")}</div>` : empty(t("No se encontraron interfaces."), "network")}
        <p class="small muted mt">${t("La velocidad del enlace es la negociada por el cable (100 Mbit/s en un puerto de 1 Gbit/s suele indicar un cable dañado). «Half-duplex» o errores crecientes también apuntan al cable o al puerto.")}</p></div>
      <div class="card"><header><h3>${ico("help")}${t("Qué puedes hacer aquí")}</h3></header>
        <ul class="small" style="padding-left:18px;line-height:1.7">
          <li>${t("Dar IP fija o DHCP a una segunda tarjeta (p. ej. un módem 4G por USB) y usarla como línea en Multi-WAN.")}</li>
          <li>${t("Crear VLAN para separar invitados, cámaras o IoT (después asígnalas como zona en Router y firewall).")}</li>
          <li>${t("En Proxmox, añadir a Bipon una tarjeta conectada a otro puente (otra línea de Internet o red separada).")}</li>
          <li>${t("Cambiar la interfaz por la que entras al panel exige confirmar en 60 s: si pierdes la conexión, se deshace sola.")}</li></ul>
        <a class="btn sm mt" href="#/multiwan">${ico("network")}${t("Ir a Multi-WAN")}</a></div>`;
    el.querySelector("#confirm")?.addEventListener("click", (e) => busy(e.currentTarget, () => api.post("/interfaces/confirm")).then(async () => { toast(t("Cambio confirmado")); st = await api.get("/interfaces"); draw(); }));
    el.querySelector("#vlan").onclick = () => modal({ title: t("Crear VLAN"), body: `<div class="form-grid">
        <label class="field">${t("Interfaz física")}<select id="vp">${list.filter((i) => i.kind !== "vlan").map((i) => `<option>${esc(i.name)}</option>`).join("")}</select></label>
        <label class="field">${t("Número de VLAN (1-4094)")}<input type="number" id="vid" min="1" max="4094" value="20"></label></div>
        <p class="small muted mt">${t("Tu switch o punto de acceso debe enviar esa VLAN etiquetada por el mismo cable.")}</p>`,
      actions: [{ label: t("Cancelar") }, { label: t("Crear"), kind: "primary", onClick: async (m) => {
        const r = await api.post("/interfaces/vlan", { parent: $("#vp", m).value, vid: Number($("#vid", m).value) }); toast(`${t("VLAN creada")}: ${r.name}`); st = await api.get("/interfaces"); setTimeout(draw, 50);
      } }] });
    el.querySelector("#pve")?.addEventListener("click", pveDialog);
  };

  const pveDialog = async () => {
    let info;
    try { info = await api.get("/interfaces/proxmox"); } catch (e) { return toastErr(e); }
    modal({ title: t("Añadir tarjeta de red en Proxmox"), wide: true, body: `
      <p class="small muted">${t("Bipon es")} <b>${esc(info.type === "lxc" ? "LXC" : "VM")} ${info.vmid}</b> ${t("en el nodo")} <b>${esc(info.node)}</b>. ${t("Tarjetas actuales")}: ${Object.keys(info.nets).map(esc).join(", ") || "—"}</p>
      <div class="form-grid mt"><label class="field">${t("Puente de Proxmox")}<select id="br">${info.bridges.map((b) => `<option>${esc(b)}</option>`).join("")}</select></label>
        <label class="field">${t("Dirección")}<select id="ipm"><option value="dhcp">DHCP</option><option value="static">${t("IP fija")}</option></select></label>
        <label class="field">${t("IP/máscara (si es fija)")}<input id="ip" placeholder="192.168.8.10/24"></label>
        <label class="field">${t("Puerta de enlace (si es fija)")}<input id="gw" placeholder="192.168.8.1"></label>
        <label class="field">${t("VLAN (opcional)")}<input type="number" id="tag" min="1" max="4094"></label></div>
      <p class="small muted mt">${t("Para un módem 4G/5G o un móvil por USB: conéctalo al servidor Proxmox, crea un puente con su interfaz (p. ej. vmbr1) y elige ese puente aquí. El token de Proxmox necesita el permiso VM.Config.Network.")}</p>`,
      actions: [{ label: t("Cancelar") }, { label: t("Añadir tarjeta"), kind: "primary", onClick: async (m) => {
        const fixed = $("#ipm", m).value === "static";
        const r = await api.post("/interfaces/proxmox", { bridge: $("#br", m).value, ip: fixed ? $("#ip", m).value.trim() : "dhcp", gw: fixed ? $("#gw", m).value.trim() : "", vlan: Number($("#tag", m).value) || null });
        toast(t(r.message), "ok", 8000); setTimeout(async () => { st = await api.get("/interfaces"); draw(); }, 2500);
      } }] });
  };

  on(el, "click", "[data-cfg]", (e, b) => {
    const i = st.interfaces.find((x) => x.name === b.dataset.cfg);
    const c = i.config || { mode: "keep" };
    const panel = i.roles.includes("panel");
    modal({ title: `${t("Configurar")} ${i.name}`, wide: true, body: `
      ${panel ? `<div class="alert warn small mb">${ico("alert")}<div>${t("Es la interfaz por la que entras al panel. Si cambias su IP tendrás que entrar por la nueva y confirmar en 60 s; si no, se deshace sola.")}</div></div>` : ""}
      <div class="form-grid"><label class="field">${t("Modo")}<select id="mode">${Object.entries(MODE).filter(([k]) => !(panel && k === "disabled")).map(([k, v]) => `<option value="${k}" ${c.mode === k ? "selected" : ""}>${t(v)}</option>`).join("")}</select></label>
        <label class="field">${t("MTU (vacío = sin cambios)")}<input type="number" id="mtu" min="576" max="9216" value="${c.mtu || ""}" placeholder="${i.mtu}"></label>
        <label class="field st">${t("IP/máscara")}<input id="addr" value="${esc(c.address || i.ipv4[0] || "")}" placeholder="192.168.8.10/24"></label>
        <label class="field st">${t("Puerta de enlace (para usarla como línea de Internet)")}<input id="gw" value="${esc(c.gateway || "")}" placeholder="${t("opcional")}"></label>
        <label class="field">${t("Descripción")}<input id="desc" value="${esc(c.description || "")}" placeholder="${t("Ej. Módem 4G del salón")}"></label></div>
      ${st.dhcp_client ? "" : `<p class="small mt" style="color:var(--warn)">${t("No hay cliente DHCP instalado: el modo DHCP necesita dhcpcd-base o isc-dhcp-client (se instala al actualizar Bipon).")}</p>`}
      <p class="small muted mt">${t("«Como la configura el sistema» deja la interfaz como la dejó Proxmox, Docker o tu sistema (recomendado salvo que necesites cambiarla). La configuración se guarda en Bipon y se vuelve a aplicar al arrancar.")}</p>`,
      actions: [{ label: t("Cancelar") }, { label: t("Aplicar"), kind: "primary", onClick: async (m) => {
        const body = { mode: $("#mode", m).value, mtu: Number($("#mtu", m).value) || null, description: $("#desc", m).value };
        if (body.mode === "static") Object.assign(body, { address: $("#addr", m).value.trim(), gateway: $("#gw", m).value.trim() });
        const r = await api.put(`/interfaces/${encodeURIComponent(i.name)}`, body);
        toast(r.notes?.length ? r.notes.join(" · ") : r.pending ? t("Aplicado: confírmalo en 60 s") : t("Aplicado"), r.notes?.length ? "warn" : "ok", 8000);
        setTimeout(async () => { st = await api.get("/interfaces"); draw(); }, 300);
      } }] });
    const m = document.querySelector(".overlay");
    const sync = () => m.querySelectorAll(".st").forEach((x) => x.classList.toggle("hidden", $("#mode", m).value !== "static"));
    $("#mode", m).onchange = sync; sync();
  });
  on(el, "click", "[data-delvlan]", async (e, b) => {
    if (!(await confirmDialog(t("Borrar VLAN"), `${t("Se borrará")} ${b.dataset.delvlan}.`))) return;
    busy(b, () => api.del(`/interfaces/vlan/${encodeURIComponent(b.dataset.delvlan)}`)).then(async () => { st = await api.get("/interfaces"); draw(); }).catch(() => {});
  });

  draw();
  timer = setInterval(async () => {
    if (!document.body.contains(el)) return clearInterval(timer);
    if (document.querySelector(".overlay")) return;
    const fresh = await api.get("/interfaces").catch(() => null);
    if (fresh) { st = fresh; draw(); }
  }, 3000);
  countdown = setInterval(() => { const c = el.querySelector("#cd"); if (c && st.deadline) c.textContent = Math.max(0, Math.round(st.deadline - Date.now() / 1000)); }, 1000);
  return () => { clearInterval(timer); clearInterval(countdown); };
}
