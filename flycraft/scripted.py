"""A scripted fake game for testing the pipeline without Minecraft (`python -m flycraft --scripted`).

A zombie walks in from the left, crosses in front of the bot, stands on its right, hits it,
and leaves. The bot stands at the origin facing north (yaw 0).
"""
from __future__ import annotations

import math

import numpy as np

from .config import Config
from .sense import Entity, GameState, relative_bearing

# (time s, x, z): the mob's path. -x is the bot's left, -z is ahead.
WAYPOINTS = [(8.0, -15.0, 0.0), (16.0, -2.0, 0.0), (19.0, -2.0, -4.0), (22.0, 2.0, -4.0),
             (24.0, 2.0, 0.0), (28.0, 2.0, 0.0), (34.0, 30.0, 0.0)]
HIT_AT = 24.0
DURATION = 40.0
_T, _X, _Z = zip(*WAYPOINTS)


class ScriptedGame:
    def __init__(self, cfg: Config):
        self._dt = cfg.dt
        self._max_distance = cfg.max_entity_distance
        self._step = 0

    def read(self) -> GameState:
        t = self._step * self._dt
        hit = self._step == round(HIT_AT / self._dt)
        self._step += 1
        nearest = None
        if _T[0] <= t <= _T[-1]:
            dx, dz = float(np.interp(t, _T, _X)), float(np.interp(t, _T, _Z))
            distance = math.hypot(dx, dz)
            if distance <= self._max_distance:
                nearest = Entity(1, "zombie", "hostile", distance, relative_bearing(dx, dz, 0.0))
        return GameState(19.0 if hit else 20.0, 0.0, 64.0, 0.0, 0.0, 0.0, nearest, hit, 1.0 if hit else 0.0)
