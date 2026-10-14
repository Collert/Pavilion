// Helpers shared by the register, cash drawer and lock screens. Money is in integer cents.
import { LIVE_ORIGIN } from "/static/js/live.js";

export const $ = (selector, root = document) => root.querySelector(selector);
export const csrftoken = document.querySelector("input[name=csrfmiddlewaretoken]")?.value;

export function fmt(cents) {
    const abs = Math.abs(Math.round(cents));
    const text = "$" + (abs / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return cents < 0 ? "−" + text : text;
}
export const fmtSigned = (cents) => (cents < 0 ? "−" : "+") + fmt(Math.abs(cents));
export const dollars = (cents) => (Math.round(cents) / 100).toFixed(2);
export const toCents = (text) => {
    const value = parseFloat(String(text).replace(/[^0-9.]/g, ""));
    return Number.isFinite(value) ? Math.round(value * 100) : 0;
};

export const ICONS = {
    check: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"></path></svg>',
    checkBig: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"></path></svg>',
    checkHuge: '<svg aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"></path></svg>',
    up: '<svg aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"></path></svg>',
    down: '<svg aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7"></path></svg>',
    minus: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 12h14"></path></svg>',
    plus: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg>',
    minusSm: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M5 12h14"></path></svg>',
    plusSm: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg>',
    plusBig: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"></path></svg>',
    chevron: '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"></path></svg>',
    chevronMd: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"></path></svg>',
    trash: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12h10l1-12M9 7V4h6v3"></path></svg>',
    dots: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"></circle><circle cx="12" cy="12" r="2"></circle><circle cx="19" cy="12" r="2"></circle></svg>',
    discount: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 5 5 19"></path><circle cx="7" cy="7" r="2"></circle><circle cx="17" cy="17" r="2"></circle></svg>',
    sliders: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"></path><circle cx="16" cy="7" r="2"></circle><circle cx="8" cy="17" r="2"></circle></svg>',
    close: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"></path></svg>',
    alert: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16.5v.01"></path></svg>',
    backspace: '<svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-6-6 6-6z"></path><path d="m13 10 4 4M17 10l-4 4"></path></svg>',
};

// el("button", {className, text, icon, attrs, on}, children). Text always goes in with textContent; `icon`
// takes one of the SVG strings above, never data.
export function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    if (props.className) node.className = props.className;
    if (props.text !== undefined) node.textContent = props.text;
    if (props.icon) node.insertAdjacentHTML("beforeend", ICONS[props.icon]);
    for (const [name, value] of Object.entries(props.attrs || {})) {
        if (value === false || value === null || value === undefined) continue;
        node.setAttribute(name, value === true ? "" : value);
    }
    for (const [event, handler] of Object.entries(props.on || {})) node.addEventListener(event, handler);
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

export function toast(message, { error = false } = {}) {
    const node = el("div", { className: "toast" + (error ? " error" : ""), attrs: { role: "status" } }, [
        el("span", { className: "toast-icon", icon: error ? "alert" : "checkBig" }), message,
    ]);
    $("#toasts").replaceChildren(node);
    setTimeout(() => node.remove(), error ? 5000 : 2700);
    if (error) node.style.animationDuration = "5s";
}

export async function post(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": csrftoken, "Content-Type": "application/json", "Accept": "application/json", "X-Live-Origin": LIVE_ORIGIN },
        body: JSON.stringify(body),
    });
    let data = {};
    try { data = await response.json(); } catch { /* not JSON */ }
    return { ok: response.ok, status: response.status, data };
}

// Close buttons inside sheets
export function wireSheets() {
    document.querySelectorAll("dialog [data-close]").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog").close());
    });
}

// A cents keypad (1-9, Clear, 0, backspace) like the drawer and lock screens use.
export function keypad(container, { onDigit, onClear, onBack, clearLabel }) {
    const keys = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => el("button", { className: "key", text: n, attrs: { "aria-label": String(n) }, on: { click: () => onDigit(n) } }));
    const clear = el("button", { className: "key text", text: clearLabel || gettext("Clear"), attrs: { "aria-label": gettext("Clear") }, on: { click: onClear } });
    const zero = el("button", { className: "key", text: 0, attrs: { "aria-label": "0" }, on: { click: () => onDigit(0) } });
    const back = el("button", { className: "key plain", icon: "backspace", attrs: { "aria-label": gettext("Delete last digit") }, on: { click: onBack } });
    container.replaceChildren(...keys, clear, zero, back);
    return { clear, back };
}
