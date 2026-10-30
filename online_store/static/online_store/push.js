// The store app in the browser (online_store/app.py), on every store page: its service worker, and this browser's
// push subscription kept in step with the server (sync while notifications are on, forget once they're off).
// On an order page, the notification card ([data-push-order]) follows the order:
// - notifications already on: the order's updates come here without asking (unless order updates were turned off
//   for this device on the account page);
// - just placed (?placed=1) and never asked: a little sheet offers them, and its button asks the browser;
// - otherwise the card's button asks; blocked notifications can't be asked for again, so it says how to unblock them;
// - iPhones outside the Home Screen app can't have them at all: the card says how to add it.
// On the account's Notifications page, this device's card is marked ([data-push-device] = a hash of its endpoint), and
// a device without notifications gets "Turn them on here" ([data-push-here]).
import * as push from "../js/push.js";

const me = document.getElementById("store-push");
const worker = push.register(me.dataset.worker, me.dataset.scope);

async function sync() {
    const registration = await worker;
    const subscription = await push.current(registration);
    const was = push.remembered("store");
    if (subscription) {
        const now = [me.dataset.user, subscription.endpoint, document.documentElement.lang].join("|");
        if (was === now) return subscription;
        const response = await push.post(me.dataset.sync, { subscription: subscription.toJSON() });
        if (response.ok) push.remember("store", now);
    } else if (was) {  // turned off since: the server can let it go
        const response = await push.post(me.dataset.forget, { endpoint: was.split("|")[1] });
        if (response.ok) push.remember("store", "");
    }
    return subscription;
}

// ---------- the order page's card ----------

function setState(card, state) {
    card.dataset.state = state;
    card.hidden = state === "none";
}

async function link(card, subscription, auto) {
    const response = await push.post(card.dataset.pushOrder, { subscription: subscription.toJSON(), auto });
    if (!response.ok) throw new Error(String(response.status));
    const answer = await response.json();
    push.remember("store", [me.dataset.user, subscription.endpoint, document.documentElement.lang].join("|"));
    setState(card, answer.linked ? "on" : "off");
}

async function ask(card) {
    setState(card, "asking");
    try {
        const subscription = await push.subscribe(await worker, me.dataset.key);
        if (subscription === "blocked") setState(card, "blocked");
        else if (subscription) await link(card, subscription, false);
        else setState(card, "off");
    } catch (error) {
        console.warn(error);
        setState(card, "error");
    }
}

async function startCard(card) {
    if (!push.supported) {
        setState(card, push.needsInstall() ? "install" : "none");
        return;
    }
    const subscription = await sync().catch(() => null);
    if (subscription) {
        await link(card, subscription, true).catch(() => setState(card, "off"));
        return;
    }
    setState(card, "off");
    const placed = new URL(location.href).searchParams.has("placed");
    if (placed) history.replaceState(history.state, "", location.pathname);  // the sheet shows once
    const sheet = document.getElementById("nb-notify-sheet");
    if (placed && push.permission() === "default" && sheet?.showModal) sheet.showModal();
}

document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-push-ask]");
    if (!button) return;
    const card = document.querySelector("[data-push-order]");
    button.closest("dialog")?.close();
    if (card) ask(card);
});

// ---------- the account's Notifications page ----------

async function hashOf(endpoint) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

async function markDevices(subscription) {
    const here = subscription ? await hashOf(subscription.endpoint) : "";
    let found = false;
    document.querySelectorAll("[data-push-device]").forEach((device) => {
        const mine = device.dataset.pushDevice === here;
        found ||= mine;
        device.classList.toggle("here", mine);
        const pill = device.querySelector("[data-this-device]");
        if (pill && pill.hidden === mine) pill.hidden = !mine;
    });
    const offer = document.querySelector("[data-push-here]");
    if (offer) {
        offer.hidden = found || !push.supported;
        offer.querySelector("[data-push-blocked]").hidden = push.permission() !== "denied";
    }
}

async function startAccount() {
    let subscription = await sync().catch(() => null);
    await markDevices(subscription);
    // live-page swaps the device list in after each button: mark it again
    new MutationObserver(() => markDevices(subscription)).observe(document.getElementById("page"), { childList: true, subtree: true });
    document.querySelector("[data-push-turn-on]")?.addEventListener("click", async (event) => {
        event.target.disabled = true;
        try {
            const result = await push.subscribe(await worker, me.dataset.key);
            if (result && result !== "blocked") {
                subscription = result;
                push.remember("store", "");
                await sync();
                location.reload();
                return;
            }
        } catch (error) {
            console.warn(error);
        }
        event.target.disabled = false;
        markDevices(subscription);
    });
}

const card = document.querySelector("[data-push-order]");
if (card) startCard(card);
else if (document.querySelector("[data-push-here]")) startAccount();
else sync().catch(() => {});
