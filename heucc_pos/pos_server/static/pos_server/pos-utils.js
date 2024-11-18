const checkoutButton = document.querySelector("#checkout-button");
const totalDisplay = document.querySelector("#total-button");
const cartElement = document.querySelector("#cart");

export function addCartItem(dishId, cart) {
    let dish = getDish(dishId)
    cart.addItem(dish)
    let existingDishCard = document.querySelector(`#dish-${dishId}`);
    if (!existingDishCard) {
        let cartItem = document.createElement("div");
        cartItem.className = "cart-item";
        cartItem.dataset.dishId = dishId;
        cartItem.id = `dish-${dishId}`;
        existingDishCard = cartItem;
        existingDishCard.innerHTML = `<div class="cart-item-title"><span><span class="item-qty"></span> X ${dish.fields.title}</span></div>
                                <button class="error remove-cart-item"><span class="material-symbols-outlined">remove</span></button>`
        existingDishCard.querySelector(".error.remove-cart-item").addEventListener("click", e => {
            e.stopPropagation()
            removeCartItem(dish, cart, existingDishCard)
        })
        cartElement.appendChild(existingDishCard);
    }
    existingDishCard.querySelector(".item-qty").innerHTML = cart.dishQuantity(dishId)
    updateCheckoutButton(cart)
}

export function getDish(id) {
    let dish = dishes.find(obj => obj.pk === parseInt(id))
    return dish
}

export function removeCartItem(dish, cart, existingDishCard) {
    cart.removeItem(dish)
    let itemQty = cart.dishQuantity(dish.pk)
    if (!itemQty) {
        cartElement.removeChild(existingDishCard)
    } else {
        existingDishCard.querySelector(".item-qty").innerHTML = itemQty
    }
    updateCheckoutButton(cart)
}

export function updateCheckoutButton(cart) {
    const { amount:discountAmount, percent:discountPercent } = cart.discounts || {};
    if (discountAmount) {
        totalDisplay.querySelector("#discount-display").textContent = ` | $${discountAmount} discount applied`
    } else if (discountPercent) {
        totalDisplay.querySelector("#discount-display").textContent = ` | ${discountPercent}% discount applied`
    } else {
        totalDisplay.querySelector("#discount-display").textContent = ''
    }
    totalDisplay.className = cart.items.length ? "" : "hidden"
    totalDisplay.querySelector("span").innerHTML = cart.remainingTotal
    if (checkoutButton) {
        checkoutButton.className = cart.items.length ? "" : "hidden"
        checkoutButton.disabled = cart.items.length === 0
    }
}

export async function lookupGiftCard(number, cardDialog) {
    const cardErrors = document.querySelector("#gift-card-errors")
    if (number) {
        const response = await fetch("/gift-cards/card-api/" + number)
        let data
        if (response.status === 200) {
            data = await response.json()
        } else if (response.status === 404) {
            cardErrors.innerHTML = `Card #${number} not found`
            cardErrors.style.display = "block";
            return null
        } else {
            cardErrors.innerHTML = "Unknown error"
            cardErrors.style.display = "block";
            return null
        }
        console.log(data)
        const giftCard = new GiftCard(data.number, data.available_balance, data.email, data.image)
        document.querySelector("#found-card-image").src = "/heucc_pos" + giftCard.image
        document.querySelector("#found-card-number").textContent = giftCard.number
        document.querySelector("#found-card-balance").textContent = giftCard.availableBalance
        document.querySelector("#card-charge-amount-inp").max = giftCard.availableBalance
        document.querySelector("dialog[open]").close()
        document.querySelector("#return-active-gift-card").style.display = "block"
        cardDialog.showModal()
        return giftCard
    } else {
        cardErrors.innerHTML = "No card number entered"
        cardErrors.style.display = "block";
        return null
    }
}

export class Cart {
    constructor(existingInstance = null) {
        // existingInstance is to reconstruct the class after passing it through the broadcast channel
        this.items = existingInstance?.items ? [...existingInstance.items] : [];
        this.discounts = existingInstance?.discounts ? { ...existingInstance.discounts } : { percent: 0, amount: 0 };
        this.total = existingInstance?.total ?? 0;
        this.partialPayments = existingInstance?.partialPayments ? [...existingInstance.partialPayments] : [];
    }
    
    get remainingTotalNum() {
        let remaining = this.total;
        remaining = Math.max(remaining - this.discounts.amount - (remaining * this.discounts.percent / 100), 0)
        this.partialPayments.forEach(payment => {
            remaining -= payment.amount;
        })
        return Math.max(remaining, 0);
    }
    get remainingTotal() {
        return this.remainingTotalNum.toLocaleString("en", { minimumFractionDigits: 2 })
    }
    addItem(item) {
        this.items.push(item);
        this.total += item.fields.price;
    }
    removeItem(item) {
        this.items.splice(this.items.findIndex(i => i.pk === item.pk), 1);
        this.total -= item.fields.price;
    }
    dishQuantity(dishId) {
        return this.items.reduce((accumulator, currentValue) => {
            return currentValue.pk === Number(dishId) ? accumulator + 1 : accumulator;
        }, 0);
    }
    get uniqueDishes() {
        const seen = new Set();
        return this.items.filter(item => {
            const key = item.pk;
            if (seen.has(key)) {
                return false;
            } else {
                seen.add(key);
                return true;
            }
        });
    }
    addPayment(payment) {
        this.partialPayments.push(payment)
        updateCheckoutButton(this)
    }
    resetPayments() {
        this.partialPayments = []
        updateCheckoutButton(this)
    }
}

class Payment {
    constructor(amount) {
        this.amount = parseFloat(amount)
    }
}
 export class CashPayment extends Payment {
    constructor(amount) {
        super(amount)
        this.type = "cash"
    }
}

export class GiftCardPayment extends Payment {
    constructor(number, amount) {
        super(amount)
        this.type = "gift"
        this.number = number
    }
    get lastFour() {
        return this.number.slice(-4)
    }
}

export class GiftCard {
    constructor(number, availableBalance, email, image) {
        this.number = number
        this.availableBalance = availableBalance
        this.originalBalance = availableBalance
        this.email = email
        this.image = image
    }
}

export class CashDrawer {
    constructor(fiveCent, tenCent, quarters, oneDollar, twoDollar, fiveDollar, tenDollar, twentyDollar, fiftyDollar, hundredDollar) {
        this.fiveCent = fiveCent
        this.tenCent = tenCent
        this.quarters = quarters
        this.oneDollar = oneDollar
        this.twoDollar = twoDollar
        this.fiveDollar = fiveDollar
        this.tenDollar = tenDollar
        this.twentyDollar = twentyDollar
        this.fiftyDollar = fiftyDollar
        this.hundredDollar = hundredDollar
    }
    closeDrawer(fiveCent, tenCent, quarters, oneDollar, twoDollar, fiveDollar, tenDollar, twentyDollar, fiftyDollar, hundredDollar) {

        let finalDrawerTotal = 0;

        finalDrawerTotal += (fiveCent - this.fiveCent) * 0.05
        finalDrawerTotal += (tenCent - this.tenCent) * 0.1
        finalDrawerTotal += (quarters - this.quarters) * 0.25
        finalDrawerTotal += (oneDollar - this.oneDollar)
        finalDrawerTotal += (twoDollar - this.twoDollar) * 2
        finalDrawerTotal += (fiveDollar - this.fiveDollar) * 5
        finalDrawerTotal += (tenDollar - this.tenDollar) * 10
        finalDrawerTotal += (twentyDollar - this.twentyDollar) * 20
        finalDrawerTotal += (fiftyDollar - this.fiftyDollar) * 50
        finalDrawerTotal += (hundredDollar - this.hundredDollar) * 100
        
        let drawerTotal = 0;
        
        drawerTotal += fiveCent * 0.05
        drawerTotal += tenCent * 0.1
        drawerTotal += quarters * 0.25
        drawerTotal += oneDollar
        drawerTotal += twoDollar * 2
        drawerTotal += fiveDollar * 5
        drawerTotal += tenDollar * 10
        drawerTotal += twentyDollar * 20
        drawerTotal += fiftyDollar * 50
        drawerTotal += hundredDollar * 100

        return {
            finalFiveCent:(fiveCent - this.fiveCent),
            finalTenCent:(tenCent - this.tenCent),
            finalQuarters:(quarters - this.quarters),
            finalOneDollar:(oneDollar - this.oneDollar),
            finalTwoDollar:(twoDollar - this.twoDollar),
            finalFiveDollar:(fiveDollar - this.fiveDollar),
            finalTenDollar:(tenDollar - this.tenDollar),
            finalTwentyDollar:(twentyDollar - this.twentyDollar),
            finalFiftyDollar:(fiftyDollar - this.fiftyDollar),
            finalHundredDollar:(hundredDollar - this.hundredDollar),
            drawerTotal:drawerTotal,
            finalDrawerTotal:finalDrawerTotal
        }
    }
    get string () {
        return JSON.stringify(this)
    }
}