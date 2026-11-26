"""Run the acid tests in FreeCAD, one freecadcmd process per test, with a per-stage time limit.

usage: python tools/run_freecad.py <path to freecadcmd> [acid-1 acid-2 ...] [--limit 300] [--out results/x.jsonl]
                                   [--render-only]

A stage that is still running after --limit seconds is killed and recorded as "hang". Results are
appended as JSON lines; the default file is results/freecad-<version>.jsonl.

When a test passes every stage, its finished output is written to meshes/ (gitignored) as STL and rendered by
tools/render.py as docs/img/<test>-freecad-<version>.png, which the site shows beside the test's sketch.
--render-only runs the tests into a temporary results file instead, so results/ is not touched, and renders
those that pass every stage.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "expected"))
import render  # noqa: E402  (numpy is needed only to draw, and only once a test passes every stage)
import values  # noqa: E402
ALL = ["acid-%d" % i for i in range(1, 12)]


def version(fc):
    out = subprocess.run([fc, "--version"], capture_output=True, text=True, timeout=120).stdout
    for word in out.split():
        if word[:1].isdigit() and word.count(".") >= 1:
            return word.strip(",")
    return "unknown"


def run_test(fc, test, out, limit, label):
    mesh = os.path.join(render.MESHES, "%s-%s.stl" % (test, render.slug(label)))
    if os.path.exists(mesh):
        os.remove(mesh)
    env = dict(os.environ, AT_TEST=test, AT_OUT=out, AT_REPO=ROOT, AT_MESH=mesh)
    prog = out + ".progress"
    if os.path.exists(prog):
        os.remove(prog)
    log = open(out + "." + test + ".log", "w", encoding="utf-8")
    p = subprocess.Popen([fc, os.path.join(ROOT, "freecad", "acid.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
    while p.poll() is None:
        time.sleep(1)
        try:
            stage, started = open(prog).read().split()
        except (OSError, ValueError):
            continue
        if time.time() - float(started) > limit:
            p.kill()
            p.wait()
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps({"pipeline": "freecad", "test": test, "stage": int(stage), "outcome": "hang",
                                    "seconds": limit, "detail": "no result within %d s" % limit}) + "\n")
            break
    log.close()
    if os.path.exists(mesh) and render.full_pass(out, test, label, len(values.TESTS[test])):
        render.render_completion(test, label, [mesh])


def main(argv):
    fc, args = argv[0], argv[1:]
    limit, out = 300, None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    only = "--render-only" in args
    if only:
        args.remove("--render-only")
    tests = args or ALL
    v = version(fc)
    out = os.path.abspath(out or os.path.join(ROOT, "results", "freecad-%s.jsonl" % v))
    if only:
        out = os.path.join(tempfile.mkdtemp(), "freecad-%s.jsonl" % v)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for t in tests:
        t0 = time.time()
        run_test(fc, t, out, limit, "FreeCAD " + v)
        print("%s done in %.0f s" % (t, time.time() - t0), flush=True)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
