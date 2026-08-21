"""Camera coverage inference (v7.19.0, Phase 2b).

The panel computes the geometric candidates — which rooms a camera's cone reaches,
with sightlines traced through open doorways/cased openings and stopped by walls.
This module hands that estimate to the reasoning LLM to judge which rooms the camera
covers well enough to CONFIRM a person, and to write one short human sentence
("the full dining room and most of the living room through the open staircase").

Never raises: on any error it returns a geometry-only result so the feature always
produces something useful.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_FULL = 0.7        # a room at ~70%+ of its area in view is treated as confirmable


def _plain_reason(candidates: dict) -> str:
    parts = []
    for r, p in sorted(candidates.items(), key=lambda kv: -(kv[1] or 0)):
        pct = round((p or 0) * 100)
        if pct >= 70:
            parts.append(f"most of {r}")
        elif pct >= 30:
            parts.append(f"part of {r}")
        else:
            parts.append(f"a corner of {r}")
    return ("Sees " + ", ".join(parts) + ".") if parts else "Nothing in view."


def _geometry_result(candidates: dict) -> dict:
    return {
        "covered": [r for r, p in candidates.items() if (p or 0) >= _FULL],
        "reason": _plain_reason(candidates),
        "source": "geometry",
        "ts": int(time.time()),
    }


def _build_prompt(ctx: dict):
    room = ctx.get("room") or "an unknown room"
    cands = ctx.get("candidates") or {}
    opens = ctx.get("openings") or []
    outdoor = not ctx.get("indoor", True)
    lines = "\n".join(
        f"- {r}: {round((p or 0) * 100)}%"
        for r, p in sorted(cands.items(), key=lambda kv: -(kv[1] or 0))
    )
    sys_p = (
        "You judge what a home security camera can see, to help confirm whether an "
        "intruder is present. You are given a geometric estimate of how much of each "
        "room falls within the camera's view — sightlines are already traced through "
        "open doorways and cased openings and blocked by solid walls. Decide which "
        "rooms the camera covers well enough to CONFIRM a person is in them (about "
        "70%+ of a room is confirmable; a small sliver is not), and write ONE short, "
        "plain sentence describing the view. Respond with ONLY a JSON object of the "
        'form {"covered": ["Room", ...], "reason": "..."} — no other text, no code '
        "fences. Use only the room names given; do not invent rooms."
    )
    usr_p = f"The camera sits in {room}"
    if outdoor and ctx.get("range_ft"):
        usr_p += f", outdoors, seeing up to about {ctx['range_ft']} ft"
    usr_p += f". Field of view about {ctx.get('fov', 90)} degrees.\n"
    usr_p += f"Portion of each room within view (with clear line of sight):\n{lines}\n"
    if opens:
        usr_p += "Open connections in play: " + ", ".join(str(o) for o in opens) + ".\n"
    usr_p += "Which rooms can it confirm a person in, and describe the view in one sentence?"
    return sys_p, usr_p


def _parse(text: str, candidates: dict) -> Optional[dict]:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        t = m.group(0)
    try:
        obj = json.loads(t)
    except Exception:
        return None
    covered = obj.get("covered")
    if not isinstance(covered, list):
        covered = []
    valid = {str(r).lower(): r for r in candidates}          # LLM must not invent rooms
    covered = [valid[str(c).lower()] for c in covered if str(c).lower() in valid]
    reason = str(obj.get("reason") or "").strip()
    if not reason:
        return None
    return {"covered": covered, "reason": reason, "source": "llm", "ts": int(time.time())}


async def infer_coverage(hass, config: dict, cam_ctx: dict) -> dict:
    """cam_ctx: { entity, room, aim, fov, range_ft, indoor, candidates: {room: frac},
    openings: [str] }. Returns { covered: [rooms], reason: str, source, ts }."""
    candidates = cam_ctx.get("candidates") or {}
    if not candidates:
        return {"covered": [], "reason": "Nothing in view.", "source": "geometry", "ts": int(time.time())}
    fallback = _geometry_result(candidates)
    try:
        from .llm_provider import create_tier_provider
        client = await hass.async_add_executor_job(create_tier_provider, config, "reasoning")
        sys_p, usr_p = _build_prompt(cam_ctx)
        result = await hass.async_add_executor_job(
            lambda: client.chat(
                messages=[{"role": "system", "content": sys_p},
                          {"role": "user", "content": usr_p}],
                max_tokens=400, temperature=0.3))
        parsed = _parse((result.get("text") or ""), candidates)
        return parsed or fallback
    except Exception as exc:
        _LOGGER.debug("camera coverage LLM failed, using geometry: %s", exc)
        return fallback
