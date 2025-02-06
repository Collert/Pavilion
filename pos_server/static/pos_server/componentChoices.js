export function componentChoices(dishId) {
    const dialog = document.querySelector("#component-choices-dialog")

    const iframe = dialog.querySelector("iframe")
    iframe.onload = () => {
        const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;

        const iframeRoot = iframeDoc.documentElement;

        const parentStyles = getComputedStyle(document.documentElement);
        const primaryBackground = parentStyles.getPropertyValue('--primary-background').trim();
        const primaryContrast = parentStyles.getPropertyValue('--primary-contrast').trim();

        iframeRoot.style.setProperty('--primary-background', primaryBackground);
        iframeRoot.style.setProperty('--primary-contrast', primaryContrast);
    };

    const newSrc = `/restaurant/component-choice?dish_id=${dishId}`

    const oldURL = iframe.src ? new URL(iframe.src) : undefined

    if (oldURL?.pathname + oldURL?.search !== newSrc) {
        iframe.src = newSrc
    }
    dialog.showModal()
}