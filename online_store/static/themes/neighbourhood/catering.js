// Catering quote (themes/neighbourhood/catering.html): the card is saved in Square's form (an iframe,
// payments/save-card). When it says the card is saved, show the send sheet again with it.
window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.data?.action !== "cardSaved") return;
    const form = document.querySelector("form[data-quote]");
    form?.requestSubmit(form.querySelector('button[value="review"]'));
});
