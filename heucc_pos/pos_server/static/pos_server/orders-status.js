import setClock from "./clock.js";
import weatherIcons from "./weatherCodes.json" assert { type: "json" };

const announcements = [
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

const clock = document.querySelector("#clock");
getLocation();
getWeather(window.sessionStorage.getItem("latitude"), window.sessionStorage.getItem("longitude"));
setClock(clock);


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
        const url = `http://api.weatherapi.com/v1/current.json?key=${APIKey}&q=${latitude},${longitude}&aqi=no`
    
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

async function announceOrderReady(name) {
    try {
        const response = await fetch(`/utils/tts${generateAnnouncementUrl(name)}`, {
        method: 'GET',
        headers: {
            'Accept': 'audio/mpeg',
        },
        });
        if (!response.ok) {
        throw new Error('Network response was not ok');
        }
        const audioBlob = await response.blob();
        playAudioBlob(audioBlob);
    } catch (error) {
        console.error('There was a problem with the fetch operation:', error);
    }
}

function playAudioBlob(audioBlob) {
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = new Audio(audioUrl);
    audio.play().catch(e => console.error("Error playing audio:", e));
}

function generateAnnouncementUrl(personName) {
    // Replace "name" placeholder with the actual name provided
    const personalizedAnnouncements = announcements.map(announcement => announcement.replace(/\${name}/g, personName));
    // Pick a random announcement
    const randomAnnouncement = personalizedAnnouncements[Math.floor(Math.random() * personalizedAnnouncements.length)];
    // Encode the announcement for URL
    return `?text=${encodeURIComponent(randomAnnouncement)}`;
}

// setTimeout(() => {
//     announceOrderReady("Rosmery")
// }, 2000);