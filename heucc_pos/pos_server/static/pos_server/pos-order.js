import { addCartItem, updateCheckoutButton, getTotal, compileSummary } from './pos-utils.js';

const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
// There's a isSuperuser declaration in layout.html
let order = []

let discounts = {
    discountPercent : 0,
    discountAmount : 0
}

const cashButton = document.querySelector("#cash-button");
const cardButton = document.querySelector("#card-button");
const preCheckoutButton = document.querySelector("#checkout-button")

const cartChannel = new BroadcastChannel('cart_channel');
const reloadChannel = new BroadcastChannel('reload_channel');

const confirmDialog = document.querySelector("#tender");
const cashInDialog = document.querySelector("#cash-in");
const changeDialog = document.querySelector("#cash-change");
const cardInDialog = document.querySelector("#card-in");
const cardDoneDialog = document.querySelector("#card-done");
const adminAuthorizeForm = document.querySelector("#authorize form");
const adminAuthorizeModal = document.querySelector("#authorize");
const discountForm = document.querySelector("#discounts form");
const discountsLink = document.querySelector("#discounts-link");
const discountModal = document.querySelector("#discounts");
const toggleOrderSummary = document.querySelector("#toggle-summary");

document.querySelectorAll(".dish").forEach(button => {
    button.addEventListener("click", e => { 
        let dishId = e.currentTarget.dataset.id;
        e.stopPropagation();
        order.push(dishId)
        addCartItem(dishId, order, cartChannel, false, discounts);
        cartChannel.postMessage({
            id : dishId, 
            orderArr : order,
            message : "add",
            discounts : discounts
        });
    })
})

cashButton.addEventListener("click", e => {
    confirmDialog.close();
    cashInDialog.querySelector("[name='cash-provided']").min = getTotal(order, discounts);
    cashInDialog.showModal();
})

cashInDialog.querySelector("form").addEventListener("submit", e => {
    e.preventDefault()
    const change = parseFloat(parseFloat(e.currentTarget.querySelector("[name='cash-provided']").value) - getTotal(order, discounts)).toFixed(2);
    changeDialog.querySelector("#change-to-give").textContent = change;
    cashInDialog.close()
    changeDialog.showModal()
    const customerName = document.querySelector("[name='customer-name']").value;
    const specialInstructions = document.querySelector("[name='special-instructions']").value;
    const link = e.currentTarget.action;
    console.log(document.querySelector("[name='here-to-go']:checked").value)
    const toGo = document.querySelector("[name='here-to-go']:checked").value === "go";
    sendOrder(link, customerName, specialInstructions, toGo);
    document.querySelector("[name='customer-name']").value = '';
    document.querySelector("[name='special-instructions']").value = '';
    e.currentTarget.querySelector("[name='cash-provided']").value = '';
    setTimeout(() => {
        changeDialog.close()
    }, 15000);
})

cardButton.addEventListener("click", e => {
    confirmDialog.close();
    cardInDialog.showModal();
    const actionLink = e.currentTarget.dataset.actionlink
    createSquarePayment(getTotal(order), actionLink)
    .then(response => {
        const status = response.status
        fetch("/restaurant/webhook/square/check_card_status", {
            headers: {"X-CSRFToken": csrftoken },
            method:'DELETE'
        })
        cardInDialog.close();
        cardDoneDialog.querySelector("#transaction-status").textContent = `Transaction ${status.toLowerCase()}!`
        cardDoneDialog.showModal();
        setTimeout(() => {
            cardDoneDialog.close();
            if (status === "COMPLETED") {
                const customerName = document.querySelector("[name='customer-name']").value;
                const specialInstructions = document.querySelector("[name='special-instructions']").value;
                const toGo = document.querySelector("[name='here-to-go']:checked").value === "go";
                sendOrder(actionLink, customerName, specialInstructions, toGo);
                document.querySelector("[name='customer-name']").value = '';
                document.querySelector("[name='special-instructions']").value = '';
            }
        }, 4000);
    });
})

preCheckoutButton.addEventListener("click", () => {
    compileSummary(order, discounts)
    cartChannel.postMessage({
        message: "openOrderSummary"
    })
    confirmDialog.showModal()
})


discountsLink.addEventListener("click", e => {
    e.preventDefault()
    if (suAuthorized) {
        discountModal.showModal();
    } else {
        adminAuthorizeModal.showModal();
    }
})
adminAuthorizeForm.addEventListener("submit", e => {
    e.preventDefault();
    fetch("{% url 'check_su' %}", {
        headers: {"X-CSRFToken": csrftoken },
        method:'POST',
        body: JSON.stringify({
            username:adminAuthorizeForm.querySelector("[name='man-user']").value,
            password:adminAuthorizeForm.querySelector("[name='man-pass']").value,
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.superuser) {
            suAuthorized = true;
            adminAuthorizeModal.close();
            discountModal.showModal();                
        } else {
            adminAuthorizeForm.querySelector("span").style.display = "block";
            adminAuthorizeForm.querySelectorAll("*").forEach(element => {
                element.classList.add("error")
            })
        }
    })
})

discountForm.addEventListener("submit", e => {
    e.preventDefault();
    const discountAmountInp = discountForm.querySelector("[name='discount-amount']");
    const discountPercentInp = discountForm.querySelector("[name='discount-percentage']");
    discounts.discountAmount = parseFloat(discountAmountInp.value) || 0;
    discounts.discountPercent = parseFloat(discountPercentInp.value) || 0;
    updateCheckoutButton(order, discounts);
    cartChannel.postMessage({
        orderArr : order,
        message : "modifyDiscount",
        discounts : discounts
    });
    discountModal.close();
    discountAmountInp.value = ''
    discountPercentInp.value = ''
})

toggleOrderSummary.addEventListener("click", e => {
    e.preventDefault()
    cartChannel.postMessage({
        message: "toggleOrderSummary"
    })
})

window.addEventListener("beforeunload", () => {
    reloadChannel.postMessage("reload");
})

document.querySelectorAll("dialog nav button.icon").forEach(button => {
    button.addEventListener("click", () => {
        document.querySelector("dialog[open]").close()
        cartChannel.postMessage({
            message: "closeOrderSummary"
        })
    })
})

let inventoryState;
checkInventory(true);

async function checkInventory(initial = false) {
    const newInventory = await fetchInventory()
    if (!initial) {
        newInventory.forEach(dish => {
            if (inventoryState.some(item => (item.fields.in_stock !== dish.fields.in_stock || item.fields.force_in_stock !== dish.fields.force_in_stock))) {
                location.reload()
            }
        })
    }
    inventoryState = newInventory;
}

async function fetchInventory() {
    const response = await fetch(invUpdatesLink);
    const data = await response.json()
    return JSON.parse(data)
}

setInterval(() => {
    if (!order.length) {
        checkInventory();
    }
}, 60000);

function sendOrder(actionLink, customerName, instructions, toGo) {
    fetch(actionLink, {
        headers: {"X-CSRFToken": csrftoken },
        method:'POST',
        body: JSON.stringify({
            order:order,
            table:customerName,
            instructions:instructions,
            toGo:toGo
        })
    }).then(()=>{
        order = []
        updateCheckoutButton(order)
        document.querySelector("#cart").innerHTML = ''
        cartChannel.postMessage({
            id : null, 
            orderArr : order,
            message : "paid",
            name: customerName
        });
        suAuthorized = isSuperuser;
        discounts = {
            discountPercent : 0,
            discountAmount : 0
        }
        setTimeout(() => {
            checkInventory();
        }, 4000);
    })
}

function createSquarePayment(amount, actionLink) {
    return new Promise((resolve, reject) => {
        // Initial payment request
        fetch(actionLink, {
            headers: { "X-CSRFToken": csrftoken },
            method: 'PUT',
            body: JSON.stringify({
                amount: parseFloat(amount).toFixed(2)
            })
        })
        .then(response => response.json())
        .then(data => {
            // console.log(data)
            if (data.message.checkout.status === "PENDING") {
                checkResponse();
            } else {
                reject(new Error('Payment failed'));
            }
        })
        .catch(error => {
            reject(error);
        });

        function checkResponse() {
            fetch("/restaurant/webhook/square/check_card_status")
            .then(response => response.json())
            .then(data => {
                console.log(data)
                if (data.status === "COMPLETED") {
                    resolve(data);
                } else if (data.status === "CANCELED") {
                    resolve(data);
                } else {
                    if (cardInDialog.open) {
                        setTimeout(checkResponse, 1000); // Retry after 1 second
                    }
                }
            })
            .catch(error => {
                reject(error);
            });
        }
    });
}