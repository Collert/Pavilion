// Customer-facing display. The register (register.js) posts the cart on BroadcastChannel("cart_channel")
// whenever it changes, so this page must run in the same browser as the register. Money is in cents.
const cartChannel = new BroadcastChannel("cart_channel");
const reloadChannel = new BroadcastChannel("reload_channel");
const thank = document.querySelector("#thank-you");
const orderSummary = document.querySelector("#out-cart-wrapper");
const cartElement = document.querySelector("#cart");

const fmt = (cents) => (cents < 0 ? "−$" : "$") + (Math.abs(cents) / 100).toFixed(2);

function renderCart(data) {
    cartElement.replaceChildren(...data.lines.map((line) => {
        const item = document.createElement("div");
        item.className = "cart-item";
        const title = document.createElement("div");
        title.className = "cart-item-title";
        title.textContent = `${line.qty} × ${line.title}`;
        const price = document.createElement("span");
        price.textContent = fmt(line.total);
        title.append(" ", price);
        item.append(title);
        if (line.mods.length) {
            const mods = document.createElement("small");
            mods.textContent = line.mods.join(" · ");
            item.append(mods);
        }
        return item;
    }));
    document.querySelector("#tender-total").textContent = (data.total / 100).toFixed(2);
    const extras = [];
    if (data.discount) extras.push(`${gettext("Discounts")} −${fmt(data.discount).slice(1)}`);
    if (data.tax) extras.push(`${gettext("Tax")} ${fmt(data.tax)}`);
    document.querySelector("#discount-display").textContent = extras.length ? " | " + extras.join(" | ") : "";
    orderSummary.classList.toggle("tucked", !data.lines.length);
}

cartChannel.addEventListener("message", (e) => {
    const data = e.data || {};
    if (data.message === "cart") {
        thank.className = "";
        renderCart(data);
    } else if (data.message === "paid") {
        thank.children[0].textContent = data.name ? interpolate(gettext("Thank you, %s!"), [data.name]) : gettext("Thank you!");
        thank.className = "anim";
        orderSummary.classList.add("tucked");
    }
});

reloadChannel.addEventListener("message", (e) => {
    if (e.data === "reload") window.location.reload();
});

// Ask an open register for the current cart
cartChannel.postMessage({ message: "hello" });
