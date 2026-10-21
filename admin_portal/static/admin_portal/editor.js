// Admin editors (dish, combo...) are one form[data-editor] that live-page.js posts in the background. This adds
// what HTML can't do: post the draft back with the form's [data-preview] button (op=preview) when a field changes,
// so sums and warnings keep up without saving, and show a chosen photo (#photo-preview) before it's saved. The
// photo's region is marked data-keep, so background answers leave the chosen file alone until the form is saved.

const SAVES = ["save", "duplicate", "archive", "restore"];

document.addEventListener("change", (event) => {
    const form = event.target.closest("form[data-editor]");
    if (!form) return;
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

// Saving sends the photo, so let the answer replace its region again
document.addEventListener("submit", (event) => {
    if (event.target.matches("form[data-editor]") && SAVES.includes(event.submitter?.value)) {
        event.target.querySelectorAll("[data-keep]").forEach((node) => node.removeAttribute("data-keep"));
    }
}, true);
