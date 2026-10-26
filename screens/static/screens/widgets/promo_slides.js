// Promo slides: turn to the next slide every data-ms milliseconds. live-page.js may swap the widget in for a fresh
// copy (offers changed), so each tick looks the slides up again.
setInterval(() => {
    document.querySelectorAll("[data-promo]").forEach((promo) => {
        const slides = [...promo.querySelectorAll(".promo-slide")];
        if (slides.length < 2) return;
        const now = Date.now();
        const due = Number(promo.dataset.next || 0);
        if (!due) { promo.dataset.next = now + Number(promo.dataset.ms || 8000); return; }
        if (now < due) return;
        const current = slides.findIndex((s) => s.classList.contains("on"));
        const next = (current + 1) % slides.length;
        slides.forEach((s, i) => s.classList.toggle("on", i === next));
        promo.querySelectorAll(".promo-dots span").forEach((d, i) => d.classList.toggle("on", i === next));
        promo.dataset.next = now + Number(promo.dataset.ms || 8000);
    });
}, 500);
