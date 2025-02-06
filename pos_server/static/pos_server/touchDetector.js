function isExclusiveTouchDevice() {
    const hasTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    const hasMouse = window.matchMedia('(pointer: fine)').matches;
    const canHover = window.matchMedia('(hover: hover)').matches;

    // If it has touch but no mouse or hover, it’s likely an exclusive touch device
    return hasTouch && !hasMouse && !canHover;
}

document.querySelector(".touch-only-display", elements => {
    if (!isExclusiveTouchDevice()) {
        elements.forEach(element => {
            element.style.display = "none";
        });
    }
})