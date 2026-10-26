// The app switcher (see docs/mockups/App switcher-html and app_switcher/views.py). Everything here is local:
// the clock, the greeting, whether the device is online, and today's stats from sessionStorage.
import { readStats } from "../pos_server/device-stats.js";
import { fmt } from "../pos_server/staff-ui.js";

const t = (text) => gettext(text);
const lang = document.documentElement.lang || undefined;
const name = JSON.parse(document.getElementById("apps-name").textContent);
const $ = (id) => document.getElementById(id);

function duration(ms) {
    const minutes = Math.max(0, Math.floor(ms / 60000));
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return h ? interpolate(t("%sh %sm"), [h, m]) : interpolate(t("%sm"), [m]);
}

function tick() {
    const now = new Date();
    $("today").textContent = now.toLocaleDateString(lang, { weekday: "long", month: "long", day: "numeric" });
    const parts = new Intl.DateTimeFormat(lang, { hour: "numeric", minute: "2-digit" }).formatToParts(now);
    $("clock-time").textContent = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    $("clock-ampm").textContent = parts.find((p) => p.type === "dayPeriod")?.value || "";
    const hour = now.getHours();
    const hello = hour < 12 ? t("Good morning, %s.") : hour < 17 ? t("Good afternoon, %s.") : t("Good evening, %s.");
    $("greeting").textContent = interpolate(hello, [name]) + " " + t("Pick up where you left off.");

    const stats = readStats();
    const tiles = [
        [fmt(stats.sales), t("Sales rung up here")],
        [String(stats.orders), t("Orders rung up")],
        [String(stats.tickets), t("Tickets moved along")],
        [duration(Date.now() - stats.since), t("Since the first screen today")],
    ];
    $("stats").replaceChildren(...tiles.map(([value, label]) => {
        const tile = document.createElement("div");
        tile.className = "stat";
        const v = document.createElement("div");
        v.className = "stat-value";
        v.textContent = value;
        const l = document.createElement("div");
        l.className = "stat-label";
        l.textContent = label;
        tile.append(v, l);
        return tile;
    }));
}

function network() {
    const online = navigator.onLine;
    $("net").classList.toggle("offline", !online);
    $("net-text").textContent = online ? t("Online") : t("Offline");
}

tick();
setInterval(tick, 10000);
network();
window.addEventListener("online", network);
window.addEventListener("offline", network);
