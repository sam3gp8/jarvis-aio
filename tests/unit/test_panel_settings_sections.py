"""Guard: Settings cards land under the sub-tab a user browses to (#577).

The Settings page groups cards into sub-tabs. `_stampSettingsSections()` stamps
each `.settings-grid .panel` with `data-section` from a heading -> section MAP,
and `_applySettingsSections()` hides cards whose section != the active sub-tab.
An unmapped heading falls to the "general" fallback — safe for a brand-new card,
but a *voice/audio* card that falls to "general" vanishes from the Voice & Audio
sub-tab while the filter box (which matches across all sections) can still find
it. That is exactly #577: the #540 "Say Something" TTS box was reachable only
through search because it wasn't in the MAP.

This locks the voice/audio cluster to the "voice" section so a sibling audio card
can't silently fall back to General again.
"""
import re
from pathlib import Path

_PANEL = (Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
          / "frontend" / "jarvis-panel.js")


def _settings_map() -> str:
    """The body of the `const MAP = { … }` object in _stampSettingsSections()."""
    js = _PANEL.read_text()
    m = re.search(r"const MAP = \{(.*?)\};", js, re.S)
    assert m, "settings-section MAP not found in _stampSettingsSections()"
    return m.group(1)


# Every card rendered in the Voice & Audio settings section — these must all map
# to "voice" so they appear together under that sub-tab, not scattered to General.
_VOICE_CLUSTER = [
    "Announcement Speakers",
    "Satellite → Speaker",
    "Voice Confirmation",
    "AI Models",
    "Briefings",
]


def test_say_something_is_in_chat_tab_with_speaker_selector():
    # #552/panel: "Say Something" moved from Settings → Voice & Audio into the
    # Chat tab, with a speaker picker (#announce-speaker) to target one speaker
    # or broadcast to all. It must NOT remain in the settings section MAP.
    js = _PANEL.read_text()
    body = _settings_map()
    assert not re.search(r'"Say Something"\s*:', body), (
        '"Say Something" should live in the Chat tab, not the Settings section MAP'
    )
    chat_block = re.search(r"this\._currentTab === 'chat' \? `(.*?)` : ''", js, re.S)
    assert chat_block, "Chat tab block not found in jarvis-panel.js"
    chat_html = chat_block.group(1)
    assert "<span>Say Something</span>" in chat_html, (
        '"Say Something" card must be rendered inside the Chat tab'
    )
    assert 'id="announce-speaker"' in chat_html, (
        '"Say Something" in the Chat tab must include the #announce-speaker selector'
    )


def test_voice_cluster_cards_all_map_to_voice():
    body = _settings_map()
    missing = [h for h in _VOICE_CLUSTER
               if not re.search(r'"' + re.escape(h) + r'"\s*:\s*"voice"', body)]
    assert not missing, (
        f"these Voice & Audio cards are not mapped to the 'voice' sub-tab: {missing} — "
        "an audio card absent from the MAP falls to General and hides from the Audio tab"
    )
