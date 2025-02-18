import { addCartItem, removeCartItem, updateCheckoutButton, Cart } from './pos-utils.js';
const cartChannel = new BroadcastChannel('cart_channel');
const reloadChannel = new BroadcastChannel('reload_channel');
const thank = document.querySelector("#thank-you");
const orderSummary = document.querySelector("#out-cart-wrapper");

cartChannel.addEventListener("message", e => {
    console.log(e.data)
    if (e.data.message === "add") {
        thank.className = "";
        // orderSummary.className = "";
        addCartItem(e.data.id, e.data.orderArr, null, true, e.data.discounts);
    } else if (e.data.message === "remove") {
        let dishCard = document.querySelector(`#dish-${e.data.id}`);
        removeCartItem(e.data.id, e.data.orderArr, dishCard)
        // if (!e.data.orderArr.length) {
        //     orderSummary.className = "tucked";
        // }
    } else if (e.data.message === "paid") {
        if (e.data.name) {
            thank.children[0].innerHTML = gettext("Thank you") + `,<br>${e.data.name}!`
        } else {
            thank.children[0].innerHTML = gettext("Thank you!")
        }
        thank.className = "anim";
        setTimeout(() => {
            updateCheckoutButton(e.data.orderArr, e.data.discounts)
            document.querySelector("#cart").innerHTML = ''
        }, 1000);
        orderSummary.className = "tucked";
    } else if (e.data.message === "modifyDiscount") {
        updateCheckoutButton(e.data.orderArr, e.data.discounts)
    } else if (e.data.message === "openOrderSummary") {
        const cart = new Cart(e.data.cart)
        const summaryCart = document.querySelector("#cart")
        summaryCart.innerHTML = '';
        console.log(cart.uniqueDishes)
        cart.uniqueDishes.forEach(item => {
            console.log(item)
            let cartItem = document.createElement("div")
            cartItem.className = "cart-item";
            cartItem.innerHTML = `<div class="cart-item-title"><span><span class="item-qty">${cart.dishQuantity(item.pk)}</span> X ${item.fields.title}</span></div>`
            summaryCart.appendChild(cartItem);
        })
        document.querySelector("#tender-total").textContent = cart.remainingTotal;
        updateCheckoutButton(cart)
        orderSummary.classList.remove("tucked")
    } else if (e.data.message === "closeOrderSummary") {
        orderSummary.classList.add("tucked")
    }
})

reloadChannel.addEventListener("message", e => {
    if (e.data === "reload") {
        window.location.reload()
    }
})