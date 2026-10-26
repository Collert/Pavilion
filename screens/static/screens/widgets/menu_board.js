// Menu boards (screens/templates/screens/menu_board_frame.html) take turns showing their pages (.mb-page) and, on
// each page, the screens of slots with more items than fit (.mb-screens > .mb-screen), with dots (.mb-dot) to show
// where they are. Timings come from data-page-sec / data-group-sec, transitions from data-page-fx / data-group-fx.
// The position is worked out from the clock, so boards that are swapped in by live-page.js (or several screens
// showing the same menu) stay in step. A board with data-preview (the admin's Display tab) runs 5× faster from when
// the page opened, and can be paused on a page (window.menuBoardPreview, set by the admin page).

const preview = (window.menuBoardPreview ??= { playing: true, start: Date.now(), page: 0 });

function show(nodes, index, fx) {
    nodes.forEach((node, i) => {
        const on = i === index;
        if (on && node.hidden) {
            node.hidden = false;
            node.classList.remove("mb-fx-fade", "mb-fx-slide", "mb-fx-rise");
            if (fx && fx !== "cut") {
                void node.offsetWidth;  // restart the animation
                node.classList.add(`mb-fx-${fx}`);
            }
        } else if (!on && !node.hidden) {
            node.hidden = true;
        }
    });
}

function mark(dots, index) {
    dots.forEach((dot, i) => dot.classList.toggle("on", i === index));
}

function tick() {
    for (const board of document.querySelectorAll(".menu-board")) {
        const isPreview = board.hasAttribute("data-preview");
        const pageSec = Number(board.dataset.pageSec) || 20, groupSec = Number(board.dataset.groupSec) || 8;
        const pages = [...board.querySelectorAll(".mb-page")];
        let page, local;
        if (isPreview && !preview.playing) {
            page = Math.min(preview.page, pages.length - 1);
            local = 0;
        } else {
            const seconds = isPreview ? ((Date.now() - preview.start) / 1000) * 5 : Date.now() / 1000;
            page = pages.length > 1 ? Math.floor(seconds / pageSec) % pages.length : 0;
            local = pages.length > 1 ? seconds % pageSec : seconds;
        }
        show(pages, page, board.dataset.pageFx);
        mark([...board.querySelectorAll(".mb-page-dots .mb-dot")], page);
        for (const wrap of pages[page]?.querySelectorAll(".mb-screens") || []) {
            const screens = [...wrap.querySelectorAll(":scope > .mb-screen")];
            const screen = screens.length > 1 ? Math.floor(local / groupSec) % screens.length : 0;
            show(screens, screen, board.dataset.groupFx);
            mark([...(wrap.parentElement.querySelectorAll(".mb-dots .mb-dot") || [])], screen);
        }
    }
}

tick();
setInterval(tick, 250);
