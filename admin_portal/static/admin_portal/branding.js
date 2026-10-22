// Branding (admin_portal/templates/admin_portal/branding.html): what HTML can't do on its own.
// - A colour picker writes its hex field and posts the draft (the hex field is what's sent).
// - The preview iframe is drawn at the store's real width (1440 or 390) and scaled down to fit the panel.

document.addEventListener("input", (event) => {
    const picker = event.target.closest("input[type=color][data-hex]");
    if (!picker) return;
    const hex = picker.form?.querySelector(`input[name="${picker.dataset.hex}"]`);
    if (hex) hex.value = picker.value.toUpperCase();
    picker.parentElement.style.background = picker.value;
});
document.addEventListener("change", (event) => {
    const picker = event.target.closest("input[type=color][data-hex]");
    const hex = picker && picker.form?.querySelector(`input[name="${picker.dataset.hex}"]`);
    if (hex) hex.dispatchEvent(new Event("change", { bubbles: true }));
}, true);

function fit() {
    document.querySelectorAll("[data-scale-stage]").forEach((stage) => {
        const frame = stage.querySelector("iframe[data-scale]");
        if (!frame) return;
        const width = Number(frame.dataset.scale);
        const room = stage.clientWidth - 28;
        const scale = Math.min(1, room / width);
        frame.style.width = `${width}px`;
        frame.style.height = `${(stage.clientHeight - 28) / scale}px`;
        frame.style.transform = `scale(${scale})`;
    });
}
new ResizeObserver(fit).observe(document.documentElement);
new MutationObserver(fit).observe(document.getElementById("page"), { childList: true, subtree: true });
fit();
