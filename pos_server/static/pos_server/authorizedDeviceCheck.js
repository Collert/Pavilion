const DEVICE_TOKEN = window.localStorage.getItem("deviceToken") || new URLSearchParams(window.location.search).get("device-token");
try {
    const csrftoken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
} catch {
    console.error("CSRF token is not in the page. Make sure to include {% csrf_token %} somewhere in the template.")
}

if (!DEVICE_TOKEN) {
    window.location.href = "/restaurant/check-device";
} else {
    window.localStorage.setItem("deviceToken", DEVICE_TOKEN)
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
