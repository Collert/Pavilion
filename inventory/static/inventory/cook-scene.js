// The little animated kitchen scenes in Cookery (docs/mockups/cookery/Cooking scene-html): a pot on the boil, a
// pan, the oven, rising dough, a snowflake, a thawing block, a chopping board. cookScene(kind, running, size)
// returns an SVG element; `running` adds the SMIL animations. Built from constants only (no data goes in).

const LABELS = {
    boil: "Pot on the boil", simmer: "Pot simmering", fry: "Pan sizzling", bake: "Oven baking", rise: "Dough rising",
    rest: "Dough resting", chill: "Chilling", freeze: "Freezing", thaw: "Thawing", prep: "Prep work",
};

const anim = (on, markup) => (on ? markup : "");

function pot(kind, run) {
    const liquid = kind === "simmer" ? "#C98A4B" : "#CFE6EE";
    const bubble = kind === "simmer" ? "2.2s" : "1.1s";
    const steam = (x, y, begin) => `<path d="M${x} ${y}c-6-8 6-12 0-20" fill="none" stroke="#FFFFFF" stroke-width="5" stroke-linecap="round" opacity="0"><animate attributeName="opacity" values="0;0.85;0" dur="2.4s" begin="${begin}" repeatCount="indefinite"/><animateTransform attributeName="transform" type="translate" values="0 6;0 -14" dur="2.4s" begin="${begin}" repeatCount="indefinite"/></path>`;
    const dot = (cx, cy, r, max, begin) => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#FFFFFF" opacity="0"><animate attributeName="opacity" values="0;0.9;0" dur="${bubble}" begin="${begin}" repeatCount="indefinite"/><animate attributeName="r" values="1;${max};${max + 1}" dur="${bubble}" begin="${begin}" repeatCount="indefinite"/></circle>`;
    return `<g>
        <path d="M104 162c0-8 6-10 6-17 4 4 6 8 6 12 2-3 2-6 2-8 6 5 8 10 8 13a11 11 0 0 1-22 0z" fill="#F4A93B">${anim(run, '<animateTransform attributeName="transform" type="scale" values="1 1;1.04 0.9;0.97 1.06;1 1" dur="0.6s" repeatCount="indefinite" additive="sum"/>')}</path>
        <path d="M126 164c0-6 4-8 4-13 3 3 4 6 4 9 2-2 2-4 2-6 4 4 5 7 5 9a7.5 7.5 0 0 1-15 1z" fill="#E8862B">${anim(run, '<animate attributeName="opacity" values="1;0.6;1" dur="0.5s" repeatCount="indefinite"/>')}</path>
        <rect x="58" y="70" width="124" height="74" rx="18" fill="#14261F"/>
        <rect x="44" y="82" width="20" height="10" rx="5" fill="#14261F"/>
        <rect x="176" y="82" width="20" height="10" rx="5" fill="#14261F"/>
        <ellipse cx="120" cy="72" rx="62" ry="12" fill="#1F4A3A"/>
        <ellipse cx="120" cy="73" rx="54" ry="8" fill="${liquid}"/>
        ${anim(run, `<g>${dot(98, 74, 5, 6, "0s")}${dot(128, 72, 4, 5, "0.35s")}${dot(148, 75, 3, 4, "0.7s")}${steam(96, 56, "0s")}${steam(122, 52, "0.8s")}${steam(146, 56, "1.6s")}</g>`)}
    </g>`;
}

function fry(run) {
    const hop = (cx, cy, rx, ry, dy, dur, begin) => `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}">${anim(run, `<animateTransform attributeName="transform" type="translate" values="0 0;0 -${dy};0 0" dur="${dur}" begin="${begin}" repeatCount="indefinite"/>`)}</ellipse>`;
    const spark = (cx, cy, r, dur, begin) => `<circle cx="${cx}" cy="${cy}" r="${r}"><animate attributeName="opacity" values="0;1;0" dur="${dur}" begin="${begin}" repeatCount="indefinite"/></circle>`;
    return `<g>
        ${anim(run, `<g><path d="M104 166c0-6 4-8 4-13 3 3 4 6 4 9 2-2 2-4 2-6 4 4 5 7 5 9a7.5 7.5 0 0 1-15 1z" fill="#F4A93B"><animate attributeName="opacity" values="1;0.6;1" dur="0.45s" repeatCount="indefinite"/></path><path d="M124 166c0-6 4-8 4-13 3 3 4 6 4 9 2-2 2-4 2-6 4 4 5 7 5 9a7.5 7.5 0 0 1-15 1z" fill="#E8862B"><animate attributeName="opacity" values="0.6;1;0.6" dur="0.5s" repeatCount="indefinite"/></path></g>`)}
        <rect x="170" y="118" width="58" height="12" rx="6" fill="#14261F"/>
        <path d="M44 112h136a4 4 0 0 1 4 4c0 18-14 30-32 30H76c-18 0-32-12-32-30a4 4 0 0 1 4-4z" fill="#14261F"/>
        <ellipse cx="112" cy="113" rx="64" ry="9" fill="#1F4A3A"/>
        <g fill="#F4C46B">${hop(88, 108, 9, 5, 10, "0.9s", "0s")}${hop(112, 110, 10, 5, 14, "1.1s", "0.3s")}${hop(136, 108, 8, 4.5, 8, "0.8s", "0.15s")}</g>
        ${anim(run, `<g fill="#FFFFFF">${spark(72, 96, 2.5, "0.6s", "0s")}${spark(150, 92, 2.5, "0.7s", "0.2s")}${spark(122, 84, 2, "0.5s", "0.4s")}${spark(96, 86, 2, "0.65s", "0.1s")}</g>`)}
    </g>`;
}

function bake(run) {
    const heat = (x, begin) => `<path d="M${x} 44c-5-5 5-9 0-14" opacity="0"><animate attributeName="opacity" values="0;1;0" dur="1.8s" begin="${begin}" repeatCount="indefinite"/><animateTransform attributeName="transform" type="translate" values="0 4;0 -10" dur="1.8s" begin="${begin}" repeatCount="indefinite"/></path>`;
    return `<g>
        <rect x="56" y="52" width="128" height="104" rx="18" fill="#14261F"/>
        <circle cx="80" cy="66" r="4" fill="#5B6A62"/><circle cx="96" cy="66" r="4" fill="#5B6A62"/>
        <rect x="140" y="62" width="30" height="8" rx="4" fill="#5B6A62"/>
        <rect x="70" y="80" width="100" height="62" rx="12" fill="#3A2A12"/>
        <rect x="70" y="80" width="100" height="62" rx="12" fill="#F4A93B" opacity="0.55">${anim(run, '<animate attributeName="opacity" values="0.35;0.75;0.35" dur="2s" repeatCount="indefinite"/>')}</rect>
        <path d="M86 132c0-10 8-16 18-16s18 6 18 16z M118 132c0-10 8-16 18-16s18 6 18 16z" fill="#F4C46B"/>
        ${anim(run, `<g fill="none" stroke="#F4A93B" stroke-width="4" stroke-linecap="round">${heat(92, "0s")}${heat(120, "0.6s")}${heat(148, "1.2s")}</g>`)}
    </g>`;
}

function rise(kind, run) {
    const z = (x, y, size, begin) => `<text x="${x}" y="${y}" font-size="${size}" opacity="0"><animate attributeName="opacity" values="0;1;0" dur="3s" begin="${begin}" repeatCount="indefinite"/><animateTransform attributeName="transform" type="translate" values="0 6;8 -10" dur="3s" begin="${begin}" repeatCount="indefinite"/>z</text>`;
    return `<g>
        <ellipse cx="120" cy="104" rx="56" ry="38" fill="#F6E3BF">${anim(run, '<animate attributeName="ry" values="30;44;30" dur="4s" repeatCount="indefinite"/><animate attributeName="cy" values="110;98;110" dur="4s" repeatCount="indefinite"/>')}</ellipse>
        <path d="M48 104h144l-10 40a16 16 0 0 1-15 12H73a16 16 0 0 1-15-12z" fill="#1F4A3A"/>
        <rect x="44" y="100" width="152" height="10" rx="5" fill="#14261F"/>
        ${kind === "rest" ? `<g fill="#1F4A3A" font-family="Manrope, sans-serif" font-weight="800">${z(158, 58, 18, "0s")}${z(170, 44, 24, "1s")}</g>` : ""}
    </g>`;
}

function cold(kind, run) {
    const drop = (x, begin) => `<path d="M${x} 136c3 5 5 8 5 10a5 5 0 0 1-10 0c0-2 2-5 5-10z"><animateTransform attributeName="transform" type="translate" values="0 -4;0 8" dur="1.4s" begin="${begin}" repeatCount="indefinite"/><animate attributeName="opacity" values="1;0" dur="1.4s" begin="${begin}" repeatCount="indefinite"/></path>`;
    const flake = (cx, cy, r, dx, dy, dur, begin) => `<circle cx="${cx}" cy="${cy}" r="${r}"><animateTransform attributeName="transform" type="translate" values="0 0;${dx} ${dy}" dur="${dur}" begin="${begin}" repeatCount="indefinite"/><animate attributeName="opacity" values="0;1;1;0" dur="${dur}" begin="${begin}" repeatCount="indefinite"/></circle>`;
    const thaw = `<g>
        <rect x="86" y="70" width="68" height="64" rx="14" fill="#CFE6EE" stroke="#8FC0D2" stroke-width="4">${anim(run, '<animate attributeName="height" values="64;56;64" dur="5s" repeatCount="indefinite"/><animate attributeName="y" values="70;78;70" dur="5s" repeatCount="indefinite"/>')}</rect>
        <path d="M98 84h16" stroke="#FFFFFF" stroke-width="5" stroke-linecap="round"/>
        <ellipse cx="120" cy="146" rx="46" ry="7" fill="#CFE6EE"/>
        ${anim(run, `<g fill="#8FC0D2">${drop(104, "0s")}${drop(138, "0.7s")}</g>`)}
    </g>`;
    const freeze = `<g fill="none" stroke="#5E9DB6" stroke-width="7" stroke-linecap="round"><g>
        ${anim(run, '<animateTransform attributeName="transform" type="rotate" values="0 120 98;60 120 98" dur="6s" repeatCount="indefinite"/>')}
        <path d="M120 58v80 M85 78l70 40 M85 118l70-40"/>
        <path d="M110 64l10 8 10-8 M110 132l10-8 10 8 M88 90l12-1 -4-12 M152 106l-12 1 4 12 M88 106l12 1-4 12 M152 90l-12-1 4-12" stroke-width="5"/>
    </g></g>`;
    return `<g>${kind === "thaw" ? thaw : freeze}
        ${anim(run, `<g fill="#FFFFFF" stroke="#8FC0D2" stroke-width="1.5">${flake(64, 40, 5, 6, 110, "4s", "0s")}${flake(176, 34, 4, -6, 120, "5s", "1s")}${flake(150, 30, 3, 4, 120, "4.5s", "2s")}${flake(88, 28, 3.5, -4, 116, "5.5s", "0.5s")}</g>`)}
    </g>`;
}

function prep(run) {
    return `<g>
        <rect x="46" y="112" width="148" height="30" rx="12" fill="#C9A06A"/>
        <rect x="46" y="112" width="148" height="10" rx="5" fill="#D9B784"/>
        <g>${anim(run, '<animateTransform attributeName="transform" type="translate" values="0 0;0 -12;0 0" dur="0.8s" repeatCount="indefinite"/>')}
            <path d="M100 58l64 44-8 8-64-44z" fill="#DCE3DF"/><path d="M92 58l14 10-10 6-12-8z" fill="#14261F"/>
        </g>
        <circle cx="86" cy="106" r="7" fill="#F4A93B"/><circle cx="104" cy="108" r="6" fill="#F4C46B"/>
    </g>`;
}

export function cookScene(kind = "prep", running = true, size = 240) {
    if (!(kind in LABELS)) kind = "prep";
    const isCold = kind === "chill" || kind === "freeze" || kind === "thaw";
    const bg = isCold ? "#E3F0F4" : kind === "bake" || kind === "fry" ? "#FDEBD0" : "#DCEBE2";
    let body;
    if (kind === "boil" || kind === "simmer") body = pot(kind, running);
    else if (kind === "fry") body = fry(running);
    else if (kind === "bake") body = bake(running);
    else if (kind === "rise" || kind === "rest") body = rise(kind, running);
    else if (isCold) body = cold(kind, running);
    else body = prep(running);
    const template = document.createElement("template");
    template.innerHTML = `<svg class="cook-scene" role="img" aria-label="${LABELS[kind]}" width="${size}" height="${Math.round(size * 0.75)}" viewBox="0 0 240 180"><circle cx="120" cy="96" r="78" fill="${bg}"/>${body}</svg>`;
    return template.content.firstChild;
}

export const SCENE_VERBS = {
    boil: "Boiling", simmer: "Simmering", fry: "Cooking", bake: "Baking", rise: "Proofing", rest: "Resting",
    chill: "Cooling", freeze: "Freezing", thaw: "Thawing", prep: "Working",
};
