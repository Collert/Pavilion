// The store app offline (online_store/_app_script.html): pages still open from the service worker's copies
// (push/templates/push/sw.js), a banner says so, and nothing that orders or changes anything is sent. Back online,
// live-page.js's stream reconnects and brings the page up to date.
const banner = document.getElementById("store-offline");

const show = () => { banner.hidden = navigator.onLine; };
window.addEventListener("online", show);
window.addEventListener("offline", show);
show();

// Before live-page.js sees it: a form that posts would fail, so the banner nudges instead
window.addEventListener("submit", (event) => {
    if (navigator.onLine) return;
    const method = (event.submitter?.getAttribute("formmethod") || event.target.getAttribute("method") || "get").toLowerCase();
    if (method !== "post") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    banner.classList.remove("nudge");
    void banner.offsetWidth;  // (restarts the animation)
    banner.classList.add("nudge");
}, true);
