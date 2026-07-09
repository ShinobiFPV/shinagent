// Q2 PWA Service Worker
// Strategy: cache-first for static assets, network-first for API calls.
// This lets the app shell load instantly even on slow connections while
// ensuring API calls (chat, TTS, camera) always use live data.

const CACHE_NAME = 'q2-v12';
const SHELL_URLS = [
  '/',
  '/static/manifest.json',
  '/static/icons/icon-180.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// Install — cache the app shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(SHELL_URLS).catch(err => {
        console.warn('Q2 SW: shell cache failed (offline install?)', err);
      });
    })
  );
  self.skipWaiting();
});

// Activate — clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch — network-first for API, cache-first for static
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always go to network for API calls — chat, TTS, camera, settings proxy,
  // and any endpoint whose GET response reflects mutable runtime state
  // (voice model, LLM backend, log tail) that must never be served stale
  // from cache.
  const isApi = ['/chat', '/tts', '/transcribe', '/analyze',
                 '/health', '/settings', '/face-api', '/camera',
                 '/voice', '/llm-switch', '/restart', '/log-tail'].some(
    p => url.pathname.startsWith(p)
  );

  if (isApi) {
    // Network-only — if offline, return a clean error
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(
          JSON.stringify({error: 'Q2 is offline — check your Tailscale connection.'}),
          {status: 503, headers: {'Content-Type': 'application/json'}}
        )
      )
    );
    return;
  }

  // Cache-first for static assets (the app shell)
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        // Cache successful GET responses for static assets
        if (event.request.method === 'GET' && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      });
    })
  );
});
