// The Availability panel (docs/mockups/Availability-html, inventory/availability_panel.html). Every dish and
// component is Off (stop selling now), Auto (follow inventory) or Always (keep selling past zero).
//
// mountAvailability(root, {keyTarget}) wires one panel. Keys go to `keyTarget` (the window on the Availability
// page, the <dialog> when the panel is a popup): arrows move between cards, Enter cycles the selected card's
// mode, Tab / Shift+Tab switch tabs, typing searches, Ctrl+Z undoes, Esc clears the search (then closes a dialog).
// Data comes from `availability_data` and is refetched on every SSE "inventory" event while the panel is open.

import { onLiveUpdate } from "/static/js/live.js";
import { el, post } from "../pos_server/staff-ui.js";

const t = (text) => gettext(text);
const fmtText = (text, ...args) => interpolate(gettext(text), args);
const MODES = ["off", "auto", "always"];
const TABS = [["dishes", () => t("Dishes")], ["parts", () => t("Components & add-ons")], ["condiments", () => t("Condiments")]];
const P = {
    off: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M5.6 5.6l12.8 12.8",
    auto: "M4 12a8 8 0 0 1 14-5.3 M20 4v4h-4 M20 12a8 8 0 0 1-14 5.3 M4 20v-4h4",
    always: "M12 12c-2-2.5-3.6-4-5.5-4a4 4 0 0 0 0 8c1.9 0 3.5-1.5 5.5-4zm0 0c2 2.5 3.6 4 5.5 4a4 4 0 0 0 0-8c-1.9 0-3.5 1.5-5.5 4z",
    alert: "M12 3.5 2.5 20h19z M12 10v4.5 M12 17.4v.1",
};
const MODE_TEXT = {
    off: [() => t("Off"), () => t("Stop selling now, whatever the count says")],
    auto: [() => t("Auto"), () => t("Follow inventory: sells until the count hits zero")],
    always: [() => t("Always"), () => t("Keep selling even at zero; inventory goes negative")],
};

function icon(d, size = 18, width = 2.2) {
    const template = document.createElement("template");
    template.innerHTML = `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"></path></svg>`;
    return template.content.firstChild;
}

// What the card says about an item, given its mode and stock (see the mockup's legend)
function status(item) {
    if (item.mode === "off") return { tone: "off", text: t("Off"), sub: t("Hidden on the register") };
    if (item.blocked_by.length) {
        return { tone: "off", text: t("Unavailable"), sub: interpolate(ngettext("%s is off", "%s are off", item.blocked_by.length), [item.blocked_by.join(", ")]) };
    }
    if (item.stock === null) return { tone: "ok", text: t("Selling"), sub: item.mode === "always" ? t("Always on · not counted") : t("Not counted") };
    if (item.mode === "always") {
        return item.stock <= 0 ? { tone: "neg", text: t("Selling past zero"), sub: t("Inventory is going negative") }
                               : { tone: "ok", text: t("Selling"), sub: t("Keeps selling past zero") };
    }
    if (item.stock <= 0) return { tone: "out", text: t("Sold out"), sub: t("Back on when restocked") };
    if (item.low) return { tone: "low", text: t("Running low"), sub: t("Turns off at zero") };
    return { tone: "ok", text: t("Selling"), sub: t("Turns off at zero") };
}

const stockText = (stock) => stock === null ? "—" : stock < 0 ? "−" + Math.abs(stock) : String(stock);

export function mountAvailability(root, { keyTarget = window } = {}) {
    const $ = (selector) => root.querySelector(selector);
    const url = root.dataset.url;
    const dialog = root.closest("dialog");
    const state = { data: null, tab: "dishes", query: "", onlyOverrides: false, selected: null, flash: null, undo: null };

    const visible = () => !dialog || dialog.open;
    const allItems = (tab) => (state.data?.tabs[tab] ?? []).flatMap((group) => group.items);
    const overrides = (tab) => allItems(tab).filter((item) => item.mode !== "auto").length;
    const findItem = (key) => TABS.flatMap(([tab]) => allItems(tab)).find((item) => item.key === key);

    // ---------- server ----------

    async function refresh() {
        try {
            const response = await fetch(url, { headers: { Accept: "application/json" } });
            if (response.ok) {
                state.data = await response.json();
                render();
            }
        } catch { /* offline: the live stream refreshes on reconnect */ }
    }

    // changes: [{item, mode}]. Shown right away, then saved; `undo` is what puts it back.
    async function setModes(changes, message, { undoable = true } = {}) {
        changes = changes.filter(({ item, mode }) => item.mode !== mode);
        if (!changes.length) return;
        const before = changes.map(({ item }) => ({ item, mode: item.mode }));
        changes.forEach(({ item, mode }) => { item.mode = mode; });
        state.flash = changes.length === 1 ? changes[0].item.key : null;
        render();
        toast(message, undoable ? before : null);
        const { ok, data } = await post(url, { changes: changes.map(({ item, mode }) => ({ kind: item.kind, id: item.id, mode })) });
        if (ok) {
            state.data = data;
            render();
        } else {
            toast(data.error || t("That didn't save. Try again."), null, true);
            refresh();
        }
    }

    function pick(item, mode) {
        let message;
        if (mode === "off") {
            message = fmtText("%s is off", item.name);
            if (item.used_in) message += " · " + interpolate(ngettext("%s dish unavailable", "%s dishes unavailable", item.used_in), [item.used_in]);
        } else if (mode === "always") {
            message = fmtText("%s will keep selling past zero", item.name);
        } else {
            message = fmtText("%s follows inventory again", item.name);
        }
        state.selected = item.key;
        setModes([{ item, mode }], { text: message, mode });
    }

    function cycle(item) {
        pick(item, MODES[(MODES.indexOf(item.mode) + 1) % MODES.length]);
    }

    function resetTab() {
        const items = allItems(state.tab).filter((item) => item.mode !== "auto");
        if (!items.length) return;
        setModes(items.map((item) => ({ item, mode: "auto" })),
            { text: interpolate(ngettext("%s item back on Auto", "%s items back on Auto", items.length), [items.length]), mode: "auto" });
    }

    function undo() {
        if (!state.undo) return;
        const before = state.undo;
        state.undo = null;
        setModes(before.map(({ item, mode }) => ({ item: findItem(item.key) ?? item, mode })), { text: t("Undone"), mode: "auto" }, { undoable: false });
    }

    // ---------- toast ----------

    let toastTimer;
    function toast(message, before, error = false) {
        state.undo = before;
        const color = error || message.mode === "off" ? "#FF9A85" : "#F4A93B";
        const mark = icon(error ? P.alert : P[message.mode] ?? P.auto, 18, 2.6);
        mark.setAttribute("stroke", color);
        const text = typeof message === "string" ? message : message.text;
        $("[data-avail-toast]").replaceChildren(el("div", { className: "avail-toast", attrs: { role: error ? "alert" : "status" } }, [
            mark, el("span", { className: "strong", text }),
            before ? el("button", { className: "avail-undo", on: { click: undo } }, [t("Undo"), el("kbd", { className: "kbd on-dark", text: "Ctrl Z" })]) : null,
        ]));
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            $("[data-avail-toast]").replaceChildren();
            state.undo = null;
        }, 4500);
    }

    // ---------- rendering ----------

    function card(item) {
        const st = status(item);
        const selected = item.key === state.selected;
        const options = MODES.map((mode) => {
            const [label, title] = MODE_TEXT[mode];
            return el("button", {
                className: "mode-btn " + mode,
                attrs: { role: "radio", "aria-checked": String(item.mode === mode), "aria-label": `${label()}: ${title()}`, title: title(), tabindex: "-1" },
                on: { click: (e) => { e.stopPropagation(); pick(item, mode); } },
            }, [icon(P[mode]), el("span", { text: label() })]);
        });
        const node = el("div", {
            className: `avail-card mode-${item.mode} tone-${st.tone}` + (selected ? " selected" : "") + (state.flash === item.key ? " flash" : ""),
            attrs: { "data-key": item.key, title: item.sub },
            on: { click: () => select(item.key) },
        }, [
            el("div", { className: "card-top" }, [
                el("span", { className: "card-status" }, [el("span", { className: "status-dot" }), el("span", { className: "status-text", text: st.text })]),
                el("span", { className: "card-stock" }, [el("span", { className: "stock-num", text: stockText(item.stock) }), el("span", { className: "stock-unit", text: item.unit })]),
            ]),
            el("div", { className: "card-mid" }, [el("div", { className: "card-name", text: item.name }), el("div", { className: "card-sub", text: st.sub })]),
            el("div", { className: "mode-track", attrs: { role: "radiogroup", "aria-label": fmtText("Availability for %s", item.name) } }, [
                ...options, selected ? el("kbd", { className: "kbd mode-kbd", text: "Enter" }) : null,
            ]),
        ]);
        return node;
    }

    function render() {
        if (!state.data) return;
        const q = state.query.trim().toLowerCase();
        const tabLabel = TABS.find(([id]) => id === state.tab)[1]();

        $("[data-override-count]").textContent = TABS.reduce((sum, [tab]) => sum + overrides(tab), 0);
        $("[data-overrides]").setAttribute("aria-pressed", String(state.onlyOverrides));
        $("[data-tabs]").replaceChildren(...TABS.map(([id, label]) => {
            const n = overrides(id);
            return el("button", {
                className: "avail-tab", attrs: { role: "tab", "aria-selected": String(id === state.tab), tabindex: "-1" },
                on: { click: () => setTab(id) },
            }, [label(), n ? el("span", { className: "tab-badge", text: n }) : null]);
        }));

        const tabItems = allItems(state.tab);
        const off = tabItems.filter((item) => item.mode === "off").length;
        const always = tabItems.filter((item) => item.mode === "always").length;
        const blocked = tabItems.filter((item) => item.mode !== "off" && item.blocked_by.length).length;
        const bits = [];
        if (off) bits.push(fmtText("%s turned off", off));
        if (always) bits.push(fmtText("%s always on", always));
        if (blocked) bits.push(fmtText("%s unavailable because a component is off", blocked));
        $("[data-summary]").textContent = bits.length ? bits.join(" · ") + ". " + t("Everything else follows inventory.") : t("Everything here follows inventory.");
        $("[data-reset]").disabled = !overrides(state.tab);
        $("[data-reset-label]").textContent = fmtText("Set all %s to Auto", tabLabel.toLowerCase());
        $("[data-section]").setAttribute("aria-label", tabLabel);

        const groups = (state.data.tabs[state.tab] ?? []).map((group) => ({
            name: group.name,
            items: group.items.filter((item) => (!q || item.name.toLowerCase().includes(q)) && (!state.onlyOverrides || item.mode !== "auto")),
        })).filter((group) => group.items.length);
        const shown = groups.flatMap((group) => group.items.map((item) => item.key));
        if (!shown.includes(state.selected)) state.selected = shown[0] ?? null;

        const list = $("[data-list]");
        const scroll = list.scrollTop;
        if (!groups.length) {
            const overridesOnly = state.onlyOverrides && !q;
            list.replaceChildren(el("div", { className: "avail-empty" }, [
                el("span", { className: "round" }, [icon(P.auto, 28, 2)]),
                el("div", { className: "disp", text: overridesOnly ? t("No overrides here") : t("Nothing matches") }),
                el("div", { text: overridesOnly ? t("Everything in this tab is following inventory.") : t("Try a different search.") }),
            ]));
        } else {
            list.replaceChildren(...groups.map((group) => el("div", { className: "avail-group" }, [
                el("div", { className: "group-head" }, [
                    el("span", { className: "overline", text: group.name }),
                    el("span", { className: "group-count", text: interpolate(ngettext("%s item", "%s items", group.items.length), [group.items.length]) }),
                ]),
                el("div", { className: "avail-grid" }, group.items.map(card)),
            ])));
        }
        list.scrollTop = scroll;
        state.flash = null;
    }

    // ---------- moving around ----------

    function select(key, reveal = false) {
        state.selected = key;
        render();
        if (reveal) $(`[data-key="${CSS.escape(key)}"]`)?.scrollIntoView({ block: "nearest" });
    }

    function setTab(id) {
        state.tab = id;
        state.selected = null;
        render();
        $("[data-list]").scrollTop = 0;
    }

    // Arrow keys: left/right step through the cards, up/down jump to the nearest card in the row above/below
    function move(key) {
        const cards = [...root.querySelectorAll(".avail-card")];
        const index = cards.findIndex((node) => node.dataset.key === state.selected);
        if (index < 0) return cards[0] && select(cards[0].dataset.key, true);
        let next = null;
        if (key === "ArrowLeft") next = cards[index - 1];
        else if (key === "ArrowRight") next = cards[index + 1];
        else {
            const here = cards[index].getBoundingClientRect();
            const down = key === "ArrowDown";
            const rows = cards.map((node) => ({ node, box: node.getBoundingClientRect() }))
                .filter(({ box }) => down ? box.top > here.top + 4 : box.top < here.top - 4);
            if (rows.length) {
                const rowTop = down ? Math.min(...rows.map(({ box }) => box.top)) : Math.max(...rows.map(({ box }) => box.top));
                const centre = here.left + here.width / 2;
                next = rows.filter(({ box }) => Math.abs(box.top - rowTop) < 4)
                    .sort((a, b) => Math.abs(a.box.left + a.box.width / 2 - centre) - Math.abs(b.box.left + b.box.width / 2 - centre))[0].node;
            }
        }
        if (next) select(next.dataset.key, true);
    }

    // ---------- input ----------

    const search = $("[data-search]");
    search.addEventListener("input", () => {
        state.query = search.value;
        render();
    });
    $("[data-overrides]").addEventListener("click", () => {
        state.onlyOverrides = !state.onlyOverrides;
        render();
    });
    $("[data-reset]").addEventListener("click", resetTab);
    root.querySelector("[data-close]")?.addEventListener("click", () => dialog?.close());

    keyTarget.addEventListener("keydown", (e) => {
        if (!visible()) return;
        if (dialog) e.stopPropagation();  // the popup has the keyboard to itself
        const inSearch = e.target === search;
        if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "z") {
            e.preventDefault();
            undo();
            return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        switch (e.key) {
            case "ArrowUp":
            case "ArrowDown":
                e.preventDefault();
                move(e.key);
                return;
            case "ArrowLeft":
            case "ArrowRight":
                if (inSearch && search.value) return;  // let the caret move in the search box
                e.preventDefault();
                move(e.key);
                return;
            case "Enter": {
                e.preventDefault();
                const item = findItem(state.selected);
                if (item) cycle(item);
                return;
            }
            case "Tab": {
                e.preventDefault();
                const index = TABS.findIndex(([id]) => id === state.tab);
                setTab(TABS[(index + (e.shiftKey ? TABS.length - 1 : 1)) % TABS.length][0]);
                return;
            }
            case "Escape":
                if (search.value) {
                    e.preventDefault();  // first Esc clears the search; the next one closes a dialog
                    search.value = "";
                    state.query = "";
                    render();
                    search.blur();
                }
                return;
        }
        // Anything typed goes into the search box
        if (!inSearch && (e.key.length === 1 || e.key === "Backspace")) search.focus();
    });

    // Live updates only while the panel is on screen: a popup connects when it opens and lets go when it closes,
    // so a closed popup doesn't hold one of the browser's few connections to the server.
    let stopLive = null;
    const listen = () => { stopLive ??= onLiveUpdate("inventory", refresh, { skipOwn: true }); };  // our POSTs return the data
    if (dialog) dialog.addEventListener("close", () => { stopLive?.(); stopLive = null; });
    else listen();

    return {
        refresh,
        // Open fresh: back to the first card, no search left over
        reset() {
            search.value = "";
            state.query = "";
            state.selected = null;
            listen();  // refreshes as soon as it connects
        },
    };
}
