# How the tests are built and checked

[TESTS.md](TESTS.md) is the reference: every dimension and every stage, in words. This file describes how a program is run against it. Building a test by hand in another program means following TESTS.md and checking against the same values.

## Conventions

- **Units and frame.** Lengths are in mm, angles in degrees, Z is up and the model is right-handed.
- **Picks.** An edge or face is picked by a point that lies on it, never by a program's own id, so every program, or a person, resolves the same pick.
- **Draft angles.** A draft angle is measured from the pull direction, as CAD programs define it. A harness translates that into the program's own sign convention. SolidCraft and Fusion add the angle to a cone's existing taper, so acid-7's absolute 18° is passed to them as 18° − 16.70°. SOLIDWORKS sets the angle from the pull direction, so it is given 18°. The geometry asked for is the same in every program.
- **Splines.** acid-2's outline is the C2 periodic cubic through its points, parameterised by chord length. A program whose own periodic interpolation is a different curve builds it from the Bézier segments that `python expected/values.py --poles` prints.

## What each stage is checked against

The values are in [`expected/values.py`](expected/values.py). It is plain Python, and every number in it is worked out from the dimensions. Nothing comes from running a CAD program.

1. **It builds:** no error, and done within 300 s.
2. **Valid:** every body is a closed solid.
3. **Bodies:** the stated number.
4. **Probe points:** listed points that must be inside, or outside, the result. They catch a feature that silently goes missing, which volume alone often cannot.
5. **Volume, where one is given:**
   - **Exact:** to 1e-5 relative. It is a closed form or a high-order quadrature of the exact shape. For example, acid-9's sphere stage integrates the square cross-section intersected with the sphere's circle, height by height.
   - **Window:** a bound from simpler shapes, where a round has no closed form.
   - **Change from the previous stage:** with slack of 1e-6 × the part's volume, for the program's own numerical integration.
   - **Spline solids:** a ±0.5% window only. Programs integrate B-spline volumes coarsely; FreeCAD 1.1.4's is 0.15% low on acid-2's outline.

## How each program is run

| Program | Harness | Probes |
|---|---|---|
| FreeCAD | `freecad/acid.py`, inside `freecadcmd`, using only the core Part and PartDesign modules, plus the built-in Assembly workbench for acid-11; `tools/run_freecad.py` runs one process per test and enforces the time limit | `Solid.isInside` at the point |
| Fusion | `fusion/acid.py`, run inside Fusion through its local MCP server's script execution (the public Fusion API). Primitives are exact bodies in a base feature; every other step is a timeline feature (Combine, Fillet, Chamfer, Shell, Draft, Thread, Split Body, Mirror, Move, Scale, Extrude, Revolve, Loft), and acid-11 uses components, revolute joints, a motion link and Interference. Half tori are a half disc revolved. `tools/run_fusion.py` runs each stage as one script, checks it here, and exports each test as `fusion/models/<test>.f3d` on the local machine | `BRepBody.pointContainment`; a point it calls "on" (within Fusion's tolerance of a face) is decided by the 0.0002 mm cube, as for SolidCraft |
| SolidCraft | `solidcraft/acid.py`, which runs `solidcraft-cli run` with the script up to each stage | a 0.0002 mm cube at the point counts as inside when at least half of it overlaps the result (`inspect.clearance`), which is exact for a point 0.000005 mm or more from a surface |
| SOLIDWORKS | `solidworks/acid.py`, a separate Python process (pywin32) that drives the running SOLIDWORKS through its COM API with core part features, and for acid-11 an assembly with concentric, distance and gear mates, driven by an angle mate stepped through the API and checked with the core Interference Detection; `tools/run_solidworks.py` runs one process per test, enforces the time limit and restarts SOLIDWORKS after a hang | a 0.0002 mm cube at the point, intersected with a copy of each body (`IBody2.Operations2`), decides when no surface crosses it; otherwise the nearest face does: the point is inside when it lies against the face's outward normal at its closest point (`IFace2.GetClosestPointOn`), which is exact at any distance; at an edge, the cube's overlap of at least half decides. `acid.py --selftest` checks it on points 0.000005 mm either side of flat and curved faces |

A stage that cannot be built stops the test: later stages build on it. A stage that builds but fails a check is recorded, and the test goes on, but the score counts only the stages before the first failure.

## Scoring and the site

`python tools/site.py` reads `results/*.jsonl` and writes [SCOREBOARD.md](SCOREBOARD.md) and the site in `docs/`, which is published with GitHub Pages.
