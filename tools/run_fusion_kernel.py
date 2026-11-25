"""Run the acid tests on Fusion's kernel alone: expected/kernel_ops.py's neutral ops, each one a
TemporaryBRepManager call, through Fusion's local MCP server. No document, no timeline feature, no
parameter, no design history, nothing saved.

usage: python tools/run_fusion_kernel.py [acid-1 ...] [--limit 300] [--out results/x.jsonl]

The ops are the same ones, in the same order, with the same numbers, as the kernel-only SolidCraft
harness (solidcore/) runs: a stage that needs an op outside what both kernels can do (round, chamfer,
shell, draft, extrude, loft, thread, joints) is "not run", and so is every stage after it. Each run
stage is one script in Fusion (fusion/acid_kernel.py) that rebuilds stages 1..n-1 untimed, builds stage
n timed, and measures it; this side checks the measurements against expected/values.py with the same
tolerances as the other harnesses. Volumes are Fusion's very-high-accuracy physical properties, taken on
copies placed in a scratch direct-modelling design that is closed unsaved (temporary bodies report 0
there, and BRepBody.volume is a coarse estimate; it is kept as tbm_volume_mm3). Probes are classified exactly by BRepBody.pointContainment: a point
Fusion puts on the boundary fails its probe (no probe cubes).

Fusion must be running with its MCP server on (FUSION_MCP, default http://127.0.0.1:27182/mcp).
Writes results/fusion-kernel-<build>.jsonl.
"""
import json
import os
import socket
import sys
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "expected"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import kernel_ops  # noqa: E402
import values  # noqa: E402
from fusion_mcp import Client, ScriptError  # noqa: E402

ALL = list(values.TESTS)
LIB = open(os.path.join(ROOT, "fusion", "acid_kernel.py"), encoding="utf-8").read()
PROBE_METHOD = "kernel point classification (BRepBody.pointContainment), not probe cubes"


def script(stage_ops, probes):
    return LIB + "\nSTAGES = %r\nPROBES = %r\n" % (stage_ops, probes)


def check(test, stage, meas, probes, prev_volume):
    exp = values.TESTS[test][stage]
    vol = sum(meas["solid_vols"])
    m = {"bodies": meas["bodies"], "volume_mm3": vol, "valid": meas["valid"],
         "tbm_volume_mm3": sum(meas.get("tbm_vols", []))}
    bad = []
    if not meas["valid"]:
        bad.append("not a closed solid")
    if "bodies" in exp and m["bodies"] != exp["bodies"]:
        bad.append("%d bodies, expected %d" % (m["bodies"], exp["bodies"]))
    v = exp.get("vol")
    if v:
        slack = values.DELTA_SLACK_REL * abs(prev_volume or 0)
        if v[0] == "exact" and abs(vol - v[1]) > values.EXACT_REL * max(1.0, abs(v[1])):
            bad.append("volume %.6f, expected %.6f" % (vol, v[1]))
        elif v[0] == "window" and not v[1] <= vol <= v[2]:
            bad.append("volume %.6f outside [%.6f, %.6f]" % (vol, v[1], v[2]))
        elif v[0] == "delta" and prev_volume is not None and not v[1] - slack <= vol - prev_volume <= v[2] + slack:
            bad.append("volume change %.6f outside [%.6f, %.6f]" % (vol - prev_volume, v[1], v[2]))
    for sv in exp.get("solid_vol", []):
        if not any(abs(b - sv) <= values.EXACT_REL * max(1.0, sv) for b in meas["solid_vols"]):
            bad.append("no solid of volume %.6f (have %s)" % (sv, ", ".join("%.6f" % b for b in meas["solid_vols"])))
    for pr, got in zip(probes, meas["probes"]):
        if got != pr["want"]:
            key = tuple(round(c, 6) for c in pr["p"])
            bad.append("%r should be %s%s" % (key, pr["want"], ", Fusion puts it on the boundary" if got == "on" else ""))
    return m, bad


def run_test(c, test, version, out, limit):
    def emit(rec):
        rec = dict({"pipeline": "fusion-kernel", "version": version, "test": test}, **rec)
        rec["probe_method"] = PROBE_METHOD
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print("  %s stage %d: %s %s" % (test, rec["stage"], rec["outcome"], (rec.get("detail") or "")[:200]), flush=True)
        return rec

    prev, built = None, []
    n_stages = len(values.TESTS[test])
    for n in range(1, n_stages + 1):
        st = kernel_ops.stage(test, n)
        if not st["run"]:
            emit({"stage": n, "outcome": "not run", "seconds": 0, "detail": st["detail"]})
            continue
        built.append(st["ops"])
        probes = st["probes"]
        t0 = time.time()
        try:
            text = c.script(script(built, [p["p"] for p in probes]), timeout=limit + 120)
            reply = json.loads(text.strip().splitlines()[-1])
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.URLError) and not isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise
            emit({"stage": n, "outcome": "hang", "seconds": limit, "detail": "no result within %d s" % limit})
            return "hang"
        except ScriptError as e:
            msg = " ".join(x.strip() for x in str(e).splitlines()
                           if x.strip() and not x.lstrip().startswith(("File ", "Traceback")))
            emit({"stage": n, "outcome": "error", "seconds": round(time.time() - t0, 3), "detail": ("error: " + msg)[:600]})
            for k in range(n + 1, n_stages + 1):
                emit({"stage": k, "outcome": "not run", "seconds": 0, "detail": "builds on stage %d, which did not build" % n})
            return "done"
        secs = reply["seconds"]
        if secs > limit:
            emit({"stage": n, "outcome": "hang", "seconds": secs, "detail": "took %.0f s, over %d s" % (secs, limit)})
            return "done"
        m, bad = check(test, n, reply["measured"], probes, prev)
        rec = {"stage": n, "outcome": "pass" if not bad else "fail", "seconds": secs, "measured": m,
               "detail": "; ".join(bad), "measure_seconds": reply.get("measure_seconds")}
        if st.get("note"):
            rec["note"] = st["note"]
        emit(rec)
        prev = m["volume_mm3"]
    return "done"


def main(args):
    limit, out = 300, None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    c = Client()
    v = c.script("import adsk.core\ndef run(_):\n    print(adsk.core.Application.get().version)\n").strip()
    version = "Fusion %s (TemporaryBRepManager, no features)" % v
    out = os.path.abspath(out or os.path.join(ROOT, "results", "fusion-kernel-%s.jsonl" % v))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(version, "->", out, "(kernel_ops spec %d)" % kernel_ops.SPEC_VERSION, flush=True)
    for t in args or ALL:
        t0 = time.time()
        if run_test(c, t, version, out, limit) == "hang":
            print("%s hung; stopping (Fusion is still busy)" % t)
            break
        print("%s done in %.0f s" % (t, time.time() - t0), flush=True)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
