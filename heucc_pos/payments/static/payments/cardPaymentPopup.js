document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector(".attach-card-payment");
    
    const popup = document.querySelector("#payment-popup");
    popup.innerHTML = `
        <div style="display: grid; gap: 1rem;">
            <nav>
                <button class="icon back-button"><span class="material-symbols-outlined">arrow_back</span></button>
            </nav>
            <iframe style="height: 50vh;border-radius: 10px;" frameborder="0"></iframe>
        </div>
    `

    const uuidField = document.createElement("input");
    uuidField.id = "hidden-uuid";
    uuidField.hidden = true;
    uuidField.name = "transaction_uuid";
    form.appendChild(uuidField);
    
    popup.querySelector(".back-button").addEventListener("click", e => {
        e.currentTarget.parentElement.parentElement.close()
    })

    form.addEventListener("submit", e => {
        if (form.dataset?.ready === "true") {
            console.log("submitting form")
        } else {
            e.preventDefault()
            const amount = form.querySelector("#transaction-amount").value;
            popup.querySelector("iframe").src = `${webPaymentWindowURL}?amount=${amount}`
            popup.showModal()
        }
    })
    
    window.addEventListener("message", e => {
        try {
            if (e.data.action === "closeCardPopup") {
                popup.close()
                fetch(webPaymentWindowURL, {
                    method:"PUT",
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('input[name="csrfmiddlewaretoken"]').value
                    },
                    body:JSON.stringify({
                        transaction_uuid:e.data.uuid
                    })
                })
                .then(response => response.json())
                .then(data => {
                    window.postMessage({transactionSuccessful:data.paid}, '*');
                    if (data.paid) {
                        form.querySelector("#hidden-uuid").value = data.uuid;
                        form.dataset.ready = true;
                        form.submit()
                    }
                })
            }
        } catch (error) {
            console.log(error)
        }
    })
})