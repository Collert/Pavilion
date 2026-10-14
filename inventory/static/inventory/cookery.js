// Cookery (docs/mockups/cookery, inventory/cookery.py): make components and move them between states.
//
// Screens: home (Craft or Transform, with the batches in progress on top), a recipe, guided cooking through its
// steps (with timers), recording the yield, the "you made it" screen, and a transform. Everything shown comes from
// the server's snapshot (refetched on the "cookery" and "inventory" live events), so a batch started on one
// screen can be picked up on another. The URL hash keeps your place (#recipe/3, #batch/12, #transform/2, ...).
//
// Starting a recipe batch reserves its ingredients (they leave inventory; the recipe screen shows what other batches
// hold), and finishing short of the recipe's yield logs the unused share as waste (inventory/cookery.py).
//
// Amounts are stored in each item's own unit, always the smallest: g, ml or ea. Counting and transforms use a friendlier display
// unit (kg for g, L for ml, pcs for ea) and convert back when they send.

import { onLiveUpdate } from "/static/js/live.js";
import { el, post } from "../pos_server/staff-ui.js";
import { cookScene, SCENE_VERBS } from "./cook-scene.js";

const t = (text) => gettext(text);
const fmt = (text, ...args) => interpolate(gettext(text), args);
const URL_ = JSON.parse(document.getElementById("cook-url").textContent);
const main = document.getElementById("cook-main");
const lang = document.documentElement.lang || undefined;
const MIN = 60000;

const state = {
    data: null, offset: 0,
    view: "home", mode: "make", query: "",
    compId: null, batch: 1, sid: null, tfId: null, tfQty: 0, tfText: null,
    entry: "", done: null, busy: false, bump: false,
};

// ---------- data helpers ----------

const nowMs = () => Date.now() + state.offset;
const comp = (id) => state.data.components.find((c) => c.id === id);
const recipeOf = (c) => (c && c.recipe ? state.data.recipes[c.recipe] : null);
const ingredient = (id) => state.data.ingredients[id];
const transform = (id) => state.data.transforms.find((x) => x.id === id);
const session = (id) => state.data.sessions.find((s) => s.id === id);
const mine = (s) => s.holder?.id === state.data.me;
const who = (s) => (mine(s) ? t("You") : s.holder?.name ?? t("Someone"));

// ---------- formatting ----------

const two = (n) => String(n).padStart(2, "0");
const trim = (n) => String(Math.round(n * 100) / 100);
function fmtLeft(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}
function fmtDur(seconds) {
    const m = Math.round(seconds / 60);
    if (m < 60) return fmt("%s min", m);
    const h = Math.floor(m / 60);
    return m % 60 ? fmt("%s h %s min", h, m % 60) : fmt("%s h", h);
}
const clock = (ms) => new Date(ms - state.offset).toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
function ago(ms) {
    const m = Math.max(0, Math.round((nowMs() - ms) / MIN));
    if (m < 1) return t("just now");
    return m < 60 ? fmt("%s min ago", m) : fmt("%s h ago", Math.floor(m / 60));
}
// An ingredient amount (stored in g, ml or pieces): big grams as kilos, big millilitres as litres
function amount(n, unit) {
    if (unit === "g" && Math.abs(n) >= 1000) return `${trim(n / 1000)} kg`;
    if (unit === "ml" && Math.abs(n) >= 1000) return `${trim(n / 1000)} L`;
    if (unit === "ea") return trim(n);
    return `${trim(n)} ${unit}`;
}
// Counting and transforms: the unit people think in, and how many stored units make one of it
const DISPLAY = { ea: [t("pcs"), 1], g: ["kg", 1000], ml: ["L", 1000] };
const shown = (unit) => DISPLAY[unit] ?? [unit, 1];
const qty = (n, unit) => `${trim(n / shown(unit)[1])} ${shown(unit)[0]}`;

// ---------- icons (constant markup only) ----------

const P = {
    back: "M15 6l-6 6 6 6", next: "M5 12h14M13 6l6 6-6 6", play: "M8 5.5v13l10-6.5z", pause: "M9 6v12 M15 6v12",
    reset: "M4 12a8 8 0 1 0 2.3-5.6 M4 4v4h4", plus: "M12 5v14M5 12h14", minus: "M5 12h14", check: "m5 12.5 4.5 4.5L19 7.5",
    recipe: "M6 3h10l3 3v15H6z M9 9h7 M9 13h7 M9 17h4", hand: "M12 3v9 M8 7v6 M16 7v6 M5 11v3a7 7 0 0 0 14 0v-3",
    clock: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 7.5V12l3 2", steps: "M4 6h16 M4 12h16 M4 18h10",
    yield: "M4 20h16 M7 20V10 M12 20V5 M17 20v-7", warn: "M12 7v6 M12 17v.01", alert: "M12 3.5 2.5 20h19z M12 10v4.5 M12 17.4v.1",
    search: "M11 4.5a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13z m9 15.5-4-4", chevron: "m9 6 6 6-6 6", big: "m5 12 5 5 9-10",
    backspace: "M9 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-6-6 6-6z m4 4 4 4M17 10l-4 4",
};
function icon(d, size = 20, width = 2.4) {
    const template = document.createElement("template");
    template.innerHTML = `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"></path></svg>`;
    return template.content.firstChild;
}
const button = (className, children, onClick, attrs = {}) => el("button", { className, attrs: { type: "button", ...attrs }, on: { click: onClick } }, children);
const backButton = (label, onClick) => button("pill-btn", [icon(P.back), label], onClick);

// ---------- server ----------

function take(data) {
    state.data = data;
    state.offset = data.now - Date.now();
}

async function refresh() {
    try {
        const response = await fetch(URL_, { headers: { Accept: "application/json" } });
        if (response.ok) {
            take(await response.json());
            render();
        }
    } catch { /* offline: the live stream refreshes when it reconnects */ }
}

async function act(action, body = {}) {
    if (state.busy) return null;
    state.busy = true;
    try {
        const { ok, data } = await post(URL_, { action, ...body });
        if (!ok) {
            toast(data.error || t("That didn't work. Try again."), true);
            return null;
        }
        take(data);
        return data;
    } finally {
        state.busy = false;
    }
}

// ---------- navigation ----------

function go(patch, { push = true } = {}) {
    Object.assign(state, patch);
    if (push) {
        const hash = routeOf();
        if (location.hash !== hash) history.pushState(null, "", hash);
    }
    render();
    main.scrollTop = 0;
}

function routeOf() {
    switch (state.view) {
        case "recipe": return `#recipe/${state.compId}`;
        case "guided":
        case "yield": return `#batch/${state.sid}`;
        case "transform": return state.sid ? `#transforming/${state.sid}` : `#transform/${state.tfId}`;
        default: return state.mode === "transform" ? "#transforms" : "#craft";
    }
}

function fromRoute() {
    const [name, raw] = location.hash.slice(1).split("/");
    const id = Number(raw);
    const base = { sid: null, entry: "", done: null, tfText: null };
    if (name === "recipe" && comp(id)) return { ...base, view: "recipe", compId: id, batch: 1 };
    if (name === "batch" && session(id)) return { ...base, view: session(id).phase === "yield" ? "yield" : "guided", sid: id };
    if (name === "transforming" && session(id)) return { ...base, view: "transform", sid: id, tfId: session(id).transform };
    if (name === "transform" && transform(id)) return { ...base, view: "transform", tfId: id, tfQty: comp(transform(id).from).have };
    return { ...base, view: "home", mode: name === "transforms" ? "transform" : "make" };
}
window.addEventListener("popstate", () => go(fromRoute(), { push: false }));

document.querySelectorAll(".rail [data-mode]").forEach((node) => {
    node.addEventListener("click", () => go({ view: "home", mode: node.dataset.mode, sid: null, entry: "" }));
});

// ---------- toast and chime ----------

let toastTimer;
function toast(text, error = false) {
    const box = document.getElementById("cook-toast");
    box.replaceChildren(el("div", { className: "cook-toast" + (error ? " error" : ""), attrs: { role: error ? "alert" : "status" } },
        [icon(error ? P.alert : P.check, 18, 2.6), el("span", { text })]));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.replaceChildren(), error ? 5000 : 3200);
}

let audio;
function chime() {
    try {
        audio ??= new AudioContext();
        [0, 0.25, 0.5].forEach((delay, n) => {
            const osc = audio.createOscillator(), gain = audio.createGain();
            osc.frequency.value = n === 2 ? 1046 : 784;
            gain.gain.setValueAtTime(0.0001, audio.currentTime + delay);
            gain.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + delay + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + delay + 0.22);
            osc.connect(gain).connect(audio.destination);
            osc.start(audio.currentTime + delay);
            osc.stop(audio.currentTime + delay + 0.25);
        });
    } catch { /* no sound available */ }
}

// ---------- timers ----------

function timerLeft(timer) {
    return timer.paused != null ? timer.paused * 1000 : Math.max(0, timer.end - nowMs());
}

// The timer that matters most for a batch (the one closest to done), or null
function sessionTimer(s) {
    if (s.kind === "transform") {
        const tf = transform(s.transform);
        return { left: Math.max(0, s.ends_at - nowMs()), dur: tf.dur * 1000, running: s.ends_at > nowMs(), scene: tf.scene };
    }
    const recipe = state.data.recipes[s.recipe];
    let best = null;
    for (const [key, timer] of Object.entries(s.timers ?? {})) {
        const step = recipe?.steps[Number(key)];
        if (!step) continue;
        const left = timerLeft(timer);
        const found = { left, dur: timer.dur * 1000, running: timer.paused == null && left > 0, paused: timer.paused != null, scene: step.scene, step: Number(key), title: step.title };
        if (!best || found.left < best.left) best = found;
    }
    return best;
}

// Ticking: texts update in place every half second; anything that changes shape (a timer running out) re-renders
let tickers = [];
let shape = "";
function shapeNow() {
    if (!state.data) return "";
    return state.data.sessions.map((s) => {
        const timers = s.kind === "transform" ? [s.ends_at] : Object.values(s.timers ?? {}).map((x) => x.end);
        return timers.map((end) => (end && end <= nowMs() ? 1 : 0)).join("");
    }).join("|");
}
setInterval(() => {
    const next = shapeNow();
    if (next !== shape) {
        if (shape && next.split("").filter((c) => c === "1").length > shape.split("").filter((c) => c === "1").length) chime();
        shape = next;
        render();
        return;
    }
    tickers.forEach((fn) => fn());
}, 500);

// ---------- rendering ----------

function render() {
    if (!state.data) return;
    // Keep the caret where it was if something is being typed
    const active = document.activeElement?.dataset?.keep;
    const caret = active ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    tickers = [];
    document.querySelectorAll(".rail [data-mode]").forEach((node) => {
        const on = state.view === "home" && node.dataset.mode === state.mode;
        if (on) node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
    });
    const views = { home: homeView, recipe: recipeView, guided: guidedView, yield: yieldView, done: doneView, transform: transformView };
    const view = (views[state.view] ?? homeView)();
    main.replaceChildren(view);
    shape = shapeNow();
    if (active) {
        const node = main.querySelector(`[data-keep="${active}"]`);
        node?.focus();
        if (node && caret) node.setSelectionRange(...caret);
    }
}

// ---------- home ----------

function ongoingCard(s) {
    const timer = sessionTimer(s);
    let name, line, scene;
    let stepText = "";
    if (s.kind === "transform") {
        const tf = transform(s.transform);
        const from = comp(tf.from);
        name = tf.name;
        line = `${SCENE_VERBS[tf.scene] ? t(SCENE_VERBS[tf.scene]) : tf.name} ${qty(s.qty, from.unit)}`;
        scene = tf.scene;
    } else {
        const c = comp(s.component);
        const recipe = state.data.recipes[s.recipe];
        name = c.name;
        if (s.phase === "yield" || !recipe) {
            const total = s.log.reduce((sum, entry) => sum + entry.amt, 0);
            line = fmt("Counting · %s so far", qty(total, c.unit));
            scene = "prep";
        } else {
            const step = recipe.steps[s.step];
            line = step.title;
            stepText = fmt("Step %s of %s", s.step + 1, recipe.steps.length) + " · ";
            scene = timer ? timer.scene : step.scene;
        }
    }
    const done = timer && timer.left <= 0;
    const timerChip = el("span", { className: "ongoing-timer" + (done ? " done breathe" : timer ? " running" : "") });
    const update = () => {
        const current = sessionTimer(s);
        timerChip.textContent = current ? (current.left <= 0 ? t("Time's up") : fmtLeft(current.left)) : t("No timer");
        if (s.kind === "transform") lineNode.textContent = line + " · " + (current.left > 0 ? fmt("ready %s", clock(s.ends_at)) : t("ready now"));
    };
    const lineNode = el("div", { className: "ongoing-line", text: line });
    update();
    tickers.push(update);
    return el("div", { className: "ongoing-card" + (done ? " done" : "") }, [
        el("span", { className: "scene-box" }, [cookScene(scene, !!timer?.running, 96)]),
        el("div", { className: "grow" }, [
            el("div", { className: "ongoing-name", text: name }), lineNode,
            el("div", { className: "ongoing-sub", text: `${stepText}${who(s)} · ${ago(s.started_at)}` }),
        ]),
        el("div", { className: "ongoing-side" }, [
            timerChip,
            button("ongoing-cta" + (mine(s) ? " mine" : ""), [mine(s) ? t("Resume") : t("Pick up")], () => openSession(s)),
        ]),
    ]);
}

async function openSession(s) {
    if (!mine(s)) {
        const name = who(s);
        if (!(await act("take", { session: s.id }))) return;
        const label = s.kind === "transform" ? transform(s.transform).name : comp(s.component).name;
        toast(fmt("You picked up %s's %s", name, label.toLowerCase()));
    }
    const fresh = session(s.id);
    if (!fresh) return go({ view: "home" });
    if (fresh.kind === "transform") return go({ view: "transform", sid: fresh.id, tfId: fresh.transform });
    go({ view: fresh.phase === "yield" || !fresh.recipe ? "yield" : "guided", sid: fresh.id, entry: "" });
}

function compTile(c) {
    const recipe = recipeOf(c);
    const pct = c.par ? Math.min(100, Math.round((c.have / c.par) * 100)) : null;
    const low = pct !== null && pct < 35;
    const freezes = state.data.transforms.some((tf) => tf.from === c.id);
    const total = recipe ? recipe.steps.reduce((sum, step) => sum + step.dur, 0) : 0;
    return button("tile comp-tile", [
        el("span", { className: "tile-tags" }, [
            el("span", { className: "tag" + (recipe ? " recipe" : "") }, [icon(recipe ? P.recipe : P.hand, 13, 2.4),
                recipe ? fmt("Recipe · makes %s", qty(recipe.yield, c.unit).replace(` ${t("pcs")}`, "")) : t("One at a time")]),
            low ? el("span", { className: "tag low", text: t("Low") }) : null,
        ]),
        el("span", { className: "tile-name", text: c.name }),
        el("span", { className: "grow" }),
        el("span", { className: "tile-stock" }, [
            el("span", { className: "strong", text: fmt("%s on hand", qty(c.have, c.unit)) }),
            c.par ? el("span", { className: "muted", text: fmt("par %s", trim(c.par / shown(c.unit)[1])) }) : null,
        ]),
        el("span", { className: "bar" }, [el("span", { className: "bar-fill" + (low ? " low" : ""), attrs: { style: `width:${pct ?? 0}%` } })]),
        el("span", { className: "tile-foot", text: freezes ? t("Can be frozen") : recipe ? fmt("%s steps · %s", recipe.steps.length, fmtDur(total)) : t("Count as you make them") }),
    ], async () => {
        if (recipe) return go({ view: "recipe", compId: c.id, batch: 1 });
        const result = await act("start", { component: c.id });
        if (result) go({ view: "yield", sid: result.session, entry: "" });
    }, { "aria-label": `${c.name}, ${fmt("%s on hand", qty(c.have, c.unit))}` });
}

function tfTile(tf) {
    const from = comp(tf.from), to = comp(tf.to);
    return button("tile tf-tile", [
        el("span", { className: "scene-box lg" }, [cookScene(tf.scene, false, 120)]),
        el("span", { className: "grow tf-text" }, [
            el("span", { className: "tf-name", text: tf.name }),
            el("span", { className: "tf-flow", text: `${from.name}  →  ${to.name}` }),
            el("span", { className: "muted", text: fmt("%s available · takes about %s", qty(from.have, from.unit), fmtDur(tf.dur)) }),
        ]),
        icon(P.chevron, 22, 2.2),
    ], () => go({ view: "transform", tfId: tf.id, sid: null, tfQty: from.have, tfText: null }));
}

function homeView() {
    const q = state.query.trim().toLowerCase();
    const make = state.mode === "make";
    const sessions = state.data.sessions;
    const search = el("input", { attrs: { type: "search", placeholder: t("Search"), "aria-label": t("Search"), "data-keep": "search", value: state.query },
        on: { input: (e) => { state.query = e.target.value; render(); } } });
    const items = make
        ? state.data.components.filter((c) => c.makeable && (!q || c.name.toLowerCase().includes(q))).map(compTile)
        : state.data.transforms.filter((tf) => !q || `${tf.name} ${comp(tf.from).name} ${comp(tf.to).name}`.toLowerCase().includes(q)).map(tfTile);
    return el("div", { className: "view swap home" }, [
        el("header", { className: "cook-head" }, [
            el("div", { className: "grow" }, [el("div", { className: "overline", text: t("Cookery") }), el("h1", { className: "cook-title", text: make ? t("Craft") : t("Transform") })]),
            el("label", { className: "cook-search" }, [icon(P.search, 18, 2), search]),
        ]),
        sessions.length ? el("section", { className: "ongoing", attrs: { "aria-label": t("Ongoing cooking") } }, [
            el("div", { className: "section-cap" }, [
                el("span", { className: "overline ink", text: t("Ongoing") }),
                el("span", { className: "muted", text: interpolate(ngettext("%s batch · anyone on shift can pick these up", "%s batches · anyone on shift can pick these up", sessions.length), [sessions.length]) }),
            ]),
            el("div", { className: "ongoing-row" }, sessions.map(ongoingCard)),
        ]) : null,
        el("section", { className: "panel cook-panel", attrs: { "aria-label": make ? t("Components") : t("Transforms") } }, [
            el("div", { className: "section-cap pad" }, [
                el("span", { className: "overline ink", text: make ? t("Components") : t("Transforms") }),
                el("span", { className: "muted", text: make ? t("Pick what to make. Ones with a recipe cook in batches.") : t("Change a component from one state to another, like frozen to fresh.") }),
            ]),
            el("div", { className: "panel-scroll" }, [
                items.length ? el("div", { className: make ? "comp-grid" : "tf-grid" }, items)
                    : el("div", { className: "empty-line", text: q ? t("Nothing matches that search.") : make ? t("Nothing is set up to be made here yet. Set a component's crafting option to Craftable in Advanced admin.") : t("No transforms yet. Add them in Advanced admin.") }),
            ]),
        ]),
    ]);
}

// ---------- recipe ----------

function recipeView() {
    const c = comp(state.compId);
    const recipe = recipeOf(c);
    if (!recipe) return homeView();
    const b = state.batch;
    const timed = recipe.steps.filter((step) => step.dur).length;
    const total = recipe.steps.reduce((sum, step) => sum + step.dur, 0);
    const shorts = [];
    const rows = recipe.ingredients.map((row) => {
        const ing = ingredient(row.ingredient);
        const need = row.qty * b;
        const short = !ing.unlimited && ing.have < need;
        if (short) shorts.push(fmt("%s (short %s)", ing.name, amount(need - ing.have, ing.unit)));
        return el("div", { className: "ing-row" }, [
            el("span", { className: "ing-mark" + (short ? " short" : "") }, [icon(short ? P.warn : P.check, 14, 3)]),
            el("span", { className: "grow ing-name", text: ing.name }),
            el("span", { className: "ing-have" }, [
                ing.unlimited ? t("not counted") : fmt("%s on hand", amount(ing.have, ing.unit)),
                ing.reserved ? el("span", { className: "ing-reserved", text: fmt("%s in batches cooking", amount(ing.reserved, ing.unit)) }) : null,
            ]),
            el("span", { className: "ing-need", text: amount(need, ing.unit) }),
        ]);
    });
    const begin = (phase) => async () => {
        const result = await act("start", { component: c.id, batch: b, phase });
        if (!result) return;
        toast(t("Ingredients taken out of inventory"));
        go({ view: phase, sid: result.session, entry: "" });
    };
    const stepper = (label, d, off, onClick) => button("round-btn", [icon(d, 20, 2.6)], onClick, { "aria-label": label, disabled: off });
    return el("div", { className: "view swap split" }, [
        el("section", { className: "panel scroll-panel", attrs: { "aria-label": t("Recipe") } }, [
            backButton(t("Craft"), () => go({ view: "home", mode: "make" })),
            el("div", { className: "overline gap-top", text: t("Recipe") }),
            el("h1", { className: "big-title", text: c.name }),
            el("div", { className: "meta-row" }, [
                [P.yield, fmt("Makes about %s", qty(recipe.yield * b, c.unit))], [P.clock, fmtDur(total)],
                [P.steps, interpolate(ngettext("%s step", "%s steps", recipe.steps.length), [recipe.steps.length])],
                [P.clock, interpolate(ngettext("%s timer", "%s timers", timed), [timed])],
            ].map(([d, text]) => el("span", { className: "meta-chip" }, [icon(d, 16, 2.2), text]))),
            el("div", { className: "batch-box" }, [
                el("div", { className: "grow" }, [
                    el("div", { className: "strong", text: t("Batch size") }),
                    el("div", { className: "batch-note", text: b === 1 ? fmt("One batch · about %s", qty(recipe.yield, c.unit)) : fmt("%s batches · about %s", b, qty(recipe.yield * b, c.unit)) }),
                ]),
                el("div", { className: "stepper-pill" }, [
                    stepper(t("Smaller batch"), P.minus, b <= 1, () => go({ batch: Math.max(1, b - 1) }, { push: false })),
                    el("span", { className: "disp stepper-value", text: `×${b}` }),
                    stepper(t("Bigger batch"), P.plus, b >= 10, () => go({ batch: Math.min(10, b + 1) }, { push: false })),
                ]),
            ]),
            el("div", { className: "section-cap gap-top" }, [el("span", { className: "overline ink", text: t("Ingredients") }), el("span", { className: "muted", text: t("Taken out of inventory when you start") })]),
            el("div", { className: "ing-list" }, rows),
            shorts.length ? el("div", { className: "short-alert", attrs: { role: "alert" } }, [icon(P.alert, 20, 2.2),
                el("div", { text: fmt("Short on %s. You can still start; inventory for that will go below zero.", shorts.join(", ")) })]) : null,
            recipe.notes ? el("div", { className: "notes-box", text: recipe.notes }) : null,
        ]),
        el("aside", { className: "panel side-panel", attrs: { "aria-label": t("Steps at a glance") } }, [
            el("div", { className: "overline ink", text: t("Steps at a glance") }),
            el("div", { className: "glance" }, recipe.steps.map((step, n) => el("div", { className: "glance-row" }, [
                el("span", { className: "num", text: n + 1 }),
                el("span", { className: "grow" }, [el("span", { className: "glance-title", text: step.title }), el("span", { className: "glance-text", text: step.text })]),
                step.dur ? el("span", { className: "dur-chip" }, [icon(P.clock, 14, 2.4), fmtDur(step.dur)]) : null,
            ]))),
            button("btn-amber big", [icon(P.play, 22), t("Start guided cooking")], begin("guided")),
            button("btn-soft", [t("Skip to recording yield"), icon(P.next, 18)], begin("yield")),
            el("div", { className: "fine", text: t("Either way, the batch shows up in Ongoing so anyone can pick it up.") }),
        ]),
    ]);
}

// ---------- guided cooking ----------

function guidedView() {
    const s = session(state.sid);
    if (!s) return goneView();
    const c = comp(s.component);
    const recipe = state.data.recipes[s.recipe];
    if (!recipe) return yieldView();
    const i = s.step;
    const step = recipe.steps[i];
    const last = i === recipe.steps.length - 1;
    const setStep = (n) => act("step", { session: s.id, step: n }).then((ok) => ok && render());
    const toYield = async () => {
        if (await act("phase", { session: s.id, phase: "yield" })) go({ view: "yield", entry: "" });
    };
    const next = () => (last ? toYield() : setStep(i + 1));

    const others = Object.entries(s.timers ?? {}).filter(([key]) => Number(key) !== i).map(([key, timer]) => {
        const label = el("span", { className: "num-tab" });
        const chip = button("other-timer", [icon(P.clock, 16), el("span", { text: recipe.steps[Number(key)].title }), label], () => setStep(Number(key)));
        const update = () => {
            const left = timerLeft(timer);
            label.textContent = left <= 0 ? t("done") : fmtLeft(left);
            chip.classList.toggle("done", left <= 0);
            chip.classList.toggle("breathe", left <= 0);
        };
        update();
        tickers.push(update);
        return chip;
    });

    const uses = step.uses.map((id) => recipe.ingredients.find((row) => row.id === id)).filter(Boolean).map((row) => {
        const ing = ingredient(row.ingredient);
        return el("span", { className: "use-chip" }, [el("span", { className: "disp", text: amount(row.qty * s.batch, ing.unit) }), ing.name]);
    });

    return el("div", { className: "view swap column" }, [
        el("header", { className: "cook-head" }, [
            button("circle-btn", [icon(P.back, 22)], () => { go({ view: "home" }); toast(t("Saved to Ongoing. Pick it up any time.")); },
                { "aria-label": t("Back to Cookery, keep this batch in Ongoing") }),
            el("div", { className: "grow" }, [
                el("div", { className: "overline", text: fmt("Guided cooking · batch ×%s · started %s", s.batch, clock(s.started_at)) }),
                el("h1", { className: "cook-title sm", text: c.name }),
            ]),
            ...others,
            button("pill-btn white", [t("Record yield"), icon(P.next, 18)], toYield),
        ]),
        el("div", { className: "segments", attrs: { role: "list", "aria-label": t("Steps") } }, recipe.steps.map((p, k) => {
            const timer = s.timers?.[k];
            const running = timer && timer.paused == null && timerLeft(timer) > 0;
            return button("segment", [el("span", { className: "seg-bar " + (k < i ? "past" : k === i ? "now" : running ? "running" : "") })],
                () => setStep(k), { role: "listitem", "aria-label": fmt("Step %s: %s", k + 1, p.title), "aria-current": k === i ? "step" : null });
        })),
        el("div", { className: "split grow-fill" }, [
            el("section", { className: "panel step-panel", attrs: { "aria-label": t("Current step") } }, [
                el("div", { className: "overline brand", text: fmt("Step %s of %s", i + 1, recipe.steps.length) }),
                el("h2", { className: "step-title", text: step.title }),
                el("p", { className: "step-text", text: step.text }),
                uses.length ? el("div", { className: "gap-top" }, [el("div", { className: "overline", text: t("For this step") }), el("div", { className: "use-row" }, uses)]) : null,
                el("div", { className: "grow" }),
                el("div", { className: "step-nav" }, [
                    button("btn-soft nav", [icon(P.back), t("Back")], () => setStep(i - 1), { disabled: i === 0 }),
                    el("div", { className: "grow" }),
                    button("btn-amber nav", [last ? t("Record yield") : t("Next step"), icon(P.next)], next),
                ]),
            ]),
            el("aside", { className: "side-col" }, [
                step.dur ? timerCard(s, step, i, next) : el("section", { className: "panel no-timer" }, [
                    el("div", { className: "scene-frame" }, [cookScene("prep", true, 240)]),
                    el("div", { className: "strong gap-sm", text: t("No timer on this step") }),
                    el("div", { className: "muted", text: t("Tap Next when you're done.") }),
                ]),
                el("section", { className: "panel up-next", attrs: { "aria-label": t("Up next") } }, [
                    el("div", { className: "overline", text: t("Up next") }),
                    ...recipe.steps.slice(i + 1, i + 4).map((p, k) => el("div", { className: "next-row" }, [
                        el("span", { className: "num sm", text: i + k + 2 }), el("span", { className: "grow ellipsis", text: p.title }),
                        el("span", { className: "muted", text: p.dur ? fmtDur(p.dur) : "" }),
                    ])),
                    recipe.steps.length - i <= 3 ? el("div", { className: "muted gap-sm", text: t("After this: count what you made.") }) : null,
                ]),
            ]),
        ]),
    ]);
}

function timerCard(s, step, i, next) {
    const timer = s.timers?.[i];
    const idle = !timer;
    const paused = timer && timer.paused != null;
    const total = timer ? timer.dur * 1000 : step.dur * 1000;
    const left = () => (timer ? timerLeft(timer) : step.dur * 1000);
    const done = timer && !paused && left() <= 0;
    const running = timer && !paused && left() > 0;
    const op = (name) => () => act("timer", { session: s.id, step: i, op: name }).then((ok) => ok && render());
    const time = el("div", { className: "timer-time disp", attrs: { role: "timer" } });
    const bar = el("div", { className: "timer-bar" });
    const update = () => {
        time.textContent = done ? "0:00" : fmtLeft(left());
        bar.style.width = idle ? "0%" : `${Math.min(100, Math.round(((total - left()) / total) * 1000) / 10)}%`;
    };
    update();
    tickers.push(update);
    const verb = SCENE_VERBS[step.scene] ? t(SCENE_VERBS[step.scene]) : t("Running");
    return el("section", { className: "panel timer-card" + (done ? " done" : ""), attrs: { "aria-label": t("Step timer") } }, [
        el("div", { className: "timer-top" }, [
            el("span", { className: "overline", text: fmt("Timer · %s", fmtDur(step.dur)) }),
            el("span", { className: "timer-status" + (done ? " done" : running ? " running" : ""), text: done ? t("Time's up!") : running ? `${verb}…` : paused ? t("Paused") : t("Ready") }),
        ]),
        el("div", { className: "scene-frame" + (done ? " ring" : "") }, [cookScene(step.scene, !!running, 260)]),
        time,
        el("div", { className: "timer-track" }, [bar]),
        el("div", { className: "timer-buttons" }, [
            button("timer-main" + (running ? " dark" : ""), [icon(done ? P.next : running ? P.pause : P.play), done ? t("Next step") : running ? t("Pause") : paused ? t("Resume") : t("Start timer")],
                done ? next : op(running ? "pause" : paused ? "resume" : "start")),
            button("timer-side", [t("+1 min")], op("add"), { disabled: idle, "aria-label": t("Add one minute") }),
            button("timer-side square", [icon(P.reset, 20, 2.2)], op("reset"), { disabled: idle, "aria-label": t("Reset timer") }),
        ]),
    ]);
}

// ---------- recording the yield ----------

const QUICK = {
    ea: [[5, () => t("Handful")], [10, () => t("Half tray")], [20, () => t("Full tray")]],
    kg: [[0.5, () => t("Half kilo")], [1, () => t("One kilo")], [2, () => t("Two kilos")]],
    L: [[0.5, () => t("Half litre")], [1, () => t("One litre")], [5, () => t("Five litres")]],
};

function yieldView() {
    const s = session(state.sid);
    if (!s) return goneView();
    const c = comp(s.component);
    const recipe = state.data.recipes[s.recipe];
    const [unit, factor] = shown(c.unit);
    const each = c.unit === "ea";
    const total = s.log.reduce((sum, entry) => sum + entry.amt, 0) / factor;
    const expected = recipe ? (recipe.yield * s.batch) / factor : 0;
    const add = async (n) => {
        if (!(n > 0)) return;
        if (await act("count", { session: s.id, amount: Math.round(n * factor * 1000) / 1000 })) {
            state.bump = !state.bump;
            render();
        }
    };
    const e = state.entry;
    const decimal = !each;
    const press = (ch) => () => {
        let v = state.entry;
        if (ch === "back") v = v.slice(0, -1);
        else if (ch === ".") { if (!decimal || v.includes(".")) return; v = (v || "0") + "."; }
        else { if (v.length >= 6) return; v = v === "0" ? ch : v + ch; }
        state.entry = v;
        render();
    };
    const ev = parseFloat(e);
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => button("key", [d], press(d), { "aria-label": d }));
    keys.push(button("key text", ["."], press("."), { "aria-label": t("Decimal point"), disabled: !decimal }));
    keys.push(button("key", ["0"], press("0"), { "aria-label": "0" }));
    keys.push(button("key plain", [icon(P.backspace, 24, 2)], press("back"), { "aria-label": t("Delete last digit"), disabled: !e }));
    const perUnit = !recipe && c.per_unit.length
        ? fmt("Each one takes %s out of inventory.", c.per_unit.map((row) => `${amount(row.qty * factor, ingredient(row.ingredient).unit)} ${ingredient(row.ingredient).name.toLowerCase()}`).join(", "))
        : "";
    const finish = async () => {
        const result = await act("finish", { session: s.id });
        if (result) go({ view: "done", sid: null, done: result.done });
    };
    const back = async () => {
        if (recipe) {
            if (await act("phase", { session: s.id, phase: "guided" })) go({ view: "guided" });
            return;
        }
        go({ view: "home" });
        toast(t("Saved to Ongoing. Pick it up any time."));
    };
    const quick = (QUICK[c.unit === "ea" ? "ea" : unit] ?? [[5, () => t("Five")], [10, () => t("Ten")], [20, () => t("Twenty")]])
        .map(([n, label]) => button("quick-add", [el("span", { className: "disp", text: `+${n}` }), el("span", { className: "muted", text: label() })], () => add(n)));
    return el("div", { className: "view swap split" }, [
        el("section", { className: "panel yield-panel", attrs: { "aria-label": t("Yield counter") } }, [
            el("div", { className: "yield-head" }, [
                button("circle-btn soft", [icon(P.back, 22)], back, { "aria-label": recipe ? t("Back to the steps") : t("Back to Cookery, keep this in Ongoing") }),
                el("div", { className: "grow" }, [el("div", { className: "overline", text: t("Recording yield") }), el("h1", { className: "cook-title sm", text: c.name })]),
                el("span", { className: "muted right", text: (mine(s) ? t("Started") : fmt("%s started", who(s))) + " " + clock(s.started_at) + (recipe ? ` · ${fmt("batch ×%s", s.batch)}` : "") }),
            ]),
            el("div", { className: "yield-count" }, [
                el("div", { className: "yield-number" }, [
                    el("span", { className: "disp yield-total" + (state.bump ? " bump" : " bump2"), attrs: { "aria-live": "polite" }, text: trim(total) }),
                    el("span", { className: "disp yield-unit", text: unit }),
                ]),
                el("div", { className: "muted strong", text: recipe ? (total >= expected ? fmt("Batch target reached · about %s expected", trim(expected)) : fmt("of about %s expected from this batch", trim(expected))) : t("Made one at a time · no batch target") }),
                recipe ? el("div", { className: "yield-track" }, [el("div", { className: "yield-bar" + (total >= expected ? " full" : ""), attrs: { style: `width:${expected ? Math.min(100, Math.round((total / expected) * 100)) : 0}%` } })]) : null,
            ]),
            each ? button("big-add", [
                el("span", { className: "big-add-icon" }, [icon(P.plus, 44, 2.8)]),
                el("span", {}, [el("span", { className: "disp big-add-label", text: t("Add one") }), el("span", { className: "big-add-hint", text: t("Tap each time one goes on the tray") })]),
            ], () => add(1)) : null,
            el("div", { className: "quick-row" }, quick),
            perUnit ? el("div", { className: "muted center gap-sm", text: perUnit }) : null,
        ]),
        el("aside", { className: "panel side-panel keypad-panel", attrs: { "aria-label": t("Add an amount") } }, [
            el("div", { className: "overline ink", text: t("Add an amount") }),
            el("div", { className: "entry-box", attrs: { "aria-live": "polite" } }, [
                el("span", { className: "disp entry" + (e ? "" : " empty"), text: e ? `+${e}` : "0" }), el("span", { className: "muted strong", text: unit }),
            ]),
            el("div", { className: "keypad cook-keys" }, keys),
            button("btn-amber wide" + (ev > 0 ? "" : " off"), [ev > 0 ? fmt("Add %s %s", e, unit) : t("Type an amount")], async () => {
                if (!(ev > 0)) return;
                state.entry = "";
                await add(Math.round(ev * 1000) / 1000);
            }, { disabled: !(ev > 0) }),
            el("div", { className: "section-cap gap-top" }, [
                el("span", { className: "overline", text: t("Added so far") }),
                button("link-btn", [t("Undo last")], () => act("uncount", { session: s.id }).then((ok) => ok && render()), { disabled: !s.log.length }),
            ]),
            el("div", { className: "log-chips" }, s.log.length
                ? s.log.slice().reverse().slice(0, 14).map((entry) => el("span", { className: "log-chip" }, [`+${trim(entry.amt / factor)}`, el("span", { text: clock(entry.at) })]))
                : [el("span", { className: "muted", text: t("Nothing yet. Every add shows up here.") })]),
            button("btn-dark wide" + (total > 0 ? "" : " off"), [icon(P.check, 20, 2.6), total > 0 ? fmt("Finish · %s %s", trim(total), unit) : t("Add some first")], finish, { disabled: !(total > 0) }),
        ]),
    ]);
}

// ---------- done ----------

function doneView() {
    const d = state.done;
    const c = comp(d.component);
    if (!c) return homeView();
    const [unit, factor] = shown(c.unit);
    const made = d.made / factor;
    const recipe = d.recipe ? state.data.recipes[d.recipe] : null;
    const stats = [{ label: t("Made"), value: `${trim(made)} ${unit}`, sub: recipe ? fmt("batch ×%s", d.batch) : t("one at a time") }];
    if (recipe && d.expected) {
        const expected = d.expected / factor;
        const waste = Math.max(0, expected - made);
        const pct = Math.round((waste / expected) * 100);
        const over = made > expected;
        // Recorded as ingredient waste by the server: the unused share of what the batch reserved
        const top = (d.waste ?? []).slice(0, 2)
            .map((row) => `${amount(row.qty, ingredient(row.ingredient)?.unit ?? "")} ${(ingredient(row.ingredient)?.name ?? "").toLowerCase()}`);
        stats.push({ label: t("Expected"), value: `${trim(expected)} ${unit}`, sub: t("from the recipe") });
        stats.push({
            label: over ? t("Extra") : t("Waste"), value: over ? `+${trim(made - expected)} ${unit}` : `${trim(waste)} ${unit}`,
            sub: over ? t("more than the recipe says") : waste ? `${pct}%` + (top.length ? ` · ${fmt("logged as waste: %s", top.join(", "))}` : "") : t("none, nice work"), warn: !over && pct >= 10,
        });
    }
    stats.push({ label: t("Time"), value: fmtDur(d.minutes * 60), sub: t("from start to finish") });
    const next = state.data.transforms.filter((tf) => tf.from === c.id).map((tf) => button("next-btn", [
        el("span", { className: "scene-box sm" }, [cookScene(tf.scene, true, 80)]),
        el("span", {}, [el("span", { className: "strong block", text: tf.name }), el("span", { className: "next-sub", text: fmt("Move them to %s · %s", comp(tf.to).name.toLowerCase(), fmtDur(tf.dur)) })]),
    ], () => go({ view: "transform", tfId: tf.id, sid: null, tfQty: Math.min(d.made, comp(tf.from).have), tfText: null })));
    const colors = ["#F4A93B", "#1F4A3A", "#DCEBE2", "#F4C46B", "#14261F", "#E8862B"];
    const confetti = Array.from({ length: 36 }, (_, k) => el("span", { className: "confetti", attrs: { "aria-hidden": "true",
        style: `left:${(k * 37) % 100}%;width:${8 + (k % 3) * 3}px;height:${12 + (k % 4) * 3}px;border-radius:${k % 2 ? "3px" : "50%"};background:${colors[k % colors.length]};animation-delay:${(k % 9) * 0.12}s` } }));
    const title = c.unit === "ea" ? fmt("Yay! You made %s %s", trim(made), c.name.toLowerCase()) : fmt("Yay! You made %s %s of %s", trim(made), unit, c.name.toLowerCase());
    return el("div", { className: "view swap done-view panel" }, [
        ...confetti,
        el("div", { className: "done-badge pop" }, [icon(P.big, 50, 2.6)]),
        el("h1", { className: "done-title", attrs: { role: "status" }, text: title }),
        el("div", { className: "done-sub", text: fmt("Added to inventory · %s on hand now", qty(d.have, c.unit)) }),
        el("div", { className: "done-stats" }, stats.map((x) => el("div", { className: "stat" + (x.warn ? " warn" : "") }, [
            el("div", { className: "overline", text: x.label }), el("div", { className: "disp stat-value", text: x.value }), el("div", { className: "stat-sub", text: x.sub }),
        ]))),
        next.length ? el("div", { className: "overline gap-top-lg", text: t("What's next?") }) : null,
        next.length ? el("div", { className: "next-row-btns" }, next) : null,
        button("btn-soft", [t("Back to Cookery")], () => go({ view: "home", done: null })),
    ]);
}

// ---------- transforms ----------

function transformView() {
    const tf = transform(state.tfId);
    if (!tf) return homeView();
    const from = comp(tf.from), to = comp(tf.to);
    const s = state.sid ? session(state.sid) : null;
    if (state.sid && !s) return goneView();
    const [unit, factor] = shown(from.unit);
    const verb = SCENE_VERBS[tf.scene] ? t(SCENE_VERBS[tf.scene]) : tf.name;
    const head = [
        backButton(t("Transform"), () => go({ view: "home", mode: "transform", sid: null })),
        el("div", { className: "overline gap-top", text: t("Transform") }),
        el("h1", { className: "big-title", text: tf.name }),
        el("div", { className: "flow-row" }, [
            el("div", { className: "flow-box" }, [el("div", { className: "overline", text: t("From") }), el("div", { className: "flow-name", text: from.name }), el("div", { className: "muted strong", text: fmt("%s on hand", qty(from.have, from.unit)) })]),
            el("span", { className: "flow-arrow" }, [icon(P.next, 22)]),
            el("div", { className: "flow-box cold" }, [el("div", { className: "overline", text: t("To") }), el("div", { className: "flow-name", text: to.name }),
                el("div", { className: "strong cold-ink", text: fmt("%s on hand", qty(to.have, to.unit)) + (s ? ` · ${fmt("%s on the way", qty(s.qty * tf.ratio, to.unit))}` : "") })]),
        ]),
    ];
    let body, aside;
    if (!s) {
        const max = from.have / factor;
        const n = Math.max(0, Math.min(state.tfQty / factor, max));
        const over = state.tfText != null && parseFloat(state.tfText) > max;
        const setQty = (value) => go({ tfText: null, tfQty: Math.round(value * factor * 1000) / 1000 }, { push: false });
        const input = el("input", { className: "disp qty-in", attrs: { type: "text", inputmode: "decimal", "data-keep": "qty", value: state.tfText ?? trim(n),
            "aria-label": fmt("How many, up to %s", trim(max)) }, on: {
            input: (e) => {
                const raw = e.target.value.replace(/[^0-9.]/g, "");
                const v = parseFloat(raw);
                state.tfText = raw;
                state.tfQty = Number.isNaN(v) ? 0 : Math.min(max, v) * factor;
                render();
            },
            focus: (e) => e.target.select(),
        } });
        const quick = [[10, "10"], [20, "20"], [max, fmt("All %s", trim(max))]].filter(([v], k) => k === 2 || v < max)
            .map(([v, label]) => button("chip-btn", [label], () => setQty(v), { "aria-pressed": String(Math.abs(n - v) < 1e-9) }));
        body = [
            el("div", { className: "how-many" }, [
                el("div", { className: "overline ink", text: t("How many?") }),
                el("div", { className: "qty-row" }, [
                    button("round-btn xl", [icon(P.minus, 30, 2.6)], () => setQty(Math.max(0, n - 1)), { "aria-label": t("Fewer"), disabled: n <= 0 }),
                    el("span", { className: "qty-col" }, [input, el("span", { className: "muted strong", text: over ? fmt("Only %s %s available, using %s", trim(max), unit, trim(max)) : fmt("%s of %s available · tap the number to type", unit, trim(max)) })]),
                    button("round-btn xl", [icon(P.plus, 30, 2.6)], () => setQty(Math.min(max, n + 1)), { "aria-label": t("More"), disabled: n >= max }),
                ]),
                el("div", { className: "chip-row" }, quick),
            ]),
            button("btn-amber big" + (n > 0 ? "" : " off"), [icon(P.play, 22), n > 0 ? fmt("Start %s %s %s", verb.toLowerCase(), trim(n), unit) : t("Nothing to move")], async () => {
                if (!(n > 0)) return;
                const result = await act("transform", { transform: tf.id, qty: Math.round(n * factor * 1000) / 1000 });
                if (!result) return;
                toast(fmt("%s %s %s. It's in Ongoing now.", verb, trim(n), unit));
                go({ sid: result.session, tfText: null });
            }, { disabled: !(n > 0) }),
            el("div", { className: "fine", text: fmt("They leave %s right away and land in %s when you mark it done.", from.name.toLowerCase(), to.name.toLowerCase()) }),
        ];
        aside = [
            el("div", { className: "scene-frame" }, [cookScene(tf.scene, false, 300)]),
            el("div", { className: "overline gap-top", text: t("Takes about") }),
            el("div", { className: "disp tf-big", text: fmtDur(tf.dur) }),
            el("div", { className: "brand strong", text: fmt("Ready around %s", clock(nowMs() + tf.dur * 1000)) }),
        ];
    } else {
        const left = () => Math.max(0, s.ends_at - nowMs());
        const done = left() <= 0;
        const big = el("div", { className: "disp tf-big" });
        const bar = el("div", { className: "tf-bar" });
        const update = () => {
            big.textContent = left() <= 0 ? t("Done!") : fmtLeft(left());
            bar.style.width = `${Math.min(100, Math.round(((tf.dur * 1000 - left()) / (tf.dur * 1000)) * 100))}%`;
        };
        update();
        tickers.push(update);
        body = [
            el("div", { className: "how-many" }, [
                el("div", { className: "disp running-qty", text: `${trim(s.qty / factor)} ${unit}` }),
                el("div", { className: "muted strong", text: fmt("%s since %s", verb, clock(s.started_at)) + (mine(s) ? "" : ` · ${fmt("started by %s", s.started_by?.name ?? who(s))}`) }),
            ]),
            button(done ? "btn-amber big" : "btn-dark big", [icon(P.check, 22, 2.6), done ? t("Mark as done") : t("Mark as done early")], async () => {
                if (!(await act("transform_done", { session: s.id }))) return;
                toast(fmt("%s %s moved to %s", trim(s.qty / factor), unit, to.name.toLowerCase()));
                go({ view: "home", mode: "transform", sid: null });
            }),
            button("danger-link", [t("Cancel and put them back")], async () => {
                if (!(await act("transform_cancel", { session: s.id }))) return;
                toast(fmt("Cancelled. %s %s back in %s", trim(s.qty / factor), unit, from.name.toLowerCase()));
                go({ view: "home", mode: "transform", sid: null });
            }),
        ];
        aside = [
            el("div", { className: "scene-frame" + (done ? " ring" : "") }, [cookScene(tf.scene, !done, 300)]),
            el("div", { className: "overline gap-top", text: done ? t("Ready") : t("Time left") }),
            big,
            el("div", { className: "tf-track" }, [bar]),
            el("div", { className: "brand strong", text: done ? fmt("Ready since %s", clock(s.ends_at)) : fmt("Ready at %s", clock(s.ends_at)) }),
        ];
    }
    return el("div", { className: "view swap split" }, [
        el("section", { className: "panel tf-panel", attrs: { "aria-label": t("Transform") } }, [...head, ...body]),
        el("aside", { className: "panel side-panel tf-side", attrs: { "aria-label": t("Transform timer") } }, [
            ...aside, el("div", { className: "grow" }), tf.how ? el("div", { className: "how-box", text: tf.how }) : null,
        ]),
    ]);
}

// A batch that finished or was picked up and closed on another screen
function goneView() {
    return el("div", { className: "view swap done-view panel" }, [
        el("h1", { className: "done-title sm", text: t("That batch is finished") }),
        el("div", { className: "done-sub", text: t("Someone wrapped it up on another screen.") }),
        button("btn-soft", [t("Back to Cookery")], () => go({ view: "home", sid: null })),
    ]);
}

// ---------- start ----------

onLiveUpdate("cookery", refresh);
onLiveUpdate("inventory", refresh);
(async () => {
    const response = await fetch(URL_, { headers: { Accept: "application/json" } });
    take(await response.json());
    go(fromRoute(), { push: false });
})();
