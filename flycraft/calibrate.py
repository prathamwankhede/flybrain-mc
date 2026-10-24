"""Calibration mode (spec F09): run the whole pipeline, log it, send no controls.

Each step is written to a CSV (one row per step, a column per input group's injection and
per output group's deviation). On exit a summary compares output deviations while idle
against while driven by an input, and suggests thresholds and turn_gain. Nothing in this
module touches the bot's controls. `--report FILE.csv` redoes the summary from a CSV.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .brain import ResolvedGroup
from .config import Config
from .decoder import settle_steps
from .loop import Loop, StepRecord

log = logging.getLogger("flycraft.calibrate")

BASE_COLUMNS = ["t_s", "wall", "step", "ready", "health", "entity", "distance", "bearing_deg", "damaged"]
# Output groups that become a threshold, and the [control] key each one sets.
THRESHOLD_KEYS = {"forward": "thresh_fwd", "jump": "thresh_jump", "backward": "thresh_back"}
TEXT_COLUMNS = {"wall", "entity"}


def columns(inputs: dict[str, ResolvedGroup], outputs: dict[str, ResolvedGroup]) -> list[str]:
    return (BASE_COLUMNS + [f"inj_{n}" for n in inputs] + [f"dev_{n}" for n in outputs] + ["dev_steer"])


def row(rec: StepRecord, inputs: dict[str, ResolvedGroup], outputs: dict[str, ResolvedGroup]) -> list:
    amounts = {i.name: i.amount for i in rec.injections}
    st = rec.state
    health, entity, distance, bearing, damaged = "", "", "", "", 0
    if st is not None:
        health, damaged = f"{st.health:.1f}", int(st.damaged)
        if st.nearest:
            entity, distance = st.nearest.name, f"{st.nearest.distance:.2f}"
            bearing = f"{math.degrees(st.nearest.bearing):.1f}"
    return ([f"{rec.t:.3f}", datetime.now().isoformat(timespec="milliseconds"), rec.step, int(rec.ready),
             health, entity, distance, bearing, damaged]
            + [f"{amounts.get(n, 0.0):.3f}" for n in inputs]
            + [f"{rec.readouts[n].deviation:.4f}" for n in outputs]
            + [f"{rec.dev_steer:.4f}"])


class CsvLogger:
    """Writes rows to `path`; an existing file there is first renamed, never overwritten."""

    def __init__(self, path: Path, header: list[str]):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(path.stat().st_mtime))
            kept = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
            n = 1
            while kept.exists():
                kept = path.with_name(f"{path.stem}.{stamp}-{n}{path.suffix}")
                n += 1
            os.replace(path, kept)
            log.info("kept the previous calibration as %s", kept.name)
        self._file = path.open("w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(header)
        self._last_flush = time.monotonic()

    def write(self, values: list) -> None:
        self._writer.writerow(values)
        if time.monotonic() - self._last_flush >= 1.0:
            self._file.flush()
            self._last_flush = time.monotonic()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "CsvLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@dataclass
class RunStats:
    path: Path
    steps: int
    tick_ms: float
    overruns: int
    connection_lost: bool


def window_line(window: list[StepRecord], outputs: dict[str, ResolvedGroup]) -> str:
    last = window[-1]
    nearest = last.state.nearest if last.state else None
    entity = (f"{nearest.name} {nearest.distance:.1f} m {math.degrees(nearest.bearing):+.0f} deg"
              if nearest else "nothing nearby")
    injected = ", ".join(f"{i.name} {i.amount:.2f}" for i in last.injections) or "-"
    devs = []
    for n in outputs:
        values = [r.readouts[n].deviation for r in window]
        devs.append(f"{n} {sum(values) / len(values):+.1f}({max(values, key=abs):+.1f})")
    warm = "" if last.ready else " (warming up)"
    return f"t={last.t:6.1f}s{warm} | {entity} | inj {injected} | dev mean(peak): " + "  ".join(devs)


def run(loop: Loop, cfg: Config, inputs: dict[str, ResolvedGroup], outputs: dict[str, ResolvedGroup],
        seconds: float, alive: Callable[[], bool], paced: bool) -> RunStats:
    """Run the pipeline for `seconds` of brain time (0 = until Ctrl+C), logging every step."""
    total_steps = math.inf if seconds <= 0 else round(seconds / loop.dt)
    window: list[StepRecord] = []
    last_print, tick_time, lost = 0.0, 0.0, False
    with CsvLogger(cfg.calibration_csv, columns(inputs, outputs)) as out:
        try:
            while loop.step < total_steps:
                if not alive():
                    lost = True
                    break
                t0 = time.perf_counter()
                rec = loop.tick()
                tick_time += time.perf_counter() - t0
                out.write(row(rec, inputs, outputs))
                window.append(rec)
                if rec.t - last_print >= cfg.print_interval_s:
                    print(window_line(window, outputs), flush=True)
                    window.clear()
                    last_print = rec.t
                if paced:
                    loop.pace()
        except KeyboardInterrupt:
            log.info("interrupted")
    return RunStats(cfg.calibration_csv, loop.step, 1000 * tick_time / max(1, loop.step), loop.overruns, lost)


# --- summary -----------------------------------------------------------------------

def load(path: Path) -> dict[str, np.ndarray]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        names = reader.fieldnames or []
    return {c: np.array([float(r[c]) if r[c] != "" else np.nan for r in rows])
            for c in names if c not in TEXT_COLUMNS}


def _pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q)) if a.size else float("nan")


def summarize(path: Path, cfg: Config, stats: RunStats | None = None) -> dict[str, float | None]:
    """Print the idle-vs-driven table and suggestions; returns the suggested config values."""
    data = load(path)
    n = data["step"].size
    ready = data["ready"] == 1
    injected = np.zeros(n, bool)
    for c in data:
        if c.startswith("inj_"):
            injected |= data[c] > 0
    settle = settle_steps(cfg)
    recent = np.convolve(injected, np.ones(settle + 1), mode="full")[:n] > 0
    idle, driven = ready & ~recent, ready & injected
    groups = [c[4:] for c in data if c.startswith("dev_") and c != "dev_steer"]

    print(f"\nCalibration summary: {n} steps ({n * cfg.dt:.0f} s), {int(ready.sum())} after warm-up; "
          f"{int(idle.sum())} idle steps, {int(driven.sum())} driven steps")
    if stats:
        print(f"{1000 / max(stats.tick_ms, 1e-9):.0f} ticks/s possible ({stats.tick_ms:.1f} ms per tick), "
              f"{stats.overruns} step(s) ran late")
    print(f"\n{'deviation (Hz)':<16}|{'idle':^28}|{'driven':^22}")
    print(f"{'group':<16}|{'p50':>6}{'p95':>7}{'p99':>7}{'max':>7} |{'p50':>6}{'p95':>8}{'max':>8}")
    print("-" * 68)
    stat = {}
    for g in groups + ["steer"]:
        col = data[f"dev_{g}"] if g != "steer" else np.abs(data["dev_steer"])
        label = g if g != "steer" else "|steer L-R|"
        i, d = col[idle], col[driven]
        stat[g] = (i, d)
        print(f"{label:<16}|{_pct(i, 50):>6.2f}{_pct(i, 95):>7.2f}{_pct(i, 99):>7.2f}{(i.max() if i.size else math.nan):>7.2f} |"
              f"{_pct(d, 50):>6.2f}{_pct(d, 95):>8.2f}{(d.max() if d.size else math.nan):>8.2f}")

    print("\nSuggestions (printed only; edit config.toml yourself):")
    out: dict[str, float | None] = {}
    for g, key in THRESHOLD_KEYS.items():
        if g not in stat:
            continue
        i, d = stat[g]
        low, high = _pct(i, 99), _pct(d, 50)
        current = getattr(cfg, key)
        if i.size and d.size and high > low:
            out[key] = round((low + high) / 2, 2)
            weak = "   WEAK: a single noise spike can cross it; debounce in F10 or raise trace_decay" if high < 2 * low else ""
            print(f"  {key} = {out[key]}   (now {current:g}; idle p99 {low:.2f}, driven p50 {high:.2f}){weak}")
        else:
            out[key] = None
            print(f"  {key}: not separable (driven p50 {high:.2f} Hz is not above idle p99 {low:.2f} Hz); "
                  "the current inputs don't drive this group, or there is too little data")
    idle_steer, driven_steer = stat["steer"]
    typical = _pct(driven_steer, 75)
    if idle_steer.size and driven_steer.size and typical > 0:
        per_step = math.radians(cfg.target_turn_deg_s) * cfg.dt
        gain = per_step / typical
        out["turn_gain"], out["turn_dead_zone"] = round(gain, 4), round(gain * _pct(idle_steer, 95), 4)
        print(f"  turn_gain = {out['turn_gain']}   (now {cfg.turn_gain:g}; aims for {cfg.target_turn_deg_s:g} deg/s "
              f"at the driven p75 of |steer L-R| = {typical:.2f} Hz)")
        print(f"  turn_dead_zone = {out['turn_dead_zone']}   (now {cfg.turn_dead_zone:g}; idle p95 of |steer L-R| "
              f"= {_pct(idle_steer, 95):.2f} Hz x turn_gain, in radians per step)")
    else:
        out["turn_gain"] = out["turn_dead_zone"] = None
        print("  turn_gain / turn_dead_zone: not enough steering data (need idle and driven steps with a response)")
    print(f"\nData: {path}")
    return out
