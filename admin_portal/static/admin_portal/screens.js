// The screen editor (admin_portal/templates/admin_portal/screen.html) and its preview iframe: a node picked in the
// preview, or a library widget dropped on one of its empty slots, is posted as an op ("sel:<path>", "drop:<path>:<name>")
// through the form's hidden [data-op] button, so live-page.js sends it like any other button.
function post(op) {
    const form = document.querySelector("form[data-screen-editor]");
    const button = form?.querySelector("[data-op]");
    if (!button) return;
    button.value = op;
    form.requestSubmit(button);
}

window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || typeof event.data !== "object" || !event.data) return;
    const { type, path, widget } = event.data;
    if (!/^r(-\d+)*$/.test(path || "")) return;
    if (type === "screen-pick") post("sel:" + path);
    if (type === "screen-drop" && /^[a-z][a-z0-9_]*$/.test(widget || "")) post("drop:" + path + ":" + widget);
});

document.addEventListener("dragstart", (event) => {
    const item = event.target.closest?.("[data-widget]");
    if (!item) return;
    event.dataTransfer.setData("text/x-widget", item.dataset.widget);
    event.dataTransfer.effectAllowed = "copy";
    document.body.classList.add("sc-dragging");
});
document.addEventListener("dragend", () => document.body.classList.remove("sc-dragging"));
