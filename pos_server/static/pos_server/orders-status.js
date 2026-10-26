import setClock from "./clock.js";
import { onLiveUpdate } from "/static/js/live.js";
// import weatherIcons from "./weatherCodes.json" assert { type: "json" };

const inProgressCol = document.querySelector("#col-1 div");
const readyCol = document.querySelector("#col-2 div");

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

// An order is ready once every station working on it is done (2) or rejected (3).
const isReady = order => Object.values(order.stations).every(status => status >= 2);
// Orders with no stations (e.g. only pre-packaged items) never show up on the board.
const needsStations = order => Object.keys(order.stations).length > 0;

syncOrders(await fetchOrders(), false);
onLiveUpdate("orders", async () => syncOrders(await fetchOrders(), true));

async function fetchOrders() {
    const response = await fetch(checkOrdersLink);
    const orders = await response.json();
    return orders.filter(order => needsStations(order) && !order.picked_up);
}

// Bring the two columns in line with `orders`. Announces orders that just became ready.
function syncOrders(orders, announce) {
    const seen = new Set();
    for (const order of orders) {
        seen.add(String(order.order_id));
        const existing = document.querySelector(`span[data-order-id="${order.order_id}"]`);
        const ready = isReady(order);
        if (!existing) {
            (ready ? readyCol : inProgressCol).appendChild(orderChip(order));
        } else if (ready && existing.parentElement === inProgressCol) {
            removeChip(existing);
            readyCol.appendChild(orderChip(order));
            if (announce) announceOrderReady(order);
        }
    }
    document.querySelectorAll("span[data-order-id]").forEach(chip => {
        if (!seen.has(chip.dataset.orderId)) removeChip(chip);
    });
}

function orderChip(order) {
    const chip = document.createElement("span");
    const text = document.createElement("span");
    text.textContent = order.name ? order.name : `${gettext("Order #")}${order.order_id}`;
    chip.appendChild(text);
    chip.dataset.orderId = order.order_id;
    return chip;
}

function removeChip(chip) {
    chip.removeAttribute("data-order-id");
    chip.classList.add("remove");
    setTimeout(() => chip.remove(), 500);
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
    if (APIKey && latitude && longitude) {
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

if (sessionStorage.getItem("fsReminderDone") !== "true") {
    alert(gettext(`
    Don't forget to put your browser in fullscreen mode (default key is F11, or in ... settings in Chrome)
    Controls:
    
    Press 1 to open the music player controls
    `))
    sessionStorage.setItem("fsReminderDone", "true");
} 

document.querySelector("#test-tts").addEventListener("click", () => {
    TTSMessage("This is what the announcements will sound like. Use this sound right now to set the volume to a good level. Here's some more speech: Lalalalalalalalala")
})

// setTimeout(() => {
//     announceOrderReady("Rosmery")
// }, 2000);