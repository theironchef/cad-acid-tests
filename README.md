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
- `results/`: one JSON line per program, version, test and stage.
- `tools/`: `run_freecad.py`, `run_fusion.py`, `run_solidworks.py`, and `site.py`, which writes the scoreboard and the site in `docs/`.

## Running

```
python tools/run_freecad.py <path to freecadcmd>
python tools/run_fusion.py
python solidcraft/acid.py <path to solidcraft-cli>
python tools/run_solidworks.py
python tools/site.py
```
