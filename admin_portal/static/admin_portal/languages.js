// Languages and Store info: while Pavilion restarts, ask every two seconds whether the Pavilion answering runs the saved
// settings and reload the page once it does (with several workers, one still restarting shows the card again). Errors
// while it's down are expected.
const watch = () => {
    const card = document.querySelector("[data-restarting]");
    if (!card || card.dataset.watching) return;
    card.dataset.watching = "1";
    const check = async () => {
        try {
            const response = await fetch(card.dataset.status, { credentials: "same-origin", cache: "no-store" });
            if (response.ok && (await response.json()).done) {
                location.replace(location.pathname);
                return;
            }
        } catch { /* still restarting */ }
        setTimeout(check, 2000);
    };
    setTimeout(check, 2000);
};

watch();
new MutationObserver(watch).observe(document.body, { childList: true, subtree: true });
