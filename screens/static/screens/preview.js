// The admin screen editor's preview (admin_portal/views.py: screen_preview). Picking a widget or an empty slot, and
// dropping a widget from the editor's library onto an empty slot, are told to the editor page around this frame; it
// posts them as ops (admin_portal/static/admin_portal/screens.js).
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
