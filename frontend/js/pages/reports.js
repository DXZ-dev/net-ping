import { api, esc, ico, ms, mbps, num, toast, busy, empty, toggle, on, datetime, $$ } from "../lib.js";
import { t } from "../i18n.js";
import { appIcon } from "../brands.js";

export const title = "Informes";
export const subtitle = "Resumen semanal de la red: uso, tiempo de pantalla, seguridad y velocidad";

const DAYS = ["L", "M", "X", "J", "V", "S", "D"];

export async function render(el, app) {
  const [list, sched] = await Promise.all([api.get("/reports"), api.get("/schedule")]);
  el.innerHTML = `<div class="grid g3">
    <div class="card"><header><h3>${ico("clock")}${t("Programación")}</h3></header>
      <div class="opt"><div class="t"><b>${t("Informe semanal")}</b><span>${t("Se envía por tus canales de notificación.")}</span></div>${toggle("r", sched.reports.enabled, 'id="r-en"')}</div>
      <div class="days mt">${DAYS.map((d, i) => `<span class="chip ${sched.reports.days.includes(i) ? "sel" : ""}" data-day="${i}">${d}</span>`).join("")}</div>
      <label class="field mt">${t("Hora")}<input type="time" id="r-t" value="${esc(sched.reports.time)}"></label>
      <h4 class="mt mb">${t("Test de velocidad automático")}</h4>
      <div class="opt"><div class="t"><b>${t("Cada noche")}</b><span>${t("Avisa si baja de lo contratado.")}</span></div>${toggle("s", sched.speed.enabled, 'id="s-en"')}</div>
      <div class="form-grid"><label class="field">${t("Hora")}<input type="time" id="s-t" value="${esc(sched.speed.time)}"></label>
        <label class="field">${t("Bajada mínima (Mbps)")}<input type="number" id="s-d" value="${sched.speed.min_download || ""}"></label>
        <label class="field">${t("Subida mínima (Mbps)")}<input type="number" id="s-u" value="${sched.speed.min_upload || ""}"></label>
        <label class="field">${t("Ping máximo (ms)")}<input type="number" id="s-p" value="${sched.speed.max_ping || ""}"></label></div>
      <button class="btn primary mt" id="save">${ico("check")}${t("Guardar")}</button></div>
    <div class="card span2"><header><h2>${ico("list")}${t("Informes")}</h2><button class="btn sm" id="gen">${ico("zap")}${t("Generar ahora")}</button><button class="btn sm" id="gen-send">${ico("upload")}${t("Generar y enviar")}</button></header>
      <div id="report">${list.length ? "" : empty(t("Aún no hay informes. Pulsa «Generar ahora»."), "list")}</div>
      ${list.length ? `<div class="chips mt">${list.map((r, i) => `<span class="chip ${i === 0 ? "sel" : ""}" data-rep="${r.id}">${new Date(r.start * 1000).toLocaleDateString()} – ${new Date(r.end * 1000).toLocaleDateString()}</span>`).join("")}</div>` : ""}</div></div>`;
  on(el, "click", "[data-day]", (e, b) => b.classList.toggle("sel"));
  el.querySelector("#save").onclick = (e) => busy(e.currentTarget, () => api.put("/schedule", {
    reports: { enabled: el.querySelector("#r-en").checked, days: $$("[data-day].sel", el).map((x) => Number(x.dataset.day)), time: el.querySelector("#r-t").value },
    speed: { enabled: el.querySelector("#s-en").checked, time: el.querySelector("#s-t").value, days: [0, 1, 2, 3, 4, 5, 6], min_download: Number(el.querySelector("#s-d").value) || null,
      min_upload: Number(el.querySelector("#s-u").value) || null, max_ping: Number(el.querySelector("#s-p").value) || null },
  })).then(() => toast(t("Guardado"))).catch(() => {});
  const show = async (id) => { const r = await api.get(`/reports/${id}`); draw(r.data); $$("[data-rep]", el).forEach((x) => x.classList.toggle("sel", x.dataset.rep === String(id))); };
  on(el, "click", "[data-rep]", (e, b) => show(b.dataset.rep));
  const gen = (send) => (e) => busy(e.currentTarget, async () => { const d = await api.post("/reports/generate", { send }); if (send) toast(t("Informe enviado")); app.reload(); }).catch(() => {});
  el.querySelector("#gen").onclick = gen(false);
  el.querySelector("#gen-send").onclick = gen(true);

  function draw(d) {
    const q = d.queries;
    const inet = d.connection.filter((c) => !["gateway", "dns"].includes(c.target));
    el.querySelector("#report").innerHTML = `<p class="muted small">${datetime(d.start)} – ${datetime(d.end)}</p>
      <div class="grid g4 mt mb">${[["Consultas", num(q.total)], ["Bloqueadas", `${num(q.blocked)} (${q.blocked_pct} %)`], ["Amenazas", String(d.threats)], ["Nuevos dispositivos", String(d.new_devices.length)],
        ["Bajada media", mbps(d.speed.down)], ["Subida media", mbps(d.speed.up)], ["Ping medio", ms(d.speed.ping)], ["Cortes", String(d.outages.length)]]
        .map(([l, v]) => `<div class="big-metric"><div class="v" style="font-size:20px">${esc(v)}</div><div class="l">${t(l)}</div></div>`).join("")}</div>
      <div class="grid g2">
        <div><h4 class="mb">${t("Tiempo de Internet por dispositivo (media/día)")}</h4>${d.screen_time.map((s) => `<div class="row between small"><span>${esc(s.name)}</span><b>${Math.floor(s.avg_day / 60)} h ${s.avg_day % 60} min</b></div>`).join("") || `<p class="muted small">—</p>`}</div>
        <div><h4 class="mb">${t("Apps más usadas")}</h4>${d.top_apps.map((a) => `<div class="row small" style="flex-wrap:nowrap">${appIcon(a.service, a.name)}<span class="grow">${esc(a.name)}</span><b>${(a.minutes / 60).toFixed(1)} h</b></div>`).join("") || `<p class="muted small">—</p>`}</div>
        <div><h4 class="mb mt">${t("Dominios más bloqueados")}</h4>${d.top_blocked.map((b) => `<div class="row between small"><span class="mono">${esc(b.domain)}</span><b>${num(b.n)}</b></div>`).join("") || "—"}</div>
        <div><h4 class="mb mt">${t("Conexión")}</h4>${inet.map((c) => `<div class="row between small"><span>${esc(c.target)}</span><span>${ms(c.latency)} · ${t("pérdida")} ${c.loss} % · ${t("disponibilidad")} <b>${(100 - c.down).toFixed(2)} %</b></span></div>`).join("") || "—"}</div></div>`;
  }
  if (list.length) show(list[0].id);
}
