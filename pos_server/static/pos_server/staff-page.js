// Loaded by every staff screen (staff_layout.html): counts the screen for today's device stats and runs the rail
// footer's clock (pos_server/rail_foot.html), if the screen has one.
import { bumpStats } from "./device-stats.js";

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
