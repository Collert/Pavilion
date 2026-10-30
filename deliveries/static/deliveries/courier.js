// The courier app's bits that HTML can't do (the pages are server-rendered, see deliveries/views.py):
// - the start button says what you picked ("Start shift · Cycling") and waits until you pick;
// - the drop-off photo shows a preview, and "Complete delivery" waits for a photo when you left it at the door;
// - web push: the service worker is registered, and starting a shift asks for notifications and saves the
//   subscription so new orders reach this phone.
// live-page.js swaps parts of the page in and out, so everything here works by delegation and re-syncs on changes.

import * as push from "../js/push.js";

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

const worker = push.register(me.dataset.worker, me.dataset.scope);
// The service worker used to sit in /static/, where it couldn't reach these pages: its subscription goes with it
navigator.serviceWorker?.getRegistrations?.().then((all) => all.filter((r) => r.scope.includes("/static/")).forEach((r) => r.unregister()));

async function save(subscription) {
    if (!subscription || subscription === "blocked") return;
    if (push.remembered("courier") === subscription.endpoint) return;
    const response = await push.post(me.dataset.subscribe, subscription.toJSON());
    if (response.ok) push.remember("courier", subscription.endpoint);
}

// Starting a shift is a tap, so the browser lets us ask for notifications then
document.addEventListener("submit", (event) => {
    if (!event.target.matches("form[data-start]") || !push.supported) return;
    worker.then((registration) => push.subscribe(registration, me.dataset.key)).then(save)
        .catch((error) => console.warn(t("Couldn't turn on notifications"), error));
}, true);

worker.then(push.current).then(save).catch(() => {});
