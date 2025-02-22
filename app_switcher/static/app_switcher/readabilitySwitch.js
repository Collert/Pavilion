const READABILITY_DISABLED = window.sessionStorage.getItem("force-off-readability") === "true" || new URLSearchParams(window.location.search).get("force-off-readability") === "true";
if (READABILITY_DISABLED) {
    document.documentElement.style.colorScheme = "only light";
    if (window.sessionStorage.getItem("force-off-readability") !== "true") {
        window.sessionStorage.setItem("force-off-readability", READABILITY_DISABLED)
    }
}
