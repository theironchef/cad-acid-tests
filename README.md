# CAD acid tests

Ten deliberately hard parts and assemblies for checking how well a CAD kernel copes. Each test packs together the kinds of geometry that broke most often in the 10,000-item [stress corpus](https://github.com/dutchman-cad/stress-corpus): features one tolerance from a seam, rounds on spline outlines, shells thicker than a nearby feature, equal crossing holes, extreme size ratios, parts far from the origin, and patterns that cross their own edges.

The tests are run on:

- **SolidCraft:** every minor release from 0.20.0 to 0.44.0, plus 0.44.1, as a baseline. After that, every new release.
- **FreeCAD:** run as a separate program through its public Python API.

Commercial CAD is not used. No test, script or expected value comes from Fusion, SolidWorks, Onshape or any other commercial product.

## How a test is scored

A test is a numbered list of **stages**, each building on the one before. A stage passes when the result:

1. builds, without an error or a timeout (the default limit is 300 s per stage);
2. is a valid closed solid, or the stated number of them;
3. matches the expected values: body count always, and volume or face count where the test gives them.

Every expected value is worked out by hand from the dimensions, as a closed form, or as a bound where an exact value would need a round's corner patch. The working is in `expected/`, and none of it comes from running a CAD program.

A pipeline's score on a test is the number of stages that pass before the first failure. For example, "7 / 12" means stages 1–7 passed and stage 8 did not. A later stage is never credited after an earlier one fails.

## Layout

- `TESTS.md`: the ten tests in words, with every dimension. This is the reference.
- `expected/`: the expected values for each stage, with their working.
- `solidcraft/`: a `solidcraft-cli` command script per test and stage.
- `freecad/`: a FreeCAD Python script per test.
- `results/`: one JSON line per pipeline, version, test and stage.
- `tools/`: the runners and the scoreboard.
- `.github/workflows/`: runs every SolidCraft release, and the FreeCAD baseline, on GitHub's runners.

## Status

Draft: the tests are being written. Scripts, expected values and runners come next.
