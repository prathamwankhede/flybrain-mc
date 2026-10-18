"""Loads config.toml into typed, read-only settings. No tunables live in code."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass(frozen=True)
class Group:
    name: str
    types: tuple[str, ...]
    side: str | None = None


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    mc_version: str
    username: str
    max_entity_distance: float
    device: str
    data_dir: Path
    seed: int
    dt: float
    sensory_input: bool
    refractory: float
    calibrate: bool
    calibration_csv: Path
    trace_decay: float
    baseline_decay: float
    turn_gain: float
    turn_dead_zone: float
    thresh_fwd: float
    thresh_jump: float
    thresh_back: float
    max_amount: float
    damage_amount: float
    stuck_seconds: float
    stuck_min_distance: float
    inputs: dict[str, Group]
    outputs: dict[str, Group]


def _groups(table: dict) -> dict[str, Group]:
    return {name: Group(name, tuple(g["types"]), g.get("side")) for name, g in table.items()}


def load(path: str | Path = DEFAULT_PATH) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    mc, brain, run = raw["minecraft"], raw["brain"], raw["run"]
    dec, ctl, stim, stuck = raw["decoder"], raw["control"], raw["stimulus"], raw["stuck"]
    base = path.parent
    return Config(
        host=mc["host"],
        port=int(mc["port"]),
        mc_version=mc["mc_version"],
        username=mc["username"],
        max_entity_distance=float(mc["max_entity_distance"]),
        device=brain["device"],
        data_dir=(base / brain["data_dir"]).resolve(),
        seed=int(brain["seed"]),
        dt=float(brain["dt"]),
        sensory_input=bool(brain["sensory_input"]),
        refractory=float(brain["refractory"]),
        calibrate=bool(run["calibrate"]),
        calibration_csv=(base / run["calibration_csv"]).resolve(),
        trace_decay=float(dec["trace_decay"]),
        baseline_decay=float(dec["baseline_decay"]),
        turn_gain=float(ctl["turn_gain"]),
        turn_dead_zone=float(ctl["turn_dead_zone"]),
        thresh_fwd=float(ctl["thresh_fwd"]),
        thresh_jump=float(ctl["thresh_jump"]),
        thresh_back=float(ctl["thresh_back"]),
        max_amount=float(stim["max_amount"]),
        damage_amount=float(stim["damage_amount"]),
        stuck_seconds=float(stuck["seconds"]),
        stuck_min_distance=float(stuck["min_distance"]),
        inputs=_groups(raw["inputs"]),
        outputs=_groups(raw["outputs"]),
    )
