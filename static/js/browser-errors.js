// Sends this page's JavaScript errors to Bugsink, like the server's (pos_server/_browser_errors.html, only outside
// DEBUG). Screens run as kiosks where nobody sees the console, so uncaught errors, failed promises and console.error
// are reported, with the console, clicks and fetches leading up to them as breadcrumbs.
//
// A fetch that never reached the server (Wi-Fi dropped, the page was left mid-request, the server restarting) isn't
// a bug in the page: screens catch up when the live stream reconnects. Each browser words it its own way.
const NETWORK_FAILURES = [
    "Failed to fetch",  // Chrome, Edge
    "Load failed",  // Safari
    "NetworkError when attempting to fetch resource",  // Firefox
];
(() => {
    const node = document.getElementById("pv-browser-errors");
    if (!node || !window.Sentry) return;
    const config = JSON.parse(node.textContent);
    Sentry.init({
        dsn: config.dsn,
        environment: config.environment,
        sendDefaultPii: false,
        integrations: [Sentry.captureConsoleIntegration({ levels: ["error"] })],
        denyUrls: [/^(chrome|moz|safari(-web)?)-extension:\/\//],
        ignoreErrors: ["ResizeObserver loop", "AbortError", ...NETWORK_FAILURES],
    });
    if (config.user) Sentry.setUser(config.user);
})();
