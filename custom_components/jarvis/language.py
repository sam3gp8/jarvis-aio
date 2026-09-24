"""
JARVIS — Household-language directive (single source of truth).

Both the conversation path (``agent.run_agent``) and every task prompt built
via ``directive_helper.build_system_prompt`` (status briefs, camera analysis,
sentinel notices, …) use :func:`language_directive`, so JARVIS's spoken and
generated output follows the home's configured language consistently rather
than only in chat replies.

This is a leaf module with no package imports, so it is cheap to import from
anywhere without pulling in heavier chains.
"""
from __future__ import annotations

# Language names keyed by the primary ISO-639 subtag of Home Assistant's
# configured language.
_LANG_NAMES = {
    "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
    "nl": "Dutch", "pt": "Portuguese", "pl": "Polish", "sv": "Swedish",
    "nb": "Norwegian", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "cs": "Czech", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    "he": "Hebrew", "el": "Greek", "hu": "Hungarian", "ro": "Romanian",
    "sk": "Slovak", "ca": "Catalan", "id": "Indonesian", "th": "Thai",
    "vi": "Vietnamese",
}


def language_directive(hass) -> str:
    """A system-prompt block steering output to the home's configured language.

    Uses Home Assistant's ``language`` so a non-English household gets JARVIS's
    output in its own language. Returns ``""`` for English installs (which are
    therefore completely unaffected). The user's own input language still wins
    if they write in something else. Never raises.
    """
    try:
        lang = (getattr(hass.config, "language", None) or "en").split("-")[0].lower()
    except Exception:
        return ""
    if not lang or lang == "en":
        return ""
    lname = _LANG_NAMES.get(lang, lang)
    return (
        f"## Language\n"
        f"Respond in {lname} by default — this household's configured language "
        f"is {lname}. If the user writes to you in another language, reply in "
        f"that language instead. Keep entity names and proper nouns unchanged.\n"
    )
