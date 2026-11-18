// Calidad de conexión: Wi-Fi débil por equipo, mapa de lag, ruta a tu juego e informe para el proveedor.
import { api, esc, ico, ms, ago, toast, busy, on, empty, lineChart, COLORS } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Calidad de conexión";
export const subtitle = "Señal de cada equipo, horas con más lag, ruta a tu juego e informe para tu proveedor";

const STATUS = { "débil": ["err", "Señal débil"], "sin respuesta": ["", "Sin respuesta"], "regular": ["warn", "Regular"], "buena": ["ok", "Buena"] };
const color = (v, med) => {
  if (v == null) return "transparent";
  const r = med ? v / med : 1;
  return r < 1.15 ? "rgba(34,197,94,.55)" : r < 1.5 ? "rgba(234,179,8,.6)" : r < 2.2 ? "rgba(249,115,22,.7)" : "rgba(239,68,68,.8)";
};

export async function render(el) {
  const charts = [];
  el.innerHTML = `
    <div class="card mb" id="q-lan"></div>
    <div class="card mb" id="q-trace"></div>
    <div class="card mb" id="q-heat"></div>
    <div class="grid g2">
      <div class="card" id="q-route"></div>
      <div class="card" id="q-report"></div>
    </div>`;
  const boxLan = el.querySelector("#q-lan"), boxHeat = el.querySelector("#q-heat"), boxRoute = el.querySelector("#q-route"), boxRep = el.querySelector("#q-report");

  // ------------------------------------------------------------ señal de cada equipo
  const drawLan = (s) => {
    const c = s.counts || {};
    boxLan.innerHTML = `<header><h2>${ico("wifi")}${t("Señal de cada equipo")}</h2><button class="btn sm" id="lan-run">${ico("refresh")}${t("Medir ahora")}</button></header>
      <p class="muted small mb">${t("Bipon hace ping a cada equipo dentro de tu red cada 10 minutos. En una red sana el mínimo es menor de 5 ms; si es mayor, el problema es la señal Wi-Fi o el cable, no tu proveedor. Los móviles en reposo tardan en el primer ping: por eso se usa el mínimo.")}</p>
      <div class="row mb small" style="gap:14px"><span class="badge err">${c["débil"] || 0} ${t("débil")}</span><span class="badge warn">${c.regular || 0} ${t("regular")}</span><span class="badge ok">${c.buena || 0} ${t("buena")}</span><span class="muted">${s.ts ? `${t("Medido")} ${ago(s.ts)}` : t("Aún sin medir")}</span></div>
      ${s.devices.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Dispositivo")}</th><th>${t("Señal")}</th><th class="right">${t("Mínimo")}</th><th class="right">${t("Media")}</th><th class="right">Jitter</th><th class="right">${t("Pérdida")}</th></tr></thead><tbody>
        ${s.devices.map((d) => `<tr><td><b>${esc(d.name || d.ip)}</b><div class="small muted">${esc(d.ip)}${d.brand ? " · " + esc(d.brand) : ""}${d.wifi ? " · Wi-Fi" : ""}</div></td>
          <td><span class="badge ${STATUS[d.status]?.[0] || ""}">${t(STATUS[d.status]?.[1] || d.status)}</span></td>
          <td class="right">${ms(d.min)}</td><td class="right">${ms(d.avg)}</td><td class="right">${ms(d.jitter)}</td><td class="right">${d.loss ?? "—"} %</td></tr>`).join("")}</tbody></table></div>
        ${(c["débil"] || 0) ? `<div class="alert warn mt">${ico("wifi")}<div class="small">${t("Para los equipos con señal débil: acércalos al router o a un repetidor, usa la banda de 5 GHz, o mejor aún conéctalos por cable (consolas y PC de juego).")}</div></div>` : ""}`
      : empty(t("Pulsa «Medir ahora» para analizar la señal de cada equipo."), "wifi")}`;
    boxLan.querySelector("#lan-run").onclick = (e) => busy(e.currentTarget, () => api.post("/quality/lan")).then(drawLan).catch(() => {});
  };
  drawLan(await api.get("/quality/lan"));

  // ------------------------------------------------------------ mapa de lag por hora
  const drawHeat = async (days = 14) => {
    const h = await api.get(`/quality/heatmap?days=${days}`);
    const order = [1, 2, 3, 4, 5, 6, 0];
    const has = h.grid.some((r) => r.some(Boolean));
    boxHeat.innerHTML = `<header><h2>${ico("clock")}${t("A qué horas sube el ping")}</h2>
      <div class="btn-group" id="hm-days">${[7, 14, 30].map((d) => `<button data-d="${d}" class="${d === days ? "active" : ""}">${d} ${t("días")}</button>`).join("")}</div></header>
      <p class="muted small mb">${t("Latencia media a Internet por día y hora. Verde = lo normal de tu línea; rojo = el doble o más. Si siempre es rojo a la misma hora, es saturación de tu proveedor en horas punta.")}</p>
      ${has ? `<div style="overflow-x:auto"><table class="heatmap"><thead><tr><th></th>${Array.from({ length: 24 }, (_, i) => `<th>${i}</th>`).join("")}</tr></thead><tbody>
        ${order.map((d) => `<tr><th class="small">${t(h.day_names[d]).slice(0, 3)}</th>${h.grid[d].map((c, hr) => `<td title="${t(h.day_names[d])} ${hr}:00 · ${c ? ms(c.ms) + (c.loss ? ` · ${c.loss}% ${t("pérdida")}` : "") : "—"}" style="background:${color(c?.ms, h.median)}"></td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <div class="row mt small" style="gap:16px;flex-wrap:wrap"><span class="muted">${t("Normal")}: <b>${ms(h.median)}</b></span>
          ${h.worst.length ? `<span class="muted">${t("Peores momentos")}: ${h.worst.slice(0, 3).map((w) => `<b>${t(w.day)} ${w.hour}:00</b> (${ms(w.ms)})`).join(", ")}</span>` : ""}</div>`
      : empty(t("Aún no hay suficientes mediciones. El monitor de conexión las va guardando cada minuto."), "clock")}`;
    on(boxHeat, "click", "[data-d]", (e, b) => drawHeat(Number(b.dataset.d)));
  };
  await drawHeat();

  // ------------------------------------------------------------ ruta a la región del juego
  const drawRoute = async (data) => {
    const r = data || (await api.get("/quality/route"));
    charts.forEach((c) => c.destroy()); charts.length = 0;
    boxRoute.innerHTML = `<header><h2>${ico("console")}${t("Ruta a tu juego")}</h2><button class="btn sm" id="rt-run">${ico("activity")}${t("Medir ahora")}</button></header>
      <p class="muted small mb">${t("Cada 3 horas se mide el ping a la región de servidores de tu juego. Si sube mucho de repente, tu proveedor cambió la ruta y te avisamos.")}</p>
      <label class="field">${t("Región de tu juego")}<select id="rt-reg"><option value="">${t("Automática (la más cercana)")}</option>${r.regions.map((x) => `<option value="${x.code}" ${x.code === r.region ? "selected" : ""}>${esc(t(x.name))}</option>`).join("")}</select></label>
      ${data?.result ? `<div class="alert ${data.result.alert ? "warn" : "ok"} mt small">${ico(data.result.alert ? "alert" : "check")}<div>${ms(data.result.ms)}${data.result.median ? ` (${t("normal")} ${ms(data.result.median)})` : ""}</div></div>` : ""}
      ${r.series.length ? `<div class="chart chart-sm mt"><canvas id="rt-chart"></canvas></div>` : `<p class="muted small mt">${t("Sin mediciones todavía.")}</p>`}`;
    const cv = boxRoute.querySelector("#rt-chart");
    if (cv) {
      const s = r.series.find((x) => x.region === r.region) || r.series.sort((a, b) => b.points.length - a.points.length)[0];
      charts.push(lineChart(cv, { labels: s.points.map((p) => p[0]), xFmt: (x) => new Date(x * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit" }),
        yFmt: (v) => `${Math.round(v)} ms`, series: [{ name: t(s.name), color: COLORS.cyan, values: s.points.map((p) => p[1]) }] }));
    }
    const run = (body) => (e) => busy(e.currentTarget || boxRoute, () => api.post("/quality/route", body)).then(drawRoute).catch(() => {});
    boxRoute.querySelector("#rt-run").onclick = run({ game_region: boxRoute.querySelector("#rt-reg").value });
    boxRoute.querySelector("#rt-reg").onchange = (e) => api.post("/quality/route", { game_region: e.target.value }).then((x) => { toast(t("Guardado")); drawRoute(x); }).catch(() => {});
  };
  await drawRoute();

  // ------------------------------------------------------------ ruta salto a salto (MTR)
  const boxTr = el.querySelector("#q-trace");
  const SEG = { casa: ["info", "Tu casa"], proveedor: ["warn", "Tu proveedor"], internet: ["", "Internet / destino"], "?": ["", "Sin identificar"] };
  const CUL = { casa: "err", proveedor: "warn", internet: "warn" };
  const drawTrace = async (fresh) => {
    const s = fresh ? { ...(await api.get("/quality/trace")), latest: fresh.latest } : await api.get("/quality/trace");
    const sum = s.summary?.culprits || {};
    boxTr.innerHTML = `<header><h2>${ico("network")}${t("Ruta salto a salto")}</h2><button class="btn sm" id="tr-run" ${s.available ? "" : "disabled"}>${ico("activity")}${t("Medir ahora")}</button></header>
      <p class="muted small mb">${t("Cada 15 minutos Bipon sigue el camino de tus paquetes router a router (como mtr) e identifica a qué operador pertenece cada salto. Así se ve si el lag empieza en tu casa, en tu proveedor o más allá. Un salto lento que no se mantiene hasta el destino se ignora (muchos routers responden lento a propósito a estas pruebas).")}</p>
      ${!s.available ? `<div class="alert warn small">${ico("alert")}<div>${t("Falta la herramienta mtr: se instala al actualizar Bipon (paquete mtr-tiny).")}</div></div>` : ""}
      ${s.summary?.runs ? `<p class="small mb">${t("Últimos 7 días")}: ${s.summary.runs} ${t("mediciones")} · ${t("culpa de tu casa")} ${sum.casa || 0} · ${t("de tu proveedor")} ${sum.proveedor || 0} · ${t("de Internet")} ${sum.internet || 0}</p>` : ""}
      ${(s.latest || []).length ? (s.latest || []).map((r) => `<div class="rec mt">
        <div class="row between"><b>${esc(t(r.label || r.target))}${r.wan ? ` · ${esc(r.wan)}` : ""}</b><span class="small muted">${r.ts ? ago(r.ts) : ""}${r.isp ? " · " + esc(r.isp) : ""}</span></div>
        <div class="alert ${r.culprit ? CUL[r.culprit] || "warn" : "ok"} small mt">${ico(r.culprit ? "alert" : "check")}<div>${esc(r.verdict)}</div></div>
        <div class="table-wrap mt"><table class="small"><thead><tr><th>#</th><th>IP</th><th>${t("Operador")}</th><th>${t("Tramo")}</th><th>${t("Media")}</th><th>${t("Peor")}</th><th>${t("Pérdida")}</th></tr></thead><tbody>
          ${r.hops.map((h) => { const [c, l] = SEG[h.segment] || SEG["?"]; return `<tr ${h.hop === r.lag_hop || h.hop === r.loss_hop ? 'style="background:rgba(239,68,68,.12)"' : ""}>
            <td>${h.hop}</td><td class="mono">${esc(h.ip || "* * *")}</td><td>${esc(h.org || (h.asn ? "AS" + h.asn : ""))}</td><td><span class="badge ${c}">${t(l)}</span></td>
            <td>${ms(h.avg)}</td><td>${ms(h.worst)}</td><td>${h.loss ? h.loss + " %" : "0"}</td></tr>`; }).join("")}</tbody></table></div></div>`).join("")
        : `<p class="muted small">${t("Aún no hay mediciones: pulsa «Medir ahora» (tarda unos 10 s).")}</p>`}`;
    boxTr.querySelector("#tr-run").onclick = (e) => busy(e.currentTarget, () => api.post("/quality/trace")).then(drawTrace).catch(() => {});
  };
  await drawTrace().catch(() => { boxTr.innerHTML = ""; });

  // ------------------------------------------------------------ informe para el proveedor
  const rep = await api.get("/quality/report?days=30");
  const s = rep.summary;
  boxRep.innerHTML = `<header><h2>${ico("list")}${t("Informe para tu proveedor")}</h2></header>
    <p class="muted small mb">${t("Cortes, latencia, pérdida y velocidad con fechas, medidos automáticamente. Úsalo como prueba al reclamar.")}</p>
    <dl class="kv small"><dt>${t("Cortes (30 días)")}</dt><dd>${s.outages} · ${s.downtime_min} min</dd><dt>${t("Latencia media")}</dt><dd>${ms(s.avg_latency)}</dd>
      <dt>${t("Pérdida media")}</dt><dd>${s.avg_loss ?? "—"} %</dd><dt>${t("Bajada media")}</dt><dd>${s.avg_download != null ? s.avg_download + " Mbps" : "—"}</dd></dl>
    <div class="form-grid mt"><label class="field">${t("Periodo (días)")}<input type="number" id="rp-days" value="30" min="1" max="365"></label>
      <label class="field">${t("Velocidad contratada (Mbps, opcional)")}<input type="number" id="rp-con" min="0"></label></div>
    <div class="row mt"><button class="btn primary" id="rp-html">${ico("external")}${t("Ver / imprimir PDF")}</button><button class="btn" id="rp-csv">${ico("download")}CSV</button></div>`;
  const q = () => `days=${Number(boxRep.querySelector("#rp-days").value) || 30}&contracted=${Number(boxRep.querySelector("#rp-con").value) || 0}`;
  boxRep.querySelector("#rp-html").onclick = () => window.open(`/api/quality/report?format=html&${q()}`, "_blank");
  boxRep.querySelector("#rp-csv").onclick = () => { location.href = `/api/quality/report?format=csv&${q()}`; };

  return () => charts.forEach((c) => c.destroy());
}
