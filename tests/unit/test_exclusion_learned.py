"""Exclusion must silence already-learnt entities immediately (issue #23):
Sentinel + appliance monitor skip excluded entities without purging state."""
import re
import pathlib


def test_sentinel_monitoring_set_excludes():
    """An excluded entity must drop out of Sentinel's monitored set, so an
    already-learnt lock's 'unlocked for N minutes' report stops."""
    src = pathlib.Path("custom_components/jarvis/sentinel.py").read_text()
    m = re.search(r"def _collect_entity_ids\(.*?\n(.*?)\n\n    def ", src, re.S)
    assert m, "could not isolate _collect_entity_ids"
    assert "is_excluded" in m.group(1), "_collect_entity_ids must filter excluded entities"


def test_sentinel_announce_gate_checks_exclusion():
    """The periodic check and the announce both consult the exclusion filter, so
    an already-tracked entity goes silent the moment it's excluded."""
    import pathlib
    src = pathlib.Path("custom_components/jarvis/sentinel.py").read_text()
    for fn in ("_announce_rule", "_check_durations"):
        m = re.search(rf"def {fn}\(.*?\n(.*?)\n\n    (?:async )?def ", src, re.S)
        assert m, f"could not isolate {fn}"
        assert "is_excluded" in m.group(1) or "_excl" in m.group(1), \
            f"{fn} must consult the exclusion filter"


def test_appliance_announce_gate_checks_exclusion():
    """Appliance cycle-complete announcements skip excluded entities."""
    import pathlib
    src = pathlib.Path("custom_components/jarvis/appliance_monitor.py").read_text()
    m = re.search(r"async def _announce_done\(.*?\n(.*?)\n\n(?:async )?def ", src, re.S)
    assert m, "could not isolate _announce_done"
    assert "is_excluded" in m.group(1), "_announce_done must consult the exclusion filter"
