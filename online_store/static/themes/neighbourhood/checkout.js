// Checkout (themes/neighbourhood/cart.html): the card form is Square's, in an iframe (payments/web-payment-window).
// When it reports a paid transaction, put its id in the checkout form and place the order.
window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    if (event.data?.action === "cardFormHeight") {  // the card form's height: no inner scrollbar, no gap
        const frame = [...document.querySelectorAll("iframe[data-card-frame]")].find((f) => f.contentWindow === event.source);
        if (frame) frame.style.height = `${Math.ceil(event.data.height)}px`;
        return;
    }
    if (event.data?.action !== "closeCardPopup") return;
    const form = document.querySelector("form[data-checkout]");
    const field = form?.querySelector('input[name="transaction_uuid"]');
    if (!field) return;
    field.value = event.data.uuid;
    form.requestSubmit(form.querySelector('button[value="place"]'));
});
