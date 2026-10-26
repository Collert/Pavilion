"""
Audio channels (design: AdminAudio): the store's music and voice channels (audio.AudioChannel), the list filtered by
type and the selected one: name, type, notes, the screens that use it (in what role, at what priority) and archiving.
One form with Save; new channels, type changes and archiving are part of the draft until it's saved.

Saving a type change or an archive takes the channel off the screens that had it (their lower channels move up).

    draft = {"rows": [{"id": 3 or "", "name", "kind", "notes"}], "sel": index, "filter": "all"|"music"|"voice", "gone": [ids]}
"""
from django.db import transaction
from django.utils.translation import gettext as _, ngettext

from audio.models import AudioChannel

TYPES = {
    AudioChannel.MUSIC: {"label": _("Music"), "color": "#7A5AC8",
                         "icon": "M9 18V5l11-2v13 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
                         "desc": _("Songs from your Library. Plays in the background on screens."),
                         "plays": _("Plays its queue in step on every screen. Staff pick what plays in the Audio app."), "where": _("Music")},
    AudioChannel.VOICE: {"label": _("Voice"), "color": "#2BA3A0",
                         "icon": "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z M5 11a7 7 0 0 0 14 0 M12 18v3 M8 21h8",
                         "desc": _("Spoken calls and announcements, like “Order for Maya is ready.”"),
                         "plays": _("Order calls, announcements and push-to-talk."), "where": _("Voice")},
}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def draft_of() -> dict:
    rows = [{"id": c.id, "name": c.name, "kind": c.kind, "notes": c.notes} for c in AudioChannel.objects.filter(archived=False)]
    return {"rows": rows, "sel": 0, "filter": "all", "gone": []}


def from_post(post) -> dict:
    rows = []
    for k in range(min(_int(post.get("count")), 200)):
        kind = post.get(f"ch-{k}-kind")
        rows.append({"id": _int(post.get(f"ch-{k}-id")) or "", "name": post.get(f"ch-{k}-name", "").strip()[:60],
                     "kind": kind if kind in TYPES else AudioChannel.MUSIC, "notes": post.get(f"ch-{k}-notes", "").strip()[:140]})
    sel = _int(post.get("sel"))
    flt = post.get("filter", "all")
    return {"rows": rows, "sel": min(max(sel, 0), max(len(rows) - 1, 0)), "filter": flt if flt in ("all", "music", "voice") else "all",
            "gone": [_int(x) for x in post.getlist("gone") if _int(x)]}


def apply_op(draft, op, post) -> str:
    rows = draft["rows"]
    name, _sep, arg = op.partition(":")
    if name == "pick" and 0 <= _int(arg, -1) < len(rows):
        draft["sel"] = _int(arg)
    elif name == "filter" and arg in ("all", "music", "voice"):
        draft["filter"] = arg
        shown = [i for i, r in enumerate(rows) if arg == "all" or r["kind"] == arg]
        if shown and draft["sel"] not in shown:
            draft["sel"] = shown[0]
    elif name == "kind" and arg in TYPES and rows:
        rows[draft["sel"]]["kind"] = arg
    elif name == "create":
        new_name = post.get("new_name", "").strip()[:60]
        kind = post.get("new_kind") if post.get("new_kind") in TYPES else AudioChannel.MUSIC
        if new_name:
            rows.append({"id": "", "name": new_name, "kind": kind, "notes": ""})
            draft["sel"] = len(rows) - 1
            draft["filter"] = "all"
    elif name == "archive" and rows:
        gone = rows.pop(draft["sel"])
        if gone["id"]:
            draft["gone"].append(gone["id"])
        draft["sel"] = 0
        return _("%(name)s archived. Save to keep it.") % {"name": gone["name"] or _("Channel")}
    return ""


def problems(draft) -> list[str]:
    names = [r["name"].lower() for r in draft["rows"]]
    if any(not n or names.count(n) > 1 for n in names):
        return [_("Each channel needs its own name")]
    return []


def is_dirty(draft) -> bool:
    return bool(draft["gone"]) or draft["rows"] != draft_of()["rows"]


def uses() -> dict:
    """{channel id: [{"screen", "orient", "as", "rank", "of"}]}: where each channel plays."""
    from screens.models import Screen
    out = {}
    for screen in Screen.objects.exclude(audio=[]):
        entries = [e for e in screen.audio or [] if isinstance(e, dict)]
        for rank, entry in enumerate(entries, 1):
            channel = (entry.get("params") or {}).get("channel")
            if not channel:
                continue
            roles = [_("%(what)s pop-up") % {"what": _("Order ready")} for p in screen.popups or []
                     if isinstance(p, dict) and p.get("widget") == "order_ready" and (p.get("params") or {}).get("channel") == entry.get("id")]
            roles += [_("Announcement pop-up") for p in screen.popups or []
                      if isinstance(p, dict) and p.get("widget") == "announcement" and (p.get("params") or {}).get("channel") == entry.get("id")]
            if _plays_in_widget(screen.layout, entry.get("id")):
                roles.append(_("Now playing widget"))
            out.setdefault(int(channel), []).append({
                "screen": screen.name, "orient": "land" if screen.width >= screen.height else "port",
                "as": ", ".join(str(r) for r in roles) or _("Background audio"), "rank": rank, "of": len(entries)})
    return out


def _plays_in_widget(node, entry_id):
    if not isinstance(node, dict):
        return False
    if (node.get("params") or {}).get("channel") == entry_id:
        return True
    return any(_plays_in_widget(child, entry_id) for child in node.get("children") or [])


def _take_off_screens(channel_ids):
    """Remove these channels from every screen's audio (and what pointed at them there)."""
    from screens.models import Screen
    for screen in Screen.objects.exclude(audio=[]):
        keep, dropped = [], set()
        for entry in screen.audio or []:
            if isinstance(entry, dict) and (entry.get("params") or {}).get("channel") in channel_ids:
                dropped.add(entry.get("id"))
            else:
                keep.append(entry)
        if not dropped:
            continue
        popups = [{**p, "params": {k: v for k, v in (p.get("params") or {}).items() if not (k == "channel" and v in dropped)}}
                  if isinstance(p, dict) else p for p in screen.popups or []]
        Screen.objects.filter(pk=screen.pk).update(audio=keep, popups=popups)


@transaction.atomic
def save(draft):
    retyped = []
    for r in draft["rows"]:
        values = {"name": r["name"], "kind": r["kind"], "notes": r["notes"]}
        if r["id"]:
            before = AudioChannel.objects.filter(pk=r["id"]).values_list("kind", flat=True).first()
            if before and before != r["kind"]:
                retyped.append(r["id"])
            AudioChannel.objects.filter(pk=r["id"]).update(**values)
        else:
            AudioChannel.objects.create(**values)
    AudioChannel.objects.filter(pk__in=draft["gone"]).update(archived=True)
    _take_off_screens(set(retyped) | set(draft["gone"]))


def page(draft, new=None) -> dict:
    rows, k = draft["rows"], draft["sel"]
    where = uses()
    saved_kinds = dict(AudioChannel.objects.values_list("id", "kind"))
    errs = problems(draft)
    dirty = is_dirty(draft)
    if errs:
        status, kind = errs[0], "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    counts = {"all": len(rows), "music": sum(r["kind"] == "music" for r in rows), "voice": sum(r["kind"] == "voice" for r in rows)}
    items = []
    for i, r in enumerate(rows):
        if draft["filter"] != "all" and r["kind"] != draft["filter"]:
            continue
        used = where.get(r["id"], []) if r["id"] else []
        screens_ = list(dict.fromkeys(u["screen"] for u in used))
        items.append({**r, "k": i, "on": i == k, "t": TYPES[r["kind"]],
                      "sub": _("On %(screens)s") % {"screens": ", ".join(screens_)} if screens_ else _("Not on any screen yet")})
    e = None
    if rows:
        r = rows[k]
        used = where.get(r["id"], []) if r["id"] else []
        n = len(used)
        others = [x["name"].lower() for i, x in enumerate(rows) if i != k]
        changed = r["id"] and saved_kinds.get(r["id"]) not in (None, r["kind"])
        t = TYPES[r["kind"]]
        e = {"r": r, "k": k, "t": t, "types": [(key, v) for key, v in TYPES.items()],
             "name_err": _("Give the channel a name.") if not r["name"] else _("Another channel already has this name.") if r["name"].lower() in others else "",
             "type_warn": (ngettext("This channel is on %(n)d screen. Switching to %(type)s takes it off, and clears what was set to play on it there.",
                                    "This channel is on %(n)d screens. Switching to %(type)s takes it off them, and clears what was set to play on it there.", n)
                           % {"n": n, "type": t["label"]}) if changed and n else (_("Switching type clears anything that was set to play on this channel.") if changed else ""),
             "uses": [{**u, "rank_text": (_("Top priority") if u["rank"] == 1 else _("Priority %(r)d of %(n)d") % {"r": u["rank"], "n": u["of"]}) if u["of"] > 1 else _("Only channel")}
                      for u in used],
             "use_head": ngettext("Used on %(n)d screen", "Used on %(n)d screens", n) % {"n": n} if n else _("Not on any screen yet"),
             "use_sub": _("Add or remove it, and set its priority, on each screen’s Audio tab.") if n else _("Nothing will play until a screen uses it."),
             "no_uses": _("Open a screen in Store › Screens, go to its Audio tab and add %(name)s.") % {"name": r["name"] or _("this channel")}
                        + " " + (_("Then point a Now playing widget at it.") if r["kind"] == "music" else _("Then pick it in a pop-up, like Order ready.")),
             "archive_hint": ngettext("It’s removed from %(n)d screen. Lower channels on it move up a place.",
                                      "It’s removed from %(n)d screens. Lower channels on those screens move up a place.", n) % {"n": n}
                             if n else _("It isn’t on any screen, so nothing else changes."),
             "archive_text": _("Remove from screens and archive") if n else _("Archive %(name)s") % {"name": r["name"] or _("channel")},
             "talk": r["id"] and r["kind"] == AudioChannel.VOICE and saved_kinds.get(r["id"]) == AudioChannel.VOICE,
             "open": r["id"] and saved_kinds.get(r["id"]) == r["kind"]}
    return {"d": draft, "items": items, "e": e, "count": len(rows), "status": status, "status_kind": kind, "can_save": dirty and not errs,
            "filters": [(key, label, counts[key]) for key, label in (("all", _("All")), ("music", _("Music")), ("voice", _("Voice")))],
            "empty": _("No music channels yet.") if draft["filter"] == "music" else _("No voice channels yet.") if draft["filter"] == "voice" else _("No channels yet."),
            "new": new, "types": [(key, v) for key, v in TYPES.items()]}
