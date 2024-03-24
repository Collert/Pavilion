export default function setClock(element, seconds = false) {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    
    // Format the time to display two digits.
    hours = hours < 10 ? '0' + hours : hours;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    
    element.innerHTML = `${hours}:${minutes}`
    if (seconds) {
        let seconds = now.getSeconds();
        seconds = seconds < 10 ? '0' + seconds : seconds;
        element.innerHTML += `:${seconds}`
    }

    setTimeout(() => {setClock(element, seconds)}, 1000);
}