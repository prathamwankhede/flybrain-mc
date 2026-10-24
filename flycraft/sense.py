"""Game-state sensing (spec F06). Run: python -m flycraft.sense

Each step reads health, position, yaw and the nearest entity from Mineflayer (one
round trip via bot.js `sense`), then derives the entity's bearing relative to where
the bot faces and a damage flag when health drops.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import sys
import time
from dataclasses import dataclass

from . import check_env, config, game
from .config import Config

log = logging.getLogger("flycraft.sense")


def wrap(angle: float) -> float:
    """Angle wrapped to (-pi, pi]."""
    a = math.fmod(angle + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


def relative_bearing(dx: float, dz: float, yaw: float) -> float:
    """Bearing of offset (dx, dz) relative to `yaw`, Mineflayer convention.

    Yaw 0 faces -z (north) and grows counter-clockwise seen from above, the same
    convention bot.lookAt uses, so positive = target on the bot's left.
    """
    return wrap(math.atan2(-dx, -dz) - yaw)


@dataclass(frozen=True)
class Entity:
    id: int
    name: str
    type: str
    distance: float
    bearing: float          # radians; positive = left (unless flip_bearing)

    @property
    def side(self) -> str:
        return "L" if self.bearing > 0 else "R"


@dataclass(frozen=True)
class GameState:
    health: float
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    nearest: Entity | None
    damaged: bool           # health dropped since the previous reading
    damage: float           # how much (hit points)


class Sensor:
    def __init__(self, conn: game.Connection, cfg: Config):
        self._conn = conn
        self._max_distance = cfg.max_entity_distance
        self._ignore = list(cfg.ignore_entity_types)
        self._sign = -1.0 if cfg.flip_bearing else 1.0
        self._last_health: float | None = None

    def read(self) -> GameState | None:
        """Current state, or None before the bot has a body (not yet spawned)."""
        raw = self._conn.helper.sense(self._conn.bot, self._max_distance, self._ignore)
        if raw is None:
            return None
        return self.parse(json.loads(str(raw)))

    def parse(self, s: dict) -> GameState:
        health = float(s["health"])
        damage = 0.0 if self._last_health is None else max(0.0, self._last_health - health)
        self._last_health = health
        nearest = None
        if n := s.get("nearest"):
            bearing = self._sign * relative_bearing(n["dx"], n["dz"], s["yaw"])
            nearest = Entity(int(n["id"]), str(n["name"]), str(n["type"]), float(n["distance"]), bearing)
        return GameState(health, s["x"], s["y"], s["z"], s["yaw"], s["pitch"], nearest, damage > 0, damage)


def describe(st: GameState) -> str:
    line = (f"hp {st.health:4.1f} | pos ({st.x:7.1f}, {st.y:5.1f}, {st.z:7.1f}) "
            f"yaw {math.degrees(st.yaw):6.1f} | ")
    if st.nearest:
        n = st.nearest
        line += f"nearest {n.name:<10} {n.distance:4.1f} m  bearing {math.degrees(n.bearing):+6.1f} ({'LEFT' if n.side == 'L' else 'RIGHT'})"
    else:
        line += "nothing nearby"
    if st.damaged:
        line += f" | DAMAGE -{st.damage:.1f}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Join Minecraft and print the sensed game state.")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    parser.add_argument("--host", help="override [minecraft] host")
    parser.add_argument("--port", type=int, help="override [minecraft] port (e.g. the LAN port shown in chat)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    missing = [name for name, problem in check_env.run_checks() if problem]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    cfg = config.load(args.config)
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    try:
        conn = game.connect(cfg)
    except game.ConnectError as e:
        log.error("%s", e)
        return 1

    sensor = Sensor(conn, cfg)
    log.info("sensing every %.0f ms for %.0f s (Ctrl+C to stop); walk a mob past the bot's left and right, then hit it",
             1000 * cfg.dt, cfg.sense_test_seconds)
    reads, read_time, next_print = 0, 0.0, 0.0
    start = time.monotonic()
    try:
        while time.monotonic() - start < cfg.sense_test_seconds:
            t0 = time.monotonic()
            if not conn.poll():
                return 1
            st = sensor.read()
            read_time += time.monotonic() - t0
            reads += 1
            if st and (st.damaged or t0 >= next_print):  # always show damage lines
                print(describe(st), flush=True)
                next_print = t0 + 1 / cfg.sense_print_hz
            time.sleep(max(0.0, cfg.dt - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        if reads:
            log.info("%d reads, %.2f ms per read (poll + sense)", reads, 1000 * read_time / reads)
        conn.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
