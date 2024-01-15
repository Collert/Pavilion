import { addCartItem, updateCheckoutButton, getTotal, compileSummary } from './pos-utils.js';

const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
let order = []

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

document.querySelectorAll(".dish").forEach(button => {
    button.addEventListener("click", e => { 
        let dishId = e.currentTarget.dataset.id;
        e.stopPropagation();
        order.push(dishId)
        addCartItem(dishId, order, cartChannel);
        cartChannel.postMessage({
            id : dishId, 
            orderArr : order,
            message : "add"
        });
    })
})

cashButton.addEventListener("click", e => {
    confirmDialog.close();
    cashInDialog.querySelector("[name='cash-provided']").min = getTotal(order);
    cashInDialog.showModal();
})

cashInDialog.querySelector("form").addEventListener("submit", e => {
    e.preventDefault()
    const change = parseFloat(e.currentTarget.querySelector("[name='cash-provided']").value) - getTotal(order);
    changeDialog.querySelector("#change-to-give").textContent = change;
    cashInDialog.close()
    changeDialog.showModal()
    const customerName = document.querySelector("[name='customer-name']").value;
    const specialInstructions = document.querySelector("[name='special-instructions']").value;
    const link = e.currentTarget.action;
    sendOrder(link, customerName, specialInstructions);
    document.querySelector("[name='customer-name']").value = '';
    e.currentTarget.querySelector("[name='cash-provided']").value = '';
    setTimeout(() => {
        changeDialog.close()
    }, 15000);
})

cardButton.addEventListener("click", () => {
    confirmDialog.close();
    cardInDialog.showModal();
})

preCheckoutButton.addEventListener("click", () => {
    compileSummary(order)
    confirmDialog.showModal()
})

window.addEventListener("beforeunload", () => {
    reloadChannel.postMessage("reload");
})

document.querySelectorAll("dialog nav button.icon").forEach(button => {
    button.addEventListener("click", () => {
        document.querySelector("dialog[open]").close()
    })
})

function sendOrder(actionLink, customerName, instructions) {
    fetch(actionLink, {
        headers: {"X-CSRFToken": csrftoken },
        method:'POST',
        body: JSON.stringify({
            order:order,
            table:customerName,
            instructions:instructions
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
    })
}