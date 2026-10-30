// Get the app (themes/<theme>/install.html): the browser's own install question, behind our button. Chromium browsers
// say they can install it (beforeinstallprompt); iPhones never do, so they get the steps; anything else that hasn't
// offered after a few seconds gets the fallback. Already inside the app, the page has nothing to offer: off to the store.
const page = document.querySelector("[data-install]");
const show = (state) => { page.dataset.state = state; };
const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const inApp = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
let offer = null;

if (inApp) location.replace(page.dataset.home);
else if (apple) show("ios");

window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();  // our button asks instead of the browser's mini bar
    offer = event;
    show("ready");
});

window.addEventListener("appinstalled", () => {
    offer = null;
    show("installed");
});

page.querySelector("[data-install-button]")?.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    const { outcome } = await offer.userChoice;
    offer = null;
    show(outcome === "accepted" ? "installed" : "waiting");
    if (outcome !== "accepted") setTimeout(() => { if (page.dataset.state === "waiting") show("fallback"); }, 1500);
});

setTimeout(() => {
    if (!offer && !apple && !inApp && page.dataset.state === "waiting") show("fallback");
}, 3000);
