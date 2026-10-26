// A screen's audio channels (Screen.audio, highest priority first; config from screens/views.py: show).
// - Music channels are radio stations: the server says which song, how far in and how loud (audio/music.py); the
//   song plays straight from the media storage, seeked to that moment, and follows the channel on every "audio" nudge.
// - Voice channels listen on a WebSocket (audio/voice.py): live push-to-talk audio (Opus, decoded with WebCodecs)
//   and order-call clips.
// - Ducking: while a voice channel is speaking, every channel below it drops to its own "play at" level.
// Browsers only play sound after someone taps the page once (or Chrome runs with
// --autoplay-policy=no-user-gesture-required), so until then a "Tap to turn on sound" button shows.
import { onLiveUpdate } from "/static/js/live.js";

const config = JSON.parse(document.getElementById("screen-audio")?.textContent || "[]");
const ctx = new AudioContext();
const lanes = config.map((c, rank) => {
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    return { ...c, rank, gain, speaking: false, release: null };
});

// ---------- sound on ----------

const prompt = document.getElementById("sound-on");
function showPrompt() {
    if (prompt) prompt.hidden = ctx.state === "running";
}
prompt?.addEventListener("click", () => ctx.resume().then(showPrompt));
document.addEventListener("pointerdown", () => ctx.state !== "running" && ctx.resume().then(showPrompt));
ctx.addEventListener("statechange", showPrompt);
ctx.resume().catch(() => {}).finally(showPrompt);

// ---------- ducking ----------

function applyDucking() {
    for (const lane of lanes) {
        const above = lanes.some((other) => other.rank < lane.rank && other.kind === "voice" && other.speaking);
        lane.gain.gain.setTargetAtTime(above ? lane.keep / 100 : 1, ctx.currentTime, 0.12);
    }
}

function speaking(lane, on) {
    clearTimeout(lane.release);
    if (on) {
        lane.speaking = true;
        applyDucking();
    } else {
        lane.release = setTimeout(() => { lane.speaking = false; applyDucking(); }, 700);  // let the last words ring out
    }
}

// ---------- music ----------

function music(lane) {
    const audio = new Audio();
    audio.crossOrigin = "anonymous";  // songs on S3 need CORS for the volume control to reach them
    audio.preload = "auto";
    lane.audio = audio;
    const volume = ctx.createGain();  // the channel's volume (set in the Audio app), before ducking
    ctx.createMediaElementSource(audio).connect(volume).connect(lane.gain);
    let trackId = null;
    let timer = null;
    let clockOffset = 0;  // server clock minus ours

    async function sync() {
        clearTimeout(timer);
        let data;
        try {
            data = await (await fetch(lane.now, { credentials: "same-origin", cache: "no-store" })).json();
        } catch {
            timer = setTimeout(sync, 5000);
            return;
        }
        clockOffset = data.server_ms - Date.now();
        volume.gain.setTargetAtTime((data.volume ?? 100) / 100, ctx.currentTime, 0.1);
        if (!data.track) {
            audio.pause();
            trackId = null;
            timer = setTimeout(sync, 30000);  // nothing queued: look again (a nudge comes sooner)
            return;
        }
        if (trackId !== data.track.id) {
            trackId = data.track.id;
            audio.src = data.track.url;
            await new Promise((done) => audio.addEventListener("loadedmetadata", done, { once: true }));
        }
        const target = data.offset + (data.paused ? 0 : (Date.now() + clockOffset - data.server_ms) / 1000);
        if (Math.abs(audio.currentTime - target) > 1.5) audio.currentTime = target;
        if (data.paused) {
            audio.pause();
            return;
        }
        audio.play().catch(() => showPrompt());
        if (data.ends_ms) timer = setTimeout(sync, Math.max(data.ends_ms - clockOffset - Date.now(), 0) + 250);
    }

    audio.addEventListener("ended", sync);
    audio.addEventListener("error", () => { trackId = null; clearTimeout(timer); timer = setTimeout(sync, 5000); });
    return sync;
}

// ---------- voice ----------

function voice(lane) {
    let decoder = null;
    const START_LEAD = 0.3, MAX_LEAD = 0.8;  // seconds of slack against network jitter
    let playAt = 0, lead = START_LEAD;
    let stamp = 0;

    function play(audioData) {
        const frames = audioData.numberOfFrames;
        const buffer = ctx.createBuffer(1, frames, audioData.sampleRate);
        const samples = new Float32Array(frames);
        audioData.copyTo(samples, { planeIndex: 0, format: "f32-planar" });
        buffer.copyToChannel(samples, 0);
        audioData.close();
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(lane.gain);
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
        const d = new AudioDecoder({ output: play, error: () => { decoder = null; } });
        d.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1 });
        return d;
    }

    function clip(url) {
        const audio = new Audio(url);
        audio.crossOrigin = "anonymous";
        ctx.createMediaElementSource(audio).connect(lane.gain);
        speaking(lane, true);
        audio.addEventListener("ended", () => speaking(lane, false));
        audio.addEventListener("error", () => speaking(lane, false));
        audio.play().catch(() => { speaking(lane, false); showPrompt(); });
    }

    function connect() {
        const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${lane.ws}`);
        socket.binaryType = "arraybuffer";
        lane.heard = lane.heard || { messages: 0, packets: 0 };  // for looking into it from the console
        socket.addEventListener("message", (event) => {
            if (typeof event.data === "string") lane.heard.messages += 1;
            else lane.heard.packets += 1;
            if (typeof event.data === "string") {
                let message;
                try { message = JSON.parse(event.data); } catch { return; }
                if (message.type === "start") {
                    playAt = 0;  // a new talker: start with fresh slack
                    lead = START_LEAD;
                    decoder = decoder || startDecoder();
                    speaking(lane, true);
                } else if (message.type === "stop") {
                    speaking(lane, false);
                } else if (message.type === "clip" && typeof message.url === "string") {
                    clip(message.url);
                }
                return;
            }
            decoder = decoder || startDecoder();
            if (!decoder || decoder.state !== "configured") return;
            stamp += 20000;
            decoder.decode(new EncodedAudioChunk({ type: "key", timestamp: stamp, data: event.data }));
        });
        socket.addEventListener("close", () => { speaking(lane, false); setTimeout(connect, 2000); });
    }
    connect();
}

// ---------- start ----------

const syncs = [];
for (const lane of lanes) {
    if (lane.kind === "music" && lane.now) syncs.push(music(lane));
    if (lane.kind === "voice" && lane.ws) voice(lane);
}
applyDucking();
window.screenAudio = { ctx, lanes };  // for looking into a screen's sound from the console
syncs.forEach((sync) => sync());
if (syncs.length) onLiveUpdate("audio", () => syncs.forEach((sync) => sync()), { refreshOnConnect: false });
