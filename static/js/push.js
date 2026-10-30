// Web push in the browser, shared by Pavilion's installable apps (the store: online_store/static/online_store/push.js;
// the courier app: deliveries/static/deliveries/courier.js). Each app registers its own service worker at the top of
// its scope (push/templates/push/sw.js), so each has its own subscription.

export const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

// iPhones and iPads only let apps on the Home Screen use notifications
export function needsInstall() {
    const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const installed = window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
    return apple && !installed && !("PushManager" in window);
}

export function permission() {
    return "Notification" in window ? Notification.permission : "denied";
}

function keyBytes(base64) {
    const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

const registrations = new Map();
export function register(url, scope) {
    if (!("serviceWorker" in navigator)) return Promise.resolve(null);
    if (!registrations.has(url)) {
        registrations.set(url, navigator.serviceWorker.register(url, { scope }).catch((error) => {
            console.warn("Service worker", error);
            return null;
        }));
    }
    return registrations.get(url);
}

// The browser's subscription for this app, when notifications are allowed and it has one
export async function current(registration) {
    if (!registration || !supported || permission() !== "granted") return null;
    return registration.pushManager.getSubscription();
}

// Ask (a tap has to lead here, or browsers won't show the question) and subscribe. Resolves to the subscription, or
// null if the person said no or dismissed it; "blocked" means they had blocked notifications before.
export async function subscribe(registration, key) {
    if (!registration || !supported) return null;
    if (permission() === "denied") return "blocked";
    if (permission() === "default" && (await Notification.requestPermission()) !== "granted") {
        return permission() === "denied" ? "blocked" : null;
    }
    return (await registration.pushManager.getSubscription())
        ?? registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
}

export function post(url, body) {
    const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    return fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": token },
        body: JSON.stringify(body),
    });
}

// What this browser last told the server, so pages only post when something changed (storage can be off: then
// they post every time, which is fine)
export function remembered(name) {
    try { return localStorage.getItem("pavilion-push-" + name) || ""; } catch { return ""; }
}
export function remember(name, value) {
    try {
        if (value) localStorage.setItem("pavilion-push-" + name, value);
        else localStorage.removeItem("pavilion-push-" + name);
    } catch { /* private window */ }
}
