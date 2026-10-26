// Today's fun numbers for this device, shown on the app switcher. Kept in sessionStorage only: they belong to
// this browser tab, reset each day, and nothing on the server depends on them.
//   since    when a staff screen was first opened today (ms)
//   screens  staff screens opened
//   orders / sales (cents)  sales rung up on the register
//   tickets  orders moved along on the kitchen display (confirmed, ready, picked up, rejected)

const KEY = "pavilion-today";
const today = () => new Date().toLocaleDateString("en-CA");  // YYYY-MM-DD, local time

export function readStats() {
    let stats = null;
    try { stats = JSON.parse(sessionStorage.getItem(KEY)); } catch { /* unavailable or broken: start over */ }
    if (!stats || stats.date !== today()) stats = { date: today(), since: Date.now(), screens: 0, orders: 0, sales: 0, tickets: 0 };
    return stats;
}

export function bumpStats(changes) {
    const stats = readStats();
    for (const [field, by] of Object.entries(changes)) stats[field] = (stats[field] || 0) + by;
    try { sessionStorage.setItem(KEY, JSON.stringify(stats)); } catch { /* private mode etc.: no stats */ }
    return stats;
}
