// The lock screen (see docs/mockups/Lock screen-html and pos_server/register.py: lock).
// Pick your name (the register's last few users), type your 4-digit PIN; the server signs you in and the
// register opens. Anyone else signs in with their password, which lists them here next time.
import { $, el, keypad, post } from "./staff-ui.js";

const t = (text) => gettext(text);
const staff = JSON.parse(document.getElementById("lock-staff").textContent);
const next = JSON.parse(document.getElementById("lock-next").textContent);
const device = JSON.parse(document.getElementById("lock-device").textContent);

// Clock
function tick() {
    const now = new Date();
    $("#today").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(now);
    $("#clock-time").textContent = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    $("#clock-ampm").textContent = parts.find((p) => p.type === "dayPeriod")?.value || "";
}
tick();
setInterval(tick, 10000);

const state = {
    who: staff[0]?.id,  // the list is the register's latest users, most recent first
    pin: "",
    error: "",
    shaking: false,
    busy: false,
};

function render() {
    if (!staff.length) return;
    const me = staff.find((s) => s.id === state.who) || staff[0];
    $("#staff").replaceChildren(...staff.map((person) => el("button", {
        className: "staff-btn", attrs: { "aria-pressed": String(person.id === state.who) },
        on: { click: () => { Object.assign(state, { who: person.id, pin: "", error: "", shaking: false }); render(); } },
    }, [el("span", { className: "avatar", text: person.name.charAt(0).toUpperCase() }), el("span", { text: person.name })])));
    $("#prompt").textContent = interpolate(t("Enter your 4-digit PIN, %s"), [me.name]);
    const dots = $("#dots");
    dots.className = "dots" + (state.shaking ? " shake bad" : "");
    dots.setAttribute("aria-label", interpolate(t("%s of 4 digits entered"), [state.pin.length]));
    dots.replaceChildren(...[0, 1, 2, 3].map((i) => el("span", { className: i < state.pin.length ? "on" : "" })));
    $("#pin-error").replaceChildren(...(state.error ? [el("span", { icon: "alert" }), state.error] : []));
    keys.clear.disabled = !state.pin;
    keys.back.disabled = !state.pin;
}

async function press(digit) {
    if (state.busy || state.shaking || state.pin.length >= 4) return;
    state.pin += digit;
    state.error = "";
    render();
    if (state.pin.length < 4) return;
    state.busy = true;
    const result = await post(location.pathname, { staff: state.who, pin: state.pin, next })
        .catch(() => ({ ok: false, data: { message: t("The register is offline. Check the connection.") } }));
    state.busy = false;
    if (result.ok) {
        $("#signin").hidden = true;
        $("#welcome").hidden = false;
        $("#welcome-title").textContent = interpolate(t("Welcome back, %s"), [result.data.name]);
        $("#welcome-text").textContent = result.data.drawer
            ? interpolate(t("%s is open. The drawer has %s in it."), [device, result.data.drawer])
            : interpolate(t("%s is open. The drawer is closed."), [device]);
        setTimeout(() => { location.href = result.data.next; }, 900);
        return;
    }
    state.error = result.data.message || t("That PIN didn't match. Try again.");
    state.shaking = true;
    render();
    setTimeout(() => { state.shaking = false; state.pin = ""; render(); }, 600);
}

let keys = { clear: {}, back: {} };
if (staff.length) {
    keys = keypad($("#keys"), {
        onDigit: (digit) => press(String(digit)),
        onClear: () => { if (!state.shaking) { state.pin = ""; state.error = ""; render(); } },
        onBack: () => { if (!state.shaking) { state.pin = state.pin.slice(0, -1); render(); } },
    });
    document.addEventListener("keydown", (e) => {
        if (/^[0-9]$/.test(e.key)) press(e.key);
        else if (e.key === "Backspace") { state.pin = state.pin.slice(0, -1); render(); }
    });
}
render();
