const CACHE = "scpper-mc-v5";
const CACHEABLE = /\.(?:html|css|js|json|gz)$/i;

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([
    "/",
    "/index.html",
    "/pages.html",
    "/users.html",
    "/forum.html",
    "/recent.html",
    "/game.html",
    "/assets/common.js",
    "/assets/baota-config.js",
    "/assets/fighting.js",
    "/assets/scpper.css",
    "/data/home-index.json.gz",
    "/data/forum-categories.json.gz",
    "/data/pages-head.json",
    "/data/users-head.json",
    "/data/recent-head.json",
  ]).catch(() => {})));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin || !CACHEABLE.test(url.pathname) && url.pathname !== "/") return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request);
    const fetched = fetch(request).then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    }).catch(() => cached);
    const networkFirst = url.pathname === "/" || /\.(?:html|css|js)$/i.test(url.pathname);
    return networkFirst ? fetched || cached : cached || fetched;
  })());
});
