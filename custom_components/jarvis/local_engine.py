"""
JARVIS Local Intent Engine (v5.7.00).

PRIMARY handler for all conversations and event reasoning. Zero API calls.
Handles 95%+ of user requests locally with pattern matching, HA entity
resolution, contextual state queries, multi-entity commands, scene/script
activation, media control, and conversational responses.

Only genuinely complex or ambiguous requests fall through to the configured
cloud LLM provider.

Architecture:
  1. Regex intent matching (commands, queries, greetings)
  2. Contextual multi-entity resolution (area-based, group, "all X")
  3. HA state introspection (who's home, what's open, etc.)
  4. Scene/script/automation triggers
  5. Media player control
  6. Complexity scoring (decides local vs LLM escalation)
  7. Follow-up context tracking
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Rolling memory of the last few acknowledgment lead-ins used, so JARVIS does
# not repeat the same opener twice in a row — variety without randomness that
# feels chaotic. MCU JARVIS rarely says the same confirmation back-to-back.
_recent_acks: list[str] = []


def _pick(options: list[str]) -> str:
    """
    Choose a phrasing that wasn't just used. Keeps confirmations from sounding
    canned while staying deterministic-enough (no external state, no cost).
    """
    if not options:
        return ""
    fresh = [o for o in options if o not in _recent_acks]
    choice = random.choice(fresh) if fresh else random.choice(options)
    _recent_acks.append(choice)
    if len(_recent_acks) > 4:
        _recent_acks.pop(0)
    return choice


# ── Result types ────────────────────────────────────────────────────────────

@dataclass
class LocalResult:
    """Result of local intent execution."""
    text: str
    success: bool
    handled: bool = True   # False = couldn't handle, fall through to LLM


# ── Follow-up context ──────────────────────────────────────────────────────

class _ConvCtx:
    last_entity: str = ""
    last_area: str = ""
    last_domain: str = ""
    last_action: str = ""
    last_ts: float = 0.0

_CTX = _ConvCtx()

def _update_ctx(**kw):
    for k, v in kw.items():
        if v:
            setattr(_CTX, f"last_{k}", v)
    _CTX.last_ts = time.time()

def _ctx_fresh():
    return (time.time() - _CTX.last_ts) < 120


# ── Normalization ───────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[.,!?;:]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


# ── Intent patterns ─────────────────────────────────────────────────────────

_INTENT_PATTERNS = [
    # Lights
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",       "turn_on",  "light"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",      "turn_off", "light"),
    (r"switch\s+on\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",     "turn_on",  "light"),
    (r"switch\s+off\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",    "turn_off", "light"),
    (r"lights?\s+on\s+(?:in\s+)?(?:the\s+)?(.+)$",              "turn_on",  "light"),
    (r"lights?\s+off\s+(?:in\s+)?(?:the\s+)?(.+)$",             "turn_off", "light"),
    (r"dim\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?\s+to\s+(\d+)", "dim",      "light"),
    (r"set\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?\s+(?:brightness\s+)?to\s+(\d+)",
     "dim", "light"),
    (r"brighten\s+(?:the\s+)?(.+)", "brighten", "light"),
    # Switches
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+switch)?$",   "turn_on",  "switch"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+switch)?$",  "turn_off", "switch"),
    # German on/off verbs (issue #511). German puts the separable particle at the
    # END — "schalte das Licht ein/aus", "mach die Lampe an/aus" — or uses the
    # infinitive "<Gerät> einschalten/ausschalten". Entity resolution already
    # worked in German; only the action verb was unrecognised, so these sentences
    # fell through to a bogus state query ("… is currently off") instead of
    # actuating. domain_hint=None lets the resolver match a light, switch, etc.
    (r"(?:schalte?|mache?)\s+(?:(?:das|die|der|den|dem)\s+)?(.+?)\s+(?:ein|an)$",
     "turn_on", None),
    (r"(?:schalte?|mache?)\s+(?:(?:das|die|der|den|dem)\s+)?(.+?)\s+aus$",
     "turn_off", None),
    (r"(.+?)\s+einschalten$", "turn_on", None),
    (r"(.+?)\s+ausschalten$", "turn_off", None),
    # Locks (unlock before lock; lock's (?<!un) stops it matching inside "unlock")
    (r"unlock\s+(?:the\s+)?(.+?)(?:\s+door)?$", "unlock", "lock"),
    (r"(?<!un)lock\s+(?:the\s+)?(.+?)(?:\s+door)?$",   "lock",   "lock"),
    # Covers / Garage
    (r"open\s+(?:the\s+)?(.+?)(?:\s+(?:door|gate|cover|garage))?$",  "open",  "cover"),
    (r"close\s+(?:the\s+)?(.+?)(?:\s+(?:door|gate|cover|garage))?$", "close", "cover"),
    # Climate
    (r"set\s+(?:the\s+)?(?:temperature|thermostat|temp)\s+(?:to|at)\s+(\d+)",
     "set_temp", "climate"),
    (r"set\s+(?:the\s+)?(.+?)\s+(?:temperature|thermostat|temp)\s+(?:to|at)\s+(\d+)",
     "set_temp_named", "climate"),
    (r"(?:make\s+it|set\s+it)\s+(?:to\s+)?(\d+)\s*(?:degrees|°)?", "set_temp", "climate"),
    # Fan
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+fan)?$",   "turn_on",  "fan"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+fan)?$",  "turn_off", "fan"),
    # Media
    (r"(?:pause|stop)\s+(?:the\s+)?(?:music|media|tv|playback)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_pause", "media_player"),
    (r"(?:resume|play|unpause)\s+(?:the\s+)?(?:music|media|tv|playback)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_play", "media_player"),
    (r"(?:volume\s+up|turn\s+(?:it|the\s+volume)\s+up)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_up", "media_player"),
    (r"(?:volume\s+down|turn\s+(?:it|the\s+volume)\s+down)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_down", "media_player"),
    (r"(?:set\s+)?volume\s+(?:to\s+)?(\d+)(?:\s+(?:percent|%?))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_set", "media_player"),
    (r"mute(?:\s+(?:the\s+)?(?:tv|speaker|media))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "mute", "media_player"),
    (r"unmute(?:\s+(?:the\s+)?(?:tv|speaker|media))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "unmute", "media_player"),
    (r"(?:skip|next)\s+(?:track|song)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_next", "media_player"),
    # Scenes/scripts
    (r"(?:activate|run|trigger|start|execute)\s+(?:the\s+)?(.+?)(?:\s+scene)?$",
     "scene", None),
    # State queries
    (r"(?:what(?:'s| is)\s+the\s+)?temperature\s+(?:in\s+)?(?:the\s+)?(.+)",
     "query_temp", None),
    (r"(?:what(?:'s| is)\s+the\s+)?(.+?)(?:\s+temperature)", "query_temp", None),
    (r"(?:is|are)\s+(?:the\s+)?(.+?)\s+(?:on|off|open|closed|locked|unlocked)",
     "query_state", None),
    (r"(?:what(?:'s| is)\s+the\s+)?status\s+of\s+(?:the\s+)?(.+)",
     "query_state", None),
    # Time/Date
    (r"what\s+time\s+is\s+it",      "query_time", None),
    (r"what(?:'s| is)\s+the\s+time", "query_time", None),
    (r"what(?:'s| is)\s+today(?:'s)?\s+date", "query_date", None),
    # Greetings
    (r"^(?:hey|hi|hello|good\s+(?:morning|afternoon|evening))(?:\s+jarvis)?[.!]?$",
     "greeting", None),
    (r"^(?:thanks|thank\s+you|cheers)(?:\s+.*)?$", "thanks", None),
    (r"^(?:you'?re\s+welcome|no\s+problem|no\s+worries)", "thanks", None),
    # Home status
    (r"(?:home|house)\s+status",     "status", None),
    (r"how(?:'s| is)\s+the\s+house", "status", None),
    (r"system\s+status",             "status", None),
    (r"run\s+(?:a\s+)?diagnostic",   "status", None),
]

# ── Multi-entity / bulk patterns ────────────────────────────────────────────

_BULK_PATTERNS = [
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?lights?$", "turn_off", "light", None),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?lights?$",  "turn_on",  "light", None),
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?lights?\s+(?:in|on)\s+(?:the\s+)?(.+)$",
     "turn_off", "light", "area"),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?lights?\s+(?:in|on)\s+(?:the\s+)?(.+)$",
     "turn_on", "light", "area"),
    (r"unlock\s+(?:all\s+)?(?:the\s+)?doors?$", "unlock", "lock", None),
    (r"(?<!un)lock\s+(?:all\s+)?(?:the\s+)?doors?$",   "lock",   "lock", None),
    (r"close\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$", "close", "cover", None),
    (r"open\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$",  "open",  "cover", None),
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?fans?$", "turn_off", "fan", None),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?fans?$",  "turn_on",  "fan", None),
    (r"turn\s+(?:off|out)\s+everything$", "turn_off", "all", None),
]

# ── Contextual queries ──────────────────────────────────────────────────────

# These are scanned BEFORE the command patterns (see try_local), so a query
# pattern that can match a bare command word would hijack the command — e.g.
# `open` in "open the garage door" matching a `what_open` query. The
# interrogative prefix on the vulnerable patterns is therefore REQUIRED (not
# optional): a real question like "what's open" still matches, but a command that
# merely *contains* the word (open / home / weather / outside) no longer does.
_QUERY_PATTERNS = [
    (r"(?:who(?:'s| is)\s+)home\b",                     "who_home"),
    (r"(?:is\s+)?(?:anyone|anybody)\s+home",              "who_home"),
    (r"(?:what(?:'s| is)\s+)(?:open|unlocked)",          "what_open"),
    (r"(?:are\s+)?(?:any|which)\s+(?:doors?|windows?)\s+open", "what_open"),
    (r"(?:are\s+)?(?:any|which)\s+(?:lights?)\s+on",     "lights_on"),
    (r"(?:how\s+many)\s+lights?\s+(?:are\s+)?on",        "lights_on"),
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)?(?:energy|power)\s+(?:usage|consumption)", "energy"),
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)weather",        "weather"),
    (r"(?:what(?:'s| is)\s+(?:it\s+)?)(?:like\s+)?outside", "weather"),
    (r"(?:how\s+(?:warm|cold|hot))\s+is\s+it",           "weather"),
    (r"(?:what\s+)?(?:devices?|entities?)\s+(?:are\s+)?(?:in|at)\s+(?:the\s+)?(.+)", "area_devices"),
]


# ── Complexity scoring ──────────────────────────────────────────────────────

def score_complexity(text: str) -> int:
    """Score 0-100. Higher = needs LLM. <70 handled locally."""
    t = text.lower()
    score = 20
    # Strong LLM signals
    for w in ("explain", "why", "how does", "what do you think",
              "write", "compose", "draft", "create a", "help me",
              "analyze", "compare", "recommend", "suggest",
              "tell me about", "what happened", "story",
              "code", "script", "program", "debug", "fix this",
              "plan", "schedule", "strategy", "brainstorm",
              "summarize", "translate", "calculate"):
        if w in t:
            score += 40
            break
    if len(text.split()) > 20:
        score += 15
    if "?" in text and len(text.split()) > 10:
        score += 10
    for w in ("because", "however", "although"):
        if w in t:
            score += 10
            break
    # Simple signals (reduce)
    for w in ("turn on", "turn off", "lock", "unlock", "open", "close",
              "dim", "brighten", "set temp", "volume", "pause", "play"):
        if w in t:
            score -= 20
            break
    for w in ("what time", "who's home", "how many lights",
              "what's open", "temperature", "status",
              "good morning", "good night", "hello", "thanks"):
        if w in t:
            score -= 15
            break
    return max(0, min(100, score))


# ── Entity resolution ────────────────────────────────────────────────────────

def _fuzzy_score(a: str, b: str) -> float:
    """
    Simple fuzzy similarity score (0-100) without external deps.
    Combines character overlap, word overlap, and edit distance approximation.
    """
    if a == b:
        return 100.0
    if not a or not b:
        return 0.0

    # Character bigram overlap (Dice coefficient)
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s)-1)) if len(s) > 1 else {s}

    bg_a, bg_b = bigrams(a), bigrams(b)
    if bg_a and bg_b:
        overlap = len(bg_a & bg_b)
        dice = (2.0 * overlap) / (len(bg_a) + len(bg_b)) * 100
    else:
        dice = 0.0

    # Word overlap
    words_a, words_b = set(a.split()), set(b.split())
    if words_a and words_b:
        common = len(words_a & words_b)
        word_score = common / max(len(words_a), len(words_b)) * 100
    else:
        word_score = 0.0

    # Containment bonus
    contain = 0.0
    if a in b:
        contain = len(a) / len(b) * 80
    elif b in a:
        contain = len(b) / len(a) * 80

    return max(dice, word_score, contain)


# Common STT mishearing patterns: what STT outputs → what user likely meant
_STT_CORRECTIONS = {
    "lamp": "light", "lamps": "lights",
    "lite": "light", "lites": "lights",
    "nite": "night", "nitestand": "nightstand",
    "night stand": "nightstand",
    "bed side": "bedside", "bed lamp": "bedside light",
    "chase": "chase", "chaise": "chase",
    "tv": "television", "teevee": "tv",
    "a.c.": "ac", "air con": "air conditioner",
    "thermos": "thermostat", "thermo": "thermostat",
    "bed room": "bedroom", "living room": "living_room",
    "bath room": "bathroom", "dining room": "dining_room",
    "down stairs": "downstairs", "up stairs": "upstairs",
    "front porch": "front_porch", "back yard": "backyard",
}


def _load_learned_aliases(hass) -> dict:
    try:
        import json as _json
        from .paths import config_path_str
        learn_file = config_path_str(".jarvis_learned.json", hass=hass)
        with open(learn_file) as f:
            learned = _json.load(f)
        aliases = learned.get("alias", {})
        return aliases if isinstance(aliases, dict) else {}
    except Exception:
        return {}


def _find_entity(hass, name_fragment, domain_hint=None, learned_aliases=None):
    """
    Fuzzy-match a name fragment against HA entities. v5.7.08.

    Matching tiers (first match wins):
      1. Learned aliases (from agent's remember tool)
      2. Exact friendly_name match
      3. Substring: fragment appears inside friendly_name
      4. Word overlap: all words in fragment appear in friendly_name
      5. STT correction → retry with corrected form
      6. Fuzzy similarity scoring (handles phonetic/STT errors)
      7. Entity_id substring match
      8. Area-based fallback

    Returns (entity_id, friendly_name) or None.
    """
    fragment = name_fragment.lower().strip()

    # Strip common suffixes
    for suffix in ("light", "lights", "lamp", "lamps", "switch", "switches",
                   "fan", "fans", "lock", "locks", "door", "doors",
                   "cover", "covers", "thermostat", "sensor",
                   "please", "now", "for me", "jarvis"):
        fragment = re.sub(rf"\s+{suffix}$", "", fragment)
    fragment = fragment.strip()
    if not fragment:
        return None

    # ── Tier 0: Check learned aliases ───────────────────────────────
    aliases = learned_aliases or {}
    if fragment in aliases:
        resolved_id = aliases[fragment]
        state = hass.states.get(resolved_id)
        if state:
            _LOGGER.info("Entity resolve: alias '%s' → %s", fragment, resolved_id)
            return (resolved_id, state.attributes.get("friendly_name", resolved_id))

    domains = [domain_hint] if domain_hint else [
        "light", "switch", "lock", "cover", "climate",
        "fan", "media_player", "sensor", "binary_sensor"]

    frag_words = set(fragment.split())
    best_match = None
    best_score = 0

    for domain in domains:
        for state in hass.states.async_all(domain):
            eid = state.entity_id
            fname = (state.attributes.get("friendly_name") or "").lower()
            fname_words = set(fname.split())

            # Tier 1: exact
            if fname == fragment:
                _LOGGER.info("Entity resolve: exact '%s' → %s", fragment, eid)
                return (eid, state.attributes.get("friendly_name", eid))

            # Tier 2: substring
            if fragment in fname:
                score = len(fragment) / max(len(fname), 1) * 100
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 3: word overlap
            if frag_words and frag_words.issubset(fname_words):
                score = len(frag_words) / max(len(fname_words), 1) * 95
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 4: fuzzy similarity
            fuzz = _fuzzy_score(fragment, fname)
            if fuzz > 55 and fuzz > best_score:
                best_score = fuzz
                best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 5: entity_id substring
            frag_u = fragment.replace(" ", "_")
            if frag_u in eid:
                score = len(frag_u) / max(len(eid), 1) * 80
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))

    # ── Tier 5b: STT correction retry ──────────────────────────────
    if (not best_match or best_score < 50):
        corrected = fragment
        for wrong, right in _STT_CORRECTIONS.items():
            if wrong in corrected:
                corrected = corrected.replace(wrong, right)
        if corrected != fragment:
            _LOGGER.info("Entity resolve: STT correction '%s' → '%s'", fragment, corrected)
            # Retry with corrected form (non-recursive, just one pass)
            for domain in domains:
                for state in hass.states.async_all(domain):
                    fname = (state.attributes.get("friendly_name") or "").lower()
                    if corrected in fname:
                        score = len(corrected) / max(len(fname), 1) * 90
                        if score > best_score:
                            best_score = score
                            best_match = (state.entity_id,
                                          state.attributes.get("friendly_name", state.entity_id))
                    fuzz = _fuzzy_score(corrected, fname)
                    if fuzz > 55 and fuzz > best_score:
                        best_score = fuzz
                        best_match = (state.entity_id,
                                      state.attributes.get("friendly_name", state.entity_id))

    # ── Tier 6: area-based fallback ────────────────────────────────
    if not best_match and domain_hint:
        try:
            from homeassistant.helpers import (
                area_registry as areg, entity_registry as er, device_registry as dr)
            area_reg = areg.async_get(hass)
            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            for area in area_reg.async_list_areas():
                if fragment in area.name.lower():
                    for entry in ent_reg.entities.values():
                        if entry.domain != domain_hint:
                            continue
                        in_area = entry.area_id == area.id
                        if not in_area and entry.device_id:
                            device = dev_reg.async_get(entry.device_id)
                            in_area = device and device.area_id == area.id
                        if in_area:
                            state = hass.states.get(entry.entity_id)
                            if state:
                                return (entry.entity_id,
                                        state.attributes.get("friendly_name", entry.entity_id))
        except Exception:
            pass

    if best_match and best_score > 25:
        _LOGGER.info("Entity resolve: '%s' → %s (score=%.0f)", fragment, best_match[0], best_score)
        return best_match

    # A failed resolution is normal, expected control flow: the phrase looked
    # vaguely command-like but matched no device, so the caller falls through
    # to the LLM (which has fuzzy search + aliases). DEBUG, not WARNING — this
    # is not an error condition and should not surface in the user's log.
    _LOGGER.debug("Entity resolve unmatched: '%s' (domain=%s, best=%.0f)", fragment, domain_hint, best_score)
    return None


def _find_entities_in_area(hass, area_name, domain):
    results = []
    try:
        from homeassistant.helpers import (
            area_registry as areg, entity_registry as er, device_registry as dr)
        try:
            from .entity_filter import is_excluded
        except Exception:
            is_excluded = lambda _h, _e: False
        area_reg = areg.async_get(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        target = None
        for area in area_reg.async_list_areas():
            if area_name.lower() in area.name.lower():
                target = area
                break
        if not target:
            return []
        for entry in ent_reg.entities.values():
            if entry.domain != domain:
                continue
            in_area = entry.area_id == target.id
            if not in_area and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                in_area = device and device.area_id == target.id
            if in_area:
                # Excluded entities don't take part in area/group commands
                # ("turn on the living-room lights"); a by-name request still
                # resolves them through a different path.
                if is_excluded(hass, entry.entity_id):
                    continue
                state = hass.states.get(entry.entity_id)
                if state:
                    results.append((entry.entity_id,
                                    state.attributes.get("friendly_name", entry.entity_id)))
    except Exception:
        pass
    return results


# Actions whose captured group is a VALUE (a number) and/or an optional AREA,
# never a device name — so the target entity is resolved from the area (if one
# was captured) or a sole/active default, not by searching for the number/None.
_MEDIA_ACTIONS = frozenset({"media_pause", "media_play", "media_next",
                            "volume_up", "volume_down", "mute", "unmute", "volume_set"})


def _default_target(hass, domain):
    """The unambiguous default entity for a domain when a command named none.

    For media_player: a single currently-active player (playing / paused /
    buffering) wins; otherwise the sole non-satellite media_player. For any other
    domain: the sole entity. Returns None when it is ambiguous (zero, or several
    with no single active one) so the caller falls through rather than guessing
    the wrong device. Never raises.
    """
    try:
        ents = [s.entity_id for s in hass.states.async_all(domain)
                if not s.entity_id.startswith("assist_satellite.")]
        if domain == "media_player":
            active = [e for e in ents
                      if (hass.states.get(e) and
                          hass.states.get(e).state in ("playing", "paused", "buffering"))]
            if len(active) == 1:
                return active[0]
        if len(ents) == 1:
            return ents[0]
    except Exception:   # pragma: no cover - defensive
        pass
    return None


def _find_scene_or_script(hass, name_fragment):
    fragment = name_fragment.lower().strip()
    for suffix in ("scene", "script", "routine", "mode"):
        fragment = re.sub(rf"\s+{suffix}$", "", fragment)
    fragment = fragment.strip()
    for domain, dtype in [("scene", "scene"), ("script", "script")]:
        for state in hass.states.async_all(domain):
            fname = (state.attributes.get("friendly_name") or "").lower()
            if fname == fragment or fragment in fname:
                return (state.entity_id,
                        state.attributes.get("friendly_name", state.entity_id), dtype)
            if fragment.replace(" ", "_") in state.entity_id:
                return (state.entity_id,
                        state.attributes.get("friendly_name", state.entity_id), dtype)
    return None


# ── Action execution ────────────────────────────────────────────────────────

def _service_for(action, entity_id):
    """Map a fast-path action to the (domain, service) it would call, or None if
    it isn't a direct actuation. Mirrors _execute_action so the confirmation
    pre-check matches exactly what would run."""
    dom = entity_id.split(".")[0] if entity_id else ""
    if action in ("turn_on", "turn_off", "toggle"):
        return (dom, action)
    fixed = {
        "lock": ("lock", "lock"), "unlock": ("lock", "unlock"),
        "open": ("cover", "open_cover"), "close": ("cover", "close_cover"),
        "dim": ("light", "turn_on"), "brighten": ("light", "turn_on"),
        "media_pause": ("media_player", "media_pause"),
        "media_play": ("media_player", "media_play"),
        "media_next": ("media_player", "media_next_track"),
        "volume_up": ("media_player", "volume_up"),
        "volume_down": ("media_player", "volume_down"),
        "mute": ("media_player", "volume_mute"),
        "unmute": ("media_player", "volume_mute"),
        "set_temp": ("climate", "set_temperature"),
        "set_temp_named": ("climate", "set_temperature"),
        "volume_set": ("media_player", "volume_set"),
    }
    return fixed.get(action)


def _needs_confirmation(hass, action, entity_id="") -> bool:
    """Whether this fast-path action would require confirmation. Protected
    actions (unlock, open a garage/cover, disarm, …) are deferred to the agent
    rather than actuated here, so the same authorization gate that guards the
    agent's tools also guards the fast-path — the fast-path never becomes a
    second, unguarded way to unlock a door."""
    svc = _service_for(action, entity_id)
    if not svc:
        return False
    try:
        from . import policy
        return bool(policy.requires_confirmation(hass, svc[0], svc[1], entity_id))
    except Exception:
        return False


async def _seam_execute(hass, domain: str, service: str, data: dict, *,
                        blocking: bool = True, action: str = "") -> bool:
    """Route a local-engine actuation through the **universal actuator seam**
    (MCU Phase H) — so the offline fast-path is planned, event-published and
    journaled like every other actuation instead of a bare ``hass.services``
    call. Behaviour-preserving: it performs the same service call (same
    blocking), just through the kernel planner, and returns True on success /
    False on failure exactly as the direct call's try/except did. Never raises."""
    entity_id = str(data.get("entity_id", "")) if isinstance(data, dict) else ""
    capability = f"{domain}.{service}"
    try:
        from . import actuation
        areq = actuation.request(capability, entity_id, params=data,
                                 action=(action or service))
        ok, _detail = await actuation.execute_actuator(
            hass, capability=capability, entity_id=entity_id, domain=domain,
            service=service, data=dict(data), action=(action or service),
            areq=areq, verify=None, blocking=blocking)
        return bool(ok)
    except Exception as exc:   # pragma: no cover - defensive
        _LOGGER.warning("Local seam execute failed for %s.%s (%s): %s",
                        domain, service, entity_id, exc)
        return False


async def _execute_action(hass, action, entity_id, args):
    try:
        domain = entity_id.split(".")[0]
        svc_map = {
            "turn_on": (domain, "turn_on"), "turn_off": (domain, "turn_off"),
            "toggle": (domain, "toggle"), "lock": ("lock", "lock"),
            "unlock": ("lock", "unlock"), "open": ("cover", "open_cover"),
            "close": ("cover", "close_cover"), "dim": ("light", "turn_on"),
            "brighten": ("light", "turn_on"),
            "media_pause": ("media_player", "media_pause"),
            "media_play": ("media_player", "media_play"),
            "media_next": ("media_player", "media_next_track"),
            "volume_up": ("media_player", "volume_up"),
            "volume_down": ("media_player", "volume_down"),
            "mute": ("media_player", "volume_mute"),
            "unmute": ("media_player", "volume_mute"),
        }

        if action in svc_map:
            svc_domain, svc_name = svc_map[action]
            svc_data = {"entity_id": entity_id}
            if action == "dim" and "brightness_pct" in args:
                svc_data["brightness_pct"] = args["brightness_pct"]
            elif action == "brighten":
                svc_data["brightness_pct"] = 100
            elif action == "mute":
                svc_data["is_volume_muted"] = True
            elif action == "unmute":
                svc_data["is_volume_muted"] = False
            return await _seam_execute(hass, svc_domain, svc_name, svc_data,
                                       blocking=True, action=action)
        elif action in ("set_temp", "set_temp_named"):
            temp = args.get("temperature")
            if temp is not None:   # a 0° setpoint is valid — don't drop it on falsy 0
                return await _seam_execute(
                    hass, "climate", "set_temperature",
                    {"entity_id": entity_id, "temperature": float(temp)},
                    blocking=True, action="set_temperature")
        elif action == "volume_set":
            level = args.get("volume_level", 50)
            return await _seam_execute(
                hass, "media_player", "volume_set",
                {"entity_id": entity_id, "volume_level": level / 100.0},
                blocking=True, action="volume_set")
        return False
    except Exception as exc:
        _LOGGER.warning("Local action failed for %s: %s", entity_id, exc)
        return False


# ── Response generation ──────────────────────────────────────────────────────

def _resp(action, fname, success, args=None, h="sir", lang="en"):
    from . import local_i18n
    if not success:
        # JARVIS reports failure calmly and precisely, no hand-wringing.
        return local_i18n.t("resp_fail", lang,
                            action=action.replace('_', ' '), name=fname, h=h)

    # Understated lead-ins, MCU style. Varied so confirmations never sound
    # canned. Each is something JARVIS would actually say.
    ack = _pick(local_i18n.leadins("ack", lang))
    sec = _pick(local_i18n.leadins("sec", lang))

    bp = (args or {}).get('brightness_pct', '?')
    temp = (args or {}).get('temperature', '?')
    vol = (args or {}).get('volume_level', '?')

    _KNOWN = {
        "turn_on", "turn_off", "toggle", "lock", "unlock", "open", "close",
        "dim", "brighten", "set_temp", "set_temp_named", "media_pause",
        "media_play", "media_next", "volume_up", "volume_down", "volume_set",
        "mute", "unmute",
    }
    key = action if action in _KNOWN else "resp_fallback"
    return local_i18n.t(key, lang, ack=ack, sec=sec, h=h, name=fname,
                        bp=bp, temp=temp, vol=vol, action=action)


def _query_resp(hass, action, entity_id, fname, h="sir", lang="en"):
    from . import local_i18n
    if action == "query_time":
        from homeassistant.util import dt as dt_util
        return local_i18n.t("query_time", lang,
                            time=local_i18n.format_time(dt_util.now(), lang), h=h)
    if action == "query_date":
        from homeassistant.util import dt as dt_util
        return local_i18n.t("query_date", lang,
                            date=local_i18n.format_date(dt_util.now(), lang), h=h)
    if action == "greeting":
        from homeassistant.util import dt as dt_util
        g = local_i18n.greet_word(dt_util.now().hour, lang)
        return _pick([local_i18n.t("greet_plain", lang, g=g, h=h),
                      local_i18n.t("greet_help", lang, g=g, h=h),
                      local_i18n.t("greet_disposal", lang, g=g, h=h)])
    if action == "thanks":
        return _pick([local_i18n.t("thanks_1", lang, h=h),
                      local_i18n.t("thanks_2", lang, h=h),
                      local_i18n.t("thanks_3", lang, h=h)])
    if action == "query_temp":
        state = hass.states.get(entity_id)
        if state:
            unit = state.attributes.get("unit_of_measurement", "°")
            return local_i18n.t("query_temp", lang, name=fname,
                                state=state.state, unit=unit, h=h)
        return local_i18n.t("query_temp_fail", lang, name=fname, h=h)
    if action == "query_state":
        state = hass.states.get(entity_id)
        if state:
            return local_i18n.t("query_state", lang, name=fname,
                                state=local_i18n.state_word(state.state, lang), h=h)
        return local_i18n.t("query_state_fail", lang, name=fname, h=h)
    if action == "status":
        return _home_status(hass, h, lang)
    return ""


def _home_status(hass, h="sir", lang="en"):
    from . import local_i18n
    lights_on = sum(1 for s in hass.states.async_all("light") if s.state == "on")
    locks_ul = sum(1 for s in hass.states.async_all("lock") if s.state == "unlocked")
    doors_open = sum(1 for s in hass.states.async_all("binary_sensor")
                     if s.attributes.get("device_class") == "door" and s.state == "on")
    people = sum(1 for s in hass.states.async_all("person") if s.state == "home")
    parts = [local_i18n.t("status_header", lang, h=h)]
    parts.append(local_i18n.t("status_lights", lang, n=lights_on,
                              noun=local_i18n.noun("light", lights_on, lang)))
    parts.append(local_i18n.t("status_locks_ok", lang) if not locks_ul else
                 local_i18n.t("status_locks_unlocked", lang, n=locks_ul,
                              noun=local_i18n.noun("lock", locks_ul, lang)))
    if doors_open:
        parts.append(local_i18n.t("status_doors", lang, n=doors_open,
                                  noun=local_i18n.noun("door", doors_open, lang)))
    parts.append(local_i18n.t("status_people", lang, n=people,
                              noun=local_i18n.noun("person", people, lang)))
    return " ".join(parts)


# ── Contextual queries ──────────────────────────────────────────────────────

def _ctx_query(hass, qtype, h="sir", area_match="", lang="en"):
    from . import local_i18n
    if qtype == "who_home":
        ppl = [s.attributes.get("friendly_name", s.entity_id)
               for s in hass.states.async_all("person") if s.state == "home"]
        if not ppl:
            return local_i18n.t("who_none", lang, h=h)
        if len(ppl) == 1:
            return local_i18n.t("who_one", lang, name=ppl[0], h=h)
        return local_i18n.t("who_many", lang,
                            names=", ".join(ppl[:-1]), last=ppl[-1], h=h)

    if qtype == "what_open":
        items = []
        for s in hass.states.async_all("binary_sensor"):
            dc = s.attributes.get("device_class", "")
            if dc in ("door", "window", "garage_door") and s.state == "on":
                items.append(s.attributes.get("friendly_name", s.entity_id))
        for s in hass.states.async_all("cover"):
            if s.state == "open":
                items.append(s.attributes.get("friendly_name", s.entity_id))
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                items.append(s.attributes.get("friendly_name", s.entity_id)
                             + local_i18n.t("open_unlocked_suffix", lang))
        if not items:
            return local_i18n.t("open_none", lang, h=h)
        return local_i18n.t("open_list", lang, items=", ".join(items))

    if qtype == "lights_on":
        on = [s.attributes.get("friendly_name", s.entity_id)
              for s in hass.states.async_all("light") if s.state == "on"]
        if not on:
            return local_i18n.t("lights_none", lang, h=h)
        c = len(on)
        listing = ", ".join(on[:5])
        return local_i18n.t("lights_list", lang, c=c,
                            noun=local_i18n.noun("light", c, lang),
                            listing=listing, ellipsis=("..." if c > 5 else ""))

    if qtype == "energy":
        for s in hass.states.async_all("sensor"):
            if s.attributes.get("device_class") == "power" and "total" in s.entity_id.lower():
                unit = s.attributes.get("unit_of_measurement", "W")
                return local_i18n.t("energy", lang, state=s.state, unit=unit, h=h)
        return local_i18n.t("energy_none", lang, h=h)

    if qtype == "weather":
        for s in hass.states.async_all("weather"):
            temp = s.attributes.get("temperature", "—")
            humidity = s.attributes.get("humidity", "—")
            condition = s.state.replace("_", " ")
            return local_i18n.t("weather", lang, condition=condition,
                                temp=temp, humidity=humidity, h=h)
        for s in hass.states.async_all("sensor"):
            if ("outdoor" in s.entity_id.lower() or "outside" in s.entity_id.lower()):
                if s.attributes.get("device_class") == "temperature":
                    unit = s.attributes.get("unit_of_measurement", "°")
                    return local_i18n.t("weather_outdoor", lang,
                                        state=s.state, unit=unit, h=h)
        return local_i18n.t("weather_none", lang, h=h)

    if qtype == "area_devices" and area_match:
        try:
            from homeassistant.helpers import (
                area_registry as areg, entity_registry as er, device_registry as dr)
            area_reg = areg.async_get(hass)
            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            target = None
            for area in area_reg.async_list_areas():
                if area_match.lower() in area.name.lower():
                    target = area
                    break
            if not target:
                return local_i18n.t("area_none", lang, area=area_match, h=h)
            devs = []
            for entry in ent_reg.entities.values():
                in_area = entry.area_id == target.id
                if not in_area and entry.device_id:
                    device = dev_reg.async_get(entry.device_id)
                    in_area = device and device.area_id == target.id
                if in_area:
                    state = hass.states.get(entry.entity_id)
                    if state:
                        devs.append(f"{state.attributes.get('friendly_name', entry.entity_id)} "
                                    f"({local_i18n.state_word(state.state, lang)})")
            if not devs:
                return local_i18n.t("area_empty", lang, name=target.name, h=h)
            listing = ", ".join(devs[:10])
            more = (local_i18n.t("area_more", lang, n=len(devs) - 10)
                    if len(devs) > 10 else "")
            return local_i18n.t("area_list", lang, name=target.name, n=len(devs),
                                noun=local_i18n.noun("device", len(devs), lang),
                                listing=listing, more=more)
        except Exception:
            pass
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

def _output_lang(hass):
    """The household output language as a primary subtag ('de'), 'en' default.
    Steers the zero-API local replies the same way the LLM path is steered by
    language.language_directive. Never raises."""
    try:
        from . import language
        return language.configured_language(hass)
    except Exception:
        return "en"


async def try_local(hass, text, honorific="sir", force=False, lang=None):
    """
    PRIMARY handler. Returns LocalResult if handled, None for LLM fallback.

    When force=True (offline salvage mode), the complexity gate is bypassed:
    JARVIS attempts to extract and execute any actionable device command or
    answerable query from the text even if it looks complex, because escalating
    to the cloud LLM is not an option. Conversational/creative requests that
    have no local handler still return None — the caller supplies an honest
    offline response.

    ``lang`` overrides the resolved household output language (#511): replies are
    rendered in it via local_i18n with zero API cost. None → resolve from config.
    """
    from . import local_i18n
    if lang is None:
        lang = _output_lang(hass)
    normalized = _normalize(text)
    complexity = score_complexity(text)

    # High complexity → immediate LLM (skipped in force/offline mode)
    if complexity >= 70 and not force:
        _LOGGER.debug("Local: complexity %d for '%s' — LLM", complexity, text[:60])
        return None

    # Contextual queries
    for pattern, qtype in _QUERY_PATTERNS:
        match = re.search(pattern, normalized)
        if match:
            area_m = match.group(1) if match.lastindex else ""
            resp = _ctx_query(hass, qtype, honorific, area_m, lang)
            if resp:
                _LOGGER.info("Local contextual: %s", qtype)
                return LocalResult(text=resp, success=True)

    # Bulk/multi-entity
    for pattern, action, domain, scope in _BULK_PATTERNS:
        match = re.search(pattern, normalized)
        if not match:
            continue
        area_name = match.group(1) if scope == "area" and match.lastindex else None
        if _needs_confirmation(hass, action, ""):
            _LOGGER.info("Local: bulk '%s' needs confirmation — deferring to agent", action)
            return None   # protected bulk → agent skips/confirms per device
        if domain == "all":
            total = 0
            for d in ("light", "switch", "fan"):
                ents = [(s.entity_id, s.attributes.get("friendly_name", s.entity_id))
                        for s in hass.states.async_all(d) if s.state == "on"]
                for eid, fn in ents:
                    if await _execute_action(hass, "turn_off", eid, {}):
                        total += 1
            return LocalResult(text=local_i18n.t(
                "bulk_alloff", lang, h=honorific, total=total,
                noun=local_i18n.noun("device", total, lang)), success=True)
        entities = _find_entities_in_area(hass, area_name, domain) if area_name else [
            (s.entity_id, s.attributes.get("friendly_name", s.entity_id))
            for s in hass.states.async_all(domain)]
        if not entities:
            continue
        ok = 0
        for eid, fn in entities:
            if await _execute_action(hass, action, eid, {}):
                ok += 1
        area_str = local_i18n.t("area_in", lang, area=area_name) if area_name else ""
        return LocalResult(text=local_i18n.t(
            "bulk_generic", lang, h=honorific, ok=ok,
            noun=local_i18n.noun(domain, ok, lang), area=area_str,
            verb=local_i18n.bulk_verb(action, lang)), success=ok > 0)

    # Scene/script (check before single-entity to catch "activate X")
    for pattern, action, _ in _INTENT_PATTERNS:
        if action != "scene":
            continue
        match = re.search(pattern, normalized)
        if not match:
            continue
        name_frag = match.group(1) if match.lastindex else ""
        if not name_frag:
            continue
        found = _find_scene_or_script(hass, name_frag)
        if found:
            eid, fname, dtype = found
            try:
                ok = await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                         blocking=True, action="activate")
                if not ok:
                    raise RuntimeError("activation did not complete")
                _update_ctx(entity=eid, domain=dtype)
                return LocalResult(text=local_i18n.t("scene_activating", lang,
                                                     name=fname, h=honorific), success=True)
            except Exception as exc:
                return LocalResult(text=local_i18n.t("scene_fail", lang, name=fname,
                                                     h=honorific, exc=exc), success=False)

    # Goodnight shortcut
    if re.search(r"^good\s*night(?:\s+jarvis)?$", normalized):
        found = _find_scene_or_script(hass, "goodnight") or _find_scene_or_script(hass, "good night")
        if found:
            eid, fname, dtype = found
            try:
                if not await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                           blocking=True, action="activate"):
                    raise RuntimeError("activation did not complete")
                return LocalResult(text=local_i18n.t("goodnight_scene", lang,
                                                     name=fname, h=honorific), success=True)
            except Exception as exc:
                _LOGGER.warning("JARVIS: goodnight scene/script '%s' failed to run: %s", eid, exc)
        off_count = 0
        for s in hass.states.async_all("light"):
            if s.state == "on":
                if await _seam_execute(hass, "light", "turn_off",
                                       {"entity_id": s.entity_id},
                                       blocking=False, action="turn_off"):
                    off_count += 1
                else:
                    _LOGGER.debug("JARVIS: goodnight could not turn off %s", s.entity_id)
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                if not await _seam_execute(hass, "lock", "lock",
                                           {"entity_id": s.entity_id},
                                           blocking=False, action="lock"):
                    _LOGGER.warning("JARVIS: goodnight could not lock %s", s.entity_id)
        return LocalResult(text=local_i18n.t(
            "goodnight_summary", lang, h=honorific, n=off_count,
            noun=local_i18n.noun("light", off_count, lang)), success=True)

    # Single-entity patterns
    _last_failed_name = None  # Track for end-of-loop error
    learned_aliases = await hass.async_add_executor_job(
        _load_learned_aliases, hass
    )
    for pattern, action, domain_hint in _INTENT_PATTERNS:
        if action == "scene":
            continue
        match = re.search(pattern, normalized)
        if not match:
            continue
        _LOGGER.info("Local intent: action=%s", action)
        if action in ("query_time", "query_date", "greeting", "thanks", "status"):
            return LocalResult(text=_query_resp(hass, action, "", "", honorific, lang), success=True)
        groups = match.groups()
        # Value/area actions: the capture is a number and/or an optional area, not
        # an entity name. Resolve the target from the captured area (if any) or a
        # sole/active default, so "pause the music", "set volume to 50" and "set
        # the temperature to 72" work without a named device instead of searching
        # for the literal number / skipping on a null name.
        if action in _MEDIA_ACTIONS or action == "set_temp":
            if action == "set_temp":
                num, area = (groups[0] if groups else None), None
            elif action == "volume_set":
                num = groups[0] if groups else None
                area = groups[1] if len(groups) > 1 else None
            else:   # media_pause/play/next, volume_up/down, mute/unmute — area only
                num, area = None, (groups[0] if groups else None)
            tgt_domain = "climate" if action == "set_temp" else "media_player"
            target = None
            if area:
                found = _find_entities_in_area(hass, area, tgt_domain)
                target = found[0][0] if found else None
            if not target:
                target = _default_target(hass, tgt_domain)
            if not target:
                # ambiguous (no area match, no sole/active default) → fall through
                # to the LLM online; honest failure named below in offline mode.
                _last_failed_name = area or f"a {tgt_domain.replace('_', ' ')}"
                continue
            vargs = {}
            if num is not None:
                try:
                    vargs["temperature" if action == "set_temp" else "volume_level"] = int(num)
                except (ValueError, TypeError):
                    continue
            if _needs_confirmation(hass, action, target):
                return None
            success = await _execute_action(hass, action, target, vargs)
            st = hass.states.get(target)
            fname = st.attributes.get("friendly_name", target) if st else target
            _update_ctx(entity=target, domain=target.split(".")[0], action=action)
            return LocalResult(text=_resp(action, fname, success, vargs, honorific, lang),
                               success=success)
        name_frag = groups[0] if groups else ""
        extra_arg = groups[1] if len(groups) > 1 else None
        if not name_frag:
            continue
        resolved = _find_entity(
            hass, name_frag, domain_hint, learned_aliases
        ) or _find_entity(hass, name_frag, None, learned_aliases)
        if not resolved:
            # Track failure but keep trying other patterns/domains
            _last_failed_name = name_frag
            _LOGGER.info(
                "Local: pattern matched '%s' but entity '%s' not found "
                "(domain=%s), trying next pattern...",
                action, name_frag, domain_hint,
            )
            continue
        entity_id, fname = resolved
        args = {}
        if action == "dim" and extra_arg:
            args["brightness_pct"] = int(extra_arg)
        elif action in ("set_temp", "set_temp_named") and extra_arg:
            args["temperature"] = int(extra_arg)
        elif action in ("set_temp", "set_temp_named") and groups:
            try: args["temperature"] = int(groups[0])
            except (ValueError, IndexError): pass
        elif action == "volume_set" and groups:
            try: args["volume_level"] = int(groups[0])
            except (ValueError, IndexError): pass
        if action.startswith("query_"):
            resp = _query_resp(hass, action, entity_id, fname, honorific, lang)
            if resp:
                return LocalResult(text=resp, success=True)
            continue
        if _needs_confirmation(hass, action, entity_id):
            _LOGGER.info("Local: '%s' on %s needs confirmation — deferring to agent",
                         action, entity_id)
            return None   # protected action → the agent runs the confirmation gate
        success = await _execute_action(hass, action, entity_id, args)
        _update_ctx(entity=entity_id, domain=entity_id.split(".")[0], action=action)
        return LocalResult(text=_resp(action, fname, success, args, honorific, lang), success=success)

    # v5.7.08: If patterns matched but entity resolution failed, ALWAYS
    # fall through to the agentic LLM. The agent has search_entities which
    # uses fuzzy matching and learned aliases — much better at finding
    # devices than the local regex resolver. Never return a local error
    # for device commands.
    # v5.9.06: In force/offline mode there's no LLM to escalate to, so return
    # an honest local error naming the device we couldn't resolve.
    if _last_failed_name:
        if force:
            return LocalResult(
                text=local_i18n.t("offline_notfound", lang,
                                  name=_last_failed_name, h=honorific),
                success=False,
            )
        _LOGGER.info(
            "Local: entity '%s' not found — escalating to agentic LLM "
            "with search_entities",
            _last_failed_name,
        )
        return None  # Fall through to agent

    # ── Appliance monitor queries ───────────────────────────────────
    appliance_match = re.search(
        r"(?:let\s+me\s+know|tell\s+me|notify\s+me|alert\s+me)"
        r".*(?:when|if).*(?:laundry|wash|dryer|dry|dishwasher|dishes)"
        r".*(?:done|finish|complete|ready)",
        normalized,
    )
    if appliance_match:
        try:
            from . import appliance_monitor
            if appliance_monitor.is_running():
                st = appliance_monitor.status()
                tracking = [
                    f"{s['friendly_name']} ({s['phase']})"
                    for s in st.get("sensors", {}).values()
                ]
                if tracking:
                    return LocalResult(
                        text=local_i18n.t("appliance_tracking", lang,
                                          h=honorific, items=", ".join(tracking)),
                        success=True,
                    )
                return LocalResult(
                    text=local_i18n.t("appliance_nosensors", lang, h=honorific),
                    success=True,
                )
            else:
                return LocalResult(
                    text=local_i18n.t("appliance_notrunning", lang, h=honorific),
                    success=True,
                )
        except Exception:
            return LocalResult(
                text=local_i18n.t("appliance_generic", lang, h=honorific),
                success=True,
            )

    # Follow-up ("also turn off the kitchen")
    if _ctx_fresh() and re.search(r"^(?:also|and|now)\s+", normalized):
        stripped = re.sub(r"^(?:also|and|now)\s+", "", normalized)
        result = await try_local(hass, stripped, honorific, force=force, lang=lang)
        if result and result.handled:
            return result

    # Low complexity bare entity name → state query.
    # Guard: only attempt this for SHORT, non-interrogative phrases. A bare
    # entity name is something like "kitchen lights" or "front door" — not a
    # question or a sentence about JARVIS itself. Without this guard, phrases
    # like "what are your capabilities" get fed wholesale to the resolver,
    # which wastes a full registry scan and (previously) logged noise.
    if complexity < 40 and _looks_like_entity_name(normalized):
        resolved = _find_entity(hass, normalized, None, learned_aliases)
        if resolved:
            entity_id, fname = resolved
            state = hass.states.get(entity_id)
            if state:
                return LocalResult(text=local_i18n.t(
                    "query_state", lang, name=fname,
                    state=local_i18n.state_word(state.state, lang),
                    h=honorific), success=True)

    _LOGGER.debug("Local: no match for '%s' (complexity=%d) — LLM", text[:60], complexity)
    return None


def _looks_like_entity_name(text: str) -> bool:
    """
    Heuristic: is this short phrase plausibly a bare device/entity name
    (vs. a question or a sentence)? Used to gate the speculative state-query
    lookup so conversational input never hits the entity resolver.
    """
    t = text.strip().lower()
    if not t:
        return False
    # Questions are never bare entity names.
    if t.endswith("?"):
        return False
    words = t.split()
    # Too long to be a device name.
    if len(words) > 4:
        return False
    # Interrogatives / conversational openers.
    _QUESTION_STARTS = (
        "what", "who", "when", "where", "why", "how", "is", "are", "can",
        "could", "would", "should", "do", "does", "did", "will", "tell",
        "explain", "describe", "give", "show me how", "help",
    )
    if words[0] in _QUESTION_STARTS:
        return False
    # A leading action verb (any supported language) makes this a COMMAND, not a
    # bare device name — so an unrecognised command (e.g. a German verb this
    # engine doesn't pattern-match) escalates to the LLM instead of being misread
    # as a state query on the device embedded in it (issue #511).
    _COMMAND_VERBS = (
        # German imperatives
        "schalte", "schalt", "mach", "mache", "dimme", "dimm",
        "öffne", "offne", "schließe", "schliesse", "schliess",
        "verriegele", "verriegle", "entriegele", "entriegle",
        "starte", "aktiviere", "spiele", "pausiere",
        # English (these normally match a command pattern already; defensive)
        "turn", "switch", "dim", "brighten", "open", "close", "lock",
        "unlock", "set", "play", "pause", "activate", "run", "mute", "unmute",
    )
    if words[0] in _COMMAND_VERBS:
        return False
    # Phrases about JARVIS itself ("your X", "you Y") aren't device names.
    if "your" in words or "you" in words:
        return False
    return True
