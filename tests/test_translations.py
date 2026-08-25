"""Config-flow translation files must mirror en.json's structure (v7.36.0)."""

import glob
import json
import os

BASE = os.path.join("custom_components", "jarvis", "translations")


def _shape(node):
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items()}
    return None


def test_all_config_flow_translations_match_en_structure():
    en = json.load(open(os.path.join(BASE, "en.json"), encoding="utf-8"))
    en_shape = _shape(en)
    files = glob.glob(os.path.join(BASE, "*.json"))
    assert len(files) >= 7, "expected en + at least 6 translations"
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        assert _shape(d) == en_shape, f"{os.path.basename(f)} structure differs from en.json"


def test_translations_have_no_empty_values():
    for f in glob.glob(os.path.join(BASE, "*.json")):
        d = json.load(open(f, encoding="utf-8"))

        def walk(n):
            if isinstance(n, dict):
                for v in n.values():
                    walk(v)
            elif isinstance(n, str):
                assert n.strip() != "", f"empty string in {os.path.basename(f)}"

        walk(d)


def test_panel_i18n_files_valid_and_nonempty():
    import glob
    base = os.path.join("custom_components", "jarvis", "frontend", "i18n")
    files = glob.glob(os.path.join(base, "*.json"))
    assert len(files) >= 12, "expected the seeded panel language files"
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        assert isinstance(d, dict) and d, f"{os.path.basename(f)} empty or not an object"
        for k, v in d.items():
            assert isinstance(v, str) and v.strip(), f"empty value for {k!r} in {os.path.basename(f)}"
