"""Brain-only smoke test (spec F04). Run: python -m flycraft.smoke_test

Stimulates the left looming detectors and counts giant-fiber (DNp01) spikes on each
side, then repeats on the right, for several noise seeds. Passes when the stimulated
side always fires more than the other side.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np
from flybrain import FlyBrain

from . import brain as brain_mod
from . import check_env, config
from .config import Config, Group

log = logging.getLogger("flycraft.smoke_test")

SIDES = ("L", "R")


def run_trial(brain: FlyBrain, stim_idx: np.ndarray, probes: dict[str, np.ndarray],
              steps: int, amount: float, seed: int) -> dict[str, int]:
    """Spikes per probe side over `steps` steps of injecting `amount` into `stim_idx`."""
    brain.reset(seed)
    counts = dict.fromkeys(probes, 0)
    inject = [(stim_idx, amount)]
    for _ in range(steps):
        fired = brain.step(inject=inject)
        for side, idx in probes.items():
            counts[side] += int(np.isin(idx, fired).sum())
    return counts


def run(cfg: Config) -> bool:
    for side, name in cfg.smoke_stimulus.items():
        if name not in cfg.inputs:
            raise SystemExit(f"smoke_test stimulus for side {side} names unknown input group {name!r}")
    brain, inputs, _ = brain_mod.setup(cfg)
    probes = brain_mod.resolve_groups(
        brain, {s: Group(f"probe_{s}", cfg.smoke_probe, s) for s in SIDES})
    brain_mod.check_groups(probes, "probe")
    probe_idx = {s: g.idx for s, g in probes.items()}

    log.info("stimulating for %d steps (%.2f s) at %.2f, seeds %s",
             cfg.smoke_steps, cfg.smoke_steps * brain.dt, cfg.smoke_amount, list(cfg.smoke_seeds))
    print(f"\n{'seed':>4} | {'stim':>4} | {'L spikes':>8} | {'R spikes':>8} | result")
    print("-" * 45)
    failures, t0 = 0, time.perf_counter()
    for seed in cfg.smoke_seeds:
        for stim in SIDES:
            counts = run_trial(brain, inputs[cfg.smoke_stimulus[stim]].idx, probe_idx,
                               cfg.smoke_steps, cfg.smoke_amount, seed)
            other = "R" if stim == "L" else "L"
            ok = counts[stim] > counts[other]
            failures += not ok
            print(f"{seed:>4} | {stim:>4} | {counts['L']:>8} | {counts['R']:>8} | {'PASS' if ok else 'FAIL'}")
    elapsed = time.perf_counter() - t0
    trials = 2 * len(cfg.smoke_seeds)
    print(f"\n{trials - failures}/{trials} trials passed "
          f"({trials * cfg.smoke_steps / elapsed:.0f} steps/s, {1000 * elapsed / (trials * cfg.smoke_steps):.1f} ms/step)")
    return failures == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Brain-only smoke test: looming stimulus -> same-side giant fiber.")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    missing = [name for name, problem in check_env.run_checks() if problem]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    try:
        passed = run(config.load(args.config))
    except brain_mod.GroupResolutionError as e:
        log.error("stopping: %s", e)
        return 1
    print("SMOKE TEST PASSED" if passed else "SMOKE TEST FAILED: stimulated side did not always win")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
