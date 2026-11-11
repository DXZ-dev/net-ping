// Vigilante de lag en vivo + «Proteger mi partida».
import { api, esc, ico, ms, ago, toast, busy } from "../lib.js";
import { t } from "../i18n.js";

const spark = (hist, base) => {
  const pts = hist.filter((x) => x[1] != null);
  if (pts.length < 2) return `<p class="muted small">${t("Midiendo… (el gráfico aparece en unos segundos)")}</p>`;
  const W = 600, H = 70, max = Math.max(30, ...pts.map((p) => p[1])) * 1.15, t0 = hist[0][0], t1 = hist[hist.length - 1][0] || t0 + 1;
  const x = (ts) => ((ts - t0) / Math.max(1, t1 - t0)) * W, y = (v) => H - (v / max) * H;
  const line = pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const lost = hist.filter((p) => p[1] == null).map((p) => `<line x1="${x(p[0])}" x2="${x(p[0])}" y1="0" y2="${H}" stroke="var(--err)" stroke-width="2" opacity=".6"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:70px;display:block">
    ${base ? `<line x1="0" x2="${W}" y1="${y(base)}" y2="${y(base)}" stroke="var(--ok)" stroke-dasharray="4 4" opacity=".7"/>` : ""}${lost}
    <polyline points="${line}" fill="none" stroke="var(--accent-2)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
};

const grade = (s) => {
  if (s.ping == null) return ["err", t("Sin respuesta")];
  const d = s.baseline ? s.ping - s.baseline : 0;
  if (s.loss > 2 || d > 60) return ["err", t("Lag alto")];
  if (d > 20 || (s.jitter ?? 0) > 15) return ["warn", t("Inestable")];
  return ["ok", t("Estable")];
};

export function mountLag(box) {
  if (!box) return;
  let devices = null;
  const draw = async () => {
    const s = await api.get("/lag").catch(() => null);
    if (!s || !document.body.contains(box)) return;
    if (!devices) devices = (await api.get("/devices").catch(() => ({ devices: [] }))).devices.filter((d) => d.online && d.ip && !d.is_gateway);
    const [cls, label] = grade(s);
    const p = s.protect;
    const busyBtn = box.querySelector(":focus");
    if (busyBtn && box.contains(busyBtn) && busyBtn.tagName === "SELECT") return;  // no redibujar mientras eliges
    box.innerHTML = `<div class="card"><header><h2>${ico("activity")}${t("Vigilante de lag")} <span class="live-dot"></span></h2><span class="badge ${cls}">${label}</span></header>
      <div class="grid g4 small">
        <div><div class="muted">${t("Ping ahora")}</div><div style="font-size:26px;font-weight:700">${ms(s.ping)}</div></div>
        <div><div class="muted">${t("Normal de tu línea")}</div><div style="font-size:20px;font-weight:600">${ms(s.baseline)}</div></div>
        <div><div class="muted">${t("Jitter (variación)")}</div><div style="font-size:20px;font-weight:600">${s.jitter != null ? s.jitter + " ms" : "—"}</div></div>
        <div><div class="muted">${t("Pérdida (5 min)")}</div><div style="font-size:20px;font-weight:600">${s.loss} %</div></div></div>
      <div class="mt">${spark(s.history, s.baseline)}</div>
      <div class="grid g2 mt">
        <div><h4 class="mb">${t("Qué está cargando la red ahora")}</h4>
          ${s.culprits.length ? s.culprits.slice(0, 5).map((c) => `<div class="row between small rec mb"><span class="truncate"><b>${esc(c.name)}</b> <span class="muted">${esc(c.ip)}</span></span>
            <span class="muted nowrap">${esc(c.reasons.map((r) => t(r)).join(", ") || "")}${c.mbps ? ` · ${c.mbps} Mbps` : ""}</span></div>`).join("")
            : `<p class="muted small">${t("Nada pesado ahora mismo: ni descargas, ni actualizaciones, ni vídeo intenso.")}</p>`}
          ${s.spikes.length ? `<p class="small muted mt">${t("Último pico")}: ${ago(s.spikes[0].ts)} · ${ms(s.spikes[0].ms)} (${t("normal")} ${ms(s.spikes[0].base)})${s.spikes[0].culprits[0] ? ` · ${esc(s.spikes[0].culprits[0].name)}` : ""}</p>` : ""}</div>
        <div><h4 class="mb">${ico("console")}${t("Proteger mi partida")}</h4>
          ${p ? `<div class="alert ok">${ico("check")}<div class="small"><b>${t("Activo")}</b> · ${t("hasta")} ${new Date(p.until * 1000).toLocaleTimeString()} · ${p.applied} ${t("dispositivos limitados a")} ${p.limit} Mbps</div></div>
              <button class="btn mt" id="lg-stop">${ico("x")}${t("Terminar")}</button>`
            : `<p class="small muted mb">${t("Mientras juegas, limita temporalmente al resto de dispositivos para que nadie dispare tu ping, y deja listo el DNS de los juegos. Se deshace solo al terminar.")}</p>
              <div class="form-grid"><label class="field">${t("Mi consola / PC")}<select id="lg-dev"><option value="">${t("Este dispositivo")}</option>${devices.map((d) => `<option value="${esc(d.mac)}">${esc(d.name || d.friendly || d.hostname || d.ip)} · ${esc(d.ip)}</option>`).join("")}</select></label>
                <label class="field">${t("Duración")}<select id="lg-min">${[30, 60, 120, 180].map((m) => `<option value="${m}" ${m === 60 ? "selected" : ""}>${m} min</option>`).join("")}</select></label>
                <label class="field">${t("Límite para los demás")}<select id="lg-lim">${[2, 5, 10, 20].map((m) => `<option value="${m}" ${m === 5 ? "selected" : ""}>${m} Mbps</option>`).join("")}</select></label></div>
              <button class="btn primary mt" id="lg-go">${ico("zap")}${t("Proteger mi partida")}</button>`}
        </div></div></div>`;
    const go = box.querySelector("#lg-go");
    if (go) go.onclick = () => busy(go, () => api.post("/lag/protect", { macs: box.querySelector("#lg-dev").value ? [box.querySelector("#lg-dev").value] : [],
      minutes: Number(box.querySelector("#lg-min").value), limit_mbps: Number(box.querySelector("#lg-lim").value) }))
      .then((r) => { toast(r.applied ? `${t("Partida protegida")}: ${r.applied} ${t("dispositivos limitados")}` : t("Activado: DNS de juegos listo. No hay dispositivos que se puedan limitar con tu router actual (ver Router y firewall → Velocidad por dispositivo)."), r.applied ? "ok" : "info", 8000); draw(); }).catch(() => {});
    const stop = box.querySelector("#lg-stop");
    if (stop) stop.onclick = () => busy(stop, () => api.del("/lag/protect")).then(() => { toast(t("Protección terminada: límites restaurados")); draw(); }).catch(() => {});
  };
  draw();
  const timer = setInterval(() => { if (!document.body.contains(box)) return clearInterval(timer); draw(); }, 3000);
}
