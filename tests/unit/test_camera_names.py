"""Tests for JARVIS-only camera renames (v6.48.0): display_name precedence
and the blank-to-revert merge, mirrored by the panel and ws_rename_camera."""
import pytest


@pytest.fixture
def cam(load):
    return load("camera")


def test_display_name_custom_wins(cam):
    assert cam.display_name("camera.x", "HA Name",
                            {"camera.x": "Eliana's Room"}) == "Eliana's Room"


def test_display_name_strips_whitespace(cam):
    assert cam.display_name("camera.x", "HA Name",
                            {"camera.x": "  Porch  "}) == "Porch"


def test_display_name_blank_custom_falls_back_to_friendly(cam):
    assert cam.display_name("camera.x", "HA Name", {"camera.x": "   "}) == "HA Name"
    assert cam.display_name("camera.x", "HA Name", {}) == "HA Name"
    assert cam.display_name("camera.x", "HA Name", None) == "HA Name"


def test_display_name_non_string_custom_ignored(cam):
    assert cam.display_name("camera.x", "HA Name", {"camera.x": 42}) == "HA Name"


def test_display_name_last_resort_entity_id(cam):
    assert cam.display_name("camera.x", None, {}) == "camera.x"
    assert cam.display_name("camera.x", "", {}) == "camera.x"


def test_merge_sets_and_strips(cam):
    out = cam.merge_camera_name({}, "camera.x", "  Front Porch ")
    assert out == {"camera.x": "Front Porch"}


def test_merge_blank_reverts(cam):
    start = {"camera.x": "Front Porch", "camera.y": "Garage"}
    assert cam.merge_camera_name(start, "camera.x", "") == {"camera.y": "Garage"}
    assert cam.merge_camera_name(start, "camera.x", "   ") == {"camera.y": "Garage"}
    assert cam.merge_camera_name(start, "camera.x", None) == {"camera.y": "Garage"}


def test_merge_does_not_mutate_input(cam):
    start = {"camera.x": "Old"}
    out = cam.merge_camera_name(start, "camera.x", "New")
    assert start == {"camera.x": "Old"}
    assert out == {"camera.x": "New"}


def test_merge_revert_missing_key_is_noop(cam):
    assert cam.merge_camera_name({"camera.y": "G"}, "camera.x", "") == {"camera.y": "G"}
