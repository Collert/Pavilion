// Push-to-talk on a voice channel (audio/voice.py). The microphone opens once, when the page loads (asking for
// permission), and keeps running: holding the button starts encoding it to Opus (20 ms packets, 48 kHz mono, with
// WebCodecs) and sending it over the channel's WebSocket straight away; letting go says "stop".
// The microphone only works on https:// pages (or localhost).
const root = document.querySelector("[data-talk]");
const button = root.querySelector("[data-talk-button]");
const status = root.querySelector("[data-talk-status]");
const t = (text) => (window.gettext ? gettext(text) : text);

let socket = null;
let encoder = null;
let talking = false;
let micReady = false;

function say(text) { status.textContent = text; }
function ready() {
    button.disabled = !(micReady && socket?.readyState === WebSocket.OPEN);
    if (!button.disabled && !talking) say(t("Hold to talk"));
}

function connect() {
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${root.dataset.ws}`);
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", ready);
    socket.addEventListener("message", (event) => {
        if (typeof event.data !== "string") return;  // others talking: this page doesn't play it
        const message = JSON.parse(event.data);
        if (message.type === "busy") { stop(); say(t("Someone else is talking")); }
        if (message.type === "denied") { stop(); say(t("Sign in as staff to talk")); }
        if (message.type === "talking") say(t("You’re on"));
        if (message.type === "start") say(`${message.who} ${t("is talking")}`);
        if (message.type === "stop" && !talking) say(t("Hold to talk"));
    });
    socket.addEventListener("close", () => { button.disabled = true; say(t("Reconnecting…")); setTimeout(connect, 2000); });
}

// The microphone, resampled to 48 kHz mono, read frame by frame for as long as the page is open
async function openMic() {
    if (!("AudioEncoder" in window) || !("MediaStreamTrackProcessor" in window)) {
        say(t("This browser can’t send voice. Use Chrome."));
        return;
    }
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
        say(t("The microphone isn’t allowed (it needs https)."));
        return;
    }
    const ctx = new AudioContext({ sampleRate: 48000 });
    const dest = ctx.createMediaStreamDestination();
    dest.channelCount = 1;
    ctx.createMediaStreamSource(stream).connect(dest);
    document.addEventListener("pointerdown", () => ctx.resume(), { once: true });
    const reader = new MediaStreamTrackProcessor({ track: dest.stream.getAudioTracks()[0] }).readable.getReader();
    micReady = true;
    ready();
    for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        if (talking && encoder?.state === "configured") encoder.encode(value);
        value.close();
    }
}

function start() {
    if (talking || button.disabled) return;
    talking = true;
    encoder = new AudioEncoder({
        output: (chunk) => {
            const data = new Uint8Array(chunk.byteLength);
            chunk.copyTo(data);
            if (socket.readyState === WebSocket.OPEN) socket.send(data);
        },
        error: () => stop(),
    });
    encoder.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1, bitrate: 32000, opus: { frameDuration: 20000 } });
    socket.send(JSON.stringify({ type: "start" }));
    button.setAttribute("aria-pressed", "true");
}

async function stop() {
    if (!talking) return;
    talking = false;
    button.setAttribute("aria-pressed", "false");
    const done = encoder;
    encoder = null;
    try { await done?.flush(); } catch { /* closed */ }
    done?.close();
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
    say(t("Hold to talk"));
}

button.addEventListener("pointerdown", (event) => { button.setPointerCapture?.(event.pointerId); start(); });
button.addEventListener("pointerup", stop);
button.addEventListener("pointercancel", stop);
button.addEventListener("lostpointercapture", stop);
button.addEventListener("keydown", (event) => { if (event.key === " " && !event.repeat) { event.preventDefault(); start(); } });
button.addEventListener("keyup", (event) => { if (event.key === " ") stop(); });
say(t("Connecting…"));
connect();
openMic();
