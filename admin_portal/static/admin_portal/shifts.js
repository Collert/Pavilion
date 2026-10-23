// The admin's Shifts (admin_portal/templates/admin_portal/shifts.html). The server draws the schedule; this adds:
// - dragging a shift along its row or onto another one (posts op=move with who, role and the new start, to the
//   half hour, keeping where it was grabbed under the pointer);
// - clicking an empty spot on a row opens a new shift for that person (and role) starting an hour before it.

const STEP = 30;
let drag = null;  // {id, grab: minutes between the shift's start and where it was grabbed, len}

const minuteAt = (lane, clientX) => {
    const r = lane.getBoundingClientRect();
    return ((clientX - r.left) / r.width) * 1440;
};

document.addEventListener("dragstart", (event) => {
    const block = event.target.closest?.("[data-shift]");
    if (!block) return;
    const lane = block.closest("[data-lane]");
    const start = Number(block.dataset.start), end = Number(block.dataset.end);
    drag = { id: block.dataset.shift, grab: minuteAt(lane, event.clientX) - start, len: end - start, block };
    block.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", block.dataset.shift);
});

document.addEventListener("dragover", (event) => {
    const lane = event.target.closest?.("[data-lane]");
    if (!drag || !lane) return;
    event.preventDefault();
    document.querySelectorAll(".sh-lane.over").forEach((l) => l !== lane && l.classList.remove("over"));
    lane.classList.add("over");
});

document.addEventListener("drop", (event) => {
    const lane = event.target.closest?.("[data-lane]");
    if (!drag || !lane) return;
    event.preventDefault();
    let start = Math.round((minuteAt(lane, event.clientX) - drag.grab) / STEP) * STEP;
    start = Math.max(0, Math.min(1440 - drag.len, start));
    const form = document.getElementById("sh-move");
    form.elements.id.value = drag.id;
    form.elements.user.value = lane.dataset.user;
    form.elements.role.value = lane.dataset.role;
    form.elements.start.value = String(start);
    form.requestSubmit();
});

document.addEventListener("dragend", () => {
    drag?.block.classList.remove("dragging");
    drag = null;
    document.querySelectorAll(".sh-lane.over").forEach((l) => l.classList.remove("over"));
});

document.addEventListener("click", (event) => {
    const lane = event.target.closest?.("[data-lane]");
    if (!lane || event.target.closest("[data-shift]")) return;
    const board = lane.closest("[data-new-href]");
    const at = Math.floor(minuteAt(lane, event.clientX) / STEP) * STEP;
    const start = Math.max(0, Math.min(1440 - 240, at - 60));
    const params = new URLSearchParams({ start: String(start) });
    if (lane.dataset.user) params.set("u", lane.dataset.user);
    if (lane.dataset.newRole) params.set("role", lane.dataset.newRole);
    const link = document.createElement("a");
    link.href = `${board.dataset.newHref}&${params}`;
    link.dataset.live = "";
    link.hidden = true;
    board.append(link);
    link.click();
    link.remove();
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelector("#sh-drawer [data-close]")?.click();
});
