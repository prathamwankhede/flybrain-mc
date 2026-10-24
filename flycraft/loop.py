"""The per-step pipeline: sense -> encode -> brain step -> decode (spec F09, reused by F10/F11)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from flybrain import FlyBrain

from .brain import ResolvedGroup
from .config import Config
from .decoder import Decoder, Readout, settle_steps
from .encoder import Encoder, Injection, pairs
from .sense import GameState

# Output group roles; the neurons behind each come from [outputs.*] in config.toml.
STEER_LEFT, STEER_RIGHT = "steer_left", "steer_right"

MAX_LAG_S = 0.5     # after a stall longer than this, resync the clock instead of replaying missed steps


class GameSource(Protocol):
    def read(self) -> GameState | None: ...


@dataclass(frozen=True)
class StepRecord:
    step: int
    t: float                              # brain time in seconds (step * dt)
    state: GameState | None
    injections: list[Injection]
    readouts: dict[str, Readout]
    ready: bool                           # decoder warm-up finished

    @property
    def dev_steer(self) -> float:
        """Left minus right steering deviation; positive = turn left."""
        left, right = self.readouts.get(STEER_LEFT), self.readouts.get(STEER_RIGHT)
        return (left.deviation if left else 0.0) - (right.deviation if right else 0.0)


class Loop:
    def __init__(self, brain: FlyBrain, inputs: dict[str, ResolvedGroup], outputs: dict[str, ResolvedGroup],
                 source: GameSource | None, cfg: Config):
        self.brain = brain
        self.source = source
        self.encoder = Encoder(inputs, cfg)
        self.decoder = Decoder(outputs, brain.n, cfg)
        self.dt = brain.dt
        self.step = 0
        self.overruns = 0
        self._next: float | None = None
        self._warmup = cfg.warmup_steps
        self._settle = settle_steps(cfg)
        self._since_injection = self._settle + 1
        brain.reset(cfg.seed)

    def warm_up(self) -> None:
        """Run the brain with no input so the decoder learns its resting baseline.

        The resting rate belongs to the brain, not the game: learning it while a mob happens to
        be nearby would bake the mob's effect into the baseline. Call before the first tick."""
        for _ in range(self._warmup):
            self.decoder.update(self.brain.step())

    def tick(self) -> StepRecord:
        assert self.source is not None, "set loop.source before ticking"
        state = self.source.read()
        injections = self.encoder.encode(state)
        fired = self.brain.step(inject=pairs(injections))
        self._since_injection = 0 if injections else self._since_injection + 1
        # Learn the resting baseline only while undriven, and not during the tail of a response.
        readouts = self.decoder.update(fired, hold_baseline=self._since_injection <= self._settle)
        record = StepRecord(self.step, self.step * self.dt, state, injections, readouts, self.decoder.ready)
        self.step += 1
        return record

    def pace(self) -> None:
        """Sleep so ticks happen once per dt of wall-clock time; counts steps that ran late."""
        now = time.monotonic()
        if self._next is None:
            self._next = now
        self._next += self.dt
        delay = self._next - now
        if delay > 0:
            time.sleep(delay)
            return
        self.overruns += 1
        if delay < -MAX_LAG_S:
            self._next = now
