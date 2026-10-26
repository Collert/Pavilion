// The admin's Library (admin_portal/templates/admin_portal/library.html). The page is server-rendered and kept in
// step by live-page.js; this adds what HTML can't do:
// - the selection: "N songs selected", select all, and how many each playlist would gain ("+3" / "Already in");
// - previews: a song's artwork plays it in this browser (one at a time);
// - dragging a playlist's songs into order (posts op=reorder with the new order);
// - the song drawer's artwork picker and Save, which wakes up once something changed; the Add songs count;
// - the Add music dialog: each file's tags are read here (audio/tags.js), checked and filled in, then the file is
//   uploaded untouched, with what was read, to admin_library_upload. The server never opens a song.
// Text from files goes into the page with textContent only.

import { probe, readTags } from "../audio/tags.js";

const songsText = (n) => interpolate(ngettext("%s song", "%s songs", n), [n]);
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function minsText(seconds) {
    const h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
    return h ? interpolate(gettext("%s hr %s min"), [h, m]) : interpolate(gettext("%s min"), [m]);
}
const mb = (bytes) => `${(bytes / 1000 ** 2).toFixed(1)} MB`;

// ---------- selection ----------

function syncSelection() {
    const form = document.getElementById("lib-rows");
    if (!form) return;
    const boxes = [...form.querySelectorAll('input[name="sel"]')];
    const picked = boxes.filter((b) => b.checked).map((b) => b.value);
    const count = form.querySelector("[data-sel-count]");
    if (count) count.textContent = interpolate(ngettext("%s song selected", "%s songs selected", picked.length), [picked.length]);
    const all = form.querySelector("[data-all]");
    if (all) {
        all.checked = boxes.length > 0 && picked.length === boxes.length;
        all.indeterminate = picked.length > 0 && !all.checked;
    }
    form.querySelectorAll("[data-has]").forEach((button) => {
        const has = new Set(button.dataset.has.split(","));
        const fresh = picked.filter((id) => !has.has(id)).length;
        button.querySelector("[data-hint]").textContent = fresh ? `+${fresh}` : gettext("Already in");
    });
    if (!picked.length) form.querySelector(".lib-menu")?.removeAttribute("open");
}

function syncAdd() {
    const form = document.querySelector("[data-add-form]");
    if (!form) return;
    const picked = [...form.querySelectorAll('input[name="sel"]:checked')];
    const secs = picked.reduce((sum, b) => sum + Number(b.dataset.secs || 0), 0);
    form.querySelector("[data-add-count]").textContent = picked.length ? `${songsText(picked.length)} · ${minsText(secs)}` : gettext("Pick songs to add");
    const button = form.querySelector("[data-add-btn]");
    button.disabled = !picked.length;
    button.textContent = picked.length ? interpolate(ngettext("Add %s song", "Add %s songs", picked.length), [picked.length]) : gettext("Add songs");
}

document.addEventListener("change", (event) => {
    const t = event.target;
    if (t.matches("#lib-rows [data-all]")) {
        document.querySelectorAll('#lib-rows input[name="sel"]').forEach((b) => { b.checked = t.checked; });
    }
    if (t.closest("#lib-rows")) syncSelection();
    if (t.closest("[data-add-form]")) syncAdd();
    if (t.matches("[data-art-input]")) pickArtwork(t);
    if (t.closest("[data-dirty-form]")) t.closest("[data-dirty-form]").classList.add("dirty");
});
document.addEventListener("input", (event) => {
    event.target.closest("[data-dirty-form]")?.classList.add("dirty");
});

// ---------- previews ----------

let preview = null, previewSrc = "";
function markPreview() {
    document.querySelectorAll("[data-preview-src]").forEach((b) => {
        const on = !!previewSrc && b.dataset.previewSrc === previewSrc;
        if (b.classList.contains("playing") !== on) b.classList.toggle("playing", on);
    });
}
new MutationObserver(markPreview).observe(document.body, { childList: true, subtree: true });

document.addEventListener("click", (event) => {
    const t = event.target;
    const play = t.closest("[data-preview-src]");
    if (play) {
        const src = play.dataset.previewSrc;
        if (preview && previewSrc === src) {
            preview.pause();
            previewSrc = "";
        } else {
            preview ??= new Audio();
            preview.onended = () => { previewSrc = ""; markPreview(); };
            preview.src = src;
            preview.play().catch(() => { previewSrc = ""; markPreview(); });
            previewSrc = src;
        }
        markPreview();
        return;
    }
    if (t.closest("[data-clear]")) {
        document.querySelectorAll('#lib-rows input[name="sel"]').forEach((b) => { b.checked = false; });
        syncSelection();
    }
    if (t.closest("[data-art-remove]")) removeArtwork(t.closest("form"));
    if (t.closest("[data-upload-open]")) openUpload();
});

document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || document.getElementById("upload-music")?.open) return;
    const close = document.querySelector("#lib-drawer [data-close], #lib-add [data-close]");
    if (close) close.click();
    else document.querySelector(".lib-menu[open]")?.removeAttribute("open");
});

// ---------- playlist order ----------

let dragging = null;
const rowOf = (target) => target.closest?.(".in-pl .lib-row");
document.addEventListener("dragstart", (event) => {
    const row = rowOf(event.target);
    if (!row) return;
    dragging = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.id);
});
document.addEventListener("dragover", (event) => {
    const row = rowOf(event.target);
    if (!dragging || !row) return;
    event.preventDefault();
    document.querySelectorAll(".lib-row.over").forEach((r) => r !== row && r.classList.remove("over"));
    row.classList.toggle("over", row !== dragging);
});
document.addEventListener("drop", (event) => {
    const row = rowOf(event.target);
    if (!dragging || !row) return;
    event.preventDefault();
    if (row !== dragging) {
        const rows = [...row.parentNode.children];
        row.parentNode.insertBefore(dragging, rows.indexOf(dragging) < rows.indexOf(row) ? row.nextSibling : row);
        const form = document.getElementById("lib-rows");
        form.querySelector("[data-order]").value = [...form.querySelectorAll(".lib-row")].map((r) => r.dataset.id).join(",");
        form.requestSubmit(form.querySelector("[data-reorder]"));
    }
});
document.addEventListener("dragend", () => {
    dragging?.classList.remove("dragging");
    dragging = null;
    document.querySelectorAll(".lib-row.over").forEach((r) => r.classList.remove("over"));
});

// ---------- song drawer artwork ----------

function pickArtwork(input) {
    const file = input.files?.[0];
    const form = input.closest("form");
    if (!file) return;
    const box = form.querySelector("[data-art-box]");
    box.style.backgroundImage = `url("${URL.createObjectURL(file)}")`;
    box.classList.remove("blank");
    form.querySelector("[data-art-label]").textContent = gettext("Replace artwork");
    form.querySelector("[data-art-remove]").hidden = false;
    form.querySelector("[data-art-clear]").value = "";
    form.closest("[data-region]")?.setAttribute("data-keep", "");  // keep the chosen picture until Save
}

function removeArtwork(form) {
    const box = form.querySelector("[data-art-box]");
    box.style.backgroundImage = "";
    box.classList.add("blank");
    form.querySelector("[data-art-input]").value = "";
    form.querySelector("[data-art-clear]").value = "1";
    form.querySelector("[data-art-label]").textContent = gettext("Upload artwork");
    form.querySelector("[data-art-remove]").hidden = true;
    form.classList.add("dirty");
}

document.addEventListener("submit", (event) => {
    if (event.target.matches("[data-dirty-form]")) document.getElementById("lib-drawer")?.removeAttribute("data-keep");
}, true);

// ---------- Add music ----------

const FORMATS = { mp3: "MP3", m4a: "M4A", aac: "AAC", flac: "FLAC", ogg: "OGG", oga: "OGG", opus: "OPUS", wav: "WAV" };
const KEYS = ["title", "artist", "album", "year", "genre"];
let U = null;  // {files, cur, over, busy}
let seq = 0;
const dlg = () => document.getElementById("upload-music");
const $ = (sel) => dlg().querySelector(sel);

function openUpload() {
    U = { files: [], cur: null, over: false, busy: false, total: 0 };
    delete dlg().dataset.shown;
    $("[data-up-rights]").checked = false;
    $("[data-up-error]").hidden = true;
    dlg().showModal();
    render();
}

function closeUpload() {
    if (!U || U.busy) return;
    U.files.forEach((f) => f.artUrl && URL.revokeObjectURL(f.artUrl));
    U = null;
    dlg().close();
}

function addFiles(list) {
    const max = Number(dlg().dataset.maxSize);
    for (const file of list) {
        const ext = file.name.split(".").pop().toLowerCase();
        const fmt = FORMATS[ext] || "";
        const f = { id: ++seq, file, size: file.size, fmt, dur: 0, kbps: 0, progress: 0, tags: {}, art: null, artUrl: "", tagArt: false,
            title: "", artist: "", album: "", year: "", genre: "", status: "reading", reason: "" };
        if (!fmt) Object.assign(f, { status: "error", reason: gettext("Not an audio file, will be skipped") });
        else if (file.size > max) Object.assign(f, { status: "error", reason: gettext("Over 200 MB, will be skipped") });
        U.files.push(f);
        if (f.status === "reading") read(f);
    }
    if (!current()) U.cur = U.files.find((f) => f.status !== "error")?.id ?? null;
    render();
}

async function read(f) {
    f.progress = 35;
    const [tags, dur] = await Promise.all([readTags(f.file), probe(f.file)]);
    if (!U || !U.files.includes(f)) return;
    if (dur === null) {
        Object.assign(f, { status: "error", reason: gettext("This browser can’t play it, will be skipped") });
    } else {
        f.dur = dur;
        f.kbps = dur ? Math.round((f.size * 8) / dur / 1000) : 0;
        f.tags = Object.fromEntries(KEYS.map((k) => [k, tags[k] || ""]));
        Object.assign(f, f.tags, { status: "ready", progress: 100 });
        if (tags.picture) setArt(f, tags.picture, true);
    }
    const wasShown = U.cur === f.id;
    if (wasShown && f.status === "error") U.cur = U.files.find((x) => x.status !== "error")?.id ?? null;
    render(wasShown);  // fill the form only if it was waiting for this file (never under someone's typing)
}

function setArt(f, blob, fromFile) {
    if (f.artUrl) URL.revokeObjectURL(f.artUrl);
    f.art = blob;
    f.artUrl = blob ? URL.createObjectURL(blob) : "";
    f.tagArt = !!blob && fromFile;
}

const current = () => U?.files.find((f) => f.id === U.cur && f.status !== "error") || null;
const tagMissing = (f) => KEYS.filter((k) => !f.tags[k]).length + (f.tagArt ? 0 : 1);
const baseName = (f) => f.file.name.replace(/\.[^.]+$/, "");

function pill(f) {
    if (f.status === "error") return [f.reason.includes("200") ? gettext("Too big") : gettext("Not audio"), "red"];
    if (f.status === "reading") return [gettext("Reading"), "grey"];
    if (f.status === "uploading") return [gettext("Uploading"), "grey"];
    if (f.status === "done") return [gettext("Added"), "green"];
    if (f.status === "failed") return [gettext("Failed"), "red"];
    const miss = tagMissing(f);
    if (miss === 6) return [gettext("No tags"), "grey"];
    if (miss) return [gettext("Some info"), "amber"];
    return [gettext("All found"), "green"];
}

function renderList() {
    const box = $("[data-up-files]");
    const tpl = document.getElementById("up-file-tpl");
    box.replaceChildren(...U.files.map((f) => {
        const node = tpl.content.firstElementChild.cloneNode(true);
        const err = f.status === "error";
        node.classList.toggle("on", f.id === U.cur && !err);
        node.classList.toggle("err", err);
        const pick = node.querySelector(".up-file-btn");
        pick.disabled = err;
        pick.setAttribute("aria-selected", String(f.id === U.cur));
        pick.addEventListener("click", () => { U.cur = f.id; render(true); });
        const art = node.querySelector(".up-file-art");
        if (f.artUrl) art.style.backgroundImage = `url("${f.artUrl}")`;
        art.classList.toggle("reading", f.status === "reading");
        art.classList.toggle("blank", !f.artUrl);
        art.classList.toggle("bad", err);
        node.querySelector(".up-file-name").textContent = err ? f.reason : f.status === "reading" ? gettext("Reading tags…") : (f.title || gettext("No title yet"));
        node.querySelector(".up-file-sub").textContent = f.file.name + (err ? "" : ` · ${mb(f.size)}`);
        const bar = node.querySelector(".up-file-bar");
        bar.hidden = !["reading", "uploading"].includes(f.status);
        bar.firstElementChild.style.width = `${Math.min(100, f.progress)}%`;
        const [text, tone] = pill(f);
        const p = node.querySelector(".up-pill");
        p.textContent = text;
        p.className = `up-pill ${tone}`;
        const rm = node.querySelector(".up-rm");
        rm.setAttribute("aria-label", interpolate(gettext("Remove %s"), [f.file.name]));
        rm.disabled = U.busy;
        rm.addEventListener("click", () => {
            if (f.artUrl) URL.revokeObjectURL(f.artUrl);
            U.files = U.files.filter((x) => x !== f);
            if (U.cur === f.id) U.cur = U.files.find((x) => x.status !== "error")?.id ?? null;
            render(true);
        });
        return node;
    }));
}

const TAGS = {
    found: () => [gettext("From file"), "green"], edited: () => [gettext("Edited"), "grey"],
    mine: () => [gettext("Added by you"), "grey"], none: () => [gettext("Not in file"), "amber"],
};
function fieldTag(f, k) {
    if (f.tags[k] && f[k] === f.tags[k]) return TAGS.found();
    if (f.tags[k]) return TAGS.edited();
    if (f[k]) return TAGS.mine();
    return TAGS.none();
}
function setTag(node, [text, tone]) {
    node.textContent = text;
    node.className = `up-tag ${tone}`;
}

function renderPane(fill) {
    const f = current();
    const reading = f?.status === "reading";
    $('[data-pane="none"]').hidden = !!f;
    $('[data-pane="none"]').textContent = U.files.length ? gettext("Pick a file on the left to check its details.") : gettext("Add some audio files to get started. We’ll read their tags as they upload.");
    $('[data-pane="wait"]').hidden = !reading;
    $('[data-pane="ready"]').hidden = !f || reading;
    if (reading) $("[data-wait-text]").textContent = interpolate(gettext("Reading tags from %s"), [f.file.name]);
    if (!f || reading) return;
    const ready = U.files.filter((x) => x.status === "ready");
    for (const k of KEYS) {
        const input = $(`[data-f="${k}"]`);
        if (fill) input.value = f[k];
        input.placeholder = k === "title" && !f.title ? baseName(f) : input.dataset.ph ?? (input.dataset.ph = input.placeholder);
        input.disabled = U.busy;
        setTag($(`[data-tag="${k}"]`), fieldTag(f, k));
        input.classList.toggle("found", !!f.tags[k] && f[k] === f.tags[k]);
        const act = $(`[data-act="${k}"]`);
        if (!act) continue;
        if (k === "title") {
            act.hidden = !!f.title;
            act.textContent = gettext("Use file name");
        } else {
            act.hidden = !f[k] || ready.length < 2;
            act.textContent = interpolate(gettext("Use for all %s"), [ready.length]);
        }
    }
    const art = $("[data-f-art]");
    art.style.backgroundImage = f.artUrl ? `url("${f.artUrl}")` : "";
    art.classList.toggle("has", !!f.artUrl);
    setTag($('[data-tag="art"]'), f.art && f.tagArt ? TAGS.found() : f.art ? TAGS.mine() : TAGS.none());
    $("[data-art-hint]").textContent = f.art ? gettext("Shows on the Music player widget and Now playing.") : gettext("This file has no embedded artwork. Add a square image, or leave it blank.");
    $("[data-art-btn]").textContent = f.art ? gettext("Replace") : gettext("Upload image");
    $("[data-f-art-rm]").hidden = !f.art;
    const lossless = f.fmt === "WAV" || f.fmt === "FLAC";
    $("[data-facts]").replaceChildren(...[[gettext("Format"), f.fmt], [gettext("Quality"), lossless ? gettext("Lossless") : f.kbps ? `${f.kbps} kbps` : "—"],
        [gettext("Length"), clock(f.dur)], [gettext("Size"), mb(f.size)]].map(([k, v]) => {
        const span = document.createElement("span");
        const a = document.createElement("span"), b = document.createElement("span");
        a.className = "up-fact-k";
        b.className = "up-fact-v num";
        a.textContent = k;
        b.textContent = v;
        span.append(a, b);
        return span;
    }));
    const miss = tagMissing(f);
    $("[data-note]").textContent = miss === 6 ? gettext("This file has no tags (common for WAV). Fill in what you know; a song without a title uses its file name.")
        : miss ? gettext("Fields marked “Not in file” weren’t in the tags. Leave them blank if you don’t know them.")
            : gettext("Everything was found in the file’s tags. Change anything that looks off.");
}

function renderFoot() {
    const ready = U.files.filter((f) => f.status === "ready");
    const reading = U.files.filter((f) => f.status === "reading").length;
    const room = Number(dlg().dataset.room);
    const tooBig = ready.reduce((n, f) => n + f.size, 0) > room;
    const button = $("[data-up-add]");
    button.disabled = U.busy || !ready.length || reading > 0 || !$("[data-up-rights]").checked || tooBig;
    const sent = U.files.filter((f) => ["uploading", "done"].includes(f.status)).length;
    button.textContent = U.busy ? interpolate(gettext("Uploading %s of %s…"), [sent, U.total])
        : reading ? interpolate(ngettext("Reading %s file…", "Reading %s files…", reading), [reading])
            : ready.length ? interpolate(ngettext("Add %s song", "Add %s songs", ready.length), [ready.length]) : gettext("Add songs");
    const zone = $("[data-up-zone]");
    zone.classList.toggle("compact", U.files.length > 0);
    zone.classList.toggle("over", U.over);
    $("[data-zone-text]").textContent = U.over ? gettext("Drop to add") : U.files.length ? gettext("Add more files") : gettext("Drop audio files here, or click to browse");
    $("[data-up-input]").disabled = U.busy;
    const error = $("[data-up-error]");
    if (tooBig && !U.busy) {
        error.textContent = gettext("There isn’t room in the library for all of these. Remove some files, or delete songs you don’t play.");
        error.hidden = false;
    } else if (error.dataset.kind === "room") {
        error.hidden = true;
    }
    error.dataset.kind = tooBig ? "room" : error.dataset.kind || "";
}

function render(fill = false) {
    if (!U) return;
    renderList();
    renderPane(fill || dlg().dataset.shown !== String(U.cur));
    dlg().dataset.shown = String(U.cur);
    renderFoot();
}

function upload(f) {
    return new Promise((resolve, reject) => {
        const data = new FormData();
        data.append("file", f.file);
        for (const k of KEYS) data.append(k, f[k].trim());
        data.append("duration", String(f.dur || 0));
        data.append("bitrate", String(f.kbps || 0));
        data.append("pl", $("[data-up-pl]").value);
        data.append("rights", "1");
        if (f.art) data.append("artwork", f.art, `cover.${(f.art.type.split("/")[1] || "jpg").replace("jpeg", "jpg")}`);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", dlg().dataset.uploadUrl);
        xhr.setRequestHeader("X-CSRFToken", document.querySelector('input[name="csrfmiddlewaretoken"]').value);
        xhr.upload.onprogress = (e) => {
            if (!e.lengthComputable) return;
            f.progress = Math.round((e.loaded / e.total) * 100);
            renderList();
        };
        xhr.onload = () => {
            let body = {};
            try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
            if (xhr.status === 200) resolve(body);
            else reject(new Error(body.message || gettext("The upload didn’t go through. Try again.")));
        };
        xhr.onerror = () => reject(new Error(gettext("The upload didn’t go through. Check the connection and try again.")));
        xhr.send(data);
    });
}

async function uploadAll() {
    const ready = U.files.filter((f) => f.status === "ready");
    if (!ready.length) return;
    U.busy = true;
    U.total = ready.length;
    $("[data-up-error]").hidden = true;
    render();
    let added = 0;
    for (const f of ready) {
        f.status = "uploading";
        f.progress = 0;
        render();
        try {
            await upload(f);
            f.status = "done";
            added++;
        } catch (err) {
            f.status = "ready";
            const error = $("[data-up-error]");
            error.textContent = err.message;
            error.dataset.kind = "upload";
            error.hidden = false;
            break;
        }
    }
    U.busy = false;
    const pl = $("[data-up-pl]").value;
    U.files = U.files.filter((f) => f.status !== "done");
    if (added) {
        const head = document.getElementById("lib-head");
        head.querySelector("[data-up-n]").value = String(added);
        head.querySelector("[data-up-pl]").value = pl;
        head.requestSubmit(head.querySelector("[data-up-done]"));
    }
    if (!U.files.some((f) => f.status === "ready")) closeUpload();
    else render(true);
}

// wiring (the dialog is outside #page, so it's never replaced)
const dialog = dlg();
if (dialog) {
    dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        closeUpload();
    });
    dialog.addEventListener("click", (event) => {
        const t = event.target;
        if (t.closest("[data-up-close]")) closeUpload();
        else if (t.closest("[data-up-add]")) uploadAll();
        else if (t.closest("[data-f-art-rm]")) {
            const f = current();
            if (f) { setArt(f, null, false); render(); }
        } else if (t.closest("[data-act]")) {
            const k = t.closest("[data-act]").dataset.act, f = current();
            if (!f) return;
            if (k === "title") f.title = baseName(f);
            else U.files.filter((x) => x.status === "ready").forEach((x) => { x[k] = f[k]; });
            render(true);
        }
    });
    dialog.addEventListener("input", (event) => {
        const k = event.target.dataset.f, f = current();
        if (!k || !f) return;
        f[k] = event.target.value;
        renderList();
        renderPane(false);
    });
    dialog.addEventListener("change", (event) => {
        const t = event.target;
        if (t.matches("[data-up-input]")) {
            addFiles([...t.files]);
            t.value = "";
        } else if (t.matches("[data-f-art-input]")) {
            const f = current(), file = t.files?.[0];
            if (f && file) setArt(f, file, false);
            t.value = "";
            render();
        } else if (t.matches("[data-up-rights]")) {
            renderFoot();
        }
    });
    dialog.addEventListener("dragover", (event) => {
        if (!U || U.busy || !event.dataTransfer?.types.includes("Files")) return;
        event.preventDefault();
        if (!U.over) { U.over = true; renderFoot(); }
    });
    dialog.addEventListener("dragleave", (event) => {
        if (U && !dialog.contains(event.relatedTarget)) { U.over = false; renderFoot(); }
    });
    dialog.addEventListener("drop", (event) => {
        if (!U || U.busy) return;
        event.preventDefault();
        U.over = false;
        addFiles([...event.dataTransfer.files]);
    });
}
