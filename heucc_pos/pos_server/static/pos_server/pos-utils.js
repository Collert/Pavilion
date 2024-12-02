import { componentChoices } from "./componentChoices.js";

const checkoutButton = document.querySelector("#checkout-button");
const totalDisplay = document.querySelector("#total-button");
const cartElement = document.querySelector("#cart");

export function addCartItem(dishId, cart, choices = {}, parent = undefined) {
    let dish = getDish(dishId)
    let customizationsString = ""
    let parentOnlyChoiceDish = false;
    if (dish.fields.choice_components.length) {
        if (Object.keys(choices).length) {
                dish.children = []
            for (const v of Object.values(choices)) {
                addCartItem(parseInt(v.id), cart, {}, dish.id)
                if (document.querySelector(`button[data-id="${dishId}"]`)?.dataset.onlyChoices === "True") return
                dish.children.push(v)
                customizationsString += v.title + ", "
            }
            customizationsString = customizationsString.slice(0, -2)
        } else {
            componentChoices(dishId)
            return
        }
    } else if (parent) {
        dish.parent = parent
        parentOnlyChoiceDish = document.querySelector(`button[data-id="${parent}"]`)?.dataset.onlyChoices === "True"
    }
    if (parentOnlyChoiceDish) {
        cart.addItem(dish)
    } else {
        cart.addItem(dish, parent !== undefined)
    }
    let existingDishCards = document.querySelectorAll(`.dish-${dishId}`);
    let existingDishCard;
    existingDishCards.forEach(card => {
        if (card.dataset.customs === JSON.stringify(choices)) {
            existingDishCard = card;
        }
    })
    if (!existingDishCard) {
        let cartItem = document.createElement("div");
        cartItem.className = "cart-item";
        cartItem.dataset.dishId = dishId;
        cartItem.dataset.customs = JSON.stringify(choices);
        if (parent && !parentOnlyChoiceDish) {
            cartItem.classList.add("part-of-combo");
        }
        cartItem.classList.add(`dish-${dishId}`);
        existingDishCard = cartItem;
        existingDishCard.innerHTML = `<div class="cart-item-title"><span><span class="item-qty"></span> X ${dish.fields.title}</span></div>
                                <button class="error remove-cart-item"><span class="material-symbols-outlined">remove</span></button>`
        existingDishCard.querySelector(".error.remove-cart-item").addEventListener("click", e => {
            e.stopPropagation()
            removeCartItem(dish, cart, existingDishCard)
        })
        cartElement.appendChild(existingDishCard);
        if (customizationsString) {
            const titleSpace = existingDishCard.querySelector(".cart-item-title")
            const small = document.createElement("small")
            small.innerHTML = customizationsString
            titleSpace.appendChild(small)
        }
    }
    existingDishCard.querySelector(".item-qty").innerHTML = cart.dishQuantity(dishId, Object.values(choices))
    updateCheckoutButton(cart)
    console.log(cart)
}

export function getDish(id) {
    // Assuming dishes is a parsed array of menu dishes that's been declared higher in the HTML.
    let dish = dishes.find(obj => obj.pk === parseInt(id))
    return {...dish}
}

export function removeCartItem(dish, cart, existingDishCard) {
    const customizations = Object.values(JSON.parse(existingDishCard.dataset.customs))
    customizations.forEach(child => {
        cart.removeItem(getDish(child.id), {parent:dish.id})
    })
    cart.removeItem(dish, {children:customizations})
    let itemQty = cart.dishQuantity(dish.pk, customizations)
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

function deepEqual(obj1, obj2) {
    // Check if both values are strictly equal
    if (obj1 === obj2) return true;

    // Check if either is null or not an object
    if (obj1 === null || typeof obj1 !== 'object' ||
        obj2 === null || typeof obj2 !== 'object') {
        return false;
    }

    // Get the keys of both objects
    const keys1 = Object.keys(obj1);
    const keys2 = Object.keys(obj2);

    // Compare the number of keys
    if (keys1.length !== keys2.length) return false;

    // Compare the values for each key recursively
    for (const key of keys1) {
        if (!keys2.includes(key) || !deepEqual(obj1[key], obj2[key])) {
            return false;
        }
    }

    return true;
}

function arraysAreEqual(arr1, arr2) {
    if (arr1.length !== arr2.length) return false;

    return arr1.every((obj1, index) => {
        const obj2 = arr2[index];
        const keys1 = Object.keys(obj1);
        const keys2 = Object.keys(obj2);

        // Check if both objects have the same keys
        if (keys1.length !== keys2.length || !keys1.every(key => keys2.includes(key))) {
            return false;
        }

        // Check if all the values are the same
        return keys1.every(key => obj1[key] === obj2[key]);
    });
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
    addItem(item, isFree = false) {
        this.items.push(item);
        if (!isFree) {
            this.total += item.fields.price;
        }
    }
    removeItem(item, context) {
        const {children, parent} = context
        if (children?.length) {
            this.items.splice(this.items.findIndex(i => (i.pk === item.pk && arraysAreEqual(children, i.children))), 1);
            this.total -= item.fields.price;
        } else if (parent) {
            this.items.splice(this.items.findIndex(i => (i.pk === item.pk && i.parent === parent)), 1);
        } else {
            this.items.splice(this.items.findIndex(i => i.pk === item.pk), 1);
            this.total -= item.fields.price;
        }
    }
    dishQuantity(dishId, children = []) {
        if (children.length) {
            return this.items.reduce((accumulator, currentValue) => {
                return (currentValue.pk === Number(dishId) && arraysAreEqual(children, currentValue.children)) ? accumulator + 1 : accumulator;
            }, 0);
        } else {
            return this.items.reduce((accumulator, currentValue) => {
                return currentValue.pk === Number(dishId) ? accumulator + 1 : accumulator;
            }, 0);
        }
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