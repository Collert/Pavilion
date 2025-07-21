import { addCartItem, updateCheckoutButton, Cart, CashPayment, GiftCardPayment, GiftCard, lookupGiftCard, CashDrawer } from './pos-utils.js';
import { playBeep } from './sounds.js'

const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
// There's a isSuperuser declaration in layout.html

let cart = new Cart

let cashDrawer = window.localStorage.getItem("cashDrawer");

let activeGiftCard;

const cartChannel = new BroadcastChannel('cart_channel');
const reloadChannel = new BroadcastChannel('reload_channel');
const choicesChannel = new BroadcastChannel('choices_channel');

const preCheckoutButton = document.querySelector("#checkout-button");

const confirmDialog = document.querySelector("#tender");

const cashButton = document.querySelector("#cash-button");
const cashInDialog = document.querySelector("#cash-in");
const changeDialog = document.querySelector("#cash-change");

const cardButton = document.querySelector("#card-button");
const cardInDialog = document.querySelector("#card-in");
const cardDoneDialog = document.querySelector("#card-done");

const adminAuthorizeForm = document.querySelector("#authorize form");
const adminAuthorizeModal = document.querySelector("#authorize");

const discountForm = document.querySelector("#discounts form");
const discountsLink = document.querySelector("#discounts-link");
const discountModal = document.querySelector("#discounts");

const summaryCart = document.querySelector("#summary");

const scannerButton = document.querySelector("#barcode-scanner-button");
const scannerDialog = document.querySelector("#card-scanner");

const cashDrawerDialog = document.querySelector("#cash-drawer");
const cashDrawerButton = document.querySelector("#cash-drawer-button");
const cashDrawerResult = document.querySelector("#cash-drawer-result");

const giftCardDialog = document.querySelector("#gift-card-dialog");

const errorDialog = document.querySelector("#error-dialog");

document.querySelectorAll(".dish").forEach(button => {
    button.addEventListener("click", e => {
        if (cashDrawer) {
            let dishId = e.currentTarget.dataset.id;
            e.stopPropagation();
            addCartItem(dishId, cart);
        } else {
            if (suAuthorized) {
                cashDrawerDialog.showModal()
            } else {
                adminAuthorizeModal.showModal();
            }
        }
    })
})

cashDrawerButton.addEventListener("click", e => {
    document.querySelector("#utils").close()
    cashDrawerDialog.showModal()
})

choicesChannel.addEventListener("message", e => {
    addCartItem(e.data.dishId, cart, e.data.choices)
    document.querySelector("#component-choices-dialog").close()
})

document.querySelector("#new-cash-drawer").addEventListener("submit", e => {
    e.preventDefault()
    cashDrawer = new CashDrawer(
        parseInt(document.querySelector("#fiveCent").value) || 0,
        parseInt(document.querySelector("#tenCent").value) || 0,
        parseInt(document.querySelector("#quarters").value) || 0,
        parseInt(document.querySelector("#oneDollar").value) || 0,
        parseInt(document.querySelector("#twoDollar").value) || 0,
        parseInt(document.querySelector("#fiveDollar").value) || 0,
        parseInt(document.querySelector("#tenDollar").value) || 0,
        parseInt(document.querySelector("#twentyDollar").value) || 0,
        parseInt(document.querySelector("#fiftyDollar").value) || 0,
        parseInt(document.querySelector("#hundredDollar").value) || 0
    )
    window.localStorage.setItem("cashDrawer", cashDrawer.string)
    e.currentTarget.style.display = "none";
    document.querySelector("#close-cash-drawer").style.display = 'grid';
    e.currentTarget.parentElement.close();
})

document.querySelector("#close-cash-drawer").addEventListener("submit", e => {
    e.preventDefault()
    let drawerEnd = cashDrawer.closeDrawer(
        parseInt(document.querySelector("#close-fiveCent").value) || 0,
        parseInt(document.querySelector("#close-tenCent").value) || 0,
        parseInt(document.querySelector("#close-quarters").value) || 0,
        parseInt(document.querySelector("#close-oneDollar").value) || 0,
        parseInt(document.querySelector("#close-twoDollar").value) || 0,
        parseInt(document.querySelector("#close-fiveDollar").value) || 0,
        parseInt(document.querySelector("#close-tenDollar").value) || 0,
        parseInt(document.querySelector("#close-twentyDollar").value) || 0,
        parseInt(document.querySelector("#close-fiftyDollar").value) || 0,
        parseInt(document.querySelector("#close-hundredDollar").value) || 0
    )
    window.localStorage.removeItem("cashDrawer");
    e.currentTarget.style.display = "none";
    document.querySelector("#new-cash-drawer").style.display = 'grid';
    e.currentTarget.parentElement.close();
    cashDrawerResult.querySelector("#result-fiveCent").innerHTML = `X ${drawerEnd.finalFiveCent}`
    cashDrawerResult.querySelector("#result-tenCent").innerHTML = `X ${drawerEnd.finalTenCent}`
    cashDrawerResult.querySelector("#result-quarters").innerHTML = `X ${drawerEnd.finalQuarters}`
    cashDrawerResult.querySelector("#result-oneDollar").innerHTML = `X ${drawerEnd.finalOneDollar}`
    cashDrawerResult.querySelector("#result-twoDollar").innerHTML = `X ${drawerEnd.finalTwoDollar}`
    cashDrawerResult.querySelector("#result-fiveDollar").innerHTML = `X ${drawerEnd.finalFiveDollar}`
    cashDrawerResult.querySelector("#result-tenDollar").innerHTML = `X ${drawerEnd.finalTenDollar}`
    cashDrawerResult.querySelector("#result-twentyDollar").innerHTML = `X ${drawerEnd.finalTwentyDollar}`
    cashDrawerResult.querySelector("#result-fiftyDollar").innerHTML = `X ${drawerEnd.finalFiftyDollar}`
    cashDrawerResult.querySelector("#result-hundredDollar").innerHTML = `X ${drawerEnd.finalHundredDollar}`
    cashDrawerResult.querySelector("#result-total").innerHTML = `$${drawerEnd.drawerTotal.toFixed(2)}`
    cashDrawerResult.querySelector("#result-total-profit").innerHTML = `$${drawerEnd.finalDrawerTotal.toFixed(2)}`
    cashDrawerResult.showModal()
    cashDrawer = undefined;
})

cashButton.addEventListener("click", e => {
    confirmDialog.close();
    // cashInDialog.querySelector("[name='cash-provided']").min = cart.remainingTotalNum;
    cashInDialog.showModal();
})

cashInDialog.querySelector("form").addEventListener("submit", e => {
    e.preventDefault()
    const changeNum = parseFloat(parseFloat(e.currentTarget.querySelector("[name='cash-provided']").value) - cart.remainingTotalNum)
    if (changeNum >= 0) {
        const change = changeNum.toFixed(2);
        changeDialog.querySelector("#change-to-give").textContent = change;
        cashInDialog.close()
        changeDialog.showModal()
        const customerName = document.querySelector("[name='customer-name']").value;
        const specialInstructions = document.querySelector("[name='special-instructions']").value;
        const link = e.currentTarget.action;
        const toGo = document.querySelector("[name='here-to-go']:checked").value === "go";
        sendOrder(link, customerName, specialInstructions, toGo);
        document.querySelector("[name='customer-name']").value = '';
        document.querySelector("[name='special-instructions']").value = '';
        e.currentTarget.querySelector("[name='cash-provided']").value = '';
        setTimeout(() => {
            changeDialog.close()
        }, 15000);
    } else {
        cart.addPayment(new CashPayment(e.currentTarget.querySelector("[name='cash-provided']").value));
        e.currentTarget.querySelector("[name='cash-provided']").value = '';
        cashInDialog.close()
    }
})

cardButton.addEventListener("click", e => {
    confirmDialog.close();
    cardInDialog.showModal();
    const actionLink = e.currentTarget.dataset.actionlink
    createSquarePayment(cart.remainingTotalNum, actionLink)
        .then(response => {
            const status = response.status
            fetch("/restaurant/webhook/square/check_card_status", {
                headers: { "X-CSRFToken": csrftoken },
                method: 'DELETE'
            })
            cardInDialog.close();
            cardDoneDialog.querySelector("#transaction-status").textContent = `Transaction ${status.toLowerCase()}!`
            cardDoneDialog.showModal();
            if (status === "COMPLETED") {
                const customerName = document.querySelector("[name='customer-name']").value;
                const specialInstructions = document.querySelector("[name='special-instructions']").value;
                const toGo = document.querySelector("[name='here-to-go']:checked").value === "go";
                sendOrder(actionLink, customerName, specialInstructions, toGo);
                document.querySelector("[name='customer-name']").value = '';
                document.querySelector("[name='special-instructions']").value = '';
            }
        })
        .catch(error => {
            // Ensure the card dialog is closed
            if (cardInDialog.open) {
                cardInDialog.close();
            }
            // Clean up the cache status
            fetch("/restaurant/webhook/square/check_card_status", {
                headers: { "X-CSRFToken": csrftoken },
                method: 'DELETE'
            }).catch(() => { }); // Ignore cleanup errors

            // Show error dialog
            errorDialog.querySelector("h2").innerText = error.message || error
            errorDialog.showModal()
        })
        .finally(() => {
            setTimeout(() => {
                try { document.querySelector("dialog[open]").close() } catch { }
            }, 10000);
        });
})

preCheckoutButton.addEventListener("click", () => {
    const tenders = document.querySelector("#tenders")
    summaryCart.innerHTML = '';
    cart.uniqueDishes.forEach(item => {
        let cartItem = document.createElement("div")
        cartItem.className = "cart-item";
        cartItem.innerHTML = `<div class="cart-item-title">${cart.dishQuantity(item.pk)} X ${item.fields.title}</div>`
        summaryCart.appendChild(cartItem);
    })
    document.querySelector("#tender-total").innerHTML = cart.remainingTotal;
    cartChannel.postMessage({
        message: "openOrderSummary",
        cart: cart
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
    fetch("/restaurant/check-su", {
        headers: { "X-CSRFToken": csrftoken },
        method: 'POST',
        body: JSON.stringify({
            username: adminAuthorizeForm.querySelector("[name='man-user']").value,
            password: adminAuthorizeForm.querySelector("[name='man-pass']").value,
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

scannerButton.addEventListener("click", e => {
    scannerDialog.showModal()
})

discountForm.addEventListener("submit", e => {
    e.preventDefault();
    const discountAmountInp = discountForm.querySelector("[name='discount-amount']");
    const discountPercentInp = discountForm.querySelector("[name='discount-percentage']");
    cart.discounts.amount = parseFloat(discountAmountInp.value) || 0;
    cart.discounts.percent = parseFloat(discountPercentInp.value) || 0;
    updateCheckoutButton(cart);
    discountModal.close();
    discountAmountInp.value = ''
    discountPercentInp.value = ''
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

// setInterval(() => {
//     if (!order.length) {
//         checkInventory();
//     }
// }, 60000);

function sendOrder(actionLink, customerName, instructions, toGo) {
    fetch(actionLink, {
        headers: { "X-CSRFToken": csrftoken },
        method: 'POST',
        body: JSON.stringify({
            cart: cart,
            name: customerName,
            instructions: instructions,
            toGo: toGo
        })
    }).then(() => {
        cart = new Cart()
        resetCard()
        updateCheckoutButton(cart)
        document.querySelector("#cart").innerHTML = ''
        cartChannel.postMessage({
            // id : null, 
            cart: cart,
            message: "paid",
            name: customerName
        });
        suAuthorized = isSuperuser;
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
                cart: cart,
                amount: parseFloat(amount).toFixed(2)
            })
        })
            .then(response => response.json())
            .then(data => {
                console.log(data)
                if (data.status === 402) {
                    reject(new Error(data.message));
                }
                if (data.message.checkout.status === "PENDING") {
                    checkResponse();
                } else {
                    reject(new Error('Payment failed'));
                }
            })
            .catch(error => {
                reject(error);
            });

        let pollAttempts = 0;
        const maxPollAttempts = 120; // 2 minutes with 1 second intervals
        const statusText = document.querySelector("#card-status-text");

        function updateStatusText(message) {
            if (statusText) {
                statusText.textContent = message;
            }
        }

        function checkResponse() {
            if (pollAttempts >= maxPollAttempts) {
                updateStatusText("Payment timeout - please check with staff");
                reject(new Error('Payment timeout - please check with staff'));
                return;
            }

            fetch("/restaurant/webhook/square/check_card_status")
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                    }
                    return response.json();
                })
                .then(data => {
                    console.log(`Poll attempt ${pollAttempts + 1}: Card status check:`, data)
                    pollAttempts++;

                    if (data.status === "COMPLETED") {
                        console.log("Payment completed successfully");
                        updateStatusText("Payment completed successfully!");
                        resolve(data);
                    } else if (data.status === "CANCELED") {
                        console.log("Payment was canceled");
                        updateStatusText("Payment was canceled");
                        resolve(data);
                    } else if (data.status === null || data.status === undefined) {
                        // Status not yet available in cache, continue polling
                        console.log("Status not yet available, continuing to poll...");
                        updateStatusText(`Waiting for payment status... (${pollAttempts}/${maxPollAttempts})`);
                        setTimeout(checkResponse, 1000);
                    } else {
                        // For any other status (like "PENDING"), continue polling
                        console.log(`Status is ${data.status}, continuing to poll...`);
                        updateStatusText(`Payment in progress... (${data.status})`);
                        setTimeout(checkResponse, 1000);
                    }
                })
                .catch(error => {
                    console.error(`Poll attempt ${pollAttempts + 1} failed:`, error);
                    pollAttempts++;
                    // On network errors, retry a few times before giving up
                    if (pollAttempts < maxPollAttempts) {
                        updateStatusText(`Connection issue, retrying... (${pollAttempts}/${maxPollAttempts})`);
                        setTimeout(checkResponse, 1000);
                    } else {
                        updateStatusText("Network error - please check with staff");
                        reject(new Error(`Network error after ${maxPollAttempts} attempts: ${error.message}`));
                    }
                });
        }
    });
}

document.querySelector("#card-number-lookup-button").addEventListener("click", async () => {
    activeGiftCard = await lookupGiftCard(document.querySelector("#card-number-input").value, giftCardDialog, cart)
})

document.querySelector("#card-charge-amount").addEventListener("submit", e => {
    e.preventDefault()
    const amount = parseFloat(document.querySelector("#card-charge-amount-inp").value)
    cart.addPayment(new GiftCardPayment(activeGiftCard.number, amount))
    activeGiftCard.availableBalance -= amount
    document.querySelector("#found-card-balance").textContent = activeGiftCard.availableBalance
    document.querySelector("dialog[open]").close()
})

document.querySelector("#card-charge-max").addEventListener("click", e => {
    const amount = Math.min(cart.remainingTotalNum, activeGiftCard.availableBalance)
    cart.addPayment(new GiftCardPayment(activeGiftCard.number, amount))
    activeGiftCard.availableBalance -= amount
    document.querySelector("#found-card-balance").textContent = activeGiftCard.availableBalance
    document.querySelector("dialog[open]").close()
})

document.querySelector("#card-reload").addEventListener("click", e => {

})

document.querySelector("#return-active-gift-card").addEventListener("click", e => {
    giftCardDialog.showModal()
})

document.querySelector("#reset-partial-payments").addEventListener("click", e => {
    resetCard()
})

function resetCard(newCart = false) {
    if (!newCart) {
        cart.resetPayments()
    }
    activeGiftCard = null;
    document.querySelector("#return-active-gift-card").style.display = "none"
    try {
        document.querySelector("dialog[open]").close()
    } catch { }
    cartChannel.postMessage({
        message: "closeOrderSummary"
    })
}

let videoDevice;

async function initCameraSelection() {
    const select = document.getElementById("camera-select");

    // Get available video input devices (cameras)
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoDevices = devices.filter(device => device.kind === "videoinput");

    // Populate the select element with available cameras
    videoDevices.forEach((device, index) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.text = device.label || gettext("Camera") + ` ${index + 1}`;
        select.appendChild(option);
    });

    // Event listener to change camera
    select.addEventListener("change", () => startScanner(select.value));

    // Start with the first camera
    if (videoDevices.length > 0) {
        videoDevice = videoDevices[0].deviceId
        startScanner(videoDevice);
    }
}

function startScanner(deviceId) {
    try {
        Quagga.stop(); // Stop any previous instance of Quagga
    } catch { }

    Quagga.init({
        inputStream: {
            type: "LiveStream",
            target: document.querySelector("#barcode-scanner"),
            constraints: {
                deviceId: deviceId,
                width: 520,
                height: 360,
                facingMode: "environment"
            }
        },
        decoder: {
            readers: ["code_128_reader"]
        },
        locate: true // Enable locating feature to increase detection accuracy
    }, function (err) {
        if (err) {
            console.error("Quagga initialization error:", err);
            return;
        }
        console.log("Barcode scanner initialized with selected camera");
        //   Quagga.start();
    });

    Quagga.onDetected(result => {
        if (result && result.codeResult && result.codeResult.code) {
            const code = result.codeResult.code;
            console.log("Barcode detected:", code);
            Quagga.stop()
            activeGiftCard = lookupGiftCard(document.querySelector("#card-number-input").value, giftCardDialog)
            playBeep()
            setTimeout(() => {
                startScanner(videoDevice);
            }, 2000);
            // Handle detected barcode
        } else {
            console.log("No valid code detected");
        }
    });
}

document.addEventListener("DOMContentLoaded", initCameraSelection);  