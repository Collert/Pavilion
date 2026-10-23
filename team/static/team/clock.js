// The time clock (team/templates/team/clock.html). The page is server-rendered and kept current by live-page.js
// (forms post in the background, the two regions are swapped, and the "clock" topic refreshes the board when anyone
// punches). This adds what HTML can't do: the ticking clock, the PIN pads (pick a name, four digits send the form),
// and going back to "Who's clocking in?" once someone's visit is over.
import { livePage } from "/static/js/live-page.js";

const t = (text) => gettext(text);
const $ = (selector, root = document) => root.querySelector(selector);

function tick() {
    const now = new Date();
    const today = $("#today");
    if (!today) return;
    today.textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(now);
    $("#clock-time").textContent = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    $("#clock-ampm").textContent = parts.find((p) => p.type === "dayPeriod")?.value || "";
}
tick();
setInterval(tick, 1000);

// ---------- PIN pads ----------

function renderPin(form) {
    const pin = form.elements.pin.value;
    const chosen = form.querySelector(`[data-staff="${CSS.escape(form.elements.staff.value)}"]`);
    const prompt = $("[data-prompt]", form);
    const name = chosen?.querySelectorAll("span")[1]?.textContent;  // after the avatar letter
    prompt.textContent = name ? interpolate(prompt.dataset.named, [name]) : prompt.dataset.empty;
    const dots = $("[data-dots]", form);
    dots.setAttribute("aria-label", interpolate(t("%s of 4 digits entered"), [pin.length]));
    dots.replaceChildren(...[0, 1, 2, 3].map((i) => {
        const dot = document.createElement("span");
        if (i < pin.length) dot.className = "on";
        return dot;
    }));
}

function press(form, key) {
    const input = form.elements.pin;
    if (form.dataset.sending) return;
    if (!form.elements.staff.value) {
        $(".pin-error", form).textContent = t("Pick your name first.");
        return;
    }
    $(".pin-error", form).textContent = "";
    $("[data-dots]", form).classList.remove("bad");
    if (key === "clear") input.value = "";
    else if (key === "back") input.value = input.value.slice(0, -1);
    else if (input.value.length < 4) input.value += key;
    renderPin(form);
    if (input.value.length === 4) {
        form.dataset.sending = "1";
        form.requestSubmit();
    }
}

document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-pin-form] [data-staff]");
    if (chip) {
        const form = chip.closest("form");
        form.elements.staff.value = chip.dataset.staff;
        form.elements.pin.value = "";
        form.querySelectorAll("[data-staff]").forEach((b) => b.setAttribute("aria-pressed", String(b === chip)));
        $(".pin-error", form).textContent = "";
        renderPin(form);
        return;
    }
    const key = event.target.closest("[data-pin-form] [data-pin-key]");
    if (key) press(key.closest("form"), key.dataset.pinKey);
});

document.addEventListener("keydown", (event) => {
    const form = $("[data-pin-form]");
    if (!form || event.target.matches("input:not([type=hidden])")) return;
    if (/^[0-9]$/.test(event.key)) press(form, event.key);
    else if (event.key === "Backspace") press(form, "back");
});

// ---------- the visit ----------

let leaving = null;
function setUp() {
    document.querySelectorAll("[data-pin-form]").forEach(renderPin);
    clearTimeout(leaving);
    const panel = $("#tc-panel");
    const until = Number(panel?.dataset.until || 0);
    if (!panel || panel.dataset.view === "who" || !until) return;
    // Their visit ends by itself (a done screen after a few seconds, anyone else after two idle minutes)
    leaving = setTimeout(() => {
        const form = document.createElement("form");
        form.method = "post";
        form.innerHTML = `<input type="hidden" name="op" value="done">`;
        form.append(document.querySelector("input[name=csrfmiddlewaretoken]").cloneNode());
        panel.append(form);
        form.requestSubmit();
    }, Math.max(1000, until - Date.now()));
}
setUp();
new MutationObserver(setUp).observe(document.getElementById("page"), { childList: true });

livePage({ topics: ["clock"] });
