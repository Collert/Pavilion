import setClock from "./clock.js";
// import weatherIcons from "./weatherCodes.json" assert { type: "json" };

const inProgressCol = document.querySelector("#col-1 div");
const readyCol = document.querySelector("#col-2 div");
const pollEverySecs = 5;
const pollEveryMilisecs = pollEverySecs * 1000;

const namedAnnouncements = [
    "${name}, your order is ready to go!",
    "${name}, your meal is all set!",
    "${name}, your food is up and ready!",
    "${name}, grab your order, it's ready!",
    "${name}, your feast awaits. It's ready!",
    "${name}, ready to eat? Your order is!",
    "${name}, hot and fresh! Your order is ready.",
    "${name}, your order is done. Come and get it!",
    "${name}, ding! Your food's ready to be picked up.",
    "${name}, it's time! Your order is ready.",
    "${name}, your takeaway is ready for pickup!",
    "${name}, good news! Your order is ready.",
    "${name}, your order is fresh off the kitchen. Ready!",
    "${name}, your cravings are ready to be satisfied!",
    "${name}, ready, set, eat! Your order is ready.",
    "${name}, your order made with love, is ready.",
    "${name}, chef's done! Your order is ready.",
    "${name}, your gourmet experience is ready!",
    "${name}, your food's hot and ready to go!",
    "${name}, scoop it up! Your order is ready."
]

const noNameAnnouncements = [
    "Order #${name}, featuring your delicious ${dishName}, is ready to go!",
    "Heads up! Order #${name} with the tasty ${dishName} is ready.",
    "Your order (#${name}), starring the ${dishName}, is now ready!",
    "Good news! Your ${dishName} in order #${name} is ready for pickup.",
    "Order #${name}, including your chosen ${dishName}, is all set and ready!",
    "Get excited! Your ${dishName} in order #${name} is hot and ready.",
    "Ready for pickup: Order #${name} with the amazing ${dishName}.",
    "It's time: Order #${name}, featuring ${dishName}, is ready to be enjoyed.",
    "Order #${name}, with the scrumptious ${dishName}, is now ready for you!",
    "Ding! Your ${dishName} in order #${name} is cooked to perfection and ready."
]

const clock = document.querySelector("#clock");
getLocation();
getWeather(window.sessionStorage.getItem("latitude"), window.sessionStorage.getItem("longitude"));
setClock(clock);

let ordersState;
getOrdersFirst();
async function getOrdersFirst() {
    ordersState = await fetchOrders();
    console.log(ordersState);
}

setInterval(async () => {
    const newOrders = await fetchOrders()
    if (newOrders.length >= ordersState.length) {
        newOrders.forEach(order => {
            let existingOrder = ordersState.find(item => item.order_id === order.order_id)
            if (!existingOrder) {
                appendNewOrder(order);
            } else if (existingOrder && existingOrder.kitchen_done !== order.kitchen_done) {
                markOrderReady(order);
            } else if (existingOrder && existingOrder.picked_up !== order.picked_up) {
    
            }
        })
    } else {
        ordersState.forEach(oldOrder => {
            if (!newOrders.some(item => item.order_id === oldOrder.order_id)) {
                removeOrder(oldOrder)
            }
        })
    }
    ordersState = newOrders
}, pollEveryMilisecs);

async function fetchOrders() {
    const data = await fetch(checkOrdersLink);
    const array = await data.json()
    return array.filter(item => item.kitchen_needed);
}

function updateWeather(weather) {
    const icon = document.querySelector("#weather-icon");
    const temp  = document.querySelector("#temp");
    const condition  = document.querySelector("#condition");

    let iconObj = weatherIcons.filter(obj => {
        return obj.code === weather.current.condition.code
    })[0]
    
    icon.textContent = weather.current.is_day ? iconObj.day : iconObj.night;
    temp.textContent = `${parseInt(weather.current.temp_c)}°C`;
    condition.textContent = weather.current.condition.text;
}

function getWeather(latitude, longitude) {
    if (latitude && longitude) {
        const url = `https://api.weatherapi.com/v1/current.json?key=${APIKey}&q=${latitude},${longitude}&aqi=no`
    
        fetch(url)
        .then(response => response.json())
        .then(data => {
            updateWeather(data);
        })
        .catch(error => {
            console.error('Error fetching the weather data:', error);
        });
    }
}

function getLocation() {
    if (navigator.geolocation) {
        if (!window.sessionStorage.getItem("latitude") || !window.sessionStorage.getItem("longitude")) {
            navigator.geolocation.getCurrentPosition(showPosition, showError);
        }
    } else {
        console.log("Geolocation is not supported by this browser.");
    }
}

function showError(error) {
    switch(error.code) {
      case error.PERMISSION_DENIED:
        console.log("User denied the request for Geolocation.");
        break;
      case error.POSITION_UNAVAILABLE:
        console.log("Location information is unavailable.");
        break;
      case error.TIMEOUT:
        console.log("The request to get user location timed out.");
        break;
      case error.UNKNOWN_ERROR:
        console.log("An unknown error occurred.");
        break;
    }
}

function showPosition(position) {
    window.sessionStorage.setItem("latitude", position.coords.latitude)
    window.sessionStorage.setItem("longitude", position.coords.longitude)
}

async function announceOrderReady(order) {
    let curVol;
    if (player && player.setVolume) {
        curVol = player.getVolume();
        player.setVolume(0.2 * curVol);
    }
    TTSMessage(generateAnnouncement(order))
    .then(() => {
        if (player && player.setVolume) {
            player.setVolume(curVol)
        }
    })
}

async function TTSMessage (text) {
    let url = `/utils/tts?text=${encodeURIComponent(text)}`
    try {
        const response = await fetch(url, {
        method: 'GET',
        headers: {
            'Accept': 'audio/mpeg',
        },
        });
        if (!response.ok) {
        throw new Error('Network response was not ok');
        }
        const audioBlob = await response.blob();
        await playAudioBlob(audioBlob);
    } catch (error) {
        console.error('There was a problem with the fetch operation:', error);
    }
}

function playAudioBlob(audioBlob) {
    return new Promise((resolve, reject) => {
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);

        audio.onended = () => {
            resolve(); // Resolve the promise when audio playback ends
        };

        audio.onerror = (e) => {
            reject("Error playing audio: " + e.error); // Reject the promise on error
        };

        audio.play().catch(e => {
            console.error("Error playing audio:", e);
            reject(e); // Also reject the promise if play() catches an error
        });
    });
}

function generateAnnouncement(order) {
    let announcement;
    if (order.name) {
        const template = namedAnnouncements[Math.floor(Math.random() * namedAnnouncements.length)];
        announcement = template.replace(/\${name}/g, order.name);
    } else {
        const template = noNameAnnouncements[Math.floor(Math.random() * noNameAnnouncements.length)];
        announcement = template.replace(/\${name}/g, order.order_id);
        announcement = announcement.replace(/\${dishName}/g, order.dishes.length === 1 ? order.dishes[0].name : `${order.dishes[0].name} and more`);
    }
    return announcement;
}

function appendNewOrder(data) {
    console.log("recieved")
    // console.log(data)
    const newOrder = document.createElement("span");
    const text = document.createElement("span");
    text.textContent = data.name ? data.name : `Order #${data.order_id}`;
    newOrder.appendChild(text)
    newOrder.dataset.orderId = data.order_id;
    inProgressCol.appendChild(newOrder);
}

function markOrderReady(data) {
    const oldOrder = document.querySelector(`span[data-order-id="${data.order_id}"]`);
    if (data.done) {
        try {
            console.log("finished")
            oldOrder.classList.add("remove");
            setTimeout(() => {
                readyCol.removeChild(oldOrder)
            }, 500);
            return
        } catch (error) {}
    }
    console.log("ready")
    const newOrder = document.createElement("span");
    const text = document.createElement("span");
    text.textContent = data.name ? data.name : `Order #${data.order_id}`;
    newOrder.appendChild(text)
    newOrder.dataset.orderId = data.order_id;
    newOrder.dataset.dishQty = data.dishes.length;
    newOrder.dataset.dish = data.dishes[0].name;
    newOrder.dataset.barDone === data.bar_done
    oldOrder.classList.add("remove");
    setTimeout(() => {
        inProgressCol.removeChild(oldOrder)
    }, 500);
    readyCol.appendChild(newOrder);
    announceOrderReady(data)
}

function removeOrder(data) {
    if (!data.kitchen_needed) {return}
    const oldOrder = document.querySelector(`span[data-order-id="${data.order_id}"]`);
    oldOrder.classList.add("remove");
    setTimeout(() => {
        readyCol.removeChild(oldOrder)
    }, 500);
}

if (sessionStorage.getItem("fsReminderDone") !== "true") {
    alert(`
    Don't forget to put your browser in fullscreen mode (default key is F11, or in ... settings in Chrome)
    Controls:
    
    Press 1 to open the music player controls
    `)
    sessionStorage.setItem("fsReminderDone", "true");
} 

document.querySelector("#test-tts").addEventListener("click", () => {
    TTSMessage("This is what the announcements will sound like. Use this sound right now to set the volume to a good level. Here's some more speech: Lalalalalalalalala")
})

// setTimeout(() => {
//     announceOrderReady("Rosmery")
// }, 2000);