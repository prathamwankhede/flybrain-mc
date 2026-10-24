"""Sensory encoder (spec F07). Run: python -m flycraft.encoder

Turns a sensed GameState into brain injections. With the nearest entity in range:
looming detectors (LC4, LPLC2) on its side -> jump, LC10a on its side -> turn toward
it, LPC1 -> walk. Damage drives both looming groups. Nothing nearby -> no injections.

The self-test builds fake game states, prints the injections, and drives the real
brain to show what each case makes the output neurons do (no Minecraft needed).
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import sys
from dataclasses import dataclass

import numpy as np

from . import brain as brain_mod
from . import check_env, config
from .brain import ResolvedGroup
from .config import Config
from .sense import Entity, GameState

log = logging.getLogger("flycraft.encoder")

# Input group roles; the neurons behind each come from [inputs.*] in config.toml.
# A role whose group is missing from the config is skipped, which switches it off.
LOOMING, TRACK, WALK = "looming", "track", "walk"


@dataclass(frozen=True)
class Injection:
    name: str            # input group name, e.g. "looming_left"
    idx: np.ndarray
    amount: float


def pairs(injections: list[Injection]) -> list[tuple[np.ndarray, float]]:
    """The `inject=` argument of FlyBrain.step."""
    return [(i.idx, i.amount) for i in injections]


def side_shares(bearing: float) -> tuple[float, float]:
    """(left, right) weights in [0, 1] for an entity at `bearing` (positive = left).

    Smooth in the bearing so steering does not flip when the entity crosses straight
    ahead. Ahead or behind, both sides get full weight (equal left/right drive, so no
    net turn); fully to one side, the other side gets none.
    """
    left = (1 + math.sin(bearing)) / 2
    return min(1.0, 2 * left), min(1.0, 2 * (1 - left))


class Encoder:
    def __init__(self, inputs: dict[str, ResolvedGroup], cfg: Config):
        self._inputs = inputs
        self._cfg = cfg
        self._damage_left = 0

    def encode(self, state: GameState | None) -> list[Injection]:
        cfg = self._cfg
        amounts: dict[str, float] = {}
        if state is not None and state.damaged:
            self._damage_left = max(1, cfg.damage_hold_steps)
        if self._damage_left > 0:
            self._damage_left -= 1
            amounts[f"{LOOMING}_left"] = amounts[f"{LOOMING}_right"] = cfg.damage_amount
        if state is not None and state.nearest is not None:
            e = state.nearest
            closeness = min(1.0, max(0.0, 1 - e.distance / cfg.max_entity_distance))
            for side, share in zip(("left", "right"), side_shares(e.bearing)):
                loom = min(cfg.max_amount, closeness) * share
                amounts[f"{LOOMING}_{side}"] = max(amounts.get(f"{LOOMING}_{side}", 0.0), loom)
                amounts[f"{TRACK}_{side}"] = cfg.track_amount * share
            amounts[WALK] = cfg.walk_amount
        return [Injection(name, self._inputs[name].idx, amount)
                for name, amount in amounts.items() if amount > 0 and name in self._inputs]


# --- self-test ---------------------------------------------------------------------

WARMUP_STEPS, TEST_STEPS = 50, 100       # settle from silence, then 2 s per case


def fake_state(distance: float | None, bearing: float = 0.0, damaged: bool = False) -> GameState:
    nearest = None if distance is None else Entity(1, "zombie", "hostile", distance, bearing)
    return GameState(20.0, 0.0, 64.0, 0.0, 0.0, 0.0, nearest, damaged, 1.0 if damaged else 0.0)


def run_case(brain, inputs, outputs, cfg: Config, distance, bearing, damaged, seed: int) -> dict[str, float]:
    """Mean output-group rates (Hz) over TEST_STEPS with the same situation each step."""
    encoder = Encoder(inputs, cfg)
    brain.reset(seed)
    for _ in range(WARMUP_STEPS):
        brain.step()
    counts = dict.fromkeys(outputs, 0)
    for t in range(TEST_STEPS):
        fired = brain.step(inject=pairs(encoder.encode(fake_state(distance, bearing, damaged and t == 0))))
        for name, g in outputs.items():
            counts[name] += int(np.isin(g.idx, fired).sum())
    return {n: counts[n] / outputs[n].size / (TEST_STEPS * brain.dt) for n in counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Encoder self-test: fake game states -> injections -> brain outputs.")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    missing = [name for name, problem in check_env.run_checks() if problem]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    cfg = config.load(args.config)
    try:
        brain, inputs, outputs = brain_mod.setup(cfg)
    except brain_mod.GroupResolutionError as e:
        log.error("stopping: %s", e)
        return 1

    half_pi = math.pi / 2
    cases = [  # label, distance, bearing, damaged
        ("nothing nearby", None, 0.0, False),
        ("mob left 4 m", 4.0, half_pi, False),
        ("mob right 4 m", 4.0, -half_pi, False),
        ("mob ahead 4 m", 4.0, 0.0, False),
        ("mob left 14 m", 14.0, half_pi, False),
        ("damage", None, 0.0, True),
    ]
    names = list(outputs)
    print(f"{'case':<16} | injections (step 0)")
    print(f"{'':<16} | " + "  ".join(f"{n:>11}" for n in names) + "   (output rate, Hz, mean of seeds)")
    print("-" * 100)
    rates: dict[str, dict[str, float]] = {}
    for label, distance, bearing, damaged in cases:
        inj = Encoder(inputs, cfg).encode(fake_state(distance, bearing, damaged))
        runs = [run_case(brain, inputs, outputs, cfg, distance, bearing, damaged, s) for s in cfg.smoke_seeds]
        rates[label] = {n: float(np.mean([r[n] for r in runs])) for n in names}
        print(f"{label:<16} | " + "  ".join(f"{rates[label][n]:>11.2f}" for n in names))
        print(f"{'':<16}   -> " + (", ".join(f"{i.name} {i.amount:.2f}" for i in inj) or "(no injections)"))

    # Damage pulse length: one step vs the configured hold.
    one_step = dataclasses.replace(cfg, damage_hold_steps=1)
    pulse = np.mean([run_case(brain, inputs, outputs, one_step, None, 0.0, True, s)["jump"] for s in cfg.smoke_seeds])
    print(f"\ndamage jump rate: 1-step pulse {pulse:.2f} Hz vs {cfg.damage_hold_steps}-step hold "
          f"{rates['damage']['jump']:.2f} Hz")

    rest, left, right = rates["nothing nearby"], rates["mob left 4 m"], rates["mob right 4 m"]
    ahead, far, hit = rates["mob ahead 4 m"], rates["mob left 14 m"], rates["damage"]
    checks = {
        "nothing nearby -> no injections": not Encoder(inputs, cfg).encode(fake_state(None)),
        "mob in range -> injections": bool(Encoder(inputs, cfg).encode(fake_state(4.0))),
        "left: steer_left > steer_right": left["steer_left"] > left["steer_right"],
        "right: steer_right > steer_left": right["steer_right"] > right["steer_left"],
        "left/right: forward rises": min(left["forward"], right["forward"]) > rest["forward"],
        "left/right: jump rises": min(left["jump"], right["jump"]) > rest["jump"] + 1.0,
        "ahead: steering balanced (|L-R| under half the left-case gap)":
            abs(ahead["steer_left"] - ahead["steer_right"])
            < 0.5 * (left["steer_left"] - left["steer_right"]),
        "far mob drives jump less than near mob": far["jump"] < left["jump"],
        "damage: jump burst": hit["jump"] > rest["jump"] + 0.5,
    }
    print()
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\nENCODER SELF-TEST PASSED" if passed else "\nENCODER SELF-TEST FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
