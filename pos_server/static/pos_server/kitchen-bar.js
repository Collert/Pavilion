// Kitchen display (KDS). Shows the orders that involve the stations this screen covers (?filter=<code>),
// and lets staff approve, reject, complete and hand out orders.
//
// Station statuses (OrderStation): 0 pending approval, 1 being made, 2 done, 3 rejected.
// A station that isn't in order.stations has nothing to make for that order.
//
// Each card's data-progress-state drives its colour (kitchen.css), relative to *this* screen:
//   0 waiting for this screen's approval, 1 waiting for another station's approval,
//   2 being made here, 3 done here (ready to hand out).

const pollEveryMilisecs = 11 * 1000;

const rejectOrderDialog = document.querySelector("#reject-reason");
const orderManagementDialog = document.querySelector("#order-management");
const preferencesDialog = document.querySelector("#filters-dialog");
const availabilityDialog = document.querySelector("#availability");
const mainDiv = document.querySelector("#markings");

const stations = JSON.parse(document.querySelector("#stations-data").textContent);
const filters = JSON.parse(document.querySelector("#filters-data").textContent);
const stationsByCode = Object.fromEntries(stations.map(station => [station.code, station]));
const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value ?? "";

const params = new URLSearchParams(document.location.search);
const minutesParam = name => {
    const minutes = parseInt(params.get(name));
    document.querySelector(`#${name}-input`).value = minutes || "";
    return minutes > 0 ? minutes * 60000 : null;
};
const autoDoneTimeout = minutesParam("auto-done");
const autoCollectTimeout = minutesParam("auto-collect");

const orders = new Map();        // order_id -> latest order data from the server
let selectedId = null;
let busy = false;                // one action at a time, so double taps don't double-submit

// ---------- order state helpers ----------

const ownStatuses = order => filters.filter(code => code in order.stations).map(code => order.stations[code]);
const otherStatuses = order => Object.entries(order.stations).filter(([code]) => !filters.includes(code)).map(([, status]) => status);
const pendingHere = order => ownStatuses(order).includes(0);
const pendingElsewhere = order => otherStatuses(order).includes(0);
const doneHere = order => ownStatuses(order).every(status => status >= 2);
const doneEverywhere = order => Object.values(order.stations).every(status => status >= 2);

function progressState(order) {
    if (doneHere(order)) return 3;
    if (pendingHere(order)) return 0;
    if (pendingElsewhere(order)) return 1;
    return 2;
}

// Does this screen care about the order? (mirrors the server-side filter in order_marking)
const relevant = order => !order.picked_up && ownStatuses(order).some(status => status <= 2);

// ---------- rendering ----------

function el(tag, { className, text, attrs } = {}, children = []) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    for (const [key, value] of Object.entries(attrs ?? {})) node.setAttribute(key, value);
    children.forEach(child => node.appendChild(child));
    return node;
}
const icon = name => el("span", { className: "material-symbols-outlined", text: name });

function progressStack(code, status) {
    const station = stationsByCode[code];
    return el("div", { className: `progress-stack ${code}-progress`, attrs: { "data-status": status, "data-station": code, title: station?.name ?? code } }, [
        icon(station?.icon ?? "restaurant"),
        el("div", { className: "hourglass" }, [icon("hourglass_bottom"), icon("hourglass_top")]),
        el("span", { className: "material-symbols-outlined check", text: "check" }),
        el("span", { className: "material-symbols-outlined done_all", text: "done_all" }),
    ]);
}

function channelLine(order) {
    const line = el("h3");
    if (order.channel === "store") {
        line.append(icon("storefront"), " " + gettext("In-person"));
    } else {
        line.append(icon(order.channel === "web" ? "shopping_cart_checkout" : "local_shipping"),
                    " " + (order.channel === "web" ? gettext("Online pick-up") : gettext("Delivery")));
        if (order.phone) line.append(" ", icon("call"), " " + order.phone);
    }
    return line;
}

function renderCard(order) {
    const timestamp = el("span", { className: "timestamp", attrs: { "data-timestamp": order.start_time } });
    timestamp.append(gettext("Prep time:") + " ", el("span", { text: "00:00" }));

    const progresses = el("div", { className: "progresses" },
        Object.entries(order.stations).map(([code, status]) => progressStack(code, status)));

    const dishes = el("ul", {}, order.dishes
        .filter(dish => filters.includes(dish.station))
        .map(dish => el("li", { text: `${dish.quantity} X ${dish.name}` })));

    const body = el("div", {}, [
        channelLine(order),
        progresses,
        el("h3", {}, [icon(order.to_go_order ? "takeout_dining" : "restaurant"),
                      document.createTextNode(" " + (order.to_go_order ? gettext("Order to-go") : gettext("Order for here")))]),
        dishes,
    ]);
    if (order.special_instructions) {
        body.append(el("h3", { text: gettext("Special instructions:") }), el("p", { text: order.special_instructions }));
    }

    const card = el("div", { className: "order", attrs: { "data-order-id": order.order_id, "data-channel": order.channel } }, [
        el("div", { className: "summary" }, [
            el("h2", { text: order.name || gettext("No name") }),
            el("div", { className: "name-time" }, [el("span", { text: `${gettext("Order")} #${order.order_id}` }), timestamp]),
        ]),
        body,
    ]);
    card.dataset.progressState = progressState(order);
    card.addEventListener("click", () => select(order.order_id));
    attachSwipability(card);
    return card;
}

const cardFor = orderId => mainDiv.querySelector(`.order[data-order-id="${orderId}"]`);
const cardIds = () => [...mainDiv.querySelectorAll(".order:not(.disappear)")].map(card => parseInt(card.dataset.orderId));

// Bring the screen in line with the latest list of active orders from the server.
function sync(latest) {
    const seen = new Set();
    for (const order of latest.filter(relevant)) {
        seen.add(order.order_id);
        orders.set(order.order_id, order);
        const card = cardFor(order.order_id);
        if (!card) {
            mainDiv.appendChild(renderCard(order));
        } else {
            updateCard(card, order);
        }
    }
    for (const id of [...orders.keys()]) {
        if (!seen.has(id)) removeCard(id);
    }
    if (selectedId === null || !orders.has(selectedId)) select(cardIds()[0] ?? null);
    showEmptyState();
}

function updateCard(card, order) {
    for (const [code, status] of Object.entries(order.stations)) {
        const stack = card.querySelector(`.progress-stack[data-station="${code}"]`);
        if (stack) stack.dataset.status = status;
    }
    card.dataset.progressState = progressState(order);
}

function removeCard(orderId) {
    orders.delete(orderId);
    const card = cardFor(orderId);
    if (!card) return;
    card.classList.add("disappear");
    setTimeout(() => card.remove(), 400);
    if (selectedId === orderId) select(cardIds().find(id => id !== orderId) ?? null);
}

function select(orderId) {
    mainDiv.querySelectorAll(".order.selected").forEach(card => card.classList.remove("selected"));
    selectedId = orderId;
    const card = orderId !== null ? cardFor(orderId) : null;
    if (card) {
        card.classList.add("selected");
        card.scrollIntoView({ behavior: "smooth" });
    }
}

function moveSelection(step) {
    const ids = cardIds();
    const index = ids.indexOf(selectedId);
    const next = ids[index + step];
    if (next !== undefined) select(next);
}

function showEmptyState() {
    const sign = mainDiv.querySelector("h1.no-orders");
    if (cardIds().length === 0 && !sign) {
        mainDiv.appendChild(el("h1", { className: "no-orders", text: gettext("No new orders"), attrs: { style: "text-align: center;" } }));
    } else if (cardIds().length && sign) {
        sign.remove();
    }
}

// ---------- server actions ----------

async function send(method, body) {
    const response = await fetch(window.location.pathname, {
        method,
        headers: { "X-CSRFToken": csrftoken, "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.status || data.error || response.statusText);
    return data;
}

async function act(method, body) {
    if (busy) return;
    busy = true;
    try {
        const data = await send(method, body);
        touch(body.orderId);
        await refresh();
        return data;
    } catch (error) {
        alert(gettext("That didn't work: ") + error.message);
    } finally {
        busy = false;
    }
}

const approve = orderId => act("POST", { orderId, action: "approve", filters }).then(data => {
    if (data?.payment_error) alert(gettext("Order approved, but capturing the card payment failed: ") + data.payment_error);
});
const reject = (orderId, rejection) => act("POST", { orderId, action: "delete", filters, rejection });
const markDone = orderId => act("PUT", { orderId, filters });
const markPickedUp = orderId => act("DELETE", { orderId });

// The main "next step" for an order: approve it, finish it, or hand it out.
function advance(orderId) {
    const order = orders.get(orderId);
    if (!order) return;
    if (pendingHere(order)) return approve(orderId);
    if (doneEverywhere(order)) {
        if (order.channel === "delivery") return;  // the courier collects delivery orders
        return markPickedUp(orderId);
    }
    if (doneHere(order) || pendingElsewhere(order)) return shakeOtherStations(orderId);
    return markDone(orderId);
}

function shakeOtherStations(orderId) {
    const icons = [...cardFor(orderId).querySelectorAll(".progress-stack")]
        .filter(stack => !filters.includes(stack.dataset.station))
        .map(stack => stack.querySelector("span"));
    icons.forEach(i => i.classList.add("shake"));
    setTimeout(() => icons.forEach(i => i.classList.remove("shake")), 2000);
}

// Remember when someone last acted on a card; the auto-done/collect timers count from there.
function touch(orderId) {
    cardFor(orderId)?.querySelector(".timestamp")?.setAttribute("data-last-interaction", new Date().toISOString());
}

async function refresh() {
    const response = await fetch(checkOrdersLink);
    if (response.ok) sync(await response.json());
}

// ---------- timers ----------

function startTimers() {
    setInterval(() => {
        document.querySelectorAll(".timestamp").forEach(node => {
            const seconds = Math.max(0, Math.floor((Date.now() - new Date(node.dataset.timestamp)) / 1000));
            node.lastElementChild.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
        });
    }, 1000);

    const autoAdvance = (timeout, state) => setInterval(() => {
        for (const order of orders.values()) {
            const card = cardFor(order.order_id);
            if (!card || progressState(order) !== state) continue;
            const stamp = card.querySelector(".timestamp");
            const since = new Date(stamp.dataset.lastInteraction || stamp.dataset.timestamp);
            if (Date.now() - since >= timeout) advance(order.order_id);
        }
    }, 60000);
    if (autoDoneTimeout) autoAdvance(autoDoneTimeout, 2);
    if (autoCollectTimeout) autoAdvance(autoCollectTimeout, 3);
}

// ---------- input ----------

window.addEventListener("keydown", e => {
    if (!rejectOrderDialog.open && !preferencesDialog.open) {
        if (e.key === "ArrowDown" || e.key === "2") moveSelection(1);
        else if (e.key === "ArrowUp" || e.key === "8") moveSelection(-1);
        else if ((e.key === "Enter" || e.key === "5") && selectedId !== null) advance(selectedId);
        else if ((e.key === "Backspace" || e.key === "Delete") && selectedId !== null) rejectOrderDialog.showModal();
        else if (e.key === "1") {
            if (availabilityDialog.open) availabilityDialog.querySelector("iframe").contentWindow.postMessage("closeDialog", "*");
            else availabilityDialog.showModal();
        }
    } else if (rejectOrderDialog.open && e.key >= "1" && e.key <= "9" && document.activeElement.name !== "reason-extra") {
        const checkbox = rejectOrderDialog.querySelector(`input[type='checkbox'][data-key='${e.key}']`);
        if (checkbox) checkbox.checked = !checkbox.checked;
    }
});

window.addEventListener("message", event => {
    if (event.data === "closeDialog" && event.origin === window.location.origin) availabilityDialog.close();
});

rejectOrderDialog.querySelector("form").addEventListener("submit", e => {
    e.preventDefault();
    const checked = [...rejectOrderDialog.querySelectorAll("input[type='checkbox']:checked")];
    if (!checked.length) {
        alert(gettext("Please select at least one rejection reason."));
        return;
    }
    reject(selectedId, {
        reasons: checked.map(checkbox => checkbox.value),
        reasonExtra: rejectOrderDialog.querySelector("textarea").value,
    });
    rejectOrderDialog.close();
});

function attachSwipability(card) {
    let startX;
    const selected = () => card.classList.contains("selected");
    const start = e => {
        if (!selected()) return;
        startX = e.touches ? e.touches[0].clientX : e.clientX;
        card.style.transition = "none";
    };
    const move = e => {
        if (!selected()) return;
        const deltaX = (e.touches ? e.touches[0].clientX : e.clientX) - startX;
        const max = window.innerWidth / 2;
        card.style.transform = `translateX(${Math.max(-max, Math.min(deltaX, max))}px)`;
    };
    const end = () => {
        if (!selected()) return;
        const currentX = parseFloat(card.style.transform.replace("translateX(", "")) || 0;
        if (Math.abs(currentX) > window.innerWidth / 4) openOrderManagement();
        card.style.transition = "transform 0.5s ease-in-out";
        card.style.transform = "translateX(0px)";
    };
    card.addEventListener("touchstart", start);
    card.addEventListener("touchmove", move);
    card.addEventListener("touchend", end);
}

const managementButtons = {
    approve: document.querySelector("#approve-order-button"),
    reject: document.querySelector("#reject-order-button"),
    done: document.querySelector("#mark-order-done-button"),
    collected: document.querySelector("#mark-order-collected-button"),
};
managementButtons.approve.addEventListener("click", () => approve(selectedId));
managementButtons.reject.addEventListener("click", () => rejectOrderDialog.showModal());
managementButtons.done.addEventListener("click", () => advance(selectedId));
managementButtons.collected.addEventListener("click", () => advance(selectedId));
orderManagementDialog.querySelectorAll("button").forEach(button => button.addEventListener("click", () => orderManagementDialog.close()));

function openOrderManagement() {
    const order = orders.get(selectedId);
    if (!order) return;
    const state = progressState(order);
    managementButtons.approve.disabled = state !== 0;
    managementButtons.reject.disabled = state !== 0;
    managementButtons.done.disabled = state !== 2;
    managementButtons.collected.disabled = state !== 3;
    orderManagementDialog.showModal();
}

// ---------- start ----------

sync(JSON.parse(document.querySelector("#initial-orders").textContent));
startTimers();
setInterval(refresh, pollEveryMilisecs);
