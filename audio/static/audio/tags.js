// What a song's file says about itself, read in the browser before it's uploaded (the admin's Add music dialog), so
// the server never has to open a song: title, artist, album, year, genre and the cover picture, from
// ID3v2 / ID3v1 (MP3, AAC), MP4 atoms (M4A), FLAC and Ogg (Vorbis, Opus) comments, and RIFF INFO (WAV).
// Only the parts of the file that hold tags are read (Blob.slice), never the whole song.
//
//   readTags(file) -> {title, artist, album, year, genre, picture: Blob | null}   (missing ones are "" / null)
//   probe(file)    -> length in seconds, or null when the browser can't play it

const MAX_TAG = 32 * 1024 * 1024;  // never read more than this for one block of tags (big cover pictures)

const GENRES = ["Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal", "New Age",
    "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska", "Death Metal",
    "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise", "Alternative Rock", "Bass", "Soul", "Punk",
    "Space", "Meditative", "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial",
    "Electronic", "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi",
    "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock", "Folk", "Folk-Rock",
    "National Folk", "Swing", "Fast Fusion", "Bebop", "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock",
    "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock", "Big Band", "Chorus", "Easy Listening", "Acoustic",
    "Humour", "Speech", "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass", "Primus", "Porn Groove",
    "Satire", "Slow Jam", "Club", "Tango", "Samba", "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle", "Duet",
    "Punk Rock", "Drum Solo", "A Cappella", "Euro-House", "Dance Hall", "Goa", "Drum & Bass", "Club-House", "Hardcore Techno",
    "Terror", "Indie", "BritPop", "Negerpunk", "Polsk Punk", "Beat", "Christian Gangsta Rap", "Heavy Metal", "Black Metal",
    "Crossover", "Contemporary Christian", "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "Jpop", "Synthpop"];

const bytes = async (file, start, end) => new Uint8Array(await file.slice(start, Math.min(end, file.size)).arrayBuffer());
const ascii = (b, at, n) => String.fromCharCode(...b.subarray(at, at + n));
const be32 = (b, at) => ((b[at] << 24) >>> 0) + (b[at + 1] << 16) + (b[at + 2] << 8) + b[at + 3];
const le32 = (b, at) => b[at] + (b[at + 1] << 8) + (b[at + 2] << 16) + ((b[at + 3] << 24) >>> 0);
const syncsafe = (b, at) => (b[at] << 21) | (b[at + 1] << 14) | (b[at + 2] << 7) | b[at + 3];
const clean = (s) => String(s || "").replace(/\0.*$/s, "").trim();
const utf8 = (b) => new TextDecoder("utf-8").decode(b);

function genreName(value) {
    const text = clean(value);
    const m = /^\(?(\d{1,3})\)?/.exec(text);  // ID3 "(17)", "17" or "(17)Rock"
    if (m && (m[0] === text || text.startsWith("("))) return GENRES[Number(m[1])] ?? text.replace(/^\(\d+\)/, "").trim();
    return text;
}

function picture(data, mime) {
    if (!data?.length) return null;
    const type = mime && mime.includes("/") ? mime.toLowerCase() : (data[0] === 0x89 ? "image/png" : "image/jpeg");
    return new Blob([data], { type: type === "image/jpg" ? "image/jpeg" : type });
}

function fill(out, values) {
    for (const key of ["title", "artist", "album", "genre"]) if (!out[key] && values[key]) out[key] = clean(values[key]);
    if (!out.artist && values.albumartist) out.artist = clean(values.albumartist);
    if (!out.year && values.year) out.year = (/\d{4}/.exec(String(values.year)) || [""])[0];
    if (!out.picture && values.picture) out.picture = values.picture;
}

// ---------- ID3 ----------

function decodeText(b, encoding) {
    if (encoding === 0) return new TextDecoder("windows-1252").decode(b);
    if (encoding === 3) return utf8(b);
    if (encoding === 1 && b[0] === 0xFE && b[1] === 0xFF) return new TextDecoder("utf-16be").decode(b.subarray(2));
    if (encoding === 1 && b[0] === 0xFF && b[1] === 0xFE) return new TextDecoder("utf-16le").decode(b.subarray(2));
    return new TextDecoder(encoding === 2 ? "utf-16be" : "utf-16le").decode(b);
}

// Where a string in `encoding` ends (its terminator), from `at`
function textEnd(b, at, encoding) {
    if (encoding === 0 || encoding === 3) {
        const i = b.indexOf(0, at);
        return i < 0 ? [b.length, b.length] : [i, i + 1];
    }
    for (let i = at; i + 1 < b.length; i += 2) if (b[i] === 0 && b[i + 1] === 0) return [i, i + 2];
    return [b.length, b.length];
}

function unsync(b) {
    const out = [];
    for (let i = 0; i < b.length; i++) {
        out.push(b[i]);
        if (b[i] === 0xFF && b[i + 1] === 0x00) i++;
    }
    return Uint8Array.from(out);
}

function id3Frames(tag) {
    const major = tag[3], flags = tag[5];
    let body = tag.subarray(10);
    if (major < 4 && flags & 0x80) body = unsync(body);
    let at = 0;
    if (flags & 0x40) at = major === 4 ? syncsafe(body, 0) : be32(body, 0) + 4;  // extended header
    const values = {};
    const pictures = [];
    const idLen = major === 2 ? 3 : 4, headLen = major === 2 ? 6 : 10;
    while (at + headLen <= body.length) {
        const id = ascii(body, at, idLen);
        if (!/^[A-Z0-9]+$/.test(id)) break;  // padding
        const size = major === 2 ? (body[at + 3] << 16) + (body[at + 4] << 8) + body[at + 5] : major === 4 ? syncsafe(body, at + 4) : be32(body, at + 4);
        const frameFlags = major === 4 ? body[at + 9] : 0;
        let data = body.subarray(at + headLen, at + headLen + size);
        at += headLen + size;
        if (frameFlags & 0x0C) continue;  // compressed or encrypted
        if (frameFlags & 0x01) data = data.subarray(4);  // data length indicator
        if (frameFlags & 0x02) data = unsync(data);
        if (!data.length) continue;
        const key = { TIT2: "title", TT2: "title", TPE1: "artist", TP1: "artist", TPE2: "albumartist", TP2: "albumartist",
            TALB: "album", TAL: "album", TYER: "year", TYE: "year", TDRC: "year", TDOR: "year", TCON: "genre", TCO: "genre" }[id];
        if (key) {
            const text = decodeText(data.subarray(1), data[0]).split("\0").find((s) => s.trim()) || "";
            if (!values[key]) values[key] = key === "genre" ? genreName(text) : text;
        } else if (id === "APIC" || id === "PIC") {
            const enc = data[0];
            let mime, i;
            if (id === "PIC") {
                mime = { JPG: "image/jpeg", PNG: "image/png" }[ascii(data, 1, 3).toUpperCase()] || "";
                i = 4;
            } else {
                const [end, next] = textEnd(data, 1, 0);
                mime = ascii(data, 1, end - 1);
                i = next;
            }
            const kind = data[i];
            const [, after] = textEnd(data, i + 1, enc);
            pictures.push({ front: kind === 3, blob: picture(data.subarray(after), mime) });
        }
    }
    values.picture = (pictures.find((p) => p.front) || pictures[0])?.blob || null;
    return values;
}

async function id3v2(file, out, start = 0) {
    const head = await bytes(file, start, start + 10);
    if (ascii(head, 0, 3) !== "ID3" || head[3] < 2 || head[3] > 4) return;
    const size = syncsafe(head, 6);
    fill(out, id3Frames(await bytes(file, start, start + 10 + Math.min(size, MAX_TAG))));
}

async function id3v1(file, out) {
    if (file.size < 128) return;
    const b = await bytes(file, file.size - 128, file.size);
    if (ascii(b, 0, 3) !== "TAG") return;
    const text = (at, n) => clean(new TextDecoder("windows-1252").decode(b.subarray(at, at + n)));
    fill(out, { title: text(3, 30), artist: text(33, 30), album: text(63, 30), year: text(93, 4), genre: GENRES[b[127]] || "" });
}

// ---------- MP4 / M4A ----------

function* atoms(b, start, end) {
    let at = start;
    while (at + 8 <= end) {
        let size = be32(b, at), head = 8;
        const type = ascii(b, at + 4, 4);
        if (size === 1) { size = be32(b, at + 8) * 2 ** 32 + be32(b, at + 12); head = 16; }
        if (size === 0) size = end - at;
        if (size < head) return;
        yield { type, start: at + head, end: Math.min(at + size, end) };
        at += size;
    }
}

function ilst(b, list) {
    const values = {};
    const names = { "\xA9nam": "title", "\xA9ART": "artist", aART: "albumartist", "\xA9alb": "album", "\xA9day": "year", "\xA9gen": "genre" };
    for (const item of atoms(b, list.start, list.end)) {
        for (const data of atoms(b, item.start, item.end)) {
            if (data.type !== "data") continue;
            const kind = be32(b, data.start) & 0xFFFFFF, payload = b.subarray(data.start + 8, data.end);
            if (names[item.type] && !values[names[item.type]]) values[names[item.type]] = utf8(payload);
            else if (item.type === "gnre" && payload.length >= 2 && !values.genre) values.genre = GENRES[((payload[0] << 8) | payload[1]) - 1] || "";
            else if (item.type === "covr" && !values.picture) values.picture = picture(payload, kind === 14 ? "image/png" : kind === 13 ? "image/jpeg" : "");
        }
    }
    return values;
}

function findIlst(b, start, end) {
    for (const atom of atoms(b, start, end)) {
        if (atom.type === "ilst") return atom;
        if (atom.type === "udta" || atom.type === "trak") {
            const found = findIlst(b, atom.start, atom.end);
            if (found) return found;
        }
        if (atom.type === "meta") {
            // "meta" is a full atom (4 bytes of version and flags) in MP4, a plain one in QuickTime files
            const plain = ascii(b, atom.start + 4, 4) === "hdlr";
            const found = findIlst(b, atom.start + (plain ? 0 : 4), atom.end);
            if (found) return found;
        }
    }
    return null;
}

async function mp4(file, out) {
    let at = 0;
    while (at + 8 <= file.size) {
        const head = await bytes(file, at, at + 16);
        let size = be32(head, 0), skip = 8;
        if (size === 1) { size = be32(head, 8) * 2 ** 32 + be32(head, 12); skip = 16; }
        if (size === 0) size = file.size - at;
        if (size < skip) return;
        if (ascii(head, 4, 4) === "moov") {
            const moov = await bytes(file, at + skip, at + Math.min(size, MAX_TAG));
            const list = findIlst(moov, 0, moov.length);
            if (list) fill(out, ilst(moov, list));
            return;
        }
        at += size;
    }
}

// ---------- FLAC and Ogg (Vorbis comments) ----------

function vorbisComments(b, at) {
    const values = {};
    const keys = { TITLE: "title", ARTIST: "artist", ALBUMARTIST: "albumartist", "ALBUM ARTIST": "albumartist", ALBUM: "album", DATE: "year", YEAR: "year", GENRE: "genre" };
    at += 4 + le32(b, at);  // vendor
    const count = le32(b, at);
    at += 4;
    for (let n = 0; n < count && at + 4 <= b.length; n++) {
        const len = le32(b, at);
        const text = utf8(b.subarray(at + 4, at + 4 + len));
        at += 4 + len;
        const eq = text.indexOf("=");
        const key = text.slice(0, eq).toUpperCase(), value = text.slice(eq + 1);
        if (keys[key] && !values[keys[key]]) values[keys[key]] = value;
        else if (key === "METADATA_BLOCK_PICTURE" && !values.picture) {
            try {
                values.picture = flacPicture(Uint8Array.from(atob(value), (c) => c.charCodeAt(0)));
            } catch { /* not a picture */ }
        }
    }
    return values;
}

function flacPicture(b) {
    let at = 4;
    const mimeLen = be32(b, at);
    const mime = ascii(b, at + 4, mimeLen);
    at += 4 + mimeLen;
    at += 4 + be32(b, at);  // description
    at += 16;  // width, height, depth, colours
    const len = be32(b, at);
    return picture(b.subarray(at + 4, at + 4 + len), mime);
}

async function flac(file, out) {
    let at = 4, front = false;
    for (let n = 0; n < 64 && at + 4 <= file.size; n++) {
        const head = await bytes(file, at, at + 4);
        const last = head[0] & 0x80, type = head[0] & 0x7F, len = (head[1] << 16) + (head[2] << 8) + head[3];
        if (type === 4 || (type === 6 && !front)) {
            const block = await bytes(file, at + 4, at + 4 + Math.min(len, MAX_TAG));
            if (type === 4) fill(out, vorbisComments(block, 0));
            else {
                front = be32(block, 0) === 3;  // the front cover wins over other pictures
                out.picture = flacPicture(block);
            }
        }
        if (last) return;
        at += 4 + len;
    }
}

async function ogg(file, out) {
    const b = await bytes(file, 0, Math.min(file.size, MAX_TAG));
    const packets = [];
    let at = 0, current = [], serial = null;
    while (at + 27 <= b.length && packets.length < 2 && ascii(b, at, 4) === "OggS") {
        const pageSerial = le32(b, at + 14), segments = b[at + 26];
        let data = at + 27 + segments;
        for (let s = 0; s < segments; s++) {
            const len = b[at + 27 + s];
            if (serial === null) serial = pageSerial;
            if (pageSerial === serial) current.push(b.subarray(data, data + len));
            data += len;
            if (len < 255 && pageSerial === serial) {
                const size = current.reduce((n, part) => n + part.length, 0);
                const packet = new Uint8Array(size);
                let p = 0;
                for (const part of current) { packet.set(part, p); p += part.length; }
                packets.push(packet);
                current = [];
            }
        }
        at = data;
    }
    const comments = packets[1];
    if (!comments) return;
    if (ascii(comments, 0, 7) === "\x03vorbis") fill(out, vorbisComments(comments, 7));
    else if (ascii(comments, 0, 8) === "OpusTags") fill(out, vorbisComments(comments, 8));
}

// ---------- WAV ----------

async function riff(file, out) {
    let at = 12;
    for (let n = 0; n < 200 && at + 8 <= file.size; n++) {
        const head = await bytes(file, at, at + 12);
        const id = ascii(head, 0, 4), len = le32(head, 4);
        if (id === "LIST" && ascii(head, 8, 4) === "INFO") {
            const b = await bytes(file, at + 12, at + 8 + Math.min(len, MAX_TAG));
            const keys = { INAM: "title", IART: "artist", IPRD: "album", ICRD: "year", IGNR: "genre" };
            const values = {};
            for (let i = 0; i + 8 <= b.length;) {
                const sub = ascii(b, i, 4), size = le32(b, i + 4);
                if (keys[sub]) values[keys[sub]] = new TextDecoder("windows-1252").decode(b.subarray(i + 8, i + 8 + size));
                i += 8 + size + (size & 1);
            }
            fill(out, values);
        } else if (id.toLowerCase() === "id3 ") {
            await id3v2(file, out, at + 8);
        }
        at += 8 + len + (len & 1);
    }
}

// ---------- ----------

export async function readTags(file) {
    const out = { title: "", artist: "", album: "", year: "", genre: "", picture: null };
    try {
        const head = await bytes(file, 0, 12);
        if (ascii(head, 0, 3) === "ID3") await id3v2(file, out);
        else if (ascii(head, 0, 4) === "fLaC") await flac(file, out);
        else if (ascii(head, 0, 4) === "OggS") await ogg(file, out);
        else if (ascii(head, 0, 4) === "RIFF" && ascii(head, 8, 4) === "WAVE") await riff(file, out);
        else if (ascii(head, 4, 4) === "ftyp") await mp4(file, out);
        if (!out.title || !out.artist) await id3v1(file, out);
    } catch {
        // tags we can't make sense of: leave what we have, the admin fills in the rest
    }
    return out;
}

export function probe(file) {
    return new Promise((resolve) => {
        const audio = new Audio(), url = URL.createObjectURL(file);
        const done = (value) => {
            clearTimeout(timer);
            audio.removeAttribute("src");
            URL.revokeObjectURL(url);
            resolve(value);
        };
        const timer = setTimeout(() => done(null), 20000);
        audio.preload = "metadata";
        audio.onloadedmetadata = () => done(Number.isFinite(audio.duration) ? audio.duration : 0);
        audio.onerror = () => done(null);
        audio.src = url;
    });
}
