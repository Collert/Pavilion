// The menu editor's display preview (admin_portal/templates/admin_portal/menu.html): play / pause / restart the
// real board drawn small (screens/widgets/menu_board.js reads window.menuBoardPreview). Paused, it shows the page
// being edited.

const state = (window.menuBoardPreview ??= { playing: true, start: Date.now(), page: 0 });
const PAUSE = "M7 5h4v14H7z M13 5h4v14h-4z", PLAY = "M8 5v14l11-7z";

function sync() {
    const frame = document.querySelector("[data-preview-page]");
    if (frame) state.page = Number(frame.dataset.previewPage) || 0;
    const button = document.querySelector("[data-preview-play]");
    if (!button) return;
    button.setAttribute("aria-label", state.playing ? gettext("Pause preview") : gettext("Play preview"));
    button.querySelector("path")?.setAttribute("d", state.playing ? PAUSE : PLAY);
    const label = document.querySelector("[data-preview-state]");
    const text = state.playing ? gettext("5× speed") : interpolate(gettext("Paused · page %s"), [state.page + 1]);
    if (label && label.textContent !== text) label.textContent = text;  // (unchanged text would wake the observer again)
}

document.addEventListener("click", (event) => {
    if (event.target.closest("[data-preview-play]")) {
        state.playing = !state.playing;
        sync();
    } else if (event.target.closest("[data-preview-restart]")) {
        state.start = Date.now();
        state.playing = true;
        sync();
    }
});

new MutationObserver(sync).observe(document.body, { childList: true, subtree: true });
sync();
