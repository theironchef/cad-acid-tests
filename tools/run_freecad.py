"""Run the acid tests in FreeCAD, one freecadcmd process per test, with a per-stage time limit.

usage: python tools/run_freecad.py <path to freecadcmd> [acid-1 acid-2 ...] [--limit 300] [--out results/x.jsonl]

A stage that is still running after --limit seconds is killed and recorded as "hang". Results are
appended as JSON lines; the default file is results/freecad-<version>.jsonl.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL = ["acid-%d" % i for i in range(1, 12)]


def version(fc):
    out = subprocess.run([fc, "--version"], capture_output=True, text=True, timeout=120).stdout
    for word in out.split():
        if word[:1].isdigit() and word.count(".") >= 1:
            return word.strip(",")
    return "unknown"


def run_test(fc, test, out, limit):
    env = dict(os.environ, AT_TEST=test, AT_OUT=out, AT_REPO=ROOT)
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
    tests = args or ALL
    out = os.path.abspath(out or os.path.join(ROOT, "results", "freecad-%s.jsonl" % version(fc)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for t in tests:
        t0 = time.time()
        run_test(fc, t, out, limit)
        print("%s done in %.0f s" % (t, time.time() - t0), flush=True)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
