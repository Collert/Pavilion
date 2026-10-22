// The screen editor (admin_portal/templates/admin_portal/screen.html) and its preview iframe.
// - A node picked in the preview, or a library widget dropped on one of its empty slots, is posted as an op
//   ("sel:<path>", "drop:<path>:<name>") through the form's hidden [data-op] button, so live-page.js sends it.
// - The frame itself is never replaced (its region is data-keep). After each answer, [data-preview-state] says what
//   it should show: when only the selection moved, the frame just moves its outline; when the draft changed (or a
//   pop-up preview), it fetches its page again and swaps it in, so it never reloads and never flashes.
function post(op) {
    const form = document.querySelector("form[data-screen-editor]");
    const button = form?.querySelector("[data-op]");
    if (!button) return;
    button.value = op;
    form.requestSubmit(button);
}

const frame = () => document.querySelector("iframe[data-preview]");
let shown = null;  // {v, popup, sel, url} the frame shows

function sync() {
    const state = document.querySelector("[data-preview-state]")?.dataset;
    const target = frame();
    if (!state || !target?.contentWindow) return;
    target.closest("[data-ratio]")?.style.setProperty("--r", state.ratio);
    const next = { v: state.v, popup: state.popup, sel: state.sel, url: state.url };
    if (shown && shown.v === next.v && shown.popup === next.popup) {
        if (shown.sel !== next.sel) target.contentWindow.postMessage({ type: "preview-select", path: next.sel }, location.origin);
    } else {
        target.contentWindow.postMessage({ type: "preview-refresh", url: next.url }, location.origin);
    }
    shown = next;
}

window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || typeof event.data !== "object" || !event.data) return;
    const { type, path, widget } = event.data;
    if (type === "preview-ready") {  // a fresh frame: bring it up to date
        shown = null;
        sync();
        return;
    }
    if (!/^r(-\d+)*$/.test(path || "")) return;
    if (type === "screen-pick") {
        frame()?.contentWindow?.postMessage({ type: "preview-select", path }, location.origin);  // move the outline now
        if (shown) shown.sel = path;
        post("sel:" + path);
    }
    if (type === "screen-drop" && /^[a-z][a-z0-9_]*$/.test(widget || "")) post("drop:" + path + ":" + widget);
});

// Each answer swaps regions: look again for what the frame should show
new MutationObserver(() => sync()).observe(document.body, { childList: true, subtree: true });

document.addEventListener("dragstart", (event) => {
    const item = event.target.closest?.("[data-widget]");
    if (!item) return;
    event.dataTransfer.setData("text/x-widget", item.dataset.widget);
    event.dataTransfer.effectAllowed = "copy";
    document.body.classList.add("sc-dragging");
});
document.addEventListener("dragend", () => document.body.classList.remove("sc-dragging"));
