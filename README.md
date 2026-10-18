# FlyCraft

> *What if a fruit fly played Minecraft?*
> *Not a fly-themed mod. An actual simulated fly brain, every one of its 166,700 neurons, holding the controls.*

FlyCraft connects a spiking simulation of the complete **fruit fly nervous system** (the [MaleCNS v1.0 connectome](https://male-cns.janelia.org/), run through the [`flybrain`](https://pypi.org/project/flybrain/) package) to a **Minecraft Java Edition** bot. A zombie creeping up on the left makes the fly's left-side looming detectors fire. Its giant fiber (the neuron that tells a real fly to *get out of here*) fires, and the bot jumps.

Nobody trained it. Nobody tuned the weights. It's wired up the way a real fly is, and what happens next is up to the fly.

**Expectations:** this fly will not beat the Ender Dragon. It won't build you a house. It'll wander around, flinch at mobs, and get stuck on fences. That's the fun part.

---

## Status

| Part | Status | |
|---|---|---|
| Brain side | **Done** | Loads the full connectome, resolves every neuron group, and passes the giant-fiber smoke test |
| Minecraft side | **In progress** | Not wired up yet: the fly can think but has no body |
| Playable | **Not yet** | See the [roadmap](#roadmap) |

---

## How it works

```
 Minecraft Java  ⇄  Mineflayer bot  ⇄  Controller loop  ⇄  FlyBrain (frozen connectome)
   (the world)       (the body)         (the nerves)        (the fly)
```

Every **20 ms brain step**, the controller runs one cycle:

| Stage | What happens |
|---|---|
| 1. **Sense** | Read the game: health, nearby entities, position, yaw. Turn "zombie 4 blocks away, 30° to the left" into a list of `(neuron_indices, voltage)` pairs. |
| 2. **Step** | `fired = brain.step(inject=pairs)`: 166,700 point neurons integrate their input and some of them spike. |
| 3. **Decode** | Smooth each motor group's spikes into a firing rate, then subtract a slow-moving baseline. |
| 4. **Act** | Rate goes above threshold → hold W, turn the camera, or jump. |

### The wiring

**In** (game → fly). We skip the simulated eyes and inject straight into feature-detector neurons, because in this model photoreceptor signals die out at the first relay.

| Game event | Neurons stimulated | Real-fly job |
|---|---|---|
| Mob approaching on the left / right | `LC4`, `LPLC2` (that side) | Looming detection: "something is about to hit me" |
| Took damage | `LC4`, `LPLC2` (both sides) | Same alarm, but louder |
| *(planned)* Moving target in view | `LC10a` | Visual tracking |

**Out** (fly → game). Descending neurons carry commands from the brain to the body:

| Neurons | Real-fly job | Minecraft control |
|---|---|---|
| `DNg100` | Forward walking | Hold **W** |
| `DNa02` (left − right) | Steering | Turn the camera |
| `DNp01` | Giant fiber, escape take-off | **Jump** |
| `MDN` | "Moonwalker", backward walking | Hold **S** |

The giant fiber is what lets a real fly launch itself away from your fly swatter in a few milliseconds. Here it makes a Minecraft character hop away from a creeper. Same circuit, different swatter.

---

## Quick start

### Requirements
- **Python 3.11+**
- **Node.js 14+** (runs Mineflayer through [JSPyBridge](https://github.com/extremeheat/JSPyBridge))
- **Minecraft Java Edition 26.2**: a local server with `online-mode=false`, or a single-player world opened to LAN
- About **260 MB** of disk for the brain data
- *Optional:* an NVIDIA GPU (CUDA 12) with `pip install "flybrain[gpu]"`. A step takes about 12–15 ms on a 24-thread CPU and about 1.4 ms on an RTX 4060.

### Setup

```bash
git clone https://github.com/prathamwankhede/flybrain-mc.git
cd flybrain-mc

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Fetch the prebuilt brain (~260 MB, once)
python -m flybrain download --data fly-data
```

### Check that everything works

```bash
# Are all dependencies present?
python -m flycraft.check_env
#   [OK] Python version
#   [OK] flybrain import
#   [OK] numpy
#   [OK] Node.js on PATH
#   [OK] javascript package
#
# All OK

# Load the brain and find the neurons
python -m flycraft.brain
# INFO flycraft.brain: brain loaded: 166700 neurons on cpu in 0.4 s
# INFO flycraft.brain: input looming_left   ['LC4', 'LPLC2'] side=L -> 165 neurons
# INFO flycraft.brain: input looming_right  ['LC4', 'LPLC2'] side=R -> 146 neurons
# INFO flycraft.brain: output forward        ['DNg100'] side=any -> 2 neurons
# INFO flycraft.brain: output steer_left     ['DNa02'] side=L -> 1 neurons
# INFO flycraft.brain: output steer_right    ['DNa02'] side=R -> 1 neurons
# INFO flycraft.brain: output jump           ['DNp01'] side=any -> 2 neurons
# INFO flycraft.brain: output backward       ['MDN'] side=any -> 4 neurons
# INFO flycraft.brain: all groups resolved
```

If a neuron name is wrong, `flycraft.brain` lists every group that matched zero neurons and stops before anything touches Minecraft. You can look cell types up on [neuPrint](https://neuprint.janelia.org) (dataset `male-cns:v1.0`).

### Watch the fly flinch (no Minecraft needed)

```bash
python -m flycraft.smoke_test
```

This pokes the left looming detectors for one simulated second, counts giant-fiber spikes on each side, then does the same on the right, for five noise seeds. If the wiring is right, the poked side's giant fiber does nearly all the firing:

```
seed | stim | L spikes | R spikes | result
---------------------------------------------
   1 |    L |       45 |        0 | PASS
   1 |    R |        2 |       40 | PASS
   ...
10/10 trials passed (155 steps/s, 6.4 ms/step)
SMOKE TEST PASSED
```

A real fly does the same thing: something looming on the left makes it jump away to the right.

---

## Configuration

**Every** tunable lives in [`config.toml`](config.toml). No magic numbers are hidden in the code.

| Section | Key settings |
|---|---|
| `[minecraft]` | `host`, `port`, `mc_version`, `username`, `max_entity_distance` |
| `[brain]` | `device` (`auto`/`cpu`/`cuda`), `data_dir`, `seed`, `dt`, `sensory_input`, `refractory` |
| `[run]` | `calibrate` (log only, send no controls), `calibration_csv` |
| `[decoder]` | `trace_decay` (rate smoothing), `baseline_decay` |
| `[control]` | `turn_gain`, `turn_dead_zone`, `thresh_fwd`, `thresh_jump`, `thresh_back` |
| `[stimulus]` | `max_amount`, `damage_amount` |
| `[stuck]` | `seconds`, `min_distance` |
| `[smoke_test]` | `steps`, `amount`, `seeds`, `probe`, `stimulus_left`, `stimulus_right` |
| `[inputs.*]` / `[outputs.*]` | Neuron groups: `types = [...]` plus an optional `side = "L" \| "R"` |

Want to see what happens if mobs poke the fly's tracking neurons instead? Add a group:

```toml
[inputs.tracking_left]
types = ["LC10a"]
side = "L"
```

---

## Roadmap

### Foundation
- [x] **F01** Project scaffolding and a single config file
- [x] **F02** Environment and dependency check
### Brain
- [x] **F03** Brain loader and neuron-group resolver
- [x] **F04** Brain-only smoke test: poke the left looming detectors and confirm the left giant fiber fires more
### Game
- [ ] **F05** Minecraft connection via Mineflayer *(first job: confirm Mineflayer supports 26.2)*
- [ ] **F06** Game-state sensing: nearest mob, its bearing, damage detection
### Integration
- [ ] **F07** Sensory encoder: game events → neuron injections
- [ ] **F08** Output decoder: spikes → smoothed rate deviations
- [ ] **F09** Calibration mode with CSV logging (watch the fly think without letting it drive)
- [ ] **F10** Control mapper: rates → W / jump / camera turns
- [ ] **F11** Main loop with real-time pacing
### Hardening
- [ ] **F12** Safe shutdown: release all keys and leave the world on Ctrl+C
- [ ] **F13** Stuck detection, so a fly that's walking into a wall eventually gives up and turns
- [ ] **F14** Observability: per-second logs explaining *why* the fly did that, plus an optional browser view
### Release
- [ ] **F15** Full documentation and calibration walkthrough
### Stretch
- [ ] **F16** Mode B: drive your own Minecraft client with simulated keystrokes, using screen-derived senses

The full design is in [`spec.md`](spec.md).

---

## Known limitations (a.k.a. why the fly is a bit dim)

1. **Twitchy at rest.** With default settings, neurons sit near firing threshold, the network ticks at about 4 Hz, and motor neurons fire at roughly the same rate no matter what's going on. Calibration (F09) and baseline subtraction exist to get around this.
2. **Blind eyes.** Photoreceptor signals die at the first relay, which is why we inject directly into feature detectors instead of feeding it pixels.
3. **Tiny motor groups.** Steering depends on one `DNa02` neuron per side, so the output is noisy.
4. **Simplified neurons.** These are point neurons: no dendrites, neuromodulators or plasticity. Whether a synapse excites or inhibits is a rough rule.
5. **Not real science.** The model hasn't been checked against recordings from real flies. FlyCraft is a toy and makes no scientific claims.

---

## Credits

- **Connectome data:** MaleCNS v1.0, licensed **CC BY 4.0**. Berg, S. *et al.* (2026), *Cell*. Thanks to everyone who mapped every synapse of a fly.
- **Brain simulator:** [`flybrain`](https://pypi.org/project/flybrain/) (MIT). FlyCraft uses only the MIT-licensed `flybrain` package from [alextitonis/fly.ai](https://github.com/alextitonis/fly.ai), not anything else in that repository.
- **Bot framework:** [Mineflayer](https://github.com/PrismarineJS/mineflayer) by PrismarineJS.
- **Inspiration:** [Fly64](https://github.com/ornata/fly), a fly brain playing Super Mario 64.

*No flies were harmed in the making of this project. A few zombies may be mildly startled.*
