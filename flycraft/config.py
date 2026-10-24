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
    auth: str
    lan_discovery_timeout: float
    spawn_timeout: float
    connect_test_seconds: float
    max_entity_distance: float
    ignore_entity_types: tuple[str, ...]
    flip_bearing: bool
    sense_test_seconds: float
    sense_print_hz: float
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
    smoke_steps: int
    smoke_amount: float
    smoke_seeds: tuple[int, ...]
    smoke_probe: tuple[str, ...]
    smoke_stimulus: dict[str, str]       # side ("L"/"R") -> input group name
    inputs: dict[str, Group]
    outputs: dict[str, Group]


def _groups(table: dict) -> dict[str, Group]:
    return {name: Group(name, tuple(g["types"]), g.get("side")) for name, g in table.items()}


def load(path: str | Path = DEFAULT_PATH) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    mc, sense, brain, run = raw["minecraft"], raw["sensing"], raw["brain"], raw["run"]
    dec, ctl, stim, stuck = raw["decoder"], raw["control"], raw["stimulus"], raw["stuck"]
    smoke = raw["smoke_test"]
    base = path.parent
    return Config(
        host=mc["host"],
        port=int(mc["port"]),
        mc_version=mc["mc_version"],
        username=mc["username"],
        auth=mc["auth"],
        lan_discovery_timeout=float(mc["lan_discovery_timeout"]),
        spawn_timeout=float(mc["spawn_timeout"]),
        connect_test_seconds=float(mc["connect_test_seconds"]),
        max_entity_distance=float(mc["max_entity_distance"]),
        ignore_entity_types=tuple(sense["ignore_entity_types"]),
        flip_bearing=bool(sense["flip_bearing"]),
        sense_test_seconds=float(sense["test_seconds"]),
        sense_print_hz=float(sense["print_hz"]),
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
        smoke_steps=int(smoke["steps"]),
        smoke_amount=float(smoke["amount"]),
        smoke_seeds=tuple(int(s) for s in smoke["seeds"]),
        smoke_probe=tuple(smoke["probe"]),
        smoke_stimulus={"L": smoke["stimulus_left"], "R": smoke["stimulus_right"]},
        inputs=_groups(raw["inputs"]),
        outputs=_groups(raw["outputs"]),
    )
