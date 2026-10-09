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


def _clean_lang_setting(val) -> str:
    """Normalise a stored language setting: ``""`` for unset / auto / default."""
    v = (val or "").strip()
    return "" if v.lower() in ("", "auto", "default") else v


def _jarvis_output_language() -> str:
    """JARVIS's own output-language setting, or ``""`` when unset / 'auto'.

    This lets a household run Home Assistant's UI in English (or any language)
    while still having JARVIS *speak and write* in another — the common case in
    issue #148, where briefings and camera analysis came out in English because
    the only language source was HA's global setting. Never raises."""
    try:
        from . import jarvis_config
        return _clean_lang_setting(jarvis_config.get("output_language", ""))
    except Exception:
        return ""


def _jarvis_ui_language() -> str:
    """JARVIS's panel UI-language setting, or ``""`` when unset / 'auto'.

    Used only as a fallback for :func:`configured_language` when the dedicated
    ``output_language`` is on Auto: a household that set JARVIS's *own* UI to,
    say, Russian clearly wants JARVIS to speak and write Russian too, yet with
    ``output_language`` unset the only remaining source was Home Assistant's
    **system** language — which a user who changed only their *profile* language
    never set, so announces and camera analysis came out in English while live
    voice (carrying a per-request language) was correct (issue #317). Never
    raises."""
    try:
        from . import jarvis_config
        return _clean_lang_setting(jarvis_config.get("ui_language", ""))
    except Exception:
        return ""


def configured_language(hass, lang: str | None = None) -> str:
    """The language JARVIS should answer in, as a primary ISO-639 subtag
    (e.g. ``"de"`` for ``"de-DE"``), ``"en"`` when unset or on error.

    Resolution order (highest first):
      1. ``lang`` — a per-request override, the conversation / voice pipeline
         language (``user_input.language``). A request coming through a German
         satellite is answered in German even in a household whose global
         language is Russian (the case that produced garbled voice replies).
      2. JARVIS's own ``output_language`` setting — so a home can have JARVIS
         speak German while Home Assistant's UI stays English (issue #148).
      3. JARVIS's panel ``ui_language`` setting — if you set JARVIS's own UI to
         a language you almost certainly want its output in it too, so this is a
         fallback when ``output_language`` is Auto. Fixes the case where
         announces / camera analysis stayed English because the user changed
         only their HA *profile* language (not the system one) that step 4 reads
         (issue #317).
      4. Home Assistant's global (system) ``language``.
      5. ``"en"``.

    Never raises."""
    try:
        raw = lang or _jarvis_output_language() or _jarvis_ui_language() \
            or getattr(hass.config, "language", None) or "en"
        return (raw or "en").split("-")[0].lower()
    except Exception:
        return "en"


def language_name(hass, lang: str | None = None) -> str:
    """The display name of the effective language (e.g. ``"German"``), or ``""``
    for English / unset — i.e. non-empty exactly when JARVIS should steer output
    to a non-English language. ``lang`` overrides the global setting as in
    :func:`configured_language`. Never raises."""
    code = configured_language(hass, lang)
    if not code or code == "en":
        return ""
    return _LANG_NAMES.get(code, code)


def language_directive(hass, lang: str | None = None) -> str:
    """A system-prompt block steering output to the effective language.

    Uses Home Assistant's ``language`` so a non-English household gets JARVIS's
    output in its own language. ``lang`` is an optional per-request override
    (the conversation / voice-pipeline language) that wins over the global
    setting, so replies follow the language the request actually came in on.
    Returns ``""`` for English (which is therefore completely unaffected). The
    user's own input language still wins if they write in something else. Never
    raises.
    """
    lname = language_name(hass, lang)
    if not lname:
        return ""
    return (
        f"## Language\n"
        f"Respond in {lname} by default. If the user clearly writes to you in "
        f"another language, reply in that language instead. Keep entity names and "
        f"proper nouns unchanged.\n"
    )


def language_task_directive(hass, lang: str | None = None) -> str:
    """A **forced** output-language suffix for internal, machine-authored task
    prompts (camera analysis, briefings, sentinel notices).

    :func:`language_directive` carries a "reply in the user's language" escape
    clause — right for a conversation, but wrong for a vision analysis whose own
    instruction text is in English: the model reads that English instruction as
    "the user wrote in English" and answers in English (issue #140). This
    directive has no escape clause — the task text is not the user speaking — so
    the model still produces its output in the configured language. Returns ``""``
    for English (unaffected). Designed to append to a task string, so it leads
    with a space. Never raises.
    """
    lname = language_name(hass, lang)
    if not lname:
        return ""
    return (
        f" Write your entire response in {lname}, regardless of the language "
        f"these instructions are written in. Keep entity names and proper nouns "
        f"unchanged."
    )
