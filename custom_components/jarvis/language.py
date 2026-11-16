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

import contextlib
import contextvars

# ── Per-request pipeline language (contextvar) ───────────────────────────────
# The conversation / voice pipeline knows the language a turn arrived in
# (``user_input.language``). Chat replies thread it explicitly, but internal
# task prompts built *mid-turn* — above all CAMERA ANALYSIS (async_analyze_camera
# → build_system_prompt → language_task_directive) — are built with no explicit
# ``lang`` and so fell back to the household's GLOBAL language. A German voice
# request in a household whose HA *system* language is Russian therefore had its
# camera narration come back in Russian while the spoken reply was correctly
# German (issue #541). Scoping the turn's language here lets configured_language
# pick it up with no new parameter threaded through the whole camera stack.
# Unset outside a request (background briefings / sentinel notices), so those
# keep using the output/ui/global language exactly as before. Mirrors the
# correlation-id contextvar pattern used elsewhere in the integration.
_REQUEST_LANG: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "jarvis_request_language", default=None)


def set_request_language(lang):
    """Set the current turn's pipeline language. Returns a reset token (or
    ``None`` on error / falsy input). Never raises."""
    try:
        return _REQUEST_LANG.set(lang or None)
    except Exception:
        return None


def reset_request_language(token) -> None:
    """Undo :func:`set_request_language`. Safe with a ``None`` token. Never
    raises."""
    try:
        if token is not None:
            _REQUEST_LANG.reset(token)
    except Exception:
        pass


def _request_language() -> str:
    """The current turn's pipeline language, or ``""`` when none is in scope.
    Never raises."""
    try:
        return _REQUEST_LANG.get() or ""
    except Exception:
        return ""


@contextlib.contextmanager
def request_language_scope(lang):
    """Scope ``lang`` as the current turn's pipeline language so internal task
    prompts follow the language the turn arrived in (issue #541). A no-op for a
    falsy ``lang`` — background work carries no request language and is
    unaffected. Never raises."""
    tok = set_request_language(lang) if lang else None
    try:
        yield
    finally:
        reset_request_language(tok)


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
      1b. The per-request contextvar (:func:`request_language_scope`) — the same
         pipeline language, picked up ambiently when a caller can't thread
         ``lang`` through (e.g. camera analysis built mid-turn). Below an
         explicit ``lang`` (identical source), above every stored setting, so a
         German turn's camera narration is German, not the household global
         (issue #541). Unset outside a request, so background work is unchanged.
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
        raw = lang or _request_language() or _jarvis_output_language() \
            or _jarvis_ui_language() \
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


def translate_directive(hass, lang: str | None = None) -> str:
    """A system prompt that instructs a model to TRANSLATE text into the
    household output language.

    Used to localize JARVIS's *static* alert templates — e.g. Sentinel's
    door/window/garage/lock lines (``"{honorific}, {friendly_name} has been open
    for {minutes} minutes."``), which are plain English format strings spoken
    verbatim and so never followed the configured language (issue #317). The
    caller feeds the already-filled English line as the user message and speaks
    the translation. Returns ``""`` for English / unset — the caller then speaks
    the original text unchanged, so English homes are completely unaffected and
    make no extra model call. Never raises.
    """
    lname = language_name(hass, lang)
    if not lname:
        return ""
    return (
        f"You are a translation engine. Translate the user's message into "
        f"{lname}. Keep entity names, proper nouns and numbers exactly as "
        f"written. Output only the translation, with no preamble or quotes."
    )
