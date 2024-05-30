const DEVICE_TOKEN = window.localStorage.getItem("deviceToken");
const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;

if (!DEVICE_TOKEN) {
    window.location.href = "/restaurant/check-device";
} else {
    fetch(
        "/restaurant/check-device",{
          method:"PUT",
            headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken,
            },
            body: JSON.stringify({
                token: DEVICE_TOKEN
            })
        }
    )
    .then(response => {
        if (!response.ok) {
            window.location.href = "/restaurant/check-device";
        }
    })
}