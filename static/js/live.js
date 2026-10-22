// Live updates over server-sent events (see pavilion/realtime.py). No polling.
//
// onLiveUpdate("orders", refresh) calls `refresh` whenever the server says something on that topic changed, and
// right after every (re)connect, so anything missed while disconnected is caught up. Several events in quick
// succession trigger a single refresh.
//
// Every topic a page listens to shares ONE stream (/events/orders,menus,...): browsers allow only six connections to
// a server over HTTP/1.1, and a stream per topic used them all up, so images, songs and refetches hung. Adding a
// topic reopens the stream with the new set.
//
// The server sends a "ping" event every 15 s. If nothing arrives for `staleMs` (a connection that died without
// the browser noticing, e.g. behind a proxy), the stream is reopened, which refreshes once on "ready".
//
// refreshOnConnect: false skips the refresh when the stream first connects, for pages that arrive with their data
// already rendered. Reconnects still refresh (that's how missed events are caught up).
const STALE_MS = 45000;
const listeners = new Set();  // {topic, trigger, refreshOnConnect, connectedBefore}
let source = null;
let openTopics = "";
let lastHeard = Date.now();
let reopenQueued = false;

const heard = () => { lastHeard = Date.now(); };

function wanted() {
    return [...new Set([...listeners].map((l) => l.topic))].sort().join(",");
}

function connect() {
    source?.close();
    source = null;
    openTopics = wanted();
    if (!openTopics) return;
    heard();
    source = new EventSource(`/events/${openTopics}`);
    for (const topic of openTopics.split(",")) {
        source.addEventListener(topic, () => {
            heard();
            for (const l of listeners) if (l.topic === topic) l.trigger();
        });
    }
    source.addEventListener("ready", () => {
        heard();
        for (const l of listeners) {
            if (l.connectedBefore || l.refreshOnConnect) l.trigger();
            l.connectedBefore = true;
        }
    });
    source.addEventListener("ping", heard);
}

// Topics added in the same moment (a page's scripts starting) open one stream between them
function reopenSoon() {
    if (reopenQueued) return;
    reopenQueued = true;
    queueMicrotask(() => {
        reopenQueued = false;
        if (wanted() !== openTopics || !source) connect();
    });
}

setInterval(() => {
    if (source && Date.now() - lastHeard > STALE_MS) connect();
}, STALE_MS / 3);

// Leaving the page: let go of the stream. Browsers may keep the old page (back/forward cache) with its stream
// still open, and a handful of those use up every connection to the server, so new pages hang. Coming back to a
// cached page reconnects, which refreshes it.
window.addEventListener("pagehide", () => { source?.close(); source = null; });
window.addEventListener("pageshow", (event) => { if (event.persisted) connect(); });

export function onLiveUpdate(topic, refresh, { debounceMs = 250, refreshOnConnect = true } = {}) {
    let timer = null;
    const listener = {
        topic, refreshOnConnect, connectedBefore: false,
        trigger: () => {
            clearTimeout(timer);
            timer = setTimeout(refresh, debounceMs);
        },
    };
    listeners.add(listener);
    reopenSoon();
    return () => {
        clearTimeout(timer);
        listeners.delete(listener);
        reopenSoon();
    };
}
