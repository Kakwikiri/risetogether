const CACHE_NAME = "risetogether-cache-v11";
const ASSETS = [
  "/static/manifest.json",
  "/static/css/styles.css",
  "/static/js/app.js",
  "/static/js/socket.js",
  "/static/images/default-avatar.png",
  "/static/images/icon-192.png",
  "/static/images/icon-512.png",
  "/static/images/risetogether-logo.png",
];

const NETWORK_ONLY_PREFIXES = [
  "/socket.io/",
  "/calls/",
  "/live/",
  "/api/",
  "/messages",
  "/chat/",
  "/notifications",
  "/admin/",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)),
  );
  self.skipWaiting();
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
      fetch(event.request).catch(
        () =>
          new Response("RiseTogether is offline. Please reconnect and try again.", {
            headers: { "Content-Type": "text/plain" },
          }),
      ),
    );
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
