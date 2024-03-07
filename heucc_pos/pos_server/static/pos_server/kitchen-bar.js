const pathSegments = new URL(window.location.href).pathname.split('/');
const station = pathSegments[pathSegments.length-1];

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
    let isFirstLoad = true;
    
    checkActiveOrders();
    setTimeout(() => {
        isFirstLoad = false;
    }, 1000);
    eventSource.onmessage = function(e) {
        if (isFirstLoad) {
            return;
        }
        const data = JSON.parse(e.data);
        console.log(data)
        const orderId = data.order_id;
        if (station === "kitchen" && data.kitchen_done) {return} else if (station === "bar" && data.bar_done) {return}
        const existingOrder = document.querySelector(`[data-orderid="${orderId}"]`)
        if (existingOrder) {return}
        // if (!checkRightDishes(data.dishes)) {return}
        if (!cards.length) {
            kitchenDiv.innerHTML = '';
            selectedIndex = 0;
        }
        const newOrder = document.createElement("div");
        newOrder.className = `order ${!cards.length ? "selected" : ""}`;
        newOrder.dataset.orderid = orderId;
        newOrder.dataset.done = false;
        newOrder.innerHTML = `<div class="summary">
                                    <h2>Order #${orderId}</h2>
                                    <div class="name-time">
                                        <span>${data.table ? `Name: ${data.table}` : "No name"}</span>
                                        <span data-timestamp="${data.timestamp}" class="timestamp">
                                            Prep time: <span>${data.timestamp}</span>
                                        </span>
                                    </div>
                                </div>
                                <div>
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
            if (dish.station === station) {
                const item = document.createElement("li");
                item.innerHTML = `${dish.quantity} X ${dish.name}`;
                list.appendChild(item);
            }
        }
        cards = document.querySelectorAll(".order");
        cards = [...cards]
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
            markOrderDone()
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

    function markOrderDone() {
        const orderDone = cards[selectedIndex].dataset.done === "True"; // Capital T because that's how they come from python :(
        const orderId = cards[selectedIndex].dataset.orderid;
        freezeDeletion = true;
        if (orderDone || (!orderDone && station === "bar")) {
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
        } else {
            fetch(window.location.href, {
                headers:{
                    "X-CSRFToken": csrftoken,
                    "Content-Type": "application/json"
                },
                method:'PUT',
                body: JSON.stringify({
                    orderId:orderId
                })
            }).then(response => {
                if (response.ok) {
                    cards[selectedIndex].classList.add("success");
                    cards[selectedIndex].dataset.done = "True"; // Capital T because that's how they come from python :(
                    setTimeout(() => {
                        freezeDeletion = false;
                    }, 400);
                }
            })
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
        if (!kitchenDiv.querySelector(".order")) {
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