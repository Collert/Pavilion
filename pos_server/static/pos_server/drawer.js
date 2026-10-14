// The cash drawer (see docs/mockups/Cash drawer-html and pos_server/register.py: cash_drawer).
// Phases: closed (enter the starting cash) -> open (today's totals) -> count (enter what's there) -> done.
import { $, el, fmt, keypad, post, toCents, dollars, toast, wireSheets } from "./staff-ui.js";

const boot = JSON.parse(document.getElementById("drawer-boot").textContent);
const t = (text) => gettext(text);
const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const when = (iso) => {
    const date = new Date(iso);
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (date.toDateString() === today.toDateString()) return interpolate(t("Today, %s"), [time(iso)]);
    if (date.toDateString() === yesterday.toDateString()) return interpolate(t("Yesterday, %s"), [time(iso)]);
    return date.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + time(iso);
};

const state = {
    phase: boot.drawer ? "open" : "closed",
    drawer: boot.drawer,
    closed: null,
    entry: boot.last?.left || 20000,
    fresh: true,       // the next digit replaces the amount instead of adding to it
    moveIn: true,
    busy: false,
};

function render() {
    const { phase } = state;
    $("#entry-view").hidden = !(phase === "closed" || phase === "count");
    $("#open-view").hidden = phase !== "open";
    $("#done-view").hidden = phase !== "done";
    renderStatus();
    if (phase === "closed" || phase === "count") renderEntry();
    if (phase === "open") renderOpen();
    if (phase === "done") renderDone();
}

function renderStatus() {
    const pill = $("#status");
    pill.className = "status-pill";
    let text = t("Drawer closed");
    if (state.phase === "open") { pill.classList.add("open"); text = interpolate(t("Open since %s"), [time(state.drawer.openedAt)]); }
    if (state.phase === "count") { pill.classList.add("counting"); text = t("Counting the cash"); }
    if (state.phase === "done") text = interpolate(t("Closed at %s"), [time(state.closed.closedAt)]);
    $("#status-text").textContent = text;
}

function renderEntry() {
    const counting = state.phase === "count";
    $("#entry-over").textContent = counting ? t("Closing count") : t("Starting cash");
    $("#entry-title").textContent = counting ? t("How much is in the drawer?") : t("Open the drawer");
    const amount = $("#entry-amount");
    amount.textContent = fmt(state.entry);
    amount.classList.remove("bump");
    void amount.offsetWidth;
    amount.classList.add("bump");
    $("#entry-chips").hidden = counting;
    $("#entry-hint").hidden = !counting;
    $("#entry-chips").replaceChildren(...[10000, 15000, 20000, 25000].map((value) => el("button", {
        text: fmt(value).replace(".00", ""), attrs: { "aria-pressed": String(state.entry === value) },
        on: { click: () => { state.entry = value; state.fresh = true; render(); } },
    })));
    const none = state.entry === 0;
    keys.clear.disabled = none;
    keys.back.disabled = none;
    $("#count-cancel").hidden = !counting;
    $("#entry-go").textContent = counting ? t("Close the drawer") : t("Open the drawer");
    $("#entry-go").disabled = (!counting && none) || state.busy;

    $("#side-over").textContent = counting ? t("Before you close") : t("Before you start");
    $("#side-title").textContent = counting ? t("Count first, then see how you did.") : t("Count the float, then start selling.");
    $("#side-body").textContent = counting
        ? t("The expected amount stays hidden until you close, so the count is honest. You'll see the difference right after.")
        : t("Cash payments are blocked until the drawer is open. The starting cash is what you leave in the drawer to make change.");
    const facts = counting
        ? [[t("Opened with"), fmt(state.drawer.opening)], [t("Opened at"), time(state.drawer.openedAt)]]
        : boot.last ? [[t("Last close"), when(boot.last.closedAt)], [t("Left in the drawer"), fmt(boot.last.left)]] : [];
    $("#side-facts").replaceChildren(...facts.map(([label, value]) => el("div", { className: "fact" }, [el("span", { text: label }), el("span", { text: value })])));
}

const keys = keypad($("#entry-keys"), {
    onDigit: (digit) => {
        const base = state.fresh ? 0 : state.entry;
        state.entry = Math.min(99999999, base * 10 + digit);
        state.fresh = false;
        render();
    },
    onClear: () => { state.entry = 0; state.fresh = false; render(); },
    onBack: () => { state.entry = Math.floor(state.entry / 10); state.fresh = false; render(); },
});

function renderOpen() {
    const d = state.drawer;
    const moved = d.paidIn - d.paidOut;
    const tiles = [
        [t("Cash sales"), fmt(d.cashSales), interpolate(ngettext("%s payment", "%s payments", d.cashCount), [d.cashCount])],
        [t("Card sales"), fmt(d.cardSales), interpolate(ngettext("%s payment", "%s payments", d.cardCount), [d.cardCount])],
        [t("Paid in and out"), (moved < 0 ? "−" : "") + fmt(Math.abs(moved)), interpolate(t("%s in · %s out"), [fmt(d.paidIn), fmt(d.paidOut)])],
        [t("Orders"), String(d.orders), d.orders ? interpolate(t("Average %s"), [fmt(d.average)]) : t("None yet")],
    ];
    $("#tiles").replaceChildren(...tiles.map(([label, value, note]) => el("div", { className: "tile row-in" }, [
        el("div", { className: "overline", text: label }),
        el("div", { className: "tile-value", text: value }),
        el("div", { className: "sub", text: note }),
    ])));
    $("#opened-amount").textContent = fmt(d.opening);
    $("#opened-text").textContent = interpolate(t("Opened at %s by %s. When the shift ends, count what is in the drawer and close it."), [time(d.openedAt), d.openedBy]);
}

function renderDone() {
    const c = state.closed;
    const diff = c.counted - c.expected;
    const kind = diff === 0 ? "balanced" : diff > 0 ? "over" : "short";
    $("#hero").className = "hero" + (kind === "balanced" ? "" : " " + kind);
    $("#hero-icon").replaceChildren(el("span", { icon: kind === "balanced" ? "checkHuge" : kind === "over" ? "up" : "down" }));
    const diffText = diff === 0 ? fmt(0) : (diff > 0 ? "+" : "−") + fmt(Math.abs(diff));
    $("#hero-amount").textContent = diffText;
    $("#hero-words").textContent = { balanced: t("Balanced. The drawer matches."), over: t("Over"), short: t("Short") }[kind];
    $("#hero-note").textContent = {
        balanced: t("Every dollar is accounted for."),
        over: t("There is more cash in the drawer than the register expected. Check for a missed sale or a miscount."),
        short: t("There is less cash in the drawer than the register expected. Recount, and check for unrecorded change or a payout."),
    }[kind];
    $("#closed-over").textContent = interpolate(t("%s · closed at %s"), [boot.device, time(c.closedAt)]);
    const rows = [
        [t("Opening balance"), fmt(c.opening)],
        [t("Cash sales"), "+" + fmt(c.cashSales)],
    ];
    if (c.paidIn) rows.push([t("Paid in"), "+" + fmt(c.paidIn)]);
    if (c.paidOut) rows.push([t("Paid out"), "−" + fmt(c.paidOut)]);
    rows.push([t("Expected in drawer"), fmt(c.expected), "strong"], [t("You counted"), fmt(c.counted), "strong last"]);
    $("#rows").replaceChildren(
        ...rows.map(([label, value, extra]) => el("div", { className: "result-row row-in " + (extra || "") }, [el("span", { text: label }), el("span", { text: value })])),
        el("div", { className: "result-diff row-in " + (kind === "balanced" ? "" : kind) }, [el("span", { text: t("Difference") }), el("span", { className: "money", text: diffText })]),
    );
}

$("#entry-go").addEventListener("click", async () => {
    if (state.busy) return;
    state.busy = true;
    render();
    const counting = state.phase === "count";
    const result = await post(location.pathname, counting ? { action: "close", counted: dollars(state.entry) } : { action: "open", amount: dollars(state.entry) })
        .catch(() => ({ ok: false, data: { message: t("The register is offline. Check the connection.") } }));
    state.busy = false;
    if (!result.ok) {
        toast(result.data.message || t("That didn't work. Try again."), { error: true });
        render();
        return;
    }
    if (counting) {
        state.closed = result.data.closed;
        state.drawer = null;
        state.phase = "done";
    } else {
        state.drawer = result.data.drawer;
        state.phase = "open";
        if (boot.next) {
            location.href = boot.next;
            return;
        }
        toast(interpolate(t("Drawer opened with %s"), [fmt(state.drawer.opening)]));
    }
    render();
});

$("#start-close").addEventListener("click", () => { state.phase = "count"; state.entry = 0; state.fresh = false; render(); });
$("#count-cancel").addEventListener("click", () => { state.phase = "open"; render(); });
$("#finish").addEventListener("click", () => {
    boot.last = { closedAt: state.closed.closedAt, left: state.closed.counted };
    state.phase = "closed";
    state.entry = state.closed.counted || 20000;
    state.fresh = true;
    state.closed = null;
    render();
});
$("#print").addEventListener("click", () => window.print());

// Paid in / paid out
wireSheets();
$("#open-move").addEventListener("click", () => {
    state.moveIn = true;
    $("#move-amount").value = "";
    $("#move-reason").value = "";
    renderMove();
    $("#move-sheet").showModal();
});
function renderMove() {
    $("#move-dir").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.dir === "in") === state.moveIn)));
}
$("#move-dir").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    state.moveIn = button.dataset.dir === "in";
    renderMove();
});
$("#move-save").addEventListener("click", async () => {
    const cents = toCents($("#move-amount").value);
    if (!cents) { toast(t("Enter an amount."), { error: true }); return; }
    const result = await post(location.pathname, { action: "move", amount: dollars(state.moveIn ? cents : -cents), reason: $("#move-reason").value })
        .catch(() => ({ ok: false, data: {} }));
    if (!result.ok) { toast(result.data.message || t("That didn't work. Try again."), { error: true }); return; }
    state.drawer = result.data.drawer;
    $("#move-sheet").close();
    toast(state.moveIn ? interpolate(t("Put %s in the drawer"), [fmt(cents)]) : interpolate(t("Took %s out of the drawer"), [fmt(cents)]));
    render();
});

render();
