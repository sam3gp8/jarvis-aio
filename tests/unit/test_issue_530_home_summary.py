"""#530 (on-demand side) — the `get_home_summary` tool surfaces openings the
vision watch sees.

The periodic watch (`opening_watch.check_openings`) already reads sensor-less
doors/windows from cameras and remembers each verdict (see `test_opening_watch`).
This pins the *on-demand* half: when the owner asks "what's open?", the home
summary must fold in any opening the watch currently sees OPEN — tagged so it
reads as camera-sourced, not a contact sensor — and must stay silent when the
watch is off (a stale cached verdict is not a live reading).
"""
import json


def _op(oid, otype, room, label):
    return {"id": oid, "type": otype, "room": room, "floor": "1st", "label": label}


async def test_home_summary_folds_in_camera_seen_open_opening(load, fake_hass, monkeypatch):
    agent = load("agent")
    ow = load("opening_watch")
    ow.clear()
    # Owner has the watch on; the watch currently sees a shed window OPEN and a
    # hall door CLOSED.
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    ow._record(_op("e2", "window", "Shed", "Shed window"), "open", "camera.shed", now=100)
    ow._record(_op("e3", "door", "Hall", "Hall door"), "closed", "camera.hall", now=100)

    out = json.loads(await agent._exec_home_summary(fake_hass, {}))

    # The open one is surfaced and tagged camera-sourced; the closed one is not.
    assert out["open_doors_windows"] == ["Shed window (seen on camera)"]


async def test_home_summary_omits_camera_openings_when_watch_off(load, fake_hass, monkeypatch):
    agent = load("agent")
    ow = load("opening_watch")
    ow.clear()
    # A remembered OPEN verdict is cached, but the watch is OFF → it must not be
    # reported as a live open.
    monkeypatch.setattr(ow, "_watch_on", lambda hass: False)
    ow._record(_op("e2", "window", "Shed", "Shed window"), "open", "camera.shed", now=100)

    out = json.loads(await agent._exec_home_summary(fake_hass, {}))
    assert out["open_doors_windows"] == []


async def test_home_summary_camera_openings_coexist_with_real_sensors(load, fake_hass, monkeypatch):
    agent = load("agent")
    ow = load("opening_watch")
    ow.clear()
    # A real contact-sensor door is open AND the watch sees a sensor-less window
    # open — the on-demand answer carries both, each labelled by its source.
    fake_hass.states.set("binary_sensor.front_door", "on",
                         device_class="door", friendly_name="Front Door")
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    ow._record(_op("e2", "window", "Shed", "Shed window"), "open", "camera.shed", now=100)

    out = json.loads(await agent._exec_home_summary(fake_hass, {}))
    assert set(out["open_doors_windows"]) == {"Front Door", "Shed window (seen on camera)"}


async def test_home_summary_unknown_verdict_is_not_reported_open(load, fake_hass, monkeypatch):
    agent = load("agent")
    ow = load("opening_watch")
    ow.clear()
    # An "unknown" verdict (a frame the model couldn't read) is never reported as
    # open — only a confident OPEN is surfaced.
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    ow._record(_op("e2", "window", "Shed", "Shed window"), "unknown", "camera.shed", now=100)

    out = json.loads(await agent._exec_home_summary(fake_hass, {}))
    assert out["open_doors_windows"] == []
