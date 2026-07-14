const CACHE_NAME = "risetogether-cache-v53";
const ASSETS = [
  "/offline",
  "/static/css/styles.css",
  "/static/js/app.js",
  "/static/js/socket.js",
  "/static/images/icon-192-v2.png",
  "/static/images/icon-512-v2.png",
  "/static/images/icon-maskable-512-v2.png",
  "/static/images/apple-touch-icon-v2.png",
  "/static/images/favicon-v2.png",
  "/static/images/social-preview.png",
  "/static/images/risetogether-logo.png",
];

const NETWORK_ONLY_PREFIXES = [
  "/socket.io/",
  "/calls/",
  "/live/",
  "/api/",
  "/messages",
  "/chat/",
  "/chat/upload",
  "/notifications",
  "/admin/",
  "/account/",
  "/profile/edit",
  "/uploads/",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (NETWORK_ONLY_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/offline")),
    );
    return;
  }

  if (url.pathname === "/static/manifest.json") {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  if (!url.pathname.startsWith("/static/")) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      return (
        cached ||
        fetch(event.request).then((response) => {
          if (!response || response.status !== 200 || response.type !== "basic") {
            return response;
          }
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
      );
    }),
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (error) {
      data = { body: event.data.text() };
    }
  }
  const title = data.title || "RiseTogether";
  const options = {
    body: data.body || "You have a new notification.",
    icon: "/static/images/icon-192-v2.png",
    badge: "/static/images/icon-192-v2.png",
    tag: data.tag || `risetogether-${Date.now()}`,
    renotify: true,
    vibrate: [120, 60, 120],
    data: {
      url: data.url || "/notifications",
      notification_id: data.notification_id || null,
      category: data.category || "notification",
    },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : "/notifications", self.location.origin);
  if (targetUrl.origin !== self.location.origin) {
    targetUrl.href = `${self.location.origin}/notifications`;
  }
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        const clientUrl = new URL(client.url);
        if (clientUrl.origin === targetUrl.origin && "focus" in client) {
          client.navigate(targetUrl.href);
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl.href);
      }
      return null;
    }),
  );
});
