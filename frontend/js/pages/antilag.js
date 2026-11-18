// Anti-lag: SQM (CAKE) contra el lag con la red cargada + prioridad de juegos, llamadas, streaming y navegación.
import { api, esc, ico, ms, ago, toast, toastErr, busy, toggle } from "../lib.js";
import { t } from "../i18n.js";
import { mountLag } from "./lag.js";

export const title = "Anti-lag y prioridad";
export const subtitle = "Ping bajo aunque alguien descargue: colas inteligentes y prioridad para juegos, llamadas, streaming y navegación";

const GRADE = { "A+": "ok", A: "ok", B: "warn", C: "warn", D: "err", F: "err" };

export async function render(el) {
  el.innerHTML = `<div id="lag" class="mb"></div><div id="sqm" class="grid g2 mb"></div>`;
  mountLag(el.querySelector("#lag"));
  const box = el.querySelector("#sqm");

  const draw = async () => {
    const s = await api.get("/sqm");
    const c = s.config;
    const lt = s.last_test;
    const tune = c.last_tune;
    box.innerHTML = `
      <div class="card"><header><h2>${ico("gauge")}${t("Anti-lag con la red cargada (SQM)")}</h2>${toggle("sqm-en", c.enabled, 'id="sqm-en"')}</header>
        ${!s.router_enabled ? `<div class="alert warn small mb">${ico("alert")}<div>${t("Necesita que Bipon sea la puerta de enlace (Router y firewall → Asistente de gateway). Sin eso, el tráfico no pasa por Bipon y no se puede ordenar.")}</div></div>` : ""}
        <p class="small muted">${t("Cuando alguien descarga o ve vídeo en 4K, el router del proveedor llena su cola y el ping de tu partida sube decenas o cientos de ms (bufferbloat). Bipon limita el tráfico a un poco menos de tu velocidad real para que la cola se forme aquí, donde CAKE la ordena: cada equipo y cada conexión tiene su turno y lo interactivo pasa primero.")}</p>
        <div class="grid g3 small mt">
          <div><div class="muted">${t("Bajada")}</div><div style="font-size:22px;font-weight:700">${s.down ? s.down + " Mbit/s" : "—"}</div></div>
          <div><div class="muted">${t("Subida")}</div><div style="font-size:22px;font-weight:700">${s.up ? s.up + " Mbit/s" : "—"}</div></div>
          <div><div class="muted">${t("Motor")}</div><div style="font-size:18px;font-weight:600">${s.cake ? "CAKE" : "fq_codel"}</div></div></div>
        <p class="small muted">${s.source ? `${t("Velocidades tomadas de")}: ${esc(t(s.source))}` : t("Haz «Medir y ajustar» para calcular las velocidades.")}</p>
        ${lt ? `<div class="rec small mt"><b>${t("Último test de velocidad")}</b> · ${ago(lt.ts)}<br>${t("Ping en reposo")} ${ms(lt.ping)} → ${t("con carga")} ${ms(lt.loaded_ping)}
          ${lt.grade ? ` · ${t("Nota de bufferbloat")} <span class="badge ${GRADE[lt.grade] || ""}">${esc(lt.grade)}</span>` : ""}</div>` : ""}
        <div class="row mt"><button class="btn primary" id="tune" ${s.router_enabled ? "" : "disabled"}>${ico("zap")}${t("Medir y ajustar")}</button></div>
        <details class="mt"><summary class="small">${t("Ajuste manual")}</summary>
          <div class="opt mt"><div class="t"><b>${t("Automático")}</b><span>${t("Usa el último test de velocidad × el margen.")}</span></div>${toggle("auto", c.auto, 'id="auto"')}</div>
          <div class="form-grid mt"><label class="field">${t("Bajada (Mbit/s)")}<input type="number" id="down" min="0" step="0.1" value="${c.down_mbit || ""}"></label>
            <label class="field">${t("Subida (Mbit/s)")}<input type="number" id="up" min="0" step="0.1" value="${c.up_mbit || ""}"></label>
            <label class="field">${t("Margen sobre tu velocidad real")}<select id="factor">${[0.8, 0.85, 0.9, 0.93, 0.95].map((f) => `<option value="${f}" ${Math.abs(c.factor - f) < 0.001 ? "selected" : ""}>${Math.round(f * 100)} %</option>`).join("")}</select></label></div>
          <p class="small muted">${t("Más bajo = ping más estable con carga; más alto = más velocidad máxima. 90 % es el equilibrio habitual.")}</p>
          <button class="btn mt" id="save">${ico("check")}${t("Guardar")}</button></details>
        ${tune ? `<p class="small muted mt">${t("Último ajuste")}: ${ago(tune.ts)} · ${t("medido")} ${tune.download}/${tune.upload} Mbit/s</p>` : ""}
        <p class="small muted mt">${t("Para comprobarlo desde tu PC o tu móvil, haz un test de bufferbloat (por ejemplo, el de Waveform): la nota debería ser A o A+.")}</p></div>
      <div class="card"><header><h2>${ico("console")}${t("Prioridad del tráfico")}</h2>${toggle("dscp", c.dscp, 'id="dscp"')}</header>
        <p class="small muted">${t("Bipon marca cada paquete según lo que es. CAKE y el Wi-Fi (WMM) de tu punto de acceso lo usan para decidir qué pasa primero.")}</p>
        <ol class="mt" style="padding-left:18px;line-height:1.9">
          <li><b>${t("Juegos y consolas")}</b> <span class="badge ok">CS4</span> <span class="small muted">${t("puertos de Xbox, PlayStation, Steam, Riot, Epic, Call of Duty… y paquetes UDP pequeños")}</span></li>
          <li><b>${t("Llamadas y videollamadas")}</b> <span class="badge ok">EF</span> <span class="small muted">Discord, WhatsApp, Teams, Zoom, Meet, FaceTime</span></li>
          <li><b>${t("Streaming")}</b> <span class="badge info">AF41</span> <span class="small muted">${t("YouTube, Netflix, Prime, Disney+, Twitch, Spotify… reconocido por su DNS")}</span></li>
          <li><b>${t("Navegación")}</b> <span class="badge">${t("normal")}</span> <span class="small muted">${t("webs, redes sociales, apps")}</span></li>
          <li><b>${t("Descargas grandes")}</b> <span class="badge">CS1</span> <span class="small muted">${t("actualizaciones, juegos descargándose, copias en la nube, torrents: solo usan lo que sobra")}</span></li></ol>
        <div class="small muted mt">${t("Servidores reconocidos por DNS desde el arranque")}: ${t("streaming")} ${s.classified.stream} · ${t("llamadas")} ${s.classified.call} · ${t("descargas")} ${s.classified.bulk}</div>
        <div class="opt mt"><div class="t"><b>${t("Detectar juegos por el tamaño de los paquetes")}</b><span>${t("Prioriza paquetes UDP pequeños aunque el juego use puertos desconocidos.")}</span></div>${toggle("heur", c.interactive_heuristic, 'id="heur"')}</div>
        <label class="field mt">${t("Una conexión pasa a «descarga grande» a partir de (MB)")}<input type="number" id="bulk" min="1" max="10000" value="${c.bulk_mb}" style="max-width:140px"></label>
        <button class="btn mt" id="save2">${ico("check")}${t("Guardar")}</button></div>`;
    const put = (patch, msg) => api.put("/sqm", patch).then((r) => { toast(t(r.message || msg || "Guardado"), r.ok === false ? "warn" : "ok", 7000); draw(); }).catch(toastErr);
    box.querySelector("#sqm-en").onchange = (e) => put({ enabled: e.target.checked });
    box.querySelector("#dscp").onchange = (e) => put({ dscp: e.target.checked });
    box.querySelector("#save").onclick = (e) => busy(e.currentTarget, () => put({ auto: box.querySelector("#auto").checked, down_mbit: Number(box.querySelector("#down").value) || 0,
      up_mbit: Number(box.querySelector("#up").value) || 0, factor: Number(box.querySelector("#factor").value) }));
    box.querySelector("#save2").onclick = (e) => busy(e.currentTarget, () => put({ interactive_heuristic: box.querySelector("#heur").checked, bulk_mb: Number(box.querySelector("#bulk").value) }));
    box.querySelector("#tune").onclick = (e) => {
      toast(t("Midiendo tu línea sin colas (unos 30 s)…"), "info", 8000);
      busy(e.currentTarget, () => api.post("/sqm/autotune")).then((r) => { toast(t(r.message), r.ok ? "ok" : "warn", 9000); draw(); }).catch(() => {});
    };
  };
  await draw();
}
