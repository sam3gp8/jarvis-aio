"""Guard: the panel's _data() must pass door states through to the model.

_house3dDoors() reads _data().doors; _data() rebuilds a whitelist object, so a
missing passthrough silently drops every door state and no door ever shows open
(the bug present through v6.31.0)."""
import re
from pathlib import Path

_PANEL = (Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
          / "frontend" / "jarvis-panel.js")


def test_data_exposes_doors():
    js = _PANEL.read_text()
    m = re.search(r"\n  _data\(\)\s*\{(.*?)\n  \}", js, re.S)
    assert m, "_data() method not found in panel"
    body = m.group(1)
    assert re.search(r"doors:\s*live\.doors", body), (
        "_data() must expose live.doors — otherwise _house3dDoors() always "
        "returns {} and no door renders open"
    )
