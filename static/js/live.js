// Live updates over server-sent events (see pavilion/realtime.py). No polling.
//
// onLiveUpdate("orders", refresh) calls `refresh` whenever the server says something on that topic changed, and
// right after every (re)connect, so anything missed while disconnected is caught up. Several events in quick
// succession trigger a single refresh.
//
// The server sends a "ping" event every 15 s. If nothing arrives for `staleMs` (a connection that died without
// the browser noticing, e.g. behind a proxy), the stream is reopened, which refreshes once on "ready".
//
// refreshOnConnect: false skips the refresh when the stream first connects, for pages that arrive with their data
// already rendered. Reconnects still refresh (that's how missed events are caught up).
export function onLiveUpdate(topic, refresh, { debounceMs = 250, staleMs = 45000, refreshOnConnect = true } = {}) {
    let timer = null;
    let connectedBefore = false;
    let source = null;
    let lastHeard = Date.now();
    const trigger = () => {
        clearTimeout(timer);
        timer = setTimeout(refresh, debounceMs);
    };
    const heard = () => { lastHeard = Date.now(); };
    const connect = () => {
        source?.close();
        heard();
        source = new EventSource(`/events/${topic}`);
        source.addEventListener(topic, () => { heard(); trigger(); });
        source.addEventListener("ready", () => {
            heard();
            if (connectedBefore || refreshOnConnect) trigger();
            connectedBefore = true;
        });
        source.addEventListener("ping", heard);
    };
    connect();
    const watchdog = setInterval(() => {
        if (source && Date.now() - lastHeard > staleMs) connect();
    }, staleMs / 3);
    // Leaving the page: let go of the stream. Browsers may keep the old page (back/forward cache) with its
    // stream still open, and a handful of those use up every connection to the server, so new pages hang.
    // Coming back to a cached page reconnects, which refreshes it.
    const hide = () => { source?.close(); source = null; };
    const show = (event) => { if (event.persisted) connect(); };
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    return () => {
        clearTimeout(timer);
        clearInterval(watchdog);
        window.removeEventListener("pagehide", hide);
        window.removeEventListener("pageshow", show);
        hide();
    };
}
