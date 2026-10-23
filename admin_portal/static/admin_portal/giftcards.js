// Gift cards (admin_portal/giftcards.html): the reveal step after issuing a card. Its number is only in this page,
// so copy and print it here, and let Done through once the box is ticked. #gc-modal keeps background answers out
// (data-keep) until Done.

document.addEventListener("click", (event) => {
    const copy = event.target.closest("[data-copy]");
    if (copy) {
        navigator.clipboard?.writeText(copy.dataset.copy.replace(/\s/g, "")).catch(() => {});
        copy.textContent = copy.dataset.done;
        copy.classList.add("done");
        return;
    }
    if (event.target.closest("[data-print]")) {
        window.print();
        return;
    }
    if (event.target.closest("[data-ack-done]")) document.getElementById("gc-modal")?.removeAttribute("data-keep");
});

document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-ack]")) return;
    const done = document.querySelector("[data-ack-done]");
    if (done) done.disabled = !event.target.checked;
});
