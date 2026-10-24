// Languages (admin_portal/languages.html): after "Restart now", ask which Pavilion is answering every two seconds and
// reload the page once it's a new one (its boot id changed). Errors while it's down are expected.
const watch = () => {
    const card = document.querySelector("[data-restarting]");
    if (!card || card.dataset.watching) return;
    card.dataset.watching = "1";
    const check = async () => {
        try {
            const response = await fetch(card.dataset.status, { credentials: "same-origin", cache: "no-store" });
            if (response.ok && (await response.json()).boot !== card.dataset.boot) {
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
