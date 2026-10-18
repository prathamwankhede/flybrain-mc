"""Brain loader and neuron-group resolver (spec F03). Run: python -m flycraft.brain"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass

import numpy as np
from flybrain import FlyBrain, has_data

from . import check_env, config
from .config import Config, Group

log = logging.getLogger("flycraft.brain")


@dataclass(frozen=True)
class ResolvedGroup:
    name: str
    types: tuple[str, ...]
    side: str | None
    idx: np.ndarray

    @property
    def size(self) -> int:
        return int(self.idx.size)


class GroupResolutionError(RuntimeError):
    def __init__(self, empty: list[ResolvedGroup]):
        self.empty = empty
        names = ", ".join(g.name for g in empty)
        super().__init__(f"{len(empty)} group(s) resolved to zero neurons: {names}")


def load_brain(cfg: Config) -> FlyBrain:
    if not has_data(cfg.data_dir):
        raise SystemExit(f"brain files (brain.npz, weights.npz) not found in {cfg.data_dir}\n"
                         f"fetch them with: python -m flybrain download --data {cfg.data_dir}")
    t0 = time.perf_counter()
    brain = FlyBrain(data=cfg.data_dir, seed=cfg.seed, device=cfg.device, dt=cfg.dt,
                     sensory_input=cfg.sensory_input, refractory=cfg.refractory)
    log.info("brain loaded: %d neurons on %s in %.1f s", brain.n, brain.device, time.perf_counter() - t0)
    return brain


def resolve_groups(brain: FlyBrain, groups: dict[str, Group]) -> dict[str, ResolvedGroup]:
    return {name: ResolvedGroup(name, g.types, g.side, brain.cells(list(g.types), side=g.side))
            for name, g in groups.items()}


def check_groups(resolved: dict[str, ResolvedGroup], kind: str) -> None:
    for g in resolved.values():
        log.info("%s %-14s %s side=%s -> %d neurons", kind, g.name, list(g.types), g.side or "any", g.size)
    empty = [g for g in resolved.values() if g.size == 0]
    for g in empty:
        log.warning("%s group %r resolved to zero neurons (types=%s, side=%s); check names on neuPrint",
                    kind, g.name, list(g.types), g.side or "any")
    if empty:
        raise GroupResolutionError(empty)


def setup(cfg: Config) -> tuple[FlyBrain, dict[str, ResolvedGroup], dict[str, ResolvedGroup]]:
    brain = load_brain(cfg)
    inputs = resolve_groups(brain, cfg.inputs)
    outputs = resolve_groups(brain, cfg.outputs)
    empty: list[ResolvedGroup] = []
    for resolved, kind in ((inputs, "input"), (outputs, "output")):
        try:
            check_groups(resolved, kind)
        except GroupResolutionError as e:
            empty += e.empty
    if empty:
        raise GroupResolutionError(empty)
    return brain, inputs, outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the brain and resolve the configured neuron groups.")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    missing = [name for name, problem in check_env.run_checks() if problem]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    try:
        setup(config.load(args.config))
    except GroupResolutionError as e:
        log.error("stopping: %s", e)
        return 1
    log.info("all groups resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
