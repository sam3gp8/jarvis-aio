"""Edge-satellite heartbeat + failover routing for JARVIS.

``HeartbeatMonitor`` pings fixed-IP audio satellites at a high frequency and, when
a node misses several cycles in a row, flags it unavailable and reroutes audio to
an adjacent speaker node. The failover state machine is pure and dependency-free;
the actual UDP probe is injectable so the routing logic tests without real I/O.

Node registry shape:
    {
      "office":  {"ip": "10.0.1.21", "speaker": "media_player.office",
                  "adjacent": ["hallway", "living_room"]},
      "hallway": {"ip": "10.0.1.22", "speaker": "media_player.hallway",
                  "adjacent": ["office"]},
      ...
    }

This is a configured capability — it is not auto-started, since it needs your
satellites' fixed IPs and adjacency. Construct it with your registry and drive
``run_once`` from an interval, then consult ``speaker_for`` when routing audio.
"""
from __future__ import annotations

import asyncio
import logging
import socket

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_MISSES = 3
DEFAULT_PING_PORT = 6053         # ESPHome native API port (a TCP connect probe)
DEFAULT_TIMEOUT_S = 0.5


class HeartbeatMonitor:
    """Track satellite liveness by consecutive misses and route around failures."""

    def __init__(
        self,
        nodes: dict[str, dict],
        *,
        max_misses: int = DEFAULT_MAX_MISSES,
        ping_fn=None,
        port: int = DEFAULT_PING_PORT,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.nodes = dict(nodes)
        self.max_misses = max(1, int(max_misses))
        self.port = port
        self.timeout = timeout
        self._ping_fn = ping_fn  # async (ip) -> bool ; injectable for tests
        self._misses: dict[str, int] = {n: 0 for n in self.nodes}
        self._available: dict[str, bool] = {n: True for n in self.nodes}

    # ── State machine (pure) ──────────────────────────────────────────────
    def record_result(self, node: str, alive: bool) -> None:
        """Fold one probe result into the node's liveness state."""
        if node not in self.nodes:
            return
        if alive:
            if not self._available[node]:
                _LOGGER.info("heartbeat: %s recovered", node)
            self._misses[node] = 0
            self._available[node] = True
        else:
            self._misses[node] += 1
            if self._misses[node] >= self.max_misses and self._available[node]:
                self._available[node] = False
                _LOGGER.warning(
                    "heartbeat: %s unavailable after %d missed cycles",
                    node, self._misses[node],
                )

    def is_available(self, node: str) -> bool:
        return self._available.get(node, False)

    def down_nodes(self) -> list[str]:
        return [n for n, ok in self._available.items() if not ok]

    # ── Failover routing ──────────────────────────────────────────────────
    def route_for(self, node: str) -> str | None:
        """The node that should actually play audio destined for ``node``:
        itself if available, else the first available adjacent node, else any
        available node, else None (everything is down)."""
        if self.is_available(node):
            return node
        for adjacent in self.nodes.get(node, {}).get("adjacent", []):
            if self.is_available(adjacent):
                _LOGGER.debug("heartbeat: rerouting %s → %s", node, adjacent)
                return adjacent
        for candidate, ok in self._available.items():
            if ok:
                _LOGGER.debug("heartbeat: rerouting %s → %s (no adjacent up)", node, candidate)
                return candidate
        return None

    def speaker_for(self, node: str) -> str | None:
        """The media_player entity to target for ``node`` after failover."""
        target = self.route_for(node)
        if target is None:
            return None
        return self.nodes.get(target, {}).get("speaker")

    # ── Probing ───────────────────────────────────────────────────────────
    async def _ping(self, ip: str | None) -> bool:
        if not ip:
            return False
        if self._ping_fn is not None:
            return await self._ping_fn(ip)
        # Default probe: a short TCP connect to the satellite's API port. Cheaper
        # and more reliable across networks than raw ICMP, and doesn't need root.
        try:
            fut = asyncio.open_connection(ip, self.port)
            reader, writer = await asyncio.wait_for(fut, timeout=self.timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return True
        except (OSError, asyncio.TimeoutError, socket.gaierror):
            return False

    async def run_once(self) -> None:
        """Probe every node once and update liveness. Drive this from an
        interval (e.g. every second) for sub-cycle failover."""
        for node, cfg in self.nodes.items():
            alive = await self._ping(cfg.get("ip"))
            self.record_result(node, alive)
