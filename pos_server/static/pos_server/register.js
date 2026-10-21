// The register (see docs/mockups/Register-html and pos_server/register.py).
//
// The cart lives in `state` (kept in sessionStorage so a reload or a trip to the drawer doesn't lose it).
// Line prices are worked out here for instant feedback, but discounts, tax and the total always come from
// the server's quote (pos_server/pricing.py), and the server prices the order again when it is placed.
// Money is in integer cents throughout.
import { onLiveUpdate } from "/static/js/live.js";
import { $, csrftoken, dollars, el, fmt, fmtSigned, post, toCents, toast, wireSheets } from "./staff-ui.js";
import { bumpStats } from "./device-stats.js";

const boot = JSON.parse(document.getElementById("register-boot").textContent);
const urls = boot.urls;
const STORE_KEY = "pavilion-register";
const cartChannel = new BroadcastChannel("cart_channel");
let catalog = boot.catalog;

// ---------- small helpers ----------

const t = (text) => gettext(text);
const clone = (value) => JSON.parse(JSON.stringify(value ?? {}));
const pad = (n) => String(n).padStart(4, "0");

// ---------- state ----------

function freshCart() {
    return { lines: [], orderDiscount: null, name: "", toGo: false, note: "", noteKitchen: true, payments: [], nextKey: 1 };
}

function loadCart() {
    try {
        const saved = JSON.parse(sessionStorage.getItem(STORE_KEY));
        if (saved && Array.isArray(saved.lines)) return { ...freshCart(), ...saved };
    } catch { /* storage unavailable or corrupt */ }
    return freshCart();
}

let state = loadCart();
const ui = {
    section: catalog.sections[0]?.code,
    search: "",
    mode: state.payments.length ? "pay" : "cart",
    method: "cash",
    moreKey: null,
    discListOpen: false,
    quote: null,
    quoteFresh: false,
    quoteError: "",
    quoteSeq: 0,
    tendered: null,          // cents handed over, null = exact
    splitOn: false,
    card: "idle",            // idle | starting | waiting
    gift: null,              // {number, balance} found
    submitting: false,
    orderNumber: boot.orderNumber,
    newKeys: new Set(),
};

function save() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* private mode */ }
}

// ---------- catalog and prices ----------

const dishOf = (id) => catalog.dishes[id];
const comboOf = (id) => catalog.combos[id];
const itemOf = (line) => catalog.items.find((item) => item.type === line.type && item.id === line.id);
const titleOf = (line) => (line.type === "dish" ? dishOf(line.id)?.title : comboOf(line.id)?.title) ?? t("Unavailable item");

function visibleGroups(kit, cust) {
    const parts = cust?.parts || {};
    return kit.groups.filter((g) => g.part === null || (parts[g.part] ?? 1) > 0);
}

// A pick-one group's choice (its standard when untouched; null = nothing), a pick-any group's {option id: count}
const pickOne = (group, cust) => ((cust.options || {})[group.key] !== undefined ? (cust.options || {})[group.key] : group.default);
const pickAny = (group, cust) => {
    const value = (cust.options || {})[group.key];
    return value && typeof value === "object" ? value : {};
};

function kitCost(kit, cust) {
    if (!kit) return 0;
    cust = cust || {};
    let total = 0;
    for (const part of kit.parts) {
        const n = (cust.parts || {})[part.id] ?? part.standard;
        if (n > part.standard) total += (n - part.standard) * part.extra;
    }
    for (const group of visibleGroups(kit, cust)) {
        if (group.pick === "any") {
            const counts = pickAny(group, cust);
            for (const option of group.options) total += option.price * (counts[option.id] || 0);
        } else {
            const id = pickOne(group, cust);
            total += group.options.find((o) => o.id === id)?.price || 0;
        }
    }
    for (const condiment of kit.condiments) total += condiment.price * ((cust.condiments || {})[condiment.id] || 0);
    return total;
}

// Required pick-any groups with nothing picked (the sheet can't be saved until they have something)
const missingPicks = (kit, cust) => (kit ? visibleGroups(kit, cust || {}).filter((g) => g.required && (g.pick === "any"
    ? !Object.values(pickAny(g, cust || {})).some((n) => n > 0) : pickOne(g, cust || {}) == null)) : []);

function kitSummary(kit, cust) {
    const out = [];
    if (!kit || !cust) return out;
    for (const part of kit.parts) {
        const n = (cust.parts || {})[part.id];
        if (n !== undefined && n !== part.standard) out.push(n === 0 ? interpolate(t("No %s"), [part.title.toLowerCase()]) : `${n}× ${part.title.toLowerCase()}`);
    }
    for (const group of visibleGroups(kit, cust)) {
        if (group.pick === "any") {
            const counts = pickAny(group, cust);
            for (const option of group.options) {
                const n = counts[option.id] || 0;
                if (n) out.push(n > 1 ? `+ ${n}× ${option.title.toLowerCase()}` : "+ " + option.title.toLowerCase());
            }
            continue;
        }
        const id = (cust.options || {})[group.key];
        if (id != null && id !== group.default) {
            const option = group.options.find((o) => o.id === id);
            if (option) out.push(option.title);
        }
    }
    for (const condiment of kit.condiments) {
        const n = (cust.condiments || {})[condiment.id] || 0;
        if (n) out.push(n > 1 ? `${n}× ${condiment.title.toLowerCase()}` : condiment.title.toLowerCase());
    }
    if ((cust.allergies || []).length) out.push(interpolate(t("Allergy: %s"), [cust.allergies.map((a) => a.toLowerCase()).join(", ")]));
    if ((cust.note || "").trim()) out.push("“" + cust.note.trim() + "”");
    return out;
}

// Drop everything that is just the standard, so equal customizations compare equal.
function normalizeCust(kit, cust) {
    const out = {};
    cust = cust || {};
    const parts = {};
    for (const part of kit?.parts || []) {
        const n = (cust.parts || {})[part.id];
        if (n !== undefined && n !== part.standard) parts[part.id] = n;
    }
    if (Object.keys(parts).length) out.parts = parts;
    const options = {};
    for (const group of kit ? visibleGroups(kit, { parts }) : []) {
        if (group.pick === "any") {
            const counts = Object.fromEntries(Object.entries(pickAny(group, cust)).filter(([, n]) => n > 0).sort());
            if (Object.keys(counts).length) options[group.key] = counts;
            continue;
        }
        const id = (cust.options || {})[group.key];
        if (id != null && id !== group.default) options[group.key] = id;
    }
    if (Object.keys(options).length) out.options = options;
    const condiments = {};
    for (const [id, n] of Object.entries(cust.condiments || {})) if (n > 0) condiments[id] = n;
    if (Object.keys(condiments).length) out.condiments = condiments;
    if ((cust.allergies || []).length) out.allergies = [...cust.allergies];
    if ((cust.note || "").trim()) out.note = cust.note.trim().slice(0, 140);
    return out;
}

const extrasKit = (combo) => ({ parts: [], groups: [], condiments: combo.condiments });

function slotOption(slot, dishId) {
    return slot.options.find((o) => o.dish === dishId);
}

// A combo line keeps every pick: line.slots = {slot id: [{dish, cust}]}; line.base = {slot id: [dish ids]} is what
// was picked when it was added (so the cart only lists changes from that).

// What a choice starts with: a required one its default dish, then the others in order (skipping sold-out
// ones when it can); an optional one nothing.
function defaultPicks(slot) {
    if (!slot.required) return [];
    const standard = slot.options.find((o) => o.default) || slot.options[0];
    const ordered = [standard, ...slot.options.filter((o) => o !== standard)].filter(Boolean);
    const open = ordered.filter((o) => dishOf(o.dish)?.available);
    return (open.length >= slot.picks ? open : ordered).slice(0, slot.picks).map((o) => o.dish);
}

const slotPicks = (line, slot) => {
    const saved = (line.slots || {})[slot.id];
    return Array.isArray(saved) ? saved : defaultPicks(slot).map((dish) => ({ dish }));  // (a cart saved before picks were lists)
};
const baseIds = (line, slot) => (Array.isArray((line.base || {})[slot.id]) ? line.base[slot.id] : defaultPicks(slot));
const pickIds = (line, slot) => slotPicks(line, slot).map((p) => p.dish);
// The choices that are a step when the combo is added: anything with something to choose
const stepSlots = (combo) => combo.slots.filter((s) => s.options.length > (s.required ? 1 : 0));

// The combo's own price for these picks (set price + upcharges, or percent off the dishes)
function comboPrice(combo, picksBySlot) {
    const chosen = combo.slots.flatMap((slot) => (picksBySlot[slot.id] || []).map((id) => ({ slot, id })));
    if (combo.pricing === "percent") {
        const total = chosen.reduce((sum, { id }) => sum + (dishOf(id)?.price || 0), 0);
        return Math.round(total * (100 - combo.percentOff) / 100);
    }
    return combo.price + chosen.reduce((sum, { slot, id }) => sum + (slotOption(slot, id)?.price || 0), 0);
}

function unitPrice(line) {
    if (line.type === "dish") {
        const dish = dishOf(line.id);
        return dish ? dish.price + kitCost(dish.kit, line.cust) : 0;
    }
    const combo = comboOf(line.id);
    if (!combo) return 0;
    const ids = Object.fromEntries(combo.slots.map((slot) => [slot.id, pickIds(line, slot)]));
    let total = comboPrice(combo, ids) + kitCost(extrasKit(combo), line.cust);
    for (const slot of combo.slots) {
        for (const pick of slotPicks(line, slot)) total += kitCost(dishOf(pick.dish)?.kit, pick.cust);
    }
    return total;
}

const sameIds = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// What changed on a line, as short readable bits.
function lineSummary(line) {
    if (line.type === "dish") return kitSummary(dishOf(line.id)?.kit, line.cust);
    const combo = comboOf(line.id);
    if (!combo) return [];
    const out = [];
    for (const slot of combo.slots) {
        const ids = pickIds(line, slot);
        if (!sameIds(ids, baseIds(line, slot))) {
            out.push(ids.length ? `${slot.title}: ${ids.map((id) => dishOf(id)?.title).join(", ")}` : interpolate(t("No %s"), [slot.title.toLowerCase()]));
        }
        for (const pick of slotPicks(line, slot)) {
            const bits = kitSummary(dishOf(pick.dish)?.kit, pick.cust);
            if (bits.length) out.push(`${dishOf(pick.dish)?.title} (${bits.join(", ")})`);
        }
    }
    out.push(...kitSummary(extrasKit(combo), line.cust));
    return out;
}

// The picks shown under a combo's name, e.g. "Fries · Iced latte" for the choices asked for when it was added.
function lineAskedPick(line) {
    if (line.type !== "combo" || !line.base) return "";
    const combo = comboOf(line.id);
    const bits = [];
    for (const slot of stepSlots(combo || { slots: [] })) {
        const ids = pickIds(line, slot);
        if (slot.id in line.base && ids.length && sameIds(ids, line.base[slot.id])) bits.push(...ids.map((id) => dishOf(id)?.title || ""));
    }
    return bits.join(" · ");
}

const shortOf = (kind, value) => (kind === "pct" ? `${value}% off` : `${fmt(value)} off`);

function serverLine(line) {
    const out = { key: line.key, type: line.type, id: line.id, qty: line.qty, cust: line.cust || {} };
    if (line.type === "combo") out.slots = line.slots || {};
    if (line.discount) out.discount = line.discount.spec;
    return out;
}

function cartBody(extra = {}) {
    return {
        lines: state.lines.map(serverLine),
        orderDiscount: state.orderDiscount ? state.orderDiscount.spec : null,
        ...extra,
    };
}

// ---------- quote ----------

let quoteTimer = null;

function requestQuote() {
    ui.quoteFresh = false;
    clearTimeout(quoteTimer);
    renderTotals();
    if (!state.lines.length) {
        ui.quote = null;
        ui.quoteError = "";
        ui.quoteFresh = true;
        renderTotals();
        return;
    }
    quoteTimer = setTimeout(runQuote, 120);
}

async function runQuote() {
    const seq = ++ui.quoteSeq;
    const keys = state.lines.map((line) => line.key);
    let result;
    try {
        result = await post(urls.quote, cartBody());
    } catch {
        if (seq === ui.quoteSeq) { ui.quoteError = t("The register is offline. Check the connection."); renderTotals(); }
        return;
    }
    if (seq !== ui.quoteSeq) return; // a newer quote is on its way
    if (!result.ok) {
        ui.quote = null;
        ui.quoteError = result.data.message || t("The order can't be priced.");
        ui.quoteFresh = true;
        renderAll();
        return;
    }
    // Discounts that stopped applying (e.g. the order got too small) are dropped, with the reason
    let dropped = false;
    result.data.lines.forEach((quoted, index) => {
        const line = state.lines.find((l) => l.key === keys[index]);
        if (quoted.discountError && line?.discount) {
            toast(quoted.discountError, { error: true });
            line.discount = null;
            dropped = true;
        }
    });
    if (result.data.orderDiscountError && state.orderDiscount) {
        toast(result.data.orderDiscountError, { error: true });
        state.orderDiscount = null;
        dropped = true;
    }
    if (dropped) {
        save();
        requestQuote();
        return;
    }
    ui.quote = { ...result.data, keys };
    ui.quoteError = "";
    ui.quoteFresh = true;
    renderAll();
}

const quotedLine = (line) => {
    const index = ui.quote?.keys.indexOf(line.key) ?? -1;
    return index >= 0 ? ui.quote.lines[index] : null;
};

// ---------- cart changes ----------

function changed({ quote = true } = {}) {
    save();
    if (quote) requestQuote();
    renderAll();
}

function addLine(line) {
    const same = (a, b) => a.type === b.type && a.id === b.id && !a.discount &&
        JSON.stringify([a.cust || {}, a.slots || {}, a.base || {}]) === JSON.stringify([b.cust || {}, b.slots || {}, b.base || {}]);
    const existing = state.lines.find((l) => same(l, line));
    let ask = null;
    if (existing) {
        existing.qty = Math.min(99, existing.qty + 1);
    } else {
        const key = "L" + state.nextKey++;
        state.lines.push({ cust: {}, ...line, key, qty: 1, discount: null });
        ui.newKeys.add(key);
        // A dish with a required pick-any group (e.g. "Choose your fillings") opens its sheet straight away
        if (line.type === "dish" && missingPicks(dishOf(line.id)?.kit, line.cust).length) ask = key;
    }
    changed();
    if (ask) openKit({ lineKey: ask });
}

function setQty(key, qty) {
    if (qty <= 0) return removeLine(key);
    const line = state.lines.find((l) => l.key === key);
    if (line) line.qty = Math.min(99, qty);
    changed();
}

function removeLine(key) {
    state.lines = state.lines.filter((l) => l.key !== key);
    if (ui.moreKey === key) ui.moreKey = null;
    changed();
}

function clearCart() {
    const keep = { nextKey: state.nextKey };
    state = { ...freshCart(), ...keep };
    ui.moreKey = null;
    ui.mode = "cart";
    ui.tendered = null;
    ui.gift = null;
    ui.splitOn = false;
    $("#customer-name").value = "";
    changed();
}

function tapItem(item) {
    if (!item.available) return;
    if (item.type === "dish") return addLine({ type: "dish", id: item.id });
    const combo = comboOf(item.id);
    if (stepSlots(combo).length) return openChoice(item, combo);
    addCombo(combo, {});
}

// Add a combo with these picks ({slot id: [dish ids]}); the other choices get their defaults
function addCombo(combo, chosen) {
    const base = Object.fromEntries(combo.slots.map((slot) => [slot.id, chosen[slot.id] ?? defaultPicks(slot)]));
    const slots = Object.fromEntries(Object.entries(base).map(([id, ids]) => [id, ids.map((dish) => ({ dish }))]));
    addLine({ type: "combo", id: combo.id, slots, base });
}

// ---------- rendering: menu ----------

function sectionCount(code) {
    return state.lines.reduce((sum, line) => (itemOf(line)?.section === code ? sum + line.qty : sum), 0);
}

// Re-render without rebuilding: nodes are cached by key and only rebuilt when their signature (what they
// show) changes, and only moved when the order changes. Rebuilding everything on each change restarted the
// CSS animations and hover states, which made the screen jerk on every tap.
function patchKeyed(container, entries, { before = null } = {}) {
    const cache = container.__keyed ??= new Map();
    const wanted = entries.map(({ key, sig, build }) => {
        const cached = cache.get(key);
        if (cached && cached.sig === sig) return cached.node;
        const node = build();
        if (cached) cached.node.replaceWith(node);
        cache.set(key, { sig, node });
        return node;
    });
    const keep = new Set(entries.map((e) => e.key));
    for (const [key, { node }] of cache) {
        if (!keep.has(key)) { node.remove(); cache.delete(key); }
    }
    // Put nodes in order, touching only the ones that are out of place
    let cursor = before ? before.nextSibling : container.firstChild;
    for (const node of wanted) {
        if (node !== cursor) container.insertBefore(node, cursor);
        else cursor = cursor.nextSibling;
        cursor = node.nextSibling;
    }
}

// One-shot animations are removed once they've played, so moving the node later doesn't replay them.
function playOnce(node, className) {
    node.classList.add(className);
    node.addEventListener("animationend", () => node.classList.remove(className), { once: true });
    return node;
}

function renderTabs() {
    patchKeyed($("#tabs"), catalog.sections.map((section) => {
        const count = sectionCount(section.code);
        const selected = !ui.search && ui.section === section.code;
        return {
            key: section.code, sig: JSON.stringify([section.name, count, selected]),
            build: () => el("button", {
                className: "tab", attrs: { role: "tab", "aria-selected": String(selected) },
                on: { click: () => { ui.section = section.code; ui.search = ""; $("#search").value = ""; renderTabs(); renderGrid(); } },
            }, [section.name, count ? el("span", { className: "tab-badge", text: count }) : null]),
        };
    }));
}

// Dish cards are built once per catalog item and updated in place (a rebuilt card under the finger would
// lose its hover lift and jump).
const cards = new Map();

function dishCard(item) {
    const sig = JSON.stringify([item.title, item.price, item.available, item.ask]);
    const cached = cards.get(item.key);
    if (cached && cached.sig === sig) return cached;
    const badge = el("span", { className: "dish-qty", attrs: { hidden: true } });
    const node = el("button", {
        className: "dish", attrs: { disabled: !item.available }, on: { click: () => tapItem(item) },
    }, [
        el("span", { className: "dish-top" }, [item.ask ? el("span", { className: "dish-ask", text: item.ask, icon: "chevron" }) : null, badge]),
        el("span", { className: "dish-name", text: item.title }),
        el("span", { className: "dish-bottom" }, [
            el("span", { className: "dish-price", text: fmt(item.price) }),
            item.available ? el("span", { className: "plus", icon: "plusBig" }) : el("span", { className: "dish-out", text: t("Sold out") }),
        ]),
    ]);
    const card = { sig, node, badge, qty: 0 };
    if (cached) cached.node.replaceWith(node);
    cards.set(item.key, card);
    return card;
}

function renderGrid() {
    const query = ui.search.trim().toLowerCase();
    const items = catalog.items.filter((item) => (query ? item.title.toLowerCase().includes(query) : item.section === ui.section));
    const grid = $("#grid");
    const nodes = items.map((item) => {
        const card = dishCard(item);
        const qty = state.lines.reduce((sum, l) => (l.type === item.type && l.id === item.id ? sum + l.qty : sum), 0);
        if (qty !== card.qty) {
            card.badge.textContent = qty;
            card.badge.hidden = !qty;
            card.node.classList.toggle("in-cart", qty > 0);
            if (qty > card.qty) playOnce(card.badge, "pop");
            card.qty = qty;
        }
        return card.node;
    });
    if (!nodes.length) {
        nodes.push(el("div", { className: "grid-empty", text: query ? t("No dishes match your search.") : t("Nothing on the menu here yet.") }));
    }
    const current = [...grid.children];
    if (current.length !== nodes.length || current.some((node, i) => node !== nodes[i])) grid.replaceChildren(...nodes);
}

// ---------- rendering: cart ----------

let drag = null;
let renderWhenDropped = false;

function renderLines() {
    if (drag?.on) { renderWhenDropped = true; return; }
    patchKeyed($("#lines"), state.lines.map((line) => ({
        key: line.key, sig: lineSignature(line),
        build: () => {
            const node = lineNode(line);
            if (ui.newKeys.has(line.key)) playOnce(node, "line-in");
            return node;
        },
    })), { before: $("#empty-cart") });
    $("#empty-cart").hidden = state.lines.length > 0;
    ui.newKeys.clear();
    $("#swipe-hint").hidden = !state.lines.length;
}

// Everything a line shows; when it doesn't change, the line isn't touched.
function lineSignature(line) {
    const quoted = quotedLine(line);
    return JSON.stringify([titleOf(line), line.qty, unitPrice(line), quoted?.discount, line.discount?.short,
                           lineSummary(line), lineAskedPick(line), ui.moreKey === line.key]);
}

function lineNode(line) {
    const quoted = quotedLine(line);
    const unit = unitPrice(line);
    const gross = unit * line.qty;
    const discountAmount = quoted?.discount?.amount || 0;
    const bits = lineSummary(line);
    const asked = lineAskedPick(line);
    const open = ui.moreKey === line.key;
    const short = quoted?.discount?.short || line.discount?.short;
    const toggle = () => { ui.moreKey = ui.moreKey === line.key ? null : line.key; renderLines(); };

    const info = el("div", {
        className: "line-info", attrs: { role: "button", tabindex: "0", "data-tap": "1", "aria-expanded": String(open),
                                         "aria-label": interpolate(t("More options for %s"), [titleOf(line)]) },
        on: { keydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } } },
    }, [
        el("div", { className: "line-name", text: titleOf(line) }),
        el("div", { className: "line-sub", text: [asked, fmt(unit), short].filter(Boolean).join(" · ") }),
        bits.length ? el("div", { className: "line-cust", text: bits.join(" · ") }) : null,
    ]);
    const row = el("div", { className: "line-row" }, [
        info,
        el("div", { className: "qty" }, [
            el("button", { attrs: { "aria-label": t("Remove one") }, icon: "minus", on: { click: () => setQty(line.key, line.qty - 1) } }),
            el("span", { text: line.qty }),
            el("button", { className: "inc", attrs: { "aria-label": t("Add one") }, icon: "plus", on: { click: () => setQty(line.key, line.qty + 1) } }),
        ]),
        el("div", { className: "line-price" }, [
            el("div", { className: "line-total", text: fmt(gross - discountAmount) }),
            discountAmount ? el("div", { className: "line-gross", text: fmt(gross) }) : null,
        ]),
    ]);
    const background = el("div", { className: "line-bg", attrs: { "aria-hidden": "true" } }, [
        el("span", { className: "bg-remove", icon: "trash" }, [el("span", { className: "js-text", text: t("Remove") })]),
        el("span", { className: "bg-more" }, [el("span", { className: "js-text", text: t("More") }), el("span", { icon: "dots" })]),
    ]);
    attachSwipe(row, background, line.key, toggle);

    const isCombo = line.type === "combo";
    const tray = open ? el("div", { className: "tray" }, [el("div", { className: "tray-row" }, [
        el("button", { className: "tray-btn", on: { click: () => { ui.moreKey = null; openDiscount(line.key); } } }, [
            el("span", { className: "tray-icon", icon: "discount" }),
            el("span", { className: "tray-text" }, [
                el("span", { className: "tray-title", text: t("Discount") }),
                el("span", { className: "tray-sub", text: short || t("Offers & codes") }),
            ]),
        ]),
        el("button", { className: "tray-btn", on: { click: () => { ui.moreKey = null; isCombo ? openCombo(line.key) : openKit({ lineKey: line.key }); } } }, [
            el("span", { className: "tray-icon", icon: "sliders" }),
            el("span", { className: "tray-text" }, [
                el("span", { className: "tray-title", text: t("Customize") }),
                el("span", { className: "tray-sub", text: bits.length
                    ? interpolate(ngettext("Edited · %s change", "Edited · %s changes", bits.length), [bits.length])
                    : (isCombo ? t("Swap & customize") : t("Parts & add-ons")) }),
            ]),
        ]),
    ])]) : null;

    return el("div", { className: "line" }, [
        el("div", { className: "line-swipe" }, [background, row]), tray,
    ]);
}

// Swipe right to remove, swipe left (or tap) for the discount/customize tray.
function attachSwipe(row, background, key, toggle) {
    row.addEventListener("pointerdown", (e) => {
        if (e.target.closest("button")) return;
        drag = { key, x: e.clientX, y: e.clientY, dx: 0, on: false, tap: !!e.target.closest("[data-tap]") };
        try { row.setPointerCapture(e.pointerId); } catch { /* not supported */ }
    });
    row.addEventListener("pointermove", (e) => {
        if (!drag || drag.key !== key) return;
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (!drag.on) {
            if (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) drag.on = true;
            else return;
        }
        drag.dx = Math.max(-170, Math.min(170, dx));
        row.classList.add("dragging");
        row.style.transform = `translateX(${drag.dx}px)`;
        background.className = "line-bg" + (drag.dx > 0 ? " remove" : drag.dx < 0 ? " more" : "");
        background.querySelector(".bg-remove .js-text").textContent = drag.dx > 90 ? t("Release to remove") : t("Remove");
        background.querySelector(".bg-more .js-text").textContent = drag.dx < -90 ? t("Release for more") : t("More");
    });
    const finish = (cancelled) => {
        const d = drag;
        drag = null;
        if (!d || d.key !== key) return;
        row.classList.remove("dragging");
        if (!d.on) {
            if (d.tap && !cancelled) toggle();
            return;
        }
        if (!cancelled && d.dx > 90) {
            row.style.transform = "translateX(440px)";
            row.style.opacity = "0";
            setTimeout(() => removeLine(key), 210);
            return;
        }
        row.style.transform = "";
        background.className = "line-bg";
        if (!cancelled && d.dx < -90) { ui.moreKey = ui.moreKey === key ? null : key; renderLines(); }
        else if (renderWhenDropped) { renderWhenDropped = false; renderLines(); }
    };
    row.addEventListener("pointerup", () => finish(false));
    row.addEventListener("pointercancel", () => finish(true));
}

function renderTotals() {
    const quote = ui.quote;
    const subtotal = state.lines.reduce((sum, line) => sum + unitPrice(line) * line.qty, 0);
    $("#subtotal").textContent = fmt(quote?.subtotal ?? subtotal);
    const discount = quote?.discount || 0;
    $("#disc-block").hidden = !discount;
    $("#disc-total").textContent = "−" + fmt(discount);
    $("#disc-toggle").setAttribute("aria-expanded", String(ui.discListOpen));
    $("#disc-list").hidden = !ui.discListOpen || !discount;
    if (discount && ui.discListOpen) {
        const entries = [];
        for (const line of state.lines) {
            const d = quotedLine(line)?.discount;
            if (!d || !d.amount) continue;
            const pick = lineAskedPick(line);
            entries.push(discEntry(`${titleOf(line)}${pick ? " · " + pick : ""} × ${line.qty}`, `${d.label} · ${d.short}`, d.amount));
        }
        if (quote.orderDiscount?.amount) {
            entries.push(discEntry(t("Whole order"), `${quote.orderDiscount.label} · ${quote.orderDiscount.short}`, quote.orderDiscount.amount));
        }
        $("#disc-list").replaceChildren(...entries);
    }
    $("#tax").textContent = fmt(quote?.tax || 0);
    $("#tax-row").hidden = parseFloat(boot.taxRate) === 0;
    $("#pay-total").textContent = fmt(quote?.total ?? subtotal);
    $("#quote-error").hidden = !ui.quoteError;
    $("#quote-error").replaceChildren(...(ui.quoteError ? [el("span", { icon: "alert" }), ui.quoteError] : []));
    $("#pay-btn").disabled = !state.lines.length || !ui.quoteFresh || !!ui.quoteError || !quote;

    const noteBtn = $("#note-btn");
    noteBtn.setAttribute("aria-pressed", String(!!state.note));
    noteBtn.querySelector(".js-label").textContent = state.note ? (state.note.length > 16 ? state.note.slice(0, 15).trim() + "…" : state.note) : t("Add note");
    const discBtn = $("#order-disc-btn");
    discBtn.setAttribute("aria-pressed", String(!!state.orderDiscount));
    const orderShort = quote?.orderDiscount?.short || state.orderDiscount?.short;
    discBtn.querySelector(".js-label").textContent = state.orderDiscount ? `${t("Discount")} · ${orderShort}` : t("Discount");
}

function discEntry(title, desc, amount) {
    return el("div", { className: "disc-entry" }, [
        el("span", { className: "disc-entry-text" }, [
            el("span", { className: "disc-entry-title", text: title }),
            el("span", { className: "disc-entry-desc", text: desc }),
        ]),
        el("span", { className: "disc-entry-amt", text: "−" + fmt(amount) }),
    ]);
}

function renderCartView() {
    document.querySelectorAll(".js-order-no").forEach((node) => { node.textContent = pad(ui.orderNumber); });
    $("#service").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.togo === "1") === state.toGo)));
    renderLines();
    renderTotals();
}

function broadcast() {
    const quote = ui.quote;
    cartChannel.postMessage({
        message: "cart",
        name: state.name,
        lines: state.lines.map((line) => ({
            title: titleOf(line), qty: line.qty, mods: lineSummary(line),
            total: unitPrice(line) * line.qty - (quotedLine(line)?.discount?.amount || 0),
        })),
        subtotal: quote?.subtotal ?? 0, discount: quote?.discount ?? 0, tax: quote?.tax ?? 0, total: quote?.total ?? 0,
        paying: ui.mode === "pay",
    });
}

function renderAll() {
    renderTabs();
    renderGrid();
    $("#cart-view").hidden = ui.mode !== "cart";
    $("#pay-view").hidden = ui.mode !== "pay";
    if (ui.mode === "cart") renderCartView();
    else renderPay();
    broadcast();
}

// ---------- dialogs ----------

wireSheets();

// Choice sheet: a combo walks through each of its choices when added ("Choose a side", then "Choose a drink")
let choice = null; // {item, combo, steps, index, picks: {slot id: [dish ids]}}

function openChoice(item, combo) {
    const steps = stepSlots(combo);
    choice = { item, combo, steps, index: 0, picks: Object.fromEntries(steps.map((s) => [s.id, []])) };
    renderChoice();
    $("#choice-sheet").showModal();
}

function choiceTitle(slot) {
    const name = slot.title.toLowerCase();
    if (!slot.required) return slot.picks > 1 ? interpolate(t("Add up to %s %s?"), [slot.picks, name]) : interpolate(t("Add a %s?"), [name]);
    return slot.picks > 1 ? interpolate(t("Choose %s %s"), [slot.picks, name]) : interpolate(t("Choose a %s"), [name]);
}

function renderChoice() {
    const { combo, steps, index } = choice;
    const slot = steps[index];
    const picked = choice.picks[slot.id];
    $("#choice-over").textContent = steps.length > 1 ? interpolate(t("%s · step %s of %s"), [combo.title, index + 1, steps.length]) : combo.title;
    $("#choice-title").textContent = choiceTitle(slot);
    const set = (ids) => { choice.picks[slot.id] = ids; renderChoice(); };
    const priceOf = (option) => (combo.pricing === "percent" ? "" : option.price ? fmtSigned(option.price) : "");
    const options = slot.options.map((option) => {
        const dish = dishOf(option.dish);
        const out = !dish?.available;
        const n = picked.filter((id) => id === option.dish).length;
        if (slot.picks > 1) {
            return counterNode({
                label: dish?.title ?? "?", n, min: 0, max: out ? n : n + slot.picks - picked.length, sub: out ? t("Sold out") : priceOf(option),
                onChange: (value) => set(value > n ? [...picked, option.dish] : (() => { const ids = [...picked]; ids.splice(ids.lastIndexOf(option.dish), 1); return ids; })()),
            });
        }
        const on = n > 0;
        return el("button", {
            className: "choice", attrs: { "aria-pressed": String(on), disabled: out && !on },
            on: { click: () => set(on && !slot.required ? [] : [option.dish]) },
        }, [
            el("span", {}, [dish?.title ?? "?", priceOf(option) ? el("span", { className: "choice-sub", text: " " + priceOf(option) }) : null]),
            out ? el("span", { className: "choice-sub", text: t("Sold out") }) : null,
            on ? el("span", { className: "check pop", icon: "checkBig" }) : null,
        ]);
    });
    $("#choice-options").replaceChildren(...options);
    const ready = !slot.required || picked.length === slot.picks;
    const last = index === steps.length - 1;
    $("#choice-add").disabled = !ready;
    $("#choice-add").firstElementChild.textContent = last ? t("Add to order") : !slot.required && !picked.length ? t("Skip") : t("Next");
    const ids = Object.fromEntries(combo.slots.map((s) => [s.id, s.id in choice.picks && (choice.picks[s.id].length || !s.required) ? choice.picks[s.id] : defaultPicks(s)]));
    $("#choice-price").textContent = fmt(comboPrice(combo, ids));
}

$("#choice-add").addEventListener("click", () => {
    if (!choice) return;
    const slot = choice.steps[choice.index];
    if (slot.required && choice.picks[slot.id].length !== slot.picks) return;
    if (choice.index < choice.steps.length - 1) {
        choice.index += 1;
        renderChoice();
        return;
    }
    addCombo(choice.combo, choice.picks);
    $("#choice-sheet").close();
    choice = null;
});

// Customize sheet (one dish: standalone, or a slot inside the combo being edited)
let kit = null; // {lineKey, slotId, index, dishId, draft}

function openKit({ lineKey, slotId = null, index = 0 }) {
    const line = state.lines.find((l) => l.key === lineKey);
    if (!line) return;
    const pick = slotId === null ? null : comboDraft.slots[slotId][index];
    const dishId = slotId === null ? line.id : pick.dish;
    const draft = slotId === null ? clone(line.cust) : clone(pick.cust);
    kit = { lineKey, slotId, index, dishId, draft };
    const sub = slotId !== null;
    const combo = sub ? comboOf(line.id) : null;
    const slot = sub ? combo.slots.find((s) => String(s.id) === String(slotId)) : null;
    $("#kit-back").hidden = !sub;
    $("#kit-over").textContent = sub ? `${combo.title} · ${slot.title}` : t("Customize");
    $("#kit-title").textContent = dishOf(dishId)?.title ?? "";
    $("#kit-sub").textContent = sub ? t("Changes to this part of the combo")
        : line.qty > 1 ? interpolate(t("Applies to all %s on this line"), [line.qty]) : t("One item on this line");
    $("#kit-save").textContent = sub ? t("Done") : t("Save changes");
    $("#kit-note").value = draft.note || "";
    renderKit();
    $("#kit-sheet").showModal();
}

function kitSection(title, note) {
    return el("div", { className: "section-head" }, [
        el("span", { className: "overline", text: title }), el("span", { className: "sub", text: note || "" }),
    ]);
}

function counterNode({ label, n, min, max, struck, standard = 1, sub, onChange }) {
    return el("div", { className: "counter" + ((struck ? n > standard : n > 0) ? " hot" : "") }, [
        el("span", { className: n === 0 && struck ? "struck" : "", text: label }),
        sub ? el("span", { className: "chip-sub", text: sub }) : null,
        el("span", { className: "stepper" }, [
            el("button", { attrs: { "aria-label": interpolate(t("Fewer %s"), [label]), disabled: n <= min }, icon: "minusSm", on: { click: () => onChange(n - 1) } }),
            el("span", { text: n }),
            el("button", { attrs: { "aria-label": interpolate(t("More %s"), [label]), disabled: n >= max }, icon: "plusSm", on: { click: () => onChange(n + 1) } }),
        ]),
    ]);
}

function chipNode({ label, on, sub, warn, disabled, onClick }) {
    return el("button", {
        className: "chip" + (warn ? " warn" : ""), attrs: { "aria-pressed": String(on), disabled }, on: { click: onClick },
    }, [on ? el("span", { icon: "check" }) : null, label, sub ? el("span", { className: "chip-sub", text: sub }) : null]);
}

// The entries of a customization sheet, shared by dishes and a combo's own extras.
function kitEntries(spec, draft, update, { allergies = true } = {}) {
    const entries = [];
    if (spec.parts.length) {
        entries.push(kitSection(t("Components"), t("Set how many of each")));
        for (const part of spec.parts) {
            const n = (draft.parts || {})[part.id] ?? part.standard;
            entries.push(counterNode({
                label: part.title, n, min: 0, max: part.max, struck: true, standard: part.standard,
                sub: n > part.standard && part.extra ? "+" + fmt((n - part.standard) * part.extra) : "",
                onChange: (value) => update((d) => { d.parts = d.parts || {}; d.parts[part.id] = value; }),
            }));
        }
    }
    for (const group of visibleGroups(spec, draft)) {
        const rule = group.pick === "any" ? t("Pick any") : t("Pick one");
        entries.push(kitSection(group.title, [group.note, group.required ? t("Required") : rule].filter(Boolean).join(" · ")));
        if (group.pick === "any") {
            const counts = pickAny(group, draft);
            for (const option of group.options) {
                const n = counts[option.id] || 0;
                entries.push(counterNode({
                    label: option.title, n, min: 0, max: option.available ? group.max : n,
                    sub: !option.available ? t("Sold out") : option.price ? "+" + fmt(option.price * Math.max(n, 1)) : "",
                    onChange: (value) => update((d) => {
                        d.options = d.options || {};
                        const next = { ...pickAny(group, d) };
                        if (value > 0) next[option.id] = value; else delete next[option.id];
                        d.options[group.key] = next;
                    }),
                }));
            }
            continue;
        }
        const current = pickOne(group, draft);
        for (const option of group.options) {
            const on = current === option.id;
            entries.push(chipNode({
                label: option.title, on, disabled: !option.available && !on,
                sub: !option.available ? t("Sold out") : option.price ? fmtSigned(option.price) : "",
                // an optional group can be cleared by tapping its choice again
                onClick: () => update((d) => { d.options = d.options || {}; d.options[group.key] = on && !group.required ? null : option.id; }),
            }));
        }
    }
    if (spec.condiments.length) {
        const free = spec.condiments.every((c) => !c.price);
        entries.push(kitSection(t("On the side"), free ? t("Free") : ""));
        for (const condiment of spec.condiments) {
            const n = (draft.condiments || {})[condiment.id] || 0;
            entries.push(counterNode({
                label: condiment.title, n, min: 0, max: condiment.available ? condiment.max : n,
                sub: condiment.price ? "+" + fmt(condiment.price) : "",
                onChange: (value) => update((d) => { d.condiments = d.condiments || {}; d.condiments[condiment.id] = value; }),
            }));
        }
    }
    if (allergies) {
        entries.push(kitSection(t("Allergies"), t("Tell the kitchen")));
        for (const allergen of boot.allergens) {
            const on = (draft.allergies || []).includes(allergen);
            entries.push(chipNode({
                label: allergen, on, warn: true,
                onClick: () => update((d) => {
                    d.allergies = (d.allergies || []).filter((a) => a !== allergen);
                    if (!on) d.allergies.push(allergen);
                }),
            }));
        }
    }
    return entries;
}

function renderKit() {
    const spec = dishOf(kit.dishId)?.kit;
    if (!spec) return;
    const update = (change) => { change(kit.draft); renderKit(); };
    $("#kit-entries").replaceChildren(...kitEntries(spec, kit.draft, update));
    const cost = kitCost(spec, kit.draft) - kitCost(spec, {});
    $("#kit-adds").textContent = cost > 0 ? interpolate(t("+%s each"), [fmt(cost)]) : cost < 0 ? interpolate(t("%s each"), [fmt(cost)]) : t("No extra charge");
    $("#kit-note-count").textContent = `${(kit.draft.note || "").length} / 140`;
    $("#kit-reset").disabled = !kitSummary(spec, kit.draft).length;
    const missing = missingPicks(spec, kit.draft);
    $("#kit-save").disabled = missing.length > 0;
    $("#kit-save").title = missing.length ? interpolate(t("Pick %s first"), [missing.map((g) => g.title.toLowerCase()).join(", ")]) : "";
}

$("#kit-note").addEventListener("input", (e) => { kit.draft.note = e.target.value.slice(0, 140); renderKit(); });
$("#kit-reset").addEventListener("click", () => { kit.draft = {}; $("#kit-note").value = ""; renderKit(); });

function closeKit() {
    $("#kit-sheet").close();
    if (kit?.slotId === null) kit = null;
}
$("#kit-close").addEventListener("click", closeKit);
$("#kit-cancel").addEventListener("click", closeKit);
$("#kit-back").addEventListener("click", closeKit);

$("#kit-save").addEventListener("click", () => {
    const spec = dishOf(kit.dishId)?.kit;
    const cust = normalizeCust(spec, kit.draft);
    if (kit.slotId !== null) {
        const pick = comboDraft.slots[kit.slotId][kit.index];
        if (Object.keys(cust).length) pick.cust = cust;
        else delete pick.cust;
        $("#kit-sheet").close();
        renderCombo();
        return;
    }
    const line = state.lines.find((l) => l.key === kit.lineKey);
    if (line) line.cust = cust;
    $("#kit-sheet").close();
    kit = null;
    changed();
    toast(t("Item customized"));
});

// Combo sheet: change the picks of each choice, customize each pick, add the combo's condiments
let comboDraft = null; // {lineKey, slots: {slot id: [{dish, cust}]}, cust}

function openCombo(lineKey) {
    const line = state.lines.find((l) => l.key === lineKey);
    if (!line) return;
    const combo = comboOf(line.id);
    comboDraft = { lineKey, slots: Object.fromEntries(combo.slots.map((s) => [s.id, clone(slotPicks(line, s))])), cust: clone(line.cust) };
    renderCombo();
    $("#combo-sheet").showModal();
}

// New picks for a choice, keeping the customizations of dishes that stay
function setPicks(slotId, ids) {
    const old = [...comboDraft.slots[slotId]];
    comboDraft.slots[slotId] = ids.map((dish) => {
        const i = old.findIndex((p) => p.dish === dish);
        return i >= 0 ? old.splice(i, 1)[0] : { dish };
    });
    renderCombo();
}

function renderCombo() {
    const line = state.lines.find((l) => l.key === comboDraft.lineKey);
    const combo = comboOf(line.id);
    const draftLine = { ...line, slots: comboDraft.slots, cust: comboDraft.cust };
    const entries = [];
    let complete = true;
    for (const slot of combo.slots) {
        const picks = comboDraft.slots[slot.id];
        const ids = picks.map((p) => p.dish);
        if (slot.required && ids.length !== slot.picks) complete = false;
        const rule = slot.required ? (slot.picks > 1 ? interpolate(t("Pick %s"), [slot.picks]) : "") : t("Optional");
        entries.push(el("div", { className: "slot-head" }, [
            el("div", {}, [
                el("div", { className: "overline", text: [slot.title, rule].filter(Boolean).join(" · ") }),
                el("div", { className: "slot-name", text: ids.length ? ids.map((id) => dishOf(id)?.title).join(", ") : interpolate(t("No %s"), [slot.title.toLowerCase()]) }),
            ]),
            ...picks.map((pick, index) => {
                const subKit = dishOf(pick.dish)?.kit;
                const changes = subKit ? kitSummary(subKit, pick.cust).length : 0;
                const label = picks.length > 1 ? dishOf(pick.dish)?.title : t("Customize");
                return el("button", {
                    className: "slot-cust" + (changes ? " hot" : ""), attrs: { disabled: !subKit },
                    on: { click: () => openKit({ lineKey: line.key, slotId: slot.id, index }) },
                }, [changes ? `${label} · ${changes}` : label, el("span", { icon: "chevronMd" })]);
            }),
        ]));
        const baseUnit = comboPrice(combo, { [slot.id]: baseIds(line, slot) });
        for (const option of slot.options) {
            const dish = dishOf(option.dish);
            const out = !dish?.available;
            const n = ids.filter((id) => id === option.dish).length;
            // one pick: against the base pick; several: what each one adds
            const relative = comboPrice(combo, { [slot.id]: [option.dish] }) - (slot.picks > 1 ? comboPrice(combo, {}) : baseUnit);
            const sub = out ? t("Sold out") : relative ? fmtSigned(relative) : "";
            if (slot.picks > 1) {
                entries.push(counterNode({
                    label: dish?.title ?? "?", n, min: 0, max: out ? n : n + slot.picks - ids.length, sub,
                    onChange: (value) => {
                        const next = [...ids];
                        if (value > n) next.push(option.dish); else next.splice(next.lastIndexOf(option.dish), 1);
                        setPicks(slot.id, next);
                    },
                }));
                continue;
            }
            const on = n > 0;
            entries.push(chipNode({
                label: dish?.title ?? "?", on, disabled: out && !on, sub,
                onClick: () => setPicks(slot.id, on && !slot.required ? [] : [option.dish]),
            }));
        }
    }
    const extras = extrasKit(combo);
    if (extras.condiments.length) {
        entries.push(...kitEntries(extras, comboDraft.cust, (change) => { change(comboDraft.cust); renderCombo(); }, { allergies: false }));
    }
    $("#combo-entries").replaceChildren(...entries);
    const standard = unitPrice({ ...line, slots: Object.fromEntries(Object.entries(line.base || {}).map(([id, ids]) => [id, ids.map((dish) => ({ dish }))])), cust: {} });
    const now = unitPrice(draftLine);
    $("#combo-over").textContent = `${combo.title} · ${fmt(standard)}`;
    $("#combo-total").textContent = fmt(now);
    $("#combo-delta").textContent = now === standard ? t("As added") : interpolate(t("%s from when it was added"), [fmtSigned(now - standard)]);
    $("#combo-reset").disabled = !lineSummary(draftLine).length;
    $("#combo-save").disabled = !complete;
}

$("#combo-reset").addEventListener("click", () => {
    const line = state.lines.find((l) => l.key === comboDraft.lineKey);
    const combo = comboOf(line.id);
    comboDraft.slots = Object.fromEntries(combo.slots.map((s) => [s.id, baseIds(line, s).map((dish) => ({ dish }))]));
    comboDraft.cust = {};
    renderCombo();
});

$("#combo-save").addEventListener("click", () => {
    const line = state.lines.find((l) => l.key === comboDraft.lineKey);
    const combo = comboOf(line.id);
    line.slots = Object.fromEntries(combo.slots.map((slot) => [slot.id, comboDraft.slots[slot.id].map((pick) => {
        const cust = normalizeCust(dishOf(pick.dish)?.kit, pick.cust);
        return { dish: pick.dish, ...(Object.keys(cust).length ? { cust } : {}) };
    })]));
    line.cust = normalizeCust(extrasKit(combo), comboDraft.cust);
    $("#combo-sheet").close();
    comboDraft = null;
    changed();
    toast(t("Combo updated"));
});

// Note sheet
const noteChips = $("#note-chips").dataset.chips.split("|");
let noteDraft = "";
let noteKitchen = true;

function openNote() {
    noteDraft = state.note;
    noteKitchen = state.noteKitchen;
    $("#note-text").value = noteDraft;
    $("#note-scope").textContent = `${t("Order")} #${pad(ui.orderNumber)} · ${state.name || t("No name")}`;
    $("#note-title").textContent = state.note ? t("Edit the note") : t("Add a note");
    $("#note-remove").hidden = !state.note;
    renderNote();
    $("#note-sheet").showModal();
}

function renderNote() {
    const parts = noteDraft.split(",").map((x) => x.trim()).filter(Boolean);
    $("#note-chips").replaceChildren(...noteChips.map((label) => {
        const on = parts.includes(label);
        return chipNode({
            label, on, onClick: () => {
                const next = on ? parts.filter((x) => x !== label) : parts.concat([label]);
                noteDraft = next.join(", ").slice(0, 140);
                $("#note-text").value = noteDraft;
                renderNote();
            },
        });
    }));
    document.querySelectorAll("#note-chips .chip").forEach((chip) => chip.classList.add("sm"));
    $("#note-count").textContent = `${noteDraft.length} / 140`;
    $("#note-kitchen").setAttribute("aria-checked", String(noteKitchen));
    $("#note-save").disabled = !noteDraft.trim();
}

$("#note-btn").addEventListener("click", openNote);
$("#note-text").addEventListener("input", (e) => { noteDraft = e.target.value; renderNote(); });
$("#note-kitchen").addEventListener("click", () => { noteKitchen = !noteKitchen; renderNote(); });
$("#note-save").addEventListener("click", () => {
    if (!noteDraft.trim()) return;
    state.note = noteDraft.trim();
    state.noteKitchen = noteKitchen;
    $("#note-sheet").close();
    changed({ quote: false });
    toast(t("Note saved"));
});
$("#note-remove").addEventListener("click", () => {
    state.note = "";
    $("#note-sheet").close();
    changed({ quote: false });
});

// Discount sheet: an offer, a coupon code or (managers) a typed-in discount, for one line or the order
let dc = null; // {scope: line key or null, pick, mode, manual, couponText, couponError, coupon}

function openDiscount(lineKey) {
    dc = { scope: lineKey || null, pick: null, mode: "pct", manual: "", couponError: "", coupon: null };
    $("#dc-coupon").value = "";
    $("#dc-manual-value").value = "";
    const manual = $("#dc-manual");
    manual.classList.toggle("locked", !boot.canManual);
    $("#dc-manual-locked").hidden = boot.canManual;
    renderDiscount();
    $("#disc-sheet").showModal();
}

function dcLine() {
    return dc.scope ? state.lines.find((l) => l.key === dc.scope) : null;
}

function dcBase() {
    const line = dcLine();
    if (line) return unitPrice(line) * line.qty;
    const subtotal = ui.quote?.subtotal ?? state.lines.reduce((sum, l) => sum + unitPrice(l) * l.qty, 0);
    const lineDiscounts = state.lines.reduce((sum, l) => sum + (quotedLine(l)?.discount?.amount || 0), 0);
    return subtotal - lineDiscounts;
}

function dcDraft() {
    const pick = dc.pick;
    if (!pick) return null;
    if (pick.src === "offer") {
        const offer = boot.offers.find((o) => o.id === pick.id);
        return { spec: { offer: offer.id }, kind: offer.kind, value: offer.value, label: offer.name, short: shortOf(offer.kind, offer.value) };
    }
    if (pick.src === "coupon") return { spec: { code: dc.coupon.code }, label: dc.coupon.code, short: dc.coupon.short, amount: dc.coupon.amount };
    const value = parseFloat(dc.manual);
    if (!(value > 0)) return null;
    if (dc.mode === "pct") {
        const pct = Math.min(100, value);
        return { spec: { manual: { kind: "pct", value: String(pct) } }, kind: "pct", value: pct, label: t("Manual"), short: shortOf("pct", pct) };
    }
    const cents = Math.round(value * 100);
    return { spec: { manual: { kind: "amt", value: dollars(cents) } }, kind: "amt", value: cents, label: t("Manual"), short: shortOf("amt", cents) };
}

function draftSavings(draft, base) {
    if (!draft) return 0;
    if (draft.amount !== undefined) return draft.amount;
    return draft.kind === "pct" ? Math.round(base * Number(draft.value) / 100) : Math.min(draft.value, base);
}

function renderDiscount() {
    const line = dcLine();
    const base = dcBase();
    $("#dc-title").textContent = line ? t("Discount this item") : t("Discount the order");
    $("#dc-scope").textContent = line ? `${titleOf(line)} × ${line.qty} · ${fmt(base)}` : `${t("Whole order")} · ${fmt(base)}`;

    const scope = line ? "item" : "order";
    const offers = boot.offers.filter((o) => o.scope === "any" || o.scope === scope);
    $("#dc-offers").replaceChildren(...(offers.length ? offers.map((offer) => {
        const on = dc.pick?.src === "offer" && dc.pick.id === offer.id;
        return el("button", {
            className: "offer", attrs: { "aria-pressed": String(on) },
            on: { click: () => { dc.pick = { src: "offer", id: offer.id }; renderDiscount(); } },
        }, [
            el("span", { className: "offer-text" }, [
                el("span", { className: "offer-name", text: offer.name }),
                offer.note ? el("span", { className: "offer-note", text: offer.note }) : null,
            ]),
            el("span", { className: "offer-badge", text: shortOf(offer.kind, offer.value) }),
        ]);
    }) : [el("div", { className: "offers-empty", text: t("No offers right now.") })]));

    $("#dc-coupon-err").hidden = !dc.couponError;
    $("#dc-coupon-err").replaceChildren(...(dc.couponError ? [el("span", { icon: "alert" }), dc.couponError] : []));
    const couponOk = dc.pick?.src === "coupon";
    $("#dc-coupon-ok").hidden = !couponOk;
    $("#dc-coupon-ok").replaceChildren(...(couponOk ? [el("span", { icon: "checkBig" }), interpolate(t("%s applied · %s"), [dc.coupon.code, dc.coupon.short])] : []));

    $("#dc-mode").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === dc.mode)));
    $("#dc-prefix").textContent = dc.mode === "pct" ? "%" : "$";
    const manualOn = dc.pick?.src === "manual";
    $("#dc-manual-wrap").classList.toggle("on", manualOn);
    const quick = dc.mode === "pct" ? [5, 10, 15, 20] : [1, 2, 5, 10];
    $("#dc-quick").replaceChildren(...quick.map((value) => el("button", {
        text: dc.mode === "pct" ? `${value}%` : `$${value}`,
        attrs: { "aria-pressed": String(manualOn && parseFloat(dc.manual) === value) },
        on: { click: () => { dc.manual = String(value); $("#dc-manual-value").value = dc.manual; dc.pick = { src: "manual" }; renderDiscount(); } },
    })));

    const draft = dcDraft();
    const existing = line ? line.discount : state.orderDiscount;
    $("#dc-saves").hidden = !draft;
    $("#dc-saves-spacer").hidden = !!draft;
    $("#dc-saves-amount").textContent = fmt(draftSavings(draft, base));
    $("#dc-apply").disabled = !draft;
    $("#dc-remove").hidden = !existing;
}

$("#dc-mode").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    dc.mode = button.dataset.mode;
    renderDiscount();
});
$("#dc-manual-value").addEventListener("input", (e) => {
    dc.manual = e.target.value;
    dc.pick = dc.manual.trim() ? { src: "manual" } : (dc.pick?.src === "manual" ? null : dc.pick);
    renderDiscount();
});
$("#dc-coupon").addEventListener("input", () => { dc.couponError = ""; renderDiscount(); });
$("#dc-coupon").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); applyCoupon(); } });
$("#dc-apply-coupon").addEventListener("click", applyCoupon);

// Ask the server whether the code works on this line/order right now, and what it saves
async function applyCoupon() {
    const code = $("#dc-coupon").value.trim().toUpperCase();
    if (!code) return;
    const spec = { code };
    const body = cartBody();
    if (dc.scope) body.lines.find((l) => l.key === dc.scope).discount = spec;
    else body.orderDiscount = spec;
    const result = await post(urls.quote, body).catch(() => ({ ok: false, data: {} }));
    const quoted = dc.scope ? result.data.lines?.[body.lines.findIndex((l) => l.key === dc.scope)] : null;
    const error = !result.ok ? (result.data.message || t("That code isn't valid."))
        : dc.scope ? quoted?.discountError : result.data.orderDiscountError;
    if (error) {
        dc.couponError = error;
        if (dc.pick?.src === "coupon") dc.pick = null;
    } else {
        const applied = dc.scope ? quoted.discount : result.data.orderDiscount;
        dc.coupon = { code, short: applied.short, amount: applied.amount };
        dc.pick = { src: "coupon" };
        dc.couponError = "";
    }
    renderDiscount();
}

$("#dc-apply").addEventListener("click", () => {
    const draft = dcDraft();
    if (!draft) return;
    const applied = { spec: draft.spec, label: draft.label, short: draft.short };
    const line = dcLine();
    if (line) line.discount = applied;
    else state.orderDiscount = applied;
    $("#disc-sheet").close();
    changed();
});
$("#dc-remove").addEventListener("click", () => {
    const line = dcLine();
    if (line) line.discount = null;
    else state.orderDiscount = null;
    $("#disc-sheet").close();
    changed();
});
$("#order-disc-btn").addEventListener("click", () => openDiscount(null));

// ---------- pay ----------

const paid = () => state.payments.reduce((sum, p) => sum + p.amount, 0);
const remaining = () => Math.max(0, (ui.quote?.total || 0) - paid());

function dueNow() {
    const left = remaining();
    if (!ui.splitOn) return left;
    const split = toCents($("#split-amount").value);
    return split > 0 ? Math.min(split, left) : left;
}

function renderPay() {
    document.querySelectorAll(".js-order-no").forEach((node) => { node.textContent = pad(ui.orderNumber); });
    const quote = ui.quote;
    $("#pay-type").textContent = state.toGo ? t("To go") : t("For here");
    $("#pay-name").textContent = state.name || t("No name");
    const left = remaining();
    $("#due-label").textContent = state.payments.length ? t("Left to pay") : t("Amount due");
    $("#due-amount").textContent = fmt(left);
    const count = interpolate(ngettext("%s item", "%s items", quote?.count || 0), [quote?.count || 0]);
    $("#due-sub").textContent = quote?.tax ? `${count} · ${interpolate(t("includes %s tax"), [fmt(quote.tax)])}` : count;

    $("#paid-list").hidden = !state.payments.length;
    $("#paid-list").replaceChildren(...state.payments.map((payment, index) => el("div", { className: "paid" }, [
        el("span", { text: payment.label + " · " + fmt(payment.amount) }),
        payment.method === "card" ? null : el("button", {
            attrs: { "aria-label": t("Remove this payment") }, icon: "close",
            on: { click: () => { state.payments.splice(index, 1); save(); renderPay(); } },
        }),
    ])));

    $("#methods").querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.method === ui.method));
        b.disabled = ui.card !== "idle";
    });
    $("#split-row").hidden = !ui.splitOn;
    $("#split-btn").textContent = ui.splitOn ? t("Pay the full amount") : t("Split payment");
    $("#split-btn").disabled = ui.card !== "idle";

    const now = dueNow();
    const cash = ui.method === "cash";
    const blocked = cash && !boot.drawerOpen;
    $("#cash-blocked").hidden = !blocked;
    $("#cash-ready").hidden = !(cash && !blocked);
    $("#card-panel").hidden = ui.method !== "card";
    $("#gift-panel").hidden = ui.method !== "gift";

    let label = "";
    let disabled = ui.submitting || now <= 0;
    if (cash) {
        const tendered = ui.tendered !== null && ui.tendered >= now ? ui.tendered : now;
        // Exact, then the next round amounts a customer is likely to hand over
        const roundUp = (step) => Math.ceil(now / step) * step;
        const notes = [...new Set([roundUp(500), roundUp(1000), roundUp(2000), roundUp(5000), roundUp(10000)])].filter((v) => v > now).slice(0, 4);
        const options = [{ value: now, label: t("Exact") }].concat(notes.map((v) => ({ value: v, label: fmt(v).replace(".00", "") })));
        const custom = el("input", {
            className: ui.tendered !== null && !options.some((o) => o.value === ui.tendered) ? "on" : "",
            attrs: { inputmode: "decimal", placeholder: t("Other"), "aria-label": t("Other amount"), autocomplete: "off" },
        });
        if (ui.tendered !== null && !options.some((o) => o.value === ui.tendered)) custom.value = dollars(ui.tendered);
        custom.addEventListener("change", () => { const value = toCents(custom.value); ui.tendered = value || null; renderPay(); });
        $("#quick-cash").replaceChildren(...options.map((option) => el("button", {
            text: option.label, attrs: { "aria-pressed": String(tendered === option.value) },
            on: { click: () => { ui.tendered = option.value; renderPay(); } },
        })), custom);
        $("#change-due").textContent = fmt(Math.max(0, tendered - now));
        label = blocked ? t("Open the drawer to take cash")
            : now < left ? interpolate(t("Take %s cash"), [fmt(now)]) : t("Complete sale");
        disabled = disabled || blocked;
    } else if (ui.method === "card") {
        const waiting = ui.card !== "idle";
        $("#card-panel").classList.toggle("waiting", waiting);
        $("#card-title").textContent = waiting ? t("Waiting for card") : t("Card on the terminal");
        $("#card-text").textContent = waiting ? t("The terminal is ready. Tap, insert or swipe.")
            : interpolate(t("Charge %s on the terminal, then the customer taps, inserts or swipes."), [fmt(now)]);
        $("#card-cancel").hidden = ui.card !== "waiting";
        label = waiting ? t("Waiting for terminal…") : interpolate(t("Charge %s"), [fmt(now)]);
        disabled = disabled || waiting;
    } else {
        const card = ui.gift;
        $("#gift-found").hidden = !card;
        if (card) {
            $("#gift-found").replaceChildren(
                el("span", { text: interpolate(t("Card x%s"), [card.number.slice(-4)]) }),
                el("span", { className: "money", text: fmt(card.balance) }));
        }
        label = card ? interpolate(t("Use %s from the gift card"), [fmt(Math.min(card.balance, now))]) : t("Look up the card");
        disabled = disabled || (card ? card.balance <= 0 : !$("#gift-number").value.trim());
    }
    $("#pay-cta").textContent = ui.submitting ? t("Sending…") : label;
    $("#pay-cta").disabled = disabled;
}

$("#pay-btn").addEventListener("click", () => {
    if ($("#pay-btn").disabled) return;
    ui.mode = "pay";
    ui.tendered = null;
    renderAll();
});

$("#back-to-cart").addEventListener("click", () => {
    if (state.payments.some((p) => p.method === "card")) {
        toast(t("A card payment was taken. Finish this order first."), { error: true });
        return;
    }
    if (ui.card !== "idle") return;
    state.payments = [];
    ui.mode = "cart";
    ui.splitOn = false;
    save();
    renderAll();
});

$("#methods").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button || button.disabled) return;
    ui.method = button.dataset.method;
    renderPay();
});

$("#split-btn").addEventListener("click", () => {
    ui.splitOn = !ui.splitOn;
    $("#split-amount").value = ui.splitOn ? dollars(Math.round(remaining() / 2)) : "";
    renderPay();
});
$("#split-amount").addEventListener("input", renderPay);

$("#pay-cta").addEventListener("click", async () => {
    if ($("#pay-cta").disabled) return;
    const now = dueNow();
    if (ui.method === "cash") {
        const tendered = ui.tendered !== null && ui.tendered >= now ? ui.tendered : now;
        addPayment({ method: "cash", amount: now, tendered, label: t("Cash") });
        if (tendered > now) toast(interpolate(t("Give %s change"), [fmt(tendered - now)]));
    } else if (ui.method === "card") {
        await chargeCard(now);
    } else if (ui.gift) {
        const amount = Math.min(ui.gift.balance, now);
        addPayment({ method: "gift", number: ui.gift.number, amount, label: interpolate(t("Gift card x%s"), [ui.gift.number.slice(-4)]) });
        ui.gift = null;
        $("#gift-number").value = "";
    } else {
        await lookupGiftCard($("#gift-number").value);
    }
});

function addPayment(payment) {
    state.payments.push(payment);
    ui.tendered = null;
    ui.splitOn = false;
    $("#split-amount").value = "";
    save();
    if (remaining() <= 0) submitOrder();
    else {
        renderPay();
        toast(interpolate(t("%s left to pay"), [fmt(remaining())]));
    }
}

async function submitOrder() {
    ui.submitting = true;
    renderPay();
    const body = cartBody({
        payments: state.payments.map((p) => ({
            method: p.method, amount: dollars(p.amount),
            ...(p.tendered !== undefined ? { tendered: dollars(p.tendered) } : {}),
            ...(p.number ? { number: p.number } : {}),
            ...(p.checkoutId ? { checkoutId: p.checkoutId } : {}),
        })),
        total: dollars(ui.quote.total),
        name: state.name, toGo: state.toGo, note: state.note, noteForKitchen: state.noteKitchen,
    });
    const result = await post(urls.register, body).catch(() => ({ ok: false, data: { message: t("The register is offline. Check the connection.") } }));
    ui.submitting = false;
    if (!result.ok) {
        const card = state.payments.filter((p) => p.method === "card").reduce((sum, p) => sum + p.amount, 0);
        state.payments = state.payments.filter((p) => p.method === "card"); // cash and gift cards weren't taken yet
        save();
        renderPay();
        const message = result.data.message || t("The order was not sent. Please try again.");
        toast(card ? interpolate(t("%s The card was charged %s: fix the order and try again, or refund it on the terminal."), [message, fmt(card)]) : message, { error: true });
        return;
    }
    const number = pad(result.data.orderId);
    bumpStats({ orders: 1, sales: ui.quote.total });
    const name = state.name;
    ui.orderNumber = result.data.nextNumber;
    cartChannel.postMessage({ message: "paid", name });
    clearCart();
    toast(interpolate(t("Order #%s paid"), [number]));
}

// Card: start a Terminal checkout, then wait for Square's webhook to report the result
let stopCardWatch = null;

async function chargeCard(amount) {
    ui.card = "starting";
    renderPay();
    const result = await post(urls.card, cartBody({
        amount: dollars(amount), total: dollars(ui.quote.total),
        payments: state.payments.map((p) => ({ method: p.method, amount: dollars(p.amount), ...(p.number ? { number: p.number } : {}) })),
    })).catch(() => ({ ok: false, data: {} }));
    if (!result.ok) {
        ui.card = "idle";
        renderPay();
        toast(result.data.message || t("The terminal didn't start the payment."), { error: true });
        return;
    }
    ui.card = "waiting";
    renderPay();
    let finished = false;
    const check = async () => {
        if (finished) return;
        const response = await fetch(urls.cardStatus).catch(() => null);
        const status = response && response.ok ? (await response.json()).status : null;
        if (status === "COMPLETED" || status === "CANCELED" || status === "CANCEL_REQUESTED") {
            finished = true;
            stopCardWatch?.();
            await fetch(urls.cardStatus, { method: "DELETE", headers: { "X-CSRFToken": csrftoken } }).catch(() => {});
            ui.card = "idle";
            if (status === "COMPLETED") addPayment({ method: "card", amount, label: t("Card"), checkoutId: result.data.checkoutId });
            else { renderPay(); toast(t("The card payment was canceled."), { error: true }); }
        }
    };
    // Square's webhook updates the status and the server sends a "checkout" event
    stopCardWatch = onLiveUpdate("checkout", check);
    $("#card-cancel").onclick = () => {
        finished = true;
        stopCardWatch?.();
        ui.card = "idle";
        renderPay();
    };
}

// Gift cards: typed (or a keyboard-style barcode scanner), or scanned with the camera
async function lookupGiftCard(number) {
    number = String(number || "").replace(/\D/g, "");
    const error = $("#gift-error");
    error.hidden = true;
    if (!number) return;
    const response = await fetch(urls.giftCard + encodeURIComponent(number)).catch(() => null);
    if (!response || !response.ok) {
        error.replaceChildren(el("span", { icon: "alert" }),
            response?.status === 404 ? interpolate(t("Card #%s wasn't found."), [number]) : t("The card couldn't be looked up."));
        error.hidden = false;
        ui.gift = null;
        renderPay();
        return;
    }
    const data = await response.json();
    // Count what this order already put on the same card
    const used = state.payments.filter((p) => p.number === data.number).reduce((sum, p) => sum + p.amount, 0);
    ui.gift = { number: data.number, balance: Math.max(0, toCents(data.available_balance) - used) };
    renderPay();
}

$("#gift-number").addEventListener("input", (e) => {
    ui.gift = null;
    $("#gift-error").hidden = true;
    if (e.target.value.replace(/\D/g, "").length === 16) lookupGiftCard(e.target.value);
    else renderPay();
});
$("#gift-number").addEventListener("keydown", (e) => { if (e.key === "Enter") lookupGiftCard(e.target.value); });

let quaggaLoaded = null;
function loadQuagga() {
    quaggaLoaded ??= new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/quagga@0.12.1/dist/quagga.min.js";
        script.onload = resolve;
        script.onerror = reject;
        document.head.append(script);
    });
    return quaggaLoaded;
}

async function startScanner(deviceId) {
    try { Quagga.stop(); } catch { /* not running */ }
    Quagga.init({
        inputStream: { type: "LiveStream", target: $("#scanner"), constraints: { deviceId, facingMode: "environment" } },
        decoder: { readers: ["code_128_reader"] },
        locate: true,
    }, (err) => { if (!err) Quagga.start(); });
}

$("#scan-btn").addEventListener("click", async () => {
    $("#scan-sheet").showModal();
    try {
        await loadQuagga();
        const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
        const select = $("#camera-select");
        select.replaceChildren(...devices.map((d, i) => el("option", { text: d.label || interpolate(t("Camera %s"), [i + 1]), attrs: { value: d.deviceId } })));
        select.onchange = () => startScanner(select.value);
        Quagga.offDetected();
        Quagga.onDetected((result) => {
            const code = result?.codeResult?.code;
            if (!code) return;
            Quagga.stop();
            $("#scan-sheet").close();
            $("#gift-number").value = code;
            lookupGiftCard(code);
        });
        startScanner(devices[0]?.deviceId);
    } catch {
        $("#scan-sheet").close();
        toast(t("The camera couldn't be started. Type the number instead."), { error: true });
    }
});
$("#scan-sheet").addEventListener("close", () => { try { Quagga.stop(); } catch { /* not running */ } });

// ---------- other controls ----------

$("#search").addEventListener("input", (e) => { ui.search = e.target.value; renderTabs(); renderGrid(); });
$("#customer-name").value = state.name;
$("#customer-name").addEventListener("input", (e) => { state.name = e.target.value; save(); broadcast(); });
$("#service").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    state.toGo = button.dataset.togo === "1";
    changed({ quote: false });
});
$("#clear-cart").addEventListener("click", clearCart);
$("#disc-toggle").addEventListener("click", () => { ui.discListOpen = !ui.discListOpen; renderTotals(); });
$("#menu-switch").addEventListener("click", () => $("#menu-sheet").showModal());

// Sold-out changes arrive live; the catalog is fetched again and the grid redrawn
onLiveUpdate("inventory", async () => {
    const response = await fetch(urls.catalog).catch(() => null);
    if (!response || !response.ok) return;
    const fresh = await response.json();
    if (JSON.stringify(fresh) === JSON.stringify(catalog)) return;
    catalog = fresh;
    if (!catalog.sections.some((s) => s.code === ui.section)) ui.section = catalog.sections[0]?.code;
    renderAll();
    requestQuote();
});

// The customer display asks for the cart when it opens
cartChannel.addEventListener("message", (e) => { if (e.data?.message === "hello") broadcast(); });

renderAll();
requestQuote();
