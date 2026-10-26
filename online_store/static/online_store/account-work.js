// The new-request forms on Availability and time off (online_store/templates/online_store/account/time.html):
// what HTML can't do on its own. The time-off form shows which shifts it touches and checks the dates; the
// availability form shows a day's times only while it's on, marks the days that changed and counts them.

const offForm = document.getElementById("timeoff-form");
if (offForm) {
    const shifts = JSON.parse(document.getElementById("shift-days").textContent);
    const impact = document.getElementById("timeoff-impact");
    const send = offForm.querySelector('button[value="timeoff"]');
    const field = (name) => offForm.querySelector(`[name="${name}"]:not([type="hidden"])`);
    const update = () => {
        const from = field("first").value, to = field("last").value || from;
        const allDay = field("all_day").checked;
        offForm.querySelector(".part-day").hidden = allDay;
        let text = "", tone = "", ok = false;
        if (!from) {
            text = "";
        } else if (to < from) {
            text = gettext("The end date is before the start date."); tone = "bad";
        } else if (from <= offForm.dataset.today) {
            text = gettext("Pick a date after today."); tone = "bad";
        } else if (!allDay && !(field("end").value > field("start").value)) {
            text = gettext("Check the times."); tone = "bad";
        } else {
            ok = true;
            const hit = shifts.filter((s) => s.date >= from && s.date <= to);
            if (hit.length) {
                text = interpolate(ngettext("You have %s shift then: %s. If approved, they go to Up for grabs.",
                    "You have %s shifts then: %s. If approved, they go to Up for grabs.", hit.length), [hit.length, hit.map((s) => s.label).join(", ")]);
                tone = "warn";
            } else {
                text = gettext("No shifts on your schedule then yet. Your manager won’t schedule you if it’s approved.");
                tone = "good";
            }
        }
        impact.textContent = text;
        impact.className = "acct-impact " + tone;
        impact.hidden = !text;
        send.disabled = !ok;
    };
    offForm.addEventListener("input", update);
    offForm.addEventListener("change", update);
    update();
}

const availForm = document.getElementById("avail-form");
if (availForm) {
    const send = document.getElementById("avail-send");
    const update = () => {
        let changed = 0;
        availForm.querySelectorAll(".acct-day").forEach((row) => {
            const on = row.querySelector('input[type="checkbox"]').checked;
            row.querySelector(".acct-day-times").hidden = !on;
            row.querySelector(".acct-day-off").hidden = on;
            const [start, end] = row.querySelectorAll('input[type="time"]');
            const now = on ? `${start.value}-${end.value}` : "";
            const differs = now !== row.dataset.was;
            row.classList.toggle("changed", differs);
            if (differs) changed += 1;
        });
        const from = availForm.querySelector('[name="from"]').value;
        const until = availForm.querySelector('[name="until"]').value;
        const ok = changed > 0 && from > availForm.dataset.today && (!until || until > from);
        send.disabled = !ok;
        send.textContent = changed
            ? interpolate(ngettext("Send availability change · %s day", "Send availability change · %s days", changed), [changed])
            : gettext("Change a day to send");
    };
    availForm.addEventListener("input", update);
    availForm.addEventListener("change", update);
    update();
}
