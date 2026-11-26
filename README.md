# CAD acid tests

Eleven deliberately hard parts and assemblies for checking how well a CAD kernel copes: acid-1 to acid-10 are single parts, and acid-11 is an assembly. Each test packs together the kinds of geometry that broke most often in the 10,000-item [stress corpus](https://github.com/dutchman-cad/stress-corpus): features one tolerance from a seam, rounds on spline outlines, shells thicker than a nearby feature, equal crossing holes, extreme size ratios, parts far from the origin, and patterns that cross their own edges.

This is a comparison: how well does each CAD program build the same hard models? The tests are run on:

- **SolidCraft:** every minor release from 0.20.0 to 0.44.0, plus 0.44.1, as a baseline. After that, every new release.
- **FreeCAD:** run as a separate program through its public Python API.
- **SOLIDWORKS:** run as a separate program through its COM API, with its core features only.
- **Fusion:** built from [PLAN.md](PLAN.md), with the version recorded. Other programs can be added the same way.

Every program is held to the same standard:
- **Expected values:** they are worked out by hand from the dimensions. None comes from any CAD program's output, so no program is graded against another.
- **Results:** they record only the outcome, timing, volume, body count and probe answers.
- **Models:** Fusion's builds are in [`fusion/models/`](fusion/models), as Fusion archives (`.f3d`) with their timelines, so each result can be opened and inspected. No other program's design files are committed.

## Results

The results table, with each test's reasoning, is at **https://theironchef.github.io/cad-acid-tests/**. It is also in [SCOREBOARD.md](SCOREBOARD.md).

A test is a numbered list of stages, each building on the one before. A program's score is the number of stages that pass before the first that does not. A stage passes when it:
- builds within 300 s;
- leaves valid closed solids, in the stated number;
- answers its probe points right;
- matches the volume, where there is one.

The details are in [PLAN.md](PLAN.md).

## Layout

- [`TESTS.md`](TESTS.md): the eleven tests, acid-1 to acid-11, with why each exists, what it stresses in a kernel, and every dimension.
- [`PLAN.md`](PLAN.md): conventions, checks, and how each program is run.
- [`expected/values.py`](expected/values.py): the expected values and probe points for every stage.
- `freecad/`, `fusion/`, `solidcraft/`, `solidworks/`: one harness per program.
- `solidcore/`: the kernel-only harness for SolidCraft's kernel (below).
- `results/`: one JSON line per program, version, test and stage.
- `tools/`: `run_freecad.py`, `run_fusion.py`, `run_solidworks.py`, and `site.py`, which writes the scoreboard and the site in `docs/`; `reference.py` and `render.py` make the pictures (below).

## Running

```
python tools/run_freecad.py <path to freecadcmd>
python tools/run_fusion.py
python solidcraft/acid.py <path to solidcraft-cli>
python tools/run_solidworks.py
python solidcore/acid.py
python tools/reference.py
python tools/site.py
```

## Pictures: what each test should look like

The site shows, for each test, a sketch of its final stage and, beside it, what each program built where it passed every stage. Both are drawn by [`tools/render.py`](tools/render.py) in the same view and style, and are in `docs/img/`.

- **The sketch.** [`tools/reference.py`](tools/reference.py) builds each test's final stage from TESTS.md as an implicit solid: a signed distance function in numpy, made of exact primitives (boxes, cylinders, cones, spheres, tori), booleans, shells as offset bands and patterns as folds. Rounds, drafts and acid-2's spline outline are approximated, and the page says how. The sketch's volume is sampled on a grid and checked against `expected/values.py`, at the last stage whose volume that file pins down, and the page gives the result. It needs numpy and Pillow: `python tools/reference.py [acid-1 ...]`.
- **The programs' output.** When a harness sees a test pass every stage, it exports the finished output as STL to `meshes/` (gitignored) and renders it as `docs/img/<test>-<program version>.png`. SolidCraft's harness uses the CLI's `file.export`, FreeCAD's tessellates the shape, Fusion's exports STL through its API and SOLIDWORKS's saves as STL. `python solidcraft/acid.py <cli> --render-only` and `python tools/run_freecad.py <freecadcmd> --render-only` make only the renders, without writing results. Without numpy the harness keeps the mesh, says so and carries on.
- **Why this is not circular.** The sketch comes from TESTS.md alone, with no CAD kernel and no program's output, and its volume is checked against the hand-derived values the programs are graded on. A program's picture is only ever of a body that already passed every one of those checks, so the pictures add a look at the shape, never a grade.

## Kernel only: SolidCraft's kernel without the app

`solidcore/` runs the same tests on SolidCraft's kernel, [solidcore](https://github.com/tangency-labs/solidcraft/tree/main/solidcore), with no SolidCraft app in the loop. A disagreement between its row and a SolidCraft release's row points at the layer: a stage the kernel builds but the app refuses is lost in the app's commands; a stage both refuse is the kernel's.

- **What it is.** A small Rust program, `acid-solidcore`, that depends on the kernel's operation layer `solidcore-ops` (the calls the app itself makes for primitives, booleans, extrude and loft, fillet and chamfer, shell, draft, split and moves) and on `solidcore` where ops has no call (the material census and the closed-solid test). It builds each test stage by stage from TESTS.md's geometry, not from the app's commands, and runs the kernel with every fix on, as the app does. The kernel commit is the `rev` pinned in `solidcore/Cargo.toml`; to use a local checkout instead, swap the git dependencies for the `path` ones commented there.
- **How it is checked.** `solidcore/acid.py` builds the program, runs one process per test, and checks what it measures after each stage against `expected/values.py`, with the same tolerances and score as the CLI harness. It writes `results/solidcore-<short sha>-full.jsonl` (version "… stage-faithful"), which the scoreboard and the site leave out: they show programs only.
- **How it differs from `solidcraft/acid.py`.**
  - Probe points are classified exactly by the kernel (`Body::classify_point`), not by overlapping 0.0002 mm probe cubes. A point the kernel puts on the boundary fails its probe.
  - "Valid" is the kernel's closed-solid test and its own validity check (`Body::check`), both required. The CLI harness reads only the closed flag.
  - Picks are the same points. Edges are the kernel's nearest edges, run on along tangent chains where the app's fillet would; faces are found by the kernel's point classification.
  - A pattern is its instances, unioned and subtracted in one boolean. acid-3's modelled thread is the ISO M6 × 1 basic groove, screw-swept by the kernel and cut from the hole.
  - acid-11's gear and driven motion (stages 4 and 5) exist only in the app's assembly, so they are recorded as "not run". Stage 6 still runs: the kernel's contact test is checked at placements worked out from the 1 : 3 ratio.
  - A stage gets `--limit` seconds (300 by default), counted from the end of the stage before. It is a hang if it does not finish.

```
python solidcore/acid.py [acid-1 ...] [--limit 300] [--bin path/to/acid-solidcore]
python solidcore/acid.py --kernel-ops [acid-1 ...]
```

The default run is stage-faithful (`results/solidcore-<sha>-full.jsonl`): every stage as TESTS.md asks, with the kernel's rounds, shells, drafts, extrude, loft and screw sweep. `--kernel-ops` instead runs the shared neutral spec [`expected/kernel_ops.py`](expected/kernel_ops.py) op for op, the same ops in the same order with the same numbers as the kernel-near Fusion harness, so the two rows compare 1 : 1. Each neutral op is one solidcore-ops call, except a cylinder off the Z axis, which is made on Z, then turned and moved. Its stages marked not expressible are "not run". It writes pipeline `solidcore-kernel` to `results/solidcore-kernel-<sha>.jsonl`.

It needs the kernel's Rust toolchain, which `solidcore/rust-toolchain.toml` pins. Set `CARGO_TARGET_DIR` to keep the build out of the repository.
