const checkoutButton = document.querySelector("#checkout-button");
const totalDisplay = document.querySelector("#total-button");
const cart = document.querySelector("#cart");
const summaryCart = document.querySelector("#summary");

export function addCartItem(dishId, orderArray, broadcastChannel, display = false) {
    updateCheckoutButton(orderArray)
    let dish = getDish(dishId)
    let existingDishCard = document.querySelector(`#dish-${dishId}`);
    if (!existingDishCard) {
        let cartItem = document.createElement("div")
        cartItem.className = "cart-item";
        cartItem.dataset.dishId = dishId;
        cartItem.id = `dish-${dishId}`;
        existingDishCard = cartItem;
        if (!display && broadcastChannel) {
            existingDishCard.innerHTML = `<div class="cart-item-title"><span><span class="item-qty"></span> X ${dish.fields.title}</span></div>
                                    <button class="error remove-cart-item"><span class="material-symbols-outlined">remove</span></button>`
            existingDishCard.querySelector(".error.remove-cart-item").addEventListener("click", e => {
                e.stopPropagation()
                orderArray.splice(orderArray.indexOf(dishId), 1);
                broadcastChannel.postMessage({
                    id : dishId, 
                    orderArr : orderArray,
                    message : "remove"
                });
                removeCartItem(dishId, orderArray, existingDishCard)
            })
        } else {
            existingDishCard.innerHTML = `<div class="cart-item-title"><span><span class="item-qty"></span> X ${dish.fields.title}</span></div>`
        }
        cart.appendChild(existingDishCard);
    }
    existingDishCard.querySelector(".item-qty").innerHTML = getQuantity(orderArray, dishId)
}

export function compileSummary (order) {
    summaryCart.innerHTML = '';
    new Set(order).forEach(item => {
        let dish = getDish(item)
        let cartItem = document.createElement("div")
        cartItem.className = "cart-item";
        cartItem.innerHTML = `<div class="cart-item-title">${getQuantity(order, item)} X ${dish.fields.title}</div>`
        summaryCart.appendChild(cartItem);
    })
    document.querySelector("#tender-total").textContent = getTotal(order);
}

export function getDish(id) {
    let dish = dishes.find(obj => obj.pk === parseInt(id))
    return dish
}

export function removeCartItem(dishId, orderArray, existingDishCard) {
    updateCheckoutButton(orderArray)
    let itemQty = getQuantity(orderArray, dishId)
    if (!itemQty) {
        cart.removeChild(existingDishCard)
    } else {
        existingDishCard.querySelector(".item-qty").innerHTML = itemQty
    }
}

export function updateCheckoutButton(order) {
    totalDisplay.className = order.length ? "" : "hidden"
    totalDisplay.querySelector("span").innerHTML = getTotal(order)
    if (checkoutButton) {
        checkoutButton.className = order.length ? "" : "hidden"
        checkoutButton.disabled = order.length === 0
    }
}

export function getQuantity(array, valueToFind) {
    return array.reduce((accumulator, currentValue) => {
                return currentValue === valueToFind ? accumulator + 1 : accumulator;
            }, 0);
}

export function getTotal(order) {
    let total = 0;
    order.forEach(item => {
        total += getDish(item).fields.price;
    })
    return total
}