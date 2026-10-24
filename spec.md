# FlyCraft: Software Specification

| Field | Value |
|---|---|
| Project | FlyCraft: simulated fruit fly brain controls a Minecraft Java Edition character |
| Document | Software Specification Sheet |
| Version | 0.1 (draft) |
| Date | 2026-10-18 |
| Status | Draft, pre-implementation |

---

## 1. Overview

### 1.1 Purpose
FlyCraft connects a simulated fruit fly brain (the MaleCNS v1.0 connectome, run through the `flybrain` Python package) to a Minecraft Java Edition character. Game events are injected into chosen sensory neurons, the network is stepped forward, and activity in chosen descending (motor-command) neurons is converted into game controls.

### 1.2 Design principles
- The brain is **frozen and untrained**. All behaviour comes from connectome wiring plus the human-chosen input/output mapping.
- Goal is entertaining, somewhat reactive wandering, not skilled play.
- Every tunable lives in one configuration file.

### 1.3 Out of scope
- Real flies or hardware.
- Reinforcement learning or weight training.
- Bedrock Edition.
- Any scientific validity claim. The upstream project describes its model as a demo, not validated against real-fly recordings.

---

## 2. System Requirements

| ID | Requirement | Detail |
|---|---|---|
| SR-1 | Python | 3.11 or newer (stdlib `tomllib`) |
| SR-2 | Brain package | `flybrain` (MIT). `flybrain[gpu]` optional for NVIDIA GPU (CUDA 12). |
| SR-3 | Brain data | About 260 MB, downloaded on first `FlyBrain()` call to `~/fly-data` (override with `FLY_DATA`). |
| SR-4 | Bridge (Mode A) | `javascript` pip package (JSPyBridge) and Node.js 14+; Mineflayer is loaded with `require('mineflayer')`. |
| SR-5 | Game | Minecraft Java Edition; local server with `online-mode=false`, or a single-player world opened to LAN. |
| SR-6 | Libraries | `numpy` |
| SR-7 | Optional | `neuprint-python` and a free neuPrint account for neuron lookup (dataset `male-cns:v1.0`). |
| SR-8 | Optional (Mode B) | `pyautogui` or `pynput`, plus `mss` |
| SR-9 | Performance reference | About 12-15 ms per step on a 24-thread CPU, about 1.4 ms on an RTX 4060 (flybrain README). Real time needs under 20 ms per step; slower than real time is acceptable. |

---

## 3. Architecture

```
Minecraft Java <-> Bridge (Mineflayer / pyautogui) <-> Controller loop <-> FlyBrain (frozen connectome)
```

Controller loop, executed once per brain step (default `dt` = 0.020 s):

| Stage | Action |
|---|---|
| 1. Sense | Read game state; build a list of `(neuron_indices, amount)` pairs. |
| 2. Step | `fired = brain.step(inject=pairs)` |
| 3. Decode | Update smoothed firing rate per output group; subtract slow baseline. |
| 4. Act | Map rate deviations to game controls. |

### 3.1 flybrain API used
```python
from flybrain import FlyBrain
brain = FlyBrain(device="auto")                 # options: dt, sensory_input, refractory, batch
idx = brain.cells(["LC4", "LPLC2"], side="L")   # by cell type; "descending_neuron" also accepted
fired = brain.step(inject=[(idx, 0.8)])         # one step; returns indices of neurons that spiked
brain.dt                                        # step length in seconds
```

---

## 4. Feature List (in order of implementation)

| # | Feature | Phase | Depends on |
|---|---|---|---|
| F01 | Project scaffolding and configuration | Foundation | none |
| F02 | Environment setup and dependency check | Foundation | F01 |
| F03 | Brain loader and neuron-group resolver | Brain | F02 |
| F04 | Brain-only smoke test | Brain | F03 |
| F05 | Minecraft connection (Mineflayer) | Game | F02 |
| F06 | Game-state sensing | Game | F05 |
| F07 | Sensory encoder | Integration | F03, F06 |
| F08 | Output decoder | Integration | F03 |
| F09 | Calibration mode with CSV logging | Integration | F07, F08 |
| F10 | Control mapper | Integration | F05, F08 |
| F11 | Main loop and timing | Integration | F07-F10 |
| F12 | Safe shutdown | Hardening | F11 |
| F13 | Stuck-detection fallback | Hardening | F11 |
| F14 | Observability (logging, optional viewer) | Hardening | F11 |
| F15 | Documentation | Release | F01-F14 |
| F16 | Mode B: pyautogui/pynput input (optional) | Extension | F11 |

---

## 5. Feature Specifications

### F01. Project scaffolding and configuration
- **Description:** Create the project layout, `requirements.txt`, and a single config file (e.g. `config.toml` or a `config.py` block) holding every tunable.
- **Config keys:** `host`, `port`, `mc_version`, `username`, `calibrate`, `turn_gain`, `thresh_fwd`, `thresh_jump`, `trace_decay`, input neuron lists, output neuron lists, `device`, `max_entity_distance`.
- **Acceptance:** No tunable constants appear outside the config file.

### F02. Environment setup and dependency check
- **Description:** A startup check that confirms Python version, `flybrain` import, Node.js on PATH, and the `javascript` package, and prints a clear message for each missing item.
- **Acceptance:** Running the check on a clean machine lists exactly what is missing; on a ready machine it reports all OK.

### F03. Brain loader and neuron-group resolver
- **Description:** Load `FlyBrain(device="auto")` and resolve the named input and output groups with `brain.cells(...)`.
- **Behaviour:** If a group resolves to zero neurons, log a warning with the name and `side` used and exit before connecting to Minecraft.
- **Acceptance:** Every configured group reports a neuron count greater than zero, or the program stops with a clear message.

### F04. Brain-only smoke test
- **Description:** Without Minecraft, stimulate the left looming detectors for 50 steps and report whether left `DNp01` fires, then repeat on the right.
- **Rationale:** Matches the flybrain README's documented result (left stimulation drives left giant fiber). Confirms the install works before any game code runs.
- **Acceptance:** Stimulated side shows more `DNp01` spikes than the unstimulated side across several noise seeds.

### F05. Minecraft connection (Mineflayer)
- **Description:** Create a bot via `mineflayer.createBot({host, port, username, auth: "offline", version})` and wait for the `spawn` event.
- **Behaviour:** Handle `kicked` and `error` events by logging and exiting cleanly.
- **Acceptance:** Bot appears in a local world and the program reports it has spawned.
- **Resolved (2026-10-24):** Joining a LAN world with `auth: "offline"` works (tested on 26.1.2). Mineflayer 4.39 supports up to 26.1.x, so the project targets 26.1.2.

### F06. Game-state sensing
- **Description:** Each step, read from Mineflayer: `bot.health`, `bot.entities`, `bot.entity.position`, `bot.entity.yaw`.
- **Derived values:** nearest other entity within `max_entity_distance`; its distance; its bearing relative to bot yaw, computed as `wrap(atan2(-dx, -dz) - yaw)`; a damage flag when health decreases.
- **Acceptance:** Printed values change correctly when a mob approaches from the left, right, and when the bot is hit.
- **Resolved (2026-10-24):** Positive relative angle means left. Checked in game on 26.1.2 with a player walking around a stationary bot: LEFT/RIGHT matched, crossing 0 when passing in front, and each hit raised the damage flag. A read costs about 2 ms. `flip_bearing` in config covers a mirrored setup.

### F07. Sensory encoder
- **Description:** Convert sensed state into injection pairs.

| Game event | Neurons | Amount |
|---|---|---|
| Entity nearby, on left | `LC4`, `LPLC2` (side L) | scaled by closeness, capped at 0.8 |
| Entity nearby, on right | `LC4`, `LPLC2` (side R) | scaled by closeness, capped at 0.8 |
| Entity nearby, on either side | `LC10a` (that side) | `track_amount` x side weight; drives same-side `DNa02`, so the bot turns toward it |
| Entity nearby | `LPC1` (both sides) | `walk_amount`; the only input found that drives `DNg100` (forward) |
| Damage taken | `LC4`, `LPLC2` both sides | 0.9, held `damage_hold_steps` steps (a 1-step pulse gave 4x less jump response) |
| (optional) Small approaching object | `LPLC1` | configurable |

- **Closeness:** `1 - distance / max_entity_distance`, clipped to [0, 1].
- **Side weights:** smooth in the bearing so steering does not flip when the entity crosses straight ahead. Ahead or behind, both sides get full weight; fully to one side, the other gets none.
- **Why LC10a and LPC1 (measured 2026-10-24, dt 0.02, 5 s):** `LC4`/`LPLC2` drive only `DNp01` (about 22 Hz); `DNg100` stays at 0 Hz and `DNa02` barely moves. `LPC1` at 0.8 gives `DNg100` about 3 Hz; `LC10a` at 0.6 gives same-side `DNa02` about 4 Hz.
- **Constraint:** Do not route raw screen pixels through the simulated photoreceptors (see Section 7).
- **Acceptance:** Injection list is empty when nothing is nearby and non-empty when a mob is within range.
- **Verified:** `python -m flycraft.encoder` drives the real brain with fake states: mob left raises left steering and mob right raises right; walking and jumping rise in both; a mob ahead balances steering; a far mob drives jump less than a near one; damage produces a jump burst.

### F08. Output decoder
- **Description:** Convert spikes into smoothed rates per output group.
- **Method:** `inst = spikes_in_group / group_size / dt`; `rate = decay * rate + (1 - decay) * inst`; baseline updated as a very slow moving average (about 0.999 weight).
- **Output:** Deviation from baseline per group.

| Neuron | Meaning | Intended control |
|---|---|---|
| `DNg100` | forward walking | hold W |
| `DNa02` (L minus R) | steering | turn camera |
| `DNp01` | giant fiber, escape take-off | jump |
| `MDN` (optional) | backward walking | hold S |

- **Acceptance:** With no input, deviations hover near zero; with the F04 stimulus, the `DNp01` deviation rises.

### F09. Calibration mode with CSV logging
- **Description:** When `calibrate = true`, print deviations per group and write them with timestamps to a CSV, without sending any controls.
- **Purpose:** Let the user choose thresholds and `turn_gain` from real numbers.
- **Acceptance:** CSV contains one row per step with a column per group and per injected stimulus.

### F10. Control mapper
- **Description:** Translate decoder output into game actions.
  - Forward: `bot.setControlState("forward", dev_fwd > thresh_fwd)`
  - Jump: `bot.setControlState("jump", dev_jump > thresh_jump)`
  - Turn: `bot.look(yaw + turn_gain * (dev_L - dev_R), pitch, true)`, applied only when the change exceeds a small dead zone
- **Acceptance:** Raising a group's deviation above its threshold changes the bot's action within one step.

### F11. Main loop and timing
- **Description:** Integrate F06-F10 into the loop, sleeping to hold approximately real time when the CPU/GPU allows it.
- **Acceptance:** Runs for 10 minutes without crashing, drift, or unbounded memory growth; reports effective steps per second.

### F12. Safe shutdown
- **Description:** On Ctrl+C or any fatal error, release all held controls and call `bot.quit()`.
- **Acceptance:** After shutdown, the bot is no longer in the world and no keys remain held.

### F13. Stuck-detection fallback
- **Description:** If the bot's position has not changed beyond a small distance for N seconds (configurable), inject a random stimulus or trigger a random action.
- **Acceptance:** A bot placed against a wall eventually changes direction.

### F14. Observability
- **Description:** Structured logging of per-second summaries (rates, injected stimuli, actions). Optional first-person browser view using Mineflayer's `prismarine-viewer`.
- **Acceptance:** A user can tell from the logs why the bot did what it did in a given second.

### F15. Documentation
- **Description:** README covering setup (LAN and local-server), calibration walkthrough, config reference, known limitations, and credits.
- **Required credit:** MaleCNS v1.0 data is CC BY 4.0; cite Berg, S. et al. (2026), *Cell*.
- **Acceptance:** A new user can reach a spawned, calibrating bot using only the README.

### F16. Mode B: pyautogui/pynput input (optional, last)
- **Description:** Alternative mode that controls the user's own Minecraft client. Inputs are limited to screen-derived signals (mean brightness, frame-to-frame change, red damage flash) via `mss`.
- **Constraint:** Minecraft captures the cursor, so relative mouse movement may not register; investigate before relying on it.
- **Process:** Do not start until F01-F15 are accepted; confirm with the project owner first.

---

## 6. Test Plan Summary

| Test | Feature(s) | Pass condition |
|---|---|---|
| T1 Clean-machine check | F02 | Missing items listed accurately |
| T2 Group resolution | F03 | All groups non-empty |
| T3 Smoke test | F04 | Same-side giant fiber responds more |
| T4 Spawn | F05 | Bot visible in world |
| T5 Bearing | F06, F07 | Mob left/right produces matching side injection |
| T6 Calibration run | F08, F09 | CSV written, no controls sent |
| T7 Live control | F10, F11 | Bot walks, turns, jumps in response to brain output |
| T8 Shutdown | F12 | No lingering bot or held keys |
| T9 Stuck | F13 | Bot leaves a wall |
| T10 Fresh-user README | F15 | Setup succeeds from docs alone |

Recommended test world: Creative mode, flat, peaceful difficulty; raise difficulty only after T7 passes.

---

## 7. Known Limitations of the Brain Model
*(from the flybrain / fly.ai README)*

1. With default settings, every neuron rests at about the firing threshold, the network ticks at around 4 Hz, and motor neurons fire at about the same rate whatever the input. Parameters (`tonic`, gain) may need tuning.
2. Signals from simulated photoreceptors die at the first relay. Inject into feature-detector neurons directly.
3. Past the eye, the wiring does give expected same-side responses (looming to giant fiber; LC10a to DNa02).
4. Point neurons only: no dendrites, neuromodulators, or plasticity. Transmitter sign is a rough rule.
5. Not validated against recordings from real flies.

---

## 8. Risks and Open Questions

| # | Risk or question | Mitigation |
|---|---|---|
| R1 | Neuron names or `side=` filters may not resolve | F03 check; look names up on neuPrint |
| R2 | Thresholds are placeholders; bot may idle | F09 calibration data |
| R3 | Left/right sign convention untested | **Closed:** verified in game (F06); `flip_bearing` exists if it ever differs |
| R4 | LAN world may reject offline auth | Use local server with `online-mode=false` |
| R5 | CPU too slow for real time | Use GPU build, or accept slower-than-real-time |
| R6 | `fly.ai` repo is associated with a crypto token and compute-mining network | Use only the MIT-licensed `flybrain` package; do not touch token, wallet, or mining features; review package source before running |

---

## 9. References

| Item | Link |
|---|---|
| flybrain on PyPI | https://pypi.org/project/flybrain/ |
| Source repository | https://github.com/alextitonis/fly.ai |
| Fly64 (Super Mario 64 fly brain) | https://github.com/ornata/fly |
| MaleCNS v1.0 data | https://male-cns.janelia.org/download/ |
| Mineflayer | https://github.com/PrismarineJS/mineflayer (see `examples/python`) |
| neuPrint | https://neuprint.janelia.org |