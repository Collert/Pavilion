// The gift card page (themes/neighbourhood/gift_cards.html): every design comes with the page (#gift-designs), so
// picking a design, an amount or who it's for only changes what's on screen. Nothing is sent until "Buy", and the
// server checks it all again then (online_store/pages.py: gift_cards_page). Listeners sit on the document, so they
// keep working after live-page swaps the form in again (e.g. after a declined card).

const read = (id) => JSON.parse(document.getElementById(id)?.textContent || "null");
const designs = read("gift-designs") || [];
const me = read("gift-me");
const fill = (text, ...values) => values.reduce((out, v) => out.replace("%s", v), text);

function form() {
    return document.querySelector("form[data-gift]");
}

function current(f) {
    const id = Number(f.querySelector('input[name="design"]:checked')?.value);
    return designs.find((d) => d.id === id) || designs[0];
}

function amountOf(f) {
    const picked = f.querySelector('input[name="amt"]:checked')?.value;
    if (picked === "other") return f.querySelector('input[name="other"]').value.replace(/\D/g, "");
    return picked || "";
}

// A design was picked: its picture, name, amounts (keeping the amount if it has it), custom range and message
function showDesign(f, previous) {
    const d = current(f);
    const preview = f.querySelector("#gift-preview");
    preview.className = `nb-gift-art big ink-${d.ink}`;
    preview.style.backgroundColor = d.color;
    preview.style.backgroundImage = d.image ? `url("${d.image}")` : "";
    f.querySelector("#gift-preview-name").textContent = d.showText ? preview.dataset.store : "";
    const title = f.querySelector("#gift-title");
    title.textContent = fill(title.dataset.text, d.name);

    const was = f.querySelector('input[name="amt"]:checked')?.value;
    const chips = d.amounts.map((a) => {
        const label = document.createElement("label");
        label.className = "nb-gift-chip";
        const input = Object.assign(document.createElement("input"), { type: "radio", name: "amt", value: String(a), className: "visually-hidden" });
        const span = document.createElement("span");
        span.textContent = `$${a}`;
        label.append(input, span);
        return label;
    });
    const other = f.querySelector("#gift-other");
    f.querySelector("#gift-amounts").replaceChildren(...chips, other);
    other.hidden = !d.custom;
    const range = f.querySelector("#gift-range");
    range.hidden = !d.custom;
    range.textContent = fill(range.dataset.text, d.min, d.max);
    const keep = [...f.querySelectorAll('input[name="amt"]')].find((i) => i.value === was && !(i.value === "other" && !d.custom));
    const fallback = d.amounts.length ? String(d.amounts[Math.min(1, d.amounts.length - 1)]) : "other";
    (keep || f.querySelector(`input[name="amt"][value="${fallback}"]`))?.click();

    const msg = f.querySelector('input[name="msg"]');
    if (msg && (!msg.value || msg.value === previous?.message)) msg.value = d.message || "";
}

// "It's a gift" or "For me": the labels, the From and Message fields, and your own name and email
function showWho(f) {
    const self = f.querySelector('input[name="self"]:checked')?.value === "1";
    f.querySelectorAll("[data-gift][data-self]").forEach((n) => { n.textContent = self ? n.dataset.self : n.dataset.gift; });
    f.querySelectorAll("[data-gift-only]").forEach((n) => { n.hidden = self; });
    if (self && me) {
        const to = f.querySelector('input[name="to"]'), email = f.querySelector('input[name="email"]');
        if (!to.value) to.value = me.name;
        if (!email.value) email.value = me.email;
    }
}

// The amount on the card and the Buy button
function showTotal(f) {
    const amount = amountOf(f);
    f.querySelector("#gift-preview-amt").textContent = amount ? `$${amount}` : "";
    const buy = f.querySelector("#gift-buy");
    buy.textContent = amount ? fill(buy.dataset.text, amount) : buy.dataset.plain;
    buy.disabled = !(amount && f.querySelector('input[name="email"]').value.trim());
}

let lastDesign = null;
document.addEventListener("change", (event) => {
    const f = form();
    if (!f || !f.contains(event.target)) return;
    if (event.target.name === "design") showDesign(f, lastDesign);
    if (event.target.name === "self") showWho(f);
    lastDesign = current(f);
    showTotal(f);
});
document.addEventListener("input", (event) => {
    const f = form();
    if (!f || !f.contains(event.target)) return;
    if (event.target.name === "other") f.querySelector('input[name="amt"][value="other"]').checked = true;
    showTotal(f);
});
if (form()) lastDesign = current(form());
