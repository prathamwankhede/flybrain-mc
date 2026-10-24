"""Output decoder (spec F08). Run: python -m flycraft.decoder

Turns the spikes of each step into a smoothed firing rate per output group, minus a
slow baseline, so a group that is merely busy at rest reads near zero and only a real
change stands out. The self-test checks that against the real brain.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass

import numpy as np

from . import brain as brain_mod
from . import check_env, config
from .brain import ResolvedGroup
from .config import Config

log = logging.getLogger("flycraft.decoder")


def settle_steps(cfg: Config) -> int:
    """Steps for a smoothed response to die away (three smoothing time constants)."""
    return math.ceil(3 / (1 - cfg.trace_decay))


@dataclass(frozen=True)
class Readout:
    rate: float          # smoothed firing rate (Hz, per neuron in the group)
    baseline: float      # slow moving average of `rate`
    deviation: float     # rate - baseline


class Decoder:
    def __init__(self, outputs: dict[str, ResolvedGroup], n_neurons: int, cfg: Config):
        self._names = list(outputs)
        self._sizes = np.array([outputs[n].size for n in self._names], dtype=np.float64)
        self._masks = np.zeros((len(self._names), n_neurons), dtype=bool)
        for row, name in enumerate(self._names):
            self._masks[row, outputs[name].idx] = True
        self._dt = cfg.dt
        self._decay = cfg.trace_decay
        self._baseline_decay = cfg.baseline_decay
        self._warmup = cfg.warmup_steps      # steps of silence that set the resting baseline
        self._rate = np.zeros(len(self._names))
        self._baseline = np.zeros(len(self._names))
        self.steps = 0

    @property
    def ready(self) -> bool:
        """True once the warm-up baseline has settled; controls should wait for this."""
        return self.steps >= self._warmup

    def update(self, fired: np.ndarray, hold_baseline: bool = False) -> dict[str, Readout]:
        """Fold in one step's spikes (the indices returned by FlyBrain.step).

        hold_baseline: don't learn the baseline this step (an input is driving the network).
        The baseline is the resting rate; averaging in driven activity would let it creep up
        and hide later responses. Ignored during warm-up."""
        counts = self._masks[:, fired].sum(axis=1)
        inst = counts / self._sizes / self._dt
        self._rate = self._decay * self._rate + (1 - self._decay) * inst
        self.steps += 1
        if self.steps <= self._warmup:   # plain running mean, so the baseline settles in seconds
            self._baseline += (self._rate - self._baseline) / self.steps
        elif not hold_baseline:
            self._baseline = self._baseline_decay * self._baseline + (1 - self._baseline_decay) * self._rate
        return {name: Readout(float(self._rate[i]), float(self._baseline[i]), float(self._rate[i] - self._baseline[i]))
                for i, name in enumerate(self._names)}


# --- self-test ---------------------------------------------------------------------

REST_STEPS = 1500          # 30 s with no input
REST_MAX_MEAN_DEV = 0.5    # Hz: mean |deviation| allowed at rest, per group


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decoder self-test: quiet at rest, jump rises under looming.")
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

    names = list(outputs)
    decoder = Decoder(outputs, brain.n, cfg)
    brain.reset(cfg.seed)
    update_time = 0.0

    def step(inject=(), hold=False):
        nonlocal update_time
        fired = brain.step(inject=inject)
        t0 = time.perf_counter()
        out = decoder.update(fired, hold_baseline=hold)
        update_time += time.perf_counter() - t0
        return out

    for _ in range(cfg.warmup_steps):
        step()
    assert decoder.ready
    rest = {n: [] for n in names}
    for _ in range(REST_STEPS):
        out = step()
        for n, r in out.items():
            rest[n].append(r.deviation)
    jump_base_before = out["jump"].baseline

    print(f"No input, {REST_STEPS} steps ({REST_STEPS * cfg.dt:.0f} s) after a {cfg.warmup_steps}-step warm-up. Deviation (Hz):")
    print(f"{'group':<12} | {'mean':>7} {'std':>7} {'p95':>7}")
    rest_stats = {}
    for n in names:
        d = np.array(rest[n])
        rest_stats[n] = (d.mean(), d.std(), np.percentile(d, 95))
        print(f"{n:<12} | {rest_stats[n][0]:>+7.2f} {rest_stats[n][1]:>7.2f} {rest_stats[n][2]:>+7.2f}")

    stim = inputs[cfg.smoke_stimulus["L"]].idx
    during = [step(inject=[(stim, cfg.smoke_amount)], hold=True)["jump"] for _ in range(cfg.smoke_steps)]
    jump_dev = float(np.mean([r.deviation for r in during]))
    print(f"\nLeft looming at {cfg.smoke_amount} for {cfg.smoke_steps} steps: mean jump deviation {jump_dev:+.1f} Hz "
          f"(peak {max(r.deviation for r in during):+.1f}); jump baseline {jump_base_before:.2f} -> {during[-1].baseline:.2f} Hz (held while driven)")
    total_steps = cfg.warmup_steps + REST_STEPS + cfg.smoke_steps
    print(f"decoder.update: {1000 * update_time / total_steps:.3f} ms per step")

    jump_std = rest_stats["jump"][1]
    checks = {
        f"rest: mean deviation under {REST_MAX_MEAN_DEV} Hz in every group":
            all(abs(rest_stats[n][0]) < REST_MAX_MEAN_DEV for n in names),
        f"looming: jump deviation above thresh_jump ({cfg.thresh_jump:g} Hz)": jump_dev > cfg.thresh_jump,
        f"looming: jump deviation above 3x its resting std ({3 * jump_std:.2f} Hz)": jump_dev > 3 * jump_std,
        "looming: baseline does not creep while driven": abs(during[-1].baseline - jump_base_before) < 0.05,
    }
    print()
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\nDECODER SELF-TEST PASSED" if passed else "\nDECODER SELF-TEST FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
