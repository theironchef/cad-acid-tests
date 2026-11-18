# CAD acid tests

Ten deliberately hard parts and assemblies for checking how well a CAD kernel copes. Each test packs together the kinds of geometry that broke most often in the 10,000-item [stress corpus](https://github.com/dutchman-cad/stress-corpus): features one tolerance from a seam, rounds on spline outlines, shells thicker than a nearby feature, equal crossing holes, extreme size ratios, parts far from the origin, and patterns that cross their own edges.

This is a comparison: how well does each CAD program build the same hard models? The tests are run on:

- **SolidCraft:** every minor release from 0.20.0 to 0.44.0, plus 0.44.1, as a baseline. After that, every new release.
- **FreeCAD:** run as a separate program through its public Python API.
- **Fusion:** built from [PLAN.md](PLAN.md), with the version recorded. Other programs can be added the same way.

Every program is held to the same standard:
- **Expected values:** they are worked out by hand from the dimensions. None comes from any CAD program's output, so no program is graded against another.
- **Results:** they record only the outcome, timing, volume, body count and probe answers. No design files or exports from any program are committed.

## Results

The results table, with each test's reasoning, is at **https://theironchef.github.io/cad-acid-tests/**. It is also in [SCOREBOARD.md](SCOREBOARD.md).

A test is a numbered list of stages, each building on the one before. A program's score is the number of stages that pass before the first that does not. A stage passes when it:
- builds within 300 s;
- leaves valid closed solids, in the stated number;
- answers its probe points right;
- matches the volume, where there is one.

The details are in [PLAN.md](PLAN.md).

## Layout

- [`TESTS.md`](TESTS.md): the ten tests, acid-1 to acid-10, with why each exists, what it stresses in a kernel, and every dimension.
- [`PLAN.md`](PLAN.md): conventions, checks, and how each program is run.
- [`expected/values.py`](expected/values.py): the expected values and probe points for every stage.
- `freecad/`, `solidcraft/`: one harness per program.
- `results/`: one JSON line per program, version, test and stage.
- `tools/`: `run_freecad.py`, and `site.py`, which writes the scoreboard and the site in `docs/`.

## Running

```
python tools/run_freecad.py <path to freecadcmd>
python solidcraft/acid.py <path to solidcraft-cli>
python tools/site.py
```
