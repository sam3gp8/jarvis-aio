"""
JARVIS Local Intent Engine (v5.7.00).

PRIMARY handler for all conversations and event reasoning. Zero API calls.
Handles 95%+ of user requests locally with pattern matching, HA entity
resolution, contextual state queries, multi-entity commands, scene/script
activation, media control, and conversational responses.

Only genuinely complex or ambiguous requests fall through to Groq/Gemini.

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
    # Locks
    (r"lock\s+(?:the\s+)?(.+?)(?:\s+door)?$",   "lock",   "lock"),
    (r"unlock\s+(?:the\s+)?(.+?)(?:\s+door)?$", "unlock", "lock"),
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
    (r"lock\s+(?:all\s+)?(?:the\s+)?doors?$",   "lock",   "lock", None),
    (r"unlock\s+(?:all\s+)?(?:the\s+)?doors?$", "unlock", "lock", None),
    (r"close\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$", "close", "cover", None),
    (r"open\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$",  "open",  "cover", None),
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?fans?$", "turn_off", "fan", None),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?fans?$",  "turn_on",  "fan", None),
    (r"turn\s+(?:off|out)\s+everything$", "turn_off", "all", None),
]

# ── Contextual queries ──────────────────────────────────────────────────────

_QUERY_PATTERNS = [
    (r"(?:who(?:'s| is)\s+)?home\b",                     "who_home"),
    (r"(?:is\s+)?(?:anyone|anybody)\s+home",              "who_home"),
    (r"(?:what(?:'s| is)\s+)?(?:open|unlocked)",          "what_open"),
    (r"(?:are\s+)?(?:any|which)\s+(?:doors?|windows?)\s+open", "what_open"),
    (r"(?:are\s+)?(?:any|which)\s+(?:lights?)\s+on",     "lights_on"),
    (r"(?:how\s+many)\s+lights?\s+(?:are\s+)?on",        "lights_on"),
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)?(?:energy|power)\s+(?:usage|consumption)", "energy"),
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)?weather",        "weather"),
    (r"(?:what(?:'s| is)\s+(?:it\s+)?)?(?:like\s+)?outside", "weather"),
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


def _find_entity(hass, name_fragment, domain_hint=None):
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
    try:
        import json as _json, os as _os
        learn_file = "/config/.jarvis_learned.json"
        if _os.path.exists(learn_file):
            with open(learn_file) as f:
                learned = _json.load(f)
            aliases = learned.get("alias", {})
            if fragment in aliases:
                resolved_id = aliases[fragment]
                state = hass.states.get(resolved_id)
                if state:
                    _LOGGER.info("Entity resolve: alias '%s' → %s", fragment, resolved_id)
                    return (resolved_id, state.attributes.get("friendly_name", resolved_id))
    except Exception:
        pass

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
                state = hass.states.get(entry.entity_id)
                if state:
                    results.append((entry.entity_id,
                                    state.attributes.get("friendly_name", entry.entity_id)))
    except Exception:
        pass
    return results


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
            await hass.services.async_call(svc_domain, svc_name, svc_data, blocking=True)
            return True
        elif action in ("set_temp", "set_temp_named"):
            temp = args.get("temperature")
            if temp:
                await hass.services.async_call(
                    "climate", "set_temperature",
                    {"entity_id": entity_id, "temperature": float(temp)}, blocking=True)
                return True
        elif action == "volume_set":
            level = args.get("volume_level", 50)
            await hass.services.async_call(
                "media_player", "volume_set",
                {"entity_id": entity_id, "volume_level": level / 100.0}, blocking=True)
            return True
        return False
    except Exception as exc:
        _LOGGER.warning("Local action failed for %s: %s", entity_id, exc)
        return False


# ── Response generation ──────────────────────────────────────────────────────

def _resp(action, fname, success, args=None, h="sir"):
    if not success:
        # JARVIS reports failure calmly and precisely, no hand-wringing.
        return (f"I wasn't able to {action.replace('_', ' ')} {fname}, {h} — "
                f"there may be a connectivity issue.")

    # Understated lead-ins, MCU style. Varied so confirmations never sound
    # canned. Each is something JARVIS would actually say.
    ack = _pick(["Done", "Right away", "As you wish", "Consider it done", "At once"])
    sec = _pick(["Secured", "Done", "Locked up"])

    bp = (args or {}).get('brightness_pct', '?')
    temp = (args or {}).get('temperature', '?')
    vol = (args or {}).get('volume_level', '?')

    r = {
        "turn_on":        f"{ack}, {h}. {fname} is on.",
        "turn_off":       f"{ack}, {h}. {fname} is off.",
        "toggle":         f"{ack}, {h}. {fname} toggled.",
        "lock":           f"{sec}, {h}. {fname} is locked.",
        "unlock":         f"{ack}, {h}. {fname} is unlocked.",
        "open":           f"Opening {fname} now, {h}.",
        "close":          f"Closing {fname} now, {h}.",
        "dim":            f"{ack}, {h}. {fname} at {bp}%.",
        "brighten":       f"{ack}, {h}. {fname} at full brightness.",
        "set_temp":       f"{ack}, {h}. Temperature set to {temp}°.",
        "set_temp_named": f"{ack}, {h}. {fname} set to {temp}°.",
        "media_pause":    f"Paused, {h}.",
        "media_play":     f"Playing now, {h}.",
        "media_next":     f"Next track, {h}.",
        "volume_up":      f"Volume up, {h}.",
        "volume_down":    f"Volume down, {h}.",
        "volume_set":     f"Volume at {vol}%, {h}.",
        "mute":           f"Muted, {h}.",
        "unmute":         f"Unmuted, {h}.",
    }
    return r.get(action, f"{ack}, {h}. {action} applied to {fname}.")


def _query_resp(hass, action, entity_id, fname, h="sir"):
    if action == "query_time":
        from homeassistant.util import dt as dt_util
        return f"It's currently {dt_util.now().strftime('%I:%M %p')}, {h}."
    if action == "query_date":
        from homeassistant.util import dt as dt_util
        return f"Today is {dt_util.now().strftime('%A, %B %d, %Y')}, {h}."
    if action == "greeting":
        from homeassistant.util import dt as dt_util
        hour = dt_util.now().hour
        g = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        tail = _pick([f"{g}, {h}.",
                      f"{g}, {h}. What can I do for you?",
                      f"{g}, {h}. At your disposal."])
        return tail
    if action == "thanks":
        return _pick([f"Of course, {h}.",
                      f"My pleasure, {h}.",
                      f"Anytime, {h}."])
    if action == "query_temp":
        state = hass.states.get(entity_id)
        if state:
            unit = state.attributes.get("unit_of_measurement", "°")
            return f"The temperature in {fname} is currently {state.state}{unit}, {h}."
        return f"I'm unable to read the temperature for {fname} at the moment, {h}."
    if action == "query_state":
        state = hass.states.get(entity_id)
        if state:
            return f"{fname} is currently {state.state}, {h}."
        return f"I'm unable to determine the status of {fname} at the moment, {h}."
    if action == "status":
        return _home_status(hass, h)
    return ""


def _home_status(hass, h="sir"):
    lights_on = sum(1 for s in hass.states.async_all("light") if s.state == "on")
    locks_ul = sum(1 for s in hass.states.async_all("lock") if s.state == "unlocked")
    doors_open = sum(1 for s in hass.states.async_all("binary_sensor")
                     if s.attributes.get("device_class") == "door" and s.state == "on")
    people = sum(1 for s in hass.states.async_all("person") if s.state == "home")
    parts = [f"All systems nominal, {h}."]
    parts.append(f"{lights_on} light{'s' if lights_on != 1 else ''} on.")
    parts.append("All locks secured." if not locks_ul else
                 f"{locks_ul} lock{'s' if locks_ul != 1 else ''} unlocked.")
    if doors_open:
        parts.append(f"{doors_open} door{'s' if doors_open != 1 else ''} open.")
    parts.append(f"{people} person{'s' if people != 1 else ''} home.")
    return " ".join(parts)


# ── Contextual queries ──────────────────────────────────────────────────────

def _ctx_query(hass, qtype, h="sir", area_match=""):
    if qtype == "who_home":
        ppl = [s.attributes.get("friendly_name", s.entity_id)
               for s in hass.states.async_all("person") if s.state == "home"]
        if not ppl:
            return f"No one appears to be home at the moment, {h}."
        if len(ppl) == 1:
            return f"{ppl[0]} is currently home, {h}."
        return f"{', '.join(ppl[:-1])} and {ppl[-1]} are currently home, {h}."

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
                items.append(s.attributes.get("friendly_name", s.entity_id) + " (unlocked)")
        if not items:
            return f"Everything is closed and secured, {h}."
        return f"Currently open or unlocked: {', '.join(items)}."

    if qtype == "lights_on":
        on = [s.attributes.get("friendly_name", s.entity_id)
              for s in hass.states.async_all("light") if s.state == "on"]
        if not on:
            return f"All lights are off, {h}."
        c = len(on)
        listing = ", ".join(on[:5])
        return f"{c} light{'s' if c != 1 else ''} on: {listing}{'...' if c > 5 else ''}."

    if qtype == "energy":
        for s in hass.states.async_all("sensor"):
            if s.attributes.get("device_class") == "power" and "total" in s.entity_id.lower():
                unit = s.attributes.get("unit_of_measurement", "W")
                return f"Current power consumption is {s.state} {unit}, {h}."
        return f"I don't have a total power sensor configured, {h}."

    if qtype == "weather":
        for s in hass.states.async_all("weather"):
            temp = s.attributes.get("temperature", "—")
            humidity = s.attributes.get("humidity", "—")
            condition = s.state.replace("_", " ")
            return f"Currently {condition} outside, {h}. Temperature is {temp}° with {humidity}% humidity."
        for s in hass.states.async_all("sensor"):
            if ("outdoor" in s.entity_id.lower() or "outside" in s.entity_id.lower()):
                if s.attributes.get("device_class") == "temperature":
                    unit = s.attributes.get("unit_of_measurement", "°")
                    return f"The outdoor temperature is {s.state}{unit}, {h}."
        return f"I don't have weather data available at the moment, {h}."

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
                return f"I couldn't find an area matching '{area_match}', {h}."
            devs = []
            for entry in ent_reg.entities.values():
                in_area = entry.area_id == target.id
                if not in_area and entry.device_id:
                    device = dev_reg.async_get(entry.device_id)
                    in_area = device and device.area_id == target.id
                if in_area:
                    state = hass.states.get(entry.entity_id)
                    if state:
                        devs.append(f"{state.attributes.get('friendly_name', entry.entity_id)} ({state.state})")
            if not devs:
                return f"No devices found in {target.name}, {h}."
            listing = ", ".join(devs[:10])
            more = f" and {len(devs) - 10} more" if len(devs) > 10 else ""
            return f"{target.name} has {len(devs)} device{'s' if len(devs) != 1 else ''}: {listing}{more}."
        except Exception:
            pass
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

async def try_local(hass, text, honorific="sir", force=False):
    """
    PRIMARY handler. Returns LocalResult if handled, None for LLM fallback.

    When force=True (offline salvage mode), the complexity gate is bypassed:
    JARVIS attempts to extract and execute any actionable device command or
    answerable query from the text even if it looks complex, because escalating
    to the cloud LLM is not an option. Conversational/creative requests that
    have no local handler still return None — the caller supplies an honest
    offline response.
    """
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
            resp = _ctx_query(hass, qtype, honorific, area_m)
            if resp:
                _LOGGER.info("Local contextual: %s", qtype)
                return LocalResult(text=resp, success=True)

    # Bulk/multi-entity
    for pattern, action, domain, scope in _BULK_PATTERNS:
        match = re.search(pattern, normalized)
        if not match:
            continue
        area_name = match.group(1) if scope == "area" and match.lastindex else None
        if domain == "all":
            total = 0
            for d in ("light", "switch", "fan"):
                ents = [(s.entity_id, s.attributes.get("friendly_name", s.entity_id))
                        for s in hass.states.async_all(d) if s.state == "on"]
                for eid, fn in ents:
                    if await _execute_action(hass, "turn_off", eid, {}):
                        total += 1
            return LocalResult(text=f"Done, {honorific}. {total} device{'s' if total != 1 else ''} turned off.", success=True)
        entities = _find_entities_in_area(hass, area_name, domain) if area_name else [
            (s.entity_id, s.attributes.get("friendly_name", s.entity_id))
            for s in hass.states.async_all(domain)]
        if not entities:
            continue
        ok = 0
        for eid, fn in entities:
            if await _execute_action(hass, action, eid, {}):
                ok += 1
        area_str = f" in {area_name}" if area_name else ""
        verb = action.replace("_", " ")
        return LocalResult(text=f"Done, {honorific}. {ok} {domain}{'s' if ok != 1 else ''}{area_str} {verb}.", success=ok > 0)

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
                await hass.services.async_call(dtype, "turn_on", {"entity_id": eid}, blocking=True)
                _update_ctx(entity=eid, domain=dtype)
                return LocalResult(text=f"Activating {fname} now, {honorific}.", success=True)
            except Exception as exc:
                return LocalResult(text=f"I wasn't able to activate {fname}, {honorific}. {exc}", success=False)

    # Goodnight shortcut
    if re.search(r"^good\s*night(?:\s+jarvis)?$", normalized):
        found = _find_scene_or_script(hass, "goodnight") or _find_scene_or_script(hass, "good night")
        if found:
            eid, fname, dtype = found
            try:
                await hass.services.async_call(dtype, "turn_on", {"entity_id": eid}, blocking=True)
                return LocalResult(text=f"Goodnight, {honorific}. {fname} activated. Rest well.", success=True)
            except Exception:
                pass
        off_count = 0
        for s in hass.states.async_all("light"):
            if s.state == "on":
                try:
                    await hass.services.async_call("light", "turn_off", {"entity_id": s.entity_id}, blocking=False)
                    off_count += 1
                except Exception:
                    pass
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                try:
                    await hass.services.async_call("lock", "lock", {"entity_id": s.entity_id}, blocking=False)
                except Exception:
                    pass
        return LocalResult(text=f"Goodnight, {honorific}. {off_count} lights off, all locks secured. Rest well.", success=True)

    # Single-entity patterns
    _last_failed_name = None  # Track for end-of-loop error
    for pattern, action, domain_hint in _INTENT_PATTERNS:
        if action == "scene":
            continue
        match = re.search(pattern, normalized)
        if not match:
            continue
        _LOGGER.info("Local intent: action=%s", action)
        if action in ("query_time", "query_date", "greeting", "thanks", "status"):
            return LocalResult(text=_query_resp(hass, action, "", "", honorific), success=True)
        groups = match.groups()
        name_frag = groups[0] if groups else ""
        extra_arg = groups[1] if len(groups) > 1 else None
        if not name_frag:
            continue
        resolved = _find_entity(hass, name_frag, domain_hint) or _find_entity(hass, name_frag, None)
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
            resp = _query_resp(hass, action, entity_id, fname, honorific)
            if resp:
                return LocalResult(text=resp, success=True)
            continue
        success = await _execute_action(hass, action, entity_id, args)
        _update_ctx(entity=entity_id, domain=entity_id.split(".")[0], action=action)
        return LocalResult(text=_resp(action, fname, success, args, honorific), success=success)

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
                text=(
                    f"I couldn't find a device matching '{_last_failed_name}', "
                    f"{honorific}, and I'm offline so I can't do a deeper search "
                    f"right now. Try the exact device name."
                ),
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
                        text=(
                            f"Already on it, {honorific}. I'm monitoring "
                            f"{', '.join(tracking)}. I'll announce when "
                            f"the cycle completes."
                        ),
                        success=True,
                    )
                return LocalResult(
                    text=(
                        f"I'm monitoring for appliance cycles, {honorific}, "
                        f"but I haven't found any power sensors matching "
                        f"your appliances yet. Make sure the power monitoring "
                        f"sensor has 'washer', 'dryer', or 'dishwasher' "
                        f"in its name."
                    ),
                    success=True,
                )
            else:
                return LocalResult(
                    text=(
                        f"The appliance monitor isn't running at the moment, "
                        f"{honorific}. It starts automatically with the "
                        f"observer. Check if the observer is enabled."
                    ),
                    success=True,
                )
        except Exception:
            return LocalResult(
                text=(
                    f"I'll keep an eye on it, {honorific}. If I have a "
                    f"power sensor for that appliance, I'll announce when "
                    f"the cycle finishes."
                ),
                success=True,
            )

    # Follow-up ("also turn off the kitchen")
    if _ctx_fresh() and re.search(r"^(?:also|and|now)\s+", normalized):
        stripped = re.sub(r"^(?:also|and|now)\s+", "", normalized)
        result = await try_local(hass, stripped, honorific, force=force)
        if result and result.handled:
            return result

    # Low complexity bare entity name → state query.
    # Guard: only attempt this for SHORT, non-interrogative phrases. A bare
    # entity name is something like "kitchen lights" or "front door" — not a
    # question or a sentence about JARVIS itself. Without this guard, phrases
    # like "what are your capabilities" get fed wholesale to the resolver,
    # which wastes a full registry scan and (previously) logged noise.
    if complexity < 40 and _looks_like_entity_name(normalized):
        resolved = _find_entity(hass, normalized, None)
        if resolved:
            entity_id, fname = resolved
            state = hass.states.get(entity_id)
            if state:
                return LocalResult(text=f"{fname} is currently {state.state}, {honorific}.", success=True)

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
    # Phrases about JARVIS itself ("your X", "you Y") aren't device names.
    if "your" in words or "you" in words:
        return False
    return True
