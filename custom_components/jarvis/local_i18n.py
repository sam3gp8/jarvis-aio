"""Zero-API localization for the local intent engine's canned replies (#511).

The local fast path (``local_engine.try_local``) answers without any model call,
so its confirmations and query answers were hard-coded English regardless of the
household's ``output_language``. A German user got "Bodenleuchte Flur is currently
off, Sir." This module holds *static* per-language templates and vocabulary so
those replies follow the configured language with **no API call and no added
latency** — exactly the opposite trade-off from routing every reply through a
translation model.

Design:
  * English (``en``) is the canonical catalogue. Any missing language OR any
    missing key falls back to the English template, so an English household is
    byte-for-byte unchanged and a partially-translated language never shows a
    blank.
  * Templates use ``str.format`` named fields. ``t()`` never raises: a bad field
    set falls back to the English template, then to the raw template.
  * Entity friendly-names, the user's honorific, numbers and units pass through
    untouched — only JARVIS's own words are translated.

Adding a language = adding its dict under each table below (ideally
native-speaker reviewed). Nothing else changes.
"""
from __future__ import annotations

from typing import Optional

# ── Varied lead-ins (MCU style; _pick keeps them from repeating) ─────────────
_LEADINS = {
    "en": {
        "ack": ["Done", "Right away", "As you wish", "Consider it done", "At once"],
        "sec": ["Secured", "Done", "Locked up"],
    },
    "de": {
        "ack": ["Erledigt", "Sofort", "Wie Sie wünschen", "Sehr wohl", "Sogleich"],
        "sec": ["Gesichert", "Erledigt", "Verriegelt"],
    },
}

# ── HA state values that surface inside replies ("… is currently {state}") ───
_STATE_WORDS = {
    "de": {
        "on": "an", "off": "aus", "open": "offen", "opening": "öffnet",
        "closed": "geschlossen", "closing": "schließt", "locked": "verriegelt",
        "unlocked": "entriegelt", "home": "zu Hause", "not_home": "abwesend",
        "idle": "inaktiv", "playing": "spielt", "paused": "pausiert",
        "unavailable": "nicht verfügbar", "unknown": "unbekannt",
        "heat": "Heizen", "cool": "Kühlen", "auto": "Automatik",
    },
}

# ── Pluralisable nouns: (singular, plural) per language ──────────────────────
# English plurals reproduce the engine's prior literal output exactly (note the
# intentional "persons", matching the old `person{'s'}` code — do not "fix").
_NOUNS = {
    "en": {
        "light": ("light", "lights"), "lock": ("lock", "locks"),
        "door": ("door", "doors"), "person": ("person", "persons"),
        "device": ("device", "devices"), "fan": ("fan", "fans"),
        "window": ("window", "windows"), "cover": ("cover", "covers"),
        "switch": ("switch", "switches"),
    },
    "de": {
        "light": ("Licht", "Lichter"), "lock": ("Schloss", "Schlösser"),
        "door": ("Tür", "Türen"), "person": ("Person", "Personen"),
        "device": ("Gerät", "Geräte"), "fan": ("Ventilator", "Ventilatoren"),
        "window": ("Fenster", "Fenster"), "cover": ("Rollo", "Rollos"),
        "switch": ("Schalter", "Schalter"),
    },
}

# ── Bulk-action verb as it reads in a summary line ───────────────────────────
# English reproduces the old `action.replace("_", " ")`. German uses the past
# participle, which is where it naturally sits in the sentence.
_BULK_VERB = {
    "en": {
        "turn_on": "turn on", "turn_off": "turn off", "lock": "lock",
        "unlock": "unlock", "open": "open", "close": "close",
    },
    "de": {
        "turn_on": "eingeschaltet", "turn_off": "ausgeschaltet",
        "lock": "verriegelt", "unlock": "entriegelt",
        "open": "geöffnet", "close": "geschlossen",
    },
}

# ── Time-of-day greeting opener ──────────────────────────────────────────────
_GREET = {
    "en": ("Good morning", "Good afternoon", "Good evening"),
    "de": ("Guten Morgen", "Guten Tag", "Guten Abend"),
}

# German weekday / month names so the date reply is actually German (strftime
# emits English regardless of locale). English keeps strftime.
_DE_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                "Samstag", "Sonntag"]
_DE_MONTHS = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]

# ── Message templates ────────────────────────────────────────────────────────
_TEMPLATES = {
    "en": {
        # _resp (command acknowledgments)
        "turn_on": "{ack}, {h}. {name} is on.",
        "turn_off": "{ack}, {h}. {name} is off.",
        "toggle": "{ack}, {h}. {name} toggled.",
        "lock": "{sec}, {h}. {name} is locked.",
        "unlock": "{ack}, {h}. {name} is unlocked.",
        "open": "Opening {name} now, {h}.",
        "close": "Closing {name} now, {h}.",
        "dim": "{ack}, {h}. {name} at {bp}%.",
        "brighten": "{ack}, {h}. {name} at full brightness.",
        "set_temp": "{ack}, {h}. Temperature set to {temp}°.",
        "set_temp_named": "{ack}, {h}. {name} set to {temp}°.",
        "media_pause": "Paused, {h}.",
        "media_play": "Playing now, {h}.",
        "media_next": "Next track, {h}.",
        "volume_up": "Volume up, {h}.",
        "volume_down": "Volume down, {h}.",
        "volume_set": "Volume at {vol}%, {h}.",
        "mute": "Muted, {h}.",
        "unmute": "Unmuted, {h}.",
        "resp_fallback": "{ack}, {h}. {action} applied to {name}.",
        "resp_fail": ("I wasn't able to {action} {name}, {h} — there may be a "
                      "connectivity issue."),
        # _query_resp
        "query_time": "It's currently {time}, {h}.",
        "query_date": "Today is {date}, {h}.",
        "greet_plain": "{g}, {h}.",
        "greet_help": "{g}, {h}. What can I do for you?",
        "greet_disposal": "{g}, {h}. At your disposal.",
        "thanks_1": "Of course, {h}.",
        "thanks_2": "My pleasure, {h}.",
        "thanks_3": "Anytime, {h}.",
        "query_temp": "The temperature in {name} is currently {state}{unit}, {h}.",
        "query_temp_fail": ("I'm unable to read the temperature for {name} at the "
                            "moment, {h}."),
        "query_state": "{name} is currently {state}, {h}.",
        "query_state_fail": ("I'm unable to determine the status of {name} at the "
                             "moment, {h}."),
        # _home_status
        "status_header": "All systems nominal, {h}.",
        "status_lights": "{n} {noun} on.",
        "status_locks_ok": "All locks secured.",
        "status_locks_unlocked": "{n} {noun} unlocked.",
        "status_doors": "{n} {noun} open.",
        "status_people": "{n} {noun} home.",
        # _ctx_query
        "who_none": "No one appears to be home at the moment, {h}.",
        "who_one": "{name} is currently home, {h}.",
        "who_many": "{names} and {last} are currently home, {h}.",
        "open_none": "Everything is closed and secured, {h}.",
        "open_list": "Currently open or unlocked: {items}.",
        "open_unlocked_suffix": " (unlocked)",
        "lights_none": "All lights are off, {h}.",
        "lights_list": "{c} {noun} on: {listing}{ellipsis}.",
        "energy": "Current power consumption is {state} {unit}, {h}.",
        "energy_none": "I don't have a total power sensor configured, {h}.",
        "weather": ("Currently {condition} outside, {h}. Temperature is {temp}° "
                    "with {humidity}% humidity."),
        "weather_outdoor": "The outdoor temperature is {state}{unit}, {h}.",
        "weather_none": "I don't have weather data available at the moment, {h}.",
        "area_none": "I couldn't find an area matching '{area}', {h}.",
        "area_empty": "No devices found in {name}, {h}.",
        "area_list": "{name} has {n} {noun}: {listing}{more}.",
        "area_more": " and {n} more",
        # try_local inline
        "bulk_alloff": "Done, {h}. {total} {noun} turned off.",
        "bulk_generic": "Done, {h}. {ok} {noun}{area} {verb}.",
        "area_in": " in {area}",
        "scene_activating": "Activating {name} now, {h}.",
        "scene_fail": "I wasn't able to activate {name}, {h}. {exc}",
        "goodnight_scene": "Goodnight, {h}. {name} activated. Rest well.",
        "goodnight_summary": ("Goodnight, {h}. {n} {noun} off, all locks secured. "
                              "Rest well."),
        "offline_notfound": ("I couldn't find a device matching '{name}', {h}, and "
                             "I'm offline so I can't do a deeper search right now. "
                             "Try the exact device name."),
        "appliance_tracking": ("Already on it, {h}. I'm monitoring {items}. I'll "
                               "announce when the cycle completes."),
        "appliance_nosensors": ("I'm monitoring for appliance cycles, {h}, but I "
                                "haven't found any power sensors matching your "
                                "appliances yet. Make sure the power monitoring "
                                "sensor has 'washer', 'dryer', or 'dishwasher' in "
                                "its name."),
        "appliance_notrunning": ("The appliance monitor isn't running at the "
                                 "moment, {h}. It starts automatically with the "
                                 "observer. Check if the observer is enabled."),
        "appliance_generic": ("I'll keep an eye on it, {h}. If I have a power "
                              "sensor for that appliance, I'll announce when the "
                              "cycle finishes."),
    },
    "de": {
        "turn_on": "{ack}, {h}. {name} ist an.",
        "turn_off": "{ack}, {h}. {name} ist aus.",
        "toggle": "{ack}, {h}. {name} umgeschaltet.",
        "lock": "{sec}, {h}. {name} ist verriegelt.",
        "unlock": "{ack}, {h}. {name} ist entriegelt.",
        "open": "{name} wird geöffnet, {h}.",
        "close": "{name} wird geschlossen, {h}.",
        "dim": "{ack}, {h}. {name} auf {bp}%.",
        "brighten": "{ack}, {h}. {name} auf voller Helligkeit.",
        "set_temp": "{ack}, {h}. Temperatur auf {temp}° gestellt.",
        "set_temp_named": "{ack}, {h}. {name} auf {temp}° gestellt.",
        "media_pause": "Pausiert, {h}.",
        "media_play": "Wird abgespielt, {h}.",
        "media_next": "Nächster Titel, {h}.",
        "volume_up": "Lauter, {h}.",
        "volume_down": "Leiser, {h}.",
        "volume_set": "Lautstärke auf {vol}%, {h}.",
        "mute": "Stummgeschaltet, {h}.",
        "unmute": "Stummschaltung aufgehoben, {h}.",
        "resp_fallback": "{ack}, {h}. Aktion auf {name} angewendet.",
        "resp_fail": ("Ich konnte {name} nicht schalten, {h} — möglicherweise ein "
                      "Verbindungsproblem."),
        "query_time": "Es ist jetzt {time}, {h}.",
        "query_date": "Heute ist {date}, {h}.",
        "greet_plain": "{g}, {h}.",
        "greet_help": "{g}, {h}. Was kann ich für Sie tun?",
        "greet_disposal": "{g}, {h}. Zu Ihren Diensten.",
        "thanks_1": "Gern geschehen, {h}.",
        "thanks_2": "Sehr gerne, {h}.",
        "thanks_3": "Jederzeit, {h}.",
        "query_temp": "Die Temperatur in {name} beträgt derzeit {state}{unit}, {h}.",
        "query_temp_fail": ("Ich kann die Temperatur für {name} momentan nicht "
                            "auslesen, {h}."),
        "query_state": "{name} ist derzeit {state}, {h}.",
        "query_state_fail": ("Ich kann den Status von {name} momentan nicht "
                             "ermitteln, {h}."),
        "status_header": "Alle Systeme im Normalbetrieb, {h}.",
        "status_lights": "{n} {noun} an.",
        "status_locks_ok": "Alle Schlösser gesichert.",
        "status_locks_unlocked": "{n} {noun} entriegelt.",
        "status_doors": "{n} {noun} offen.",
        "status_people": "{n} {noun} zu Hause.",
        "who_none": "Momentan scheint niemand zu Hause zu sein, {h}.",
        "who_one": "{name} ist derzeit zu Hause, {h}.",
        "who_many": "{names} und {last} sind derzeit zu Hause, {h}.",
        "open_none": "Alles ist geschlossen und gesichert, {h}.",
        "open_list": "Derzeit offen oder entriegelt: {items}.",
        "open_unlocked_suffix": " (entriegelt)",
        "lights_none": "Alle Lichter sind aus, {h}.",
        "lights_list": "{c} {noun} an: {listing}{ellipsis}.",
        "energy": "Aktueller Stromverbrauch: {state} {unit}, {h}.",
        "energy_none": "Ich habe keinen Gesamt-Stromsensor konfiguriert, {h}.",
        "weather": ("Draußen derzeit {condition}, {h}. Temperatur {temp}° bei "
                    "{humidity}% Luftfeuchtigkeit."),
        "weather_outdoor": "Die Außentemperatur beträgt {state}{unit}, {h}.",
        "weather_none": "Mir liegen momentan keine Wetterdaten vor, {h}.",
        "area_none": "Ich konnte keinen Bereich finden, der zu '{area}' passt, {h}.",
        "area_empty": "Keine Geräte in {name} gefunden, {h}.",
        "area_list": "{name} hat {n} {noun}: {listing}{more}.",
        "area_more": " und {n} weitere",
        "bulk_alloff": "Erledigt, {h}. {total} {noun} ausgeschaltet.",
        "bulk_generic": "Erledigt, {h}. {ok} {noun}{area} {verb}.",
        "area_in": " in {area}",
        "scene_activating": "{name} wird aktiviert, {h}.",
        "scene_fail": "Ich konnte {name} nicht aktivieren, {h}. {exc}",
        "goodnight_scene": "Gute Nacht, {h}. {name} aktiviert. Schlafen Sie gut.",
        "goodnight_summary": ("Gute Nacht, {h}. {n} {noun} aus, alle Schlösser "
                              "gesichert. Schlafen Sie gut."),
        "offline_notfound": ("Ich konnte kein Gerät finden, das zu '{name}' passt, "
                             "{h}, und ich bin offline, kann also gerade nicht "
                             "tiefer suchen. Versuchen Sie den genauen "
                             "Gerätenamen."),
        "appliance_tracking": ("Bin bereits dran, {h}. Ich überwache {items}. Ich "
                               "melde mich, wenn der Zyklus abgeschlossen ist."),
        "appliance_nosensors": ("Ich achte auf Geräte-Zyklen, {h}, habe aber noch "
                                "keine Leistungssensoren gefunden, die zu Ihren "
                                "Geräten passen. Stellen Sie sicher, dass der "
                                "Leistungssensor 'washer', 'dryer' oder "
                                "'dishwasher' im Namen trägt."),
        "appliance_notrunning": ("Die Geräteüberwachung läuft momentan nicht, {h}. "
                                 "Sie startet automatisch mit dem Observer. Prüfen "
                                 "Sie, ob der Observer aktiviert ist."),
        "appliance_generic": ("Ich behalte es im Auge, {h}. Wenn ich einen "
                              "Leistungssensor für dieses Gerät habe, melde ich "
                              "mich, wenn der Zyklus endet."),
    },
}


def _norm(lang: Optional[str]) -> str:
    """Primary subtag, lowercased; '' / None → 'en'."""
    if not lang:
        return "en"
    return str(lang).split("-")[0].strip().lower() or "en"


def t(key: str, lang: Optional[str] = "en", **kw) -> str:
    """Render message ``key`` in ``lang``; never raises.

    Falls back to the English template when the language or key is missing, and
    again (bare template) if a format field is absent, so a response is always
    produced."""
    code = _norm(lang)
    en = _TEMPLATES["en"]
    tmpl = _TEMPLATES.get(code, {}).get(key)
    if tmpl is None:
        tmpl = en.get(key, "")
    try:
        return tmpl.format(**kw)
    except Exception:
        try:
            return en.get(key, "").format(**kw)
        except Exception:
            return en.get(key, "")


def leadins(kind: str, lang: Optional[str] = "en") -> list:
    """The varied lead-in words for ``kind`` ('ack' / 'sec') in ``lang``."""
    code = _norm(lang)
    table = _LEADINS.get(code) or _LEADINS["en"]
    return table.get(kind) or _LEADINS["en"].get(kind, [""])


def greet_word(hour: int, lang: Optional[str] = "en") -> str:
    """Time-of-day greeting opener for ``hour`` (0–23) in ``lang``."""
    code = _norm(lang)
    morning, afternoon, evening = _GREET.get(code) or _GREET["en"]
    if hour < 12:
        return morning
    if hour < 18:
        return afternoon
    return evening


def state_word(state, lang: Optional[str] = "en") -> str:
    """Translate an HA state value ('on'/'off'/'open'/…) for display.

    Unknown states (and all English) pass through unchanged, so a numeric or
    free-form state is never mangled."""
    code = _norm(lang)
    if code == "en":
        return str(state)
    return _STATE_WORDS.get(code, {}).get(str(state), str(state))


def noun(key: str, n: int, lang: Optional[str] = "en") -> str:
    """The singular/plural form of a known noun for count ``n`` in ``lang``.

    Falls back to the English noun table, then to the raw key, so an unmapped
    noun still renders."""
    code = _norm(lang)
    forms = _NOUNS.get(code, {}).get(key) or _NOUNS["en"].get(key)
    if not forms:
        return key
    return forms[0] if n == 1 else forms[1]


def bulk_verb(action: str, lang: Optional[str] = "en") -> str:
    """How a bulk action reads in a summary line ('turn on' / 'eingeschaltet')."""
    code = _norm(lang)
    table = _BULK_VERB.get(code) or _BULK_VERB["en"]
    return table.get(action) or _BULK_VERB["en"].get(action, action.replace("_", " "))


def format_time(dt, lang: Optional[str] = "en") -> str:
    """Localised clock time. English keeps 12-hour AM/PM; others use 24-hour."""
    code = _norm(lang)
    if code == "en":
        return dt.strftime("%I:%M %p")
    return dt.strftime("%H:%M")


def format_date(dt, lang: Optional[str] = "en") -> str:
    """Localised long date. English uses strftime; German uses its own names."""
    code = _norm(lang)
    if code == "de":
        return f"{_DE_WEEKDAYS[dt.weekday()]}, {dt.day}. {_DE_MONTHS[dt.month]} {dt.year}"
    return dt.strftime("%A, %B %d, %Y")
