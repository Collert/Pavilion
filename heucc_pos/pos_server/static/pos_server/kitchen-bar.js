const pathSegments = new URL(window.location.href).pathname.split('/');
const station = pathSegments[pathSegments.length-1];

console.log(station)

document.addEventListener("DOMContentLoaded", () => {
    try {document.querySelector(".order").classList.add("selected");} catch (error) {}
    
    console.log(eventSource)
    const kitchenDiv = document.querySelector("#kitchen");
    let cards = document.querySelectorAll(".order");
    cards = [...cards]
    let selectedIndex = cards.findIndex(element => element.classList.contains('selected'));
    let isFirstLoad = true;
    setTimeout(() => {
        isFirstLoad = false;
    }, 1000);
    eventSource.onmessage = function(e) {
        console.log(e)
        if (isFirstLoad) {
            return;
        }
        const data = JSON.parse(e.data);
        console.log(data)
        if (!checkRightDishes(data.dishes)) {return}
        if (!cards.length) {
            kitchenDiv.innerHTML = '';
            selectedIndex = 0;
        }
        const newOrder = document.createElement("div");
        const orderId = data.order_id;
        newOrder.className = `order ${!cards.length ? "selected" : ""}`;
        newOrder.dataset.orderid = orderId;
        newOrder.innerHTML = `<div class="summary">
                                    <h2>Order #${data.order_id}</h2>
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
        kitchenDiv.appendChild(newOrder)
        trackTime(newOrder.querySelector(".timestamp"))
        const list = document.querySelector(`#order${data.order_id}ul`);
        for (const dish of data.dishes) {
            console.log(dish)
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
    document.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key == "2") {
            updateSelection(selectedIndex + 1);
        } else if (e.key === 'ArrowUp' || e.key === "8") {
            updateSelection(selectedIndex - 1);
        } else if ((e.key === "Enter" || e.key === "5") && !freezeDeletion) {
            const orderId = cards[selectedIndex].dataset.orderid;
            freezeDeletion = true;
            fetch(`{% url '${station}' %}`,{
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
                    }, 400);
                }
            })
        }
    });

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
})