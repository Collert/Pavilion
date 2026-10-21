// The courier app's bits that HTML can't do (the pages are server-rendered, see deliveries/views.py):
// - the start button says what you picked ("Start shift · Cycling") and waits until you pick;
// - the drop-off photo shows a preview, and "Complete delivery" waits for a photo when you left it at the door;
// - web push: the service worker is registered, and starting a shift asks for notifications and saves the
//   subscription so new orders reach this phone.
// live-page.js swaps parts of the page in and out, so everything here works by delegation and re-syncs on changes.

const me = document.getElementById("courier-js");
const t = (s) => (window.gettext ? window.gettext(s) : s);
// Only touch the DOM when something changes: sync() runs on every DOM change, so writing anyway would loop
const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
const setFlag = (node, name, on) => { if (node[name] !== on) node[name] = on; };

// ---------- start shift ----------

function syncStart() {
    const form = document.querySelector("form[data-start]");
    if (!form) return;
    const picked = form.querySelector('input[name="mode"]:checked');
    const button = form.querySelector("[data-start-button]");
    setFlag(button, "disabled", !picked);
    setText(button, picked ? `${button.dataset.go} · ${picked.dataset.short}` : button.dataset.pick);
}

// ---------- proof of delivery ----------

let previewUrl;
function syncProof() {
    const form = document.querySelector("form[data-proof]");
    const button = document.querySelector("[data-complete]");
    if (!form || !button) return;
    const file = form.querySelector('input[name="photo"]').files[0];
    const handoff = form.querySelector('input[name="handoff"]:checked')?.value;
    const img = form.querySelector("[data-preview]");
    if (file) {
        if (img.dataset.name !== file.name + file.size) {
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = URL.createObjectURL(file);
            img.src = previewUrl;
            img.dataset.name = file.name + file.size;
        }
    }
    setFlag(img, "hidden", !file);
    setFlag(form.querySelector("[data-empty]"), "hidden", !!file);
    setFlag(form.querySelector("[data-retake]"), "hidden", !file);
    const hint = form.querySelector("[data-hint]");
    setText(hint, handoff === "hand" ? hint.dataset.hand : hint.dataset.door);
    const ok = !!file || handoff === "hand";
    setFlag(button, "disabled", !ok);
    setText(button, ok ? button.dataset.ready : button.dataset.need);
}

function sync() {
    syncStart();
    syncProof();
}

document.addEventListener("change", sync);
new MutationObserver(() => requestAnimationFrame(sync)).observe(document.body, { childList: true, subtree: true });
sync();

// ---------- web push ----------

function keyBytes(base64) {
    const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

const worker = "serviceWorker" in navigator
    ? navigator.serviceWorker.register(me.dataset.worker).catch(() => null)
    : Promise.resolve(null);

async function subscribe() {
    const registration = await worker;
    if (!registration || !("PushManager" in window) || Notification.permission !== "granted") return;
    try {
        const { vapidPublicKey } = await (await fetch(me.dataset.vapid)).json();
        if (!vapidPublicKey) return;
        const subscription = await registration.pushManager.getSubscription()
            ?? await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(vapidPublicKey) });
        await fetch(me.dataset.subscribe, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
            body: JSON.stringify(subscription),
        });
    } catch (error) {
        console.warn(t("Couldn't turn on notifications"), error);
    }
}

// Starting a shift is a tap, so the browser lets us ask for notifications then
document.addEventListener("submit", (event) => {
    if (!event.target.matches("form[data-start]") || !("Notification" in window)) return;
    if (Notification.permission === "default") Notification.requestPermission().then(subscribe);
    else subscribe();
}, true);

if ("Notification" in window && Notification.permission === "granted") subscribe();
