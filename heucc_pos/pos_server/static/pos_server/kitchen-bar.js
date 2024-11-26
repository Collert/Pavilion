const pollEverySecs = 10;
const pollEveryMilisecs = pollEverySecs * 1000;

const pathSegments = new URL(window.location.href).pathname.split('/');
const station = pathSegments[pathSegments.length-1];
let prevOrderId;
try {
    let orders = $0.querySelectorAll(".order")
    prevOrderId = parseInt(orders[orders.length-1].dataset.orderid)
} catch {}

document.addEventListener("DOMContentLoaded", () => {
    try {document.querySelector(".order").classList.add("selected");} catch (error) {}

    // setTimeout(() => {
    //     window.location.reload()
    //     // Reload every 10 minutes to reset the nginx connection timeout
    // }, 600000);
    
    const kitchenDiv = document.querySelector("#kitchen");
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
            kitchenDiv.innerHTML = '';
            selectedIndex = 0;
        }
        const newOrder = document.createElement("div");
        newOrder.className = `order ${!cards.length ? "selected" : ""}`;
        newOrder.dataset.orderId = orderId;
        newOrder.dataset.done = false;
        newOrder.dataset.channel = data.channel;
        newOrder.dataset.paymentId = data.payment_id;
        newOrder.dataset.kitchenStatus = data.kitchen_status;
        newOrder.dataset.barStatus = data.bar_status;
        newOrder.dataset.gngStatus = data.gng_status;
        newOrder.dataset.pendingHere = pendingApprovalSelf(data)
        newOrder.dataset.pendingOthers = pendingApprovalOtherStations(data)
        let channel;
        if (data.channel == "store") {
            channel = '<span class="material-symbols-outlined">storefront</span> In-person order'
        } else if (data.channel == "web") {
            channel = `<span class="material-symbols-outlined">shopping_cart_checkout</span> Online pick-up order
                        <span class="material-symbols-outlined">call</span> ${data.phone}`
        } else if (data.channel == "delivery") {
            channel = `<span class="material-symbols-outlined">local_shipping</span> Delivery order
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
                                    <h2>${data.table ? data.table : "No name"}</h2>                                    
                                    <div class="name-time">
                                        <span>Order #${orderId}</span>
                                        <span data-timestamp="${data.timestamp}" class="timestamp">
                                            Prep time: <span>${data.timestamp}</span>
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
        kitchenDiv.appendChild(newOrder)
        trackTime(newOrder.querySelector(".timestamp"))
        const list = document.querySelector(`#order${data.order_id}ul`);
        for (const dish of data.dishes) {
            if (filters.includes(dish.station) || data.channel !== "store") {
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
            kitchenDiv.removeChild(existingOrder)
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
    })

    document.querySelectorAll(".timestamp").forEach(node => trackTime(node))

    const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
    let freezeDeletion = false;
    const availabilityDialog = document.querySelector("#availability");
    window.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key == "2") {
            updateSelection(selectedIndex + 1);
        } else if (e.key === 'ArrowUp' || e.key === "8") {
            updateSelection(selectedIndex - 1);
        } else if ((e.key === "Enter" || e.key === "5") && !freezeDeletion) {
            if (filters.every(filter => ["1", "2", "4"].includes(cards[selectedIndex].dataset[`${filter}Status`]))) {
                markOrderDone()
            } else if (pendingApprovalSelf({
                kitchen_status: parseInt(cards[selectedIndex].dataset.kitchenStatus),
                bar_status: parseInt(cards[selectedIndex].dataset.barStatus),
                gng_status: parseInt(cards[selectedIndex].dataset.gngStatus)
            })) {
                approveOrder(true)
            }
        } else if (e.key === "Backspace" || e.key === "Delete") {
            approveOrder(false)
        } else if (e.key === "1") {
            if (availabilityDialog.open) {
                availabilityDialog.querySelector("iframe").contentWindow.postMessage('closeDialog', '*');
            } else {
                availabilityDialog.showModal()
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

    function approveOrder(approved) {
        const orderId = cards[selectedIndex].dataset.orderId;
        fetch(window.location.href, {
            headers:{
                "X-CSRFToken": csrftoken,
                "Content-Type": "application/json"
            },
            method:'POST',
            body: JSON.stringify({
                orderId:orderId,
                action: approved ? "approve" : "delete",
                filters:filters
            })
        })
        .then(response => response.json())
        .then(data => {
            console.log(data.payment_id)
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
                    if (data.action === "delete") {
                        cards[selectedIndex].classList.add("disappear");
                        setTimeout(() => {
                            document.querySelector("#kitchen").removeChild(cards[selectedIndex])
                            cards = document.querySelectorAll(".order");
                            cards = [...cards]
                            selectedIndex = 0;
                            updateSelection(selectedIndex);
                            freezeDeletion = false;
                            checkActiveOrders();
                        }, 400);
                    } else if (data.action === "approve") {
                        if (orderFilters.kitchen) {
                            cards[selectedIndex].dataset.kitchenStatus = 1;
                        }
                        if (orderFilters.bar) {
                            cards[selectedIndex].dataset.barStatus = 1;
                        }
                        if (orderFilters.gng) {
                            cards[selectedIndex].dataset.gngStatus = 1;
                        }
                    }
                }
            })
        })
    }

    function updateColors(card) {
        if (filters.every(filter => ["2", "4"].includes(card.dataset[`${filter}Status`]))) {
            card.classList.add("success")
        } else {
            card.classList.remove("success")
        }
        const order = {
            kitchen_status: parseInt(card.dataset.kitchenStatus),
            bar_status: parseInt(card.dataset.barStatus),
            gng_status: parseInt(card.dataset.gngStatus)
        }
        card.dataset.pendingHere = pendingApprovalSelf(order)
        card.dataset.pendingOthers = pendingApprovalOtherStations(order)
    }

    function pendingApprovalSelf(order) {
        // Map the station status fields in the order object
        const stationStatusFields = {
            kitchen: order.kitchen_status,
            bar: order.bar_status,
            gng: order.gng_status
        };
    
        // Check stations covered by filters
        for (const [station, status] of Object.entries(stationStatusFields)) {
            if (filters.includes(station) && status === 0) { // Pending approval
                return true;
            }
        }
    
        return false;
    }

    function pendingApprovalOtherStations(order) {
        // Map the station status fields in the order object
        const stationStatusFields = {
            kitchen: order.kitchen_status,
            bar: order.bar_status,
            gng: order.gng_status
        };
    
        // Check stations not covered by filters
        for (const [station, status] of Object.entries(stationStatusFields)) {
            if (!filters.includes(station) && status === 0) { // Pending approval
                return true;
            }
        }
    
        return false;
    }

    function markOrderDone() {
        const orderId = cards[selectedIndex].dataset.orderId;
        const orderDone = ["kitchen", "bar", "gng"].every(filter => ["2", "4"].includes(cards[selectedIndex].dataset[`${filter}Status`]));
        if (orderDone && cards[selectedIndex].dataset.channel === "delivery") {return}
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
                    cards[selectedIndex].classList.add("disappear");
                    setTimeout(() => {
                        document.querySelector("#kitchen").removeChild(cards[selectedIndex])
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
            !pendingApprovalSelf({
            kitchen_status: parseInt(cards[selectedIndex].dataset.kitchenStatus),
            bar_status: parseInt(cards[selectedIndex].dataset.barStatus),
            gng_status: parseInt(cards[selectedIndex].dataset.gngStatus)
            })
            &&
            filters.every(filter => ["1", "4"].includes(cards[selectedIndex].dataset[`${filter}Status`]))
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
                    filters.forEach(filter => {cards[selectedIndex].dataset[`${filter}Status`] = 2})
                    setTimeout(() => {
                        freezeDeletion = false;
                    }, 400);
                }
            })
        } else {
            const icons = shakeOthers(cards[selectedIndex])
            setTimeout(() => {
                freezeDeletion = false;
                icons.forEach(icon => {icon.classList.remove("shake")})
            }, 2000);
        }
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
        if (!kitchenDiv.querySelector(".order") && !kitchenDiv.querySelector("h1")) {
            const noOrderSign = document.createElement("h1");
            noOrderSign.textContent = "No new orders";
            noOrderSign.style = "text-align: center;"
            kitchenDiv.appendChild(noOrderSign);
        }
    }

    function checkRightDishes(dishes) {
        let hasRightDishes = false;
        for (const dish of dishes) {
            if (dish.station === station) {
                hasRightDishes = true
                break
            }
        }
        return hasRightDishes;
    }

    // Functions to swipe away orders

    cards.forEach(card => { attachSwipability(card) })

    function attachSwipability(card) {
        let startX;

        function handleStart(e) {
            if (card.classList.contains('selected')) {
                startX = e.touches ? e.touches[0].clientX : e.clientX;
            }
        }
    
        function handleMove(e) {
            if (card.classList.contains('selected')) {
                const currentX = e.touches ? e.touches[0].clientX : e.clientX;
                const deltaX = currentX - startX;
        
                // Add your logic to move the element on the screen
                card.style.transform = `translateX(${deltaX}px)`;
            }
        }

        function handleEnd() {
            if (card.classList.contains('selected')) {
                const screenWidth = window.innerWidth;
                const threshold = screenWidth * 0.5;
                const deltaX = parseInt(card.style.transform.replace('translateX(', '').replace('px)', ''), 10);
    
                if (Math.abs(deltaX) > threshold) {
                    card.style.transition = 'transform 0.5s ease-in-out';
                    card.style.transform = `translateX(${deltaX > 0 ? screenWidth : -screenWidth}px)`;
                    setTimeout(() => {
                        // card.style.display = 'none';
                        markOrderDone()
                    }, 500);
                } else {
                    card.style.transition = 'transform 0.5s ease-in-out';
                    card.style.transform = 'translateX(0px)';
                }
            }
        }

        // I don't think the mouse ones are needed but they are here just in case

        card.addEventListener('touchstart', handleStart);
        // card.addEventListener('mousedown', handleStart);

        card.addEventListener('touchmove', handleMove);
        // card.addEventListener('mousemove', handleMove);

        card.addEventListener('touchend', handleEnd);
        // card.addEventListener('mouseup', handleEnd);

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