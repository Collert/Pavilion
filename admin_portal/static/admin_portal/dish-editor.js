// The dish editor (admin_portal/templates/admin_portal/dish.html) is one form that live-page.js posts in the
// background. This adds what HTML can't do: post the draft back (op=preview) when a field changes, so the sums and
// warnings keep up without saving, and show a chosen photo before it's saved. The photo's region is data-keep, so
// background answers leave the chosen file alone until Save or Duplicate.

document.addEventListener("change", (event) => {
    const form = event.target.closest("#dish-form");
    if (!form) return;
    if (event.target.name === "photo") {
        const file = event.target.files?.[0];
        const region = document.getElementById("dish-photo");
        if (!file || !region) return;
        const img = document.getElementById("photo-preview");
        img.src = URL.createObjectURL(file);
        img.hidden = false;
        region.querySelector(".photo-letter")?.remove();
        region.querySelector(".photo-label").textContent = gettext("Change photo");
        region.setAttribute("data-keep", "");
        form.querySelector('input[name="photo_pending"]').value = "1";
    }
    form.requestSubmit(document.getElementById("dish-preview"));
});

// Saving sends the photo, so let the answer replace its region again
document.addEventListener("submit", (event) => {
    if (event.target.id === "dish-form" && ["save", "duplicate", "archive", "restore"].includes(event.submitter?.value)) {
        document.getElementById("dish-photo")?.removeAttribute("data-keep");
    }
}, true);
