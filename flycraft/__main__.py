"""Run FlyCraft. `python -m flycraft` (calibration while `calibrate = true`)."""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from . import brain as brain_mod
from . import calibrate, check_env, config, game, scripted
from .loop import Loop
from .sense import Sensor

log = logging.getLogger("flycraft")
NEEDS_NODE = {"Node.js on PATH", "javascript package"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m flycraft", description="Run FlyCraft (calibration mode for now).")
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="path to config.toml")
    parser.add_argument("--host", help="override [minecraft] host")
    parser.add_argument("--port", type=int, help="override [minecraft] port (e.g. the LAN port shown in chat)")
    parser.add_argument("--seconds", type=float, help="override calibration_seconds (0 = until Ctrl+C)")
    parser.add_argument("--scripted", action="store_true",
                        help="replay a scripted fake game instead of joining Minecraft (tests the full pipeline)")
    parser.add_argument("--report", metavar="CSV", help="print the calibration summary for an existing CSV and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = config.load(args.config)
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    if args.report:
        calibrate.summarize(Path(args.report), cfg)
        return 0

    missing = [name for name, problem in check_env.run_checks()
               if problem and not (args.scripted and name in NEEDS_NODE)]
    if missing:
        log.error("environment not ready (%s); run: python -m flycraft.check_env", ", ".join(missing))
        return 1
    try:   # before any Minecraft connection, so an empty group stops us early
        brain, inputs, outputs = brain_mod.setup(cfg)
    except brain_mod.GroupResolutionError as e:
        log.error("stopping: %s", e)
        return 1
    if not cfg.calibrate:
        log.error("live control arrives with F10/F11; set calibrate = true in config.toml to run calibration")
        return 1

    seconds = args.seconds if args.seconds is not None else (
        scripted.DURATION if args.scripted else cfg.calibration_seconds)
    loop = Loop(brain, inputs, outputs, None, cfg)
    log.info("learning the resting baseline: %d silent steps (%.0f s of brain time)...", cfg.warmup_steps,
             cfg.warmup_steps * cfg.dt)
    loop.warm_up()
    conn = None
    try:
        if args.scripted:
            loop.source, alive = scripted.ScriptedGame(cfg), (lambda: True)
        else:
            conn = game.connect(cfg)
            loop.source, alive = Sensor(conn, cfg), conn.poll
        log.info("calibrating for %s; no controls are sent (Ctrl+C to stop early)",
                 f"{seconds:g} s" if seconds > 0 else "as long as you like")
        stats = calibrate.run(loop, cfg, inputs, outputs, seconds, alive, paced=not args.scripted)
    except game.ConnectError as e:
        log.error("%s", e)
        return 1
    finally:
        if conn:
            conn.quit()
    calibrate.summarize(stats.path, cfg, stats)
    if stats.connection_lost:
        log.error("the connection to Minecraft ended before the run was over")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
