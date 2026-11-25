"""Run the acid tests on SolidCraft's kernel alone (solidcore, through its operation layer solidcore-ops),
stage by stage, and check each stage against expected/values.py. No SolidCraft app is involved.

usage: python solidcore/acid.py [acid-1 ...] [--kernel-ops] [--limit 300] [--out results/x.jsonl] [--bin path]
                                [--date YYYY-MM-DD]

Two modes:
  (default)      stage-faithful: every stage as TESTS.md describes it, with the kernel's rounds, shells, drafts,
                 extrude, loft and screw sweep; picks by point. Writes results/solidcore-<sha>-full.jsonl.
  --kernel-ops   the shared neutral spec, expected/kernel_ops.py, op for op (the same ops, order and numbers as
                 the kernel-near Fusion harness); its not_expressible stages are "not run". Pipeline
                 "solidcore-kernel", results/solidcore-kernel-<sha>.jsonl.

The Rust binary in this folder (acid-solidcore, built here with cargo unless --bin is given) builds each test
with direct kernel calls, as TESTS.md describes the geometry, and after every stage prints what it measured:
  bodies      the kernel's material census (solids) summed over the bodies;
  closed      the kernel's closed-solid test on every body (brep_is_closed_touching_solids);
  check       the kernel's own validity check (Body::check at the geometry level);
  volume      the kernel's mass properties at 1e-8 relative accuracy;
  probes      each probe point classified exactly by the kernel (Body::classify_point): inside, outside or
              on the boundary. The CLI harness (solidcraft/acid.py) instead overlaps 0.0002 mm probe cubes
              with the result; a point the kernel puts on the boundary fails its probe here.
This script compares those numbers with values.py (the same tolerances as solidcraft/acid.py) and writes
results/solidcore-<short sha>-full.jsonl, one line per stage, in the CLI harness's schema.

The kernel commit is the rev pinned in solidcore/Cargo.toml. A stage that prints nothing within --limit
seconds (from the end of the stage before) is a hang: the process is killed and the test stops.
"""
import datetime
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "expected"))
import values  # noqa: E402

TESTS = list(values.TESTS)
PROBE_METHOD = "kernel point classification (solidcore-ops Body::classify_point), not probe cubes"


def cli_harness():
    """solidcraft/acid.py, for acid-9's fillet edge picks (the same points the CLI is given)."""
    spec = importlib.util.spec_from_file_location("solidcraft_acid", os.path.join(ROOT, "solidcraft", "acid.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pinned_rev():
    text = open(os.path.join(HERE, "Cargo.toml"), encoding="utf-8").read()
    m = re.search(r'rev\s*=\s*"([0-9a-f]{7,40})"', text)
    return m.group(1) if m else None


def commit_date(rev):
    """The commit's date in US Eastern time, from a local SolidCraft checkout beside this repository."""
    for repo in (os.path.join(os.path.dirname(ROOT), "solidcraft"),):
        try:
            iso = subprocess.run(["git", "-C", repo, "show", "-s", "--format=%cI", rev], capture_output=True,
                                 text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        t = datetime.datetime.fromisoformat(iso)
        try:
            from zoneinfo import ZoneInfo
            t = t.astimezone(ZoneInfo("America/New_York"))
        except Exception:  # no tz database (Windows without tzdata): US Eastern by the US DST rule
            u = t.astimezone(datetime.timezone.utc)
            def nth_sunday(month, n):
                d = datetime.datetime(u.year, month, 1, 7, tzinfo=datetime.timezone.utc)
                return d + datetime.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
            dst = nth_sunday(3, 2) <= u < nth_sunday(11, 1) - datetime.timedelta(hours=1)
            t = u - datetime.timedelta(hours=4 if dst else 5)
        return t.date().isoformat()
    return None


def build():
    target = os.environ.get("CARGO_TARGET_DIR") or os.path.join(HERE, "target")
    t0 = time.time()
    subprocess.run(["cargo", "build", "--release"], cwd=HERE, check=True)
    exe = os.path.join(target, "release", "acid-solidcore" + (".exe" if os.name == "nt" else ""))
    print("built %s in %.0f s" % (exe, time.time() - t0), flush=True)
    return exe


def input_for(test, harness):
    stages = values.TESTS[test]
    probes = [list(stages[n].get("inside", [])) + list(stages[n].get("outside", [])) for n in sorted(stages)]
    inp = {"test": test, "probes": [[list(p) for p in s] for s in probes]}
    if test in ("acid-2", "acid-11"):
        inp["spline"] = values.SPLINE.bezier()
        inp["spline_h"] = values.SPLINE.h
    if test == "acid-9":
        inp["tower_edges"] = harness._tower_edge_points()
    return inp


def check(test, stage, meas, prev_volume):
    """The CLI harness's checks (solidcraft/acid.py), on the kernel's measurements."""
    exp = values.TESTS[test][stage]
    vol = meas["volume_mm3"]
    m = {"bodies": meas["bodies"], "volume_mm3": vol, "valid": bool(meas["closed"] and meas["check_valid"]),
         "closed": meas["closed"], "kernel_check": meas["check_valid"]}
    bad = []
    if not meas["closed"]:
        bad.append("not a closed solid")
    if not meas["check_valid"]:
        bad.append("fails the kernel's validity check")
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
        if not any(abs(b - sv) <= values.EXACT_REL * max(1.0, sv) for b in meas["solid_volumes"] if b == b):
            bad.append("no solid of volume %.6f" % sv)
    pts = [("inside", p) for p in exp.get("inside", [])] + [("outside", p) for p in exp.get("outside", [])]
    for (kind, p), got in zip(pts, meas["probes"]):
        if got != kind:
            key = tuple(round(c, 6) for c in p)
            bad.append("%r should be %s%s" % (key, kind, "" if got in ("inside", "outside") else ", the kernel puts it %s"
                                              % ("on the boundary" if got == "on" else got)))
    notes = [n for n in meas.get("notes", []) if "not a closed solid" not in n]
    if notes and bad:
        bad.append("kernel: " + "; ".join(notes)[:400])
    return m, bad


def reader(stream, q):
    for line in stream:
        q.put(line)
    q.put(None)


def spec_input(test):
    """expected/kernel_ops.py's stages of the test, every float as the spec computed it."""
    sys.path.insert(0, os.path.join(ROOT, "expected"))
    import kernel_ops
    spec = kernel_ops.as_json()["tests"][test]
    return {"test": test, "spec_version": kernel_ops.SPEC_VERSION,
            "stages": [dict(spec[k], stage=int(k)) for k in sorted(spec, key=int)]}


def run_test(exe, test, version, out, limit, harness, tmp, kernel_ops=False):
    path = os.path.join(tmp, test + ".json")
    json.dump(spec_input(test) if kernel_ops else input_for(test, harness), open(path, "w", encoding="utf-8"))
    errf = open(os.path.join(tmp, test + ".err"), "w+", encoding="utf-8", errors="replace")
    p = subprocess.Popen([exe, "--spec", path] if kernel_ops else [exe, path], stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8",
                         errors="replace")
    q = queue.Queue()
    threading.Thread(target=reader, args=(p.stdout, q), daemon=True).start()
    prev_v = None
    n_stages = len(values.TESTS[test])
    for n in range(1, n_stages + 1):
        rec = {"pipeline": "solidcore-kernel" if kernel_ops else "solidcore", "version": version, "test": test,
               "stage": n}
        t0 = time.time()
        try:
            line = q.get(timeout=limit)
        except queue.Empty:
            line = "TIMEOUT"
        if line == "TIMEOUT":
            p.kill()
            rec.update(outcome="hang", seconds=limit, detail="no result within %d s" % limit)
        elif line is None:
            p.wait()
            errf.seek(0)
            tail = errf.read().strip().splitlines()[-3:]
            rec.update(outcome="error", seconds=round(time.time() - t0, 3),
                       detail="the kernel process ended (exit %s): %s" % (p.returncode, " | ".join(tail))[:400])
        else:
            r = json.loads(line)
            if "not_run" in r:
                rec.update(outcome="not run", seconds=0, detail=r["not_run"])
            elif not r["ok"]:
                rec.update(outcome="error", seconds=round(r["seconds"], 3), detail="error: " + r["error"][:600])
            elif "interference" in r:
                bad = ["; ".join(r["interference"])] if r["interference"] else []
                rec.update(outcome="pass" if not bad else "fail", seconds=round(r["seconds"], 3),
                           detail="; ".join(bad), note=r.get("note"))
            else:
                m, bad = check(test, n, r["measured"], prev_v)
                rec.update(outcome="pass" if not bad else "fail", seconds=round(r["seconds"], 3), measured=m,
                           detail="; ".join(bad))
                rec["measure_seconds"] = round(r["measure_seconds"], 3)
                if r.get("note"):
                    rec["note"] = r["note"]
                prev_v = m["volume_mm3"]
        rec["probe_method"] = PROBE_METHOD
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(test, n, rec["outcome"], rec.get("seconds"), (rec.get("detail") or "")[:160], flush=True)
        if rec["outcome"] in ("error", "hang"):
            break
    if p.poll() is None:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    errf.close()


def main(argv):
    args = list(argv)
    opt = {}
    kops = "--kernel-ops" in args
    if kops:
        args.remove("--kernel-ops")
    for k in ("--limit", "--out", "--bin", "--date"):
        if k in args:
            i = args.index(k)
            opt[k] = args[i + 1]
            del args[i:i + 2]
    limit = int(opt.get("--limit", 300))
    rev = pinned_rev()
    short = rev[:9] if rev else "local"
    date = opt.get("--date") or (commit_date(rev) if rev else None) or "unknown date"
    version = "solidcore %s (main %s, %s)" % (short, date, "kernel ops 1:1" if kops else "stage-faithful")
    exe = opt.get("--bin") or build()
    out = os.path.abspath(opt.get("--out") or os.path.join(ROOT, "results", ("solidcore-kernel-%s.jsonl" if kops else "solidcore-%s-full.jsonl") % short))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    harness = cli_harness()
    print(version, "->", out, flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        for t in args or TESTS:
            run_test(exe, t, version, out, limit, harness, tmp, kops)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
