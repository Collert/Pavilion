// Sends this page's JavaScript errors to Bugsink, like the server's (pos_server/_browser_errors.html, only outside
// DEBUG). Screens run as kiosks where nobody sees the console, so uncaught errors, failed promises and console.error
// are reported, with the console, clicks and fetches leading up to them as breadcrumbs.
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
        ignoreErrors: ["ResizeObserver loop", "AbortError"],
    });
    if (config.user) Sentry.setUser(config.user);
})();
