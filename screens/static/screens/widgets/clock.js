// Ticks every [data-clock] on the screen. The server renders the first value; this keeps it current between
// refetches, in the restaurant's time zone (data-tz) rather than the display's.
const lang = document.documentElement.lang || undefined;

function parts(node) {
    const timeZone = node.dataset.tz || undefined;
    const hour12 = node.dataset.hour12 === "1";
    const time = new Intl.DateTimeFormat(lang, { hour: "numeric", minute: "2-digit", hour12, timeZone })
        .formatToParts(new Date());
    const clock = time.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    const ampm = time.find((p) => p.type === "dayPeriod")?.value.toLowerCase() ?? "";
    const date = new Intl.DateTimeFormat(lang, { weekday: "long", month: "long", day: "numeric", timeZone }).format(new Date());
    return { clock, ampm, date };
}

function tick() {
    document.querySelectorAll("[data-clock]").forEach((node) => {
        const { clock, ampm, date } = parts(node);
        const set = (selector, text) => {
            const el = node.querySelector(selector);
            if (el && el.textContent !== text) el.textContent = text;
        };
        set("[data-clock-time]", clock);
        set("[data-clock-ampm]", ampm);
        set("[data-clock-date]", date);
        node.setAttribute("aria-label", `${gettext("Time")} ${clock} ${ampm}`.trim());
    });
}

tick();
setInterval(tick, 1000);
