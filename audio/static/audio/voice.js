// A voice channel's talk screen (audio/templates/audio/voice.html, design: AudioVoice), on the channel's WebSocket
// (audio/voice.py). This device is also a speaker for the channel: other people talking (Opus packets, decoded with
// WebCodecs), order calls and chimes play here unless "This speaker" is muted; the volume is this device's.
// Hold the button (or the space bar) to talk: the microphone opens when the page loads (asking once) and, while
// held, is encoded to Opus (20 ms packets, 48 kHz mono) and sent straight away. One person talks at a time.
// "Replay last" and the Recent list's play buttons play order calls again on this device only. The microphone only
// works on https:// pages (or localhost).

const stage = document.querySelector("[data-voice]");
const $ = (sel) => document.querySelector(sel);
const channelId = stage.dataset.channel;
const store = { get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } } };

// ---------- this device's speaker ----------

const ctx = new AudioContext({ sampleRate: 48000 });
const out = ctx.createGain();
const analyser = ctx.createAnalyser();
analyser.fftSize = 64;
out.connect(analyser).connect(ctx.destination);
let muted = store.get(`audio-voice-muted-${channelId}`, "0") === "1";
let volume = Number(store.get("audio-voice-volume", "80"));

function applyVolume() {
    out.gain.setTargetAtTime(muted ? 0 : volume / 100, ctx.currentTime, 0.05);
    const button = $("[data-speaker]");
    button.setAttribute("aria-checked", String(!muted));
    $("[data-speaker-on]").hidden = muted;
    $("[data-speaker-off]").hidden = !muted;
    const range = $("[data-volume]");
    range.value = String(volume);
    range.style.setProperty("--pct", `${volume}%`);
    $("[data-volume-n]").textContent = `${volume}%`;
}
$("[data-speaker]").addEventListener("click", () => {
    muted = !muted;
    store.set(`audio-voice-muted-${channelId}`, muted ? "1" : "0");
    ctx.resume();
    applyVolume();
});
$("[data-volume]").addEventListener("input", (event) => {
    volume = Number(event.target.value);
    store.set("audio-voice-volume", String(volume));
    applyVolume();
});
document.addEventListener("pointerdown", () => ctx.state !== "running" && ctx.resume(), true);

// ---------- what's going on ----------

const TEXT = {
    idle: () => [gettext("Channel is clear"), gettext("Hold the button to talk.")],
    talking: () => [gettext("You’re live"), interpolate(gettext("Everyone on %s can hear you. Let go to stop."), [stage.dataset.name])],
    incoming: (s) => [interpolate(gettext("%s is talking"), [s.who]), gettext("You can talk once they let go.")],
    playing: (s) => [gettext("Order call playing"), s.text],
    replay: (s) => [interpolate(gettext("Replaying %s"), [s.text || gettext("the last call")]), gettext("Only on this device. Nobody else hears it.")],
    chime: () => [gettext("Chime playing"), gettext("A quick tone so people look up before you talk.")],
    offline: (s) => [gettext("Not connected"), s.text || gettext("Reconnecting…")],
};
const state = { mode: "offline", who: "", text: "", since: Date.now() };
let micReady = false;
let socket = null;

function set(mode, extra = {}) {
    if (state.mode !== mode) state.since = Date.now();
    Object.assign(state, { mode, ...extra });
    render();
}

function busy() {
    return ["incoming", "playing", "replay", "chime"].includes(state.mode);
}

function render() {
    const { mode } = state;
    const [head, sub] = TEXT[mode](state);
    const box = $("[data-status]");
    box.className = `au-status ${{ idle: "idle", talking: "talking", incoming: "incoming", offline: "off" }[mode] || "playing"}`;
    $("[data-status-head]").textContent = head;
    $("[data-status-sub]").textContent = sub;
    const ptt = $("[data-ptt]");
    const connected = socket?.readyState === WebSocket.OPEN;
    ptt.disabled = !connected || !micReady || (busy() && mode !== "talking");
    ptt.setAttribute("aria-pressed", String(mode === "talking"));
    ptt.closest(".au-ptt-wrap").classList.toggle("talking", mode === "talking");
    $("[data-ptt-icon]").hidden = busy();
    $("[data-ptt-busy]").hidden = !busy();
    $("[data-ptt-text]").textContent = mode === "talking" ? gettext("Release to stop") : mode === "incoming" ? gettext("Channel busy")
        : busy() ? gettext("Please wait") : gettext("Hold to talk");
    $("[data-ptt-sub]").textContent = mode === "talking" ? gettext("You’re live") : mode === "incoming" ? gettext("Someone else is talking")
        : busy() ? gettext("Something is playing") : !micReady ? micProblem || gettext("Opening the microphone…") : gettext("or hold the space bar");
    ptt.setAttribute("aria-label", mode === "talking" ? gettext("Talking. Release to stop.") : busy() ? gettext("Channel busy") : gettext("Hold to talk"));
    const recent = document.getElementById("au-recent");
    $("[data-replay-last]").disabled = busy() || mode === "talking" || !recent?.dataset.lastCall;
    $("[data-chime]").disabled = busy() || mode === "talking";
    document.querySelectorAll("[data-replay]").forEach((b) => { b.disabled = busy() || mode === "talking"; });
}

// timer and waveform
const bars = [...document.querySelectorAll("[data-wave] span")];
const levels = new Uint8Array(analyser.frequencyBinCount);
let micAnalyser = null;
const COLOURS = { talking: "#E2442F", incoming: "#F4A93B", playing: "#4E79B5", replay: "#4E79B5", chime: "#4E79B5" };
function frame() {
    const active = !["idle", "offline"].includes(state.mode);
    const secs = (Date.now() - state.since) / 1000;
    $("[data-timer]").textContent = active ? `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, "0")}` : "";
    const source = state.mode === "talking" ? micAnalyser : analyser;
    source?.getByteFrequencyData(levels);
    bars.forEach((bar, i) => {
        const level = active && source ? levels[(i * 3) % levels.length] / 255 : 0;
        bar.style.height = `${active ? Math.max(10, 10 + level * 54) : 8}px`;
        bar.style.background = active ? COLOURS[state.mode] : "#D9E1DC";
    });
    requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ---------- listening ----------

const START_LEAD = 0.3, MAX_LEAD = 0.8;  // seconds of slack against network jitter
let decoder = null, playAt = 0, lead = START_LEAD, stamp = 0, release = null;
function playPcm(audioData) {
    const frames = audioData.numberOfFrames;
    const buffer = ctx.createBuffer(1, frames, audioData.sampleRate);
    const samples = new Float32Array(frames);
    audioData.copyTo(samples, { planeIndex: 0, format: "f32-planar" });
    buffer.copyToChannel(samples, 0);
    audioData.close();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(out);
    // A jitter buffer: packets arrive in bursts (the talker's browser, Wi-Fi power saving), so play them a little
    // behind. When it runs dry anyway, wait a bit longer before starting again, and keep that extra slack.
    if (playAt < ctx.currentTime + 0.02) {
        if (playAt) lead = Math.min(lead + 0.1, MAX_LEAD);
        playAt = ctx.currentTime + lead;
    }
    source.start(playAt);
    playAt += buffer.duration;
}
function startDecoder() {
    if (!("AudioDecoder" in window)) return null;
    const d = new AudioDecoder({ output: playPcm, error: () => { decoder = null; } });
    d.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1 });
    return d;
}

let clipAudio = null;
function playClip(url, mode, text) {
    clipAudio?.pause();
    const audio = new Audio(url);
    audio.crossOrigin = "anonymous";
    ctx.createMediaElementSource(audio).connect(out);
    clipAudio = audio;
    set(mode, { text });
    const done = () => { if (clipAudio === audio) { clipAudio = null; if (state.mode === mode) set("idle"); } };
    audio.addEventListener("ended", done);
    audio.addEventListener("error", done);
    ctx.resume();
    audio.play().catch(done);
}

function connect() {
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${stage.dataset.ws}`);
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => set(stage.dataset.busyWho ? "incoming" : "idle", { who: stage.dataset.busyWho }));
    socket.addEventListener("message", (event) => {
        if (typeof event.data !== "string") {
            decoder = decoder || startDecoder();
            if (!decoder || decoder.state !== "configured") return;
            stamp += 20000;
            decoder.decode(new EncodedAudioChunk({ type: "key", timestamp: stamp, data: event.data }));
            return;
        }
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type === "start") {
            clearTimeout(release);
            playAt = 0;  // a new talker: start with fresh slack
            lead = START_LEAD;
            decoder = decoder || startDecoder();
            set("incoming", { who: message.first || message.who || gettext("Someone") });
        } else if (message.type === "stop") {
            release = setTimeout(() => state.mode === "incoming" && set("idle"), 700);  // let the last words ring out
        } else if (message.type === "clip" && typeof message.url === "string") {
            if (state.mode !== "talking") playClip(message.url, message.chime ? "chime" : "playing", message.text || "");
        } else if (message.type === "talking") {
            set("talking");
        } else if (message.type === "busy") {
            stopTalking(false);
        } else if (message.type === "denied") {
            stopTalking(false);
            set("offline", { text: gettext("Sign in as staff to talk.") });
        }
    });
    socket.addEventListener("close", () => {
        stopTalking(false);
        set("offline");
        setTimeout(connect, 2000);
    });
}

// ---------- talking ----------

let encoder = null, wantTalk = false, micProblem = "";
async function openMic() {
    if (!("AudioEncoder" in window) || !("MediaStreamTrackProcessor" in window)) {
        micProblem = gettext("This browser can’t send voice. Use Chrome.");
        render();
        return;
    }
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
        micProblem = gettext("The microphone isn’t allowed (it needs https).");
        render();
        return;
    }
    const micCtx = new AudioContext({ sampleRate: 48000 });
    const dest = micCtx.createMediaStreamDestination();
    dest.channelCount = 1;
    const input = micCtx.createMediaStreamSource(stream);
    input.connect(dest);
    micAnalyser = micCtx.createAnalyser();
    micAnalyser.fftSize = 64;
    input.connect(micAnalyser);
    document.addEventListener("pointerdown", () => micCtx.resume(), { once: true });
    const reader = new MediaStreamTrackProcessor({ track: dest.stream.getAudioTracks()[0] }).readable.getReader();
    micReady = true;
    render();
    for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        if (state.mode === "talking" && encoder?.state === "configured") encoder.encode(value);
        value.close();
    }
}

function startTalking() {
    if (wantTalk || busy() || !micReady || socket?.readyState !== WebSocket.OPEN) return;
    wantTalk = true;
    clipAudio?.pause();
    encoder = new AudioEncoder({
        output: (chunk) => {
            const data = new Uint8Array(chunk.byteLength);
            chunk.copyTo(data);
            if (socket.readyState === WebSocket.OPEN) socket.send(data);
        },
        error: () => stopTalking(),
    });
    encoder.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1, bitrate: 32000, opus: { frameDuration: 20000 } });
    socket.send(JSON.stringify({ type: "start" }));  // "talking" comes back when we have the channel, "busy" if not
}

async function stopTalking(say = true) {
    if (!wantTalk) return;
    wantTalk = false;
    const done = encoder;
    encoder = null;
    try { await done?.flush(); } catch { /* closed */ }
    done?.close();
    if (say && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
    if (state.mode === "talking") set("idle");
}

const ptt = $("[data-ptt]");
ptt.addEventListener("pointerdown", (event) => {
    if (event.button > 0) return;
    ptt.setPointerCapture?.(event.pointerId);
    startTalking();
});
ptt.addEventListener("pointerup", () => stopTalking());
ptt.addEventListener("pointercancel", () => stopTalking());
ptt.addEventListener("lostpointercapture", () => stopTalking());
ptt.addEventListener("contextmenu", (event) => event.preventDefault());
const typing = () => document.activeElement?.matches("input:not([type=range]), textarea, select");
document.addEventListener("keydown", (event) => {
    if ((event.key === " " || (event.key === "Enter" && document.activeElement === ptt)) && !event.repeat && !typing()) {
        event.preventDefault();
        startTalking();
    }
});
document.addEventListener("keyup", (event) => {
    if ((event.key === " " || event.key === "Enter") && wantTalk) {
        event.preventDefault();
        stopTalking();
    }
});
window.addEventListener("blur", () => stopTalking());

// ---------- replays (this device only) ----------

document.addEventListener("click", (event) => {
    const one = event.target.closest("[data-replay]");
    if (one) playClip(one.dataset.replay, "replay", one.dataset.replayText);
    if (event.target.closest("[data-replay-last]")) {
        const recent = document.getElementById("au-recent");
        if (recent?.dataset.lastCall) playClip(recent.dataset.lastCall, "replay", recent.dataset.lastText);
    }
});
// The Recent list is swapped in by live-page.js: keep its buttons in step
new MutationObserver(render).observe(document.querySelector(".au-recent"), { childList: true });

applyVolume();
render();
connect();
openMic();
