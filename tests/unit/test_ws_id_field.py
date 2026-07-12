"""Guard against the reserved-'id' WebSocket bug (v6.36.2).

In the HA WS protocol, `id` is the message sequence number — the frontend sets
it and overwrites anything we send under that key. A command that declares its
own `id` payload field therefore silently receives the wrong value (this broke
the Memory tab's forget button, which deleted nothing). Custom ids must use a
distinct name like `fact_id`.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
_WS = _ROOT / "websocket.py"
_PANEL = _ROOT / "frontend" / "jarvis-panel.js"


def test_no_ws_command_declares_reserved_id_field():
    offenders = re.findall(r'vol\.(?:Optional|Required)\(\s*["\']id["\']\s*\)',
                           _WS.read_text())
    assert not offenders, (
        "a WS command schema declares a reserved 'id' field — it collides with "
        "the HA message id and is overwritten by the frontend; use e.g. 'fact_id'"
    )


def test_forget_button_sends_fact_id():
    panel = _PANEL.read_text()
    m = re.search(r'jarvis/forget_knowledge["\'],?\s*([^}]*)\}', panel)
    assert m, "forget_knowledge WS call not found in panel"
    assert "fact_id" in m.group(1) and re.search(r'\bid\b', m.group(1)) is None or "fact_id" in m.group(1)
    assert "fact_id" in m.group(1)


def test_forget_handler_reads_fact_id():
    assert 'msg.get("fact_id")' in _WS.read_text()
