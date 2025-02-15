const pollEverySecs = 11; // Put something that's not a factor of 10 to avoid race condition with auto actions. Needs to be fixed later.
const pollEveryMilisecs = pollEverySecs * 1000;

const rejectOrderDialog = document.querySelector("#reject-reason")
const orderManagementDialog = document.querySelector("#order-management")
const preferencesDialog = document.querySelector("#filters-dialog")

document.addEventListener("DOMContentLoaded", () => {
    let prevOrderId;
    try {
        let orders = document.querySelectorAll(".order")
        prevOrderId = parseInt(orders[orders.length-1].dataset.orderid)
    } catch {}
    try {document.querySelector(".order").classList.add("selected");} catch (error) {}

    const params = new URLSearchParams(document.location.search);
    let autoDoneTimeout = params.get("auto-done");
    document.querySelector("#auto-done-input").value = autoDoneTimeout
    if (autoDoneTimeout) {
        autoDoneTimeout = parseInt(autoDoneTimeout) * 60000
    }
    let autoCollectTimeout = params.get("auto-collect");
    document.querySelector("#auto-collect-input").value = autoCollectTimeout
    if (autoCollectTimeout) {
        autoCollectTimeout = parseInt(autoCollectTimeout) * 60000
    }
    
    const mainDiv = document.querySelector("#markings");
    let cards = document.querySelectorAll(".order");
    cards = [...cards]
    let selectedIndex = cards.findIndex(element => element.classList.contains('selected'));
    // let isFirstLoad = true;

    checkActiveOrders();

    let ordersState;
    getOrdersFirst();
    async function getOrdersFirst() {
        ordersState = await fetchOrders();
    }

    setInterval(async () => {
        const newOrders = await fetchOrders();
        // Remove orders that are in ordersState but not in newOrders
        if (newOrders.length === 0) {
            // If newOrders is empty, remove all orders in ordersState
            ordersState.forEach(order => removeOrder(order));
        } else {
            // Otherwise, remove orders in ordersState that are not in newOrders
            ordersState.forEach(order => {
                if (!newOrders.some(item => item.order_id === order.order_id)) {
                    removeOrder(order);
                }
            });
        }
        // Append new orders to the list
        newOrders.forEach(order => {
            if (!ordersState.some(item => item.order_id === order.order_id)) {
                appendOrder(order);
            }
            const card = cards.find(card => parseInt(card.dataset.orderId) === order.order_id)
            if (card) {
                card.dataset.kitchenStatus = order.kitchen_status
                card.dataset.barStatus = order.bar_status
                card.dataset.gngStatus = order.gng_status
                updateColors(cards.find(card => parseInt(card.dataset.orderId) === order.order_id))
            }
        });
        // Update the ordersState to match the newOrders
        ordersState = newOrders;
    }, pollEveryMilisecs);

    if (autoDoneTimeout) {
        setInterval(() => {
            const now = new Date();
            cards.forEach(card => {
                if (card.dataset.progressState == 2) {
                    console.log(card)
                    const startTime = new Date(card.querySelector(".timestamp").getAttribute('data-last-interaction') || card.querySelector(".timestamp").getAttribute('data-timestamp'));
                    if (now - startTime >= autoDoneTimeout) {
                        markOrderDone(card.dataset.orderId);
                    }
                }
            });
        }, 60000);
    }

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
    
    async function fetchOrders() {
        const data = await fetch(checkOrdersLink);
        const array = await data.json()
        const orders = new UniqueKeySet("order_id")
        if (orderFilters.kitchen) {
            array.filter(item => (item.kitchen_status <= 2 && item.picked_up === false)).forEach(i => {
                orders.add(i)
            });
        }
        if (orderFilters.bar) {
            array.filter(item => (item.bar_status <= 2 && item.picked_up === false)).forEach(i => {
                orders.add(i)
            });;
        }
        if (orderFilters.gng) {
            array.filter(item => (item.gng_status <= 2 && item.picked_up === false)).forEach(i => {
                orders.add(i)
            });
        }
        return orders.values
    }

    function appendOrder(data) {
        const orderId = data.order_id;
        // if (station === "kitchen" && data.kitchen_done) {return} else if (station === "bar" && data.bar_done) {return}
        const existingOrder = document.querySelector(`[data-orderid="${orderId}"]`)
        if (existingOrder) {return}
        // if (prevOrderId && orderId - 1 !== prevOrderId) {
        //     window.location.reload()
        // } else {
        //     prevOrderId = orderId;
        // }
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
            channel = '<span class="material-symbols-outlined">storefront</span> In-person'
        } else if (data.channel == "web") {
            channel = `<span class="material-symbols-outlined">shopping_cart_checkout</span> Online pick-up
                        <span class="material-symbols-outlined">call</span> ${data.phone}`
        } else if (data.channel == "delivery") {
            channel = `<span class="material-symbols-outlined">local_shipping</span> Delivery
                        <span class="material-symbols-outlined">call</span> ${data.phone}`
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
                                    <h2>${data.name ? data.name : "No name"}</h2>                                    
                                    <div class="name-time">
                                        <span>Order #${orderId}</span>
                                        <span data-timestamp="${data.start_time}" class="timestamp">
                                            Prep time: <span>${data.start_time}</span>
                                        </span>
                                    </div>
                                </div>
                                <div>
                                    <h3>
                                        ${channel}
                                    </h3>
                                    ${progresses.outerHTML}
                                    <h3>
                                        ${data.to_go_order ? "<span class='material-symbols-outlined'>takeout_dining</span> Order to-go" : "<span class='material-symbols-outlined'>restaurant</span> Order for here"}
                                    </h3>
                                    <ul id="order${data.order_id}ul">
                                        
                                    </ul>
                                    ${data.special_instructions ? '<h3>Special instructions:</h3><p>'+data.special_instructions+'</p>' : ''}
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
        cards = document.querySelectorAll(".order");
        cards = [...cards]
    };

    function removeOrder(data) {
        const orderId = data.order_id;
        const existingOrder = document.querySelector(`[data-order-id="${orderId}"]`)
        try {
            mainDiv.removeChild(existingOrder)
        } catch {}
        cards = document.querySelectorAll(".order");
        cards = [...cards]
        updateSelection(selectedIndex);
        checkActiveOrders()
    };

    cards.forEach(card => {
        card.addEventListener("click", e => {
            updateSelection(cards.findIndex(cd => cd === e.currentTarget))
        })
        updateColors(card)
    })

    document.querySelectorAll(".timestamp").forEach(node => trackTime(node))

    const csrfTokenInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    const csrftoken = csrfTokenInput ? csrfTokenInput.value : '';
    let freezeDeletion = false;
    const availabilityDialog = document.querySelector("#availability");
    window.addEventListener('keydown', (e) => {
        if (!rejectOrderDialog.open && !preferencesDialog.open) { // Normal key bindings
            if (e.key === 'ArrowDown' || e.key == "2") {
                updateSelection(selectedIndex + 1);
            } else if (e.key === 'ArrowUp' || e.key === "8") {
                updateSelection(selectedIndex - 1);
            } else if ((e.key === "Enter" || e.key === "5") && !freezeDeletion) {
                if (filters.every(filter => ["1", "2", "4"].includes(cards[selectedIndex].dataset[`${filter}Status`]))) {
                    markOrderDone(cards[selectedIndex].dataset.orderId)
                } else if (pendingApprovalSelf(cards[selectedIndex])) {
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

    rejectOrderDialog.querySelector("form").addEventListener("submit", e => {
        e.preventDefault();
        const checkboxes = rejectOrderDialog.querySelectorAll("input[type='checkbox']");
        const isChecked = [...checkboxes].some(checkbox => checkbox.checked);

        if (!isChecked) {
            alert("Please select at least one rejection reason.");
            return
        }
        const rejection = {
            reasons: [...document.querySelectorAll("#reject-reason input[type='checkbox']:checked")].map(checkbox => checkbox.value),
            reasonExtra: document.querySelector("#reject-reason textarea").value
        }
        console.log(rejection)
        approveOrder(cards[selectedIndex].dataset.orderId, false, rejection)
        rejectOrderDialog.close()
    })

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
            cards[selectedIndex].querySelector(".timestamp").setAttribute("data-last-interaction", new Date().toISOString())
        })

        function processOrderResponse(data) {
            if (data.action === "delete") {
                cards[selectedIndex].classList.add("disappear");
                setTimeout(() => {
                    document.querySelector("#markings").removeChild(cards[selectedIndex]);
                    cards = document.querySelectorAll(".order");
                    cards = [...cards];
                    selectedIndex = 0;
                    updateSelection(selectedIndex);
                    freezeDeletion = false;
                    checkActiveOrders();
                }, 400);
            } else if (data.action === "approve") {
                filters.forEach(filter => {cards[selectedIndex].dataset[`${filter}Status`] = 1})
                // if (orderFilters.kitchen) {
                //     cards[selectedIndex].dataset["kitchenStatus"] = 1;
                // }
                // if (orderFilters.bar) {
                //     cards[selectedIndex].dataset["barStatus"] = 1;
                // }
                // if (orderFilters.gng) {
                //     cards[selectedIndex].dataset["gngStatus"] = 1;
                // }
            }
            checkActiveOrders()
            updateColors(cards[selectedIndex])
        }
    }

    function updateColors(card) {
        let progressState;
        if (filters.every(filter => ["2", "4"].includes(card.dataset[`${filter}Status`]))) {
            progressState = 3;
            // card.classList.add("success")
        } else if (pendingApprovalSelf(card)) {
            progressState = 0;
        } else if (pendingApprovalOtherStations(card)) {
            progressState = 1;
        } else if (filters.some(filter => ["1"].includes(card.dataset[`${filter}Status`]))) {
            progressState = 2;
        }
        card.dataset.progressState = progressState;
        // const order = {
        //     kitchen_status: parseInt(card.dataset.kitchenStatus),
        //     bar_status: parseInt(card.dataset.barStatus),
        //     gng_status: parseInt(card.dataset.gngStatus)
        // }
        // card.dataset.pendingHere = pendingApprovalSelf(order)
        // card.dataset.pendingOthers = pendingApprovalOtherStations(order)
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

    function markOrderDone(orderId) {
        const card = cards.find(card => card.dataset.orderId == orderId);
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
                        document.querySelector("#markings").removeChild(card)
                        cards = document.querySelectorAll(".order");
                        cards = [...cards]
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

    function updateSelection(newIndex) {
        if (newIndex < cards.length && newIndex >= 0) {
            cards[selectedIndex].classList.remove('selected');
            cards[newIndex].classList.add('selected');
            const node = document.querySelector(".selected")
            node.scrollIntoView({ behavior: 'smooth' });
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

            node.children[0].innerHTML = minutes + ':' + seconds;
        }

        setInterval(updateCounter, 1000);
    }
    
    function checkActiveOrders() {
        if (!mainDiv.querySelector(".order") && !mainDiv.querySelector("h1")) {
            const noOrderSign = document.createElement("h1");
            noOrderSign.textContent = "No new orders";
            noOrderSign.style = "text-align: center;"
            mainDiv.appendChild(noOrderSign);
        }
    }

    // Functions to swipe away orders

    cards.forEach(card => { attachSwipability(card) })

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

    function openOrderManagement() {
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