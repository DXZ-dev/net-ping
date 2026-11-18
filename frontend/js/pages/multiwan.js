// Multi-WAN: varias conexiones a Internet (respaldo, reparto de carga, juegos por la línea con menos ping).
import { api, esc, ico, ms, ago, toast, toastErr, busy, on, modal, confirmDialog, toggle, empty, $ } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Multi-WAN";
export const subtitle = "Varias conexiones a Internet: respaldo automático, reparto de carga y la línea más rápida para jugar";

const spark = (vals) => {
  const pts = vals.map((v, i) => [i, v]).filter((p) => p[1] != null);
  if (pts.length < 2) return `<div class="muted small">${t("Midiendo…")}</div>`;
  const W = 240, H = 36, max = Math.max(20, ...pts.map((p) => p[1])) * 1.2, n = Math.max(1, vals.length - 1);
  const line = pts.map((p) => `${((p[0] / n) * W).toFixed(1)},${(H - (p[1] / max) * H).toFixed(1)}`).join(" ");
  const lost = vals.map((v, i) => (v == null ? `<line x1="${(i / n) * W}" x2="${(i / n) * W}" y1="0" y2="${H}" stroke="var(--err)" stroke-width="2" opacity=".55"/>` : "")).join("");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:36px;display:block">${lost}
    <polyline points="${line}" fill="none" stroke="var(--accent-2)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
};

const stateBadge = (w, watching) => {
  if (w.enabled === false) return `<span class="badge">${t("Desactivada")}</span>`;
  if (!watching) return `<span class="badge">${t("Sin vigilar")}</span>`;
  if (w.forced) return `<span class="badge warn">${t("Prueba de caída")}</span>`;
  if (w.up === false) return `<span class="badge err">${t("Caída")}</span>`;
  if (w.checked) return `<span class="badge ok">${t("Activa")}</span>`;
  return `<span class="badge">${t("Comprobando…")}</span>`;
};

export async function render(el) {
  let st = await api.get("/multiwan");
  let devices = (await api.get("/devices").catch(() => ({ devices: [] }))).devices.filter((d) => d.ip && !d.is_gateway && !String(d.mac).startsWith("ip:"));
  let timer = null;

  const cfg = () => st.config;
  const save = async (patch, msg = "Guardado") => {
    const r = await api.put("/multiwan", { ...cfg(), ...patch });
    st = r;
    const notes = r.result?.notes || [];
    toast(notes.length ? notes.join(" · ") : t(msg), notes.length ? "warn" : "ok", notes.length ? 9000 : 3500);
    draw();
  };

  const wanForm = (w = {}) => {
    const ifs = st.all_ifaces || [];
    return `<div class="form-grid">
      <label class="field">${t("Nombre")}<input id="f-name" value="${esc(w.name || "")}" placeholder="${t("Ej. Fibra, 4G, Proveedor 2")}"></label>
      <label class="field">${t("Interfaz de red")}<select id="f-iface">${ifs.map((i) => `<option value="${esc(i.name)}" ${i.name === (w.iface || st.lan) ? "selected" : ""}>${esc(i.name)} ${i.ip ? "· " + esc(i.ip) : ""} ${i.state === "up" ? "" : "(" + esc(i.state) + ")"}</option>`).join("")}</select></label>
      <label class="field">${t("Puerta de enlace (router de esa línea)")}<input id="f-gw" value="${esc(w.gateway || "auto")}" placeholder="auto"></label>
      <label class="field">${t("Prioridad (1 = principal)")}<input type="number" id="f-prio" min="1" max="99" value="${w.priority || (st.wans.length + 1)}"></label>
      <label class="field">${t("Peso en el reparto")}<input type="number" id="f-weight" min="1" max="100" value="${w.weight || 1}"></label>
      <label class="field">${t("Bajada contratada (Mbit/s)")}<input type="number" id="f-down" min="0" step="0.1" value="${w.down_mbit || ""}" placeholder="${t("opcional")}"></label>
      <label class="field">${t("Subida contratada (Mbit/s)")}<input type="number" id="f-up" min="0" step="0.1" value="${w.up_mbit || ""}" placeholder="${t("opcional")}"></label></div>
      <div class="opt mt"><div class="t"><b>${t("Línea activa")}</b><span>${t("Desactívala para dejar de usarla sin borrarla.")}</span></div>${toggle("f-en", w.enabled !== false, 'id="f-en"')}</div>
      <p class="small muted mt">${t("«auto» detecta la puerta de enlace de esa interfaz (DHCP o módem USB). Con un solo cable, la segunda línea es otro router en tu misma red: escribe su IP (p. ej. 192.168.18.2).")}</p>`;
  };
  const readForm = (m, w = {}) => ({
    ...w, name: $("#f-name", m).value.trim() || t("Línea"), iface: $("#f-iface", m).value, gateway: $("#f-gw", m).value.trim() || "auto",
    priority: Number($("#f-prio", m).value) || 1, weight: Number($("#f-weight", m).value) || 1,
    down_mbit: Number($("#f-down", m).value) || 0, up_mbit: Number($("#f-up", m).value) || 0, enabled: $("#f-en", m).checked,
  });

  const draw = () => {
    if (!document.body.contains(el)) return;
    if (el.querySelector(".overlay") || document.querySelector(".overlay")) return; // no redibujar con un formulario abierto
    const c = cfg();
    const wans = st.wans || [];
    const ups = wans.filter((w) => w.up !== false && w.enabled !== false);
    el.innerHTML = `
      ${!st.router_enabled ? `<div class="alert warn mb">${ico("alert")}<div><b>${t("Bipon todavía no es la puerta de enlace")}</b>
        <p class="small">${t("El multi-WAN decide por qué línea sale cada conexión, así que el tráfico tiene que pasar por Bipon. Actívalo en Router y firewall → Asistente de gateway (funciona con un solo cable).")}</p>
        <a class="btn sm mt" href="#/router">${ico("router")}${t("Ir al asistente de gateway")}</a></div></div>` : ""}
      <div class="card mb"><header><h2>${ico("network")}${t("Multi-WAN")} ${c.enabled ? '<span class="live-dot"></span>' : ""}</h2>
        <div class="row">${toggle("mw-en", c.enabled, 'id="mw-en"')}<button class="btn primary sm" id="add">${ico("plus")}${t("Añadir línea")}</button></div></header>
        <div class="grid g3 small mb">
          <div><div class="muted">${t("Líneas activas")}</div><div style="font-size:24px;font-weight:700">${ups.length} / ${wans.length}</div></div>
          <div><div class="muted">${t("Modo")}</div><div style="font-size:18px;font-weight:600">${c.mode === "balance" ? t("Reparto de carga") : t("Respaldo automático")}</div></div>
          <div><div class="muted">${t("Juegos y llamadas por")}</div><div style="font-size:18px;font-weight:600">${esc((wans.find((w) => w.is_best) || {}).name || (c.smart_gaming ? t("midiendo…") : t("desactivado")))}</div></div>
        </div>
        ${wans.length ? `<div class="grid g2">${wans.map((w) => `<div class="rec">
          <div class="row between"><b>${esc(w.name)}</b><span class="row">${w.is_primary ? `<span class="badge info">${t("Principal")}</span>` : ""}${w.is_best && c.smart_gaming ? `<span class="badge ok">${ico("console")}${t("Menos ping")}</span>` : ""}${stateBadge(w, c.enabled && st.active)}</span></div>
          <div class="small muted">${esc(w.iface)} · ${t("puerta de enlace")} ${esc(w.gateway === "auto" ? (w.gw_detected || "auto") : w.gateway)} · ${t("prioridad")} ${w.priority} · ${t("peso")} ${w.weight}${w.down_mbit ? ` · ${w.down_mbit}/${w.up_mbit} Mbit/s` : ""}</div>
          <div class="grid g3 small mt"><div><span class="muted">${t("Ping")}</span><br><b>${ms(w.latency)}</b></div><div><span class="muted">${t("Jitter")}</span><br><b>${w.jitter != null ? w.jitter + " ms" : "—"}</b></div><div><span class="muted">${t("Pérdida")}</span><br><b>${w.loss != null ? w.loss + " %" : "—"}</b></div></div>
          <div class="mt">${c.enabled && st.active ? spark(w.spark || []) : `<div class="muted small">${t("Activa el multi-WAN para vigilar esta línea.")}</div>`}</div>
          ${w.up === false && w.reason ? `<div class="small" style="color:var(--err)">${esc(w.reason)} · ${w.since ? ago(w.since) : ""}</div>` : ""}
          <div class="row mt"><button class="btn sm" data-edit="${w.id}">${ico("edit")}${t("Editar")}</button>
            ${w.forced ? `<button class="btn sm" data-restore="${w.id}">${ico("refresh")}${t("Terminar prueba")}</button>` : `<button class="btn sm" data-sim="${w.id}" ${c.enabled && st.active ? "" : "disabled"}>${ico("zap")}${t("Simular caída 60 s")}</button>`}
            <button class="icon-btn" data-del="${w.id}" title="${t("Eliminar")}">${ico("trash")}</button></div></div>`).join("")}</div>`
        : empty(t("Añade tus líneas de Internet: la actual y la de respaldo (otro router, 4G o el móvil)."), "network")}
      </div>
      <div class="grid g2 mb">
        <div class="card"><header><h3>${ico("settings")}${t("Cómo trabajan juntas")}</h3></header>
          <div class="col">
            <label class="opt"><div class="t"><b>${t("Respaldo automático")}</b><span>${t("Todo sale por la principal. Si cae o su ping se dispara, Bipon pasa a la siguiente en segundos y vuelve cuando la principal está estable.")}</span></div><input type="radio" name="mode" value="failover" ${c.mode !== "balance" ? "checked" : ""} style="width:20px;height:20px;flex:none;margin:0"></label>
            <label class="opt"><div class="t"><b>${t("Reparto de carga")}</b><span>${t("Usa todas las líneas a la vez; si una cae, sus equipos pasan a las demás.")}</span></div><input type="radio" name="mode" value="balance" ${c.mode === "balance" ? "checked" : ""} style="width:20px;height:20px;flex:none;margin:0"></label>
            <div id="bal" class="${c.mode === "balance" ? "" : "hidden"}"><label class="field">${t("Repartir")}<select id="by">
              <option value="device" ${c.balance_by !== "connection" ? "selected" : ""}>${t("Por dispositivo según su consumo (recomendado: ninguna web ve cambiar tu IP)")}</option>
              <option value="connection" ${c.balance_by === "connection" ? "selected" : ""}>${t("Por conexión (más reparto, algunos bancos o juegos pueden desconectar)")}</option></select></label></div>
            <div class="opt"><div class="t"><b>${t("Juegos, llamadas y consolas por la línea con menos ping")}</b><span>${t("Las partidas nuevas salen por la línea más rápida en ese momento; solo cambia si otra es claramente mejor varias veces seguidas, para que no haya saltos.")}</span></div>${toggle("smart", c.smart_gaming, 'id="smart"')}</div>
          </div>
          <details class="mt"><summary class="small">${t("Ajustes avanzados de la vigilancia")}</summary><div class="form-grid mt">
            <label class="field">${t("Destinos de comprobación")}<input id="targets" value="${esc((c.targets || []).join(", "))}"></label>
            <label class="field">${t("Comprobar cada (s)")}<input type="number" id="interval" min="1" max="30" value="${c.interval}"></label>
            <label class="field">${t("Fallos seguidos para darla por caída")}<input type="number" id="fail" min="2" max="20" value="${c.fail_after}"></label>
            <label class="field">${t("Respuestas seguidas para volver a usarla")}<input type="number" id="recover" min="2" max="60" value="${c.recover_after}"></label>
            <label class="field">${t("Pérdida máxima (%)")}<input type="number" id="loss" min="5" max="100" value="${c.max_loss}"></label>
            <label class="field">${t("Ping máximo (ms, 0 = sin límite)")}<input type="number" id="maxlat" min="0" max="5000" value="${c.max_latency}"></label></div></details>
          <button class="btn primary mt" id="save">${ico("check")}${t("Guardar")}</button></div>
        <div class="card"><header><h3>${ico("devices")}${t("Línea por dispositivo")}</h3></header>
          <p class="small muted mb">${t("Fija un equipo a una línea (p. ej. la consola por la fibra y la TV por el 4G). Si esa línea cae, usa las demás.")}</p>
          ${devices.length && wans.length ? `<div class="table-wrap" style="max-height:340px;overflow:auto"><table><tbody>${devices.map((d) => `<tr><td><b>${esc(d.name || d.hostname || d.vendor || d.ip)}</b><div class="small muted">${esc(d.ip)}</div></td>
            <td><select data-rule="${esc(d.mac)}"><option value="">${t("Automático")}</option>${wans.map((w) => `<option value="${w.id}" ${c.device_rules?.[d.mac] === w.id ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select></td></tr>`).join("")}</tbody></table></div>`
            : empty(wans.length ? t("Todavía no hay dispositivos detectados.") : t("Añade al menos una línea para asignar equipos."), "devices")}</div>
      </div>
      <div class="grid g2">
        <div class="card"><header><h3>${ico("list")}${t("Historial")}</h3></header>
          ${st.history.length ? `<div class="col">${st.history.slice(0, 15).map((h) => `<div class="row between small"><span>${h.kind === "down" ? "🔴" : h.kind === "up" ? "🟢" : "🧪"} ${esc(h.message)}</span><span class="muted">${ago(h.ts)}</span></div>`).join("")}</div>` : `<p class="muted small">${t("Sin cambios de línea todavía.")}</p>`}</div>
        <div class="card"><header><h3>${ico("help")}${t("¿Cómo conecto una segunda línea?")}</h3></header>
          <ol class="small" style="padding-left:18px;line-height:1.7">
            <li><b>${t("Otro router en tu misma red (un solo cable):")}</b> ${t("conéctalo al switch o al Huawei, ponle otra IP (p. ej. 192.168.18.2) y apaga su DHCP. Añádelo como línea con esa IP como puerta de enlace.")}</li>
            <li><b>${t("Módem 4G/5G o el móvil por USB:")}</b> ${t("conéctalo al servidor Proxmox y crea un puente con él; luego en Interfaces de red añade una tarjeta a Bipon en ese puente.")}</li>
            <li><b>${t("Segunda tarjeta de red:")}</b> ${t("conecta ahí el otro proveedor y elige esa interfaz con puerta de enlace «auto».")}</li></ol>
          <a class="btn sm mt" href="#/interfaces">${ico("network")}${t("Interfaces de red")}</a></div>
      </div>`;
    el.querySelector("#mw-en").onchange = (e) => save({ enabled: e.target.checked }, e.target.checked ? "Multi-WAN activado" : "Multi-WAN desactivado").catch((err) => { toastErr(err); e.target.checked = !e.target.checked; });
    el.querySelectorAll("input[name=mode]").forEach((r) => (r.onchange = () => el.querySelector("#bal").classList.toggle("hidden", r.value !== "balance" || !r.checked)));
    el.querySelector("#save").onclick = (e) => busy(e.currentTarget, () => save({
      mode: el.querySelector("input[name=mode]:checked").value, balance_by: el.querySelector("#by").value, smart_gaming: el.querySelector("#smart").checked,
      targets: el.querySelector("#targets").value.split(/[\s,]+/).filter(Boolean), interval: Number(el.querySelector("#interval").value),
      fail_after: Number(el.querySelector("#fail").value), recover_after: Number(el.querySelector("#recover").value),
      max_loss: Number(el.querySelector("#loss").value), max_latency: Number(el.querySelector("#maxlat").value),
    })).catch(() => {});
    el.querySelector("#add").onclick = () => modal({ title: t("Añadir línea de Internet"), body: wanForm(), wide: true,
      actions: [{ label: t("Cancelar") }, { label: t("Añadir"), kind: "primary", onClick: async (m) => { await save({ wans: [...cfg().wans, readForm(m)] }, "Línea añadida"); } }] });
  };

  on(el, "click", "[data-edit]", (e, b) => {
    const w = cfg().wans.find((x) => x.id === b.dataset.edit);
    modal({ title: `${t("Editar")} ${w.name}`, body: wanForm(w), wide: true,
      actions: [{ label: t("Cancelar") }, { label: t("Guardar"), kind: "primary", onClick: async (m) => { await save({ wans: cfg().wans.map((x) => (x.id === w.id ? readForm(m, w) : x)) }); } }] });
  });
  on(el, "click", "[data-del]", async (e, b) => {
    const w = cfg().wans.find((x) => x.id === b.dataset.del);
    if (!(await confirmDialog(t("Eliminar línea"), `${t("Se eliminará")} «${w.name}». ${t("Sus equipos pasarán a las demás líneas.")}`))) return;
    const rules = Object.fromEntries(Object.entries(cfg().device_rules || {}).filter(([, v]) => v !== w.id));
    save({ wans: cfg().wans.filter((x) => x.id !== w.id), device_rules: rules }, "Línea eliminada").catch(toastErr);
  });
  on(el, "click", "[data-sim]", async (e, b) => {
    if (!(await confirmDialog(t("Simular caída"), t("Durante 60 segundos Bipon tratará esta línea como caída: verás cómo el tráfico pasa a la otra y vuelve solo."), { danger: false, label: t("Probar") }))) return;
    busy(b, () => api.post(`/multiwan/${b.dataset.sim}/simulate`, { seconds: 60 })).then(async () => { toast(t("Prueba en marcha: 60 s")); st = await api.get("/multiwan"); draw(); }).catch(() => {});
  });
  on(el, "click", "[data-restore]", (e, b) => busy(b, () => api.post(`/multiwan/${b.dataset.restore}/restore`)).then(async () => { st = await api.get("/multiwan"); draw(); }).catch(() => {}));
  on(el, "change", "[data-rule]", (e, s) => {
    const rules = { ...(cfg().device_rules || {}) };
    if (s.value) rules[s.dataset.rule] = s.value; else delete rules[s.dataset.rule];
    save({ device_rules: rules }).catch(toastErr);
  });

  draw();
  timer = setInterval(async () => {
    if (!document.body.contains(el)) return clearInterval(timer);
    if (el.contains(document.activeElement) && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    const fresh = await api.get("/multiwan").catch(() => null);
    if (fresh) { st = fresh; draw(); }
  }, 3000);
  return () => clearInterval(timer);
}
