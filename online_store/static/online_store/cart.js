// The online store's cart. Lines use the format the server prices (pos_server/pricing.py):
//     {key, type: "dish"|"combo", id, qty, slots}
// plus a few display-only fields (title, image, unit in cents). The total, discount and tax shown to the
// customer come from the server's quote (online_store.views.cart_quote); the order is priced again when placed.

export class OnlineCart {
    constructor(saved = null) {
        this.lines = Array.isArray(saved?.lines) ? saved.lines : [];
        this.partialPayments = Array.isArray(saved?.partialPayments) ? saved.partialPayments : [];
        this.coupon = saved?.coupon || "";
        this.nextKey = saved?.nextKey || 1;
        this.quote = null;
        this.quoteError = "";
    }

    add(line) {
        const same = this.lines.find((l) => l.type === line.type && l.id === line.id &&
            JSON.stringify(l.slots || {}) === JSON.stringify(line.slots || {}));
        if (same) same.qty += 1;
        else this.lines.push({ ...line, key: "L" + this.nextKey++, qty: 1 });
    }

    remove(key) {
        const line = this.lines.find((l) => l.key === key);
        if (!line) return;
        if (line.qty > 1) line.qty -= 1;
        else this.lines = this.lines.filter((l) => l.key !== key);
    }

    get count() {
        return this.lines.reduce((sum, line) => sum + line.qty, 0);
    }

    // Cents. Until the server has priced the cart, an estimate from the menu prices.
    get totalCents() {
        return this.quote ? this.quote.total : this.lines.reduce((sum, line) => sum + line.unit * line.qty, 0);
    }

    get giftCents() {
        return this.partialPayments.reduce((sum, p) => sum + Math.round(p.amount * 100), 0);
    }

    // What is left to pay by card, in dollars (the payment popup and the forms use dollars).
    get remainingTotalNum() {
        return Math.max(0, this.totalCents - this.giftCents) / 100;
    }

    get remainingTotal() {
        return this.remainingTotalNum.toFixed(2);
    }

    addPayment(payment) {
        this.partialPayments.push(payment);
    }

    resetPayments() {
        this.partialPayments = [];
    }

    serverLines() {
        return this.lines.map(({ type, id, qty, slots }) => ({ type, id, qty, ...(type === "combo" ? { slots: slots || {} } : {}) }));
    }

    toJSON() {
        return { lines: this.lines, partialPayments: this.partialPayments, coupon: this.coupon, nextKey: this.nextKey };
    }
}

export class GiftCardPayment {
    constructor(number, amount) {
        this.type = "gift";
        this.number = number;
        this.amount = parseFloat(amount);
    }
}

export class GiftCard {
    constructor(number, availableBalance, email, image) {
        this.number = number;
        this.availableBalance = availableBalance;
        this.originalBalance = availableBalance;
        this.email = email;
        this.image = image;
    }
}

export async function lookupGiftCard(number, cardDialog) {
    const cardErrors = document.querySelector("#gift-card-errors");
    const showError = (message) => {
        cardErrors.textContent = message;
        cardErrors.style.display = "block";
        return null;
    };
    if (!number) return showError(gettext("No card number entered"));
    const response = await fetch("/gift-cards/card-api/" + encodeURIComponent(number));
    if (response.status === 404) return showError(interpolate(gettext("Card #%s not found"), [number]));
    if (response.status !== 200) return showError(gettext("Unknown error"));
    cardErrors.style.display = "none";
    const data = await response.json();
    const giftCard = new GiftCard(data.number, parseFloat(data.available_balance), data.email, data.image);
    if (giftCard.image) document.querySelector("#found-card-image").src = giftCard.image;
    document.querySelector("#found-card-number").textContent = giftCard.number;
    document.querySelector("#found-card-balance").textContent = giftCard.availableBalance.toFixed(2);
    document.querySelector("#card-charge-amount-inp").max = giftCard.availableBalance;
    document.querySelector("dialog[open]")?.close();
    cardDialog.showModal();
    return giftCard;
}
