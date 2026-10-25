// The card form (payments/templates/payments/_card_form.html): Square's card fields, styled with the store theme's
// colours, then the card's token goes to `payUrl`, which charges the amount the server fixed (never one from here).
// Once paid: in the store's sheet, tell the page (postMessage closeCardPopup, as checkout.js expects); on the
// customer's phone, reload to show the paid page.

const config = JSON.parse(document.getElementById("card-config").textContent);
const button = document.getElementById("card-pay");
const label = document.getElementById("card-pay-text");
const errorBox = document.getElementById("card-error");
const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";

const MESSAGES = {
    GENERIC_DECLINE: gettext("The card was declined. Try another card."),
    CVV_FAILURE: gettext("The security code (CVV) doesn’t match."),
    ADDRESS_VERIFICATION_FAILURE: gettext("The postal code doesn’t match the card."),
    INVALID_EXPIRATION: gettext("The expiry date isn’t right."),
    INSUFFICIENT_FUNDS: gettext("The card was declined: not enough funds."),
    CARD_EXPIRED: gettext("This card has expired."),
};

function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
}

function busy(on, text) {
    button.disabled = on;
    button.classList.toggle("busy", on);
    label.textContent = text;
}

// The theme's colours for Square's fields (they live in Square's iframe, so they need real values, not variables)
function themeStyle() {
    const css = getComputedStyle(document.documentElement);
    const get = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    const ink = get("--ink", "#221E18"), line = get("--dash", "#D9CDB6"), focus = get("--deep", "#2C5A43"), bad = "#B3361F";
    return {
        input: { color: ink, fontSize: "16px", fontWeight: "500" },
        "input::placeholder": { color: get("--soft-ink", "#7C7466") },
        ".input-container": { borderColor: line, borderRadius: "14px", borderWidth: "1.5px" },
        ".input-container.is-focus": { borderColor: focus },
        ".input-container.is-error": { borderColor: bad },
        ".message-text": { color: get("--soft-ink", "#7C7466") },
        ".message-icon": { color: get("--soft-ink", "#7C7466") },
        ".message-text.is-error": { color: bad },
        ".message-icon.is-error": { color: bad },
    };
}

async function pay(card) {
    showError("");
    busy(true, gettext("Paying…"));
    try {
        const tokenized = await card.tokenize();
        if (tokenized.status !== "OK") {
            busy(false, interpolate(gettext("Pay %s"), [config.amountText]));
            return;  // Square shows what's wrong under its fields
        }
        const response = await fetch(config.payUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
            body: JSON.stringify({ token: tokenized.token, ...(config.extra || {}) }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== "success") {
            const code = (data.errors || [])[0]?.code;
            throw new Error(MESSAGES[code] || data.message || gettext("The payment didn’t go through. Try again."));
        }
        busy(true, gettext("Paid"));
        button.classList.add("done");
        if (config.mode === "sheet") {
            setTimeout(() => parent.postMessage({ action: "closeCardPopup", uuid: config.extra?.transaction_uuid }, location.origin), 600);
        } else {
            location.reload();
        }
    } catch (e) {
        busy(false, interpolate(gettext("Pay %s"), [config.amountText]));
        showError(e.message);
    }
}

async function start() {
    if (!window.Square) {
        showError(gettext("The card form couldn’t load. Check the connection and reload."));
        return;
    }
    try {
        const payments = window.Square.payments(config.appId, config.locationId);
        const card = await payments.card({ style: themeStyle() });
        document.getElementById("card-container").replaceChildren();
        await card.attach("#card-container");
        busy(false, interpolate(gettext("Pay %s"), [config.amountText]));
        button.addEventListener("click", () => pay(card));
    } catch (e) {
        console.error(e);
        showError(gettext("The card form couldn’t load. Reload to try again."));
    }
}

start();

// In the store's checkout the form sits in a frame: tell the page how tall it is, so the frame fits it exactly
if (config.mode === "sheet" && parent !== window) {
    const report = () => parent.postMessage({ action: "cardFormHeight", height: document.documentElement.scrollHeight }, location.origin);
    new ResizeObserver(report).observe(document.body);
    report();
}
