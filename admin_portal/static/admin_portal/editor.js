// Admin editors (dish, combo...) are one form[data-editor] that live-page.js posts in the background. This adds
// what HTML can't do: post the draft back with the form's [data-preview] button (op=preview) when a field changes,
// so sums and warnings keep up without saving (search fields marked data-type-preview as you type), and show a chosen photo (#photo-preview) before it's saved. The
// photo's region is marked data-keep, so background answers leave the chosen file alone until the form is saved.

const SAVES = ["save", "duplicate", "archive", "restore"];

document.addEventListener("change", (event) => {
    const form = event.target.closest("form[data-editor]");
    if (!form || event.target.matches("[data-no-preview]")) return;  // e.g. picking days in a menu sent by its own button
    // Files kept as soon as they're picked (a restock's receipt): send it now, then clear the field
    if (event.target.matches("[data-upload]")) {
        event.target.closest("label")?.setAttribute("aria-busy", "true");  // a spinner until the answer swaps the region
        form.requestSubmit(form.querySelector("[data-preview]"));
        return;
    }
    if (event.target.type === "file") {
        const file = event.target.files?.[0];
        const region = event.target.closest("[data-region]");
        const img = document.getElementById("photo-preview");
        if (!file || !region || !img) return;
        img.src = URL.createObjectURL(file);
        img.hidden = false;
        region.querySelector(".photo-letter")?.remove();
        const label = region.querySelector(".photo-label");
        if (label) label.textContent = gettext("Change photo");
        region.setAttribute("data-keep", "");
        const pending = form.querySelector('input[name="photo_pending"]');
        if (pending) pending.value = "1";
    }
    form.requestSubmit(form.querySelector("[data-preview]"));
});

// Save turns on as you type: the server only hears of a text field when it loses focus, and that happens on the
// press of a still-disabled Save, which swallowed the click (the preview is sent first; Save checks the draft again)
document.addEventListener("input", (event) => {
    const form = event.target.closest("form[data-editor]");
    if (!form || event.target.matches('[type="search"], [data-no-preview]')) return;
    form.querySelectorAll('button[name="op"][value="save"]:disabled').forEach((button) => { button.disabled = false; });
});

// Search-like fields ([data-type-preview]) show results as you type
let typing;
document.addEventListener("input", (event) => {
    const form = event.target.closest("form[data-editor]");
    if (!form || !event.target.matches("[data-type-preview]")) return;
    clearTimeout(typing);
    typing = setTimeout(() => form.requestSubmit(form.querySelector("[data-preview]")), 300);
});

// Lists with a selected row (recipe steps, [data-step]): going into another row selects it, so previews follow
document.addEventListener("focusin", (event) => {
    const row = event.target.closest("[data-step]");
    const form = row?.closest("form[data-editor]");
    const sel = form?.querySelector('input[name="sel"]');
    if (!sel || sel.value === row.dataset.step) return;
    sel.value = row.dataset.step;
    form.requestSubmit(form.querySelector("[data-preview]"));
});

// Saving sends the photo, so let the answer replace its region again. Previews leave a chosen photo out (photo_pending
// tells the server there is one): a big picture was sent again with every change, and a Save pressed meanwhile was
// dropped. (live-page.js reads the form right after this, so the field is disabled only for that.)
document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.matches("form[data-editor]")) return;
    if (SAVES.includes(event.submitter?.value)) {
        form.querySelectorAll("[data-keep]").forEach((node) => node.removeAttribute("data-keep"));
    } else if (event.submitter?.matches("[data-preview]")) {
        const files = [...form.querySelectorAll('input[type="file"]:not([data-upload], :disabled)')];
        files.forEach((input) => { input.disabled = true; });
        setTimeout(() => files.forEach((input) => { input.disabled = false; }));
    }
}, true);
