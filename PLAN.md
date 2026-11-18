# Modelling plan

How each test in [TESTS.md](TESTS.md) is built and checked. This file covers what every pipeline needs: the construction order, how to pick each edge or face, and what each stage is checked against. Scripts follow it. Anyone building a test by hand in another CAD program can follow it too.

## Conventions

- **Units and frame.** Lengths are in mm, angles in degrees, Z is up and the model is right-handed.
- **Picking edges and faces.** Each pick is given as a point that lies on the edge or face, with a tolerance of 1e-4. No CAD-specific id is ever used, so any tool, or a person, can resolve the same pick.
- **Bodies.** Each body is named in the test. A result counts as correct by its geometry, whatever the bodies are called.
- **Order.** Each stage is applied to the result of the stage before. A stage that names an earlier stage builds that part separately; this happens only in AT-10.

## What each stage is checked against

Every stage is checked for these four things:

1. **It builds:** no error, and done within 300 s.
2. **Valid:** every body is a closed, manifold solid.
3. **Bodies:** the stated number of bodies.
4. **Probe points:** listed points that must be inside, or outside, the result. This is the cheapest exact check, and it is what catches a feature that silently goes missing. Probes sit at least 0.01 from any surface, or less where the feature itself is smaller.

A stage can also be checked for these, where the test gives them:

5. **Volume:**
   - **Exact:** a closed form, written as `V = …`, to 1e-6 relative.
   - **Window:** a lower and upper bound, written as `V ∈ [lo, hi]`, where a round or a thread has no simple closed form. The bounds come from simpler shapes that the result must contain, or must lie inside.
   - Volume is not checked where the feature is too small for it to show. AT-06's needle adds 0.0014 mm³ to a body of 5.2e8 mm³, for instance.
6. **Face count:** exact, where the topology is unambiguous.

All values are worked out from the dimensions in `expected/AT-NN.py`, which prints them. Nothing comes from running a CAD program.

---

## AT-01 Seamstress

**Frame.** Torus centre circle R = 30, tube r = 8. The seam is the meridian in the plane Y = 0 on the +X side.

| Stage | Build | Picks | Checks |
|---|---|---|---|
| 1 | A torus, cut by the plane Z = 0, keeping Z ≥ 0 | – | 1 body; V = π²·R·r² = **18949.6405** |
| 2 | A Ø1.9 cylinder from (√(30² − 0.95001²), 0.95001, 0) down to Z = −1.9, joined | – | 1 body; V = 18949.6405 + π·0.95²·1.9 = **18955.0275**; probe (29.985, 0.95, −1) is inside |
| 3 | A Ø1.4 cylinder along Z from Z = −1 to 3, centred at (0, 38, 0), cut | – | 1 body; probe (0, 38.1, 0.5) is outside; V ∈ window (the notch removes less than π·0.7²·3) |
| 4 | R 0.8 rounds | Edges at (−22, 0, 0) (inner circle) and (−38, 0, 0) (outer circle) | 1 body; V ∈ window; probe (37.9, 0, 0.05) is outside, because the round removed it |
| 5 | A shell inward to 0.6, removing the boss's end face | Face at (29.985, 0.95001, −1.9) | 1 body; probe (−30, 0, 4) is outside (the void); probe (−30, 0, 7.7) is inside (the wall) |
| 6 | 12 Ø1 cylinders along Z through everything, on R = 30, at 15° + 30°·k, cut | – | 1 body; probe (30·cos 15°, 30·sin 15°, 7.7) is outside |
| 7 | A split by the plane Z = 4, keeping both | – | 2 bodies; probes at Z = 3.9 and Z = 4.1 inside different bodies |
| 8 | Move all bodies by (10000, 0, 0) | – | 2 bodies; every earlier probe, moved, gives the same answer; V unchanged to 1e-6 |

## AT-02 Spline bracelet

**Frame.** The outline is a periodic cubic spline through the 8 points in TESTS.md, parameterised by chord length. Its area A comes from Green's theorem on the exact spline (`expected/spline.py`), so stage 1 is exact: V = 25·A.

| Stage | Build | Picks | Checks |
|---|---|---|---|
| 1 | Extrude the outline 25 from Z = 0 | – | V = 25·A, exact |
| 2 | R 3 round | The top edge loop: the edge at (60, 0, 25) with its tangent chain | V ∈ window: the round removes (1 − π/4)·9 per unit length on a straight edge. On the convex spline it removes a little less, bounded using the outline's least and greatest curvature (`expected/AT-02.py`) |
| 3 | Four Ø6 × 10 cylinders from Z = 25 at (±30, ±12), joined | – | 1 body; probe (30, 12, 34) is inside |
| 4 | A 3° draft, outward from Z = 0 | Side face at (60, 0, 10) | probe (60.5, 0, 20) is inside (the drafted side passes X ≈ 61.05 there, and it was outside before the draft) |
| 5 | Offset the top face +2 | Face at (0, 0, 25) | probe (0, 0, 26.5) is inside |
| 6 | A Ø4 hole along Z through each cylinder and the body | – | probe (30, 12, 10) is outside |
| 7 | Mirror the body across X = 80 | – | 2 bodies |
| 8 | Join both bodies with the box (55, −5, 0)–(105, 5, 5) | – | 1 body; probe (80, 0, 2.5) is inside |

## AT-03 Hollow clockwork

**Frame.** The block runs from (0, 0, 0) to (80, 60, 30), so the top face is Z = 30 and its centre is C = (40, 30).

| Stage | Build | Picks | Checks |
|---|---|---|---|
| 1 | The block | – | V = **144000** |
| 2 | Six 3 × 3 × 2 pads, centred at (20 + 20i, 20 + 20j, 30), i = 0..2, j = 0..1, joined | – | V = 144000 + 6·18 = **144108** |
| 3 | A Ø6 hole 20 deep at C, with an M6 × 1 modelled thread 15 long | Hole face at (43, 30, 20) | V ∈ [144108 − π·3²·20, 144108 − π·2.459²·20]; probe (40, 30, 25) is outside |
| 4 | 8 Ø2.5 holes on r = 12 about C, at 0°, 45° … 315°, cut from Z = 32 down to Z = 20, so they also cut the pads they cross | – | probe (52, 30, 25) is outside |
| 5 | A Ø3 cylinder along Z, centred at (1.50001, 0, 30), from Z = 26 to 32, cut | – | probe (1.5, 0.5, 29) is outside; probe (0.005, 0.5, 29) is inside (the 0.00001 sliver past the notch is too thin to probe, so this checks the corner stays) |
| 6 | A closed shell of 1.2, with no face removed | – | 1 body; probe (10, 10, 10) is outside (the void); probe (0.6, 10, 10) is inside |
| 7 | R 0.5 rounds on the top face's outer loop | Edges at (40, 0, 30), (80, 30, 30), (40, 60, 30) and (0, 30, 30) | probe (79.95, 59.95, 29.95) is outside |

## AT-04 Crossfire

**Frame.** The cube spans −20..20 on every axis.

| Stage | Build | Checks |
|---|---|---|
| 1 | The cube | V = **64000** |
| 2 | Three Ø18 through-holes, along X, Y and Z | V = 64000 − [3π·9²·40 − 3·(16/3)·9³ + 8·(2 − √2)·9³] = **41711.4129**: two holes overlap in a Steinmetz bicylinder, all three in a tricylinder |
| 3 | Ø8 through-holes: along X at (Y, Z) = (±10, 0); along Y at (X, Z) = (±10, 0.00001) | 1 body; V to 1e-9 by 1-D quadrature of the exact cross-sections (`expected/AT-04.py`), because offset crossing cylinders have no simple closed form; probe (10, 10, 0) is outside |
| 4 | Separate Ø10 cylinders along Z from −30 to 30, at X = 26 and X = 35.99999, joined to each other | 2 bodies; volume of the second body = 2·π·25·60 − (lens volume, exact for a 0.00001 overlap) |
| 5 | A Ø10 sphere centred at (26, 0, 34.99999), joined to the cylinders | 2 bodies; probe (26, 0, 39) is inside |
| 6 | R 2 rounds on the cube's 12 edges | Picks at each edge's midpoint, e.g. (20, 20, 0); 2 bodies; probe (19.9, 19.9, 0) is outside |

## AT-05 Corner cascade

**Frame.** The block runs from (1000, 1000, 1000) to (1030, 1020, 1010).

| Stage | Build | Picks | Checks |
|---|---|---|---|
| 1 | The block | – | V = **6000** |
| 2 | R 1.3 rounds on the 4 vertical edges | Edge midpoints, e.g. (1000, 1000, 1005) | V = 6000 − 4·(1 − π/4)·1.3²·10 = **5985.4929** |
| 3 | R 1.29 rounds on the top loop | (1015, 1000, 1010) and its tangent chain | V ∈ window; probe (1015, 1000.05, 1009.95) is outside |
| 4 | A 0.00002 chamfer | Edge at (1015, 1000, 1000) | builds, 1 body (too small for volume to show) |
| 5 | An R 0.000025 round | Edge at (1000, 1010, 1000) | builds, 1 body |
| 6 | A shell of 0.4, removing the bottom face | Face at (1015, 1010, 1000) | probe (1015, 1010, 1005) is outside; probe (1015, 1019.8, 1005) is inside |

## AT-06 Needle and planet

Each body's volume is checked on its own. The probes catch the needle and the rod, which are too small for the volume to show.

| Stage | Build | Checks |
|---|---|---|
| 1 | A Ø1000 sphere, cut at Z = 499.99975 | V = 4/3·π·500³ − cap = **523598775.598**; the flat is Ø0.99999987 |
| 2 | A Ø0.12 × 0.12 cylinder on the flat, joined | 1 body; probe (0, 0, 500.05) is inside; probe (0.07, 0, 500.05) is outside |
| 3 | A separate half ring: centre circle Ø1000, tube Ø1, Z ≥ 0, moved to Z = 600 | 2 bodies; ring V = π²·500·0.25 = **1233.7006** |
| 4 | A closed shell of the ring to 0.06 | ring V = π²·500·(0.25 − 0.44²) = exact |
| 5 | A separate Ø1 cylinder from Z = −1600 to −600, drafted 0.02° inward from the bottom | 3 bodies; V = π·1000/3·(0.5² + 0.5·r₁ + r₁²) with r₁ = 0.150934, = **364.6846** |
| 6 | A Ø0.8 rod from Z = −600 to −400, joined to the sphere and the needle | 2 bodies; probe (0, 0, −550) is inside |

## AT-07 Volcano

| Stage | Build | Picks | Checks |
|---|---|---|---|
| 1 | A cone frustum: r = 100 at Z = 0, r = 40 at Z = 200 | – | V = π·200/3·(100² + 100·40 + 40²) = **3267256.360** |
| 2 | 6 Ø10 holes 50 deep from Z = 200, on r = 32, at 0° … 300° | – | V = 3267256.360 − 6·π·25·50 = exact |
| 3 | Offset the top face +5 | Face at (0, 0, 200) | probe (0, 0, 204) is inside; probe (32, 0, 204) is outside |
| 4 | A 2° draft outward from Z = 0 | Side face at (70, 0, 100) | V ∈ window |
| 5 | A Ø120 pocket from Z = 0 up to 80 | – | probe (0, 0, 40) is outside |
| 6 | A shell of 2, removing the bottom face | Face at (80, 0, 0) | probe (0, 0, 150) is outside; probe (0, 0, 204) is inside |

## AT-08 Lattice drum

| Stage | Build | Checks |
|---|---|---|
| 1 | A Ø100 × 120 cylinder | V = **942477.796** |
| 2 | A Ø6 hole along −X from X = 50 to 30 at Z = 20, then a circular pattern of 24 | V exact: 24 × (the hole's volume, with its cap on the cylinder worked out exactly) |
| 3 | A linear pattern of that ring, 5 up Z at 20 pitch | V exact; 120 holes |
| 4 | 7 Ø4 × 3 bosses on the arc of r = 30 from (30, 0, 120) to (0, 30, 120) | V += 7·π·4·3 |
| 5 | A closed shell of 2, with no face removed | probe (0, 0, 60) is outside |
| 6 | A split at Z = 61, keeping both | 2 bodies |
| 7 | Mirror the upper body across Z = 140 | 3 bodies |

## AT-09 Origami

The flat pattern is the check. Its area follows exactly from the bend allowances (K = 0.44), so stage 6 is exact, while the folded volumes are windows.

| Stage | Build | Checks |
|---|---|---|
| 1 | A 120 × 80 × 1 sheet | V = **9600** |
| 2 | A fold at X = 60, 30°, R 2 | V ∈ window: with K = 0.44 the bent zone's volume differs from the flat strip's by θ·(0.5 − K)·t²·w ≈ 2.5, and tools differ in which length they keep; probe on the far side of the fold |
| 3 | Flanges 20 tall at X = 0 and X = 120, with bend radius 1 and K 0.44 | V ∈ window, from the bend allowance (`expected/AT-09.py`) |
| 4 | A 10 × 40 slot across the X = 0 flange's bend | V −= the slot's area through the bend, exact |
| 5 | An R 0.4 round on the +Y long edge across the fold | builds, 1 body |
| 6 | Flatten | flat area exact from the bend allowances with K = 0.44; the slot appears as a 10 × 40 rectangle |

## AT-10 Orrery

| Stage | Build | Checks |
|---|---|---|
| 1–4 | Base = AT-03 at stage 6; ring = AT-01 at stage 5, centred over the base; cam = AT-02 at stage 4 × 0.25; gear | 4 parts |
| 5 | Joints: ring revolute about Z; cam revolute about (60, 0) ∥ Z; gear cylindrical on the cam axis; ring : cam = 1 : 3 | joints solve |
| 6 | Drive the ring through 360° in 36 steps | the cam's angle = 3 × the ring's angle at every step |
| 7 | Check interference between every pair of parts at every step | none |

The orrery checks assembly and motion, which FreeCAD can run only through its Assembly workbench. Where a pipeline has no joints, stages 5–7 are recorded as not supported, not as failed.

---

## Build order for the work

1. `expected/`: one Python file per test. Each prints its exact values, windows and probes. `spline.py` gives AT-02's exact area and perimeter, and an exact inclusion–exclusion helper covers AT-04.
2. `solidcraft/`: one command script per test, with the checkpoint taken after each stage. Point probes use the CLI's point-in-solid query where the release has one.
3. `tools/run.py`: runs a pipeline on all tests and writes `results/<pipeline>-<version>.jsonl`.
4. `.github/workflows/solidcraft.yml`: downloads a release's `linux-x86_64.tar.gz`. It runs the baseline once (0.20.0 … 0.44.0, plus 0.44.1), then each new release.
5. `freecad/`: a script per test, using FreeCAD's public Python API in a separate process (`isInside` for the probes). It runs on GitHub's runners.
6. `SCOREBOARD.md`: generated, with pipelines and versions as rows and the tests as columns.
