"""Guard: every Home Assistant component the integration imports must be
declared in the manifest's dependencies or after_dependencies (v6.73.0).

This is exactly the check hassfest enforces in CI — a new import of an HA
component (like logbook) that isn't declared fails the HACS/hassfest validation.
Catching it here means it fails locally on `pytest` instead of after a push.

HA components that are always available and don't need declaring (the standard
library of the platform) are allow-listed below."""
import json
import pathlib
import re

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"

# Components that are core/always-present and are conventionally not required in
# a manifest's dependency list (persistent_notification is auto-loaded, http is
# already a hard dependency, etc.). Keep this conservative.
_ALLOWED_UNDECLARED = {
    "persistent_notification",  # always available, auto-set-up
    "websocket_api",            # core API surface, always present (hassfest
                                # does not require it in dependencies)
}


def _manifest_declared() -> set:
    m = json.loads((COMP / "manifest.json").read_text())
    return set(m.get("dependencies", [])) | set(m.get("after_dependencies", []))


def _imported_components() -> set:
    """Find every homeassistant.components.<X> referenced across the code, in
    both import styles:
        from homeassistant.components.<X> import ...
        from homeassistant.components import <X>
        import homeassistant.components.<X>
    """
    found: set = set()
    pat_dotted = re.compile(r"homeassistant\.components\.([a-z_][a-z0-9_]*)")
    # `from homeassistant.components import a, b as c, d` — capture just the
    # imported component names (the token right after 'import', and after each
    # comma), handling `as` aliases and stopping at end of line.
    pat_from = re.compile(
        r"from\s+homeassistant\.components\s+import\s+(.+)")
    for py in COMP.glob("*.py"):
        text = py.read_text()
        for m in pat_dotted.finditer(text):
            found.add(m.group(1))
        for m in pat_from.finditer(text):
            clause = m.group(1)
            # strip a trailing comment, then split the import list on commas
            clause = clause.split("#", 1)[0]
            for piece in clause.split(","):
                # first identifier in the piece is the component ("logbook" in
                # "logbook as lb"); ignore the alias
                token = piece.strip().split()[0] if piece.strip().split() else ""
                if re.fullmatch(r"[a-z_][a-z0-9_]*", token):
                    found.add(token)
    return found


def test_all_imported_ha_components_are_declared():
    declared = _manifest_declared()
    imported = _imported_components()
    missing = sorted(c for c in imported
                     if c not in declared and c not in _ALLOWED_UNDECLARED)
    assert not missing, (
        "these HA components are imported but not declared in manifest "
        f"dependencies/after_dependencies (hassfest will fail): {missing}. "
        "Add them to after_dependencies in manifest.json.")


def test_logbook_and_recorder_declared():
    # the two history components activity_history depends on
    declared = _manifest_declared()
    assert "logbook" in declared
    assert "recorder" in declared
