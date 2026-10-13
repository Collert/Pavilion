// Live updates over server-sent events (see pavilion/realtime.py).
//
// onLiveUpdate("orders", refresh) calls `refresh` whenever the server says something on that topic changed,
// right after (re)connecting (to catch up on anything missed), and every `fallbackMs` as a safety net in case
// the stream is down. Several events in quick succession trigger a single refresh.
export function onLiveUpdate(topic, refresh, { fallbackMs = 60000, debounceMs = 250 } = {}) {
    let timer = null;
    const trigger = () => {
        clearTimeout(timer);
        timer = setTimeout(refresh, debounceMs);
    };
    const source = new EventSource(`/events/${topic}`);
    source.addEventListener(topic, trigger);
    source.addEventListener("ready", trigger);
    const poll = setInterval(refresh, fallbackMs);
    return () => {
        source.close();
        clearInterval(poll);
    };
}
