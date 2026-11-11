import { api, stream, esc, ico, ms, mbps, datetime, toast, toastErr, gauge, lineChart, COLORS, $, empty } from "../lib.js";

export const title = "Test de velocidad";
export const subtitle = "Internet (red de Cloudflare, nodo más cercano), bufferbloat y velocidad de tu red local";

const GRADE_TXT = {
  "A+": "Excelente: el ping apenas cambia aunque la red esté saturada. Ideal para juegos competitivos.",
  A: "Muy bueno: ideal para gaming y videollamadas.", B: "Bueno: puede notarse algo de lag si alguien descarga mientras juegas.",
  C: "Regular: el ping sube bastante con la red cargada. Activa SQM/QoS en el router.",
  D: "Malo: lag notable al descargar o subir. Activa SQM/QoS (CAKE) en el router.", F: "Muy malo: la conexión se colapsa bajo carga. Activa SQM/QoS urgentemente.",
};

export async function render(el) {
  let ctrl = null;
  el.innerHTML = `
    <div class="tabs"><button class="active" data-t="inet">${ico("globe")}Internet</button><button data-t="lan">${ico("wifi")}Red local (LAN/Wi-Fi)</button></div>
    <div id="inet">
      <div class="grid g3">
        <div class="card span2">
          <div class="speed-gauge"><canvas id="g"></canvas></div>
          <div class="center"><span id="phase" class="muted">Pulsa para empezar</span><div class="progress mt" style="max-width:420px;margin:10px auto"><i id="prog" style="width:0"></i></div>
            <button class="btn primary lg mt" id="go">${ico("play")}Iniciar test</button></div>
          <div class="grid g4 mt">
            ${m("ping", "Ping")}${m("jitter", "Jitter")}${m("down", "Descarga")}${m("up", "Subida")}
          </div>
        </div>
        <div class="card" id="bloat"><header><h3>${ico("activity")}Bufferbloat</h3></header>
          <p class="muted small">Mide cuánto aumenta el ping mientras la conexión está al máximo. Es la causa nº 1 de lag en juegos y cortes en videollamadas cuando alguien descarga.</p>
          <div id="bloat-res" class="mt">${empty("Ejecuta un test para ver la nota", "gauge")}</div></div>
      </div>
      <div class="card mt" id="info-card"></div>
      <div class="card mt"><header><h2>${ico("clock")}Historial</h2></header><div class="chart"><canvas id="hist"></canvas></div><div id="hist-table" class="mt"></div></div>
    </div>
    <div id="lan" class="hidden">
      <div class="card">
        <p class="muted">Mide la velocidad entre <b>este navegador</b> y el servidor Bipon Net. Útil para comprobar tu Wi-Fi, cables y switches. Ejecútalo desde el dispositivo que quieras medir.</p>
        <div class="grid g3 mt">${m("lping", "Latencia LAN")}${m("ldown", "Descarga LAN")}${m("lup", "Subida LAN")}</div>
        <div class="center mt"><button class="btn primary lg" id="lgo">${ico("play")}Medir red local</button></div>
        <p class="muted small mt" id="lan-note"></p>
      </div>
    </div>`;

  el.querySelectorAll("[data-t]").forEach((b) => b.onclick = () => {
    el.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("active", x === b));
    el.querySelector("#inet").classList.toggle("hidden", b.dataset.t !== "inet");
    el.querySelector("#lan").classList.toggle("hidden", b.dataset.t !== "lan");
  });
  const g = el.querySelector("#g");
  const set = (id, v) => { el.querySelector(`#v-${id}`).textContent = v; };
  gauge(g, 0, 1000, { label: "Mbps" });

  el.querySelector("#go").onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    ["ping", "jitter", "down", "up"].forEach((k) => set(k, "—"));
    const phase = el.querySelector("#phase"), prog = el.querySelector("#prog");
    ctrl = new AbortController();
    try {
      await stream("/speedtest/run", (ev) => {
        if (ev.error) throw new Error(ev.error);
        if (ev.phase === "start") phase.textContent = "Conectando con el servidor más cercano…";
        if (ev.phase === "info") phase.textContent = `Servidor Cloudflare ${ev.info.colo || ""} · ${ev.info.isp || ""}`;
        if (ev.phase === "ping") { set("ping", ms(ev.value)); prog.style.width = "10%"; }
        if (ev.phase === "ping_done") { set("ping", ms(ev.ping)); set("jitter", ms(ev.jitter)); phase.textContent = "Midiendo descarga…"; }
        if (ev.phase === "download") { gauge(g, ev.value, 1000, { label: "Mbps ↓ descarga" }); set("down", mbps(ev.value)); prog.style.width = `${15 + ev.progress * 40}%`; }
        if (ev.phase === "download_done") phase.textContent = "Midiendo subida…";
        if (ev.phase === "upload") { gauge(g, ev.value, 1000, { label: "Mbps ↑ subida", color: COLORS.violet }); set("up", mbps(ev.value)); prog.style.width = `${55 + ev.progress * 45}%`; }
        if (ev.phase === "done") {
          const r = ev.result;
          prog.style.width = "100%";
          phase.textContent = `Completado · ${r.server}`;
          set("down", mbps(r.download)); set("up", mbps(r.upload)); set("ping", ms(r.ping)); set("jitter", ms(r.jitter));
          gauge(g, r.download, 1000, { label: "Mbps ↓ descarga" });
          showBloat(r);
          toast("Test completado");
        }
      }, ctrl.signal);
    } catch (err) { if (err.name !== "AbortError") toastErr(err); phase.textContent = "Error"; }
    btn.disabled = false;
    history();
  };

  function showBloat(r) {
    const box = el.querySelector("#bloat-res");
    if (r.grade == null) { box.innerHTML = `<p class="muted">No se pudo medir (ICMP bloqueado).</p>`; return; }
    const cls = r.grade === "A+" ? "Ap" : r.grade;
    box.innerHTML = `<div class="row"><span class="grade grade-${cls}">${r.grade}</span>
      <div><div>Reposo: <b>${ms(r.idle_ping)}</b></div><div>Con carga: <b>${ms(r.loaded_ping)}</b></div><div>Aumento: <b class="${r.bufferbloat > 60 ? "err" : "ok"}">+${ms(r.bufferbloat)}</b></div></div></div>
      <p class="small mt">${esc(GRADE_TXT[r.grade])}</p>
      ${["C", "D", "F"].includes(r.grade) ? `<div class="alert info mt">${ico("info")}<div class="small">Configura SQM/QoS en tu router limitando a <b>${Math.round(r.download * 0.92)} / ${Math.round(r.upload * 0.92)} Mbps</b>. Si Bipon Net es tu router, actívalo en <a href="#/gaming">Gaming → QoS</a>.</div></div>` : ""}`;
  }

  async function history() {
    const rows = await api.get("/speedtest/history?limit=60").catch(() => []);
    const last = rows[0];
    el.querySelector("#info-card").innerHTML = last ? `<div class="row between"><div><h3>Último resultado</h3><p class="muted small">${datetime(last.ts)} · ${esc(last.server || "")} · IP ${esc(last.ip || "")} · ${esc(last.isp || "")}</p></div>
      <div class="row">${[["↓", mbps(last.download)], ["↑", mbps(last.upload)], ["Ping", ms(last.ping)], ["Bufferbloat", last.grade || "—"]].map(([l, v]) => `<div class="big-metric" style="min-width:90px"><div class="v" style="font-size:20px">${esc(v)}</div><div class="l">${l}</div></div>`).join("")}</div></div>` : `<p class="muted">Aún no hay tests.</p>`;
    if (last && last.grade) showBloat({ ...last, idle_ping: last.ping, loaded_ping: last.loaded_ping });
    const asc = [...rows].reverse();
    lineChart(el.querySelector("#hist"), { labels: asc.map((r) => r.ts), xFmt: (t) => new Date(t * 1000).toLocaleDateString("es", { day: "numeric", month: "short" }),
      yFmt: (v) => `${Math.round(v)}`,
      series: [{ name: "Descarga (Mbps)", color: COLORS.blue, values: asc.map((r) => r.download) }, { name: "Subida (Mbps)", color: COLORS.violet, values: asc.map((r) => r.upload) },
        { name: "Ping (ms)", color: COLORS.amber, values: asc.map((r) => r.ping), fill: false }] });
    el.querySelector("#hist-table").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Fecha</th><th class="right">Descarga</th><th class="right">Subida</th><th class="right">Ping</th><th class="right">Jitter</th><th>Bufferbloat</th><th>Servidor</th></tr></thead>
      <tbody>${rows.slice(0, 15).map((r) => `<tr><td>${datetime(r.ts)}</td><td class="right">${mbps(r.download)}</td><td class="right">${mbps(r.upload)}</td><td class="right">${ms(r.ping)}</td><td class="right">${ms(r.jitter)}</td><td>${r.grade ? `<span class="badge ${["A+", "A"].includes(r.grade) ? "ok" : r.grade === "B" ? "info" : "err"}">${r.grade}</span>` : "—"}</td><td class="muted">${esc(r.server || "")}</td></tr>`).join("")}</tbody></table></div>` : "";
  }

  // ------------------------------------------------------------- LAN
  el.querySelector("#lgo").onclick = async (e) => {
    const btn = e.currentTarget; btn.disabled = true;
    try {
      const pings = [];
      for (let i = 0; i < 10; i++) { const t0 = performance.now(); await fetch("/api/lan/ping", { cache: "no-store" }); pings.push(performance.now() - t0); }
      pings.sort((a, b) => a - b);
      $("#v-lping", el).textContent = ms(pings[Math.floor(pings.length / 2)]);
      const dl = await timed(async (onBytes) => {
        const res = await fetch("/api/lan/download?size=200000000", { cache: "no-store" });
        const rd = res.body.getReader();
        for (;;) { const { value, done } = await rd.read(); if (done) break; if (onBytes(value.length)) { rd.cancel(); break; } }
      }, (v) => { $("#v-ldown", el).textContent = mbps(v); });
      $("#v-ldown", el).textContent = mbps(dl);
      const blob = new Uint8Array(8 * 1024 * 1024); crypto.getRandomValues(blob.subarray(0, 65536));
      let sent = 0; const t0 = performance.now();
      while (performance.now() - t0 < 6000) {
        await fetch("/api/lan/upload", { method: "POST", body: blob, cache: "no-store" });
        sent += blob.length;
        $("#v-lup", el).textContent = mbps((sent * 8) / ((performance.now() - t0) / 1000) / 1e6);
      }
      el.querySelector("#lan-note").textContent = dl < 100 ? "Velocidad baja para una red local: si usas Wi-Fi, acércate al router o usa la banda de 5/6 GHz; si es por cable, revisa que negocie a 1 Gbps." : "Buena velocidad local.";
    } catch (err) { toastErr(err); }
    btn.disabled = false;
  };

  async function timed(fn, onProgress) {
    let total = 0; const t0 = performance.now(); let last = t0;
    await fn((n) => {
      total += n;
      const now = performance.now();
      if (now - last > 250) { last = now; onProgress((total * 8) / ((now - t0) / 1000) / 1e6); }
      return now - t0 > 8000;
    });
    return (total * 8) / ((performance.now() - t0) / 1000) / 1e6;
  }

  history();
  return () => ctrl?.abort();
}

const m = (id, l) => `<div class="big-metric"><div class="v" id="v-${id}">—</div><div class="l">${l}</div></div>`;
