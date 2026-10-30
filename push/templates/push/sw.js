// The service worker of one of Pavilion's installable apps ({{ app }}), served from the top of its scope
// ({{ scope }}) by push/views.py: service_worker. It shows push notifications (push/send.py) and opens their page on
// a tap, and keeps the subscription when the browser renews it. {% if offline %}Pages that can't load show the
// offline page.{% elif browse %}It keeps the last copy of every page and file the app loads (the network first, always),
// so the app can still be browsed offline; the page says it's offline (online_store/static/online_store/offline.js).
{% else %}It leaves every request to the network.{% endif %}
const CONFIG = {{ config|safe }};
{% if offline %}const CACHE = "pavilion-" + CONFIG.app + "-" + CONFIG.version;

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([CONFIG.offline, ...CONFIG.precache])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(caches.keys()
        .then((keys) => Promise.all(keys.filter((k) => k.startsWith("pavilion-" + CONFIG.app + "-") && k !== CACHE).map((k) => caches.delete(k))))
        .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return;
    if (request.mode === "navigate") {
        event.respondWith(fetch(request).catch(() => caches.match(CONFIG.offline)));
    } else if (CONFIG.precache.includes(new URL(request.url).pathname)) {
        event.respondWith(fetch(request).catch(() => caches.match(request)));
    }
});
{% elif browse %}// Not versioned: what was saved stays through updates, and every visit online replaces it
const CACHE = "pavilion-" + CONFIG.app + "-pages", LIMIT = 400;

// The first pages saved straight away, so the app opens offline even before they've been visited
const warm = () => caches.open(CACHE).then((cache) => Promise.all(CONFIG.warm.map((url) =>
    fetch(url, { credentials: "include" }).then((response) => response.ok && cache.put(url, response)).catch(() => {}))));

self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim().then(warm)));

// Saved under the address it came from: a navigation can't be answered with a response marked as redirected
async function save(url, response) {
    const copy = response.redirected
        ? new Response(await response.blob(), { status: response.status, statusText: response.statusText, headers: response.headers })
        : response;
    const cache = await caches.open(CACHE);
    await cache.put(url, copy);
    const keys = await cache.keys();
    await Promise.all(keys.slice(0, Math.max(0, keys.length - LIMIT)).map((key) => cache.delete(key)));  // oldest first
}

const keep = (response) => response.type === "opaque"  // another site's picture or stylesheet
    || (response.ok && !(response.headers.get("Cache-Control") || "").includes("no-store"));

async function fromNetwork(event) {
    const request = event.request;
    try {
        const response = await fetch(request);
        if (keep(response)) event.waitUntil(save(response.redirected ? response.url : request.url, response.clone()).catch(() => {}));
        return response;
    } catch (error) {
        const saved = await caches.match(request, { ignoreVary: true, ignoreSearch: false });
        if (saved) return saved;
        if (request.mode === "navigate") {  // never opened here: the home page rather than the browser's error
            const home = await caches.match(CONFIG.scope, { ignoreVary: true });
            if (home) return home;
        }
        throw error;
    }
}

self.addEventListener("fetch", (event) => {
    const request = event.request, url = new URL(request.url);
    if (url.origin === self.location.origin && CONFIG.forget.includes(url.pathname)) {
        event.waitUntil(caches.delete(CACHE));  // signing in or out: nothing of the last person's stays on the device
        return;
    }
    if (request.method !== "GET" || !url.protocol.startsWith("http")) return;
    if (url.origin === self.location.origin && !url.pathname.startsWith(CONFIG.scope)
        && !CONFIG.files.some((prefix) => url.pathname.startsWith(prefix))) return;  // the live stream, other apps
    if (request.headers.get("Accept")?.includes("text/event-stream")) return;
    event.respondWith(fromNetwork(event));
});
{% else %}self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
{% endif %}
self.addEventListener("push", (event) => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data?.text() }; }
    const url = data.url || CONFIG.scope;
    const options = { body: data.body || "", icon: data.icon || CONFIG.icon, badge: data.badge || CONFIG.badge || undefined, data: { url } };
    if (data.tag) Object.assign(options, { tag: data.tag, renotify: true });
    if (data.action) options.actions = [{ action: "open", title: data.action }];
    event.waitUntil(self.registration.showNotification(data.title || CONFIG.name, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = new URL(event.notification.data?.url || CONFIG.scope, self.location.origin).href;
    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
        const open = windows.find((w) => w.url === url) || windows.find((w) => w.url.startsWith(self.registration.scope));
        if (open) return open.focus().then((w) => (w && w.url !== url && "navigate" in w ? w.navigate(url) : w));
        return self.clients.openWindow(url);
    }));
});

// The browser renewed the subscription (or dropped it): subscribe again and tell Pavilion which one it replaces
self.addEventListener("pushsubscriptionchange", (event) => {
    const old = event.oldSubscription;
    const renewed = event.newSubscription
        ? Promise.resolve(event.newSubscription)
        : self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: CONFIG.key });
    event.waitUntil(renewed.then((subscription) => fetch(CONFIG.renew, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: subscription.toJSON(), replaces: old ? old.endpoint : "" }),
    })).catch(() => {}));
});
