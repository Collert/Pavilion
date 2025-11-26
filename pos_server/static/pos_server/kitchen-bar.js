// WebSocket connection for real-time order updates
let ordersSocket = null;
let reconnectAttempts = 0;
const maxReconnectAttempts = 10;
const reconnectDelay = 3000;

const rejectOrderDialog = document.querySelector("#reject-reason")
const orderManagementDialog = document.querySelector("#order-management")
const preferencesDialog = document.querySelector("#filters-dialog")

// Module-level variables that will be initialized in DOMContentLoaded
let mainDiv;
let cards = [];
let selectedIndex = 0;
let ordersState = [];
let csrftoken = '';
let freezeDeletion = false;
let availabilityDialog;
let autoDoneTimeout;
let autoCollectTimeout;

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/orders/`;
    
    ordersSocket = new WebSocket(wsUrl);
    
    ordersSocket.onopen = function(e) {
        console.log('WebSocket connected for orders');
        reconnectAttempts = 0;
    };
    
    ordersSocket.onmessage = function(e) {
        const data = JSON.parse(e.data);
        handleWebSocketMessage(data);
    };
    
    ordersSocket.onclose = function(e) {
        console.log('WebSocket closed. Attempting to reconnect...');
        if (reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts++;
            setTimeout(connectWebSocket, reconnectDelay);
        }
    };
    
    ordersSocket.onerror = function(e) {
        console.error('WebSocket error:', e);
    };
}

function handleWebSocketMessage(data) {
    if (data.type === 'initial_orders') {
        // Initial orders received on connection - filter and update state
        const filteredOrders = data.orders.filter(order => shouldShowOrder(order));
        ordersState = filteredOrders;
        processInitialOrders(filteredOrders);
    } else if (data.type === 'order_created') {
        // New order created
        if (data.order && shouldShowOrder(data.order)) {
            if (!ordersState.some(o => o.order_id === data.order.order_id)) {
                ordersState.push(data.order);
                appendOrder(data.order);
            }
        }
    } else if (data.type === 'order_updated') {
        // Order updated
        if (data.order) {
            const existingIndex = ordersState.findIndex(o => o.order_id === data.order.order_id);
            if (existingIndex !== -1) {
                ordersState[existingIndex] = data.order;
                updateExistingOrder(data.order);
            } else if (shouldShowOrder(data.order)) {
                ordersState.push(data.order);
                appendOrder(data.order);
            }
        }
    } else if (data.type === 'order_deleted') {
        // Order deleted
        const orderToRemove = ordersState.find(o => o.order_id === data.order_id);
        if (orderToRemove) {
            removeOrder(orderToRemove);
            ordersState = ordersState.filter(o => o.order_id !== data.order_id);
        }
    }
}

function shouldShowOrder(order) {
    // Check if order should be shown based on current filters
    if (!order || order.picked_up) return false;
    
    if (orderFilters.kitchen && order.kitchen_status <= 2 && order.kitchen_status !== 4) return true;
    if (orderFilters.bar && order.bar_status <= 2 && order.bar_status !== 4) return true;
    if (orderFilters.gng && order.gng_status <= 2 && order.gng_status !== 4) return true;
    
    return false;
}

function processInitialOrders(orders) {
    // Update existing cards and add new ones
    orders.forEach(order => {
        const existingCard = document.querySelector(`[data-order-id="${order.order_id}"]`);
        if (existingCard) {
            updateExistingOrder(order);
        }
    });
}

function updateExistingOrder(order) {
    const card = cards.find(c => parseInt(c.dataset.orderId) === order.order_id);
    if (card) {
        card.dataset.kitchenStatus = order.kitchen_status;
        card.dataset.barStatus = order.bar_status;
        card.dataset.gngStatus = order.gng_status;
        updateColors(card);
        
        // Check if order should be removed (all stations done or picked up)
        if (!shouldShowOrder(order)) {
            removeOrder(order);
        }
    }
}

function appendOrder(data) {
    const orderId = data.order_id;
    const existingOrder = document.querySelector(`[data-order-id="${orderId}"]`)
    
    if (existingOrder) {
        return;
    }
    
    if (!cards.length) {
        mainDiv.innerHTML = '';
        selectedIndex = 0;
    }
    const newOrder = document.createElement("div");
    newOrder.className = `order ${!cards.length ? "selected" : ""}`;
    newOrder.dataset.orderId = orderId;
    newOrder.dataset.channel = data.channel;
    newOrder.dataset.paymentId = data.payment_id;
    newOrder.dataset.kitchenStatus = data.kitchen_status;
    newOrder.dataset.barStatus = data.bar_status;
    newOrder.dataset.gngStatus = data.gng_status;
    let channel;
    if (data.channel == "store") {
        channel = '<span class="material-symbols-outlined">storefront</span> ' + gettext('In-person')
    } else if (data.channel == "web") {
        channel = `<span class="material-symbols-outlined">shopping_cart_checkout</span> ` + gettext("Online pick-up") +
                    `<span class="material-symbols-outlined">call</span> ${data.phone}`
    } else if (data.channel == "delivery") {
        channel = `<span class="material-symbols-outlined">local_shipping</span> ` + gettext("Delivery") +
                    `<span class="material-symbols-outlined">call</span> ${data.phone}`
    }
    const progresses = document.createElement("div");
    progresses.classList.add("progresses")
    if (data.kitchen_status != 4) {
        const statusStack = document.createElement("div");
        statusStack.classList.add("kitchen-progress", "progress-stack")
        statusStack.innerHTML = `<span class="material-symbols-outlined">restaurant</span>
                                <div class="hourglass">
                                    <span class="material-symbols-outlined">hourglass_bottom</span>
                                    <span class="material-symbols-outlined">hourglass_top</span>
                                </div>
                                <span class="material-symbols-outlined check">check</span>
                                <span class="material-symbols-outlined done_all">done_all</span>`
        progresses.appendChild(statusStack)
    }
    if (data.bar_status != 4) {
        const statusStack = document.createElement("div");
        statusStack.classList.add("bar-progress", "progress-stack")
        statusStack.innerHTML = `<span class="material-symbols-outlined">local_cafe</span>
        <div class="hourglass">
        <span class="material-symbols-outlined">hourglass_bottom</span>
        <span class="material-symbols-outlined">hourglass_top</span>
                                </div>
                                <span class="material-symbols-outlined check">check</span>
                                <span class="material-symbols-outlined done_all">done_all</span>`
        progresses.appendChild(statusStack)
    }
    if (data.gng_status != 4) {
        const statusStack = document.createElement("div");
        statusStack.classList.add("gng-progress", "progress-stack")
        statusStack.innerHTML = `<span class="material-symbols-outlined">kitchen</span>
                                <div class="hourglass">
                                    <span class="material-symbols-outlined">hourglass_bottom</span>
                                    <span class="material-symbols-outlined">hourglass_top</span>
                                </div>
                                <span class="material-symbols-outlined check">check</span>
                                <span class="material-symbols-outlined done_all">done_all</span>`
        progresses.appendChild(statusStack)
    }
    newOrder.innerHTML = `<div class="summary">
                                <h2>${data.name ? data.name : gettext("No name")}</h2>                                    
                                <div class="name-time">
                                    <span>Order #${orderId}</span>
                                    <span data-timestamp="${data.start_time}" class="timestamp">
                                        ${gettext("Prep time")}: <span>${data.start_time}</span>
                                    </span>
                                </div>
                            </div>
                            <div>
                                <h3>
                                    ${channel}
                                </h3>
                                ${progresses.outerHTML}
                                <h3>
                                    ${data.to_go_order ? "<span class='material-symbols-outlined'>takeout_dining</span> " + gettext("Order to-go") : "<span class='material-symbols-outlined'>restaurant</span> " + gettext("Order for here")}
                                </h3>
                                <ul id="order${data.order_id}ul">
                                    
                                </ul>
                                ${data.special_instructions ? '<h3>' + gettext("Special instructions") + ':</h3><p>' + data.special_instructions + '</p>' : ''}
                            </div>`
    newOrder.addEventListener("click", e => {
        updateSelection(cards.findIndex(cd => cd === e.currentTarget))
    })
    attachSwipability(newOrder)
    mainDiv.appendChild(newOrder)
    trackTime(newOrder.querySelector(".timestamp"))
    const list = document.querySelector(`#order${data.order_id}ul`);
    for (const dish of data.dishes) {
        if (filters.includes(dish.station)) {
            const item = document.createElement("li");
            item.innerHTML = `${dish.quantity} X ${dish.name}`;
            list.appendChild(item);
        }
    }
    cards = [...document.querySelectorAll(".order")];
    checkActiveOrders();
}

function removeOrder(data) {
    const orderId = data.order_id;
    const existingOrder = document.querySelector(`[data-order-id="${orderId}"]`)
    try {
        mainDiv.removeChild(existingOrder)
    } catch {}
    cards = [...document.querySelectorAll(".order")];
    updateSelection(selectedIndex);
    checkActiveOrders()
}

function updateColors(card) {
    let progressState;
    if (filters.every(filter => ["2", "4"].includes(card.dataset[`${filter}Status`]))) {
        progressState = 3;
    } else if (pendingApprovalSelf(card)) {
        progressState = 0;
    } else if (pendingApprovalOtherStations(card)) {
        progressState = 1;
    } else if (filters.some(filter => ["1"].includes(card.dataset[`${filter}Status`]))) {
        progressState = 2;
    }
    card.dataset.progressState = progressState;
}

function pendingApprovalSelf(card) {
    const stationStatusFields = {};
    stations.forEach(station => {
        stationStatusFields[station] = card.dataset[`${station}Status`];
    });

    // Check stations covered by filters
    for (const [station, status] of Object.entries(stationStatusFields)) {
        if (filters.includes(station) && status == 0) { // Pending approval
            return true;
        }
    }

    return false;
}

function pendingApprovalOtherStations(card) {
    const stationStatusFields = {};
    stations.forEach(station => {
        stationStatusFields[station] = card.dataset[`${station}Status`];
    });

    // Check stations not covered by filters
    for (const [station, status] of Object.entries(stationStatusFields)) {
        if (!filters.includes(station) && status == 0) { // Pending approval
            return true;
        }
    }

    return false;
}

function approveOrder(orderId, approved, rejection = undefined) {
    console.log("approve order")
    fetch(window.location.href, {
        headers:{
            "X-CSRFToken": csrftoken,
            "Content-Type": "application/json"
        },
        method:'POST',
        body: JSON.stringify({
            orderId:orderId,
            action: approved ? "approve" : "delete",
            filters:filters,
            rejection: rejection
        })
    })
    .then(response => response.json())
    .then(data => {
        console.log(data)
        if (data.payment_id && data.all_approved) {
            fetch(approveOrderLink, {
                headers:{
                    "X-CSRFToken": csrftoken,
                    "Content-Type": "application/json"
                },
                method:data.action === "delete" ? 'DELETE' : 'POST',
                body: JSON.stringify({
                    payment_id:data.payment_id
                })
            })
            .then(response => {
                if (response.ok) {
                    processOrderResponse(data);
                }
            })
        } else {
            processOrderResponse(data);
        }
        if (cards[selectedIndex]) {
            cards[selectedIndex].querySelector(".timestamp").setAttribute("data-last-interaction", new Date().toISOString())
        }
    })

    function processOrderResponse(data) {
        if (data.action === "delete") {
            if (cards[selectedIndex]) {
                cards[selectedIndex].classList.add("disappear");
                setTimeout(() => {
                    try {
                        document.querySelector("#markings").removeChild(cards[selectedIndex]);
                    } catch {}
                    cards = [...document.querySelectorAll(".order")];
                    selectedIndex = 0;
                    updateSelection(selectedIndex);
                    freezeDeletion = false;
                    checkActiveOrders();
                }, 400);
            }
        } else if (data.action === "approve") {
            if (cards[selectedIndex]) {
                filters.forEach(filter => {cards[selectedIndex].dataset[`${filter}Status`] = 1})
            }
        }
        checkActiveOrders()
        if (cards[selectedIndex]) {
            updateColors(cards[selectedIndex])
        }
    }
}

function markOrderDone(orderId) {
    const card = cards.find(card => card.dataset.orderId == orderId);
    if (!card) return;
    
    const orderDone = stations.every(filter => ["2", "4"].includes(card.dataset[`${filter}Status`]));
    if (orderDone && card.dataset.channel === "delivery") {return}
    freezeDeletion = true;
    if (orderDone) {
        fetch(window.location.href, {
            headers:{
                "X-CSRFToken": csrftoken,
                "Content-Type": "application/json"
            },
            method:'DELETE',
            body: JSON.stringify({
                orderId:orderId
            })
        }).then(response => {
            if (response.ok) {
                card.classList.add("disappear");
                setTimeout(() => {
                    try {
                        document.querySelector("#markings").removeChild(card)
                    } catch {}
                    cards = [...document.querySelectorAll(".order")];
                    selectedIndex = 0;
                    updateSelection(selectedIndex);
                    freezeDeletion = false;
                    checkActiveOrders();
                }, 400);
            }
        })
    } else if (
        !pendingApprovalSelf(card)
        &&
        filters.every(filter => ["1", "4"].includes(card.dataset[`${filter}Status`]))
        &&
        !pendingApprovalOtherStations(card)
    ) {
        fetch(window.location.href, {
            headers:{
                "X-CSRFToken": csrftoken,
                "Content-Type": "application/json"
            },
            method:'PUT',
            body: JSON.stringify({
                orderId:orderId,
                filters:filters
            })
        }).then(response => {
            if (response.ok) {
                filters.forEach(filter => {card.dataset[`${filter}Status`] = 2})
                setTimeout(() => {
                    freezeDeletion = false;
                }, 400);
                updateColors(card)
            }
        })
    } else {
        const icons = shakeOthers(card)
        setTimeout(() => {
            freezeDeletion = false;
            icons.forEach(icon => {icon.classList.remove("shake")})
        }, 2000);
    }
    card.querySelector(".timestamp").setAttribute("data-last-interaction", new Date().toISOString())
}

function shakeOthers(orderCard){
    const icons = []
    const otherFilters = ["kitchen", "bar", "gng"].filter(n => !filters.includes(n))
    otherFilters.forEach(filter => {
        const icon = orderCard.querySelector(`.progress-stack.${filter}-progress span:first-child`)
        if (icon) {
            icons.push(icon)
        }
    })
    icons.forEach(icon => {icon.classList.add("shake")})
    return icons
}

function updateSelection(newIndex) {
    if (newIndex < cards.length && newIndex >= 0) {
        if (cards[selectedIndex]) {
            cards[selectedIndex].classList.remove('selected');
        }
        cards[newIndex].classList.add('selected');
        const node = document.querySelector(".selected")
        if (node) {
            node.scrollIntoView({ behavior: 'smooth' });
        }
        selectedIndex = newIndex;
    }
}

function trackTime(node) {
    var startTime = new Date(node.getAttribute('data-timestamp'));

    function updateCounter() {
        var now = new Date();
        var differenceInSeconds = Math.floor((now - startTime) / 1000);

        var minutes = Math.floor(differenceInSeconds / 60);
        var seconds = differenceInSeconds % 60;

        // Formatting minutes and seconds to always have two digits
        minutes = minutes.toString().padStart(2, '0');
        seconds = seconds.toString().padStart(2, '0');

        if (node.children[0]) {
            node.children[0].innerHTML = minutes + ':' + seconds;
        }
    }

    setInterval(updateCounter, 1000);
}

function checkActiveOrders() {
    if (!mainDiv) return;
    if (!mainDiv.querySelector(".order") && !mainDiv.querySelector("h1")) {
        const noOrderSign = document.createElement("h1");
        noOrderSign.textContent = gettext("No new orders");
        noOrderSign.style = "text-align: center;"
        mainDiv.appendChild(noOrderSign);
    }
}

function attachSwipability(card) {
    let startX;

    function handleStart(e) {
        if (card.classList.contains('selected')) {
            startX = e.touches ? e.touches[0].clientX : e.clientX;
            card.style.transition = 'none'; // Disable transition during swipe
        }
    }

    function handleMove(e) {
        if (card.classList.contains('selected')) {
            const currentX = e.touches ? e.touches[0].clientX : e.clientX;
            const deltaX = currentX - startX;
    
            // Move the element on the screen, but limit it to half the screen width
            const maxDeltaX = window.innerWidth / 2;
            card.style.transform = `translateX(${Math.max(-maxDeltaX, Math.min(deltaX, maxDeltaX))}px)`;
        }
    }

    function handleEnd() {
        if (card.classList.contains('selected')) {
            const currentX = parseFloat(card.style.transform.replace('translateX(', '').replace('px)', ''));
            const maxDeltaX = window.innerWidth / 2;
            if (Math.abs(currentX) > maxDeltaX / 2) {
                openOrderManagement();
            }
            card.style.transition = 'transform 0.5s ease-in-out';
            card.style.transform = 'translateX(0px)'; // Bring it back to its original position
        }
    }

    card.addEventListener('touchstart', handleStart);
    card.addEventListener('touchmove', handleMove);
    card.addEventListener('touchend', handleEnd);
}

function openOrderManagement() {
    if (!cards[selectedIndex]) return;
    
    const orderManagementApproveButton = document.querySelector("#approve-order-button");
    const orderManagementRejectButton = document.querySelector("#reject-order-button");
    const orderManagementDoneButton = document.querySelector("#mark-order-done-button");
    const orderManagementCollectedButton = document.querySelector("#mark-order-collected-button");
    
    const state = parseInt(cards[selectedIndex].dataset.progressState);
    if (state === 0) {
        orderManagementApproveButton.disabled = false;
        orderManagementRejectButton.disabled = false;
        orderManagementDoneButton.disabled = true;
        orderManagementCollectedButton.disabled = true;
    } else if (state === 1) {
        orderManagementApproveButton.disabled = true;
        orderManagementRejectButton.disabled = true;
        orderManagementDoneButton.disabled = true;
        orderManagementCollectedButton.disabled = true;
    } else if (state === 2) {
        orderManagementApproveButton.disabled = true;
        orderManagementRejectButton.disabled = true;
        orderManagementDoneButton.disabled = false;
        orderManagementCollectedButton.disabled = true;
    } else if (state === 3) {
        orderManagementApproveButton.disabled = true;
        orderManagementRejectButton.disabled = true;
        orderManagementDoneButton.disabled = true;
        orderManagementCollectedButton.disabled = false;
    }
    orderManagementDialog.showModal();
}

document.addEventListener("DOMContentLoaded", () => {
    try {document.querySelector(".order").classList.add("selected");} catch (error) {}

    const params = new URLSearchParams(document.location.search);
    autoDoneTimeout = params.get("auto-done");
    const autoDoneInput = document.querySelector("#auto-done-input");
    if (autoDoneInput) autoDoneInput.value = autoDoneTimeout;
    if (autoDoneTimeout) {
        autoDoneTimeout = parseInt(autoDoneTimeout) * 60000;
    }
    autoCollectTimeout = params.get("auto-collect");
    const autoCollectInput = document.querySelector("#auto-collect-input");
    if (autoCollectInput) autoCollectInput.value = autoCollectTimeout;
    if (autoCollectTimeout) {
        autoCollectTimeout = parseInt(autoCollectTimeout) * 60000;
    }
    
    mainDiv = document.querySelector("#markings");
    cards = [...document.querySelectorAll(".order")];
    selectedIndex = cards.findIndex(element => element.classList.contains('selected'));
    if (selectedIndex < 0) selectedIndex = 0;

    checkActiveOrders();

    // Initialize WebSocket connection after DOM is ready
    connectWebSocket();

    // Auto-done timer
    if (autoDoneTimeout) {
        setInterval(() => {
            const now = new Date();
            cards.forEach(card => {
                if (card.dataset.progressState == 2) {
                    const startTime = new Date(card.querySelector(".timestamp").getAttribute('data-last-interaction') || card.querySelector(".timestamp").getAttribute('data-timestamp'));
                    if (now - startTime >= autoDoneTimeout) {
                        markOrderDone(card.dataset.orderId);
                    }
                }
            });
        }, 60000);
    }

    // Auto-collect timer
    if (autoCollectTimeout) {
        setInterval(() => {
            const now = new Date();
            cards.forEach(card => {
                if (card.dataset.progressState == 3) {
                    const startTime = new Date(card.querySelector(".timestamp").getAttribute('data-last-interaction') || card.querySelector(".timestamp").getAttribute('data-timestamp'));
                    if (now - startTime >= autoCollectTimeout) {
                        markOrderDone(card.dataset.orderId);
                    }
                }
            });
        }, 60000);
    }

    cards.forEach(card => {
        card.addEventListener("click", e => {
            updateSelection(cards.findIndex(cd => cd === e.currentTarget))
        })
        updateColors(card)
        attachSwipability(card)
    })

    document.querySelectorAll(".timestamp").forEach(node => trackTime(node))

    const csrfTokenInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    csrftoken = csrfTokenInput ? csrfTokenInput.value : '';
    availabilityDialog = document.querySelector("#availability");
    
    window.addEventListener('keydown', (e) => {
        if (!rejectOrderDialog.open && !preferencesDialog.open) { // Normal key bindings
            if (e.key === 'ArrowDown' || e.key == "2") {
                updateSelection(selectedIndex + 1);
            } else if (e.key === 'ArrowUp' || e.key === "8") {
                updateSelection(selectedIndex - 1);
            } else if ((e.key === "Enter" || e.key === "5") && !freezeDeletion) {
                if (cards[selectedIndex] && filters.every(filter => ["1", "2", "4"].includes(cards[selectedIndex].dataset[`${filter}Status`]))) {
                    markOrderDone(cards[selectedIndex].dataset.orderId)
                } else if (cards[selectedIndex] && pendingApprovalSelf(cards[selectedIndex])) {
                    approveOrder(cards[selectedIndex].dataset.orderId, true)
                }
            } else if (e.key === "Backspace" || e.key === "Delete") {
                rejectOrderDialog.showModal()
            } else if (e.key === "1") {
                if (availabilityDialog.open) {
                    availabilityDialog.querySelector("iframe").contentWindow.postMessage('closeDialog', '*');
                } else {
                    availabilityDialog.showModal()
                }
            }
        } else { // If the reject dialog is open
            if (e.key >= "1" && e.key <= "9" && document.activeElement.name !== "reason-extra") {
                const checkbox = rejectOrderDialog.querySelector(`input[type='checkbox'][data-key='${e.key}']`)
                if (checkbox) {
                    checkbox.checked = !checkbox.checked
                }
            }
        }
    });

    window.addEventListener('message', event => {
        if (event.data === 'closeDialog' && event.origin === window.location.origin) {
            availabilityDialog.close();
        }
    });

    rejectOrderDialog.querySelector("form").addEventListener("submit", e => {
        e.preventDefault();
        const checkboxes = rejectOrderDialog.querySelectorAll("input[type='checkbox']");
        const isChecked = [...checkboxes].some(checkbox => checkbox.checked);

        if (!isChecked) {
            alert(gettext("Please select at least one rejection reason."));
            return
        }
        const rejection = {
            reasons: [...document.querySelectorAll("#reject-reason input[type='checkbox']:checked")].map(checkbox => checkbox.value),
            reasonExtra: document.querySelector("#reject-reason textarea").value
        }
        approveOrder(cards[selectedIndex].dataset.orderId, false, rejection)
        rejectOrderDialog.close()
    })

    const orderManagementApproveButton = document.querySelector("#approve-order-button")
    const orderManagementRejectButton = document.querySelector("#reject-order-button")
    const orderManagementDoneButton = document.querySelector("#mark-order-done-button")
    const orderManagementCollectedButton = document.querySelector("#mark-order-collected-button")

    orderManagementApproveButton.addEventListener("click", () => {
        approveOrder(cards[selectedIndex].dataset.orderId, true)
    })

    orderManagementRejectButton.addEventListener("click", () => {
        rejectOrderDialog.showModal()
    })

    orderManagementDoneButton.addEventListener("click", () => {
        markOrderDone(cards[selectedIndex].dataset.orderId)
    })

    orderManagementCollectedButton.addEventListener("click", () => {
        markOrderDone(cards[selectedIndex].dataset.orderId)
    })

    orderManagementDialog.querySelectorAll("button").forEach(button => {
        button.addEventListener("click", () => {
            orderManagementDialog.close()
        })
    })
})

class UniqueKeySet {
    constructor(key) {
      this.key = key; // Property name to enforce uniqueness on
      this.map = new Map();
    }
  
    add(item) {
        if (item) {
            const keyValue = item[this.key];
            if (!keyValue) {
              throw new Error(`Item must have a unique key property: ${this.key}`);
            }
            this.map.set(keyValue, item); // Replace any existing item with the same key
        }
    }
  
    delete(item) {
      const keyValue = item[this.key];
      return this.map.delete(keyValue);
    }
  
    has(item) {
      const keyValue = item[this.key];
      return this.map.has(keyValue);
    }
  
    get size() {
      return this.map.size;
    }
  
    get values() {
      return Array.from(this.map.values());
    }
}
