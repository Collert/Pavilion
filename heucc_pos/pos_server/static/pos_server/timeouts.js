const minute = 60 * 1000;

// Define the timeout period in milliseconds (5 minutes)
const TIMEOUT_PERIOD = 5 * minute;

function onUserInactivity() {
    console.log(`User has been inactive for ${TIMEOUT_PERIOD / minute} minutes, reloading page`);
    window.location.reload()
}

// Setup a variable to hold the timeout ID
let timeoutID;

// Reset the timeout function to start counting from zero
function resetTimeout() {
    // Clear the existing timeout, if any
    if (timeoutID) {
        clearTimeout(timeoutID);
    }

    // Set a new timeout
    timeoutID = setTimeout(onUserInactivity, TIMEOUT_PERIOD);
}

// Add event listeners for user actions
window.addEventListener('mousemove', resetTimeout, false);
window.addEventListener('keypress', resetTimeout, false);
window.addEventListener('scroll', resetTimeout, false); 
window.addEventListener('touchstart', resetTimeout, false);

// Initialize the timeout timer at script start
resetTimeout();
