// A music channel's page (audio/templates/audio/music.html). The server renders everything and live-page.js keeps it
// current; this adds what HTML can't do:
// - the song's clock: elapsed and remaining time and the position slider move every half second;
// - sliders post when let go (seek, volume: [data-submit-on-change]);
// - dragging the queue into order (posts op=move:<entry key> with the new index);
// - a long press or right-click on a song or playlist opens its sheet (play now, play next, add to queue);
// - the playing song is kept in view in the queue.

const page = document.getElementById("page");
const skew = Number(page?.dataset.now || Date.now()) - Date.now();  // server clock minus ours
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// ---------- the clock ----------

let holding = null;  // the slider being dragged: left alone until it's let go
function tick() {
    const clock = document.querySelector("[data-clock]");
    if (!clock) return;
    const length = Number(clock.dataset.length) || 1;
    const playing = clock.hasAttribute("data-playing");
    const pos = Math.min(length, Math.max(0, playing ? (Date.now() + skew - Number(clock.dataset.started)) / 1000 : Number(clock.dataset.offset)));
    const range = clock.querySelector("[data-seek]");
    if (range && range !== holding) {
        range.value = String(pos);
        range.style.setProperty("--pct", `${(pos / length) * 100}%`);
        put(clock.querySelector("[data-pos]"), fmt(pos));
        put(clock.querySelector("[data-left]"), `-${fmt(length - pos)}`);
    }
}
function put(node, text) {
    if (node.textContent !== text) node.textContent = text;  // only real changes (a watcher below sees text changes)
}
setInterval(tick, 500);
tick();

document.addEventListener("pointerdown", (event) => {
    if (event.target.matches?.(".au-range")) holding = event.target;
});
document.addEventListener("pointerup", () => { setTimeout(() => { holding = null; }, 0); });

document.addEventListener("input", (event) => {
    const t = event.target;
    if (!t.matches(".au-range")) return;
    t.style.setProperty("--pct", `${(Number(t.value) / Number(t.max || 100)) * 100}%`);
    if (t.matches("[data-vol]")) document.querySelector("[data-vol-n]").textContent = `${t.value}%`;
    if (t.matches("[data-seek]")) {
        const clock = t.closest("[data-clock]");
        clock.querySelector("[data-pos]").textContent = fmt(Number(t.value));
        clock.querySelector("[data-left]").textContent = `-${fmt(Number(clock.dataset.length) - Number(t.value))}`;
    }
});

document.addEventListener("change", (event) => {
    const t = event.target;
    if (!t.matches("[data-submit-on-change]") || !t.form) return;
    holding = null;
    t.blur();  // so the answer can redraw it
    t.form.requestSubmit();
});

// ---------- dragging the queue ----------

let dragging = null;
const rowOf = (target) => target.closest?.("[data-queue] .au-q-row");
document.addEventListener("dragstart", (event) => {
    const row = rowOf(event.target);
    if (!row) return;
    dragging = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.key);
});
document.addEventListener("dragover", (event) => {
    const row = rowOf(event.target);
    if (!dragging || !row) return;
    event.preventDefault();
    document.querySelectorAll(".au-q-row.over").forEach((r) => r !== row && r.classList.remove("over"));
    row.classList.toggle("over", row !== dragging);
});
document.addEventListener("drop", (event) => {
    const row = rowOf(event.target);
    if (!dragging || !row) return;
    event.preventDefault();
    if (row !== dragging) {
        const from = Number(dragging.dataset.index), to = Number(row.dataset.index);
        row.parentNode.insertBefore(dragging, from < to ? row.nextSibling : row);
        const form = document.getElementById("au-ctl");
        form.querySelector("[data-move-to]").value = String(to);  // its place once it has left its old one
        const button = form.querySelector("[data-move]");
        button.value = `move:${dragging.dataset.key}`;
        form.requestSubmit(button);
    }
});
document.addEventListener("dragend", () => {
    dragging?.classList.remove("dragging");
    dragging = null;
    document.querySelectorAll(".au-q-row.over").forEach((r) => r.classList.remove("over"));
});

// ---------- the sheet on a long press or right-click ----------

function openSheet(href) {
    const link = document.createElement("a");
    link.href = href;
    link.dataset.live = "";
    link.hidden = true;
    page.append(link);
    link.click();
    link.remove();
}

let press = null, pressed = false;
document.addEventListener("contextmenu", (event) => {
    const target = event.target.closest?.("[data-sheet-href]");
    if (!target) return;
    event.preventDefault();
    openSheet(target.dataset.sheetHref);
});
document.addEventListener("pointerdown", (event) => {
    const target = event.target.closest?.("[data-sheet-href]");
    if (!target || event.pointerType === "mouse") return;
    pressed = false;
    clearTimeout(press);
    press = setTimeout(() => { pressed = true; openSheet(target.dataset.sheetHref); }, 480);
});
for (const name of ["pointerup", "pointercancel", "pointerleave"]) document.addEventListener(name, () => clearTimeout(press));
document.addEventListener("click", (event) => {
    if (pressed && event.target.closest?.("[data-sheet-href]")) {  // the press that opened the sheet doesn't also play
        event.preventDefault();
        event.stopPropagation();
        pressed = false;
    }
}, true);

// ---------- keep the playing song in view ----------

let shownKey = null;
function showCurrent() {
    const box = document.querySelector("[data-queue]");
    const row = box?.querySelector(".au-q-row.cur");
    if (!row || row.dataset.key === shownKey) return;
    shownKey = row.dataset.key;
    const top = row.offsetTop - box.offsetTop - 60;
    if (row.offsetTop - box.offsetTop < box.scrollTop || row.offsetTop - box.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTo({ top, behavior: "smooth" });
}
new MutationObserver(() => { showCurrent(); tick(); }).observe(page, { childList: true, subtree: true });
showCurrent();
