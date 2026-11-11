// Service worker de Bipon Net: notificaciones push y carga rápida de la interfaz.
const CACHE = "bipon-shell-v2";
const SHELL = ["/", "/assets/css/app.css", "/assets/js/app.js", "/assets/js/lib.js", "/assets/js/i18n.js", "/assets/js/theme.js"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))); self.clients.claim(); });

// red primero; caché sólo como respaldo de la interfaz (la API nunca se cachea)
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {}); return r; })
    .catch(() => caches.match(e.request).then((r) => r || caches.match("/"))));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: "Bipon Net", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Bipon Net", { body: d.body || "", tag: d.tag, icon: "/assets/icon-192.png", badge: "/assets/icon-192.png" }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs[0] ? cs[0].focus() : self.clients.openWindow("/"))));
});
