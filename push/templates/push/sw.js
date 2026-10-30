// The service worker of one of Pavilion's installable apps ({{ app }}), served from the top of its scope
// ({{ scope }}) by push/views.py: service_worker. It shows push notifications (push/send.py) and opens their page on
// a tap, and keeps the subscription when the browser renews it. {% if offline %}Pages that can't load show the
// offline page.{% else %}It leaves every request to the network.{% endif %}
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
