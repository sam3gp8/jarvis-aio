"""Regression tests for the air-gapped fallback templates."""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tpl = _load_standalone("jarvis_templates", "intent/templates.py")


def test_template_for_known_key_formats_honorific():
    out = tpl.template_for("network_restored", honorific="sir")
    assert out is not None
    assert "Sir" in out
    assert "restored" in out.lower()


def test_template_for_unknown_key_is_none():
    assert tpl.template_for("does_not_exist") is None


def test_match_status_picks_best_keyword_hit():
    out = tpl.match_status("is the sump pump ok in the basement", honorific="sir")
    assert out is not None
    assert "sump pump" in out.lower()


def test_match_status_no_hit_is_none():
    assert tpl.match_status("compose a sonnet about the moon") is None


def test_match_status_empty_is_none():
    assert tpl.match_status("") is None


def test_all_templates_format_without_error():
    # Every template must format cleanly with just an honorific.
    for key in tpl.STATUS_TEMPLATES:
        assert tpl.template_for(key, honorific="ma'am")
