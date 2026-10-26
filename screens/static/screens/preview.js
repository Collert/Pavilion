// The admin screen editor's preview (admin_portal/views.py: screen_preview). Picking a widget or an empty slot, and
// dropping a widget from the editor's library onto an empty slot, are told to the editor page around this frame; it
// posts them as ops (admin_portal/static/admin_portal/screens.js). The frame never reloads: the editor tells it what's
// selected (only the outline moves) or that the draft changed (the new HTML is fetched and swapped in, no flash).
const tell = (message) => window.parent.postMessage(message, location.origin);

document.addEventListener("click", (event) => {
    const node = event.target.closest("[data-path]");
    event.preventDefault();
    if (node) tell({ type: "screen-pick", path: node.dataset.path });
}, true);

document.addEventListener("dragover", (event) => {
    const slot = event.target.closest(".slot-edit");
    if (!slot || !event.dataTransfer.types.includes("text/x-widget")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    document.querySelectorAll(".slot-edit.is-over").forEach((s) => s !== slot && s.classList.remove("is-over"));
    slot.classList.add("is-over");
});
document.addEventListener("dragleave", (event) => event.target.closest?.(".slot-edit")?.classList.remove("is-over"));
document.addEventListener("drop", (event) => {
    const slot = event.target.closest(".slot-edit");
    const widget = event.dataTransfer.getData("text/x-widget");
    if (!slot || !widget) return;
    event.preventDefault();
    tell({ type: "screen-drop", path: slot.dataset.path, widget });
});

function select(path) {
    document.querySelectorAll(".is-selected").forEach((node) => node.classList.remove("is-selected"));
    if (path) document.querySelector(`[data-path="${CSS.escape(path)}"]`)?.classList.add("is-selected");
}

// The draft changed: fetch the preview again and swap the page in place (stylesheets a new widget needs come first)
let latest = 0;
async function refresh(url) {
    const ticket = ++latest;
    let html;
    try {
        html = await (await fetch(url, { credentials: "same-origin" })).text();
    } catch {
        return;
    }
    if (ticket !== latest) return;  // a newer change is on its way
    const doc = new DOMParser().parseFromString(html, "text/html");
    const page = doc.getElementById("page");
    if (!page) return;
    const have = new Set([...document.querySelectorAll('link[rel="stylesheet"]')].map((l) => l.getAttribute("href")));
    const missing = [...doc.querySelectorAll('link[rel="stylesheet"]')].filter((l) => !have.has(l.getAttribute("href")));
    await Promise.all(missing.map((link) => new Promise((done) => {
        const copy = document.importNode(link, true);
        copy.addEventListener("load", done);
        copy.addEventListener("error", done);
        document.head.append(copy);
    })));
    const current = document.getElementById("page");
    current.replaceWith(document.importNode(page, true));
    hidePreviewedPopup();
}

// A pop-up shown with Preview goes away after its "Show for" time, like on the real screen
let hideTimer;
function hidePreviewedPopup() {
    clearTimeout(hideTimer);
    const slot = document.querySelector("[data-preview-seconds]");
    if (!slot?.querySelector(".popup")) return;
    hideTimer = setTimeout(() => slot.querySelector(".popup")?.remove(), Number(slot.dataset.previewSeconds || 6) * 1000);
}
hidePreviewedPopup();

window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || typeof event.data !== "object" || !event.data) return;
    if (event.data.type === "preview-select") select(event.data.path);
    if (event.data.type === "preview-refresh" && typeof event.data.url === "string" && event.data.url.startsWith("/")) refresh(event.data.url);
});
tell({ type: "preview-ready" });
