/* 光纤获客助手 - 离线缓存 Service Worker */
const CACHE = "yishankeji-v21";
const PRECACHE = [
  "/",
  "/static/index.html",
  "/static/style.css",
  "/static/app.js",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // 接口请求不走缓存，保证数据实时
  if (url.pathname.startsWith("/api/")) return;
  // 页面导航：优先网络，断网时用缓存壳
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put("/", copy));
          return resp;
        })
        .catch(() => caches.open(CACHE).then((cache) => cache.match("/")))
    );
    return;
  }
  // 静态资源：先用缓存，同时后台更新
  if (e.request.method === "GET" && url.origin === self.location.origin) {
    e.respondWith(
      caches.match(e.request).then((hit) => {
        const fresh = fetch(e.request)
          .then((resp) => {
            if (resp.ok) {
              const copy = resp.clone();
              caches.open(CACHE).then((cache) => cache.put(e.request, copy));
            }
            return resp;
          })
          .catch(() => hit);
        return hit || fresh;
      })
    );
  }
});
