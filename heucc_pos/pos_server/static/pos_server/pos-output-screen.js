import { addCartItem, removeCartItem, updateCheckoutButton } from './pos-utils.js';
const cartChannel = new BroadcastChannel('cart_channel');
const reloadChannel = new BroadcastChannel('reload_channel');
const thank = document.querySelector("#thank-you");

cartChannel.addEventListener("message", e => {
    console.log(e.data)
    if (e.data.message === "add") {
        thank.className = "";
        addCartItem(e.data.id, e.data.orderArr, null, true, e.data.discounts);
    } else if (e.data.message === "remove") {
        let dishCard = document.querySelector(`#dish-${e.data.id}`);
        removeCartItem(e.data.id, e.data.orderArr, dishCard)
    } else if (e.data.message === "paid") {
        if (e.data.name) {
            thank.children[0].innerHTML = `Thank you,<br>${e.data.name}!`
        } else {
            thank.children[0].innerHTML = "Thank you!"
        }
        thank.className = "anim";
        setTimeout(() => {
            updateCheckoutButton(e.data.orderArr, e.data.discounts)
            document.querySelector("#cart").innerHTML = ''
        }, 1000);
    } else if (e.data.message === "modifyDiscount") {
        updateCheckoutButton(e.data.orderArr, e.data.discounts)
    }
})

reloadChannel.addEventListener("message", e => {
    if (e.data === "reload") {
        window.location.reload()
    }
})