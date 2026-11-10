import { api, esc, ico, ms, toast, toastErr, busy, ago, $, $$, on, bytes, fresh } from "../lib.js";

export const title = "Asistente inteligente";
export const subtitle = "Analiza tu red y aplica la configuración óptima para tu caso";

const AREA_ICON = { DNS: "globe", Unbound: "server", "Protección": "shield", "Control parental": "users", Seguridad: "lock",
  "Conexión": "wifi", "Acceso remoto": "network", Juegos: "console", "Gaming y streaming": "console", Velocidad: "gauge" };
const PRIO = { alta: "err", media: "warn", baja: "info" };

export async function render(el) {
  el.innerHTML = `<div id="intro"></div><div id="progress"></div><div id="result"></div>`;
  let poll = null;

  const state = await api.get("/advisor");
  if (state.running) watch(); else if (state.last) show(state.last); else intro();

  function intro() {
    el.querySelector("#intro").innerHTML = `
      <div class="card" style="padding:28px">
        <div class="row" style="gap:22px;flex-wrap:nowrap;align-items:flex-start">
          <div class="brand-logo" style="width:64px;height:64px;border-radius:18px;flex:none">${ico("zap")}</div>
          <div class="grow">
            <h2>Optimiza tu red en un clic</h2>
            <p class="muted">El asistente ejecuta un análisis completo (≈30-60 s) y propone la configuración ideal para <b>tu</b> conexión, <b>tu</b> ubicación y <b>tus</b> dispositivos:</p>
            <div class="grid g2 mt small">
              <div class="row">${ico("globe")}DNS más rápido desde tu zona y con CDN más cercana</div>
              <div class="row">${ico("devices")}Inventario de dispositivos y perfiles sugeridos</div>
              <div class="row">${ico("network")}Detección de CGNAT, doble NAT, NAT de juegos y MTU</div>
              <div class="row">${ico("server")}Unbound y caché ajustados a tu hardware</div>
              <div class="row">${ico("console")}Modo gaming/streaming sin lag de DNS</div>
              <div class="row">${ico("shield")}Protección contra malware y control parental</div>
            </div>
            <button class="btn primary lg mt" id="run">${ico("zap")}Analizar mi red</button>
          </div>
        </div>
      </div>`;
    el.querySelector("#run").onclick = start;
  }

  async function start() {
    el.querySelector("#intro").innerHTML = "";
    el.querySelector("#result").innerHTML = "";
    const req = api.post("/advisor/analyze");
    watch();
    try { const r = await req; stopWatch(); show(r); }
    catch (e) { stopWatch(); toastErr(e); intro(); }
  }

  function watch() {
    const box = el.querySelector("#progress");
    const tick = async () => {
      const s = await api.get("/advisor").catch(() => null);
      if (!s) return;
      box.innerHTML = `<div class="card"><header><h2><span class="btn ghost loading" style="width:24px;height:24px;padding:0"></span>Analizando tu red…</h2></header>
        <ol class="steps">${(s.progress.length ? s.progress : ["Preparando análisis…"]).map((p, i, a) => `<li class="${i === a.length - 1 ? "" : "muted"}">${esc(p)} ${i < a.length - 1 ? `<span class="ok">${ico("check")}</span>` : ""}</li>`).join("")}</ol>
        <div class="progress mt"><i style="width:${Math.min(95, 15 + s.progress.length * 16)}%"></i></div></div>`;
      if (!s.running && s.last) { stopWatch(); show(s.last); }
    };
    tick();
    poll = setInterval(tick, 1200);
  }
  function stopWatch() { clearInterval(poll); poll = null; el.querySelector("#progress").innerHTML = ""; }

  function show(r) {
    const color = r.score >= 90 ? "var(--ok)" : r.score >= 75 ? "#84cc16" : r.score >= 50 ? "var(--warn)" : "var(--err)";
    const recs = r.recommendations;
    const auto = recs.filter((x) => x.auto && !x.applied);
    const top = r.dns_benchmark.filter((b) => b.median != null).slice(0, 7);
    const box = fresh(el.querySelector("#result"));
    box.innerHTML = `
      <div class="grid g3">
        <div class="card center" style="display:flex;flex-direction:column;align-items:center;justify-content:center">
          <svg viewBox="0 0 120 120" width="150" height="150"><circle cx="60" cy="60" r="50" fill="none" stroke="var(--grid)" stroke-width="12"/>
            <circle cx="60" cy="60" r="50" fill="none" stroke="${color}" stroke-width="12" stroke-linecap="round" stroke-dasharray="${(r.score / 100) * 314} 314" transform="rotate(-90 60 60)"/>
            <text x="60" y="66" text-anchor="middle" font-size="30" font-weight="700" fill="var(--text)">${r.score}</text></svg>
          <h3>Salud de la red</h3><p class="muted small">Análisis ${ago(r.ts)} · ${r.seconds} s</p>
          <button class="btn sm mt" id="rerun">${ico("refresh")}Analizar de nuevo</button>
        </div>
        <div class="card span2">
          <header><h2>${ico("info")}Resumen</h2></header>
          <p>${esc(r.summary)}</p>
          <div class="grid g4 mt">
            ${metric("Dispositivos", `${r.network.online}/${r.network.devices}`, "en línea")}
            ${metric("DNS más rápido", top[0] ? ms(top[0].median) : "—", top[0]?.name || "")}
            ${metric("Conectividad", r.nat.label || "—", r.nat.nat?.label || "")}
            ${metric("Servidor", `${r.hardware.cpus} CPU`, bytes(r.hardware.memory) + " RAM")}
          </div>
          ${r.factors.length ? `<div class="chips mt">${r.factors.map((f) => `<span class="badge warn">${f.points} · ${esc(f.text)}</span>`).join("")}</div>` : `<p class="ok mt">${ico("check")} Sin problemas detectados</p>`}
        </div>
      </div>

      <div class="card mt">
        <header><h2>${ico("zap")}Configuración recomendada</h2>
          ${auto.length ? `<button class="btn primary" id="apply-all">${ico("check")}Aplicar ${auto.length} recomendaciones</button>` : ""}</header>
        ${recs.length ? `<div class="col">${recs.map(recCard).join("")}</div>` : `<div class="alert ok">${ico("check")}<div><b>¡Todo optimizado!</b><p class="small">Tu configuración ya es la recomendada para tu red.</p></div></div>`}
      </div>

      <div class="grid g2 mt">
        <div class="card"><header><h3>${ico("globe")}Ranking DNS desde tu ubicación</h3><a class="btn sm ghost" href="#/dns">Ver DNS</a></header>
          <div class="table-wrap"><table><thead><tr><th>#</th><th>Resolver</th><th class="right">DNS</th><th class="right">CDN</th><th class="right">Fiable</th></tr></thead>
          <tbody>${top.map((b, i) => `<tr><td>${i + 1}</td><td>${esc(b.name)}</td><td class="right">${ms(b.median)}</td><td class="right">${ms(b.cdn_avg)}</td><td class="right">${b.reliability}%</td></tr>`).join("")}</tbody></table></div>
          <p class="muted small mt">«CDN» = latencia al servidor de streaming/descargas al que te envía ese DNS. Más bajo = vídeo más estable.</p></div>
        <div class="card"><header><h3>${ico("activity")}Diagnóstico</h3><a class="btn sm ghost" href="#/tools">Herramientas</a></header>
          ${r.diagnosis.checks.map((c) => `<div class="row between small" style="padding:5px 0"><span><span class="dot dot-${c.status === "ok" ? "ok" : c.status === "info" ? "off" : c.status === "warn" ? "warn" : "err"}"></span> ${esc(c.name)}</span><span class="muted truncate" style="max-width:60%" title="${esc(c.detail)}">${esc(c.detail)}</span></div>`).join("")}</div>
      </div>`;

    box.querySelector("#rerun").onclick = start;
    const applyAll = box.querySelector("#apply-all");
    if (applyAll) applyAll.onclick = () => apply(auto.map((x) => x.id), applyAll);
    on(box, "click", "[data-apply]", (e, b) => apply([b.dataset.apply], b));
  }

  async function apply(ids, btn) {
    await busy(btn, async () => {
      const { results } = await api.post("/advisor/apply", { ids });
      const ok = results.filter((r) => r.ok).length, bad = results.filter((r) => !r.ok);
      if (ok) toast(`${ok} recomendación(es) aplicada(s)`);
      bad.forEach((b) => toast(`${b.id}: ${b.error}`, "error", 7000));
      const s = await api.get("/advisor");
      show(s.last);
    }).catch(() => {});
  }


  function recCard(x) {
    const link = { speedtest: "#/speed", sqm: "#/speed", mtu: "#/connectivity", nat_strict: "#/connectivity", gateway_latency: "#/tools" }[x.id]
      || (x.id.startsWith("nat_") ? "#/connectivity" : null);
    return `<div class="rec row" style="align-items:flex-start;flex-wrap:nowrap;gap:14px">
      <div class="stat"><div class="ic tint-${x.applied ? "green" : "blue"}" style="width:38px;height:38px">${ico(x.applied ? "check" : AREA_ICON[x.area] || "info")}</div></div>
      <div class="grow">
        <div class="row"><b>${esc(x.title)}</b>${x.priority ? `<span class="badge ${PRIO[x.priority]}">prioridad ${esc(x.priority)}</span>` : ""}<span class="badge">${esc(x.area)}</span>${x.impact ? `<span class="badge violet">${esc(x.impact)}</span>` : ""}</div>
        <p class="muted small">${esc(x.why)}</p>
        ${x.current || x.proposed ? `<div class="small mono">${x.current ? `<div><span class="muted">Actual:</span> ${esc(x.current)}</div>` : ""}${x.proposed ? `<div><span class="muted">Propuesto:</span> <span class="ok">${esc(x.proposed)}</span></div>` : ""}</div>` : ""}
      </div>
      <div class="col" style="flex:none">
        ${x.applied ? `<span class="badge ok">${ico("check")}Aplicado</span>` : x.auto ? `<button class="btn sm primary" data-apply="${esc(x.id)}">Aplicar</button>` : link ? `<a class="btn sm" href="${link}">Ver cómo</a>` : `<span class="badge">Manual</span>`}
      </div></div>`;
  }

  return () => clearInterval(poll);
}

function metric(l, v, s) {
  return `<div class="big-metric"><div class="v" style="font-size:20px">${esc(v)}</div><div class="l">${esc(l)}<br><span class="small">${esc(s)}</span></div></div>`;
}
