"""Run the acid tests in Fusion through its local MCP server, stage by stage.

usage: python tools/run_fusion.py [acid-1 acid-2 ...] [--limit 300] [--out results/x.jsonl] [--no-export]

Fusion must be running with its MCP server on (FUSION_MCP, default http://127.0.0.1:27182/mcp).
Each stage is one script run in Fusion (fusion/acid.py); this side checks what it measured against
expected/values.py, exactly as the FreeCAD harness does. Each test is a new, unsaved design: when it
ends, it is exported as fusion/models/<test>.f3d on this machine (never saved to the cloud) and closed.
A stage that does not answer within --limit seconds is recorded as "hang" and the run stops, since
Fusion is then still busy with it.
"""
import json
import math
import os
import socket
import sys
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "expected"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import values  # noqa: E402
from fusion_mcp import Client, ScriptError  # noqa: E402

ALL = ["acid-%d" % i for i in range(1, 12)]
LIB = open(os.path.join(ROOT, "fusion", "acid.py"), encoding="utf-8").read()
# A draft angle is measured from the pull direction (PLAN.md). Fusion's Draft adds its angle to a cone
# face's existing taper, as SolidCraft does, so acid-7's absolute 18 degrees is passed as 18 - 16.70.
DRAFT7 = 18.0 - math.degrees(math.atan(60 / 200))


def script(test, stage, action, probes=None, path=None):
    head = {"TEST": test, "STAGE": stage, "ACTION": action, "PROBES": probes or {}, "PATH": path,
            "BEZIER": [[list(p) for p in seg] for seg in values.SPLINE.bezier()],
            "SPLINE_H": list(values.SPLINE.h), "DRAFT7": DRAFT7, "MOTION": values.TESTS["acid-11"][5]["motion"]}
    return LIB + "\n" + "".join("%s = %r\n" % (k, v) for k, v in head.items())


def check(test, stage, m, prev_volume):
    exp = values.TESTS[test][stage]
    total = sum(m["solid_vols"])
    bad = []
    if not m["valid"]:
        bad.append("not a valid closed solid")
    if "bodies" in exp and m["bodies"] != exp["bodies"]:
        bad.append("%d bodies, expected %d" % (m["bodies"], exp["bodies"]))
    vol = exp.get("vol")
    if vol:
        slack = values.DELTA_SLACK_REL * abs(prev_volume or 0)
        if vol[0] == "exact" and abs(total - vol[1]) > values.EXACT_REL * max(1.0, abs(vol[1])):
            bad.append("volume %.6f, expected %.6f" % (total, vol[1]))
        elif vol[0] == "window" and not vol[1] <= total <= vol[2]:
            bad.append("volume %.6f outside [%.6f, %.6f]" % (total, vol[1], vol[2]))
        elif vol[0] == "delta" and prev_volume is not None and not vol[1] - slack <= total - prev_volume <= vol[2] + slack:
            bad.append("volume change %.6f outside [%.6f, %.6f]" % (total - prev_volume, vol[1], vol[2]))
    for sv in exp.get("solid_vol", []):
        if not any(abs(v - sv) <= values.EXACT_REL * max(1.0, sv) for v in m["solid_vols"]):
            bad.append("no solid of volume %.6f (have %s)" % (sv, ", ".join("%.6f" % v for v in m["solid_vols"])))
    for p, w in zip(exp.get("inside", []), m["inside"]):
        if w != "in":
            bad.append("%r should be inside (is %s)" % (tuple(round(c, 6) for c in p), w))
    for p, w in zip(exp.get("outside", []), m["outside"]):
        if w != "out":
            bad.append("%r should be outside (is %s)" % (tuple(round(c, 6) for c in p), w))
    return {"bodies": m["bodies"], "volume_mm3": total, "valid": m["valid"]}, bad


def run_test(c, test, out, limit, version, export):
    def emit(rec):
        rec.update(pipeline="fusion", version=version, test=test)
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print("  %s stage %d: %s %s" % (test, rec["stage"], rec["outcome"], rec.get("detail", "")[:200]), flush=True)

    prev, built = None, False
    for n in range(1, len(values.TESTS[test]) + 1):
        exp = values.TESTS[test][n]
        probes = {"inside": exp.get("inside", []), "outside": exp.get("outside", [])}
        t0 = time.time()
        try:
            reply = json.loads(c.script(script(test, n, "build", probes), timeout=limit + 120).strip().splitlines()[-1])
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.URLError) and not isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise
            emit({"stage": n, "outcome": "hang", "seconds": limit, "detail": "no result within %d s" % limit})
            return "hang"
        except ScriptError as e:
            msg = str(e).strip()
            emit({"stage": n, "outcome": "error", "seconds": round(time.time() - t0, 3),
                  "detail": " ".join(x.strip() for x in msg.splitlines() if x.strip() and not x.lstrip().startswith(("File ", "Traceback")))[:400] or "error"})
            break
        built = True
        secs = reply["seconds"]
        if secs > limit:
            emit({"stage": n, "outcome": "hang", "seconds": secs, "detail": "took %.0f s, over %d s" % (secs, limit)})
            break
        m, bad = check(test, n, reply["measured"], prev)
        bad += reply.get("problems", [])
        emit({"stage": n, "outcome": "pass" if not bad else "fail", "seconds": secs, "measured": m, "detail": "; ".join(bad)})
        prev = m["volume_mm3"]
    if export and (built or n > 1):
        path = os.path.join(ROOT, "fusion", "models", test + ".f3d")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        c.script(script(test, 0, "export", path=path), timeout=600)
    c.script(script(test, 0, "close"), timeout=120)
    return "done"


def main(args):
    limit, out, export = 300, None, True
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    if "--no-export" in args:
        args.remove("--no-export")
        export = False
    c = Client()
    v = c.script("import adsk.core\ndef run(_):\n    print(adsk.core.Application.get().version)\n").strip()
    version = "Fusion " + v
    out = os.path.abspath(out or os.path.join(ROOT, "results", "fusion-%s.jsonl" % v))
    for t in args or ALL:
        t0 = time.time()
        if run_test(c, t, out, limit, version, export) == "hang":
            print("%s hung; stopping (Fusion is still busy)" % t)
            break
        print("%s done in %.0f s" % (t, time.time() - t0), flush=True)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
