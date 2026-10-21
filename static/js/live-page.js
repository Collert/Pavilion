// Server-rendered pages that stay current, without rendering anything in the browser.
//
// The page is Django HTML in <main id="page" data-now="<server time, ms>">, with the parts that can change marked
// data-region (each with an id). This script:
// - submits forms (and follows links marked data-live) in the background. The server answers as usual (post,
//   redirect, get); the regions whose HTML changed are swapped in. A different page replaces #page instead.
// - refetches the page when the server sends a nudge on one of `topics` (SSE), and swaps in what changed. If nothing
//   changed, nothing on screen is touched (so your own action's echo is invisible).
// - ticks countdowns: [data-ends="<ms>"] shows the time left (data-done-text once it's up) and [data-bar-ends]
//   [data-bar-total] fills a bar. When one runs out the page is refetched so the server can show what's next
//   (data-chime also rings).
// - forms with data-keypad get an on-screen keypad ([data-key] buttons) for their amount field; data-reset clears
//   the form after it's sent; a form with data-search submits itself as you type (a POST one saves as you type: while
//   you type in a region, only its [data-calc] parts are swapped). A form with data-queue sends its last submit after
//   one in flight instead of dropping it; a region with data-keep is left alone by background answers.
// Regions marked data-transient (toasts) are only taken from responses to your own actions.
// Without JavaScript, everything still works as plain forms and links.

import { onLiveUpdate } from "./live.js";

let offset = 0;  // server clock minus this device's clock
let busy = false;

function readClock(page) {
    const now = Number(page?.dataset.now);
    if (now) offset = now - Date.now();
}

// A region's HTML without what this script keeps up to date itself (countdown text and end times, bar widths) and
// CSRF tokens (masked anew on every page)
const COUNTDOWN = ["data-ends", "data-bar-ends", "data-bar-total"];
function fingerprint(node) {
    const copy = node.cloneNode(true);
    copy.querySelectorAll("[data-ends]").forEach((n) => { n.textContent = ""; });
    copy.querySelectorAll("[data-ends], [data-bar-ends]").forEach((n) => [...COUNTDOWN, "style", "data-ran-out"].forEach((a) => n.removeAttribute(a)));
    copy.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach((n) => n.removeAttribute("value"));
    return copy.outerHTML;
}

// Same region, only the countdowns moved (e.g. +1 minute): update their end times in place, nothing else
function syncCountdowns(live, next) {
    const theirs = [...next.querySelectorAll("[data-ends], [data-bar-ends]")];
    [...live.querySelectorAll("[data-ends], [data-bar-ends]")].forEach((node, n) => {
        for (const name of COUNTDOWN) {
            const value = theirs[n]?.getAttribute(name);
            if (value !== null && value !== undefined && node.getAttribute(name) !== value) {
                node.setAttribute(name, value);
                delete node.dataset.ranOut;
            }
        }
    });
}

// A region you're typing in keeps its fields, but the numbers worked out from them ([data-calc]) still update
function syncCalc(live, next) {
    const mine = live.querySelectorAll("[data-calc]"), theirs = next.querySelectorAll("[data-calc]");
    if (mine.length !== theirs.length) return;
    mine.forEach((node, n) => {
        if (node.outerHTML !== theirs[n].outerHTML) node.replaceWith(document.importNode(theirs[n], true));
    });
}

const typingIn = (node) => node.contains(document.activeElement) && document.activeElement.matches("input, textarea");

// Swap in the regions that changed. Returns false when the page is laid out differently (then replace #page).
function swapRegions(doc, { transient }) {
    const fresh = [...doc.querySelectorAll("[data-region]")];
    const current = [...document.querySelectorAll("[data-region]")];
    const ids = (list) => list.map((n) => n.id).sort().join(" ");
    if (ids(fresh) !== ids(current)) return false;
    for (const next of fresh) {
        if (next.hasAttribute("data-transient") && !transient) continue;
        const live = document.getElementById(next.id);
        if (live.hasAttribute("data-keep")) continue;  // holds something only this browser has (a chosen file)
        if (typingIn(live)) {
            syncCalc(live, next);
            continue;
        }
        if (fingerprint(live) === fingerprint(next)) syncCountdowns(live, next);
        else live.replaceWith(document.importNode(next, true));
    }
    return true;
}

function replacePage(doc) {
    document.getElementById("page").replaceWith(document.importNode(doc.getElementById("page"), true));
    for (const next of doc.querySelectorAll("[data-region]")) {
        const live = document.getElementById(next.id);
        if (live && !document.getElementById("page").contains(live)) live.replaceWith(document.importNode(next, true));
    }
    document.title = doc.title;
    window.scrollTo(0, 0);
}

// Load `url` (GET, or the given POST) and bring the page in line with the answer
async function load(url, init = {}, { transient = true, history: mode = "push" } = {}) {
    let response;
    try {
        response = await fetch(url, { credentials: "same-origin", ...init });
    } catch {
        return;  // offline: the live stream refetches when it reconnects
    }
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const page = doc.getElementById("page");
    if (!page) {
        location.assign(response.url);  // not one of these pages (e.g. the sign-in page): go there properly
        return;
    }
    readClock(page);
    const target = new URL(response.url);
    const samePath = target.pathname === location.pathname;
    if (!(samePath && swapRegions(doc, { transient }))) replacePage(doc);
    if (target.href !== location.href) history[mode === "push" && !samePath ? "pushState" : "replaceState"](null, "", target);
    tick();
}

let queued = null;
async function act(url, init, after, { queue = false } = {}) {
    if (busy) {  // one action at a time: a double tap doesn't send twice
        if (queue) queued = () => act(url, init, after, { queue });  // but typing that's still saving is sent after
        return;
    }
    busy = true;
    try {
        await load(url, init);
        after?.();
    } finally {
        busy = false;
        const next = queued;
        queued = null;
        next?.();
    }
}

const refresh = () => (busy ? null : load(location.href, {}, { transient: false, history: "replace" }));

// Nudges on several topics from one change arrive together: refetch once
let refreshTimer;
const refreshSoon = () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 150);
};

// ---------- countdowns ----------

function fmtLeft(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    const two = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

let audio;
function chime() {
    try {
        audio ??= new AudioContext();
        [0, 0.25, 0.5].forEach((delay, n) => {
            const osc = audio.createOscillator(), gain = audio.createGain();
            osc.frequency.value = n === 2 ? 1046 : 784;
            gain.gain.setValueAtTime(0.0001, audio.currentTime + delay);
            gain.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + delay + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + delay + 0.22);
            osc.connect(gain).connect(audio.destination);
            osc.start(audio.currentTime + delay);
            osc.stop(audio.currentTime + delay + 0.25);
        });
    } catch { /* no sound */ }
}

function tick() {
    const now = Date.now() + offset;
    let ranOut = false, ring = false;
    document.querySelectorAll("[data-ends]").forEach((node) => {
        const left = Number(node.dataset.ends) - now;
        node.textContent = left > 0 ? fmtLeft(left) : node.dataset.doneText ?? "0:00";
        if (left <= 0 && !node.dataset.ranOut) {
            node.dataset.ranOut = "1";
            ranOut = true;
            ring ||= node.hasAttribute("data-chime");
        }
    });
    document.querySelectorAll("[data-bar-ends]").forEach((node) => {
        const total = Number(node.dataset.barTotal), left = Math.max(0, Number(node.dataset.barEnds) - now);
        node.style.width = `${total ? Math.min(100, ((total - left) / total) * 100) : 100}%`;
    });
    if (ring) chime();
    if (ranOut) setTimeout(refreshSoon, 600);  // let the server see it's up, then show what comes next
}

// ---------- wiring ----------

export function livePage({ topics = [] } = {}) {
    readClock(document.getElementById("page"));

    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!form.closest("#page, [data-region]")) return;
        const submitter = event.submitter;
        const target = submitter?.getAttribute("formtarget") || form.getAttribute("target");
        if (target && target !== "_self") return;  // e.g. a printable page in a new tab: let the browser do it
        event.preventDefault();
        const data = new FormData(form, submitter);
        // A button's formaction / formmethod win, so one form can preview with GET and confirm with POST
        const action = submitter?.getAttribute("formaction") || form.getAttribute("action") || location.pathname + location.search;
        const method = (submitter?.getAttribute("formmethod") || form.getAttribute("method") || "get").toLowerCase();
        if (method === "get") {
            const url = new URL(action, location.href);
            url.search = new URLSearchParams(data);
            load(url, {}, { history: "replace" });
            return;
        }
        act(action, { method: "POST", body: data }, () => form.hasAttribute("data-reset") && form.reset(),
            { queue: form.hasAttribute("data-search") || form.hasAttribute("data-queue") });
    });

    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[data-live]");
        if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
            event.preventDefault();
            act(link.href, {});
            return;
        }
        const key = event.target.closest("[data-keypad] [data-key]");
        if (key) {
            const form = key.closest("form");
            const input = form.querySelector('input[name="amount"]');
            const k = key.dataset.key;
            if (k === "back") input.value = input.value.slice(0, -1);
            else if (k === ".") { if (form.hasAttribute("data-decimal") && !input.value.includes(".")) input.value = (input.value || "0") + "."; }
            else if (input.value.length < 6) input.value = input.value === "0" ? k : input.value + k;
        }
    });

    let searchTimer;
    document.addEventListener("input", (event) => {
        const form = event.target.closest("form[data-search]");
        if (!form) return;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => form.requestSubmit(), 300);
    });

    window.addEventListener("popstate", () => load(location.href, {}, { history: "replace" }));
    // The page arrived up to date, so the first connection doesn't refetch; nudges and reconnects do
    for (const topic of topics) onLiveUpdate(topic, refreshSoon, { refreshOnConnect: false });
    tick();
    setInterval(tick, 500);
}
