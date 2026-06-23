"""JARVIS audio layer: prosody and announcement shaping."""
"""JARVIS audio layer: adaptive prosody + appliance-noise compensation."""
from __future__ import annotations

from .noise_gate import NoiseGate
from .prosody import ProsodyController, VocalProfile

__all__ = ["ProsodyController", "VocalProfile", "NoiseGate"]
