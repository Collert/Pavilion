// Every admin page (admin_portal/layout.html): keeps the rail's clock ticking.
const time = document.getElementById("admin-time");
const day = document.getElementById("admin-day");
if (time && day) {
    const lang = document.documentElement.lang || undefined;
    const tick = () => {
        const now = new Date();
        time.textContent = now.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
        day.textContent = now.toLocaleDateString(lang, { weekday: "short", month: "short", day: "numeric" });
    };
    tick();
    setInterval(tick, 10000);
}
