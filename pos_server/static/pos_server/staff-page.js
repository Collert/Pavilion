// Loaded by every staff screen (staff_layout.html): counts the screen for today's device stats and runs the rail
// footer's clock (pos_server/rail_foot.html), if the screen has one.
import { bumpStats } from "./device-stats.js";
import { onLiveUpdate } from "/static/js/live.js";

bumpStats({ screens: 1 });

const time = document.getElementById("rail-time");
const day = document.getElementById("rail-day");
if (time && day) {
    const lang = document.documentElement.lang || undefined;
    const tick = () => {
        const now = new Date();
        time.textContent = now.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "");
        day.textContent = now.toLocaleDateString(lang, { weekday: "short" });
    };
    tick();
    setInterval(tick, 10000);
}

// The rail's Orders button watches the stations this device's kitchen screen last showed (order-marking.js keeps
// them, with the newest order it showed): it opens on those stations, and a dot appears when there's a newer order
// there or one waiting for a station to accept it (views.orders_attention), on the same live feed as the KDS.
const ordersLink = document.getElementById("rail-orders");
if (ordersLink) {
    let watch = null;
    try { watch = JSON.parse(localStorage.getItem("pavilion.ordersWatch") || "null"); } catch { /* nothing kept */ }
    if (watch?.stations?.length) {
        const stations = watch.stations.join(",");
        ordersLink.href = `${ordersLink.dataset.kds}?filter=${encodeURIComponent(stations)}`;
        const dot = document.getElementById("rail-orders-dot");
        const check = async () => {
            const response = await fetch(`${ordersLink.dataset.attention}?stations=${encodeURIComponent(stations)}`,
                { headers: { Accept: "application/json" } }).catch(() => null);
            if (!response?.ok) return;
            const { newest, waiting } = await response.json();
            let seen = Number(watch.seen) || 0;
            try { seen = Number(JSON.parse(localStorage.getItem("pavilion.ordersWatch") || "{}").seen) || seen; } catch { /* keep */ }
            const on = waiting > 0 || newest > seen;
            dot.hidden = !on;
            ordersLink.setAttribute("aria-label", on ? gettext("Orders: new orders") : gettext("Orders"));
        };
        onLiveUpdate("orders", check);
    }
}
