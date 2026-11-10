// Apariencia: temas, efectos visuales y color de acento (por usuario).
export const SKINS = [
  { id: "glass", name: "Cristal", desc: "Efecto glass oscuro con luces animadas", mode: "dark", sw: ["#0b1020", "#3b82f6", "#22d3ee"] },
  { id: "glass-light", name: "Cristal claro", desc: "Glass luminoso", mode: "light", sw: ["#eef2fb", "#3b82f6", "#a5b4fc"] },
  { id: "zinc", name: "Zinc (shadcn)", desc: "Minimalista, plano y nítido", mode: "dark", sw: ["#09090b", "#fafafa", "#27272a"] },
  { id: "zinc-light", name: "Zinc claro (shadcn)", desc: "Minimalista claro", mode: "light", sw: ["#ffffff", "#18181b", "#e4e4e7"] },
  { id: "aurora", name: "Aurora", desc: "Verdes y turquesas boreales", mode: "dark", sw: ["#04130f", "#10b981", "#22d3ee"] },
  { id: "midnight", name: "Medianoche OLED", desc: "Negro puro, ahorra batería", mode: "dark", sw: ["#000000", "#6366f1", "#121212"] },
  { id: "neon", name: "Neón", desc: "Cyberpunk magenta y cian", mode: "dark", sw: ["#0a0014", "#ff2bd6", "#00f0ff"] },
  { id: "nord", name: "Nord", desc: "Gris azulado, descansado para la vista", mode: "dark", sw: ["#2e3440", "#88c0d0", "#5e81ac"] },
  { id: "sunset", name: "Atardecer", desc: "Claro y cálido", mode: "light", sw: ["#fff7ed", "#f97316", "#e11d48"] },
];

export const DEFAULT_PREFS = { skin: "glass", blur: "normal", bg: "animated", anim: "on", radius: "md", density: "normal", accent: "" };

export function loadPrefs() {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem("bipon-ui") || "{}") }; } catch { return { ...DEFAULT_PREFS }; }
}

export function applyPrefs(p) {
  const r = document.documentElement;
  const skin = SKINS.find((s) => s.id === p.skin) || SKINS[0];
  r.dataset.skin = skin.id;
  r.dataset.theme = skin.mode;
  r.dataset.blur = p.blur === "off" ? "off" : p.blur === "high" ? "high" : "normal";
  r.dataset.bg = p.bg || "animated";
  r.dataset.anim = p.anim === "off" ? "off" : "on";
  r.dataset.radius = p.radius || "md";
  r.dataset.density = p.density || "normal";
  if (p.accent) { r.style.setProperty("--accent", p.accent); r.style.setProperty("--ring", p.accent + "73"); }
  else { r.style.removeProperty("--accent"); r.style.removeProperty("--ring"); }
  window.dispatchEvent(new Event("resize"));
}

export function savePrefs(p) {
  try { localStorage.setItem("bipon-ui", JSON.stringify(p)); } catch {}
  applyPrefs(p);
}
