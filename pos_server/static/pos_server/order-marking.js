// Kitchen display (KDS): see docs/mockups/Order marking-html and pos_server.views.order_marking.
//
// The screen works for the stations in ?filter= (all of them without it) and shows each order's dishes for
// those stations. Station statuses (OrderStation): 0 not confirmed, 1 confirmed/being made, 2 ready.
// An order goes, for this screen:
//   new        one of my stations hasn't confirmed               Enter confirms, Esc rejects
//   confirmed  mine confirmed, another station hasn't            (waits)
//   progress   every station confirmed, mine still making it     Enter marks my stations ready
//   ready      mine are ready, another station isn't             (waits)
//   pickup     every station is ready                            Enter marks it picked up (couriers take deliveries)
// Picked up orders leave every screen. Everything is keyboard driven; the <kbd> hints show the keys.
//
// Auto (?auto-ready=<min>&auto-pickup=<min>, set in the Auto sheet): marks this screen's part ready <min> after the
// order went in progress (Order.in_progress_since), and marks it picked up <min> after this screen marked it ready
// (OrderStation.ready_at), trying again every minute while other stations are still on it.

import { onLiveUpdate } from "/static/js/live.js";
import { $, el, post, wireSheets } from "./staff-ui.js";
import { bumpStats } from "./device-stats.js";

const t = (text) => gettext(text);
const fmtText = (text, ...args) => interpolate(gettext(text), args);
const data = (id) => JSON.parse(document.getElementById(id).textContent);

const STATIONS = data("kds-stations");
const REASONS = data("kds-reasons");
const FEED_URL = data("kds-feed");
const stationBy = Object.fromEntries(STATIONS.map((s) => [s.code, s]));

// The device's clock may be off; timers and auto steps run on the server's time
let clockOffset = Date.parse(data("kds-now")) - Date.now();
const nowMs = () => Date.now() + clockOffset;

const params = new URLSearchParams(window.location.search);
const minutesParam = (name) => {
    const minutes = parseInt(params.get(name), 10);
    return minutes > 0 ? Math.min(minutes, 240) : null;
};

const state = {
    orders: new Map(),          // every active order from the feed, by id
    filters: data("kds-filters"),
    openId: null,
    autoOpen: true,             // open a card by itself (at start, and when the open one leaves)
    lastIndex: 0,               // where the open card was, to open its neighbour when it leaves
    busy: false,
    reject: { id: null, picked: new Set() },
    auto: { ready: minutesParam("auto-ready"), pickup: minutesParam("auto-pickup") },
    retryAt: new Map(),         // "<order id>:<action>" -> when auto may try that step again
};
data("kds-orders").forEach((order) => state.orders.set(order.order_id, order));

// ---------- icons (constant markup only; data always goes in with textContent) ----------

const P = {
    store: "M4 9l1.5-5h13L20 9 M4 9h16 M5 9v11h14V9 M10 20v-5h4v5",
    bag: "M5 8h14l-1 12H6z M9 8V6.5a3 3 0 0 1 6 0V8",
    scooter: "M4 17a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M15 17a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M9 17h6 M17.5 17 15 7h-3 M6.5 14.5 8.5 10h5",
    plate: "M7 3v7 M5 3v4.5a2 2 0 0 0 4 0V3 M7 10v11 M17 3c-2 1-2.5 4.5-2.5 8H17 M17 3v18",
    box: "M4 8l8-4 8 4-8 4z M4 8v8l8 4 8-4V8 M12 12v8",
    phone: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    check: "m5 12.5 4.5 4.5L19 7.5",
    checkCircle: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M8 12.4l2.8 2.8 5.2-5.6",
    hourglass: "M7 3h10 M7 21h10 M8 3v3.5a4 4 0 0 0 1.6 3.2L12 12l2.4-2.3A4 4 0 0 0 16 6.5V3 M8 21v-3.5a4 4 0 0 1 1.6-3.2L12 12l2.4 2.3a4 4 0 0 1 1.6 3.2V21",
    cross: "M6 6l12 12M18 6 6 18",
    clock: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 7.5V12l3 2",
    chevron: "m6 9 6 6 6-6",
    alert: "M12 3.5 2.5 20h19z M12 10v4.5 M12 17.4v.1",
    note: "M5 4h14v12l-4 4H5z M15 20v-4h4 M8.5 9h7 M8.5 12.5h4.5",
    handoff: "M5 8h14l-1 12H6z M9 8V6.5a3 3 0 0 1 6 0V8 M9.5 14l2 2 3.5-4",
};

const STATUS_ICON = {
    new: { bg: "#FFFFFF", ring: "#5B6A62", dash: "3.4 2.6" },
    confirmed: { bg: "#FFFFFF", ring: "#1F4A3A", mark: "m8 12.4 2.8 2.8 5.2-5.6", markColor: "#1F4A3A" },
    progress: { bg: "#FFFFFF", ring: "#14261F", fill: "M12 3a9 9 0 0 1 0 18z", fillColor: "#F4A93B" },
    ready: { bg: "#1F4A3A", ring: "#1F4A3A", mark: "m8 12.4 2.8 2.8 5.2-5.6", markColor: "#FFFFFF" },
    pickup: { bg: "#F4A93B", ring: "#F4A93B", mark: "m8 12.4 2.8 2.8 5.2-5.6", markColor: "#14261F" },
};

function svg(markup) {
    const template = document.createElement("template");
    template.innerHTML = markup.trim();
    return template.content.firstChild;
}
const icon = (d, size = 20, width = 2) => svg(`<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"></path></svg>`);
function statusIcon(key, size = 22) {
    const i = STATUS_ICON[key];
    return svg(`<svg class="status-icon" aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24">`
        + `<circle cx="12" cy="12" r="9" fill="${i.bg}"></circle>`
        + (i.fill ? `<path d="${i.fill}" fill="${i.fillColor}"></path>` : "")
        + `<circle cx="12" cy="12" r="9" fill="none" stroke="${i.ring}" stroke-width="2" stroke-dasharray="${i.dash || "none"}"></circle>`
        + (i.mark ? `<path d="${i.mark}" fill="none" stroke="${i.markColor}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"></path>` : "")
        + "</svg>");
}
const stationIcon = (code) => el("span", { className: "msym", text: stationBy[code]?.icon || "restaurant", attrs: { "aria-hidden": "true" } });
const kbd = (text, dark = false) => el("kbd", { className: "kbd" + (dark ? " on-dark" : ""), text });

// ---------- formatting ----------

const lang = document.documentElement.lang || undefined;
const list = new Intl.ListFormat(lang, { style: "long", type: "conjunction" });
const joinNames = (names) => list.format(names);
const clockOf = (date) => date.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
const two = (n) => String(n).padStart(2, "0");
function fmtElapsed(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    return h ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}
const ageOf = (order) => (nowMs() - Date.parse(order.start_time)) / 1000;
const stationNames = (codes) => codes.map((code) => stationBy[code]?.name ?? code);

// ---------- order state, relative to this screen ----------

const involved = (order) => STATIONS.map((s) => s.code).filter((code) => code in order.stations);
const mine = (order) => involved(order).filter((code) => state.filters.includes(code));
const others = (order) => involved(order).filter((code) => !state.filters.includes(code));
const statusOf = (order, code) => order.stations[code];

function statusKey(order) {
    const all = involved(order).map((code) => statusOf(order, code));
    const my = mine(order).map((code) => statusOf(order, code));
    if (my.includes(0)) return "new";
    if (all.includes(0)) return "confirmed";
    if (my.includes(1)) return "progress";
    if (all.includes(1)) return "ready";
    return "pickup";
}

// The icon for one station's status on an order
function stationKey(order, code) {
    const status = statusOf(order, code);
    if (status === 0) return "new";
    if (status === 1) return Object.values(order.stations).includes(0) ? "confirmed" : "progress";
    return Object.values(order.stations).every((s) => s >= 2) ? "pickup" : "ready";
}

const STATUS_TEXT = {
    new: () => t("Not confirmed"),
    confirmed: () => t("Confirmed · waiting"),
    progress: () => t("In progress"),
    ready: () => t("Ready · waiting"),
    pickup: (order) => order?.channel === "delivery" ? t("Waiting for courier") : t("Ready for pickup"),
};

const relevant = (order) => !order.picked_up && mine(order).some((code) => statusOf(order, code) <= 2)
    && visibleDishes(order).length > 0;
const visibleDishes = (order) => order.dishes.filter((dish) => state.filters.includes(dish.station));

function visibleOrders() {
    return [...state.orders.values()].filter(relevant)
        .sort((a, b) => new Date(a.start_time) - new Date(b.start_time) || a.order_id - b.order_id);
}

// What Enter does for the order (null when it's waiting on someone else)
function primaryAction(order) {
    const key = statusKey(order);
    if (key === "new") return "confirm";
    if (key === "progress") return "ready";
    if (key === "pickup" && order.channel !== "delivery") return "pickup";
    return null;
}

// ---------- auto ----------

// When auto should take the order's next step here: {action, at} (ms, server time), or null
function autoStep(order) {
    const key = statusKey(order);
    const retry = (action) => state.retryAt.get(`${order.order_id}:${action}`) ?? 0;
    if (state.auto.ready && key === "progress" && order.in_progress_at) {
        return { action: "ready", at: Math.max(Date.parse(order.in_progress_at) + state.auto.ready * 60000, retry("ready")) };
    }
    if (state.auto.pickup && (key === "ready" || key === "pickup") && order.channel !== "delivery") {
        const readyTimes = mine(order).map((code) => Date.parse(order.ready_at?.[code] ?? "")).filter(Number.isFinite);
        if (!readyTimes.length) return null;
        return { action: "pickup", at: Math.max(Math.max(...readyTimes) + state.auto.pickup * 60000, retry("pickup")) };
    }
    return null;
}

// Every few seconds: take the first step that's due. Pickup waits a minute more while another station is still on it.
async function runAuto() {
    if (state.busy || (!state.auto.ready && !state.auto.pickup)) return;
    let bumped = false;
    for (const order of visibleOrders()) {
        const step = autoStep(order);
        if (!step || step.at > nowMs()) continue;
        const retryKey = `${order.order_id}:${step.action}`;
        if (step.action === "pickup" && statusKey(order) !== "pickup") {
            state.retryAt.set(retryKey, nowMs() + 60000);
            bumped = true;
            continue;
        }
        state.retryAt.set(retryKey, nowMs() + 60000);  // if it fails, don't hammer the server
        await act(order.order_id, step.action, {}, { auto: true });
        return;
    }
    if (bumped) render();  // show the new countdown
}
setInterval(runAuto, 5000);

const autoLabel = (action) => action === "ready" ? t("Auto ready in %s") : t("Auto pickup in %s");
function autoDue(order) {
    const step = autoStep(order);
    if (!step) return null;
    return el("span", { className: "cap auto-due", attrs: { "data-due": step.at, "data-label": autoLabel(step.action) } },
        [icon(P.clock, 14, 2.4), el("span", { text: "" })]);
}

// ---------- cards ----------

function buildRows(order) {
    const dishes = visibleDishes(order);
    const multi = state.filters.length > 1;
    const groups = [];
    for (const dish of dishes) {
        const last = groups[groups.length - 1];
        if (last && last.line === dish.line && dish.combo) last.dishes.push(dish);
        else groups.push({ line: dish.line, combo: dish.combo, dishes: [dish] });
    }

    const rows = [];
    const row = ({ child = false, qty, name, isCombo, station, allergies = [], tags = [], note }) => {
        const title = el("div", { className: "row-title" }, [
            el("span", { className: "disp row-name", text: name }),
            isCombo ? el("span", { className: "cap pill combo", text: t("Combo") }) : null,
            multi && station ? el("span", { className: "cap pill station" }, [stationIcon(station), stationBy[station]?.name ?? station]) : null,
            ...allergies.map((a) => el("span", { className: "allergy-pill" }, [icon(P.alert, 15, 2.6), fmtText("Allergy: %s", a)])),
        ]);
        const chips = tags.map((tag) => el("span", { className: "mod " + tag.kind, text: tag.text }));
        if (note) chips.push(el("span", { className: "mod note", text: `“${note}”` }));
        rows.push(el("div", { className: "row" + (child ? " child" : "") }, [
            el("span", { className: "disp row-qty", text: qty }),
            el("div", { className: "row-main" }, [title, chips.length ? el("div", { className: "mods" }, chips) : null]),
        ]));
    };

    for (const group of groups) {
        const first = group.dishes[0];
        if (!group.combo) {
            row({ qty: `${first.quantity}×`, name: first.name, station: first.station, allergies: first.allergies, tags: first.tags, note: first.note });
        } else if (group.dishes.length === 1) {
            row({ qty: `${first.quantity}×`, name: `${group.combo} (${first.name})`, station: first.station, allergies: first.allergies,
                  tags: [...first.combo_tags, ...first.tags], note: first.note });
        } else {
            row({ qty: `${first.quantity}×`, name: group.combo, isCombo: true, tags: first.combo_tags });
            group.dishes.forEach((dish) => row({ child: true, name: dish.name, station: dish.station, allergies: dish.allergies, tags: dish.tags, note: dish.note }));
        }
    }
    return rows;
}

function facts(order) {
    const placed = new Date(order.timestamp);
    const start = new Date(order.start_time);
    const scheduled = start - placed > 60000;
    const fact = (path, label, sub) => el("div", { className: "fact" }, [
        el("span", { className: "fact-icon" }, [icon(path)]),
        el("span", {}, [el("span", { className: "strong", text: label }), sub ? el("span", { className: "cap", text: sub }) : null]),
    ]);
    const chips = [];
    if (order.channel === "store") {
        chips.push(fact(P.store, t("In person"), order.cashier
            ? fmtText("Placed %s by %s", clockOf(placed), order.cashier) : fmtText("Placed %s", clockOf(placed))));
    } else if (order.channel === "web") {
        chips.push(fact(P.bag, t("Online pickup"), scheduled ? fmtText("Pickup at %s", clockOf(start)) : fmtText("Placed %s", clockOf(placed))));
    } else {
        chips.push(fact(P.scooter, t("Delivery"), order.address || fmtText("Placed %s", clockOf(placed))));
    }
    if (order.to_go_order) chips.push(fact(P.box, t("To go"), order.channel === "store" ? t("Customer is waiting") : t("Pack for travel")));
    else chips.push(fact(P.plate, t("For here"), t("Dine in")));
    if (order.phone && order.channel !== "store") chips.push(fact(P.phone, String(order.phone), t("Phone")));
    return chips;
}

function stationBadges(order) {
    return involved(order).map((code) => {
        const label = `${stationBy[code]?.name ?? code}: ${STATUS_TEXT[stationKey(order, code)](order)}`;
        return el("div", { className: "station-badge" + (state.filters.includes(code) ? " mine" : ""), attrs: { role: "img", "aria-label": label, title: label } }, [
            el("span", { className: "box" }, [stationIcon(code), statusIcon(stationKey(order, code))]),
            el("span", { className: "cap", text: stationBy[code]?.name ?? code }),
        ]);
    });
}

function waitBox(order, key) {
    if (key === "pickup") {
        return el("div", { className: "wait-box pickup", attrs: { role: "status" } }, [
            el("span", { className: "round" }, [icon(P.scooter, 22)]),
            el("div", { className: "grow" }, [
                el("div", { className: "disp", text: t("Waiting for the courier") }),
                el("div", { className: "cap", text: t("Every station is done. The courier marks it picked up.") }),
            ]),
        ]);
    }
    const waitingOn = stationNames(others(order).filter((code) => statusOf(order, code) === (key === "confirmed" ? 0 : 1)));
    const sub = key === "confirmed"
        ? (waitingOn.length ? fmtText("%s still to confirm. Cooking starts once every station is in.", joinNames(waitingOn)) : t("Cooking starts once every station is in."))
        : (waitingOn.length ? fmtText("%s still making their part. It's ready for pickup once every station is done.", joinNames(waitingOn)) : t("It's ready for pickup once every station is done."));
    return el("div", { className: "wait-box", attrs: { role: "status" } }, [
        el("span", { className: "round" }, [icon(P.hourglass, 22)]),
        el("div", { className: "grow" }, [el("div", { className: "disp", text: t("Waiting on other stations") }), el("div", { className: "cap", text: sub })]),
        (key === "ready" && autoDue(order)) || el("span", { className: "cap you" }, [icon(P.check, 16, 2.6), key === "confirmed" ? t("You confirmed") : t("You marked it ready")]),
    ]);
}

function actions(order, key) {
    const myNames = joinNames(stationNames(mine(order)));
    const stillAt = stationNames(others(order).filter((code) => statusOf(order, code) < 2));
    let hint = key === "new" ? fmtText("Confirms for %s", myNames)
        : key === "progress" ? fmtText("Marks %s ready", myNames)
        : t("Every station is done");
    if (stillAt.length && key !== "pickup") hint += " · " + fmtText("Still at %s", joinNames(stillAt));

    const buttons = [];
    if (key === "new") {
        buttons.push(el("button", { className: "act reject", on: { click: () => openReject(order.order_id) } },
            [icon(P.cross, 18, 2.6), t("Reject"), kbd("Esc")]));
        buttons.push(el("button", { className: "act confirm", on: { click: () => act(order.order_id, "confirm") } },
            [icon(P.check, 20, 2.6), t("Confirm"), kbd("Enter")]));
    } else if (key === "progress") {
        buttons.push(el("button", { className: "act ready", on: { click: () => act(order.order_id, "ready") } },
            [icon(P.checkCircle, 20, 2.6), t("Mark ready"), kbd("Enter", true)]));
    } else {
        buttons.push(el("button", { className: "act pickup", on: { click: () => act(order.order_id, "pickup") } },
            [icon(P.handoff, 20, 2.2), t("Picked up"), kbd("Enter", true)]));
    }
    return el("div", { className: "actions" }, [el("span", { className: "cap hint", text: hint }), autoDue(order), ...buttons]);
}

function timerClass(order) {
    const age = ageOf(order);
    return "timer" + (age >= 720 ? " late" : age >= 420 ? " warn" : "");
}

function buildCard(order, open) {
    const key = statusKey(order);
    const rows = buildRows(order);
    const dishes = visibleDishes(order);
    const count = dishes.reduce((sum, dish) => sum + dish.quantity, 0);
    const allergies = [...new Set(dishes.flatMap((dish) => dish.allergies))];
    const allergyItems = [...new Set(dishes.filter((dish) => dish.allergies.length).map((dish) => dish.name))];
    const name = order.name || t("No name");

    const timer = el("span", { className: timerClass(order), attrs: { "data-start": order.start_time } },
        [icon(P.clock, 18, 2.2), el("span", { text: fmtElapsed(ageOf(order)) })]);
    const head = el("button", { className: "card-head", attrs: { "aria-expanded": String(open) }, on: { click: () => toggle(order.order_id) } }, [
        el("span", { className: "order-num" }, [el("span", { className: "cap", text: "#" }), el("span", { className: "disp", text: order.order_id })]),
        el("span", { className: "order-who" }, [
            el("span", { className: "disp order-name", text: name }),
            el("span", { className: "cap order-count", text: interpolate(ngettext("%s item", "%s items", count), [count]) }),
        ]),
        allergies.length ? el("span", { className: "overline allergy-flag" }, [icon(P.alert, 16, 2.4), t("Allergy")]) : null,
        el("span", { className: "order-status" }, [statusIcon(key), el("span", { className: "cap", text: STATUS_TEXT[key](order) })]),
        timer,
        el("span", { className: "chev" }, [icon(P.chevron, 20, 2.4)]),
    ]);

    const card = el("article", { className: "card" + (open ? " open" : ""), attrs: { "data-order": order.order_id, "aria-label": fmtText("Order %s, %s", order.order_id, name) } }, [head]);
    if (!open) return card;

    const body = el("div", { className: "card-body" });
    if (allergies.length) {
        body.append(el("div", { className: "allergy-alert", attrs: { role: "alert" } }, [
            el("span", { className: "round" }, [icon(P.alert, 24, 2.4)]),
            el("div", { className: "grow" }, [el("div", { className: "overline", text: t("Allergy alert") }), el("div", { className: "disp", text: allergies.join(" · ") })]),
            el("div", { className: "cap", text: fmtText("On %s", joinNames(allergyItems)) }),
        ]));
    }
    body.append(el("div", { className: "facts" }, [
        el("div", { className: "fact-chips" }, facts(order)),
        el("div", { className: "stations-box" }, [el("div", { className: "overline", text: t("Stations") }), el("div", { className: "station-badges" }, stationBadges(order))]),
    ]));
    body.append(el("div", { className: "rows" }, rows));
    if (order.special_instructions && order.note_for_kitchen !== false) {
        body.append(el("div", { className: "note-box" }, [
            svg(`<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#1F4A3A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${P.note}"></path></svg>`),
            el("div", {}, [el("div", { className: "overline", text: t("Order note") }), el("div", { className: "strong", text: order.special_instructions })]),
        ]));
    }
    body.append(primaryAction(order) ? actions(order, key) : waitBox(order, key));
    card.append(body);
    return card;
}

// ---------- rendering ----------

const listNode = $("#list");
const cards = new Map();   // order id -> { node, sig, open }

function render() {
    const shown = visibleOrders();
    const ids = shown.map((o) => o.order_id);

    // Keep a card open: the one that was, else its neighbour, else (at start) the first
    if (state.openId !== null && !ids.includes(state.openId)) {
        state.openId = null;
        state.autoOpen = true;
    }
    if (state.openId === null && state.autoOpen && ids.length) {
        state.openId = ids[Math.min(state.lastIndex, ids.length - 1)];
        state.autoOpen = false;
    }
    if (state.openId !== null) state.lastIndex = ids.indexOf(state.openId);

    const filterKey = state.filters.join(",");
    for (const [id, card] of cards) {
        if (!ids.includes(id)) {
            card.node.remove();
            cards.delete(id);
        }
    }
    let previous = null;
    let scrollTo = null;
    for (const order of shown) {
        const open = order.order_id === state.openId;
        const sig = JSON.stringify([order, open, filterKey, autoStep(order)]);
        let card = cards.get(order.order_id);
        if (!card || card.sig !== sig) {
            const node = buildCard(order, open);
            if (!card) node.classList.add("card-in");
            else if (open && !card.open) node.querySelector(".card-body")?.classList.add("open-in");
            if (open && (!card || !card.open)) scrollTo = node;
            card?.node.replaceWith(node);
            card = { node, sig, open };
            cards.set(order.order_id, card);
        }
        const want = previous ? previous.nextSibling : listNode.firstChild;
        if (want !== card.node) listNode.insertBefore(card.node, want);
        previous = card.node;
    }

    let empty = listNode.querySelector(".empty");
    if (!shown.length && !empty) {
        empty = el("div", { className: "empty" }, [
            el("span", { className: "round" }, [icon(P.check, 28, 2.4)]),
            el("div", { className: "disp", text: t("All caught up") }),
            el("div", { className: "strong", attrs: { id: "empty-sub" } }),
        ]);
        listNode.append(empty);
    } else if (shown.length && empty) {
        empty.remove();
    }
    if (!shown.length) $("#empty-sub").textContent = fmtText("New orders for %s will appear here.", headTitle());

    renderHeader(shown);
    renderFilterSheet();
    if (scrollTo) reveal(scrollTo);
}

// Scroll so the open card shows from its top (all of it, when it fits)
function reveal(node) {
    requestAnimationFrame(() => listNode.scrollTo({ top: Math.max(0, node.offsetTop - 2), behavior: "smooth" }));
}

const headTitle = () => state.filters.length === STATIONS.length ? t("All stations") : joinNames(stationNames(state.filters));

function renderHeader(shown) {
    $("#head-title").textContent = headTitle();
    $("#filter-count").textContent = state.filters.length;
    $("#filter-open").setAttribute("aria-label", fmtText("Filter stations, %s selected", state.filters.length));
    tickHeader(shown);

    const keys = shown.map(statusKey);
    const pills = [
        ["new", t("Not confirmed"), keys.filter((k) => k === "new").length],
        ["confirmed", t("Waiting"), keys.filter((k) => k === "confirmed" || k === "ready").length],
        ["progress", t("In progress"), keys.filter((k) => k === "progress").length],
        ["pickup", t("Ready for pickup"), keys.filter((k) => k === "pickup").length],
    ];
    $("#summary").replaceChildren(...pills.map(([key, label, count]) => el("div", { className: "summary-pill" }, [
        statusIcon(key, 20), el("span", { className: "disp", text: count }), el("span", { className: "cap", text: label }),
    ])));
}

function tickHeader(shown = visibleOrders()) {
    $("#head-over").textContent = shown.length
        ? fmtText("%s open · oldest waiting %s", shown.length, fmtElapsed(ageOf(shown[0])))
        : t("No open orders");
}

// ---------- timers ----------

setInterval(() => {
    listNode.querySelectorAll(".timer[data-start]").forEach((node) => {
        const order = { start_time: node.dataset.start };
        node.lastElementChild.textContent = fmtElapsed(ageOf(order));
        node.className = timerClass(order);
    });
    listNode.querySelectorAll(".auto-due[data-due]").forEach((node) => {
        node.lastElementChild.textContent = interpolate(node.dataset.label, [fmtElapsed((Number(node.dataset.due) - nowMs()) / 1000)]);
    });
    tickHeader();
}, 1000);

// ---------- toast ----------

const TOAST = {
    ok: [P.check, "#F4A93B"],
    wait: [P.hourglass, "#A9BBB2"],
    reject: [P.cross, "#FF9A85"],
    error: [P.alert, "#FF9A85"],
};
let toastTimer;
function say(text, kind = "ok") {
    const [path, color] = TOAST[kind];
    const mark = icon(path, 18, 2.8);
    mark.setAttribute("stroke", color);
    $("#toasts").replaceChildren(el("div", { className: "kds-toast", attrs: { role: kind === "error" ? "alert" : "status" } }, [mark, el("span", { className: "strong", text })]));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("#toasts").replaceChildren(), kind === "error" ? 6000 : 4200);
}

// ---------- server ----------

let refreshing = null;
async function refresh() {
    // One fetch at a time; a refresh asked for during one runs right after it
    if (refreshing) return refreshing.then(refresh);
    refreshing = (async () => {
        try {
            const response = await fetch(FEED_URL, { headers: { Accept: "application/json" } });
            if (!response.ok) return;
            const serverDate = Date.parse(response.headers.get("Date"));
            if (Number.isFinite(serverDate) && Math.abs(serverDate - nowMs()) > 2000) clockOffset = serverDate - Date.now();
            const orders = await response.json();
            state.orders = new Map(orders.map((order) => [order.order_id, order]));
            render();
        } catch {
            /* offline: the live stream reconnects and refreshes */
        }
    })();
    try { await refreshing; } finally { refreshing = null; }
}

// Do `action` for this screen's stations. The open order stays open (unless it leaves the screen).
async function act(id, action, extra = {}, { auto = false } = {}) {
    if (state.busy) return;
    const before = state.orders.get(id);
    if (!before) return;
    state.busy = true;
    try {
        const { ok, data: result } = await post(window.location.pathname, { orderId: id, action, filters: mine(before), ...extra });
        if (!ok) {
            const error = result.error || t("That didn't work. Try again.");
            say(auto ? fmtText("Auto: %s", error) : error, "error");
            await refresh();
            return false;
        }
        bumpStats({ tickets: 1 });
        await refresh();
        const order = state.orders.get(id);
        const tell = (text, kind) => say(auto ? fmtText("Auto: %s", text) : text, kind);
        if (action === "confirm") {
            const pending = order ? stationNames(involved(order).filter((code) => statusOf(order, code) === 0)) : [];
            if (result.payment_error) tell(fmtText("Order #%s confirmed, but taking the card payment failed: %s", id, result.payment_error), "error");
            else if (pending.length) tell(fmtText("Order #%s confirmed · waiting on %s", id, joinNames(pending)), "wait");
            else tell(fmtText("Order #%s confirmed by every station · cooking", id));
        } else if (action === "ready") {
            const making = order ? stationNames(involved(order).filter((code) => statusOf(order, code) === 1)) : [];
            if (making.length) tell(fmtText("Order #%s ready here · waiting on %s", id, joinNames(making)), "wait");
            else tell(fmtText("Order #%s is ready for pickup", id));
        } else if (action === "pickup") {
            tell(fmtText("Order #%s picked up", id));
        } else if (action === "reject") {
            tell(fmtText("Order #%s rejected · %s", id, result.reason), "reject");
        }
        return true;
    } finally {
        state.busy = false;
    }
}

// ---------- opening cards ----------

function toggle(id) {
    if (state.openId === id) {
        state.openId = null;
        state.autoOpen = false;
    } else {
        state.openId = id;
    }
    render();
}

function step(delta) {
    const ids = visibleOrders().map((o) => o.order_id);
    if (!ids.length) return;
    const index = ids.indexOf(state.openId);
    const next = index < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, index + delta));
    if (ids[next] === state.openId) {
        cards.get(state.openId)?.node.classList.add("nudge");
        setTimeout(() => cards.get(state.openId)?.node.classList.remove("nudge"), 700);
        return;
    }
    state.openId = ids[next];
    render();
}

// ---------- reject sheet ----------

const rejectSheet = $("#reject-sheet");
const rejectText = $("#reject-text");
const reasonButtons = REASONS.map((reason, index) => {
    const button = el("button", { className: "reason", attrs: { "aria-pressed": "false", type: "button" }, on: { click: () => toggleReason(reason.code) } },
        [reason.label, index < 9 ? kbd(`Ctrl ${index + 1}`) : null]);
    button.dataset.code = reason.code;
    return button;
});
$("#reasons").replaceChildren(...reasonButtons);

function rejectReason() {
    const picked = REASONS.filter((r) => state.reject.picked.has(r.code)).map((r) => r.label).join("; ");
    const text = rejectText.value.trim();
    return picked && text ? `${picked}: ${text}` : picked || text;
}

function renderReject() {
    reasonButtons.forEach((button) => button.setAttribute("aria-pressed", String(state.reject.picked.has(button.dataset.code))));
    $("#reject-count").textContent = `${rejectText.value.length}/200`;
    const reason = rejectReason();
    $("#reject-go").disabled = !reason;
    $("#reject-hint").textContent = reason ? "" : t("Pick a reason or write one");
}

function toggleReason(code) {
    if (state.reject.picked.has(code)) state.reject.picked.delete(code);
    else state.reject.picked.add(code);
    renderReject();
}

function openReject(id) {
    const order = state.orders.get(id);
    if (!order || statusKey(order) !== "new") return;
    state.reject = { id, picked: new Set() };
    rejectText.value = "";
    $("#reject-title").textContent = `#${order.order_id} · ${order.name || t("No name")}`;
    renderReject();
    rejectSheet.showModal();
    rejectText.focus();
}

async function sendReject() {
    if (!rejectReason() || state.busy) return;
    const { id, picked } = state.reject;
    const done = await act(id, "reject", { reasons: [...picked], text: rejectText.value.trim() });
    if (done !== false) rejectSheet.close();
}

rejectText.addEventListener("input", renderReject);
$("#reject-go").addEventListener("click", sendReject);
rejectSheet.addEventListener("close", () => { state.reject = { id: null, picked: new Set() }; });
rejectSheet.addEventListener("keydown", (e) => {
    e.stopPropagation();  // the sheet handles its own keys; the screen's Enter must not see them
    const digit = /^(?:Digit|Numpad)([1-9])$/.exec(e.code);
    if (digit && (e.ctrlKey || e.altKey) && !e.metaKey) {
        e.preventDefault();
        const reason = REASONS[Number(digit[1]) - 1];
        if (reason) toggleReason(reason.code);
        return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendReject();
        return;
    }
    // Anything typed goes into the reason, wherever the focus is in the sheet
    if (e.target !== rejectText && !e.ctrlKey && !e.altKey && !e.metaKey && (e.key.length === 1 || e.key === "Backspace")) {
        rejectText.focus();
    }
});

// ---------- filter sheet ----------

const filterSheet = $("#filter-sheet");
const allCodes = STATIONS.map((s) => s.code);

$("#legend").append(...["new", "confirmed", "progress", "ready", "pickup"].map((key) =>
    el("span", { className: "legend-item" }, [statusIcon(key, 20), el("span", { className: "cap", text: STATUS_TEXT[key]() })])));

function renderFilterSheet() {
    const open = [...state.orders.values()].filter((o) => !o.picked_up);
    $("#station-grid").replaceChildren(...STATIONS.map((station, index) => {
        const on = state.filters.includes(station.code);
        const count = open.filter((o) => (o.stations[station.code] ?? 9) < 2).length;
        return el("button", { className: "station-toggle", attrs: { "aria-pressed": String(on), type: "button" }, on: { click: () => toggleStation(station.code) } }, [
            el("span", { className: "icon" }, [stationIcon(station.code)]),
            el("span", { className: "grow" }, [
                el("span", { className: "strong", text: station.name }),
                el("span", { className: "cap", text: count ? interpolate(ngettext("%s open order", "%s open orders", count), [count]) : t("All caught up") }),
            ]),
            index < 9 ? kbd(String(index + 1)) : null,
            el("span", { className: "check" }, [icon(P.check, 14, 3.2)]),
        ]);
    }));
}

function setFilters(codes) {
    state.filters = allCodes.filter((code) => codes.includes(code));
    const params = new URLSearchParams(window.location.search);
    params.delete("filter");
    if (state.filters.length !== allCodes.length) params.set("filter", state.filters.join(","));
    const query = params.toString();
    history.replaceState(null, "", window.location.pathname + (query ? "?" + query : ""));
    render();
}

function toggleStation(code) {
    const next = state.filters.includes(code) ? state.filters.filter((c) => c !== code) : [...state.filters, code];
    if (next.length) setFilters(next);  // keep at least one station
}

const filterButton = $("#filter-open");
function openFilter() {
    renderFilterSheet();
    filterSheet.showModal();
    filterButton.setAttribute("aria-expanded", "true");
}
filterButton.addEventListener("click", openFilter);
filterSheet.addEventListener("close", () => filterButton.setAttribute("aria-expanded", "false"));
$("#select-all").addEventListener("click", () => setFilters(allCodes));
filterSheet.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const digit = /^(?:Digit|Numpad)([1-9])$/.exec(e.code);
    if (digit) {
        e.preventDefault();
        const station = STATIONS[Number(digit[1]) - 1];
        if (station) toggleStation(station.code);
    } else if (e.key === "a" || e.key === "A") {
        e.preventDefault();
        setFilters(allCodes);
    } else if (e.key === "Enter") {
        e.preventDefault();
        filterSheet.close();
    }
});

// ---------- auto sheet ----------

const autoSheet = $("#auto-sheet");
const autoButton = $("#auto-open");
const autoInputs = { ready: $("#auto-ready"), pickup: $("#auto-pickup") };

function renderAutoButton() {
    const on = Boolean(state.auto.ready || state.auto.pickup);
    autoButton.setAttribute("aria-pressed", String(on));
    $("#auto-badge").hidden = !on;
    const parts = [];
    if (state.auto.ready) parts.push(fmtText("ready after %s min", state.auto.ready));
    if (state.auto.pickup) parts.push(fmtText("picked up after %s min", state.auto.pickup));
    autoButton.setAttribute("aria-label", on ? fmtText("Auto: %s", parts.join(", ")) : t("Auto: off"));
}

function openAuto() {
    autoInputs.ready.value = state.auto.ready ?? "";
    autoInputs.pickup.value = state.auto.pickup ?? "";
    autoSheet.showModal();
    autoInputs.ready.focus();
    autoInputs.ready.select();
}

function saveAuto() {
    const minutes = (input) => {
        const value = parseInt(input.value, 10);
        return value > 0 ? Math.min(value, 240) : null;
    };
    state.auto = { ready: minutes(autoInputs.ready), pickup: minutes(autoInputs.pickup) };
    state.retryAt.clear();
    const query = new URLSearchParams(window.location.search);
    for (const [name, value] of [["auto-ready", state.auto.ready], ["auto-pickup", state.auto.pickup]]) {
        if (value) query.set(name, value);
        else query.delete(name);
    }
    const search = query.toString();
    history.replaceState(null, "", window.location.pathname + (search ? "?" + search : ""));
    autoSheet.close();
    renderAutoButton();
    render();
    runAuto();
}

autoButton.addEventListener("click", openAuto);
$("#auto-save").addEventListener("click", saveAuto);
autoSheet.addEventListener("keydown", (e) => {
    e.stopPropagation();  // keep Enter away from the screen's handler
    if (e.key === "Enter") {
        e.preventDefault();
        saveAuto();
    }
});
renderAutoButton();

// ---------- keyboard ----------

window.addEventListener("keydown", (e) => {
    if (document.querySelector("dialog[open]") || e.ctrlKey || e.altKey || e.metaKey || e.repeat && e.key === "Enter") return;
    const order = state.orders.get(state.openId);
    switch (e.key) {
        case "ArrowDown":
        case "ArrowUp":
            e.preventDefault();
            step(e.key === "ArrowDown" ? 1 : -1);
            break;
        case "Enter": {
            e.preventDefault();  // don't also "click" whatever button has focus
            const action = order && primaryAction(order);
            if (action) act(order.order_id, action);
            else if (order) cards.get(order.order_id)?.node.querySelector(".wait-box")?.classList.add("nudge");
            break;
        }
        case "Escape":
            if (order && statusKey(order) === "new") {
                e.preventDefault();
                openReject(order.order_id);
            }
            break;
        case "f":
        case "F":
            e.preventDefault();
            openFilter();
            break;
        case "a":
        case "A":
            e.preventDefault();
            openAuto();
            break;
        case "PageDown":
        case "PageUp":
            e.preventDefault();
            listNode.scrollBy({ top: (e.key === "PageDown" ? 1 : -1) * listNode.clientHeight * 0.8, behavior: "smooth" });
            break;
    }
});
document.addEventListener("animationend", (e) => e.target.classList?.remove("nudge"));

// ---------- start ----------

wireSheets();
render();
onLiveUpdate("orders", refresh);
