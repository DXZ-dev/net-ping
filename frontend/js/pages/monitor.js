import { api, esc, ico, ms, pct, lineChart, COLORS, PALETTE, busy, toast } from "../lib.js";

export const title = "Monitor de conexión";
export const subtitle = "Latencia, pérdida y disponibilidad medidas continuamente";

export async function render(el) {
  let hours = 24;
  const charts = [];
  el.innerHTML = `
    <div class="row mb"><div class="btn-group" id="rng">${[[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 días"], [720, "30 días"]].map(([v, l]) => `<button data-h="${v}" class="${v === hours ? "active" : ""}">${l}</button>`).join("")}</div>
      <span class="grow"></span><button class="btn" id="now">${ico("activity")}Medir ahora</button></div>
    <div class="grid g4 mb" id="sum"></div>
    <div class="card mb"><header><h2>${ico("activity")}Latencia</h2></header><div class="legend mb" id="leg"></div><div class="chart" style="height:300px"><canvas id="lat"></canvas></div></div>
    <div class="card"><header><h2>${ico("alert")}Pérdida de paquetes</h2></header><div class="chart chart-sm"><canvas id="loss"></canvas></div></div>`;
  el.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => { hours = Number(b.dataset.h); el.querySelectorAll("[data-h]").forEach((x) => x.classList.toggle("active", x === b)); load(); });
  el.querySelector("#now").onclick = (e) => busy(e.currentTarget, () => api.post("/monitor/sample")).then(() => { toast("Medición registrada"); load(); }).catch(() => {});

  const label = (k) => k === "gateway" ? "Router" : k === "dns" ? "Resolución DNS" : k;
  async function load() {
    const d = await api.get(`/monitor?hours=${hours}`);
    const keys = Object.keys(d.series);
    el.querySelector("#sum").innerHTML = d.summary.map((s) => `<div class="card"><h4>${esc(label(s.target))}</h4>
      <div class="v" style="font-size:22px;font-weight:700;margin-top:6px">${ms(s.avg)}</div>
      <div class="muted small">mín ${ms(s.min)} · máx ${ms(s.max)}</div>
      <div class="small mt"><span class="${s.loss > 1 ? "err" : "ok"}">pérdida ${pct(s.loss)}</span> · disponibilidad <b>${pct(100 - (s.down || 0))}</b></div></div>`).join("") || `<div class="card span2"><p class="muted">Las mediciones aparecerán en un minuto.</p></div>`;
    const times = [...new Set(keys.flatMap((k) => d.series[k].map((p) => p.t)))].sort((a, b) => a - b);
    const color = (i, k) => k === "gateway" ? COLORS.green : k === "dns" ? COLORS.amber : PALETTE[(i + 0) % PALETTE.length];
    const ser = (field) => keys.map((k, i) => { const m = new Map(d.series[k].map((p) => [p.t, p[field]])); return { name: label(k), color: color(i, k), fill: false, values: times.map((t) => m.get(t) ?? null) }; });
    el.querySelector("#leg").innerHTML = keys.map((k, i) => `<span><i style="background:${color(i, k)}"></i>${esc(label(k))}</span>`).join("");
    const xFmt = hours > 24 ? (t) => new Date(t * 1000).toLocaleDateString("es", { day: "numeric", month: "short" }) : undefined;
    charts.forEach((c) => c.destroy()); charts.length = 0;
    charts.push(lineChart(el.querySelector("#lat"), { labels: times, series: ser("latency"), yFmt: (v) => `${v.toFixed(0)} ms`, xFmt }));
    charts.push(lineChart(el.querySelector("#loss"), { labels: times, series: ser("loss"), yFmt: (v) => `${v.toFixed(0)}%`, xFmt, minY: 5 }));
  }
  await load();
  const t = setInterval(load, 60000);
  return () => { clearInterval(t); charts.forEach((c) => c.destroy()); };
}
