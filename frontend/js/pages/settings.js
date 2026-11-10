import { api, esc, ico, toast, toastErr, busy, toggle, ago, datetime } from "../lib.js";

export const title = "Ajustes";
export const subtitle = "Seguridad, escaneo, monitor, retención y copias de seguridad";

export async function render(el) {
  const [s, sys, events] = await Promise.all([api.get("/settings"), api.get("/system"), api.get("/events?limit=100")]);
  el.innerHTML = `
    <div class="grid g2">
      <form class="card" id="pw"><header><h2>${ico("key")}Contraseña de administrador</h2></header>
        <div class="col"><label class="field">Contraseña actual<input type="password" name="current" required autocomplete="current-password"></label>
        <label class="field">Nueva contraseña<input type="password" name="new" minlength="8" required autocomplete="new-password"></label>
        <label class="field">Repite la nueva<input type="password" name="new2" minlength="8" required autocomplete="new-password"></label>
        <div><button class="btn primary">${ico("check")}Cambiar</button></div></div><p class="muted small mt">Cambiarla cierra todas las demás sesiones.</p></form>

      <form class="card" id="gen"><header><h2>${ico("settings")}General</h2></header>
        <div class="form-grid">
          <label class="field">Escaneo de dispositivos cada (s)<input type="number" name="scan_interval" min="60" value="${s.devices.scan_interval}"></label>
          <label class="field">Retención de datos (días)<input type="number" name="retention_days" min="1" max="365" value="${s.retention_days}"></label>
          <label class="field">Medición del monitor cada (s)<input type="number" name="interval" min="15" value="${s.monitor.interval}"></label>
          <label class="field">Destinos del monitor<input name="targets" value="${esc(s.monitor.targets.join(", "))}"></label>
          <label class="field">Subredes a escanear<input name="subnets" value="${esc((s.devices.subnets || []).join(", "))}" placeholder="automático"></label></div>
        <div class="opt"><div class="t"><b>Resolver nombres de dispositivos</b><span>Pregunta al router el nombre de cada equipo (PTR).</span></div>${toggle("resolve_names", s.devices.resolve_names)}</div>
        <div class="opt"><div class="t"><b>Monitor de conexión</b><span>Mide latencia y pérdida continuamente.</span></div>${toggle("monitor_enabled", s.monitor.enabled)}</div>
        <button class="btn primary mt">${ico("check")}Guardar</button></form>
    </div>
    <div class="grid g2 mt">
      <div class="card"><header><h2>${ico("download")}Copia de seguridad</h2></header>
        <p class="muted small">Exporta listas, reglas, perfiles, nombres de dispositivos y toda la configuración (sin contraseña ni claves VPN).</p>
        <div class="row mt"><a class="btn primary" href="/api/backup">${ico("download")}Descargar copia</a>
          <label class="btn">${ico("upload")}Restaurar…<input type="file" accept=".json" id="restore" class="hidden"></label></div></div>
      <div class="card"><header><h2>${ico("info")}Acerca de</h2></header>
        <dl class="kv"><dt>Versión</dt><dd>NetPing ${esc(sys.version)}</dd><dt>Sistema</dt><dd>${esc(sys.platform)}</dd><dt>Python</dt><dd>${esc(sys.python)}</dd>
        <dt>IP del servidor</dt><dd>${esc(sys.ip || "")}</dd><dt>Puerto DNS</dt><dd>${esc(sys.dns.port)}</dd><dt>API</dt><dd><a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></dd></dl>
        <div class="alert info mt">${ico("info")}<div class="small">Para que toda la red use NetPing, configura en tu router el DNS del DHCP como <b>${esc(sys.ip || "IP de este equipo")}</b> (sin DNS secundario externo, o los anuncios se colarán).</div></div></div>
    </div>
    <div class="card mt"><header><h2>${ico("list")}Registro de eventos</h2></header>
      <div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Nivel</th><th>Categoría</th><th>Mensaje</th></tr></thead><tbody>
      ${events.map((e) => `<tr><td class="nowrap muted">${datetime(e.ts)}</td><td><span class="badge ${{ info: "info", warning: "warn", error: "err" }[e.level]}">${esc(e.level)}</span></td><td>${esc(e.category)}</td><td>${esc(e.message)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Sin eventos</td></tr>`}</tbody></table></div></div>`;

  el.querySelector("#pw").onsubmit = (e) => {
    e.preventDefault(); const f = e.target;
    if (f.new.value !== f.new2.value) return toast("Las contraseñas no coinciden", "error");
    busy(f.querySelector("button"), () => api.post("/auth/password", { current: f.current.value, new: f.new.value })).then(() => { toast("Contraseña cambiada"); f.reset(); }).catch(() => {});
  };
  el.querySelector("#gen").onsubmit = (e) => {
    e.preventDefault(); const f = e.target;
    busy(f.querySelector("button"), () => api.put("/settings", {
      devices: { scan_interval: Number(f.scan_interval.value), resolve_names: f.resolve_names.checked, subnets: f.subnets.value.split(/[\s,]+/).filter(Boolean) },
      monitor: { interval: Number(f.interval.value), enabled: f.monitor_enabled.checked, targets: f.targets.value.split(/[\s,]+/).filter(Boolean) },
      retention_days: Number(f.retention_days.value),
    })).then(() => toast("Ajustes guardados")).catch(() => {});
  };
  el.querySelector("#restore").onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    try { await api.post("/restore", JSON.parse(await file.text())); toast("Copia restaurada. Las listas se están descargando."); } catch (err) { toastErr(err); }
  };
}
