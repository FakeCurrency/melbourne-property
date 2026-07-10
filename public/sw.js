/* Melbourne Property service worker — conservative network-first.
   Always tries the network (so deploys are picked up immediately) and falls
   back to the last cached copy only when offline. Cross-origin requests
   (map tiles, geocoding) are left alone. */
const CACHE = "mp-v2";

self.addEventListener("install", e => self.skipWaiting());

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => {
            c.put(e.request, copy);
            /* Evict stale ?v= variants of the same path so old cache-busted
               URLs don't accumulate. Fire-and-forget — never delays the page. */
            if (url.search) {
              c.keys().then(reqs => Promise.all(
                reqs.filter(r => {
                  const old = new URL(r.url);
                  return old.pathname === url.pathname && old.search !== url.search;
                }).map(r => c.delete(r))
              ));
            }
          });
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
