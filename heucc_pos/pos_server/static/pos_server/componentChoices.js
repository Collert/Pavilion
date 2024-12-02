export function componentChoices(dishId) {
    const dialog = document.querySelector("#component-choices-dialog")

    const iframe = dialog.querySelector("iframe")
    iframe.src = `/restaurant/component-choice?dish_id=${dishId}`
    dialog.showModal()
}