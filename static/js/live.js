// Live updates over server-sent events (see pavilion/realtime.py). No polling.
//
// onLiveUpdate("orders", refresh) calls `refresh` whenever the server says something on that topic changed, and
// right after every (re)connect, so anything missed while disconnected is caught up. Several events in quick
// succession trigger a single refresh.
//
// The server sends a "ping" event every 15 s. If nothing arrives for `staleMs` (a connection that died without
// the browser noticing, e.g. behind a proxy), the stream is reopened, which refreshes once on "ready".
export function onLiveUpdate(topic, refresh, { debounceMs = 250, staleMs = 45000 } = {}) {
    let timer = null;
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
        source.addEventListener("ready", () => { heard(); trigger(); });
        source.addEventListener("ping", heard);
    };
    connect();
    const watchdog = setInterval(() => {
        if (Date.now() - lastHeard > staleMs) connect();
    }, staleMs / 3);
    return () => {
        clearTimeout(timer);
        clearInterval(watchdog);
        source.close();
    };
}
